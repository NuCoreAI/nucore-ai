"""Unix domain socket support: IoXWrapper.__init__'s "unix://" base_url
parsing, the AsyncClient's native UDS transport (httpx.AsyncHTTPTransport),
and _subscribe_events routing over a Unix socket via
websockets.asyncio.client.unix_connect.
"""

from __future__ import annotations

import http.server
import os
import socketserver
import tempfile
import threading

from iox.iox_wrapper import IoXWrapper


def _bare_wrapper() -> IoXWrapper:
    wrapper = object.__new__(IoXWrapper)
    wrapper._client = None
    return wrapper


# ----------------------------------------------------------------------
# AsyncClient's native Unix-domain-socket transport -- exercised through
# the real IoXWrapper.get() call path, not the transport in isolation
# (there's no adapter class of our own anymore to test directly; httpx's
# AsyncHTTPTransport(uds=...) handles this natively).
# ----------------------------------------------------------------------

class _UnixHTTPServer(socketserver.UnixStreamServer):
    pass


class _EchoHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        body = b"hello from unix socket"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


async def test_get_over_unix_socket_round_trips_a_real_request():
    with tempfile.TemporaryDirectory() as tmpdir:
        socket_path = os.path.join(tmpdir, "test.sock")
        server = _UnixHTTPServer(socket_path, _EchoHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        wrapper = _bare_wrapper()
        wrapper.unix_socket = socket_path
        wrapper.base_url = "http://localhost"
        wrapper.username = "admin"
        wrapper.password = "secret"
        try:
            response = await wrapper.get("/anything")
            assert response.status_code == 200
            assert response.text == "hello from unix socket"
        finally:
            server.shutdown()
            thread.join(timeout=5)
            if wrapper._client is not None:
                await wrapper._client.aclose()


# ----------------------------------------------------------------------
# IoXWrapper.__init__ -- unix:// base_url parsing
# ----------------------------------------------------------------------

def test_init_with_unix_scheme_base_url_sets_unix_socket_and_placeholder_base_url():
    wrapper = IoXWrapper(
        json_output=False,
        prompt_format_type="shared-features",
        base_url="unix:///var/run/iox.sock",
        username="admin",
        password="secret",
    )
    assert wrapper.unix_socket == "/var/run/iox.sock"
    assert wrapper.base_url == "http://localhost"
    assert wrapper._client is None


def test_init_with_http_base_url_leaves_unix_socket_none():
    wrapper = IoXWrapper(
        json_output=False,
        prompt_format_type="shared-features",
        base_url="https://192.168.1.10:80",
        username="admin",
        password="secret",
    )
    assert wrapper.unix_socket is None
    assert wrapper.base_url == "https://192.168.1.10:80"


# ----------------------------------------------------------------------
# _subscribe_events -- unix socket routing
#
# NOTE: this repo has a top-level `secrets/` package (src for cert/key
# management) that shadows the stdlib `secrets` module whenever the repo
# root is on sys.path (true for a plain `pytest` invocation from the repo
# root) -- `websockets`' real server-side handshake needs stdlib
# `secrets.token_bytes` and breaks under that shadowing. Pre-existing,
# unrelated to this feature. Stubbing `unix_connect` (matching this
# repo's existing stub-over-real-IO convention, e.g. test_soap_post.py)
# avoids depending on a real websockets server and sidesteps it.
# ----------------------------------------------------------------------

_EVENT_XML = (
    '<Event seqnum="1" sid="0"><control>ST</control>'
    '<action uom="0" prec="0">1</action><node>node1</node></Event>'
)


class _FakeWebSocket:
    def __init__(self, messages):
        self._messages = iter(messages)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._messages)
        except StopIteration:
            raise StopAsyncIteration


class _FakeUnixConnect:
    """Stand-in for websockets.asyncio.client.unix_connect -- records the
    call args and hands back one fake message, then closes (mirrors a real
    server sending one event and dropping the connection)."""

    instances = []

    def __init__(self, path, uri=None, **kwargs):
        self.path = path
        self.uri = uri
        self.kwargs = kwargs
        _FakeUnixConnect.instances.append(self)

    async def __aenter__(self):
        return _FakeWebSocket([_EVENT_XML])

    async def __aexit__(self, *exc_info):
        return False


async def test_subscribe_events_over_unix_socket_delivers_a_message(monkeypatch):
    _FakeUnixConnect.instances = []
    monkeypatch.setattr("websockets.asyncio.client.unix_connect", _FakeUnixConnect)

    wrapper = _bare_wrapper()
    wrapper.unix_socket = "/var/run/iox.sock"
    wrapper.username = "admin"
    wrapper.password = "secret"

    received = []

    async def on_message(event):
        received.append(event)

    result = await wrapper._subscribe_events(on_message_callback=on_message)

    assert result is True
    assert len(received) == 1
    assert received[0]["control"] == "ST"
    assert received[0]["node"] == "node1"

    assert len(_FakeUnixConnect.instances) == 1
    call = _FakeUnixConnect.instances[0]
    assert call.path == "/var/run/iox.sock"
    assert call.uri == "ws://localhost/rest/subscribe"
    assert call.kwargs["additional_headers"]["Authorization"].startswith("Basic ")

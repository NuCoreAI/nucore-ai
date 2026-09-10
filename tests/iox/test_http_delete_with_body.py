"""IoXWrapper.delete() -- regression test for a real DELETE-with-body round
trip through httpx.AsyncClient.

node_ops' "delete" branch always sends a JSON body (required by
``/api/nodes/{id}/``), but ``httpx.AsyncClient.delete()`` -- unlike
post/put/patch -- doesn't accept a body at all; passing ``content=`` raised
a TypeError that the wrapper's broad ``except Exception`` swallowed into a
silent ``None``, so every insteon device delete failed with "no response
from backend" no matter how many times it was retried. Exercises the real
``IoXWrapper.delete()`` method against a live local server (not a stubbed
``wrapper.delete``, which is how the existing node_ops tests miss this)
so a regression here fails loudly again.
"""

from __future__ import annotations

import http.server
import threading

from iox.iox_wrapper import IoXWrapper


class _EchoDeleteHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_DELETE(self):
        length = int(self.headers.get("Content-Length", 0))
        self.received_body = self.rfile.read(length)
        self.__class__.last_body = self.received_body
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()


def _bare_wrapper() -> IoXWrapper:
    wrapper = object.__new__(IoXWrapper)
    wrapper._client = None
    wrapper.unix_socket = None
    return wrapper


async def test_delete_with_json_body_returns_response_not_none():
    server = http.server.HTTPServer(("127.0.0.1", 0), _EchoDeleteHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    wrapper = _bare_wrapper()
    wrapper.base_url = f"http://127.0.0.1:{server.server_port}"
    wrapper.username = "admin"
    wrapper.password = "secret"
    try:
        response = await wrapper.delete(
            "/api/nodes/n1/",
            body='{"nodeType": "node", "family": "1"}',
            headers={"Content-Type": "application/json"},
        )
        assert response is not None
        assert response.status_code == 200
        assert _EchoDeleteHandler.last_body == b'{"nodeType": "node", "family": "1"}'
    finally:
        server.shutdown()
        thread.join(timeout=5)
        if wrapper._client is not None:
            await wrapper._client.aclose()

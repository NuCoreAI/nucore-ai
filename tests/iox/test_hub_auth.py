"""IoXWrapper's hub login/JWT-auth methods (design/mcp-server.md Phase 2):
verify_bearer_token, login_with_credentials, refresh_access_token, logout.

Exercised against a real local HTTP server (same precedent as
test_http_delete_with_body.py), not a stubbed client, so a real httpx
round-trip bug (wrong method, wrong body shape, wrong auth header) fails
loudly here rather than being masked by a hand-written fake.
"""

from __future__ import annotations

import http.server
import json
import threading

import pytest

from iox.iox_wrapper import IoXWrapper


def _bare_wrapper() -> IoXWrapper:
    wrapper = object.__new__(IoXWrapper)
    wrapper._client = None
    wrapper.unix_socket = None
    return wrapper


class _RecordingHandler(http.server.BaseHTTPRequestHandler):
    """Records the last request's method/path/headers/body and replies with
    whatever response_status/response_body this class currently holds --
    set per test before the server is started."""

    response_status = 200
    response_body: bytes = b"{}"

    def log_message(self, *args):
        pass

    def _handle(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b""
        self.__class__.last_method = self.command
        self.__class__.last_path = self.path
        self.__class__.last_headers = dict(self.headers)
        self.__class__.last_body = body
        self.send_response(self.response_status)
        self.send_header("Content-Length", str(len(self.response_body)))
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(self.response_body)

    def do_GET(self):
        self._handle()

    def do_POST(self):
        self._handle()


def _start_server(handler_cls=_RecordingHandler) -> http.server.HTTPServer:
    server = http.server.HTTPServer(("127.0.0.1", 0), handler_cls)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


async def _teardown(server: http.server.HTTPServer, wrapper: IoXWrapper) -> None:
    server.shutdown()
    if wrapper._client is not None:
        await wrapper._client.aclose()


@pytest.mark.asyncio
async def test_verify_bearer_token_200_is_true():
    _RecordingHandler.response_status = 200
    _RecordingHandler.response_body = b"<DT></DT>"
    server = _start_server()
    wrapper = _bare_wrapper()
    wrapper.base_url = f"http://127.0.0.1:{server.server_port}"
    try:
        result = await wrapper.verify_bearer_token("abc123")
        assert result is True
        assert _RecordingHandler.last_method == "GET"
        assert _RecordingHandler.last_path == "/rest/time"
        assert _RecordingHandler.last_headers["Authorization"] == "Bearer abc123"
    finally:
        await _teardown(server, wrapper)


@pytest.mark.asyncio
async def test_verify_bearer_token_401_is_false():
    _RecordingHandler.response_status = 401
    _RecordingHandler.response_body = b""
    server = _start_server()
    wrapper = _bare_wrapper()
    wrapper.base_url = f"http://127.0.0.1:{server.server_port}"
    try:
        result = await wrapper.verify_bearer_token("bad-token")
        assert result is False
    finally:
        await _teardown(server, wrapper)


@pytest.mark.asyncio
async def test_verify_bearer_token_unreachable_is_none():
    wrapper = _bare_wrapper()
    wrapper.base_url = "http://127.0.0.1:1"  # nothing listens here
    result = await wrapper.verify_bearer_token("abc123")
    assert result is None


@pytest.mark.asyncio
async def test_login_with_credentials_sends_local_mode_body():
    _RecordingHandler.response_status = 200
    _RecordingHandler.response_body = json.dumps(
        {"successful": True, "data": {"accessToken": "AT", "refreshToken": "RT"}}
    ).encode()
    server = _start_server()
    wrapper = _bare_wrapper()
    base_url = f"http://127.0.0.1:{server.server_port}"
    try:
        result = await wrapper.login_with_credentials("alice", "secret", base_url)
        assert result == {
            "status_code": 200,
            "body": {"successful": True, "data": {"accessToken": "AT", "refreshToken": "RT"}},
        }
        assert _RecordingHandler.last_method == "POST"
        assert _RecordingHandler.last_path == "/api/login"
        sent = json.loads(_RecordingHandler.last_body)
        assert sent == {"username": "alice", "password": "secret", "local": True}
    finally:
        await _teardown(server, wrapper)


@pytest.mark.asyncio
async def test_login_with_credentials_403_passes_through_status_and_body():
    _RecordingHandler.response_status = 403
    _RecordingHandler.response_body = json.dumps({"successful": False}).encode()
    server = _start_server()
    wrapper = _bare_wrapper()
    base_url = f"http://127.0.0.1:{server.server_port}"
    try:
        result = await wrapper.login_with_credentials("alice", "wrong", base_url)
        assert result == {"status_code": 403, "body": {"successful": False}}
    finally:
        await _teardown(server, wrapper)


@pytest.mark.asyncio
async def test_login_with_credentials_unreachable_is_none():
    wrapper = _bare_wrapper()
    result = await wrapper.login_with_credentials("alice", "secret", "http://127.0.0.1:1")
    assert result is None


@pytest.mark.asyncio
async def test_refresh_access_token_sends_refresh_token_body():
    _RecordingHandler.response_status = 200
    _RecordingHandler.response_body = json.dumps(
        {"successful": True, "data": {"accessToken": "AT2", "refreshToken": "RT2"}}
    ).encode()
    server = _start_server()
    wrapper = _bare_wrapper()
    base_url = f"http://127.0.0.1:{server.server_port}"
    try:
        result = await wrapper.refresh_access_token("RT1", base_url)
        assert result["status_code"] == 200
        assert result["body"]["data"]["accessToken"] == "AT2"
        assert _RecordingHandler.last_path == "/api/jwt/refresh"
        assert json.loads(_RecordingHandler.last_body) == {"refreshToken": "RT1"}
    finally:
        await _teardown(server, wrapper)


@pytest.mark.asyncio
async def test_refresh_access_token_unreachable_is_none():
    wrapper = _bare_wrapper()
    result = await wrapper.refresh_access_token("RT1", "http://127.0.0.1:1")
    assert result is None


@pytest.mark.asyncio
async def test_logout_sends_refresh_and_access_token_body():
    _RecordingHandler.response_status = 200
    _RecordingHandler.response_body = json.dumps({"successful": True, "data": None}).encode()
    server = _start_server()
    wrapper = _bare_wrapper()
    base_url = f"http://127.0.0.1:{server.server_port}"
    try:
        result = await wrapper.logout("RT1", base_url, access_token="AT1")
        assert result == {"status_code": 200, "body": {"successful": True, "data": None}}
        assert _RecordingHandler.last_path == "/api/jwt/logout"
        assert json.loads(_RecordingHandler.last_body) == {"refreshToken": "RT1", "accessToken": "AT1"}
    finally:
        await _teardown(server, wrapper)


@pytest.mark.asyncio
async def test_logout_unreachable_is_none():
    wrapper = _bare_wrapper()
    result = await wrapper.logout("RT1", "http://127.0.0.1:1")
    assert result is None

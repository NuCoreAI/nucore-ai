"""The plain-HTTP ``/system-prompt/{tool_set}`` route on build_mcp_server's
actual Starlette app -- added because `resources/read` turned out not to be
a given capability on real MCP clients (confirmed directly against two of
them), while a plain GET works with any shell/curl-capable client regardless
of MCP resource support. Exercised through the real ASGI app (httpx's
ASGITransport, in-process, no real socket needed), the same pattern eisy-ai's
own mcp_proxy tests use for its ASGI app -- not a re-implementation of
Starlette's routing, which is already tested upstream."""

from __future__ import annotations

import httpx
import pytest

from unified.mcp.server import _render_system_prompt, build_mcp_server

from .test_server import FakeBackend


@pytest.fixture
def nucore_interface():
    nucore_interface = FakeBackend(json_output=True, formatter_type="minimal")
    # Same hang-avoidance as the `server` fixture in test_server.py -- see
    # that fixture's own comment for why both flags must be False here.
    nucore_interface.device_structure_changed = False
    nucore_interface.routines_changed = False
    return nucore_interface


@pytest.fixture
async def client(nucore_interface):
    app = build_mcp_server(nucore_interface=nucore_interface, plugin_output_root="/tmp")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://eisy-ai") as client:
        yield client


@pytest.mark.asyncio
async def test_system_prompt_http_endpoint_unified_returns_full_prompt(client, nucore_interface):
    response = await client.get("/system-prompt/unified")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")
    expected = await _render_system_prompt(nucore_interface, "unified")
    assert response.text == expected


@pytest.mark.asyncio
async def test_system_prompt_http_endpoint_plugin_authoring_returns_distinct_prompt(client, nucore_interface):
    response = await client.get("/system-prompt/plugin_authoring")
    assert response.status_code == 200
    expected = await _render_system_prompt(nucore_interface, "plugin_authoring")
    assert response.text == expected
    assert "Dynamic Profiles JSON API" in response.text
    assert "DEVICE DATABASE" not in response.text


@pytest.mark.asyncio
async def test_system_prompt_http_endpoint_bad_tool_set_is_400(client):
    response = await client.get("/system-prompt/bogus")
    assert response.status_code == 400

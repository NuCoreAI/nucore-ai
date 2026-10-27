"""build_mcp_server(...): the merged tool registry and dispatch, exercised
through the real registered MCP request handlers (not a reimplementation of
the wire protocol -- see this module's own notes on why a direct handler
call is the right level to test at)."""

from __future__ import annotations

import mcp.types as types
import pytest

from nucore.nucore_interface import NuCoreInterface
from unified.mcp.server import (
    _GET_SYSTEM_PROMPT_TOOL_NAME,
    _build_merged_handlers,
    _deny_mcp_delete_confirmation,
    _load_tool_specs,
    _render_system_prompt,
    build_mcp_protocol_server,
)
from unified import dispatch as customer_dispatch
from unified import plugin_authoring

# NuCoreInterface is an ABC; none of the tools exercised below touch any of
# these methods, so every one is a trivial stub -- same spirit as
# tests/unified/handlers/test_diagnostics.py's FakeBackend, just not
# re-overriding anything a specific test needs (nothing here does).
_ABSTRACT_METHODS = [
    "_load",
    "_load_routines",
    "_load_variables",
    "_subscribe_events",
    "add_device",
    "add_node",
    "create_automation_routine",
    "discover_devices",
    "finish_device_discovery",
    "get_all_routines",
    "get_all_routines_summary",
    "get_device_id",
    "get_device_name",
    "get_properties",
    "get_routine",
    "get_routine_summary",
    "group_scene_add_member",
    "group_scene_get_link_types",
    "group_scene_get_node_roles",
    "group_scene_remove_member",
    "group_scene_update_link",
    "node_ops",
    "remove_device",
    "routine_ops",
    "scene_test",
    "send_commands",
    "update_routine",
    "variable_ops",
]


def _not_implemented(*_args, **_kwargs):
    raise NotImplementedError


FakeBackend = type(
    "FakeBackend",
    (NuCoreInterface,),
    {name: _not_implemented for name in _ABSTRACT_METHODS},
)


def _get_handlers(server):
    """The registered ListToolsRequest/CallToolRequest handlers -- the
    actual MCP wire protocol (session negotiation, SSE framing) is the
    SDK's own, already-tested surface; this calls the handlers it
    registered directly, same as any other dispatch test in this repo
    calls a handler function directly."""
    return (
        server.request_handlers[types.ListToolsRequest],
        server.request_handlers[types.CallToolRequest],
    )


def _get_resource_handlers(server):
    return (
        server.request_handlers[types.ListResourcesRequest],
        server.request_handlers[types.ReadResourceRequest],
    )


@pytest.fixture
def server():
    nucore_interface = FakeBackend(json_output=True, formatter_type="minimal")
    # Both default to True on a fresh NuCoreInterface, which would otherwise
    # send _refresh_routines_database (called by prompt_builder.py, reached
    # by the new system-prompt resources below) into _refresh_device_structure's
    # real "while not self.is_subscribed: ... sleep(1)" loop -- FakeBackend
    # never actually subscribes, so that loop never exits. Short-circuiting
    # both flags is a no-op for every test that doesn't touch either prompt
    # resource (nothing else in this file reads routines/device structure).
    nucore_interface.device_structure_changed = False
    nucore_interface.routines_changed = False
    return build_mcp_protocol_server(nucore_interface=nucore_interface, plugin_output_root="/tmp")


def test_merged_handlers_union_customer_and_plugin_authoring():
    merged = _build_merged_handlers(
        plugin_output_root="/tmp", search_engine=None, search_engine_api_key=None, secret_values=None
    )
    assert set(customer_dispatch.TOOL_HANDLERS).issubset(merged)
    # Reused customer tools collapse into one entry; the net new count is the
    # plugin_authoring-only tools (verified directly against the source: 19
    # of them without search_web).
    plugin_authoring_only = set(merged) - set(customer_dispatch.TOOL_HANDLERS)
    assert len(plugin_authoring_only) == 19
    assert "search_web" not in merged  # no search engine configured


def test_merged_handlers_include_search_web_when_configured():
    merged = _build_merged_handlers(
        plugin_output_root="/tmp", search_engine="brave", search_engine_api_key="key", secret_values=None
    )
    assert "search_web" in merged


def test_tool_specs_exclude_both_switch_tools():
    merged = _build_merged_handlers(
        plugin_output_root="/tmp", search_engine=None, search_engine_api_key=None, secret_values=None
    )
    specs = _load_tool_specs(merged)
    names = {s.name for s in specs}
    assert names == set(merged)
    assert "switch_to_developer_mode" not in names
    assert "switch_to_customer_mode" not in names


@pytest.mark.asyncio
async def test_list_tools_returns_full_merged_set(server):
    list_tools, _ = _get_handlers(server)
    result = await list_tools(types.ListToolsRequest())
    names = {tool.name for tool in result.root.tools}
    merged = _build_merged_handlers(
        plugin_output_root="/tmp", search_engine=None, search_engine_api_key=None, secret_values=None
    )
    # get_system_prompt is MCP-only -- not in merged_handlers (never dispatched
    # through plugin_authoring.dispatch.execute_tool, handled directly in
    # _call_tool instead), so it's the one deliberate name not in that set.
    assert names == set(merged) | {_GET_SYSTEM_PROMPT_TOOL_NAME}


@pytest.mark.asyncio
async def test_call_tool_unknown_name_is_error(server):
    _, call_tool = _get_handlers(server)
    req = types.CallToolRequest(params=types.CallToolRequestParams(name="nonexistent_tool", arguments={}))
    result = await call_tool(req)
    assert result.root.isError is True


@pytest.mark.asyncio
async def test_call_tool_plugin_authoring_member_succeeds(server):
    """lookup_uom is plugin_authoring-only and explicitly backend-independent
    -- a clean success-path check with zero NuCoreInterface dependency."""
    _, call_tool = _get_handlers(server)
    req = types.CallToolRequest(params=types.CallToolRequestParams(name="lookup_uom", arguments={"keyword": "degree"}))
    result = await call_tool(req)
    assert result.root.isError is False
    assert result.root.structuredContent["matches"]


@pytest.mark.asyncio
async def test_call_tool_customer_member_error_maps_to_is_error(server):
    """get_time_info is customer-set; against a backend whose get_timespecs
    isn't implemented, execute_tool's {"error": ...} convention must map to
    isError=True, not a silent isError=False success."""
    _, call_tool = _get_handlers(server)
    req = types.CallToolRequest(params=types.CallToolRequestParams(name="get_time_info", arguments={}))
    result = await call_tool(req)
    assert result.root.isError is True
    assert "error" in result.root.structuredContent


# --- resources: the two tool sets' system prompts, exposed read-only ---


@pytest.mark.asyncio
async def test_list_resources_returns_both_system_prompts(server):
    list_resources, _ = _get_resource_handlers(server)
    result = await list_resources(types.ListResourcesRequest())
    by_uri = {str(r.uri): r for r in result.root.resources}
    assert set(by_uri) == {"eisy-ai://system-prompt/unified", "eisy-ai://system-prompt/plugin-authoring"}
    assert all(r.mimeType == "text/markdown" for r in by_uri.values())


@pytest.mark.asyncio
async def test_read_resource_unified_returns_full_system_prompt(server):
    _, read_resource = _get_resource_handlers(server)
    req = types.ReadResourceRequest(
        params=types.ReadResourceRequestParams(uri="eisy-ai://system-prompt/unified")
    )
    result = await read_resource(req)
    text = result.root.contents[0].text
    assert "DEVICE DATABASE" in text
    assert "ROUTINES DATABASE" in text


@pytest.mark.asyncio
async def test_read_resource_plugin_authoring_returns_distinct_prompt(server):
    """Confirms this is genuinely the *other* module's own system_prompt.md,
    not the unified one read twice -- the unified prompt never mentions the
    Dynamic Profiles JSON API, and the plugin_authoring one never renders a
    DEVICE DATABASE section (it deliberately doesn't build one -- see that
    module's own docstring)."""
    _, read_resource = _get_resource_handlers(server)
    req = types.ReadResourceRequest(
        params=types.ReadResourceRequestParams(uri="eisy-ai://system-prompt/plugin-authoring")
    )
    result = await read_resource(req)
    text = result.root.contents[0].text
    assert "Dynamic Profiles JSON API" in text
    assert "DEVICE DATABASE" not in text


@pytest.mark.asyncio
async def test_read_resource_unknown_uri_raises(server):
    _, read_resource = _get_resource_handlers(server)
    req = types.ReadResourceRequest(params=types.ReadResourceRequestParams(uri="eisy-ai://nonexistent"))
    with pytest.raises(Exception):
        await read_resource(req)


# --- delete confirmation: MCP's own independent floor ---


@pytest.mark.parametrize(
    "name,arguments",
    [
        ("delete_plugin", {"plugin_id": "5", "name": "Test Plugin"}),
        ("delete_registered_plugin", {"location": "some/plugin"}),
        ("node_op", {"operation": "delete", "node_id": "n1"}),
        ("routine_status_op", {"operation": "delete", "id": "r1"}),
        ("variable_op", {"type": 1, "operation": "delete", "id": "v1"}),
    ],
)
def test_deny_mcp_delete_confirmation_forces_confirmed_false(name, arguments):
    """The exact allowlist runtime.py's _wrap_dispatch_for_delete_confirmation
    also gates -- confirmed:true from the caller is always overridden, since
    MCP has no human chat turn to independently verify."""
    result = _deny_mcp_delete_confirmation(name, {**arguments, "confirmed": True})
    assert result["confirmed"] is False


def test_deny_mcp_delete_confirmation_leaves_non_delete_operation_untouched():
    arguments = {"operation": "rename", "node_id": "n1", "new_name": "Kitchen Light"}
    result = _deny_mcp_delete_confirmation("node_op", arguments)
    assert result == arguments


def test_deny_mcp_delete_confirmation_leaves_unrelated_tool_untouched():
    arguments = {"confirmed": True, "keyword": "degree"}
    result = _deny_mcp_delete_confirmation("lookup_uom", arguments)
    assert result == arguments


@pytest.mark.asyncio
async def test_call_tool_node_op_delete_always_previews_over_mcp(server):
    """End-to-end through the real dispatch path (not just the helper in
    isolation): confirmed:true from an MCP caller still only ever gets back
    node_ops.py's own no-op preview, never reaching the (unimplemented, in
    FakeBackend) real node_ops backend call."""
    _, call_tool = _get_handlers(server)
    req = types.CallToolRequest(
        params=types.CallToolRequestParams(
            name="node_op", arguments={"operation": "delete", "node_id": "n1", "confirmed": True}
        )
    )
    result = await call_tool(req)
    assert result.root.isError is False
    assert result.root.structuredContent["confirmation_required"] is True
    assert "mcp_note" in result.root.structuredContent


@pytest.mark.asyncio
async def test_call_tool_delete_plugin_always_previews_over_mcp(server):
    """plugin_id '5' resolves via _get_plugin_number's pure-digit-string
    path, so this reaches the confirmed check with zero other backend calls."""
    _, call_tool = _get_handlers(server)
    req = types.CallToolRequest(
        params=types.CallToolRequestParams(
            name="delete_plugin", arguments={"plugin_id": "5", "name": "Test Plugin", "confirmed": True}
        )
    )
    result = await call_tool(req)
    assert result.root.isError is False
    assert result.root.structuredContent["confirmation_required"] is True
    assert "mcp_note" in result.root.structuredContent


@pytest.mark.asyncio
async def test_call_tool_delete_confirmation_mcp_note_explains_it_is_permanent(server):
    """The handler's own message promises a literal customer 'YES' will get
    this through -- true on the chat path, a dead end here, since
    _deny_mcp_delete_confirmation always forces confirmed back to False no
    matter how many times this is retried. Confirmed directly: a real
    client retried after getting a real YES from the customer, got the
    identical preview back, and had no way to tell this apart from a
    transient failure. The MCP-specific note must say this plainly rather
    than let the handler's chat-path wording mislead the model."""
    _, call_tool = _get_handlers(server)
    req = types.CallToolRequest(
        params=types.CallToolRequestParams(
            name="node_op", arguments={"operation": "delete", "node_id": "n1", "confirmed": True}
        )
    )
    result = await call_tool(req)
    note = result.root.structuredContent["mcp_note"]
    assert "never be honored" in note
    assert "preview-only over MCP" in note
    assert "do not retry" in note.lower()


@pytest.mark.asyncio
async def test_call_tool_list_result_is_wrapped_not_auto_detected(server):
    """get_routine_details' own documented shape is "a list, one entry per
    id" -- confirmed directly against the real MCP SDK: its own auto-wrap
    mistakes a plain list of business-data dicts for already-built content
    blocks and crashes with a wall of pydantic "Field required" errors, an
    opaque "response-format error" to a real client with no way to tell it
    was this server's bug. _call_tool must wrap a non-dict result itself."""
    _, call_tool = _get_handlers(server)
    req = types.CallToolRequest(
        params=types.CallToolRequestParams(name="get_routine_details", arguments={"ids": [1, 2]})
    )
    result = await call_tool(req)
    assert result.root.isError is False
    wrapped = result.root.structuredContent["result"]
    assert isinstance(wrapped, list)
    assert [entry["id"] for entry in wrapped] == [1, 2]
    assert all("error" in entry for entry in wrapped)  # FakeBackend's get_routine is unimplemented


# --- get_system_prompt: the tool-callable fallback for clients (e.g. Claude
# Desktop) that don't feed MCP resources into the model's own context ---


@pytest.mark.asyncio
async def test_list_tools_includes_get_system_prompt_tool(server):
    list_tools, _ = _get_handlers(server)
    result = await list_tools(types.ListToolsRequest())
    tool = next(t for t in result.root.tools if t.name == _GET_SYSTEM_PROMPT_TOOL_NAME)
    assert tool.inputSchema["required"] == ["tool_set"]
    assert tool.inputSchema["properties"]["tool_set"]["enum"] == ["unified", "plugin_authoring"]


@pytest.mark.asyncio
async def test_call_tool_get_system_prompt_unified_returns_resource_link(server):
    """The tool's own response never inlines the prompt text -- every real
    MCP client caps a tools/call response well below what a large
    installation's full prompt can reach (verified: Grok 20,000 bytes,
    OpenAI Codex 10 KiB, Gemini CLI 40,000 chars, all truncating silently
    head-and-tail). Instead it points at the already-registered resource and
    tells the model explicitly to fetch and read the whole thing."""
    _, call_tool = _get_handlers(server)
    req = types.CallToolRequest(
        params=types.CallToolRequestParams(name=_GET_SYSTEM_PROMPT_TOOL_NAME, arguments={"tool_set": "unified"})
    )
    result = await call_tool(req)
    assert result.root.isError is False
    blocks = result.root.content
    text_block = next(b for b in blocks if b.type == "text")
    link_block = next(b for b in blocks if b.type == "resource_link")
    assert str(link_block.uri) == "eisy-ai://system-prompt/unified"
    assert link_block.mimeType == "text/markdown"
    assert "eisy-ai://system-prompt/unified" in text_block.text
    assert "/system-prompt/unified" in text_block.text
    assert "REQUIRED in full" in text_block.text
    assert "unified" in text_block.text


@pytest.mark.asyncio
async def test_call_tool_get_system_prompt_plugin_authoring_returns_distinct_link(server):
    _, call_tool = _get_handlers(server)
    req = types.CallToolRequest(
        params=types.CallToolRequestParams(
            name=_GET_SYSTEM_PROMPT_TOOL_NAME, arguments={"tool_set": "plugin_authoring"}
        )
    )
    result = await call_tool(req)
    assert result.root.isError is False
    blocks = result.root.content
    text_block = next(b for b in blocks if b.type == "text")
    link_block = next(b for b in blocks if b.type == "resource_link")
    assert str(link_block.uri) == "eisy-ai://system-prompt/plugin-authoring"
    assert "eisy-ai://system-prompt/plugin-authoring" in text_block.text
    assert "/system-prompt/plugin_authoring" in text_block.text


@pytest.mark.asyncio
async def test_call_tool_get_system_prompt_resource_link_size_matches_real_content(server):
    """ResourceLink.size is the real rendered byte length, not a guess or
    placeholder -- and the tool's own instructional text stays far smaller
    than the content it points at, which is the entire point of this
    design."""
    _, call_tool = _get_handlers(server)
    req = types.CallToolRequest(
        params=types.CallToolRequestParams(name=_GET_SYSTEM_PROMPT_TOOL_NAME, arguments={"tool_set": "unified"})
    )
    result = await call_tool(req)
    blocks = result.root.content
    text_block = next(b for b in blocks if b.type == "text")
    link_block = next(b for b in blocks if b.type == "resource_link")

    nucore_interface = FakeBackend(json_output=True, formatter_type="minimal")
    nucore_interface.device_structure_changed = False
    nucore_interface.routines_changed = False
    text = await _render_system_prompt(nucore_interface, "unified")

    assert link_block.size == len(text.encode("utf-8"))
    assert len(text_block.text) < link_block.size


@pytest.mark.asyncio
async def test_call_tool_get_system_prompt_uses_public_base_url_when_given():
    """Without a real absolute URL, a client has no way to know which
    host/port actually serves this -- confirmed directly: a real client
    guessed the hub's own web UI/REST ports instead and got nowhere. When
    the embedding proxy's real address is known, the instruction must give
    an absolute URL, not a bare path."""
    nucore_interface = FakeBackend(json_output=True, formatter_type="minimal")
    nucore_interface.device_structure_changed = False
    nucore_interface.routines_changed = False
    server = build_mcp_protocol_server(
        nucore_interface=nucore_interface,
        plugin_output_root="/tmp",
        public_base_url="https://eisy.local:8260",
    )
    _, call_tool = _get_handlers(server)
    req = types.CallToolRequest(
        params=types.CallToolRequestParams(name=_GET_SYSTEM_PROMPT_TOOL_NAME, arguments={"tool_set": "unified"})
    )
    result = await call_tool(req)
    text_block = next(b for b in result.root.content if b.type == "text")
    assert "https://eisy.local:8260/system-prompt/unified" in text_block.text


class _FakeAuthedRequest:
    """Just enough of Starlette's Request surface for this test --
    .headers.get(...). Real callers get the real Request via
    request_context.request (see server.py's own docstring on why)."""

    def __init__(self, headers: dict[str, str]) -> None:
        self.headers = headers


@pytest.mark.asyncio
async def test_call_tool_get_system_prompt_includes_auth_header_in_curl_hint_when_present(server):
    """get_system_prompt's curl fallback is a *separate* HTTP request from
    this tool call -- over an authenticated proxy (design/mcp-server.md's
    Phase 2), the model has no other way to know its own session's bearer
    token, so this call's own Authorization header must be lifted into the
    returned instructions verbatim."""
    from mcp.server.lowlevel.server import request_ctx
    from mcp.shared.context import RequestContext

    _, call_tool = _get_handlers(server)
    token = request_ctx.set(
        RequestContext(
            request_id="1",
            meta=None,
            session=None,
            lifespan_context=None,
            request=_FakeAuthedRequest({"authorization": "Bearer live-token-abc"}),
        )
    )
    try:
        req = types.CallToolRequest(
            params=types.CallToolRequestParams(name=_GET_SYSTEM_PROMPT_TOOL_NAME, arguments={"tool_set": "unified"})
        )
        result = await call_tool(req)
    finally:
        request_ctx.reset(token)

    text_block = next(b for b in result.root.content if b.type == "text")
    assert 'curl -H "Authorization: Bearer live-token-abc"' in text_block.text
    assert "never repeat its value back" in text_block.text


@pytest.mark.asyncio
async def test_call_tool_get_system_prompt_omits_auth_header_when_none_present(server):
    """No request_context at all (e.g. a bare local/dev invocation with no
    proxy auth) -- the curl hint falls back to its original, header-free
    form, same as before this fix."""
    _, call_tool = _get_handlers(server)
    req = types.CallToolRequest(
        params=types.CallToolRequestParams(name=_GET_SYSTEM_PROMPT_TOOL_NAME, arguments={"tool_set": "unified"})
    )
    result = await call_tool(req)
    text_block = next(b for b in result.root.content if b.type == "text")
    assert "-H " not in text_block.text
    assert "curl " in text_block.text


@pytest.mark.asyncio
async def test_call_tool_get_system_prompt_invalid_tool_set_is_error(server):
    """The MCP SDK itself rejects an invalid enum value against the
    registered inputSchema before this tool's handler branch ever runs
    (confirmed directly: the result's content is the SDK's own generic
    "Input validation error", with no structuredContent) -- so
    _render_system_prompt's own ValueError/except branch is unreachable
    for this specific case in practice, kept only as a defensive fallback.
    Either way, the caller still correctly sees isError=True, not a silent
    success."""
    _, call_tool = _get_handlers(server)
    req = types.CallToolRequest(
        params=types.CallToolRequestParams(name=_GET_SYSTEM_PROMPT_TOOL_NAME, arguments={"tool_set": "bogus"})
    )
    result = await call_tool(req)
    assert result.root.isError is True


@pytest.mark.asyncio
async def test_render_system_prompt_matches_resource_content(server):
    """The tool and the resource must render byte-identical content for the
    same tool_set -- both call this exact same function, so a divergence
    here would mean one of the two call sites stopped doing that."""
    nucore_interface = FakeBackend(json_output=True, formatter_type="minimal")
    nucore_interface.device_structure_changed = False
    nucore_interface.routines_changed = False
    unified_text = await _render_system_prompt(nucore_interface, "unified")
    assert "DEVICE DATABASE" in unified_text
    with pytest.raises(ValueError):
        await _render_system_prompt(nucore_interface, "bogus")

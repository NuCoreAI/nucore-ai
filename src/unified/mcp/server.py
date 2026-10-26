"""``build_mcp_server(...)``: wraps the customer and plugin-authoring tool
sets' existing dispatch as an MCP server. See design/mcp-server.md for the
full design -- this is a thin adapter, nothing more: ``tool_*.json`` ->
``ToolSpec`` (existing loader) -> ``mcp.types.Tool`` for ``list_tools``;
``call_tool`` -> the existing, unmodified
``plugin_authoring.dispatch.execute_tool`` (which also dispatches
customer-set tools, via the merged handlers dict below) -> MCP result.

Deliberately has no authentication and no scope tiering: this server is
served over a Unix domain socket whose filesystem permissions are the only
access control (see run_mcp_server.py), the same trust model
run_unified_runtime.py's websocket server already uses for its own Unix
socket. A network-reachable proxy, in whichever repo embeds this library,
is responsible for any real authentication before traffic ever reaches this
socket -- see design/mcp-server.md's "AuthHook" contract.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import mcp.types as types
from mcp.server.lowlevel import Server
from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from pydantic import AnyUrl
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from nucore import NuCoreInterface
from utils import get_logger

from .. import dispatch as customer_dispatch
from .. import plugin_authoring
from .. import prompt_builder
from ..adapters.base_adapter import LLMAdapter
from ..runtime import _DELETE_CONFIRMATION_OPERATION_TOOLS, _DELETE_CONFIRMATION_TOOLS

logger = get_logger(__name__)

_CUSTOMER_TOOLS_DIR = Path(customer_dispatch.__file__).resolve().parent / "tools"

_UNIFIED_PROMPT_URI = "eisy-ai://system-prompt/unified"
_PLUGIN_AUTHORING_PROMPT_URI = "eisy-ai://system-prompt/plugin-authoring"
_GET_SYSTEM_PROMPT_TOOL_NAME = "get_system_prompt"


async def _render_system_prompt(nucore_interface: NuCoreInterface, tool_set: str) -> str:
    """Shared by the system-prompt resources and the ``get_system_prompt``
    tool below -- one real call into each tool set's own existing
    prompt-builder, no duplicated rendering logic."""
    if tool_set == "unified":
        return await prompt_builder.build_system_prompt(nucore_interface)
    if tool_set == "plugin_authoring":
        sections = await plugin_authoring.build_system_prompt_sections(nucore_interface)
        return "\n\n".join(sections)
    raise ValueError(f"tool_set must be 'unified' or 'plugin_authoring', got {tool_set!r}")


def _build_merged_handlers(
    *,
    plugin_output_root: str,
    search_engine: str | None,
    search_engine_api_key: str | None,
    secret_values: list[str] | None,
) -> dict[str, Any]:
    """One process-wide handlers dict covering both tool sets. Built once at
    server-construction time, not per MCP session -- there is no caller
    identity to key a fresh ``EvidenceLedger``/discovery-tool cap off of
    here (see this module's own docstring on auth), so the discovery tools'
    per-connection caps (``MAX_GITHUB_QUERIES`` etc.) become process-wide
    across every caller that reaches this socket, same as
    ``nucore_interface`` itself already is. Accepted tradeoff, not an
    oversight -- revisit only if this server ever needs to distinguish
    between callers."""
    handlers: dict[str, Any] = dict(customer_dispatch.TOOL_HANDLERS)
    handlers.update(
        plugin_authoring.dispatch.build_tool_handlers(
            ledger=plugin_authoring.EvidenceLedger(),
            search_engine=search_engine,
            search_engine_api_key=search_engine_api_key,
            secret_values=secret_values or [],
            plugin_output_root=plugin_output_root,
            get_user_id=None,
        )
    )
    return handlers


def _deny_mcp_delete_confirmation(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """MCP has no equivalent of runtime.py's ``_wrap_dispatch_for_delete_confirmation``
    floor (a real human's own literal chat turn being exactly "YES") -- there is no chat
    turn at all on this transport. Rather than silently losing that floor and letting an
    MCP-driven model decide for itself when a delete is confirmed, force ``confirmed`` to
    False for exactly the tools/operations runtime.py also gates, so each one always
    resolves to its own handler's existing no-op preview over MCP. This never touches the
    chat path -- importing these two constants is a read-only reference to runtime.py's
    own allowlist, kept in sync with it on purpose, with zero edits to runtime.py or its
    wiring."""
    is_delete_attempt = name in _DELETE_CONFIRMATION_TOOLS or (
        name in _DELETE_CONFIRMATION_OPERATION_TOOLS and arguments.get("operation") == "delete"
    )
    return {**arguments, "confirmed": False} if is_delete_attempt else arguments


def _load_tool_specs(merged_handlers: dict[str, Any]) -> list[Any]:
    """Tool specs from both tool sets' ``tools/`` directories, filtered to
    *merged_handlers*'s keys -- the one filter that drops both tool sets'
    orphan mode-switch files (``tool_switch_to_developer_mode.json``,
    ``tool_switch_to_customer_mode.json``), which exist on disk but are
    intercepted by the agentic loop itself and have no meaning to an MCP
    host that supplies its own orchestration."""
    paths = sorted(_CUSTOMER_TOOLS_DIR.glob("tool_*.json")) + sorted(plugin_authoring.TOOLS_DIR.glob("tool_*.json"))
    specs = LLMAdapter.tools_spec_from_files(paths)
    return [s for s in specs if s.name in merged_handlers]


def build_mcp_protocol_server(
    *,
    nucore_interface: NuCoreInterface,
    plugin_output_root: str,
    search_engine: str | None = None,
    search_engine_api_key: str | None = None,
    secret_values: list[str] | None = None,
    server_name: str = "eisy-ai",
    public_base_url: str | None = None,
) -> Server:
    """Build the ``mcp.server.lowlevel.Server`` with ``list_tools``/
    ``call_tool`` registered -- split out from ``build_mcp_server`` (which
    wraps this in the actual Streamable HTTP ASGI app) so tests can call the
    registered handlers directly via ``server.request_handlers`` without
    reaching through Starlette's routing internals.

    *public_base_url*: this server itself only ever binds a Unix domain
    socket (see run_mcp_server.py) and genuinely has no way to know its own
    externally-reachable address -- only whatever network-facing proxy
    embeds it does (confirmed directly: eisy-ai's start_eisyai.py hardcodes
    its proxy to TLS port 8260 against a cert whose CN is "eisy.local", none
    of which this module can see). Without this, the ``get_system_prompt``
    tool's plain-HTTP instructions can only name a relative path, which a
    real client has no way to resolve correctly (confirmed directly: a real
    client guessed the hub's own web UI/REST ports instead and got a 401).
    Pass the real scheme://host:port the embedding proxy is reachable at
    (e.g. ``"https://eisy.local:8260"``) to get a correct absolute URL
    instead; ``None`` falls back to a relative-path description, which is
    fine for tests/local dev where there's no real external address."""
    merged_handlers = _build_merged_handlers(
        plugin_output_root=plugin_output_root,
        search_engine=search_engine,
        search_engine_api_key=search_engine_api_key,
        secret_values=secret_values,
    )
    specs = _load_tool_specs(merged_handlers)
    logger.info("MCP server: %d tools available (%s)", len(specs), server_name)

    server: Server = Server(server_name)

    @server.list_tools()
    async def _list_tools() -> list[types.Tool]:
        tools = [types.Tool(name=s.name, description=s.description, inputSchema=s.json_schema) for s in specs]
        tools.append(
            types.Tool(
                name=_GET_SYSTEM_PROMPT_TOOL_NAME,
                description="Returns the complete system prompt for one tool set -- the same "
                "CRITICAL RULES, per-concept operating rules, host environment, live DEVICE "
                "DATABASE/ROUTINES DATABASE (or, for plugin_authoring, evidence/developer-identity "
                "rules and installed-plugins listing) that a chat session already has standing in "
                "context automatically. Call this FIRST, before any device/group/routine/variable/"
                "plugin tool, unless you already have this content in context: every id those "
                "tools expect (e.g. 'use the id exactly as shown in DEVICE DATABASE') comes from "
                "here, not from any other tool's own output. MCP-only -- the chat interface already "
                "has this standing in context and never needs to call it.",
                inputSchema={
                    "type": "object",
                    "required": ["tool_set"],
                    "properties": {
                        "tool_set": {
                            "type": "string",
                            "enum": ["unified", "plugin_authoring"],
                            "description": "'unified' for customer/device/scene/routine/variable/"
                            "plugin tools; 'plugin_authoring' for developer/plugin-generation tools.",
                        }
                    },
                },
            )
        )
        return tools

    @server.list_resources()
    async def _list_resources() -> list[types.Resource]:
        return [
            types.Resource(
                uri=_UNIFIED_PROMPT_URI,
                name="system-prompt-unified",
                description="The complete customer-facing system prompt: all operating rules "
                "(devices, groups/scenes, folders, variables, routines, plugins), host "
                "environment, the live DEVICE DATABASE and ROUTINES DATABASE, and current user "
                "preferences/time -- the same content a chat session already has standing in "
                "context for the customer tool set. Tool ids/behavior conventions assume this has "
                "been read first.",
                mimeType="text/markdown",
            ),
            types.Resource(
                uri=_PLUGIN_AUTHORING_PROMPT_URI,
                name="system-prompt-plugin-authoring",
                description="The complete developer/plugin-authoring system prompt: "
                "evidence-gathering requirements, developer-identity commissioning, "
                "secret-scan/overwrite conventions, and a live installed-plugins listing -- the "
                "same content a chat session already has standing in context for the "
                "plugin_authoring tool set. A separate prompt from the customer one above; read "
                "whichever matches the tools you're about to call.",
                mimeType="text/markdown",
            ),
        ]

    @server.read_resource()
    async def _read_resource(uri: AnyUrl) -> Iterable[ReadResourceContents]:
        if str(uri) == _UNIFIED_PROMPT_URI:
            text = await _render_system_prompt(nucore_interface, "unified")
            return [ReadResourceContents(content=text, mime_type="text/markdown")]
        if str(uri) == _PLUGIN_AUTHORING_PROMPT_URI:
            text = await _render_system_prompt(nucore_interface, "plugin_authoring")
            return [ReadResourceContents(content=text, mime_type="text/markdown")]
        raise ValueError(f"unknown resource '{uri}'")

    @server.call_tool()
    async def _call_tool(name: str, arguments: dict[str, Any]) -> Any:
        if name == _GET_SYSTEM_PROMPT_TOOL_NAME:
            tool_set = arguments.get("tool_set")
            try:
                text = await _render_system_prompt(nucore_interface, tool_set)
            except ValueError as exc:
                result = {"error": str(exc)}
            else:
                # Never send this text through the tools/call response itself --
                # every real MCP client caps that response well below a large
                # installation's real size (verified: Grok 20,000 bytes, OpenAI
                # Codex 10 KiB, Gemini CLI 40,000 chars, all truncating silently
                # head-and-tail). Point at the content instead and tell the
                # model explicitly to fetch and read it in full -- a partial
                # read of DEVICE DATABASE/ROUTINES DATABASE can silently drop
                # the one profile/device a query actually needed. Two retrieval
                # paths, HTTP first: resources/read is not reliably available
                # on real clients (confirmed directly, twice -- Claude Desktop
                # and another MCP client both reported having no tool able to
                # open an MCP resource at all), while a plain HTTP GET works
                # with any shell/curl-capable client regardless of MCP resource
                # support.
                uri = _UNIFIED_PROMPT_URI if tool_set == "unified" else _PLUGIN_AUTHORING_PROMPT_URI
                http_path = f"/system-prompt/{tool_set}"
                # A bare relative path means nothing to a model that has no
                # way to introspect its own MCP transport's underlying host --
                # confirmed directly: a real client guessed the hub's own web
                # UI/REST ports instead of the actual MCP proxy port and got
                # nowhere. public_base_url (see this function's own docstring)
                # is the embedding proxy's real, externally-reachable address;
                # use it to give an absolute URL when it's known.
                http_url = f"{public_base_url.rstrip('/')}{http_path}" if public_base_url else http_path
                size = len(text.encode("utf-8"))
                return types.CallToolResult(
                    content=[
                        types.TextContent(
                            type="text",
                            text=(
                                f"The {tool_set} system prompt is {size} bytes -- large, and "
                                "REQUIRED in full. It contains the complete DEVICE DATABASE/"
                                "ROUTINES DATABASE (every device/group's id and name, and every "
                                "property/command's name and valid values) plus the operating "
                                "rules. A partial or truncated read risks missing a device, "
                                "property, command, or rule -- resolving a customer's phrase to "
                                "the right device/property requires seeing the complete content "
                                "at once. It is also available as plain text via a GET request to "
                                f"{http_url} -- fetch it with curl (or any HTTP client reachable on "
                                "this network) and save it to a local file. Do not guess a different "
                                "host/port for this (e.g. the hub's own web UI or REST API ports) -- "
                                f"use exactly {http_url}. Or, if this session supports MCP resources, "
                                f"read {uri} directly. Either way, read the ENTIRE file/resource into "
                                "your context before answering any question about devices, "
                                "properties, commands, or routines. Do not attempt to read only part "
                                "of it, or search/grep within a partial copy."
                            ),
                        ),
                        types.ResourceLink(
                            type="resource_link",
                            name=f"system-prompt-{tool_set.replace('_', '-')}",
                            uri=uri,
                            description=f"The complete {tool_set} system prompt -- read in full, not partially.",
                            mimeType="text/markdown",
                            size=size,
                        ),
                    ],
                )
        elif name not in merged_handlers:
            raise ValueError(f"unknown tool '{name}'")
        else:
            arguments = _deny_mcp_delete_confirmation(name, arguments)
            result = await plugin_authoring.dispatch.execute_tool(
                name, arguments, nucore_interface=nucore_interface, tool_handlers=merged_handlers
            )
        if isinstance(result, dict) and "error" in result:
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=json.dumps(result))],
                structuredContent=result,
                isError=True,
            )
        # A plain dict result is auto-wrapped by the SDK's call_tool decorator
        # as both structuredContent and a JSON TextContent (isError=False) --
        # see mcp.server.lowlevel.server.Server.call_tool's own source.
        return result

    return server


def build_mcp_server(
    *,
    nucore_interface: NuCoreInterface,
    plugin_output_root: str,
    search_engine: str | None = None,
    search_engine_api_key: str | None = None,
    secret_values: list[str] | None = None,
    server_name: str = "eisy-ai",
    public_base_url: str | None = None,
) -> Starlette:
    """Build the MCP Streamable HTTP ASGI app, served at ``/mcp``, plus a
    plain-HTTP ``/system-prompt/{tool_set}`` twin (see
    ``_system_prompt_http_endpoint`` below). Callers (``run_mcp_server.py``)
    serve this over a Unix domain socket; see that module for the
    socket-permission handling this design depends on for access control.

    *public_base_url*: see ``build_mcp_protocol_server``'s own docstring --
    this is the only thing threaded through just to build a correct absolute
    URL in the ``get_system_prompt`` tool's instructions."""
    server = build_mcp_protocol_server(
        nucore_interface=nucore_interface,
        plugin_output_root=plugin_output_root,
        search_engine=search_engine,
        search_engine_api_key=search_engine_api_key,
        secret_values=secret_values,
        server_name=server_name,
        public_base_url=public_base_url,
    )
    session_manager = StreamableHTTPSessionManager(app=server, json_response=False, stateless=False)

    @contextlib.asynccontextmanager
    async def _lifespan(_app: Starlette):
        async with session_manager.run():
            yield

    async def _system_prompt_http_endpoint(request: Request) -> Response:
        """Plain-HTTP twin of the ``eisy-ai://system-prompt/*`` resources and
        the ``get_system_prompt`` tool -- same underlying render, reached by a
        real GET instead of MCP framing. Exists because `resources/read`
        turned out not to be a given on real MCP clients (confirmed directly
        against two of them), while a plain GET works with any shell/curl-
        capable client. Forwarded through eisy-ai's proxy under the same
        AuthHook check as ``/mcp`` -- that proxy mounts at ``/`` and applies
        its auth check to every path before forwarding, so this gets the same
        coverage with no changes needed on that side."""
        tool_set = request.path_params["tool_set"]
        try:
            text = await _render_system_prompt(nucore_interface, tool_set)
        except ValueError as exc:
            return Response(str(exc), status_code=400)
        return Response(text, media_type="text/markdown")

    # A *class instance* endpoint, not a function/method or a Mount: Route
    # treats a plain function/method endpoint as `func(request) -> response`
    # (see starlette.routing.Route.__init__), the wrong calling convention
    # for a raw ASGI sub-application like handle_request(scope, receive,
    # send) -- verified directly, this silently mis-dispatches otherwise.
    # Mount works too but only matches/redirects exact-vs-trailing-slash
    # paths in a way that breaks a client posting to exactly "/mcp" with no
    # trailing slash (verified directly: a real client got a 307 redirect
    # it had to follow) -- Route with a class-instance endpoint, mirroring
    # the official SDK's own fastmcp.server.StreamableHTTPASGIApp pattern,
    # matches "/mcp" exactly with no redirect.
    class _McpASGIApp:
        async def __call__(self, scope, receive, send) -> None:
            await session_manager.handle_request(scope, receive, send)

    return Starlette(
        routes=[
            Route("/mcp", endpoint=_McpASGIApp()),
            Route("/system-prompt/{tool_set}", endpoint=_system_prompt_http_endpoint),
        ],
        lifespan=_lifespan,
    )

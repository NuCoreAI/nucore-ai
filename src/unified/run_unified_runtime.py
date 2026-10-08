from __future__ import annotations

import argparse,os
import asyncio
import ctypes
import ctypes.util
import functools, json
import ipaddress
import ssl
import uuid
from pathlib import Path
from typing import Any, NamedTuple

import websockets
from dotenv import load_dotenv

from unified import plugin_authoring
from unified.models import IntentHandlerResult
from unified.plugin_authoring import search_result_enrichment
from unified.plugin_authoring.handlers import discovery as plugin_authoring_discovery
from unified.provider_dispatch_adapter import ProviderDispatchLLMAdapter
from unified.runtime import DispatchBundle, ToolSetBundle, UnifiedRuntime, resolve_llm_profile
from unified.runtime_config import _load_runtime_config
from unified.provider_clients import resolve_env_placeholder
from unified.session_store import SessionStore
from unified.dispatch_builder import build_default_dispatch_adapter
from unified.stream_handler import StreamHandler
from nucore import NuCoreInterface, PromptFormatTypes
from utils import configure_logging, configure_prompt_logging, get_logger


logger = get_logger(__name__)

# Load secrets/.env directly rather than relying on VS Code's debug-adapter
# "envFile" mechanism -- that only applies when launched via the debugger
# (never from a plain terminal), and only affects the debuggee's own
# environment after launch.json's own ${env:...} substitution has already
# run, so it can't feed CLI arg values either way. Loading it here works
# identically regardless of how this script is started, and never
# overwrites variables already set in the real environment (load_dotenv's
# default behavior).
_default_env_file = Path(__file__).resolve().parents[2] / "secrets" / ".env"
if _default_env_file.exists():
    load_dotenv(_default_env_file)

class EisyUIContext:
    """Class to represent context messages from the Eisy UI.

    One instance per websocket connection -- NOT a shared/global object.
    Concurrent connections used to clobber a single module-level instance's
    ``context``/``message`` (and would have done the same to ``user_id``),
    so each connection (and each --query/REPL invocation) now constructs its
    own.
    """
    def __init__(self):
        self.context:dict = None
        self.message:str = None
        self.user_id:str | None = None
        self.is_developer:bool | None = None
        self.client_id:str | None = None
        # Sticky active tool set for this connection -- None means "use the
        # runtime's own default" (see UnifiedRuntime.default_tool_set / the
        # runtime config's default_profile_name). Set explicitly either by
        # the /developer or /customer chat command (see process_message) or
        # by UnifiedRuntime.handle_query's returned "active_tool_set" after
        # a model-driven mid-turn switch -- see
        # design/developers/merged-toolsets.md.
        self.active_tool_set: str | None = None

    _SWITCH_COMMANDS = {"/developer": "plugin_authoring", "/customer": "unified"}

    def process_message(self, message_data: str)->str:
        """
            Process an incoming message from the Eisy UI.
            If it's a context message, store the context and return None.
            If it's a user message, prepend the context (if any) and return the
            combined message.
            Always keep the last context since the UI sends a context with every user
            interaction with the UI, so the context is always up-to-date for the latest user message.

            :param message_data: The raw JSON string received from the WebSocket, expected to contain a "type" field.
            :return str: The processed message to send to the runtime, or None if no message should be sent.

        """
        try:
            message= json.loads(message_data)
            type = message.get("type", "")
            if type == "context":
                self.context = message.get("context", None)
                user = (self.context or {}).get("user") or {}
                # Keep the last known identity fields if a later context
                # payload omits them, rather than clearing them -- safer
                # degradation.
                self.user_id = user.get("username") or self.user_id
                # isDeveloper is a real boolean -- False is a legitimate
                # value that must not be discarded in favor of a stale
                # prior True the way a truthy `or` fallback would.
                if user.get("isDeveloper") is not None:
                    self.is_developer = user.get("isDeveloper")
                self.client_id = (self.context or {}).get("clientId") or self.client_id
                self.message = None
                return None
            if type == "message":
                self.message = message.get("message", None)
                text = self.message.strip() if self.message else None
                # Explicit, user-triggered tool-set switch -- sticky, same
                # operation the model's own switch_to_developer_mode/
                # switch_to_customer_mode tool performs (see
                # design/developers/merged-toolsets.md). If the target isn't
                # actually enabled on this installation, UnifiedRuntime
                # falls back to its own default tool set rather than erroring
                # -- the model-driven tool path (not this one) is where a
                # disabled target gets explained back to the user.
                if text in self._SWITCH_COMMANDS:
                    self.active_tool_set = self._SWITCH_COMMANDS[text]
                    return None
                return text
            logger.warning(f"Received message with unrecognized type: {type}")
            return None
        except Exception as e:
            # it's a regular string
            return message_data.strip() if message_data else None

    def get_context(self)->dict:
        """Get the current context stored in the UI context object."""
        return self.context

    def get_message(self)->str:
        """Get the last user message stored in the UI context object."""
        return self.message

    def get_user_id(self) -> str | None:
        """The authenticated user's durable id (an email address), sourced
        from the context payload's ``user.username`` -- used as this
        conversation's session_id instead of a fresh uuid4 per connection,
        so identity (and therefore conversation history) survives a
        reconnect. None if no context carrying one has been seen yet on
        this connection."""
        return self.user_id

    def get_is_developer(self) -> bool | None:
        """Whether the authenticated user is a developer, sourced from the
        context payload's ``user.isDeveloper``. None if no context carrying
        one has been seen yet on this connection."""
        return self.is_developer

    def get_client_id(self) -> str | None:
        """The UI client's id, sourced from the context payload's top-level
        ``clientId``. None if no context carrying one has been seen yet on
        this connection."""
        return self.client_id

    def get_active_tool_set(self) -> str | None:
        """This connection's sticky active tool set, if `/developer`/
        `/customer` has been typed or a mid-turn model-driven switch has
        happened. None means "use the runtime's own default"."""
        return self.active_tool_set

    def set_active_tool_set(self, tool_set_name: str) -> None:
        """Called after a turn that ended in a different tool set than it
        started in (a mid-round model-driven switch), so the *next* turn on
        this connection starts there too -- sticky either way it happens."""
        self.active_tool_set = tool_set_name


def _build_parser() -> argparse.ArgumentParser:
    """Build and return the CLI argument parser for the unified runtime."""
    parser = argparse.ArgumentParser(description="Run standalone unified runtime")
    parser.add_argument(
        "--runtime-config",
        type=str,
        default=None,
        help="Required path to runtime profile JSON containing top-level 'nucore_runtime'",
    )
    parser.add_argument(
        "--secrets-file",
        type=str,
        default=None,
        help="Optional path to a JSON object of secret key/value pairs (for example API keys)",
    )
    parser.add_argument(
        "--query",
        type=str,
        default=None,
        help="Single query mode (non-interactive)",
    )
    parser.add_argument(
        "--websocket-port",
        type=int,
        default=None,
        help="Run as a WebSocket server on this port instead of --query/REPL mode; "
             "each connection gets its own session, every received message is treated "
             "as a query, and responses stream back over the same connection. Ignored "
             "when --websocket-host is a Unix socket path.",
    )
    parser.add_argument(
        "--websocket-host",
        type=str,
        default="0.0.0.0",
        help="IP address to bind the WebSocket server to over TCP, or a "
             "'unix://<path>' URI to serve over a Unix domain socket at <path> instead "
             "(e.g. 'unix:///tmp/ai-listener'; sufficient on its own to enter WebSocket "
             "server mode, without --websocket-port). Any other value is rejected.",
    )
    parser.add_argument(
        "--websocket-client-id",
        type=int,
        default=None,
        help="Unix socket mode only: required effective UID (checked via getpeereid()) of "
             "the connecting client process. Connections from any other UID are rejected. "
             "Ignored when --websocket-host is a TCP host/IP.",
    )
    parser.add_argument(
        "--ssl-certfile",
        type=str,
        default=None,
        help="Path to a PEM certificate file; with --ssl-keyfile, serves --websocket-port over wss:// instead of ws://.",
    )
    parser.add_argument(
        "--ssl-keyfile",
        type=str,
        default=None,
        help="Path to a PEM private key file; with --ssl-certfile, serves --websocket-port over wss:// instead of ws://.",
    )
    parser.add_argument(
        "--search-engine",
        type=str,
        choices=["brave", "tavily"],
        default=None,
        help=(
            "Web-search provider for plugin_authoring's search_web tool (Brave/Tavily). Falls "
            "back to runtime config's 'search_engine' key when omitted. Requires "
            "SEARCH_ENGINE_API_KEY to also be set. When the resolved LLM provider for this tool "
            "set is Claude, omitting this (and its key) uses Claude's own native web_search tool "
            "instead -- no second API key needed; passing --search-engine explicitly always forces "
            "this Brave/Tavily fallback regardless of provider. For any other provider, omitting "
            "this means no web search tool at all, and the flow falls through to asking the user "
            "for URLs."
        ),
    )
    parser.add_argument(
        "--backend-api-classpath",
        type=str,
        default=None,
        help="Backend API class path (e.g., 'iox.IoXWrapper')",
    )
    parser.add_argument(
        "--backend-api-base-url",
        type=str,
        default=None,
        help="Backend API base URL",
    )
    parser.add_argument(
        "--backend-api-username",
        type=str,
        default=None,
        help="Backend API username",
    )
    parser.add_argument(
        "--backend-api-password",
        type=str,
        default=None,
        help="Backend API password",
    )
    parser.add_argument(
        "--json-output",
        dest="json_output",
        type=bool,
        default=True,
        required=False,
        help="Enable JSON output for backend API",
    )
    parser.add_argument(
        "--prompt_type",
        dest="prompt_type",
        required=False,
        type=str,
        default="shared-features",
        help="The type of prompt to use (e.g., 'per-device', 'shared-features', etc.)",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default=None,
        help="Logger level override (DEBUG, INFO, WARNING, ERROR, CRITICAL)",
    )
    parser.add_argument(
        "--log-file",
        type=str,
        default=None,
        help="Optional log file path; enables rotating file logs",
    )
    parser.add_argument(
        "--log-json",
        action="store_true",
        help="Enable JSON log output format",
    )
    parser.add_argument(
        "--no-log-console",
        action="store_true",
        help="Disable console log output",
    )
    parser.add_argument(
        "--stream",
        dest="stream",
        action="store_true",
        default=None,
        help=(
            "Force-enable LLM token streaming for every nucore_runtime profile, "
            "overriding each profile's own 'stream' setting in runtime config."
        ),
    )
    parser.add_argument(
        "--no-stream",
        dest="stream",
        action="store_false",
        help="Force-disable LLM token streaming for every profile, overriding runtime config.",
    )
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=None,
        help="Override the agentic loop's max tool-call iterations per query (defaults to runtime config's 'max_iterations', or 8).",
    )
    parser.add_argument(
        "--preferences-dir",
        type=str,
        default=None,
        help=(
            "Directory to store this installation's customer preferences (aliases/events) in -- "
            "overrides runtime config's 'preferences_dir'. There is no default: preferences are "
            "unavailable for an installation that hasn't set either."
        ),
    )
    parser.add_argument(
        "--prompt-log-dir",
        type=str,
        default=None,
        help=(
            "Directory to write the debug prompt/tool-call log into -- overrides runtime config's "
            "'prompt_log_dir'. Defaults to '<cwd>/logs'."
        ),
    )
    parser.add_argument(
        "--no-prompt-log",
        action="store_true",
        help="Disable the debug prompt/tool-call log entirely. On by default.",
    )
    return parser


def _load_secrets_file(path: str | Path) -> dict[str, str]:
    """Load a secrets file into a flat ``dict[str, str]``.

    The file must be JSON with a top-level object containing key/value pairs,
    for example:

    {
      "OPENAI_API_KEY": "...",
      "ANTHROPIC_API_KEY": "..."
    }
    """
    file_path = Path(path).expanduser().resolve()
    if not file_path.exists() or not file_path.is_file():
        raise FileNotFoundError(f"Secrets file not found: {file_path}")

    with file_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)

    if not isinstance(payload, dict):
        raise ValueError("Secrets file must contain a top-level JSON object")

    return {str(key): str(value) for key, value in payload.items() if value is not None}


def _load_backend_api(
    classpath: str | None,
    base_url: str | None,
    username: str | None,
    password: str | None,
    json_output: bool = False,
    poly: Any = None,
) -> Any:
    """Dynamically load and instantiate a backend API class.

    Returns None if classpath is missing, or if neither ``poly`` nor all of
    base_url/username/password are provided.

    Args:
        classpath: Fully qualified class path (e.g., 'iox.IoXWrapper')
        base_url: Backend API base URL
        username: Backend API username
        password: Backend API password
        json_output: Whether to enable JSON output for backend API
        poly: Polyglot interface instance -- alternative to base_url/username/
              password, passed through as-is to the backend API class.

    Returns:
        Instantiated backend API object or None if parameters incomplete.
    """
    if not classpath or not (poly or all([base_url, username, password])):
        return None

    return _load_backend_api_cached(
        classpath=classpath,
        base_url=base_url,
        username=username,
        password=password,
        json_output=bool(json_output),
        poly=poly,
    )


@functools.lru_cache(maxsize=8)
def _load_backend_api_cached(
    *,
    classpath: str,
    base_url: str,
    username: str,
    password: str,
    json_output: bool,
    poly: Any = None,
) -> Any:
    """LRU-cached backend API instantiation.

    Separated from :func:`_load_backend_api` so that repeated calls with the
    same arguments (common in the interactive loop) return the already-
    constructed object without re-importing the module or hitting the network.

    Args:
        classpath:   Fully qualified ``"module.ClassName"`` string.
        base_url:    Backend service base URL.
        username:    Authentication username.
        password:    Authentication password.
        json_output: Whether the backend should return JSON-formatted data.
        poly:        Polyglot interface instance, passed through as-is.

    Raises:
        ValueError: If ``classpath`` is malformed or the class cannot be
                    imported / instantiated.
    """
    # Parse classpath into (module_name, class_name) pair.
    parts = classpath.rsplit(".", 1)
    if len(parts) != 2:
        raise ValueError(
            f"Invalid backend API classpath format: {classpath}. "
            "Expected 'module.ClassName' or 'package.module.ClassName'"
        )

    module_name, class_name = parts
    try:
        module = __import__(module_name, fromlist=[class_name])
        api_class = getattr(module, class_name)
        return api_class(
            base_url=base_url,
            username=username,
            password=password,
            json_output=json_output,
            prompt_format_type=PromptFormatTypes.PROFILE,
            poly=poly,
        )
    except (ImportError, AttributeError) as e:
        raise ValueError(f"Failed to load backend API from {classpath}: {e}")


async def _run_once(
    runtime: UnifiedRuntime,
    query: str,
    eisy_ui_context: EisyUIContext,
    session_id: str | None = None,
) -> None:
    """Execute a single query through the runtime and deliver the result.

    ``handle_query`` now returns complete, already-synthesized results --
    when a tool call runs, the runtime produces the final human-readable text
    itself (in the same rich context the tool call was made in) rather than
    handing raw tool results back to the caller to translate. So this
    function only ever needs to deliver ``get_text_output()``; there is no
    separate translation step for it to orchestrate.

    Delivery depends on ``runtime.stream_handler``:
      - ``None``: no handler attached, print ``text_output`` to stdout directly.
      - attached, but nothing streamed live this turn (the profile that
        produced the answer had ``stream`` off, or made a tool call on its
        final round): send the complete ``text_output`` as one chunk.
      - attached and chunks already streamed live this turn: the terminating
        round of ``AgenticLoop`` only ever returns plain text with no tool
        call, so any live chunk means the LLM adapter already streamed that
        exact final answer as it generated -- resending the complete text
        here would duplicate it, so just signal end-of-stream instead.

    Args:
        runtime:          The active :class:`~UnifiedRuntime` instance.
        query:            The user query string to process.
        eisy_ui_context:  This connection's own EisyUIContext (never shared
                           across connections -- see its class docstring).
        session_id:       Fallback session identifier, used only if
                           eisy_ui_context has no durable user_id yet (e.g.
                           the client never sent a context message).
    """
    query = eisy_ui_context.process_message(query)
    if not query:
        return
    # Prefer the durable, authenticated user_id over the per-connection
    # fallback -- this is what lets identity (and therefore conversation
    # history) survive a reconnect instead of resetting to a fresh uuid4
    # every time.
    session_id = eisy_ui_context.get_user_id() or session_id or "default"
    results = await runtime.handle_query(
        query,
        framework_context=eisy_ui_context.get_context(),
        session_id=session_id,
        active_tool_set=eisy_ui_context.get_active_tool_set(),
        connection_context=eisy_ui_context,
    )
    if not results:
        return
    if not isinstance(results, list):
        results = [results]
    for result in results:
        if result is None:
            continue
        # Sticky either way a switch happens -- see merged-toolsets.md. The
        # *next* turn on this connection starts wherever this one ended up,
        # whether that came from /developer, /customer, or a mid-round
        # model-driven switch_to_*_mode tool call.
        if isinstance(result, IntentHandlerResult) and isinstance(result.output, dict):
            final_tool_set = result.output.get("active_tool_set")
            if final_tool_set:
                eisy_ui_context.set_active_tool_set(final_tool_set)
        text_output = result.get_text_output() if isinstance(result, IntentHandlerResult) else (str(result) if result else "Unknown results from the model")
        if runtime.stream_handler is not None:
            if runtime.stream_handler.get_stream_chunk_count() > 0:
                # Already streamed live during generation -- just close the stream.
                await runtime.stream_handler.send_chunk("", True)
            else:
                await runtime.stream_handler.send_chunk(text_output, True)
        else:
            print(text_output)

        # History is now recorded inside UnifiedRuntime itself (handle_query),
        # so every caller gets consistent multi-turn memory without having to
        # replicate this bookkeeping.

    return

async def _run_loop(runtime: UnifiedRuntime, eisy_ui_context: "EisyUIContext") -> None:
    """Run an interactive REPL that repeatedly prompts for queries.

    Reads lines from stdin and dispatches each to :func:`_run_once`.  Exits
    cleanly on ``quit`` / ``exit`` (and common variants), ``Ctrl+C``
    (``KeyboardInterrupt``), and ``Ctrl+D`` / pipe-close (``EOFError``).

    The stream handler is reset before every query so per-call state (e.g.
    chunk counters) does not leak between turns.

    Args:
        runtime: The active :class:`~UnifiedRuntime` instance.
        eisy_ui_context: The same instance already passed to this process's
            ``dispatch_factory`` call in ``main`` -- REPL mode has no
            context-message mechanism to ever populate its ``user_id``, but
            it's still the one shared instance, not a second, independent
            one.
    """
    print("Standalone Unified Runtime")
    print("Type 'quit' to exit")
    while True:
        try:
            query = input("\n> ").strip()
        except KeyboardInterrupt:
            # Allow Ctrl+C to terminate the interactive loop immediately.
            logger.info("\nInterrupted. Exiting.")
            break
        except EOFError:
            break

        if not query:
            continue

        # Normalise common shell/debug-console variants of exit commands.
        command = query.casefold().strip().strip("\"'")
        if command in {"quit", "exit", "q", ":q", "quit()", "exit()"} or command.startswith(("quit ", "exit ")):
            break
        # Reset per-call stream handler state before dispatching.
        runtime.reset_stream_handler()
        try:
            await _run_once(runtime, query, eisy_ui_context, session_id="default")
        except asyncio.CancelledError:
            logger.info("\nCancelled. Exiting.")
            break


_UNIX_SOCKET_PREFIX = "unix://"


def _parse_websocket_host(value: str) -> tuple[bool, str]:
    """Parse ``--websocket-host`` into ``(is_unix, host_or_path)``.

    Accepts either a bare IP address (TCP) or a ``unix://<path>`` URI (Unix
    domain socket at ``<path>``). Anything else -- including a bare
    filesystem path with no ``unix://`` prefix -- is rejected, so a typo'd IP
    can't silently fall through to being treated as a socket path.
    """
    if value.startswith(_UNIX_SOCKET_PREFIX):
        path = value[len(_UNIX_SOCKET_PREFIX):]
        if not path:
            raise ValueError(f"--websocket-host {value!r} has no path after '{_UNIX_SOCKET_PREFIX}'")
        return True, path
    try:
        ipaddress.ip_address(value)
    except ValueError:
        raise ValueError(
            f"--websocket-host must be an IP address or a '{_UNIX_SOCKET_PREFIX}<path>' URI, got {value!r}"
        )
    return False, value


_libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)


def _get_unix_peer_uid(websocket) -> int | None:
    """Effective UID of the process on the other end of a Unix domain socket
    connection, via the BSD/POSIX ``getpeereid()`` libc call -- this runtime
    targets FreeBSD (eisy), which has no Linux ``SO_PEERCRED``. Returns None
    if the underlying socket isn't reachable (e.g. not actually a Unix
    socket) or the call fails."""
    sock = websocket.transport.get_extra_info("socket")
    if sock is None:
        return None
    euid = ctypes.c_uint32()
    egid = ctypes.c_uint32()
    if _libc.getpeereid(sock.fileno(), ctypes.byref(euid), ctypes.byref(egid)) != 0:
        return None
    return euid.value


class _RawWebSocketAdapter:
    """Adapts a ``websockets`` connection to the small surface
    :meth:`StreamHandler.send_chunk` expects -- ``.client_state.name`` /
    ``await .send_text(...)``, matching Starlette's ``WebSocket`` (what
    ``eisy_ai/chat.py``'s FastAPI path already passes it). Keeps
    ``stream_handler.py`` provider-agnostic instead of teaching it two APIs.
    """

    class _State:
        def __init__(self, name: str) -> None:
            self.name = name

    def __init__(self, websocket) -> None:
        self._websocket = websocket

    @property
    def client_state(self):
        connected = self._websocket.state is websockets.protocol.State.OPEN
        return self._State("CONNECTED" if connected else "DISCONNECTED")

    async def send_text(self, data: str) -> None:
        await self._websocket.send(data)


_CUSTOMER_TOOLS_DIR = Path(__file__).parent / "tools"

# The plugin-lifecycle tools (plus run_shell_command, Stage 6's hardware/USB
# detection) plugin_authoring reuses as-is from the customer tool set (see
# plugin_authoring/dispatch.py's module docstring for why these five and not
# the others) -- referenced directly rather than copied, so the two tool
# sets never drift on what these schemas say.
_PLUGIN_AUTHORING_REUSED_CUSTOMER_TOOLS = (
    "tool_plugin_list_installed.json",
    "tool_plugin_ops.json",
    "tool_plugin_get_capabilities.json",
    "tool_plugin_call.json",
    "tool_diagnostics_run_shell.json",
)


async def _record_native_search_evidence(
    raw_response: Any, *, ledger: "plugin_authoring.EvidenceLedger", secret_values: list[str]
) -> None:
    """``AgenticLoop``'s ``on_raw_response`` hook for the Claude-native web
    search path: a ``web_search_tool_result`` block never becomes a
    ``ToolCall`` (see ``parse_tool_calls``), so it never reaches
    ``dispatch`` -- this is the only place that ever sees it. Walks every
    such block in *raw_response*'s content, marks ``ledger.web_search_tried``,
    and license-enriches/records each result the same way ``search_web``'s
    fallback path already does (``search_result_enrichment
    .classify_and_record``), so a GitHub-domain result is treated
    identically no matter which path found it."""
    content = raw_response.get("content") if isinstance(raw_response, dict) else None
    for block in content or []:
        if not isinstance(block, dict) or block.get("type") != "web_search_tool_result":
            continue
        ledger.web_search_tried = True
        items = block.get("content")
        if not isinstance(items, list):
            continue  # error shape -- {"type": "web_search_tool_result_error", ...}
        for item in items:
            if isinstance(item, dict) and item.get("type") == "web_search_result":
                await search_result_enrichment.classify_and_record(
                    ledger,
                    tier="web_search",
                    url=item.get("url"),
                    title=item.get("title"),
                    secret_values=secret_values,
                )


def _build_plugin_authoring_tool_set(
    *,
    nucore_interface: NuCoreInterface,
    plugin_output_root: str,
    search_engine: str | None = None,
    search_engine_api_key: str | None = None,
    secret_values: list[str] | None = None,
    provider: str | None = None,
) -> ToolSetBundle:
    """Build the ``plugin_authoring`` tool set's :class:`ToolSetBundle` --
    called from ``main()`` only when ``runtime_config``'s
    ``nucore_runtime.plugin_authoring.enabled`` is true (see
    design/developers/merged-toolsets.md; there is no longer a
    ``--tool-set``/``--plugin-output-root`` CLI flag -- *plugin_output_root*
    comes from that profile's own config instead, and *search_engine*/
    *search_engine_api_key* are now the top-level, tool-set-agnostic config
    keys of the same names).

    ``dispatch_factory`` (the bundle's second field) is called at most once
    per connection, lazily, by :class:`UnifiedRuntime` (see that class's own
    docstring) -- this is what gives each connection its own fresh
    ``EvidenceLedger`` (design/developers/impl_plan.md's discovery tools), so
    one user's evidence can never satisfy another's "credible source" check.

    *search_engine* (``"brave"``/``"tavily"``) together with
    *search_engine_api_key* controls whether the Brave/Tavily ``search_web``
    tool is registered. *provider* is the resolved LLM provider for this
    tool set's conversations (``resolve_llm_profile``'s ``"provider"`` key)
    -- when it resolves to ``claude`` and *search_engine* was not explicitly
    given, Claude's own native ``web_search`` server tool is used instead:
    no ``search_web``/no second API key, via *extra_tools*/*on_raw_response*
    on the returned dispatch bundle rather than a ``tool_*.json`` file
    (there is nothing to dispatch -- Anthropic executes it server-side).
    Passing *search_engine* explicitly always wins, on any provider, forcing
    the Brave/Tavily fallback -- today's exact behavior. *secret_values* are
    the literal secret strings (backend password, loaded secrets-file
    values, the search engine key itself) the discovery tools refuse to
    send in an outbound query/fetch.
    """
    web_search_enabled = bool(search_engine and search_engine_api_key)
    native_claude_search = (
        bool(provider) and ProviderDispatchLLMAdapter._normalize(provider) == "claude" and not search_engine
    )

    tool_spec_paths = sorted(
        p for p in plugin_authoring.TOOLS_DIR.glob("tool_*.json") if p.name != "tool_search_web.json"
    ) + [_CUSTOMER_TOOLS_DIR / name for name in _PLUGIN_AUTHORING_REUSED_CUSTOMER_TOOLS]
    if web_search_enabled:
        tool_spec_paths.append(plugin_authoring.TOOLS_DIR / "tool_search_web.json")
    tool_spec_paths.sort()

    def _make_dispatch(eisy_ui_context: "EisyUIContext | None" = None) -> DispatchBundle:
        # plugin_output_root is consumed by list_generated_plugins/
        # read_generated_plugin/generate_plugin_scaffold/install_generated_plugin
        # (design/developers/impl_plan.md Phase 3, plugin_authoring_p4_impl.md).
        # eisy_ui_context, when given, is this connection's own live
        # EisyUIContext -- its bound get_user_id method (not a snapshot) is
        # threaded into configure_developer/generate_plugin_scaffold so they
        # always see this connection's current authenticated identity, even
        # though it's typically still unknown at the moment this factory runs
        # (no context message has necessarily arrived yet).
        ledger = plugin_authoring.EvidenceLedger(web_search_available=web_search_enabled or native_claude_search)
        tool_handlers = plugin_authoring.dispatch.build_tool_handlers(
            ledger=ledger,
            search_engine=search_engine,
            search_engine_api_key=search_engine_api_key,
            secret_values=secret_values or [],
            plugin_output_root=plugin_output_root,
            get_user_id=(eisy_ui_context or EisyUIContext()).get_user_id,
        )
        dispatch = functools.partial(
            plugin_authoring.dispatch.execute_tool, nucore_interface=nucore_interface, tool_handlers=tool_handlers
        )
        if not native_claude_search:
            return DispatchBundle(dispatch=dispatch)
        extra_tools = [
            {
                "type": "web_search_20250305",
                "name": "web_search",
                "max_uses": plugin_authoring_discovery.MAX_WEB_SEARCH_QUERIES,
            }
        ]
        on_raw_response = functools.partial(
            _record_native_search_evidence, ledger=ledger, secret_values=secret_values or []
        )
        return DispatchBundle(dispatch=dispatch, extra_tools=extra_tools, on_raw_response=on_raw_response)

    return ToolSetBundle(
        tool_spec_paths=tool_spec_paths,
        dispatch_factory=_make_dispatch,
        system_prompt_builder=plugin_authoring.build_system_prompt_sections,
    )


async def _run_websocket_server(
    nucore_interface: NuCoreInterface,
    llm_adapter,
    runtime_config_path: str,
    force_stream: bool | None,
    host: str,
    port: int | None,
    is_unix: bool,
    client_uid: int | None = None,
    ssl_context: ssl.SSLContext | None = None,
    tool_sets: dict[str, ToolSetBundle] | None = None,
) -> None:
    """Serve WebSocket connections directly, no HTTP framework involved.

    ``nucore_interface``/``llm_adapter`` are shared across every connection
    (same CLI-configured backend for the life of the process), and so, now,
    is one ``SessionStore`` -- each connection gets its own
    :class:`UnifiedRuntime` and its own :class:`StreamHandler` (own
    websocket target), matching the isolation ``eisy_ai/chat.py`` already
    gives each browser tab for everything except conversation history, which
    is shared and keyed by session id specifically so a reconnecting
    client's history survives instead of starting over empty (this was
    previously a private, per-connection ``SessionStore`` too, which meant a
    reconnect silently lost everything despite ``EisyUIContext.get_user_id``'s
    session id being stable across one -- see ``SessionStore.lock`` for what
    keeps sharing it across connections safe). Dispatch is also per-connection:
    each entry in *tool_sets* hands back a dispatch *factory*, and
    ``UnifiedRuntime`` calls it fresh for each connection rather than sharing
    one dispatch object -- for ``plugin_authoring`` this is what gives each
    connection its own fresh ``EvidenceLedger`` (design/developers/impl_plan.md's
    discovery tools), so one user's evidence never satisfies another's
    "credible source" check.

    ``runtime_config`` is *not* shared, unlike the other two -- it's rebuilt
    fresh per connection (a cheap local JSON read, no network I/O) because
    ``_load_runtime_config`` bakes a bound ``stream_handler.handle_stream_chunk``
    callback directly into it (see ``runtime_config.py``'s
    ``_coerce_runtime_profile``). Reusing one shared ``runtime_config`` across
    connections would mean every connection's live token stream gets routed to
    whichever ``StreamHandler`` built it first -- one with no websocket
    attached -- so streaming silently no-ops and only the final complete
    answer (sent separately by ``_run_once``) ever reaches the client.

    ``ssl_context``, when given, serves ``wss://`` instead of ``ws://`` --
    required for clients (e.g. the Eisy UI) that always connect over TLS, the
    way ``eisy_ai/chat.py`` did via its ``certs/`` files.

    ``is_unix`` serves over a Unix domain socket at path ``host`` instead of
    TCP (``port`` is unused in that case). ``client_uid``, when given (Unix
    socket mode only), is checked against each connection's real peer UID via
    ``getpeereid()`` -- connections from any other UID are closed immediately,
    before any query is processed.

    *tool_sets* maps each enabled tool-set name (today: ``"unified"``/
    ``"plugin_authoring"``, whichever ``runtime_config.enabled_profiles``
    says are on) to its own :class:`~unified.runtime.ToolSetBundle` -- see
    ``main()``'s ``_build_plugin_authoring_tool_set`` call and
    ``UnifiedRuntime``'s own docstring for what each one needs.
    """
    # Shared across every connection -- see this function's own docstring
    # and SessionStore.lock for why (a reconnect must find its history, not
    # start over empty; the lock is what keeps that sharing safe).
    shared_session_store = SessionStore()

    async def handler(websocket) -> None:
        if client_uid is not None:
            peer_uid = _get_unix_peer_uid(websocket)
            if peer_uid != client_uid:
                logger.warning(
                    f"Rejecting WebSocket connection: peer uid {peer_uid!r} != expected {client_uid}"
                )
                await websocket.close(code=1008, reason="unauthorized")
                return

        # Fallback only -- used until/unless this connection's own
        # EisyUIContext picks up a durable user_id from a context message.
        fallback_session_id = str(uuid.uuid4())
        eisy_ui_context = EisyUIContext()
        stream_handler = StreamHandler()
        stream_handler.set_websocket(_RawWebSocketAdapter(websocket))
        runtime_config = _load_runtime_config(
            path=runtime_config_path,
            stream_handler=stream_handler,
            force_stream=force_stream,
        )
        # tool_sets' own dispatch_factory per entry is still called lazily,
        # at most once per connection -- see ToolSetBundle's docstring; this
        # function and UnifiedRuntime itself have no idea what tool-set
        # specific config (output root, search engine, secrets, provider)
        # produced each bundle. The caller (main()) resolves that once.
        runtime = UnifiedRuntime(
            nucore_interface=nucore_interface,
            llm_client=llm_adapter,
            runtime_config=runtime_config,
            session_store=shared_session_store,
            tool_sets=tool_sets,
        )
        runtime.stream_handler = stream_handler
        try:
            async for message in websocket:
                runtime.reset_stream_handler()
                await _run_once(runtime, message, eisy_ui_context, session_id=fallback_session_id)
        except websockets.ConnectionClosed:
            pass

    if is_unix:
        # Remove a stale socket file left behind by a prior crashed run --
        # asyncio's create_unix_server otherwise fails with "address already
        # in use" even though nothing is actually listening on it.
        if os.path.exists(host):
            os.remove(host)
        logger.info(f"WebSocket server listening on unix socket {host} ({'wss' if ssl_context else 'ws'}://)")
        server_cm = websockets.unix_serve(handler, path=host, ssl=None)# ssl_context)
    else:
        logger.info(f"WebSocket server listening on {host}:{port} ({'wss' if ssl_context else 'ws'}://)")
        server_cm = websockets.serve(handler, host, port, ssl=ssl_context)

    async with server_cm:
        if is_unix:
            # The socket file is created by the bind() above with a default
            # mode governed by umask (often world-readable/writable), and on
            # BSD (FreeBSD/eisy) its group is inherited from the containing
            # directory rather than the process's own primary group. Force
            # both explicitly so the socket ends up owned by, and readable/
            # writable only by, this process's own uid/primary gid.
            os.chown(host, os.getuid(), os.getgid())
            os.chmod(host, 0o660)
        await asyncio.Future()  # run forever, until KeyboardInterrupt/CancelledError


# Module-level reference to the backend API instance; populated in main() so
# that it can be inspected from a debugger or extended tests without re-running
# the full startup sequence.
nucore_interface: NuCoreInterface = None


def main(args:Any=None, poly=None) -> None:
    """CLI entry point: parse arguments, configure logging, and start the runtime.

    Startup sequence:
    1. Parse CLI arguments.
    2. Configure the shared logger (level, file, JSON, console).
    3. Resolve the runtime profile path.
    4. Load the runtime profile and build the LLM dispatch adapter.
    5. Instantiate the backend API (``nucore_interface``).
    6. Construct :class:`~UnifiedRuntime` and either run a single query
       (``--query``) or enter the interactive REPL.
    7. Shut down the runtime on exit regardless of how it terminates.
    """
    if args is None:
        args = _build_parser().parse_args()

    log_config = configure_logging(
        level=args.log_level,
        log_file=args.log_file,
        json_output=True if args.log_json else None,
        console=False if args.no_log_console else None,
        force=True,
    )
    logger.debug("Logging initialized", extra={"log_config": log_config})

    runtime_config_path = Path(args.runtime_config).expanduser().resolve() if args.runtime_config else None
    secrets_env = _load_secrets_file(args.secrets_file) if args.secrets_file else None

    if runtime_config_path is None:
        raise ValueError("--runtime-config is required and must point to a JSON file with top-level 'nucore_runtime'")
    if not runtime_config_path.exists() or not runtime_config_path.is_file():
        raise FileNotFoundError(f"Runtime profile file not found: {runtime_config_path}")

    # One StreamHandler instance covers both roles: per-request LLM token
    # streaming (wired into runtime_config below, gated per-profile by each
    # profile's own 'stream' key unless --stream/--no-stream forces it) and
    # final-response delivery in _run_once -- stdout print in --query/REPL
    # mode, or a per-connection WebSocket target in --websocket-port mode
    # (set separately per connection in _run_websocket_server).
    stream_handler = StreamHandler()

    # This load is used both to build the LLM dispatch adapter's provider
    # clients (below) and as UnifiedRuntime's own config.
    runtime_config = _load_runtime_config(
        path=str(runtime_config_path),
        stream_handler=stream_handler,
        force_stream=args.stream,
    )

    # --prompt-log-dir wins over runtime config's 'prompt_log_dir', same
    # CLI-overrides-config precedence already used for --preferences-dir;
    # unlike that one, this always has a real default ('<cwd>/logs') rather
    # than being left unconfigured. --no-prompt-log/'prompt_log_enabled'
    # is a separate on/off switch -- the directory never doubles as one.
    prompt_log_dir = args.prompt_log_dir if args.prompt_log_dir is not None else runtime_config.get("prompt_log_dir")
    if not prompt_log_dir:
        prompt_log_dir = str(Path.cwd() / "logs")
    prompt_log_enabled = not args.no_prompt_log and runtime_config.get("prompt_log_enabled", True)
    configure_prompt_logging(prompt_log_dir, enabled=prompt_log_enabled)

    # Build the LLM dispatch adapter from the resolved config.
    llm_adapter = build_default_dispatch_adapter(runtime_config, env=secrets_env)

    # launch.json's ${env:...} substitution can't see envFile-provided values (they're
    # injected into this process's own environment only after VS Code has already
    # resolved args) -- so launch.json leaves these blank and we fall back to
    # os.environ here instead, which envFile does correctly populate.
    backend_api_username = args.backend_api_username or os.environ.get("BACKEND_API_USER_NAME")
    backend_api_password = args.backend_api_password or os.environ.get("BACKEND_API_PASSWORD")

    # plugin_authoring: --search-engine wins over runtime config's
    # 'search_engine' key, same CLI-overrides-config precedence as
    # --preferences-dir above. Neither brave nor tavily is treated as the
    # implied default when both are unset. search_engine_api_key is now a
    # top-level config key (see runtime_config.py), resolved via the same
    # "${ENV_VAR}" convention api_key already uses -- never a literal secret
    # in the file itself (design/developers/merged-toolsets.md).
    search_engine = args.search_engine or runtime_config.get("search_engine")
    env_map = secrets_env or dict(os.environ)
    search_engine_api_key = resolve_env_placeholder(runtime_config.get("search_engine_api_key"), env_map)
    # The resolved provider for plugin_authoring's own conversations (not
    # necessarily the dispatch adapter's overall default_provider, which
    # comes from a different runtime_config key -- see resolve_llm_profile's
    # docstring) -- plugin_authoring's native-vs-fallback web search
    # selection needs this before _build_plugin_authoring_tool_set runs.
    provider = resolve_llm_profile(runtime_config, preferred_key="plugin_authoring").get("provider")
    # Literal secret values the discovery tools refuse to send in an
    # outbound query/fetch -- never put these in a search query or a log.
    secret_values = [
        v
        for v in (
            backend_api_password,
            *(secrets_env.values() if secrets_env else ()),
            search_engine_api_key,
        )
        if v
    ]

    global nucore_interface
    nucore_interface = _load_backend_api(
        classpath=args.backend_api_classpath,
        base_url=args.backend_api_base_url,
        username=backend_api_username,
        password=backend_api_password,
        json_output=args.json_output,
        poly=poly,
    )

    if nucore_interface is None:
        raise ValueError("Backend API failed to load. Please check your parameters and try again.")

    # No default -- None means preferences (aliases/events) are simply
    # unavailable for this installation. --preferences-dir wins over runtime
    # config's 'preferences_dir', same CLI-overrides-config precedence
    # already used for --max-iterations.
    nucore_interface.preferences_dir = (
        args.preferences_dir if args.preferences_dir is not None else runtime_config.get("preferences_dir")
    )

    # --max-iterations, when given, forces every enabled profile's own
    # max_iterations uniformly, the same "CLI override wins for every
    # profile" shape --stream/--no-stream already has -- max_iterations
    # itself otherwise comes from each profile individually (see
    # runtime_config.py; it genuinely differs by tool set, which is why it
    # moved off the top level -- design/developers/merged-toolsets.md).
    if args.max_iterations is not None:
        for profile in runtime_config["nucore_runtime"].values():
            profile["max_iterations"] = args.max_iterations

    websocket_is_unix, websocket_host = _parse_websocket_host(args.websocket_host)

    # Resolved once, regardless of mode -- both the websocket server and the
    # direct --query/REPL path below need the same tool_sets mapping, and
    # neither needs to know what went into producing it. There is no longer
    # a --tool-set/--plugin-output-root flag: which tool sets exist and are
    # reachable is entirely a function of runtime_config's own profiles and
    # their 'enabled' flags (see runtime_config.py / merged-toolsets.md).
    # "unified" needs no explicit entry -- UnifiedRuntime falls back to its
    # own customer-facing default when it's absent from this dict.
    tool_sets: dict[str, ToolSetBundle] = {}
    plugin_authoring_profile = runtime_config["nucore_runtime"].get("plugin_authoring")
    if plugin_authoring_profile is not None and plugin_authoring_profile.get("enabled"):
        tool_sets["plugin_authoring"] = _build_plugin_authoring_tool_set(
            nucore_interface=nucore_interface,
            plugin_output_root=plugin_authoring_profile["plugin_output_root"],
            search_engine=search_engine,
            search_engine_api_key=search_engine_api_key,
            secret_values=secret_values,
            provider=provider,
        )

    if args.websocket_port or websocket_is_unix:
        # Native WebSocket server mode: this process itself is the server --
        # no external HTTP framework, no caller-supplied connection object.
        # nucore_interface is shared across every connection for the life of
        # the process, so it's shut down exactly once here -- never per
        # connection (see _run_websocket_server's docstring).
        if bool(args.ssl_certfile) != bool(args.ssl_keyfile):
            raise ValueError("--ssl-certfile and --ssl-keyfile must be given together")
        ssl_context = None
        if args.ssl_certfile and args.ssl_keyfile:
            ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ssl_context.load_cert_chain(args.ssl_certfile, args.ssl_keyfile)

        logger.info("Starting native WebSocket server; responses stream per connection.")
        try:
            asyncio.run(_run_websocket_server(
                nucore_interface, llm_adapter, str(runtime_config_path), args.stream,
                websocket_host, args.websocket_port,
                websocket_is_unix,
                client_uid=args.websocket_client_id if websocket_is_unix else None,
                ssl_context=ssl_context,
                tool_sets=tool_sets,
            ))
        except KeyboardInterrupt:
            logger.warning("\nInterrupted. Exiting.")
        finally:
            nucore_interface.shutdown()
        return

    # Built once and reused in both branches below -- plugin_authoring's
    # tools (configure_developer, generate_plugin_scaffold) need the exact
    # same instance any dispatch factory bound get_user_id to, not a second,
    # independent one; UnifiedRuntime itself now calls each tool set's own
    # dispatch_factory lazily (see ToolSetBundle's docstring), passing this
    # same connection_context through each time.
    eisy_ui_context = EisyUIContext()
    runtime = UnifiedRuntime(
        nucore_interface=nucore_interface,
        llm_client=llm_adapter,
        runtime_config=runtime_config,
        tool_sets=tool_sets,
    )
    runtime.stream_handler = stream_handler
    logger.info("Unified runtime initialized")

    if args.query:
        # Single-query (non-interactive) mode: run once and exit.
        if runtime.stream_state is not None:
            runtime.stream_state["chunks"] = 0
        try:
            asyncio.run(_run_once(runtime, args.query, eisy_ui_context))
        except KeyboardInterrupt:
            logger.warning("\nInterrupted. Exiting.")
        finally:
            runtime.shutdown()
        return

    # Interactive REPL mode.
    try:
        asyncio.run(_run_loop(runtime, eisy_ui_context))
    except KeyboardInterrupt:
        logger.warning("\nInterrupted. Exiting.")
    finally:
        runtime.shutdown()


if __name__ == "__main__":
    main()

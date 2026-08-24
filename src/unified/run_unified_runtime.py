from __future__ import annotations

import argparse,os
import asyncio
import functools, json
import ssl
import uuid
from pathlib import Path
from typing import Any

import websockets
from dotenv import load_dotenv

from unified.models import IntentHandlerResult
from unified.runtime import UnifiedRuntime
from unified.runtime_config import _load_runtime_config
from unified.dispatch_builder import build_default_dispatch_adapter
from unified.stream_handler import StreamHandler
from nucore import NuCoreInterface, PromptFormatTypes
from utils import configure_logging, get_logger


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
                # Keep the last known user_id if a later context payload
                # omits it, rather than clearing it -- safer degradation.
                self.user_id = (self.context or {}).get("user_id") or self.user_id
                self.message = None
                return None
            if type == "message":
                self.message = message.get("message", None)
                return self.message.strip() if self.message else None
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
        from the context payload -- used as this conversation's session_id
        instead of a fresh uuid4 per connection, so identity (and therefore
        e.g. Plan's session ownership) survives a reconnect. None if no
        context carrying one has been seen yet on this connection."""
        return self.user_id


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
             "as a query, and responses stream back over the same connection.",
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
        "--diagnostic-step",
        type=str,
        default=None,
        help=(
            "Bypass the LLM/agentic loop entirely and call this diagnostic tool "
            "directly against the backend (e.g. 'get_full_system_config', "
            "'get_dev_links_table', 'quick_plm_sanity_check'). "
            "Prints the raw result and exits. Pairs with --diagnostic-params. "
            "For manual testing against a live hub only."
        ),
    )
    parser.add_argument(
        "--diagnostic-params",
        type=str,
        default=None,
        help="JSON object of keyword params for --diagnostic-step, e.g. '{\"device_id\": \"12 34 56 1\"}'.",
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
    # fallback -- this is what lets identity (and Plan's session ownership)
    # survive a reconnect instead of resetting to a fresh uuid4 every time.
    session_id = eisy_ui_context.get_user_id() or session_id or "default"
    results = await runtime.handle_query(query, framework_context=eisy_ui_context.get_context(), session_id=session_id)
    if not results:
        return
    if not isinstance(results, list):
        results = [results]
    for result in results:
        if result is None:
            continue
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

async def _run_diagnostic_step_direct(
    nucore_interface: NuCoreInterface, step: str, params_json: str | None
) -> None:
    """Call a single diagnostic step directly against the backend, bypassing
    the LLM/AgenticLoop/dispatch layer entirely -- for manual testing against
    a live hub without spending on LLM calls or needing conversational
    back-and-forth to reach a specific step.

    *step* is passed straight to ``run_diagnostic_step`` (e.g.
    'get_full_system_config', 'get_dev_links_table') -- there's no session to
    open first.
    """
    params = json.loads(params_json) if params_json else {}
    result = await nucore_interface.run_diagnostic_step(step, **params)
    print(json.dumps(result, indent=2, default=str))


async def _run_loop(runtime: UnifiedRuntime) -> None:
    """Run an interactive REPL that repeatedly prompts for queries.

    Reads lines from stdin and dispatches each to :func:`_run_once`.  Exits
    cleanly on ``quit`` / ``exit`` (and common variants), ``Ctrl+C``
    (``KeyboardInterrupt``), and ``Ctrl+D`` / pipe-close (``EOFError``).

    The stream handler is reset before every query so per-call state (e.g.
    chunk counters) does not leak between turns.

    Args:
        runtime: The active :class:`~UnifiedRuntime` instance.
    """
    print("Standalone Unified Runtime")
    print("Type 'quit' to exit")
    eisy_ui_context = EisyUIContext()
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


async def _run_websocket_server(
    nucore_interface: NuCoreInterface,
    llm_adapter,
    runtime_config_path: str,
    force_stream: bool | None,
    max_iterations: int,
    port: int,
    ssl_context: ssl.SSLContext | None = None,
) -> None:
    """Serve WebSocket connections directly, no HTTP framework involved.

    ``nucore_interface``/``llm_adapter`` are shared across every connection
    (same CLI-configured backend for the life of the process); each
    connection gets its own :class:`UnifiedRuntime` (own session/history) and
    its own :class:`StreamHandler` (own websocket target), matching the
    isolation ``eisy_ai/chat.py`` already gives each browser tab -- just
    without needing a caller-supplied connection object or an HTTP server in
    front of it.

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
    """
    async def handler(websocket) -> None:
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
        runtime = UnifiedRuntime(
            nucore_interface=nucore_interface,
            llm_client=llm_adapter,
            runtime_config=runtime_config,
            max_iterations=max_iterations,
        )
        runtime.stream_handler = stream_handler
        try:
            async for message in websocket:
                runtime.reset_stream_handler()
                await _run_once(runtime, message, eisy_ui_context, session_id=fallback_session_id)
        except websockets.ConnectionClosed:
            pass

    logger.info(f"WebSocket server listening on port {port} ({'wss' if ssl_context else 'ws'}://)")
    async with websockets.serve(handler, "0.0.0.0", port, ssl=ssl_context):
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

    # Build the LLM dispatch adapter from the resolved config.
    llm_adapter = build_default_dispatch_adapter(runtime_config, env=secrets_env)

    # launch.json's ${env:...} substitution can't see envFile-provided values (they're
    # injected into this process's own environment only after VS Code has already
    # resolved args) -- so launch.json leaves these blank and we fall back to
    # os.environ here instead, which envFile does correctly populate.
    backend_api_username = args.backend_api_username or os.environ.get("BACKEND_API_USER_NAME")
    backend_api_password = args.backend_api_password or os.environ.get("BACKEND_API_PASSWORD")

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

    resolved_max_iterations = (
        args.max_iterations if args.max_iterations is not None else int(runtime_config.get("max_iterations", 8))
    )

    if args.diagnostic_step:
        # Direct-to-backend testing mode: no LLM, no AgenticLoop, no
        # UnifiedRuntime -- just the real hub connection built above.
        asyncio.run(nucore_interface._refresh_device_structure())
        asyncio.run(_run_diagnostic_step_direct(nucore_interface, args.diagnostic_step, args.diagnostic_params))
        return

    if args.websocket_port:
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
                resolved_max_iterations, args.websocket_port,
                ssl_context=ssl_context,
            ))
        except KeyboardInterrupt:
            logger.warning("\nInterrupted. Exiting.")
        finally:
            nucore_interface.shutdown()
        return

    runtime = UnifiedRuntime(
        nucore_interface=nucore_interface,
        llm_client=llm_adapter,
        runtime_config=runtime_config,
        max_iterations=resolved_max_iterations,
    )
    runtime.stream_handler = stream_handler
    logger.info("Unified runtime initialized")

    if args.query:
        # Single-query (non-interactive) mode: run once and exit.
        if runtime.stream_state is not None:
            runtime.stream_state["chunks"] = 0
        try:
            asyncio.run(_run_once(runtime, args.query, EisyUIContext()))
        except KeyboardInterrupt:
            logger.warning("\nInterrupted. Exiting.")
        finally:
            runtime.shutdown()
        return

    # Interactive REPL mode.
    try:
        asyncio.run(_run_loop(runtime))
    except KeyboardInterrupt:
        logger.warning("\nInterrupted. Exiting.")
    finally:
        runtime.shutdown()


if __name__ == "__main__":
    main()

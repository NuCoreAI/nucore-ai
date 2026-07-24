from __future__ import annotations

import argparse,os
import asyncio
import functools, json
from pathlib import Path
from typing import Any

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
    """Class to represent context messages from the Eisy UI."""
    def __init__(self):
        self.context:dict = None
        self.message:str = None

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
        "--diagnostic-step",
        type=str,
        default=None,
        help=(
            "Bypass the LLM/agentic loop entirely and call this diagnostic step "
            "directly against the backend (e.g. 'start' to open a session, "
            "'check_device_links', 'get_full_system_config', 'conclude', 'stop'). "
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
) -> Any:
    """Dynamically load and instantiate a backend API class.

    Returns None if any required parameter is None.

    Args:
        classpath: Fully qualified class path (e.g., 'iox.IoXWrapper')
        base_url: Backend API base URL
        username: Backend API username
        password: Backend API password
        json_output: Whether to enable JSON output for backend API

    Returns:
        Instantiated backend API object or None if parameters incomplete.
    """
    if not all([classpath, base_url, username, password]):
        return None

    return _load_backend_api_cached(
        classpath=classpath,
        base_url=base_url,
        username=username,
        password=password,
        json_output=bool(json_output),
    )


@functools.lru_cache(maxsize=8)
def _load_backend_api_cached(
    *,
    classpath: str,
    base_url: str,
    username: str,
    password: str,
    json_output: bool,
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
            prompt_format_type=PromptFormatTypes.PROFILE
        )
    except (ImportError, AttributeError) as e:
        raise ValueError(f"Failed to load backend API from {classpath}: {e}")


eisy_ui_context = EisyUIContext()
async def _run_once(
    runtime: UnifiedRuntime,
    query: str,
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
        runtime:    The active :class:`~UnifiedRuntime` instance.
        query:      The user query string to process.
        session_id: Optional session identifier for conversation tracking.
    """
    global eisy_ui_context
    query = eisy_ui_context.process_message(query)
    if not query:
        return
    session_id = session_id or "default"
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

    ``step == "start"`` opens a session (``start_diagnostics``); any other
    value is passed straight to ``run_diagnostic_step`` (e.g. 'conclude',
    'stop', or a real step name from the catalog).
    """
    params = json.loads(params_json) if params_json else {}
    if step == "start":
        result = await nucore_interface.start_diagnostics()
    else:
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
            await _run_once(runtime, query, session_id="default")
        except asyncio.CancelledError:
            logger.info("\nCancelled. Exiting.")
            break


# Module-level reference to the backend API instance; populated in main() so
# that it can be inspected from a debugger or extended tests without re-running
# the full startup sequence.
nucore_interface: NuCoreInterface = None


def main(args:Any=None, websocket=None) -> None:
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
    # final-response delivery in _run_once (websocket when one is attached,
    # stdout print otherwise -- send_chunk handles both).
    stream_handler = StreamHandler()
    stream_handler.set_websocket(websocket)

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
    )

    if nucore_interface is None:
        raise ValueError("Backend API failed to load. Please check your parameters and try again.")

    if args.diagnostic_step:
        # Direct-to-backend testing mode: no LLM, no AgenticLoop, no
        # UnifiedRuntime -- just the real hub connection built above.
        asyncio.run(nucore_interface._refresh_device_structure())
        asyncio.run(_run_diagnostic_step_direct(nucore_interface, "start", None))
        asyncio.run(_run_diagnostic_step_direct(nucore_interface, args.diagnostic_step, args.diagnostic_params))
        return

    resolved_max_iterations = (
        args.max_iterations if args.max_iterations is not None else int(runtime_config.get("max_iterations", 8))
    )

    runtime = UnifiedRuntime(
        nucore_interface=nucore_interface,
        llm_client=llm_adapter,
        runtime_config=runtime_config,
        max_iterations=resolved_max_iterations,
    )
    runtime.stream_handler = stream_handler
    logger.info("Unified runtime initialized")

    if websocket:
        logger.info("WebSocket connection detected; responses will be sent to the client.")
        return runtime

    if args.query:
        # Single-query (non-interactive) mode: run once and exit.
        if runtime.stream_state is not None:
            runtime.stream_state["chunks"] = 0
        try:
            asyncio.run(_run_once(runtime, args.query))
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

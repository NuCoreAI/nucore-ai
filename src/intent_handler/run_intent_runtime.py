from __future__ import annotations

import argparse
import asyncio
import functools
from pathlib import Path
from typing import Any

from intent_handler import IntentHandlerResult, IntentRuntime, StreamHandler, build_default_dispatch_adapter, _load_runtime_config
from nucore import NuCoreInterface, PromptFormatTypes
from utils import configure_logging, get_logger


logger = get_logger(__name__)


def _default_intent_dir() -> Path:
    """Resolve the default intent handler directory for both repo and installed runs."""
    repo_path = Path(__file__).resolve().parents[1] / "intent_handler_directory"
    if repo_path.exists():
        return repo_path

    try:
        import intent_handler_directory

        package_path = Path(intent_handler_directory.__file__).resolve().parent
        if package_path.exists():
            return package_path
    except Exception:
        pass

    return repo_path


def _default_runtime_config_path() -> Path:
    """Resolve runtime_config.json for both repo and installed runs."""
    repo_path = Path(__file__).resolve().parent / "runtime_assets" / "runtime_config.json"
    if repo_path.exists():
        return repo_path

    try:
        from intent_handler import runtime_assets

        package_path = Path(runtime_assets.__file__).resolve().parent / "runtime_config.json"
        if package_path.exists():
            return package_path
    except Exception:
        pass

    return repo_path


def _build_parser() -> argparse.ArgumentParser:
    """Build and return the CLI argument parser for the intent runtime."""
    parser = argparse.ArgumentParser(description="Run standalone intent runtime")
    parser.add_argument(
        "--intent-dir",
        type=str,
        default=None,
        help="Path to intent handler directory",
    )
    parser.add_argument(
        "--runtime-config",
        type=str,
        default=None,
        help="Optional explicit path to runtime_config.json",
    )
    parser.add_argument(
        "--query",
        type=str,
        default=None,
        help="Single query mode (non-interactive)",
    )
    parser.add_argument(
        "--provider",
        type=str,
        default=None,
        help="Override default LLM provider (e.g., claude, openai, gemini)",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="Override model for the selected provider",
    )
    parser.add_argument(
        "--api-key",
        type=str,
        default=None,
        help="Override API key for the selected provider",
    )
    parser.add_argument(
        "--model-url",
        "--model_url",
        dest="model_url",
        type=str,
        default=None,
        help="Override model base URL or full chat/completions endpoint for OpenAI-compatible providers",
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
        "--stream",
        action="store_true",
        help="Stream model text tokens to stdout when provider supports it",
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
    return parser


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

async def _run_once(
    runtime: IntentRuntime,
    query: str,
    session_id: str | None = None,
) -> None:
    """Execute a single query through the runtime and print the result.

    If the handler returns tool results (agentic loop), the results are
    stringified and sent back to the runtime via
    :meth:`~IntentRuntime.handle_agent_response` so the LLM can process
    them before producing a final text response.

    When a stream handler was active during the call, the response tokens
    were already printed live; this function just emits a trailing newline
    and returns without reprinting.

    Args:
        runtime:    The active :class:`~IntentRuntime` instance.
        query:      The user query string to process.
        session_id: Optional session identifier for conversation tracking.
    """
    result = await runtime.handle_query(query, session_id=session_id)
    tool_results = result.get_tool_results() if isinstance(result, IntentHandlerResult) else None
    if tool_results:
        # Agentic loop: feed tool results back so the LLM can respond to them.
        stringified_tool_results = "\n".join([f"\n{query}\n# AGENT RESPONSE:\n{str(tr)}" for tr in tool_results])
        result = await runtime.handle_agent_response(stringified_tool_results, session_id=None)
        return

    text_output = result.get_text_output() if isinstance(result, IntentHandlerResult) else (str(result) if result else None)
    if text_output is not None and result.get_stream_handler() is not None:
        streamed_chunks = result.get_stream_handler().get_stream_chunk_count()
        if streamed_chunks > 0:
            # Response was already printed live by the stream handler; just add newline.
            print()
            return
        logger.info(f"\n{text_output}\n")

    return

async def _run_loop(runtime: IntentRuntime) -> None:
    """Run an interactive REPL that repeatedly prompts for queries.

    Reads lines from stdin and dispatches each to :func:`_run_once`.  Exits
    cleanly on ``quit`` / ``exit`` (and common variants), ``Ctrl+C``
    (``KeyboardInterrupt``), and ``Ctrl+D`` / pipe-close (``EOFError``).

    The stream handler is reset before every query so per-call state (e.g.
    chunk counters) does not leak between turns.

    Args:
        runtime: The active :class:`~IntentRuntime` instance.
    """
    print("Standalone Intent Runtime")
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


def main() -> None:
    """CLI entry point: parse arguments, configure logging, and start the runtime.

    Startup sequence:
    1. Parse CLI arguments.
    2. Configure the shared logger (level, file, JSON, console).
    3. Resolve paths for the intent handler directory and runtime config.
    4. Load the runtime config and build the LLM dispatch adapter.
    5. Instantiate the backend API (``nucore_interface``).
    6. Construct :class:`~IntentRuntime` and either run a single query
       (``--query``) or enter the interactive REPL.
    7. Shut down the runtime on exit regardless of how it terminates.
    """
    args = _build_parser().parse_args()

    log_config = configure_logging(
        level=args.log_level,
        log_file=args.log_file,
        json_output=True if args.log_json else None,
        console=False if args.no_log_console else None,
        force=True,
    )
    logger.debug("Logging initialized", extra={"log_config": log_config})

    # Resolve paths — prefer explicit CLI args, fall back to auto-detected defaults.
    intent_dir = Path(args.intent_dir).expanduser().resolve() if args.intent_dir else _default_intent_dir()
    runtime_config_path = (
        Path(args.runtime_config).expanduser().resolve()
        if args.runtime_config
        else _default_runtime_config_path()
    )

    if not intent_dir.exists() or not intent_dir.is_dir():
        raise FileNotFoundError(f"Intent handler directory not found: {intent_dir}")
    if not runtime_config_path.exists() or not runtime_config_path.is_file():
        raise FileNotFoundError(f"Runtime config file not found: {runtime_config_path}")

    runtime_config = _load_runtime_config(
        path=str(runtime_config_path),
        stream_handler=None,  # Stream handler will be set later after defining the callback
        provider=args.provider,
        api_key=args.api_key,
        model=args.model,
        model_url=args.model_url,
    )

    # Build the LLM dispatch adapter from the resolved config.
    llm_adapter = build_default_dispatch_adapter(runtime_config)

    global nucore_interface
    nucore_interface = _load_backend_api(
        classpath=args.backend_api_classpath,
        base_url=args.backend_api_base_url,
        username=args.backend_api_username,
        password=args.backend_api_password,
        json_output=args.json_output,
    )

    if nucore_interface is None:
        raise ValueError("Backend API failed to load. Please check your parameters and try again.")

    runtime = IntentRuntime(
        intent_handler_directory=intent_dir,
        llm_client=llm_adapter,
        nucore_interface=nucore_interface,
        runtime_config_path=runtime_config_path,
        stream_handler=StreamHandler(),  # Default stream handler instance; can be customized as needed
        runtime_provider=args.provider,
        runtime_api_key=args.api_key,
        runtime_model=args.model,
        runtime_model_url=args.model_url,
    )
    logger.info("Intent runtime initialized", extra={"intent_dir": str(intent_dir)})

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

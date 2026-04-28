from __future__ import annotations

import argparse
import asyncio
import functools
from pathlib import Path
from typing import Any

from intent_handler import IntentHandlerResult, IntentRuntime, StreamHandler, build_default_dispatch_adapter, _load_runtime_config
from nucore import NuCoreInterface, PromptFormatTypes


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run standalone intent runtime")
    parser.add_argument(
        "--intent-dir",
        type=str,
        default="src/intent_handler_directory",
        help="Path to intent handler directory",
    )
    parser.add_argument(
        "--runtime-config",
        type=str,
        default="src/intent_handler/runtime_assets/runtime_config.json",
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

    # Parse classpath: "package.module.ClassName"
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
    session_id: str | None = None) -> None:
    result = await runtime.handle_query(query, session_id=session_id)
    tool_results = result.get_tool_results() if isinstance(result, IntentHandlerResult) else None
    if tool_results:
        stringified_tool_results = "\n".join([f"# AGENT RESPONSE:\n{str(tr)}" for tr in tool_results])
        result = await runtime.handle_query(stringified_tool_results, session_id=None)
        return

    text_output = result.get_text_output() if isinstance(result, IntentHandlerResult) else (str(result) if result else None)
    if text_output is not None and result.get_stream_handler() is not None:
        streamed_chunks = result.get_stream_handler().get_stream_chunk_count()
        if streamed_chunks > 0:
            # Response was already printed live by the stream handler.
            print()
            return
        print (f"\n{text_output}\n")

    return

async def _run_loop(runtime: IntentRuntime) -> None:
    print("Standalone Intent Runtime")
    print("Type 'quit' to exit")
    while True:
        try:
            query = input("\n> ").strip()
        except KeyboardInterrupt:
            # Allow Ctrl+C to terminate the interactive loop immediately.
            print("\nInterrupted. Exiting.")
            break
        except EOFError:
            break

        if not query:
            continue

        # Normalize common shell/debug-console variants of exit commands.
        command = query.casefold().strip().strip("\"'")
        if command in {"quit", "exit", "q", ":q", "quit()", "exit()"} or command.startswith(("quit ", "exit ")):
            break
        runtime.reset_stream_handler()  # Reset stream handler state before each query
        try:
            await _run_once(runtime, query, session_id="default")
        except asyncio.CancelledError:
            print("\nCancelled. Exiting.")
            break
    

nucore_interface : NuCoreInterface = None  # Global variable to hold the backend API instance

def main() -> None:
    args = _build_parser().parse_args()

    intent_dir = Path(args.intent_dir).expanduser().resolve()
    runtime_config_path = (
        Path(args.runtime_config).expanduser().resolve()
        if args.runtime_config
        else Path(__file__).resolve().parent / "runtime_assets" / "runtime_config.json"
    )

    runtime_config = _load_runtime_config(
        path=args.runtime_config,
        stream_handler=None,  # Stream handler will be set later after defining the callback
        provider=args.provider,
        api_key=args.api_key,
        model=args.model,
        model_url=args.model_url,
    )

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

    if args.query:
        if runtime.stream_state is not None:
            runtime.stream_state["chunks"] = 0
        try:
            asyncio.run(_run_once(runtime, args.query))
        except KeyboardInterrupt:
            print("\nInterrupted. Exiting.")
        finally:
            runtime.shutdown()
        return

    try:
        asyncio.run(_run_loop(runtime))
    except KeyboardInterrupt:
        print("\nInterrupted. Exiting.")
    finally:
        runtime.shutdown()


if __name__ == "__main__":
    main()

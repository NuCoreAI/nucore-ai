from __future__ import annotations

import argparse
import asyncio
import functools
import json
from pathlib import Path
from typing import Any

from intent_handler import IntentRuntime, build_default_dispatch_adapter


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


def _load_runtime_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Runtime config not found: {path}")
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _apply_runtime_overrides(
    runtime_config: dict[str, Any],
    provider: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """Apply CLI overrides to runtime config."""
    config = dict(runtime_config)
    supported_llms = dict(config.get("supported_llms", {}))

    selected_key = provider or config.get("default_llm") or next(iter(supported_llms), None)
    if not selected_key:
        raise ValueError("No provider specified and no default_llm in runtime_config")

    if selected_key not in supported_llms:
        raise ValueError(f"Provider '{selected_key}' not found in runtime_config.supported_llms")

    llm_cfg = dict(supported_llms.get(selected_key, {}))
    if model:
        llm_cfg["model"] = model
    if api_key:
        llm_cfg["api_key"] = api_key

    supported_llms[selected_key] = llm_cfg
    config["supported_llms"] = supported_llms
    if provider:
        config["default_llm"] = selected_key

    return config


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
        )
    except (ImportError, AttributeError) as e:
        raise ValueError(f"Failed to load backend API from {classpath}: {e}")


def _extract_text_output(output: Any) -> str | None:
    if isinstance(output, str):
        return output

    if isinstance(output, dict):
        text = output.get("text")
        if isinstance(text, str) and text.strip():
            return text

        content = output.get("content")
        if isinstance(content, str) and content.strip():
            return content

    return None


async def _run_once(
    runtime: IntentRuntime,
    query: str,
    *,
    stream_state: dict[str, int] | None = None,
) -> None:
    result = await runtime.handle_query(query)
    print(f"\nIntent: {result.intent}")
    print("Output:")

    streamed_chunks = (stream_state or {}).get("chunks", 0)
    text_output = _extract_text_output(result.output)

    if streamed_chunks > 0:
        # Token chunks are already printed by the stream callback.
        print()
        return

    if text_output is not None:
        print(text_output)
        return

    print(result.output)


async def _run_loop(runtime: IntentRuntime, *, stream_state: dict[str, int] | None = None) -> None:
    print("Standalone Intent Runtime")
    print("Type 'quit' to exit")
    while True:
        try:
            query = input("\n> ").strip()
        except EOFError:
            break

        if not query:
            continue
        if query.lower() in {"quit", "exit"}:
            break
        if stream_state is not None:
            stream_state["chunks"] = 0
        await _run_once(runtime, query, stream_state=stream_state)


def main() -> None:
    args = _build_parser().parse_args()

    intent_dir = Path(args.intent_dir).expanduser().resolve()
    runtime_config_path = (
        Path(args.runtime_config).expanduser().resolve()
        if args.runtime_config
        else Path(__file__).resolve().parent / "runtime_assets" / "runtime_config.json"
    )

    runtime_config = _load_runtime_config(runtime_config_path)

    stream_state: dict[str, int] | None = None
    if args.stream:
        stream_state = {"chunks": 0}

        def _stream_chunk_to_stdout(chunk: str) -> None:
            if not chunk:
                return
            stream_state["chunks"] += 1
            print(chunk, end="", flush=True)

        for _, llm_cfg in runtime_config.get("supported_llms", {}).items():
            if not isinstance(llm_cfg, dict):
                continue
            llm_cfg["stream"] = True
            llm_cfg["stream_handler"] = _stream_chunk_to_stdout

    runtime_config = _apply_runtime_overrides(
        runtime_config,
        provider=args.provider,
        api_key=args.api_key,
        model=args.model,
    )
    llm_adapter = build_default_dispatch_adapter(runtime_config)

    backend_api = _load_backend_api(
        classpath=args.backend_api_classpath,
        base_url=args.backend_api_base_url,
        username=args.backend_api_username,
        password=args.backend_api_password,
        json_output=args.json_output,
    )

    runtime = IntentRuntime(
        intent_handler_directory=intent_dir,
        llm_client=llm_adapter,
        backend_api=backend_api,
        runtime_config_path=runtime_config_path,
    )

    if args.query:
        if stream_state is not None:
            stream_state["chunks"] = 0
        asyncio.run(_run_once(runtime, args.query, stream_state=stream_state))
        return

    asyncio.run(_run_loop(runtime, stream_state=stream_state))


if __name__ == "__main__":
    main()

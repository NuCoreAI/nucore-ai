from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

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
        default=None,
        help="Optional explicit path to runtime_config.json",
    )
    parser.add_argument(
        "--query",
        type=str,
        default=None,
        help="Single query mode (non-interactive)",
    )
    return parser


def _load_runtime_config(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"Runtime config not found: {path}")
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


async def _run_once(runtime: IntentRuntime, query: str) -> None:
    result = await runtime.handle_query(query)
    print(f"\nIntent: {result.intent}")
    print("Output:")
    print(result.output)


async def _run_loop(runtime: IntentRuntime) -> None:
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
        await _run_once(runtime, query)


def main() -> None:
    args = _build_parser().parse_args()

    intent_dir = Path(args.intent_dir).expanduser().resolve()
    runtime_config_path = (
        Path(args.runtime_config).expanduser().resolve()
        if args.runtime_config
        else Path(__file__).resolve().parent / "runtime_assets" / "runtime_config.json"
    )

    runtime_config = _load_runtime_config(runtime_config_path)
    llm_adapter = build_default_dispatch_adapter(runtime_config)

    runtime = IntentRuntime(
        intent_handler_directory=intent_dir,
        llm_client=llm_adapter,
        backend_api=None,
        runtime_config_path=runtime_config_path,
    )

    if args.query:
        asyncio.run(_run_once(runtime, args.query))
        return

    asyncio.run(_run_loop(runtime))


if __name__ == "__main__":
    main()

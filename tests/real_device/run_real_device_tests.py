#!/usr/bin/env python3
"""Real-device fuzz-testing harness for the intent handler system.

Pulls the LIVE device/group/folder/routine inventory off a real IoX/ISY hub,
asks an LLM to generate adversarial corner-case queries grounded in that real
inventory (ambiguous references, vague quantities, nonexistent devices,
conflicting routine logic, etc.), runs each one through the real
IntentRuntime, and flags anything that looks broken -- exceptions, tool
failures, silent no-ops, or a non-tool-call response that doesn't read as a
clarifying question. There's no hand-written expected answer for a fuzzily
generated query, so nothing is asserted as "correct"; flagged cases are
written to results/latest-failures.md for a human/Claude to judge, and are
also appended to seeds/regression.yaml so they're replayed (not just
discovered once by chance) on every future run.

Connection args mirror run_intent_runtime.py's CLI (and the
"Claude-IntentHandler-LakeEncino" launch.json config), so they can be pasted
straight in. See README.md for the full design and safety notes.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent))

# Load secrets/.env directly rather than relying on VS Code's debug-adapter
# "envFile" mechanism -- see the matching comment in run_intent_runtime.py
# for why that mechanism doesn't reliably deliver these values here.
_default_env_file = Path(__file__).resolve().parents[2] / "secrets" / ".env"
if _default_env_file.exists():
    load_dotenv(_default_env_file)

from harness import snapshot, verify
from harness.inventory import get_live_inventory
from harness.query_gen import GeneratedCase, generate_fuzzy_queries
from harness.report import CaseResult, write_run
from harness.runtime_factory import HarnessConnectionConfig, build_runtime
from harness.seeds import add_seeds, load_seeds
from rag import ProfileRagFormatter

ALL_INTENTS = ["command_control_status", "group_scene_ops", "node_ops", "routine_automation", "routine_status_ops"]
ROUTINE_INTENTS = {"routine_automation", "routine_status_ops"}
NODE_TRACKING_INTENTS = {"node_ops", "group_scene_ops"}  # families that can create a new node/group as a side effect


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fuzz-test the intent handler system against a live hub")
    parser.add_argument("--backend-api-classpath", required=True, help="e.g. iox.IoXWrapper")
    parser.add_argument("--backend-api-base-url", required=True)
    parser.add_argument("--backend-api-username", required=True)
    parser.add_argument("--backend-api-password", required=True)
    parser.add_argument("--runtime-config", required=True)
    parser.add_argument("--secrets-file", default=None)
    parser.add_argument("--intent-dir", default=None)
    parser.add_argument("--path-to-data-directory", default=None)
    parser.add_argument("--json-output", type=bool, default=True)
    parser.add_argument("--log-level", default="ERROR")
    parser.add_argument("--only", default=None, help="Comma-separated intent families to fuzz (default: all five)")
    parser.add_argument("--count-per-intent", type=int, default=3, help="Fresh fuzzy queries to generate per intent family")
    parser.add_argument("--no-seeds", action="store_true", help="Skip replaying previously-flagged seed queries")
    parser.add_argument("--seeds-only", action="store_true", help="Only replay seeds; skip fresh LLM generation")
    parser.add_argument("--no-seed-capture", action="store_true", help="Don't append newly-flagged queries to seeds/regression.yaml")
    return parser


def _extract_touched_device_ids(intent_family: str, result: Any) -> set[str]:
    """Best-effort: which real device/node ids did the actual tool call touch?

    Used only to enrich the report (did the system act on something other
    than what the query generator predicted?) -- never gates pass/fail, and
    any parsing hiccup here is swallowed rather than allowed to break the run.
    Encoding conventions differ by intent family (command_control_status
    tool args are Base-64 RAG ids; group_scene_ops/node_ops use raw
    addresses directly), so each is handled separately.
    """
    touched: set[str] = set()
    try:
        for tool_call in result.get_tool_calls() if result else []:
            args = tool_call.args
            items = args if isinstance(args, list) else [args]
            for item in items:
                if not isinstance(item, dict):
                    continue
                if intent_family == "command_control_status":
                    raw_id = item.get("device") or item.get("device_id")
                    if raw_id:
                        touched.add(ProfileRagFormatter.decode_id(raw_id))
                elif intent_family == "group_scene_ops":
                    for key in ("controller_address", "link_address"):
                        if item.get(key):
                            touched.add(str(item[key]))
                elif intent_family == "node_ops":
                    if item.get("node_id"):
                        touched.add(str(item["node_id"]))
    except Exception:
        pass
    return touched


async def run_generated_case(runtime: Any, case: GeneratedCase) -> CaseResult:
    nucore_interface = runtime.nucore_interface
    session_id = f"fuzz-{case.id}"

    # --- Snapshot predicted targets before the call. ---
    device_snapshots: dict[str, snapshot.DeviceSnapshot] = {}
    node_snapshots: dict[str, snapshot.NodeSnapshot] = {}
    for device_id in case.target_device_ids:
        try:
            device_snapshots[device_id] = await snapshot.snapshot_device(nucore_interface, device_id)
        except Exception:
            pass
        if case.intent_family == "node_ops":
            try:
                node_snapshots[device_id] = await snapshot.snapshot_node(nucore_interface, device_id)
            except Exception:
                pass

    routine_snap: snapshot.RoutineSnapshot | None = None
    if case.intent_family in ROUTINE_INTENTS and case.target_routine_id:
        routine_snap = await snapshot.snapshot_routine(nucore_interface, case.target_routine_id)

    before_node_ids: set[str] = set()
    if case.intent_family in NODE_TRACKING_INTENTS:
        before_node_ids = await snapshot.list_node_ids(nucore_interface)

    before_routine_ids: set[str] = set()
    if case.intent_family == "routine_automation" and not case.target_routine_id:
        before_routine_ids = await snapshot.list_routine_ids(nucore_interface)

    before_state: dict[str, Any] = {dev_id: snap.properties for dev_id, snap in device_snapshots.items()}
    if routine_snap is not None and routine_snap.definition:
        before_state["routine"] = routine_snap.definition

    # --- Run the query. ---
    started = time.monotonic()
    exception: Exception | None = None
    result = None
    try:
        results = await runtime.handle_query(case.query, session_id=session_id)
        result = (results or [None])[-1]
    except Exception as exc:  # network/backend errors against the real hub surface here
        exception = exc
    duration = time.monotonic() - started

    outcome = verify.verify_heuristic(result, exception=exception)
    routed_intent = result.route_result.intent if (result is not None and result.route_result is not None) else None

    # --- Enrich with actually-touched devices + (for routines) actual content, for review context only. ---
    details = dict(outcome.details)
    touched_ids = _extract_touched_device_ids(case.intent_family, result)
    unscoped_touched = touched_ids - set(case.target_device_ids)
    if unscoped_touched:
        details["unscoped_devices_touched"] = sorted(unscoped_touched)

    created_node_id: str | None = None
    if case.intent_family in NODE_TRACKING_INTENTS:
        after_node_ids = await snapshot.list_node_ids(nucore_interface)
        new_node_ids = after_node_ids - before_node_ids
        if new_node_ids:
            details["newly_created_node_ids"] = sorted(new_node_ids)
            if len(new_node_ids) == 1:
                created_node_id = next(iter(new_node_ids))

    created_routine_id: str | None = None
    if case.intent_family == "routine_automation":
        actual_routine_id = case.target_routine_id
        if not actual_routine_id:
            after_routine_ids = await snapshot.list_routine_ids(nucore_interface)
            new_routine_ids = after_routine_ids - before_routine_ids
            if len(new_routine_ids) == 1:
                created_routine_id = next(iter(new_routine_ids))
                actual_routine_id = created_routine_id
        if actual_routine_id:
            try:
                details["actual_routine"] = await nucore_interface.get_routine(actual_routine_id)
            except Exception:
                pass

    # --- Restore, best-effort, always attempted. ---
    restore_notes: dict[str, Any] = {}
    restore_error: str | None = None
    try:
        for device_id, snap in device_snapshots.items():
            restore_notes[device_id] = await snapshot.best_effort_restore_property(nucore_interface, snap)
        for device_id, node_snap in node_snapshots.items():
            await snapshot.restore_node(nucore_interface, node_snap)
        if created_node_id is not None:
            await snapshot.delete_created_node(nucore_interface, created_node_id)
        if routine_snap is not None:
            await snapshot.restore_routine(nucore_interface, routine_snap, created_routine_id=None)
        elif created_routine_id is not None:
            await snapshot.restore_routine(nucore_interface, snapshot.RoutineSnapshot(routine_id=None), created_routine_id=created_routine_id)
        if case.intent_family == "group_scene_ops" and touched_ids - {created_node_id}:
            restore_notes["group_scene_membership"] = "not automatically restored -- verify group/scene membership manually"
    except Exception as exc:
        restore_error = f"{type(exc).__name__}: {exc}"

    after_state: dict[str, Any] = {}
    for device_id in device_snapshots:
        try:
            after_state[device_id] = (await snapshot.snapshot_device(nucore_interface, device_id)).properties
        except Exception:
            pass

    return CaseResult(
        case_id=case.id,
        query=case.query,
        intent_family=case.intent_family,
        corner_case_type=case.corner_case_type,
        rationale=case.rationale,
        source=case.source,
        routed_intent=routed_intent,
        flagged=outcome.flagged,
        severity=outcome.severity,
        reason=outcome.reason,
        details=details,
        before_state=before_state,
        after_state=after_state,
        restore_notes=restore_notes,
        restore_error=restore_error,
        duration_s=duration,
    )


async def _run_all(args: argparse.Namespace) -> int:
    intents = [i for i in (args.only.split(",") if args.only else ALL_INTENTS) if i]

    # launch.json's ${env:...} substitution can't see envFile-provided values (they're
    # injected into this process's own environment only after VS Code has already
    # resolved args) -- so launch.json leaves these blank and we fall back to
    # os.environ here instead, which envFile does correctly populate.
    backend_api_username = args.backend_api_username or os.environ.get("BACKEND_API_USER_NAME")
    backend_api_password = args.backend_api_password or os.environ.get("BACKEND_API_PASSWORD")

    connection_config = HarnessConnectionConfig(
        backend_api_classpath=args.backend_api_classpath,
        backend_api_base_url=args.backend_api_base_url,
        backend_api_username=backend_api_username,
        backend_api_password=backend_api_password,
        runtime_config_path=Path(args.runtime_config),
        secrets_file=Path(args.secrets_file) if args.secrets_file else None,
        intent_dir=Path(args.intent_dir) if args.intent_dir else None,
        path_to_data_directory=Path(args.path_to_data_directory) if args.path_to_data_directory else None,
        json_output=bool(args.json_output),
        log_level=args.log_level,
    )
    runtime = build_runtime(connection_config)

    cases: list[GeneratedCase] = []
    if not args.no_seeds:
        cases.extend(c for c in load_seeds() if c.intent_family in intents)

    results: list[CaseResult] = []
    try:
        if not args.seeds_only:
            print("Pulling live device/routine inventory from the hub...")
            inventory = await get_live_inventory(runtime.nucore_interface)
            llm_config = dict(runtime.runtime_config.get("supported_llms", {}).get("default", {}))
            llm_config["temperature"] = 0.9  # favor variety over the assistant's normal low-temperature precision
            print(f"Generating {args.count_per_intent} fuzzy queries per intent family ({', '.join(intents)})...")
            generated = await generate_fuzzy_queries(runtime.llm_client, llm_config, inventory, intents, args.count_per_intent)
            cases.extend(generated)

        if not cases:
            print("No cases to run.")
            return 0

        for case in cases:
            print(f"[{case.source:9s}] {case.intent_family:22s} {case.query[:70]!r} ... ", end="", flush=True)
            result = await run_generated_case(runtime, case)
            suffix = f" - {result.reason}" if result.flagged else ""
            print(f"{result.severity.upper()}{suffix}")
            results.append(result)
    finally:
        runtime.shutdown()

    if not args.no_seed_capture:
        newly_flagged = [case for case, result in zip(cases, results) if result.flagged]
        add_seeds(newly_flagged)

    run_json_path, failures_md_path = write_run(results)
    ok = sum(1 for r in results if r.severity == "ok")
    review = sum(1 for r in results if r.severity == "review")
    error = sum(1 for r in results if r.severity == "error")
    print(f"\n{ok}/{len(results)} ok, {review} review, {error} error.")
    print(f"Full run: {run_json_path}")
    if failures_md_path:
        print(f"Flagged: {failures_md_path}")
        return 1
    return 0


def main() -> None:
    args = _build_parser().parse_args()
    exit_code = asyncio.run(_run_all(args))
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()

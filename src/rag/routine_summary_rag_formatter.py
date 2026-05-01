"""
Converts the flat NuCore routines runtime JSON into a compact, LLM-friendly
nested tree where folders are internal nodes and routines are leaf nodes.

Compactness rules applied:
  - Timestamps shortened to "YYYY-MM-DD HH:MM" and renamed (lastRun, lastFinish, nextRun)
  - Empty timestamps omitted entirely
  - "running": "idle" omitted (idle is the unremarkable default)
  - "enabled": true omitted (enabled is the default; only shown when false)
  - "runAtStartup": false omitted (false is the default; only shown when true)
  - "comment" omitted when absent
"""

import json
from datetime import datetime
from .rag_formatter import RAGFormatter
from .rag_data_struct import RAGData
from utils import get_logger
logger = get_logger(__name__)


class RoutineSummaryRagFormatter(RAGFormatter):
    
    def __init__(self):
        super().__init__(name="routine_status_ops", description="Provides information about the status of routines, including their schedule, last run times, and whether they are currently running. Useful for monitoring and debugging routine behavior.")


    @staticmethod   
    def _fmt_ts(ts: str) -> str:
        if not ts:
            return ""
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            return dt.strftime("%Y-%m-%d %H:%M")
        except ValueError:
            return ts

    @staticmethod
    def _make_folder_node(item: dict, children: list) -> dict:
        node = {"id": item["id"], "name": item["name"], "status": item["status"]}
        if item.get("comment"):
            node["comment"] = item["comment"]
        if children:
            node["children"] = children
        return node


    @staticmethod
    def _make_routine_node(item: dict) -> dict:
        node = {"id": item["id"], "name": item["name"], "status": item["status"]}
        if item.get("comment"):
            node["comment"] = item["comment"]
        # Only include non-default state to reduce noise
        if not item.get("enabled", True):
            node["enabled"] = False
        if item.get("runAtStartup", False):
            node["runAtStartup"] = True
        running = item.get("running", "idle")
        if running != "idle":
            node["running"] = running
        for src_key, out_key in [
            ("lastRunTime",           "lastRun"),
            ("lastFinishTime",        "lastFinish"),
            ("nextScheduledRunTime",  "nextRun"),
        ]:
            val = RoutineSummaryRagFormatter._fmt_ts(item.get(src_key, ""))
            if val:
                node[out_key] = val
        return node

    @staticmethod
    def build_tree(data: list) -> list:
        by_id = {item["id"]: item for item in data}
        children_map: dict[str, list] = {item["id"]: [] for item in data}
        roots: list[str] = []

        for item in data:
            pid = item.get("parentId")
            if pid and pid in children_map:
                children_map[pid].append(item["id"])
            else:
                roots.append(item["id"])

        def recurse(node_id: str) -> dict:
            item = by_id[node_id]
            if item.get("folder", False):
                kids = [recurse(cid) for cid in children_map[node_id]]
                return RoutineSummaryRagFormatter._make_folder_node(item, kids)
            return RoutineSummaryRagFormatter._make_routine_node(item)

        return [recurse(r) for r in roots]


    def format(self, **kwargs) -> RAGData:
        raw = kwargs.get("routines_summary")
        if not raw:
            raise ValueError("Missing required input: raw_routine_summary")

        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError as e:
                raise ValueError(f"Invalid JSON input: {e}")

        data = raw.get("data", [])
        tree = RoutineSummaryRagFormatter.build_tree(data)
        
        routine_count = sum(1 for item in data if not item.get("folder", False))
        folder_count  = sum(1 for item in data if item.get("folder", False))
        logger.info(f"Input : {len(data)} items ({folder_count} folders, {routine_count} routines)")

             # Create RAG data structure
        rag_docs = RAGData()


        # Add as single document with all devices
        content = f"```json\n{json.dumps(tree)}\n```"
        
        rag_docs.add_document(
            content,
            None,  # No embeddings
            id="routines_summary",
            metadata={"format": "minimal tree", "routine_count": routine_count, "folder_count": folder_count}
        )
        
        return rag_docs
        


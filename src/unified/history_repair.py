"""Repairs for stored conversation history that the provider would reject
when replayed."""

from __future__ import annotations

from typing import Any

from utils import get_logger

logger = get_logger(__name__)


def drop_unpaired_web_search_blocks(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove Claude ``server_tool_use`` (web search) blocks that have no
    ``web_search_tool_result`` anywhere in *messages*, and results with no
    matching use.

    A turn can be stored with a search request whose result block never came
    back in any stored response (the search is issued in one round alongside a
    client ``tool_use`` and resolved server-side in the next, and that next
    response can carry only text). Replaying such an orphan makes the API
    reject the *entire* request ("web_search tool use ... was found without a
    corresponding web_search_tool_result block"), and since the orphan is in
    persisted history, every later turn of that session fails the same way.
    Pairing is checked across all of *messages*, not per message, because a
    use and its result legitimately land in different assistant messages
    (the use alongside a client tool_use, the result at the start of the
    next round). Returns *messages* itself when nothing needs removing; never
    mutates the stored messages.
    """
    use_ids: set[str] = set()
    result_ids: set[str] = set()
    for message in messages:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "server_tool_use" and block.get("id"):
                use_ids.add(block["id"])
            elif block.get("type") == "web_search_tool_result" and block.get("tool_use_id"):
                result_ids.add(block["tool_use_id"])
    orphan_uses = use_ids - result_ids
    orphan_results = result_ids - use_ids
    if not orphan_uses and not orphan_results:
        return messages

    def is_orphan(block: Any) -> bool:
        if not isinstance(block, dict):
            return False
        if block.get("type") == "server_tool_use":
            return block.get("id") in orphan_uses
        if block.get("type") == "web_search_tool_result":
            return block.get("tool_use_id") in orphan_results
        return False

    cleaned: list[dict[str, Any]] = []
    for message in messages:
        content = message.get("content")
        if isinstance(content, list) and any(is_orphan(block) for block in content):
            kept = [block for block in content if not is_orphan(block)]
            if not kept:
                continue
            message = {**message, "content": kept}
        cleaned.append(message)
    logger.warning(
        "unified: dropped %d unpaired web_search block(s) from replayed history",
        len(orphan_uses) + len(orphan_results),
    )
    return cleaned

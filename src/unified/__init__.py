"""Unified single-prompt + native-tool-calling path (design/design.md).

A new, parallel path alongside the router/intent-handler pipeline -- see
``design/design.md``'s "UNIFIED PROMPT + TOOLS" proposal. Deliberately does
not import from or depend on ``intent_handler_directory`` or the router;
it talks directly to the shared ``NuCoreInterface``/``IoXWrapper`` backend
primitives those also happen to call, independently.
"""

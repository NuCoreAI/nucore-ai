"""Unified single-prompt + native-tool-calling runtime (design/design.md).

The single query-handling path: one system prompt (compact DEVICE DATABASE/
ROUTINES DATABASE), one native tool-calling agentic loop, no router, no
per-intent directory dispatch. Talks directly to the shared
``NuCoreInterface``/``IoXWrapper`` backend primitives.
"""

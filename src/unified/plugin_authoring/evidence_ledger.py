"""Per-connection state for the discovery tools (design/developers/impl_plan.md
Phase 2): what's been tried, what was found, and whether the fallback tier
order has actually been followed. One instance per connection/session -- see
run_unified_runtime.py's ``_resolve_tool_set``/``_make_dispatch``, which
construct a fresh ledger every time the dispatch factory is called, so one
user's evidence can never satisfy another's "credible source" check.

Tier order is: store first (recommend-and-stop) -> web search (native
Claude or Brave/Tavily fallback, see run_unified_runtime.py), with any
GitHub-domain result license-vetted wherever it surfaces (search_result_
enrichment.classify_and_record), -> asking the customer for URLs as a last
resort. ``search_github_plugins`` is no longer a mandatory step before web
search (it used to be -- see git history for why that was dropped: a
Claude-native web search call is a server-side tool our dispatch layer
never sees, so a "call GitHub first" sequencing rule can't be enforced once
that path exists; classifying every result by URL, regardless of which tool
produced it, applies uniformly instead). ``github_tried``/
``github_query_count`` remain purely as ``search_github_plugins``'s own
cap/attempt bookkeeping, not a gate on anything else.

This module has no knowledge of HTTP, GitHub, or any search provider; it's
just the bookkeeping the discovery handlers (handlers/discovery.py,
search_result_enrichment.py) all share.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class EvidenceLedger:
    """Tracks fallback-tier progress and the evidence gathered so far for one
    plugin-authoring session.

    ``web_search_available`` is set once at construction from whether a
    search engine *and* its key were both configured for this run (see
    ``_resolve_tool_set``) -- it's not about whether a search has succeeded,
    only whether the tier exists at all this session. ``can_ask_for_urls``
    uses it to treat "the web-search tier doesn't exist" the same as "the
    web-search tier was tried" when deciding if asking the user for URLs is
    reasonable yet.
    """

    web_search_available: bool = False

    store_checked: bool = False
    github_tried: bool = False
    web_search_tried: bool = False

    sources: list[dict[str, Any]] = field(default_factory=list)

    github_query_count: int = 0
    web_search_query_count: int = 0
    fetch_count: int = 0
    license_lookup_count: int = 0

    def record_source(
        self,
        *,
        tier: str,
        url: str | None = None,
        title: str | None = None,
        license: str | None = None,  # noqa: A002 - matches the domain term
        license_ok: bool | None = None,
        note: str | None = None,
    ) -> dict[str, Any]:
        """Append one evidence entry and return it (so a handler can include
        it verbatim in its own return value without re-deriving the shape)."""
        entry = {
            "tier": tier,
            "url": url,
            "title": title,
            "license": license,
            "license_ok": license_ok,
            "note": note,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }
        self.sources.append(entry)
        return entry

    def can_ask_for_urls(self) -> bool:
        """The ask-for-URLs step is only reasonable once web search has
        either been tried this session or was never available to try in
        the first place (``web_search_available`` reflects native-or-
        fallback availability, see run_unified_runtime.py)."""
        return self.web_search_tried or not self.web_search_available

    def has_evidence(self) -> bool:
        """Phase 3's generate_plugin_scaffold calls this before writing
        anything -- an empty ledger means refuse and create no files."""
        return bool(self.sources)

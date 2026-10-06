"""Discovery tools for the non-technical plugin-authoring flow
(design/developers/impl_plan.md Phase 2): check the NuCore store first, then
a bounded fallback chain -- GitHub search, web search (native Claude or a
configurable Brave/Tavily engine, see run_unified_runtime.py's
``_resolve_tool_set``), then (conversationally, no tool here) asking the
user for URLs -- before any scaffold gets generated. Every handler records
what it found (or didn't) onto the per-connection ``EvidenceLedger`` passed
in by ``run_unified_runtime.py``'s dispatch factory.

``search_github_plugins`` is not a mandatory step before ``search_web`` --
see ``..search_result_enrichment`` and ``evidence_ledger.py``'s module
docstring for why (a GitHub URL gets the same license-vetting treatment
wherever it surfaces, GitHub-search or general web search, instead of a
sequencing rule that a Claude-native web search call couldn't honor
anyway).

Each outbound call (GitHub, Brave/Tavily, fetch_reference) follows
``iox_wrapper.py``'s existing httpx convention: an explicit timeout, never
raises -- a connection failure or non-2xx response becomes an ``{"error":
...}`` dict, same as every other tool handler in this codebase.
"""

from __future__ import annotations

from typing import Any

import httpx

from nucore import NuCoreInterface

from ...handlers import plugin_management
from .. import search_result_enrichment
from ..evidence_ledger import EvidenceLedger
from ..secret_guard import contains_secret, redact_secrets  # noqa: F401 - redact_secrets re-exported for callers that logged via this module

REQUEST_TIMEOUT = 15.0

# Bounded, named constants -- not buried magic numbers. Proposed defaults;
# easy to tune once real usage shows whether they're too tight or too loose.
MAX_GITHUB_QUERIES = 3
GITHUB_RESULTS_PER_QUERY = 5
MAX_WEB_SEARCH_QUERIES = 3
WEB_SEARCH_RESULTS_PER_QUERY = 5
MAX_FETCHES = 5
FETCH_BODY_CHAR_CAP = 50_000

# Permissive license allowlist (design/developers/impl_plan.md: "MIT,
# Apache-2.0, BSD" -- both BSD variants spelled out). Anything else is
# flagged, never silently treated as safe to copy from.
LICENSE_ALLOWLIST = frozenset({"MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause"})

_TEXT_CONTENT_TYPES = ("text/", "application/json", "application/xml", "application/x-yaml")


def _vet_license(spdx_id: str | None) -> tuple[bool, str]:
    if not spdx_id:
        return False, "no license detected"
    if spdx_id in LICENSE_ALLOWLIST:
        return True, f"{spdx_id} (allowlisted)"
    return False, f"{spdx_id} is not on the permissive allowlist -- never copy from this source"


async def search_store_plugins(nucore_interface: NuCoreInterface, args: dict[str, Any], *, ledger: EvidenceLedger) -> Any:
    """Wraps ``plugin_management.list_store_plugins`` and scores each
    candidate by keyword overlap against *args['query']* -- the NuCore
    store is checked before any external research, per impl_plan.md's
    "store-first recommend-and-stop" design."""
    query = (args.get("query") or "").strip().lower()
    if not query:
        return {"error": "query is required"}

    result = await plugin_management.list_store_plugins(nucore_interface, {})
    if "error" in result:
        ledger.store_checked = True
        return result

    keywords = query.split()
    candidates = []
    for plugin in result.get("plugins", []):
        haystack = f"{plugin.get('name') or ''} {plugin.get('description') or ''}".lower()
        score = sum(1 for kw in keywords if kw in haystack)
        if score > 0:
            candidates.append({**plugin, "viability_score": score})
    candidates.sort(key=lambda p: p["viability_score"], reverse=True)

    ledger.store_checked = True
    return {"candidates": candidates, "recommend_and_stop": bool(candidates)}


async def search_github_plugins(
    nucore_interface: NuCoreInterface, args: dict[str, Any], *, ledger: EvidenceLedger, secret_values: list[str]
) -> Any:
    """Anonymous GitHub REST repo search (no token -- the query cap keeps
    this well under GitHub's unauthenticated rate limit). Vets each result's
    license against ``LICENSE_ALLOWLIST`` and records every result (vetted
    or not) onto the ledger; ``github_tried`` is set regardless of whether
    anything useful came back, since the tier was genuinely attempted."""
    query = (args.get("query") or "").strip()
    if not query:
        return {"error": "query is required"}

    secret_hit = contains_secret(query, secret_values)
    if secret_hit:
        return {"error": "query appears to contain a configured secret value; refusing to send it externally"}

    if ledger.github_query_count >= MAX_GITHUB_QUERIES:
        return {"error": f"GitHub search cap reached ({MAX_GITHUB_QUERIES} queries this session)"}
    ledger.github_query_count += 1
    ledger.github_tried = True

    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            response = await client.get(
                "https://api.github.com/search/repositories",
                params={"q": query, "per_page": GITHUB_RESULTS_PER_QUERY},
                headers={"Accept": "application/vnd.github+json"},
            )
    except Exception as exc:
        return {"error": f"GitHub search failed: {exc}"}

    if response.status_code != 200:
        return {"error": f"GitHub search returned status {response.status_code}"}

    items = response.json().get("items", [])
    results = []
    for item in items[:GITHUB_RESULTS_PER_QUERY]:
        spdx_id = (item.get("license") or {}).get("spdx_id")
        license_ok, note = _vet_license(spdx_id)
        entry = ledger.record_source(
            tier="github",
            url=item.get("html_url"),
            title=item.get("full_name"),
            license=spdx_id,
            license_ok=license_ok,
            note=note,
        )
        results.append({**entry, "description": item.get("description"), "updated_at": item.get("updated_at")})

    return {"repositories": results}


async def _search_brave(query: str, api_key: str) -> list[dict[str, Any]]:
    """Normalizes to the same ``{title, url, snippet}`` shape ``_search_tavily``
    returns -- callers never need to know which provider answered."""
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        response = await client.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": query, "count": WEB_SEARCH_RESULTS_PER_QUERY},
            headers={"Accept": "application/json", "X-Subscription-Token": api_key},
        )
    response.raise_for_status()
    results = (response.json().get("web") or {}).get("results", [])
    return [
        {"title": r.get("title"), "url": r.get("url"), "snippet": r.get("description")}
        for r in results[:WEB_SEARCH_RESULTS_PER_QUERY]
    ]


async def _search_tavily(query: str, api_key: str) -> list[dict[str, Any]]:
    """Normalizes to the same ``{title, url, snippet}`` shape ``_search_brave``
    returns -- callers never need to know which provider answered."""
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        response = await client.post(
            "https://api.tavily.com/search",
            json={"query": query, "max_results": WEB_SEARCH_RESULTS_PER_QUERY},
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        )
    response.raise_for_status()
    results = response.json().get("results", [])
    return [
        {"title": r.get("title"), "url": r.get("url"), "snippet": r.get("content")}
        for r in results[:WEB_SEARCH_RESULTS_PER_QUERY]
    ]


_PROVIDERS = {"brave": _search_brave, "tavily": _search_tavily}


async def search_web(
    nucore_interface: NuCoreInterface,
    args: dict[str, Any],
    *,
    ledger: EvidenceLedger,
    search_engine: str,
    search_engine_api_key: str,
    secret_values: list[str],
) -> Any:
    """Tier 2 (the Brave/Tavily fallback -- Claude-native search is a
    separate path entirely, see run_unified_runtime.py's
    ``_resolve_tool_set``). Dispatches to whichever provider
    ``search_engine`` names; neither Brave nor Tavily is the "default"
    path, they're equally valid selections. No longer gated on GitHub
    having been tried first -- see evidence_ledger.py's module docstring."""
    query = (args.get("query") or "").strip()
    if not query:
        return {"error": "query is required"}

    secret_hit = contains_secret(query, secret_values)
    if secret_hit:
        return {"error": "query appears to contain a configured secret value; refusing to send it externally"}

    if ledger.web_search_query_count >= MAX_WEB_SEARCH_QUERIES:
        return {"error": f"web search cap reached ({MAX_WEB_SEARCH_QUERIES} queries this session)"}

    provider = _PROVIDERS.get(search_engine)
    if provider is None:
        return {"error": f"unknown search engine '{search_engine}'"}

    ledger.web_search_query_count += 1
    ledger.web_search_tried = True

    try:
        raw_results = await provider(query, search_engine_api_key)
    except Exception as exc:
        return {"error": f"{search_engine} search failed: {exc}"}

    results = [
        await search_result_enrichment.classify_and_record(
            ledger, tier="web_search", url=r.get("url"), title=r.get("title"), secret_values=secret_values, note=r.get("snippet")
        )
        for r in raw_results
    ]
    return {"results": results}


async def fetch_reference(
    nucore_interface: NuCoreInterface, args: dict[str, Any], *, ledger: EvidenceLedger, secret_values: list[str]
) -> Any:
    """Fetches one discovered-or-user-supplied URL. ``source_tier`` is the
    model's own self-report of where the URL came from
    (``"github"``/``"web_search"``/``"user_url"``) -- this is the one spot
    where tier enforcement is an approximation rather than a hard guarantee,
    since nothing stops the model from mislabeling it. A ``"user_url"``
    fetch (the "ask the user for URLs" tier, which has no tool of its own)
    is refused unless web search has genuinely been tried this session, or
    was never available at all -- see ``EvidenceLedger.can_ask_for_urls``."""
    url = (args.get("url") or "").strip()
    source_tier = args.get("source_tier")
    if not url:
        return {"error": "url is required"}
    if source_tier not in ("github", "web_search", "user_url"):
        return {"error": "source_tier must be 'github', 'web_search', or 'user_url'"}

    if source_tier == "user_url" and not ledger.can_ask_for_urls():
        return {"error": "web search must be tried (or unavailable) this session before fetching a user-supplied URL"}

    secret_hit = contains_secret(url, secret_values)
    if secret_hit:
        return {"error": "url appears to contain a configured secret value; refusing to fetch it"}

    if ledger.fetch_count >= MAX_FETCHES:
        return {"error": f"fetch cap reached ({MAX_FETCHES} fetches this session)"}
    ledger.fetch_count += 1

    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, follow_redirects=True) as client:
            response = await client.get(url)
    except Exception as exc:
        return {"error": f"fetch failed: {exc}"}

    if response.status_code != 200:
        return {"error": f"fetch returned status {response.status_code}"}

    content_type = response.headers.get("content-type", "")
    if not any(content_type.startswith(t) for t in _TEXT_CONTENT_TYPES):
        return {"error": f"refusing to fetch non-text content-type '{content_type}'"}

    body = response.text
    truncated = len(body) > FETCH_BODY_CHAR_CAP
    if truncated:
        body = body[:FETCH_BODY_CHAR_CAP]

    ledger.record_source(tier=source_tier, url=url, note="fetched by fetch_reference")
    return {"url": url, "content": body, "truncated": truncated}

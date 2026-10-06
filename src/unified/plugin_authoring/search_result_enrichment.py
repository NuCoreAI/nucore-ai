"""Shared GitHub-domain classification for search results, regardless of
which path found them (design/developers/impl_plan.md's
``search_github_plugins`` fallback chain, now generalized): a plain
``{title, url, snippet}`` result from ``search_web`` (Brave/Tavily) or from
Claude's native ``web_search`` server tool both just carry a URL -- when
that URL is a GitHub repo, this module fires one follow-up call to GitHub's
single-repo REST endpoint to get its real license (the same vetting
``search_github_plugins`` already gets for free from GitHub's *search* API
response, which this generic path doesn't have), then records it onto the
``EvidenceLedger`` exactly like ``search_github_plugins`` does. Anything
that isn't a GitHub repo URL is just recorded plainly.

This replaces the old "search_github_plugins must be called before
search_web" sequencing rule (impossible to enforce once a server-side tool
can be invoked without ever reaching our dispatch layer) with a rule that
applies uniformly no matter which tool actually produced the URL.
"""

from __future__ import annotations

import re
from typing import Any

import httpx

from .evidence_ledger import EvidenceLedger
from .secret_guard import contains_secret

REQUEST_TIMEOUT = 15.0

# Same reasoning as discovery.py's other caps -- bounded, not a buried
# magic number. A session doing a lot of general web search could otherwise
# trigger an unbounded number of follow-up GitHub lookups.
MAX_LICENSE_LOOKUPS = 5

# Permissive license allowlist -- identical list to discovery.py's, kept
# here too since this module must not import from handlers/discovery.py
# (the dependency runs the other way: discovery.py will import this module).
LICENSE_ALLOWLIST = frozenset({"MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause"})

_GITHUB_REPO_RE = re.compile(r"^https?://github\.com/([^/]+)/([^/?#]+)")


def _vet_license(spdx_id: str | None) -> tuple[bool, str]:
    if not spdx_id:
        return False, "no license detected"
    if spdx_id in LICENSE_ALLOWLIST:
        return True, f"{spdx_id} (allowlisted)"
    return False, f"{spdx_id} is not on the permissive allowlist -- never copy from this source"


async def _fetch_github_license(owner: str, repo: str) -> str | None:
    """Anonymous ``GET /repos/{owner}/{repo}`` -- same never-raises httpx
    convention as discovery.py. Returns the repo's SPDX license id, or
    ``None`` on any failure (no license detected, request error, non-200)."""
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            response = await client.get(
                f"https://api.github.com/repos/{owner}/{repo}",
                headers={"Accept": "application/vnd.github+json"},
            )
    except Exception:
        return None
    if response.status_code != 200:
        return None
    return ((response.json() or {}).get("license") or {}).get("spdx_id")


async def classify_and_record(
    ledger: EvidenceLedger,
    *,
    tier: str,
    url: str | None,
    title: str | None,
    secret_values: list[str],
    note: str | None = None,
) -> dict[str, Any]:
    """Records one search result onto *ledger*, enriching it with real
    license metadata when *url* is a GitHub repo and the per-session lookup
    cap hasn't been reached yet. Never raises -- a failed/capped lookup just
    means the result is recorded without license info, same as any other
    non-GitHub result. *note* (e.g. a search result's snippet) is kept as-is
    for a non-GitHub result; a GitHub result's license-vetting note takes
    its place, since there's one note slot and license info is the more
    useful of the two there."""
    match = _GITHUB_REPO_RE.match(url or "")
    if not match or contains_secret(url or "", secret_values):
        return ledger.record_source(tier=tier, url=url, title=title, note=note)

    if ledger.license_lookup_count >= MAX_LICENSE_LOOKUPS:
        return ledger.record_source(tier=tier, url=url, title=title, note=note or "license lookup cap reached this session")

    ledger.license_lookup_count += 1
    owner, repo = match.group(1), match.group(2)
    spdx_id = await _fetch_github_license(owner, repo)
    license_ok, license_note = _vet_license(spdx_id)
    return ledger.record_source(tier=tier, url=url, title=title, license=spdx_id, license_ok=license_ok, note=license_note)

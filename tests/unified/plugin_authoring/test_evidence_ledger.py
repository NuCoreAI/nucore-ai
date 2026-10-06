"""EvidenceLedger's gating logic in isolation -- no HTTP, no dispatch, just
the fallback-tier bookkeeping the discovery handlers (test_discovery.py)
rely on.
"""

from __future__ import annotations

from unified.plugin_authoring.evidence_ledger import EvidenceLedger


def test_fresh_ledger_cannot_ask_for_urls_when_web_search_is_available():
    ledger = EvidenceLedger(web_search_available=True)
    assert ledger.can_ask_for_urls() is False


def test_ask_for_urls_unlocks_once_web_search_tried():
    # No more "search_github_plugins must run first" gate (see
    # evidence_ledger.py's module docstring) -- web_search_tried alone is
    # enough, regardless of github_tried.
    ledger = EvidenceLedger(web_search_available=True)
    assert ledger.github_tried is False
    ledger.web_search_tried = True
    assert ledger.can_ask_for_urls() is True


def test_ask_for_urls_allowed_when_web_search_was_never_available():
    ledger = EvidenceLedger(web_search_available=False)
    assert ledger.can_ask_for_urls() is True  # web search tier doesn't exist this session


def test_has_evidence_reflects_recorded_sources():
    ledger = EvidenceLedger()
    assert ledger.has_evidence() is False

    ledger.record_source(tier="github", url="https://github.com/example/repo")
    assert ledger.has_evidence() is True


def test_record_source_returns_the_entry_it_appended():
    ledger = EvidenceLedger()
    entry = ledger.record_source(tier="github", url="https://github.com/example/repo", title="example/repo")
    assert entry["tier"] == "github"
    assert entry["url"] == "https://github.com/example/repo"
    assert entry in ledger.sources

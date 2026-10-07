"""EvidenceLedger's gating logic in isolation -- no HTTP, no dispatch, just
the fallback-tier bookkeeping the discovery handlers (test_discovery.py)
rely on. Also covers merge_sources/render_sources_md/parse_sources_md, the
pure functions behind sources.md's cross-session, deduped evidence record.
"""

from __future__ import annotations

from unified.plugin_authoring.evidence_ledger import (
    EvidenceLedger,
    merge_sources,
    parse_sources_md,
    render_sources_md,
)


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


# --- merge_sources ---


def _source(**overrides):
    base = {
        "tier": "github",
        "url": "https://github.com/example/repo",
        "title": "example/repo",
        "license": "MIT",
        "license_ok": True,
        "note": "a note",
        "fetched_at": "2026-01-01T00:00:00+00:00",
    }
    base.update(overrides)
    return base


def test_merge_sources_appends_a_genuinely_new_source():
    existing = [_source(url="https://a.example")]
    new = [_source(url="https://b.example")]
    merged = merge_sources(existing, new)
    assert [s["url"] for s in merged] == ["https://a.example", "https://b.example"]


def test_merge_sources_skips_a_url_match_and_leaves_the_existing_row_untouched():
    existing = [_source(url="https://a.example", title="old title", license_ok=True)]
    new = [_source(url="https://a.example", title="new title", license_ok=False)]
    merged = merge_sources(existing, new)
    assert len(merged) == 1
    assert merged[0]["title"] == "old title"
    assert merged[0]["license_ok"] is True


def test_merge_sources_dedupes_by_title_when_no_url():
    existing = [_source(url=None, title="store listing")]
    new = [_source(url=None, title="store listing")]
    merged = merge_sources(existing, new)
    assert len(merged) == 1


def test_merge_sources_dedupes_within_the_new_list_itself():
    existing: list = []
    new = [_source(url="https://a.example"), _source(url="https://a.example")]
    merged = merge_sources(existing, new)
    assert len(merged) == 1


def test_merge_sources_preserves_order():
    existing = [_source(url="https://a.example")]
    new = [_source(url="https://b.example"), _source(url="https://c.example")]
    merged = merge_sources(existing, new)
    assert [s["url"] for s in merged] == ["https://a.example", "https://b.example", "https://c.example"]


# --- render_sources_md / parse_sources_md ---


def test_render_then_parse_round_trips_fields():
    sources = [
        _source(url="https://a.example", license_ok=True),
        _source(url="https://b.example", title="no license info", license=None, license_ok=None, note=None),
    ]
    parsed = parse_sources_md(render_sources_md(sources))
    assert parsed[0]["url"] == "https://a.example"
    assert parsed[0]["license_ok"] is True
    assert parsed[0]["note"] == "a note"
    assert parsed[1]["license"] is None
    assert parsed[1]["license_ok"] is None
    assert parsed[1]["note"] is None


def test_render_escapes_pipes_and_newlines_in_cells():
    sources = [_source(title="a | pipe", note="line one\nline two")]
    rendered = render_sources_md(sources)
    data_lines = [line for line in rendered.splitlines() if line.startswith("| github")]
    assert len(data_lines) == 1
    assert "\n" not in data_lines[0].split("|")[-2]
    parsed = parse_sources_md(rendered)
    assert parsed[0]["title"] == "a / pipe"
    assert "line one line two" in parsed[0]["note"]


def test_parse_sources_md_skips_header_and_separator_rows():
    rendered = render_sources_md([_source()])
    parsed = parse_sources_md(rendered)
    assert len(parsed) == 1


def test_parse_sources_md_tolerates_empty_or_malformed_input():
    assert parse_sources_md("") == []
    assert parse_sources_md("just some prose, no table here\n") == []

"""search_result_enrichment.classify_and_record -- the GitHub-domain
license enrichment shared by search_web and plugin_authoring's native
Claude web search harvesting hook (run_unified_runtime.py). All outbound
calls mocked, no real network.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from unified.plugin_authoring import search_result_enrichment
from unified.plugin_authoring.evidence_ledger import EvidenceLedger


def _fake_response(*, status_code=200, json_data=None):
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = json_data or {}
    return response


def _mock_client(*, get_response=None):
    client = AsyncMock()
    client.get = AsyncMock(return_value=get_response)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=client)
    cm.__aexit__ = AsyncMock(return_value=False)
    return patch.object(search_result_enrichment.httpx, "AsyncClient", return_value=cm), client


@pytest.mark.asyncio
async def test_github_repo_url_is_license_enriched():
    response = _fake_response(json_data={"license": {"spdx_id": "MIT"}})
    patcher, client = _mock_client(get_response=response)
    ledger = EvidenceLedger()

    with patcher:
        entry = await search_result_enrichment.classify_and_record(
            ledger, tier="web_search", url="https://github.com/acme/pool-plugin", title="acme/pool-plugin", secret_values=[]
        )

    assert entry["license"] == "MIT"
    assert entry["license_ok"] is True
    assert ledger.license_lookup_count == 1
    client.get.assert_called_once_with(
        "https://api.github.com/repos/acme/pool-plugin", headers={"Accept": "application/vnd.github+json"}
    )


@pytest.mark.asyncio
async def test_github_repo_url_with_non_permissive_license_is_flagged():
    response = _fake_response(json_data={"license": {"spdx_id": "GPL-3.0"}})
    patcher, _ = _mock_client(get_response=response)
    ledger = EvidenceLedger()

    with patcher:
        entry = await search_result_enrichment.classify_and_record(
            ledger, tier="web_search", url="https://github.com/acme/gpl-plugin", title="x", secret_values=[]
        )

    assert entry["license"] == "GPL-3.0"
    assert entry["license_ok"] is False


@pytest.mark.asyncio
async def test_non_github_url_is_recorded_plainly_with_no_extra_call():
    ledger = EvidenceLedger()
    with patch.object(search_result_enrichment.httpx, "AsyncClient") as client_cls:
        entry = await search_result_enrichment.classify_and_record(
            ledger, tier="web_search", url="https://acme.example/api", title="Acme API", secret_values=[], note="docs"
        )

    client_cls.assert_not_called()
    assert entry["url"] == "https://acme.example/api"
    assert entry["note"] == "docs"
    assert entry.get("license") is None
    assert ledger.license_lookup_count == 0


@pytest.mark.asyncio
async def test_github_url_containing_a_secret_is_recorded_plainly_not_fetched():
    ledger = EvidenceLedger()
    with patch.object(search_result_enrichment.httpx, "AsyncClient") as client_cls:
        entry = await search_result_enrichment.classify_and_record(
            ledger,
            tier="web_search",
            url="https://github.com/acme/repo?token=sk-secret123",
            title="x",
            secret_values=["sk-secret123"],
        )

    client_cls.assert_not_called()
    assert entry.get("license") is None


@pytest.mark.asyncio
async def test_license_lookup_cap_is_enforced():
    response = _fake_response(json_data={"license": {"spdx_id": "MIT"}})
    patcher, client = _mock_client(get_response=response)
    ledger = EvidenceLedger()

    with patcher:
        for _ in range(search_result_enrichment.MAX_LICENSE_LOOKUPS):
            entry = await search_result_enrichment.classify_and_record(
                ledger, tier="web_search", url="https://github.com/acme/repo", title="x", secret_values=[]
            )
            assert entry["license"] == "MIT"

        over_cap = await search_result_enrichment.classify_and_record(
            ledger, tier="web_search", url="https://github.com/acme/repo", title="x", secret_values=[]
        )

    assert over_cap.get("license") is None
    assert client.get.call_count == search_result_enrichment.MAX_LICENSE_LOOKUPS


@pytest.mark.asyncio
async def test_github_lookup_failure_falls_back_to_plain_record():
    patcher, _ = _mock_client(get_response=_fake_response(status_code=404))
    ledger = EvidenceLedger()

    with patcher:
        entry = await search_result_enrichment.classify_and_record(
            ledger, tier="web_search", url="https://github.com/acme/missing", title="x", secret_values=[]
        )

    assert entry.get("license") is None
    assert entry["license_ok"] is False

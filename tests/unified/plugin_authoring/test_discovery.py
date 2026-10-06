"""The four discovery tools (design/developers/impl_plan.md Phase 2), with
every outbound call mocked -- these tests never touch a real network.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from unified.plugin_authoring.evidence_ledger import EvidenceLedger
from unified.plugin_authoring.handlers import discovery


def _fake_response(*, status_code=200, json_data=None, text="", headers=None):
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = json_data or {}
    response.text = text
    response.headers = headers or {"content-type": "text/plain"}
    response.raise_for_status = MagicMock()
    if status_code >= 400:
        response.raise_for_status.side_effect = Exception(f"status {status_code}")
    return response


def _mock_client(*, get_response=None, post_response=None, get_side_effect=None):
    """Patches discovery.httpx.AsyncClient so ``async with ... as client``
    yields a client whose .get/.post return the given canned response(s).
    ``discovery`` and ``search_result_enrichment`` both do a plain
    ``import httpx``, so this is the exact same ``httpx.AsyncClient``
    attribute either module would see -- one patch covers calls from both
    (see ``test_search_web_license_enriches_a_github_result``, which needs
    two different responses across different URLs through this one shared
    mock, via ``get_side_effect`` instead of a fixed ``get_response``)."""
    client = AsyncMock()
    if get_side_effect is not None:
        client.get = AsyncMock(side_effect=get_side_effect)
    elif get_response is not None:
        client.get = AsyncMock(return_value=get_response)
    if post_response is not None:
        client.post = AsyncMock(return_value=post_response)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=client)
    cm.__aexit__ = AsyncMock(return_value=False)
    return patch.object(discovery.httpx, "AsyncClient", return_value=cm), client


# --- search_store_plugins ---


@pytest.mark.asyncio
async def test_search_store_plugins_scores_by_keyword_overlap(monkeypatch):
    async def fake_list_store_plugins(nucore_interface, args):
        return {
            "plugins": [
                {"nsid": "1", "name": "Pool Controller", "description": "control your pool pump and heater"},
                {"nsid": "2", "name": "Light Switch", "description": "turn lights on and off"},
            ]
        }

    monkeypatch.setattr(discovery.plugin_management, "list_store_plugins", fake_list_store_plugins)
    ledger = EvidenceLedger()

    result = await discovery.search_store_plugins(
        SimpleNamespace(), {"query": "pool pump controller"}, ledger=ledger
    )

    assert result["recommend_and_stop"] is True
    assert result["candidates"][0]["nsid"] == "1"
    assert ledger.store_checked is True


@pytest.mark.asyncio
async def test_search_store_plugins_no_match_does_not_recommend(monkeypatch):
    async def fake_list_store_plugins(nucore_interface, args):
        return {"plugins": [{"nsid": "2", "name": "Light Switch", "description": "turn lights on and off"}]}

    monkeypatch.setattr(discovery.plugin_management, "list_store_plugins", fake_list_store_plugins)
    ledger = EvidenceLedger()

    result = await discovery.search_store_plugins(SimpleNamespace(), {"query": "pool pump"}, ledger=ledger)

    assert result["candidates"] == []
    assert result["recommend_and_stop"] is False
    assert ledger.store_checked is True


@pytest.mark.asyncio
async def test_search_store_plugins_requires_query():
    result = await discovery.search_store_plugins(SimpleNamespace(), {}, ledger=EvidenceLedger())
    assert "error" in result


# --- search_github_plugins ---


@pytest.mark.asyncio
async def test_search_github_plugins_vets_license_and_records_sources():
    response = _fake_response(
        json_data={
            "items": [
                {
                    "full_name": "acme/pool-plugin",
                    "html_url": "https://github.com/acme/pool-plugin",
                    "description": "pool controller plugin",
                    "updated_at": "2026-01-01T00:00:00Z",
                    "license": {"spdx_id": "MIT"},
                },
                {
                    "full_name": "other/pool-plugin",
                    "html_url": "https://github.com/other/pool-plugin",
                    "description": "another one",
                    "updated_at": "2026-01-01T00:00:00Z",
                    "license": {"spdx_id": "GPL-3.0"},
                },
            ]
        }
    )
    patcher, client = _mock_client(get_response=response)
    ledger = EvidenceLedger()

    with patcher:
        result = await discovery.search_github_plugins(
            SimpleNamespace(), {"query": "pool controller"}, ledger=ledger, secret_values=[]
        )

    repos = result["repositories"]
    assert repos[0]["license_ok"] is True
    assert repos[1]["license_ok"] is False
    assert ledger.github_tried is True
    assert len(ledger.sources) == 2


@pytest.mark.asyncio
async def test_search_github_plugins_enforces_query_cap():
    response = _fake_response(json_data={"items": []})
    patcher, client = _mock_client(get_response=response)
    ledger = EvidenceLedger()

    with patcher:
        for _ in range(discovery.MAX_GITHUB_QUERIES):
            result = await discovery.search_github_plugins(
                SimpleNamespace(), {"query": "pool controller"}, ledger=ledger, secret_values=[]
            )
            assert "error" not in result

        over_cap = await discovery.search_github_plugins(
            SimpleNamespace(), {"query": "pool controller"}, ledger=ledger, secret_values=[]
        )
    assert "error" in over_cap
    assert client.get.call_count == discovery.MAX_GITHUB_QUERIES  # the capped call never fired


@pytest.mark.asyncio
async def test_search_github_plugins_refuses_query_containing_a_secret():
    patcher, client = _mock_client(get_response=_fake_response(json_data={"items": []}))
    ledger = EvidenceLedger()

    with patcher:
        result = await discovery.search_github_plugins(
            SimpleNamespace(),
            {"query": "pool controller sk-secret123"},
            ledger=ledger,
            secret_values=["sk-secret123"],
        )

    assert "error" in result
    assert ledger.github_tried is False
    client.get.assert_not_called()


# --- search_web ---


@pytest.mark.asyncio
async def test_search_web_succeeds_without_github_tried_first():
    # The old "search_github_plugins must run before search_web" gate is
    # gone (see evidence_ledger.py's module docstring) -- a fresh ledger
    # with github_tried still False must not block this call.
    response = _fake_response(
        json_data={"web": {"results": [{"title": "Acme API", "url": "https://acme.example/api", "description": "docs"}]}}
    )
    patcher, client = _mock_client(get_response=response)
    ledger = EvidenceLedger(web_search_available=True)
    assert ledger.github_tried is False

    with patcher:
        result = await discovery.search_web(
            SimpleNamespace(),
            {"query": "acme api docs"},
            ledger=ledger,
            search_engine="brave",
            search_engine_api_key="key",
            secret_values=[],
        )

    assert "error" not in result
    assert result["results"][0]["url"] == "https://acme.example/api"


@pytest.mark.asyncio
async def test_search_web_license_enriches_a_github_result():
    web_response = _fake_response(
        json_data={
            "web": {
                "results": [
                    {"title": "acme/pool-plugin", "url": "https://github.com/acme/pool-plugin", "description": "a plugin"}
                ]
            }
        }
    )
    github_response = _fake_response(json_data={"license": {"spdx_id": "MIT"}})

    async def dispatch_by_url(url, **kwargs):
        return github_response if url.startswith("https://api.github.com/repos/") else web_response

    patcher, client = _mock_client(get_side_effect=dispatch_by_url)
    ledger = EvidenceLedger(web_search_available=True)

    with patcher:
        result = await discovery.search_web(
            SimpleNamespace(),
            {"query": "acme api docs"},
            ledger=ledger,
            search_engine="brave",
            search_engine_api_key="key",
            secret_values=[],
        )

    assert result["results"][0]["license"] == "MIT"
    assert result["results"][0]["license_ok"] is True
    assert ledger.license_lookup_count == 1
    assert client.get.call_count == 2


@pytest.mark.asyncio
async def test_search_web_brave_normalizes_results():
    response = _fake_response(
        json_data={"web": {"results": [{"title": "Acme API", "url": "https://acme.example/api", "description": "docs"}]}}
    )
    patcher, client = _mock_client(get_response=response)
    ledger = EvidenceLedger(web_search_available=True)

    with patcher:
        result = await discovery.search_web(
            SimpleNamespace(),
            {"query": "acme api docs"},
            ledger=ledger,
            search_engine="brave",
            search_engine_api_key="key",
            secret_values=[],
        )

    assert result["results"][0]["url"] == "https://acme.example/api"
    assert ledger.web_search_tried is True


@pytest.mark.asyncio
async def test_search_web_tavily_normalizes_results():
    response = _fake_response(
        json_data={"results": [{"title": "Acme API", "url": "https://acme.example/api", "content": "docs"}]}
    )
    patcher, client = _mock_client(post_response=response)
    ledger = EvidenceLedger(web_search_available=True)

    with patcher:
        result = await discovery.search_web(
            SimpleNamespace(),
            {"query": "acme api docs"},
            ledger=ledger,
            search_engine="tavily",
            search_engine_api_key="key",
            secret_values=[],
        )

    assert result["results"][0]["url"] == "https://acme.example/api"
    assert ledger.web_search_tried is True


@pytest.mark.asyncio
async def test_search_web_refuses_query_containing_a_secret():
    patcher, client = _mock_client(get_response=_fake_response(json_data={"web": {"results": []}}))
    ledger = EvidenceLedger(web_search_available=True)

    with patcher:
        result = await discovery.search_web(
            SimpleNamespace(),
            {"query": "acme sk-secret123"},
            ledger=ledger,
            search_engine="brave",
            search_engine_api_key="key",
            secret_values=["sk-secret123"],
        )

    assert "error" in result
    client.get.assert_not_called()


@pytest.mark.asyncio
async def test_search_web_enforces_query_cap():
    response = _fake_response(json_data={"web": {"results": []}})
    patcher, client = _mock_client(get_response=response)
    ledger = EvidenceLedger(web_search_available=True)

    with patcher:
        for _ in range(discovery.MAX_WEB_SEARCH_QUERIES):
            result = await discovery.search_web(
                SimpleNamespace(),
                {"query": "acme api docs"},
                ledger=ledger,
                search_engine="brave",
                search_engine_api_key="key",
                secret_values=[],
            )
            assert "error" not in result

        over_cap = await discovery.search_web(
            SimpleNamespace(),
            {"query": "acme api docs"},
            ledger=ledger,
            search_engine="brave",
            search_engine_api_key="key",
            secret_values=[],
        )
    assert "error" in over_cap


# --- fetch_reference ---


@pytest.mark.asyncio
async def test_fetch_reference_github_tier_requires_no_gate():
    response = _fake_response(text="hello world", headers={"content-type": "text/plain"})
    patcher, client = _mock_client(get_response=response)
    ledger = EvidenceLedger()

    with patcher:
        result = await discovery.fetch_reference(
            SimpleNamespace(),
            {"url": "https://github.com/acme/repo", "source_tier": "github"},
            ledger=ledger,
            secret_values=[],
        )

    assert result["content"] == "hello world"
    assert ledger.has_evidence() is True


@pytest.mark.asyncio
async def test_fetch_reference_user_url_requires_web_search_tried_first():
    ledger = EvidenceLedger(web_search_available=True)  # github/web_search not tried yet
    result = await discovery.fetch_reference(
        SimpleNamespace(),
        {"url": "https://example.com/docs", "source_tier": "user_url"},
        ledger=ledger,
        secret_values=[],
    )
    assert "error" in result


@pytest.mark.asyncio
async def test_fetch_reference_user_url_allowed_once_web_search_tried():
    response = _fake_response(text="docs", headers={"content-type": "text/html"})
    patcher, client = _mock_client(get_response=response)
    ledger = EvidenceLedger(web_search_available=True)
    ledger.web_search_tried = True

    with patcher:
        result = await discovery.fetch_reference(
            SimpleNamespace(),
            {"url": "https://example.com/docs", "source_tier": "user_url"},
            ledger=ledger,
            secret_values=[],
        )

    assert result["content"] == "docs"


@pytest.mark.asyncio
async def test_fetch_reference_rejects_non_text_content_type():
    response = _fake_response(text="binarydata", headers={"content-type": "image/png"})
    patcher, client = _mock_client(get_response=response)
    ledger = EvidenceLedger()

    with patcher:
        result = await discovery.fetch_reference(
            SimpleNamespace(),
            {"url": "https://example.com/image.png", "source_tier": "github"},
            ledger=ledger,
            secret_values=[],
        )

    assert "error" in result


@pytest.mark.asyncio
async def test_fetch_reference_truncates_large_bodies():
    big_body = "x" * (discovery.FETCH_BODY_CHAR_CAP + 100)
    response = _fake_response(text=big_body, headers={"content-type": "text/plain"})
    patcher, client = _mock_client(get_response=response)
    ledger = EvidenceLedger()

    with patcher:
        result = await discovery.fetch_reference(
            SimpleNamespace(),
            {"url": "https://example.com/big", "source_tier": "github"},
            ledger=ledger,
            secret_values=[],
        )

    assert result["truncated"] is True
    assert len(result["content"]) == discovery.FETCH_BODY_CHAR_CAP


@pytest.mark.asyncio
async def test_fetch_reference_enforces_fetch_cap():
    response = _fake_response(text="ok", headers={"content-type": "text/plain"})
    patcher, client = _mock_client(get_response=response)
    ledger = EvidenceLedger()

    with patcher:
        for _ in range(discovery.MAX_FETCHES):
            result = await discovery.fetch_reference(
                SimpleNamespace(),
                {"url": "https://example.com/doc", "source_tier": "github"},
                ledger=ledger,
                secret_values=[],
            )
            assert "error" not in result

        over_cap = await discovery.fetch_reference(
            SimpleNamespace(),
            {"url": "https://example.com/doc", "source_tier": "github"},
            ledger=ledger,
            secret_values=[],
        )
    assert "error" in over_cap


@pytest.mark.asyncio
async def test_fetch_reference_refuses_url_containing_a_secret():
    patcher, client = _mock_client(get_response=_fake_response(text="ok"))
    ledger = EvidenceLedger()

    with patcher:
        result = await discovery.fetch_reference(
            SimpleNamespace(),
            {"url": "https://example.com/?token=sk-secret123", "source_tier": "github"},
            ledger=ledger,
            secret_values=["sk-secret123"],
        )

    assert "error" in result
    client.get.assert_not_called()


@pytest.mark.asyncio
async def test_fetch_reference_rejects_invalid_source_tier():
    result = await discovery.fetch_reference(
        SimpleNamespace(),
        {"url": "https://example.com", "source_tier": "not_a_real_tier"},
        ledger=EvidenceLedger(),
        secret_values=[],
    )
    assert "error" in result

"""mdns_search (handlers/mdns.py) -- argument validation and bounding; the
mDNS browsing itself is covered in test_mdns_client.py."""

from __future__ import annotations

import pytest

from unified.plugin_authoring import mdns_client
from unified.plugin_authoring.handlers import mdns
from unified.plugin_authoring.handlers.mdns import mdns_search


@pytest.fixture
def browse_calls(monkeypatch):
    calls = []

    def fake_browse(service_types, timeout, **_kwargs):
        calls.append((service_types, timeout))
        return {"results": [], "count": 0, "window_seconds": timeout}

    monkeypatch.setattr(mdns_client, "browse", fake_browse)
    return calls


@pytest.mark.asyncio
async def test_no_service_types_browses_everything_with_the_longer_default_timeout(browse_calls):
    result = await mdns_search(None, {})
    assert "error" not in result
    assert browse_calls == [(None, mdns.DEFAULT_BROWSE_ALL_TIMEOUT_SECONDS)]


@pytest.mark.asyncio
async def test_specific_service_types_use_the_shorter_default_timeout(browse_calls):
    await mdns_search(None, {"service_types": ["_ipp._tcp"]})
    assert browse_calls == [(["_ipp._tcp.local"], mdns.DEFAULT_TIMEOUT_SECONDS)]


@pytest.mark.asyncio
async def test_empty_list_is_the_same_as_omitted(browse_calls):
    await mdns_search(None, {"service_types": []})
    assert browse_calls == [(None, mdns.DEFAULT_BROWSE_ALL_TIMEOUT_SECONDS)]


@pytest.mark.asyncio
async def test_service_types_get_a_local_suffix_and_are_deduplicated(browse_calls):
    await mdns_search(None, {"service_types": [" _ipp._tcp ", "_ipp._tcp.local", "_HAP._tcp.", "_x._sub._ipp._tcp"]})
    assert browse_calls[0][0] == ["_ipp._tcp.local", "_HAP._tcp.local", "_x._sub._ipp._tcp.local"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad",
    ["", "  ", 5, "ipp", "_ipp", "_ipp._icmp", "ipp._tcp", "_ip p._tcp", "_ipp._tcp/../x"],
)
async def test_invalid_service_type_is_rejected_without_browsing(browse_calls, bad):
    result = await mdns_search(None, {"service_types": [bad]})
    assert "error" in result
    assert browse_calls == []


@pytest.mark.asyncio
async def test_service_types_must_be_a_list_and_are_capped(browse_calls):
    assert "error" in await mdns_search(None, {"service_types": "_ipp._tcp"})
    too_many = [f"_s{i}._tcp" for i in range(mdns.MAX_SERVICE_TYPES + 1)]
    assert "error" in await mdns_search(None, {"service_types": too_many})
    assert browse_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("given, expected", [(0, 1.0), (0.2, 1.0), (6, 6.0), (500, 20.0), (7.5, 7.5)])
async def test_timeout_is_clamped(browse_calls, given, expected):
    await mdns_search(None, {"timeout_seconds": given})
    assert browse_calls[0][1] == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["5", True, [1]])
async def test_non_numeric_timeout_is_rejected(browse_calls, bad):
    result = await mdns_search(None, {"timeout_seconds": bad})
    assert "error" in result
    assert browse_calls == []


@pytest.mark.asyncio
async def test_unexpected_failure_never_raises(monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("socket exploded")

    monkeypatch.setattr(mdns_client, "browse", boom)
    result = await mdns_search(None, {})
    assert "socket exploded" in result["error"]


@pytest.mark.asyncio
async def test_browse_result_is_returned_as_is(monkeypatch):
    payload = {"results": [{"instance": "x"}], "count": 1, "window_seconds": 6.0}
    monkeypatch.setattr(mdns_client, "browse", lambda *_a, **_k: payload)
    assert await mdns_search(None, {}) == payload

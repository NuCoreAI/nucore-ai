"""_get_all_plm_links's PLM-links cache -- a full PLM link scan is slow, real
hardware I/O, so a recent-enough result is served from disk instead of
re-scanning every call. A cached file is only valid when it's BOTH young
enough (_PLM_LINKS_CACHE_MAX_AGE_S) AND large enough
(_PLM_LINKS_CACHE_MIN_SIZE_BYTES) -- the size check guards against treating a
truncated/corrupted partial write as a usable cache, which age alone can't
catch.
"""

from __future__ import annotations

import os
import time

import pytest

from iox.diagnostics.insteon_diag import (
    INSTEONDiagnostics,
    _PLM_LINKS_CACHE_MIN_SIZE_BYTES,
    _is_cache_fresh,
)


def _write(path, size_bytes: int) -> None:
    with open(path, "w") as f:
        f.write("x" * size_bytes)


# ---------------------------------------------------------------------------
# _is_cache_fresh -- direct unit tests
# ---------------------------------------------------------------------------

def test_cache_missing_file_is_not_fresh(tmp_path):
    assert _is_cache_fresh(str(tmp_path / "does_not_exist.txt")) is False


def test_cache_young_and_large_enough_is_fresh(tmp_path):
    path = tmp_path / "plm.txt"
    _write(path, _PLM_LINKS_CACHE_MIN_SIZE_BYTES + 1)
    assert _is_cache_fresh(str(path)) is True


def test_cache_too_small_is_not_fresh_even_if_young(tmp_path):
    path = tmp_path / "plm.txt"
    _write(path, _PLM_LINKS_CACHE_MIN_SIZE_BYTES - 1)
    assert _is_cache_fresh(str(path)) is False


def test_cache_exactly_at_min_size_is_fresh(tmp_path):
    path = tmp_path / "plm.txt"
    _write(path, _PLM_LINKS_CACHE_MIN_SIZE_BYTES)
    assert _is_cache_fresh(str(path)) is True


def test_cache_large_enough_but_too_old_is_not_fresh(tmp_path):
    path = tmp_path / "plm.txt"
    _write(path, _PLM_LINKS_CACHE_MIN_SIZE_BYTES + 1)
    old = time.time() - 7200
    os.utime(path, (old, old))
    assert _is_cache_fresh(str(path)) is False


# ---------------------------------------------------------------------------
# _get_all_plm_links -- integration through the real cache-decision code path
# ---------------------------------------------------------------------------

def _bare_insteon_diag() -> INSTEONDiagnostics:
    diag = object.__new__(INSTEONDiagnostics)
    diag._is_running = False
    diag._file_path = None
    diag._plm_address = None
    diag._plm_connected = False
    diag._refresh_plm_links = False
    return diag


@pytest.mark.asyncio
async def test_get_all_plm_links_ignores_a_small_fresh_cache_file(tmp_path, monkeypatch):
    diag = _bare_insteon_diag()

    async def fake_get_plm_info():
        return True, "AAAAA3 / Connected"
    diag._get_plm_info = fake_get_plm_info

    cache_path = tmp_path / "plm_links_table_all.txt"
    monkeypatch.setattr(diag, "_get_file_path", lambda *a, **kw: str(cache_path))
    _write(cache_path, _PLM_LINKS_CACHE_MIN_SIZE_BYTES - 1)  # too small, even though fresh

    fetch_calls = []

    class FakeWrapper:
        async def _send_device_specific_with_option(self, command, node, option, flag, specs):
            fetch_calls.append(1)
            return "ack"

    diag._iox_wrapper = FakeWrapper()

    async def fake_read(path):
        return "some content"
    diag._read_from_file = fake_read

    await diag._get_all_plm_links()

    assert len(fetch_calls) == 1, "a too-small cache file must not be served -- a live scan should run instead"


@pytest.mark.asyncio
async def test_get_all_plm_links_serves_a_large_fresh_cache_file(tmp_path, monkeypatch):
    diag = _bare_insteon_diag()

    async def fake_get_plm_info():
        return True, "AAAAA3 / Connected"
    diag._get_plm_info = fake_get_plm_info

    cache_path = tmp_path / "plm_links_table_all.txt"
    monkeypatch.setattr(diag, "_get_file_path", lambda *a, **kw: str(cache_path))
    _write(cache_path, _PLM_LINKS_CACHE_MIN_SIZE_BYTES + 1)

    fetch_calls = []

    class FakeWrapper:
        async def _send_device_specific_with_option(self, command, node, option, flag, specs):
            fetch_calls.append(1)
            return "ack"

    diag._iox_wrapper = FakeWrapper()

    result = await diag._get_all_plm_links()

    assert len(fetch_calls) == 0, "a valid (young + large enough) cache file should be served without a live scan"
    assert result == "x" * (_PLM_LINKS_CACHE_MIN_SIZE_BYTES + 1)

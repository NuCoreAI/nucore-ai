"""validate_profile/lookup_uom -- local, hub-free authoring aids. Both take
nucore_interface only for dispatch-signature uniformity (the same precedent
handlers/shell.py's run_shell_command sets) and never call it, so every
test here passes None.
"""

from __future__ import annotations

import pytest

from unified.dev_tools.handlers.profile_authoring import lookup_uom, validate_profile

VALID_PROFILE = {
    "families": [
        {
            "id": "fam1",
            "name": "Test Family",
            "instances": [
                {
                    "id": "inst1",
                    "name": "Test Instance",
                    "editors": [{"id": "ED_ONOFF", "ranges": [{"uom": "25", "subset": "0,1"}]}],
                    "nodedefs": [
                        {
                            "id": "ND_SWITCH",
                            "name": "Switch",
                            "properties": [{"id": "ST", "editor": "ED_ONOFF"}],
                            "cmds": {"sends": [], "accepts": [{"id": "DON"}]},
                        }
                    ],
                }
            ],
        }
    ]
}

PROFILE_WITH_DANGLING_EDITOR = {
    "families": [
        {
            "id": "fam1",
            "instances": [
                {
                    "id": "inst1",
                    "name": "Bad",
                    "nodedefs": [
                        {"id": "ND_X", "properties": [{"id": "ST", "editor": "MISSING_EDITOR"}], "cmds": {}}
                    ],
                }
            ],
        }
    ]
}


@pytest.mark.asyncio
async def test_validate_profile_accepts_well_formed_profile():
    result = await validate_profile(None, {"profile": VALID_PROFILE})
    assert result == {"valid": True, "errors": []}


@pytest.mark.asyncio
async def test_validate_profile_reports_dangling_editor_reference():
    result = await validate_profile(None, {"profile": PROFILE_WITH_DANGLING_EDITOR})
    assert result["valid"] is False
    assert any("MISSING_EDITOR" in e for e in result["errors"])


@pytest.mark.asyncio
async def test_validate_profile_rejects_non_dict_profile():
    result = await validate_profile(None, {"profile": "not a dict"})
    assert result == {"valid": False, "errors": ["'profile' must be a JSON object"]}


@pytest.mark.asyncio
async def test_lookup_uom_matches_by_category():
    result = await lookup_uom(None, {"keyword": "temperature"})
    ids = {m["id"] for m in result["matches"]}
    assert "4" in ids  # Celsius


@pytest.mark.asyncio
async def test_lookup_uom_no_match_returns_empty_list():
    result = await lookup_uom(None, {"keyword": "not_a_real_unit_xyz"})
    assert result == {"matches": []}


@pytest.mark.asyncio
async def test_lookup_uom_requires_keyword():
    result = await lookup_uom(None, {})
    assert result == {"error": "keyword is required"}

"""validate_profile/lookup_uom -- local, hub-free authoring aids. Both take
nucore_interface only for dispatch-signature uniformity (the same precedent
handlers/shell.py's run_shell_command sets) and never call it, so every
test here passes None.
"""

from __future__ import annotations

import pytest

from unified.plugin_authoring.handlers.profile_authoring import lookup_uom, validate_profile

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


# --- UOM cross-check (_check_uom_consistency): the model guessing a uom
# from memory instead of calling lookup_uom, as happened for real with uom
# 25/"Enum" on a plain counter, must fail validation mechanically rather
# than depend on the model remembering to verify it ---


@pytest.mark.asyncio
async def test_validate_profile_rejects_unknown_uom_id():
    profile = {
        "families": [{
            "id": "fam1",
            "instances": [{
                "id": "inst1",
                "name": "Test Instance",
                "editors": [{"id": "ED_X", "ranges": [{"min": 0, "max": 100, "uom": "99999"}]}],
                "nodedefs": [{"id": "ND_X", "properties": [{"id": "ST", "editor": "ED_X"}], "cmds": {}}],
            }],
        }]
    }
    result = await validate_profile(None, {"profile": profile})
    assert result["valid"] is False
    assert any("not a recognized UOM id" in e for e in result["errors"])


@pytest.mark.asyncio
async def test_validate_profile_rejects_enum_uom_with_min_max_range():
    # The real bug: uom 25 ("Enum" -- an index into an enumerated list, per
    # nucore.uom.is_enumeration_uom) paired with a min/max range instead of
    # a subset -- exists in the UOM table, so nucore's own parser accepts
    # it silently; only this cross-check catches the mismatch.
    profile = {
        "families": [{
            "id": "fam1",
            "instances": [{
                "id": "inst1",
                "name": "Test Instance",
                "editors": [{"id": "counter_editor", "ranges": [{"min": 0, "max": 999999, "uom": "25"}]}],
                "nodedefs": [{"id": "ND_X", "properties": [{"id": "count", "editor": "counter_editor"}], "cmds": {}}],
            }],
        }]
    }
    result = await validate_profile(None, {"profile": profile})
    assert result["valid"] is False
    assert any("must use a 'subset' range" in e for e in result["errors"])


@pytest.mark.asyncio
async def test_validate_profile_accepts_enum_uom_with_subset_range():
    # Same uom (25/"Enum"), correctly paired with subset this time -- must
    # not be flagged (VALID_PROFILE above already covers this, but this one
    # asserts it explicitly as its own regression case).
    profile = {
        "families": [{
            "id": "fam1",
            "instances": [{
                "id": "inst1",
                "name": "Test Instance",
                "editors": [{"id": "ED_ONOFF", "ranges": [{"uom": "25", "subset": "0,1"}]}],
                "nodedefs": [{"id": "ND_X", "properties": [{"id": "ST", "editor": "ED_ONOFF"}], "cmds": {}}],
            }],
        }]
    }
    result = await validate_profile(None, {"profile": profile})
    assert result == {"valid": True, "errors": []}


# --- malformed catalog documents must report a clear validation error,
# never leak a raw KeyError as "unexpected error: '<field>'" (regression:
# a model-authored profile omitted instance.name entirely) ---


@pytest.mark.asyncio
async def test_validate_profile_reports_instance_missing_name_clearly():
    profile = {
        "families": [{
            "id": "fam1",
            "instances": [{
                "id": "inst1",
                "nodedefs": [{"id": "ND_X", "properties": [{"id": "ST", "editor": "ED_ONOFF"}], "cmds": {}}],
                "editors": [{"id": "ED_ONOFF", "ranges": [{"uom": "25", "subset": "0,1"}]}],
            }],
        }]
    }
    result = await validate_profile(None, {"profile": profile})
    assert result["valid"] is False
    assert any("'name'" in e for e in result["errors"])
    assert not any("unexpected error" in e for e in result["errors"])


@pytest.mark.asyncio
async def test_validate_profile_reports_instance_missing_id_clearly():
    profile = {"families": [{"id": "fam1", "instances": [{"name": "Test Instance"}]}]}
    result = await validate_profile(None, {"profile": profile})
    assert result["valid"] is False
    assert any("'id'" in e for e in result["errors"])
    assert not any("unexpected error" in e for e in result["errors"])


@pytest.mark.asyncio
async def test_validate_profile_reports_nodedef_missing_id_clearly():
    profile = {
        "families": [{
            "id": "fam1",
            "instances": [{
                "id": "inst1",
                "name": "Test Instance",
                "nodedefs": [{"properties": [{"id": "ST", "editor": "ED_ONOFF"}], "cmds": {}}],
            }],
        }]
    }
    result = await validate_profile(None, {"profile": profile})
    assert result["valid"] is False
    assert any("'id'" in e for e in result["errors"])
    assert not any("unexpected error" in e for e in result["errors"])


@pytest.mark.asyncio
async def test_validate_profile_reports_property_missing_editor_clearly():
    profile = {
        "families": [{
            "id": "fam1",
            "instances": [{
                "id": "inst1",
                "name": "Test Instance",
                "nodedefs": [{"id": "ND_X", "properties": [{"id": "ST"}], "cmds": {}}],
            }],
        }]
    }
    result = await validate_profile(None, {"profile": profile})
    assert result["valid"] is False
    assert any("'editor'" in e for e in result["errors"])
    assert not any("unexpected error" in e for e in result["errors"])


@pytest.mark.asyncio
async def test_validate_profile_reports_command_missing_id_clearly():
    profile = {
        "families": [{
            "id": "fam1",
            "instances": [{
                "id": "inst1",
                "name": "Test Instance",
                "nodedefs": [{"id": "ND_X", "properties": [], "cmds": {"sends": [], "accepts": [{"native": "true"}]}}],
            }],
        }]
    }
    result = await validate_profile(None, {"profile": profile})
    assert result["valid"] is False
    assert any("'id'" in e for e in result["errors"])
    assert not any("unexpected error" in e for e in result["errors"])


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

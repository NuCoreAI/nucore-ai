"""validate_profile/lookup_uom/lookup_property_id -- local, hub-free
authoring aids. All three take nucore_interface only for dispatch-signature
uniformity (the same precedent handlers/shell.py's run_shell_command sets)
and never call it, so every test here passes None.
"""

from __future__ import annotations

import pytest

from unified.plugin_authoring.handlers.profile_authoring import (
    lookup_property_id,
    lookup_uom,
    validate_profile,
)
from unified.plugin_authoring.standard_property_ids import STANDARD_PROPERTY_IDS

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
                            "links": {"ctl": [], "rsp": []},
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
                "nodedefs": [
                    {
                        "id": "ND_X",
                        "properties": [{"id": "ST", "editor": "ED_ONOFF"}],
                        "cmds": {},
                        "links": {"ctl": [], "rsp": []},
                    }
                ],
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


# --- property id format cross-check (_check_property_id_format): a model
# guessing/inventing a property id that doesn't match the real hub's own
# constraint (all-caps letters/digits/underscore, starting with a letter,
# max 30 chars -- confirmed against ../iox-vscode-plugin/schemas/
# node.properties.schema.json) must fail validation mechanically ---


@pytest.mark.asyncio
async def test_validate_profile_rejects_lowercase_property_id():
    profile = {
        "families": [{
            "id": "fam1",
            "instances": [{
                "id": "inst1",
                "name": "Test Instance",
                "editors": [{"id": "ED_X", "ranges": [{"min": 0, "max": 100, "uom": "1"}]}],
                "nodedefs": [{"id": "ND_X", "properties": [{"id": "flowRate", "editor": "ED_X"}], "cmds": {}}],
            }],
        }]
    }
    result = await validate_profile(None, {"profile": profile})
    assert result["valid"] is False
    assert any("flowRate" in e and "must be all-caps" in e for e in result["errors"])


@pytest.mark.asyncio
async def test_validate_profile_rejects_too_long_property_id():
    too_long = "A" * 31
    profile = {
        "families": [{
            "id": "fam1",
            "instances": [{
                "id": "inst1",
                "name": "Test Instance",
                "editors": [{"id": "ED_X", "ranges": [{"min": 0, "max": 100, "uom": "1"}]}],
                "nodedefs": [{"id": "ND_X", "properties": [{"id": too_long, "editor": "ED_X"}], "cmds": {}}],
            }],
        }]
    }
    result = await validate_profile(None, {"profile": profile})
    assert result["valid"] is False
    assert any(too_long in e for e in result["errors"])


@pytest.mark.asyncio
async def test_validate_profile_accepts_meaningful_custom_property_id():
    # Not a standard id, but conforms to the format -- must be accepted
    # (this feature is about steering *which* id gets chosen, via
    # lookup_property_id/the system prompt, not about restricting the
    # format to only the standard catalogue).
    profile = {
        "families": [{
            "id": "fam1",
            "instances": [{
                "id": "inst1",
                "name": "Test Instance",
                "editors": [{"id": "ED_X", "ranges": [{"min": 0, "max": 100, "uom": "1"}]}],
                "nodedefs": [
                    {
                        "id": "ND_X",
                        "properties": [{"id": "FILTER_LIFE", "editor": "ED_X"}],
                        "cmds": {},
                        "links": {"ctl": [], "rsp": []},
                    }
                ],
            }],
        }]
    }
    result = await validate_profile(None, {"profile": profile})
    assert result == {"valid": True, "errors": []}


# --- links-object-present cross-check (_check_links_object_present): every
# nodedef must include a links object with ctl/rsp arrays, even when empty
# -- confirmed mandatory against a real generated profile.json. nucore's own
# parser leaves NodeDef.links as None with no error at all when the key is
# simply absent ---


@pytest.mark.asyncio
async def test_validate_profile_rejects_nodedef_missing_links():
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
    assert result["valid"] is False
    assert any("ND_X" in e and "missing 'links'" in e for e in result["errors"])


@pytest.mark.asyncio
async def test_validate_profile_rejects_nodedef_links_missing_ctl_or_rsp():
    profile = {
        "families": [{
            "id": "fam1",
            "instances": [{
                "id": "inst1",
                "name": "Test Instance",
                "editors": [{"id": "ED_ONOFF", "ranges": [{"uom": "25", "subset": "0,1"}]}],
                "nodedefs": [
                    {
                        "id": "ND_X",
                        "properties": [{"id": "ST", "editor": "ED_ONOFF"}],
                        "cmds": {},
                        "links": {"ctl": []},
                    }
                ],
            }],
        }]
    }
    result = await validate_profile(None, {"profile": profile})
    assert result["valid"] is False
    assert any("links.rsp" in e for e in result["errors"])


@pytest.mark.asyncio
async def test_validate_profile_accepts_nodedef_with_empty_links():
    profile = {
        "families": [{
            "id": "fam1",
            "instances": [{
                "id": "inst1",
                "name": "Test Instance",
                "editors": [{"id": "ED_ONOFF", "ranges": [{"uom": "25", "subset": "0,1"}]}],
                "nodedefs": [
                    {
                        "id": "ND_X",
                        "properties": [{"id": "ST", "editor": "ED_ONOFF"}],
                        "cmds": {},
                        "links": {"ctl": [], "rsp": []},
                    }
                ],
            }],
        }]
    }
    result = await validate_profile(None, {"profile": profile})
    assert result == {"valid": True, "errors": []}


# --- linkdef-reference cross-check (_check_linkdef_references): a nodedef's
# links.ctl/links.rsp must name a linkdef actually defined in that
# instance's linkdefs[] -- a dangling reference round-trips fine through
# nucore.profile locally ---


@pytest.mark.asyncio
async def test_validate_profile_rejects_dangling_linkdef_reference():
    profile = {
        "families": [{
            "id": "fam1",
            "instances": [{
                "id": "inst1",
                "name": "Test Instance",
                "editors": [{"id": "ED_ONOFF", "ranges": [{"uom": "25", "subset": "0,1"}]}],
                "linkdefs": [],
                "nodedefs": [
                    {
                        "id": "ND_X",
                        "properties": [{"id": "ST", "editor": "ED_ONOFF"}],
                        "cmds": {},
                        "links": {"ctl": ["I_STD"], "rsp": []},
                    }
                ],
            }],
        }]
    }
    result = await validate_profile(None, {"profile": profile})
    assert result["valid"] is False
    assert any("I_STD" in e and "not defined" in e for e in result["errors"])


@pytest.mark.asyncio
async def test_validate_profile_accepts_linkdef_reference_that_matches():
    profile = {
        "families": [{
            "id": "fam1",
            "instances": [{
                "id": "inst1",
                "name": "Test Instance",
                "editors": [{"id": "ED_ONOFF", "ranges": [{"uom": "25", "subset": "0,1"}]}],
                "linkdefs": [{"id": "I_STD", "protocol": "I_STD", "name": "Insteon"}],
                "nodedefs": [
                    {
                        "id": "ND_X",
                        "properties": [{"id": "ST", "editor": "ED_ONOFF"}],
                        "cmds": {},
                        "links": {"ctl": ["I_STD"], "rsp": []},
                    }
                ],
            }],
        }]
    }
    result = await validate_profile(None, {"profile": profile})
    assert result == {"valid": True, "errors": []}


# --- linkdef cmd/parameters cross-check (_check_linkdef_cmd_parameters): the
# official Dynamic Profiles docs (developer.isy.io/docs/API/pg/
# DynamicProfiles) say a cmd: true linkdef "must not specify any
# parameters" -- nucore.profile doesn't enforce this either ---


@pytest.mark.asyncio
async def test_validate_profile_rejects_cmd_true_linkdef_with_parameters():
    profile = {
        "families": [{
            "id": "fam1",
            "instances": [{
                "id": "inst1",
                "name": "Test Instance",
                "editors": [{"id": "ED_X", "ranges": [{"min": 0, "max": 100, "uom": "1"}]}],
                "linkdefs": [
                    {
                        "id": "ASSOC_CMD",
                        "protocol": "ASSOC_CMD",
                        "name": "Z-Wave Association Command",
                        "cmd": True,
                        "parameters": [{"id": "OL", "editor": "ED_X"}],
                    }
                ],
                "nodedefs": [],
            }],
        }]
    }
    result = await validate_profile(None, {"profile": profile})
    assert result["valid"] is False
    assert any("ASSOC_CMD" in e and "must not specify parameters" in e for e in result["errors"])


@pytest.mark.asyncio
async def test_validate_profile_accepts_cmd_true_linkdef_without_parameters():
    # The official doc's own example: a Z-Wave association command linkdef,
    # cmd: true, no parameters at all.
    profile = {
        "families": [{
            "id": "fam1",
            "instances": [{
                "id": "inst1",
                "name": "Test Instance",
                "linkdefs": [
                    {"id": "ASSOC_CMD", "protocol": "ASSOC_CMD", "name": "Z-Wave Association Command", "cmd": True}
                ],
                "nodedefs": [],
            }],
        }]
    }
    result = await validate_profile(None, {"profile": profile})
    assert result == {"valid": True, "errors": []}


@pytest.mark.asyncio
async def test_validate_profile_accepts_non_cmd_linkdef_with_parameters():
    profile = {
        "families": [{
            "id": "fam1",
            "instances": [{
                "id": "inst1",
                "name": "Test Instance",
                "editors": [{"id": "ED_X", "ranges": [{"min": 0, "max": 100, "uom": "1"}]}],
                "linkdefs": [
                    {
                        "id": "I_DIMMER",
                        "protocol": "I_STD",
                        "name": "Insteon",
                        "parameters": [{"id": "OL", "editor": "ED_X"}],
                    }
                ],
                "nodedefs": [],
            }],
        }]
    }
    result = await validate_profile(None, {"profile": profile})
    assert result == {"valid": True, "errors": []}


@pytest.mark.asyncio
async def test_lookup_property_id_matches_standard_id_by_keyword():
    result = await lookup_property_id(None, {"keyword": "temperature"})
    ids = {m["id"] for m in result["matches"]}
    assert "CLITEMP" in ids


@pytest.mark.asyncio
async def test_lookup_property_id_matches_by_id_substring():
    result = await lookup_property_id(None, {"keyword": "clihum"})
    ids = {m["id"] for m in result["matches"]}
    assert "CLIHUM" in ids


@pytest.mark.asyncio
async def test_lookup_property_id_no_match_returns_empty_list():
    result = await lookup_property_id(None, {"keyword": "not_a_real_property_xyz"})
    assert result == {"matches": []}


@pytest.mark.asyncio
async def test_lookup_property_id_requires_keyword():
    result = await lookup_property_id(None, {})
    assert result == {"error": "keyword is required"}


def test_standard_property_ids_never_includes_the_gv_fallback():
    # The whole point of this catalogue is to steer the model away from
    # defaulting to GV0/GV1/... -- it must never itself suggest one. (Not a
    # bare "GV" prefix check: GVOL/"Water Volume" legitimately starts with
    # those two letters and must stay in the catalogue.)
    import re

    gv_fallback = re.compile(r"^GV\d+$")
    assert not any(gv_fallback.match(property_id) for property_id in STANDARD_PROPERTY_IDS)

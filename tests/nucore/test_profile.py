"""Profile/LinkDef parsing -- focused unit tests against nucore.profile.Profile
directly, not through validate_profile, since validate_profile's own
{"valid", "errors"} return shape never echoes back parsed structure.
"""

from __future__ import annotations

from nucore import Profile


def test_load_from_json_parses_linkdef_and_parameter_desc():
    raw = {
        "families": [
            {
                "id": "fam1",
                "name": "Test Family",
                "instances": [
                    {
                        "id": "inst1",
                        "name": "Test Instance",
                        "editors": [{"id": "ED_X", "ranges": [{"min": 0, "max": 100, "uom": "1"}]}],
                        "linkdefs": [
                            {
                                "id": "I_DIMMER",
                                "protocol": "I_STD",
                                "name": "Insteon",
                                "desc": "Dimmer link for controllers without retries",
                                "parameters": [
                                    {
                                        "id": "OL",
                                        "editor": "ED_X",
                                        "name": "On Level",
                                        "desc": "The on-level percentage",
                                    }
                                ],
                            }
                        ],
                        "nodedefs": [],
                    }
                ],
            }
        ]
    }

    profile = Profile()
    profile.load_from_json(raw)

    linkdef = profile.families[0].instances[0].linkdefs[0]
    assert linkdef.desc == "Dimmer link for controllers without retries"
    assert linkdef.parameters["OL"].desc == "The on-level percentage"


def test_load_from_json_linkdef_desc_defaults_to_none_when_absent():
    raw = {
        "families": [
            {
                "id": "fam1",
                "name": "Test Family",
                "instances": [
                    {
                        "id": "inst1",
                        "name": "Test Instance",
                        "linkdefs": [{"id": "I_STD", "protocol": "I_STD", "name": "Insteon"}],
                        "nodedefs": [],
                    }
                ],
            }
        ]
    }

    profile = Profile()
    profile.load_from_json(raw)

    assert profile.families[0].instances[0].linkdefs[0].desc is None

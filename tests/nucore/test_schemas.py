"""Structural checks for src/nucore/schemas/ -- the JSON Schema restructuring of
iox-vscode-plugin's validation schemas onto the Dynamic Profiles shape (see
src/nucore/schemas/README.md). Purely structural (field names/types/required-ness);
cross-document referential integrity (e.g. an editor id referenced but never
defined anywhere in the document) is NOT checked by JSON Schema and stays
validate_profile's job (src/unified/dev_tools/handlers/profile_authoring.py).
"""

from __future__ import annotations

import glob
import json
import os

import pytest
from jsonschema import Draft202012Validator, RefResolver

from nucore.uom import PREDEFINED_UOMS

SCHEMAS_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "src", "nucore", "schemas")
)


def _schema_paths() -> list[str]:
    return sorted(glob.glob(os.path.join(SCHEMAS_DIR, "**", "*.json"), recursive=True))


def test_every_schema_file_is_valid_json():
    for path in _schema_paths():
        with open(path) as f:
            json.load(f)


def test_uom_enum_matches_predefined_uoms():
    """defs/uom.schema.json is generated from PREDEFINED_UOMS -- guards against drift
    if the UOM table changes without regenerating the schema (see schemas/README.md)."""
    with open(os.path.join(SCHEMAS_DIR, "defs", "uom.schema.json")) as f:
        uom_schema = json.load(f)
    assert set(uom_schema["enum"]) == set(PREDEFINED_UOMS.keys())


def _build_store() -> dict[str, dict]:
    """Map every schema file's bare filename to its parsed contents -- a RefResolver `store`.
    Every $ref in every schema file (top-level or defs/) is a bare filename, e.g. "range.schema.json",
    never "defs/range.schema.json": jsonschema<4.18's legacy RefResolver re-joins an
    already-resolved scope against the pre-push scope in push_scope() (`urljoin(resolution_scope,
    scope)`), and when both the current scope and the newly-resolved one share a "defs/" path
    prefix, that redundant re-join duplicates it (`urljoin("defs/editor.schema.json",
    "defs/range.schema.json")` == "defs/defs/range.schema.json") -- a real bug, not a typo; see
    src/nucore/schemas/README.md. A flat, directory-free namespace (bare filenames only, all
    unique across schemas/) sidesteps it entirely, since joining any base against a bare filename
    always just replaces the base outright."""
    store: dict[str, dict] = {}
    for path in _schema_paths():
        with open(path) as f:
            contents = json.load(f)
        store[os.path.basename(path)] = contents
    return store


@pytest.fixture(scope="module")
def store() -> dict[str, dict]:
    return _build_store()


def _validator_for(store: dict[str, dict], filename: str) -> Draft202012Validator:
    with open(os.path.join(SCHEMAS_DIR, filename)) as f:
        schema = json.load(f)
    resolver = RefResolver(base_uri=schema.get("$id", filename), referrer=schema, store=store)
    return Draft202012Validator(schema, resolver=resolver)


# Same fixture unified.dev_tools' validate_profile tool accepts today
# (tests/unified/dev_tools/test_profile_authoring.py's VALID_PROFILE) -- the two
# "views" of Dynamic Profiles the shared defs/ definitions serve should agree on
# this happy path.
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


def test_nucore_profile_catalog_accepts_validate_profile_fixture(store):
    validator = _validator_for(store, "nucore_profile_catalog.schema.json")
    assert list(validator.iter_errors(VALID_PROFILE)) == []


# design/developers/plugin_model.md §3's own worked Profile example (one NodeDef,
# two properties, three accepted commands).
DYNAMIC_PROFILE_UPDATE_EXAMPLE = {
    "editors": [
        {
            "id": "online",
            "ranges": [{"uom": "25", "subset": "0,1", "names": {"0": "Offline", "1": "Online"}}],
        },
        {
            "id": "test",
            "ranges": [
                {
                    "uom": "25",
                    "subset": "0-5",
                    "names": {
                        "0": "Not tested",
                        "1": "Testing...",
                        "2": "Success",
                        "3": "Timeout",
                        "4": "Failure",
                        "5": "Auth. Failure",
                    },
                }
            ],
        },
    ],
    "linkdefs": [],
    "nodedefs": [
        {
            "id": "CTL",
            "name": "Controller",
            "icon": "GenericCtl",
            "properties": [
                {"id": "ST", "editor": "online", "name": "Plugin Status"},
                {"id": "GV0", "editor": "test", "name": "Test result"},
            ],
            "cmds": {
                "sends": [],
                "accepts": [
                    {"id": "DISCOVER", "native": "false", "name": "Discover devices"},
                    {"id": "QUERYALL", "name": "Query All"},
                    {"id": "TEST", "name": "Test"},
                ],
            },
            "links": {"ctl": [], "rsp": []},
        }
    ],
}


def test_dynamic_profile_update_accepts_plugin_model_worked_example(store):
    validator = _validator_for(store, "dynamic_profile_update.schema.json")
    assert list(validator.iter_errors(DYNAMIC_PROFILE_UPDATE_EXAMPLE)) == []


def test_dynamic_profile_update_accepts_partial_update_payload(store):
    """updateJsonProfile()'s own delete example from plugin_model.md §2 -- a
    partial payload with only `delete` and empty add/replace arrays."""
    payload = {
        "delete": {
            "editors": ["I3_LOAD_4", "I3_ON_OFF"],
            "nodedefs": ["*"],
            "linkdefs": ["*"],
        },
        "editors": [],
        "nodedefs": [],
        "linkdefs": [],
    }
    validator = _validator_for(store, "dynamic_profile_update.schema.json")
    assert list(validator.iter_errors(payload)) == []


def test_linkdef_cmd_true_rejects_parameters(store):
    """plugin_model.md §3: 'If true, ... The linkdef must not specify any parameters.'"""
    bad_linkdef = {
        "id": "ASSOC_CMD",
        "protocol": "ASSOC_CMD",
        "name": "Z-Wave Association Command",
        "cmd": True,
        "parameters": [{"id": "x", "editor": "e"}],
    }
    payload = {"linkdefs": [bad_linkdef]}
    validator = _validator_for(store, "dynamic_profile_update.schema.json")
    assert list(validator.iter_errors(payload)) != []

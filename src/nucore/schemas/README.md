# `nucore` Dynamic Profiles JSON Schemas

JSON Schema (draft 2020-12) restructuring of [`iox-vscode-plugin`](https://github.com/universaldevices/iox-vscode-plugin)'s
`schemas/` directory onto this repo's own **Dynamic Profiles** object model
([`design/developers/plugin_model.md`](../../../design/developers/plugin_model.md) §3), per
[`design/developers/plugin_dev_tooling.md`](../../../design/developers/plugin_dev_tooling.md) §6
step 1's recommendation. Not copied as-is: `iox-vscode-plugin`'s schemas validate `ioxplugin`'s
older, different dialect (`plugin`/`editors`/`nodedefs` with inline `is_settable`, an
`idref`-capable editor shape, no `linkdefs` concept at all).

These are packaged data (`pyproject.toml`'s `[tool.setuptools.package-data]` `nucore` entry), not
Python-importable API -- `src/nucore/__init__.py`'s public surface (see
[`nucore_domain_model.md`](../../../design/developers/nucore_domain_model.md) §15) is unaffected.
Nothing in this repo currently loads or enforces these schemas at runtime; `validate_profile`
(`src/unified/dev_tools/handlers/profile_authoring.py`) still validates purely by running
`nucore.Profile().load_from_json(...)` and capturing its debug-log output, per its own design.
This directory exists for anyone hand-authoring or vetting a Dynamic Profiles document outside
that chat loop -- a plugin developer's own editor/CI, a future CLI, or the sibling `eisy-ai` repo.

## Two shapes, one set of shared definitions

Dynamic Profiles has two different top-level shapes in this codebase:

| Schema | Shape | Who actually uses it |
|---|---|---|
| [`dynamic_profile_update.schema.json`](dynamic_profile_update.schema.json) | Flat `{editors, nodedefs, linkdefs[, delete]}` | What a plugin's own backend sends/receives via `polyglot.getJsonProfile()`/`updateJsonProfile()` -- the wire format `plugin_model.md` §2-3 documents. |
| [`nucore_profile_catalog.schema.json`](nucore_profile_catalog.schema.json) | `families[] -> instances[] -> {editors, nodedefs, linkdefs}` | nucore-ai's own catalog-fetch wrapper (`GET /rest/profiles`) -- what `src/nucore/profile.py`'s `Profile.__parse_profile__` actually requires, and what `validate_profile`/its test fixtures exercise today. |

Both `$ref` into the same [`defs/`](defs/) object definitions (`NodeDef`, `Property`, `Cmd`,
`Parameter`, `Editor`, `Range`, `LinkDef`, plus `id`/`icon`/`uom` leaf definitions), so the two
views never drift on field shapes. Each `defs/*.schema.json` file's own `description` documents
which `iox-vscode-plugin` source file (if any) it was restructured from and which `plugin_model.md`
§3 subsection it matches -- see each file directly for that detail rather than duplicating it here.

**Resolution is a flat namespace, deliberately.** Every `$ref` in every file here -- both the two
top-level wrappers referencing into `defs/`, and `defs/*.schema.json` files referencing each other
-- is a bare filename (`"$ref": "range.schema.json"`), never a `"defs/..."`-prefixed path, and only
the two top-level files declare their own `"$id"` (needed as a validator's `base_uri`) -- files
under `defs/` deliberately don't. This works because every filename under `schemas/` is unique.
It isn't just a style choice: `jsonschema<4.18`'s legacy `RefResolver.push_scope()` re-joins an
already-resolved scope against the pre-push scope (`urljoin(resolution_scope, scope)`), and when
both the current scope and the newly-resolved one share a `"defs/"` path prefix, that redundant
re-join duplicates it (`urljoin("defs/editor.schema.json", "defs/range.schema.json")` ==
`"defs/defs/range.schema.json"`, a real, reproducible bug in that resolver, not a typo here) --
see `tests/nucore/test_schemas.py`'s `_build_store()` docstring for the concrete repro. A flat,
directory-free namespace sidesteps it entirely, since joining any base against a bare filename
always just replaces the base outright. If a future bump past `jsonschema>=4.18` (which resolves
`$ref`/`$id` via the `referencing` package instead) makes this moot, the flat-namespace convention
can be dropped -- it isn't otherwise load-bearing.

## Leniency policy: required fields follow the parser, not the spec table's formatting

`plugin_model.md`'s own field tables mark some fields `(Optional)` and leave others unmarked, but
that markup is not a reliable signal of what's actually required end to end -- e.g. `Cmd.native`
has no `(Optional)` marker, yet §3's own worked example includes it on only one of three sibling
commands. These schemas instead follow **what `src/nucore/profile.py`'s parser actually tolerates
being absent** (a direct `dict[...]` index access is required; a `.get(...)` call is optional),
matching [`nucore_domain_model.md`](../../../design/developers/nucore_domain_model.md) §14's
documented design choice: *"Parsing is lenient, not strict."* In practice this means only `id`
fields and editor-reference fields (`Property.editor`, `Parameter.editor`) are required almost
everywhere; `name`, `desc`, `cmds`, `links`, and similar are optional even where the spec table
doesn't mark them so. Each affected `defs/*.schema.json` file's `description` calls this out where
it diverges from a literal reading of the table. This also means `dynamic_profile_update.schema.json`
and `nucore_profile_catalog.schema.json` are validated against the **same**, lenient object
definitions -- not a strict wire-format contract in one and a lenient parser-shape in the other.

## What's out of scope

- **No `jsonschema` production dependency, no validator wired into `validate_profile`.** These
  files are structural references today; `jsonschema` is a dev/test-only dependency
  (`pyproject.toml`'s `dev` extra, `requirements-dev.txt`), used only by
  `tests/nucore/test_schemas.py`.
- **No cross-reference/referential-integrity checking.** JSON Schema validates structure (field
  names, types, required-ness) per-object; it cannot check that every `"editor": "<id>"` string
  actually matches an entry in the top-level `editors` array. That's `validate_profile`'s job
  (it runs the real `nucore` parser, which does resolve and report dangling references) and stays
  that way.
- **ioxplugin-only concerns were dropped, not restructured**: `plugin.schema.json`/
  `plugin.meta.schema.json` (node-server packaging/publish metadata), `protocol.schema.json`/
  `protocol.generic.schema.json`/`protocol.modbus.schema.json`/`serial.schema.json`/
  `tcp.schema.json`/`modbus.node*.schema.json` (transport/protocol declaration), and
  `schemas/old/` (superseded, and in one case literally invalid JSON Schema -- a lowercase
  `"oneof"` keyword typo). None of these have a Dynamic Profiles analog.

## Regenerating the UOM-derived files

[`defs/uom.schema.json`](defs/uom.schema.json) (the plain UOM id enum) and
[`defs/uom_named_states.schema.json`](defs/uom_named_states.schema.json) (per-UOM named-state
reference tables, e.g. Thermostat mode) are generated, not hand-maintained -- the former from
`nucore.uom.PREDEFINED_UOMS` (the single source of truth `lookup_uom` also reads), the latter from
the local `iox-vscode-plugin` clone's 15 per-UOM `uom.<id>.schema.json` files, reformatted from
`"<Label> | <value>"` display strings into `{value, label}` pairs matching the shape
`EditorMinMaxRange.names`/`EditorSubsetRange.names` (`src/nucore/editor.py`) actually hold.
`tests/nucore/test_schemas.py::test_uom_enum_matches_predefined_uoms` guards against the former
drifting out of sync; there is no equivalent automated guard for the latter (`uom_named_states` is
reference data, not enforced by any other schema -- see `range.schema.json`'s `names` field).

# Domain and plugin models — nucore-ai's internal model, and the Dynamic Profiles model a plugin submits

> Reference doc, not a proposal. Explains what's shipped today: how `src/nucore/` represents a
> hub's device catalog internally, and the Dynamic Profiles JSON shape a plugin's own backend
> authors and sends to PG3/IoX at runtime. Consolidated from `nucore_domain_model.md`,
> `plugin_model.md`, and §1 of `plugin_concepts_and_lifecycle.md` (original text archived under
> `legacy/`).

See also: [Design decisions and implementations](plugin_authoring_design.md) ·
[APIs](plugin_apis.md) · [Lifecycle and runtime](plugin_lifecycle_and_runtime.md)

## 1. Two shapes, reconciled up front

There are **two different top-level shapes** for the same underlying concepts (`nodedefs`,
`editors`, `linkdefs`), and most confusion between the model docs traces back to conflating
them:

- **The wire shape** — flat: `{editors[], nodedefs[], linkdefs[]}`. This is what a plugin's own
  backend actually exchanges with PG3/IoX via `polyglot.getJsonProfile()`/`updateJsonProfile()`
  (§3-4 below). Nothing about family or instance appears in it — a plugin has no way to specify
  either; both are assigned by the platform.
- **The catalog shape** — nested: `Family → Instance → {editors[], nodedefs[], linkdefs[]}`.
  This is nucore-ai's own internal model (§5-12 below), built by fetching `GET /rest/profiles`
  and parsing the result with `Profile.__parse_profile__`.

**Family/instance assignment, concretely**: every plugin-originated device is unconditionally
`family = "10"` (`DEVICE_FAMILY_PLUGIN`, a fixed constant at `src/iox/iox_definitions.py:78,86`
— confirmed, not inferred). `instance` is the install-time **slot** number the NuCore/IoX
platform itself assigns — usually one slot per plugin, but the platform supports installing
multiple instances of the same plugin side by side, each in its own slot. This assignment
happens hub/firmware-side; there is no "slot" concept anywhere in this repo's Python code
(confirmed by repo-wide grep) because nucore-ai never computes it — like every other protocol
family, it only ever reads back whatever `instance` value the hub already assigned. Every live
`Node` resolves its `NodeDef` via the composite key `"{node_def_id}.{family}.{instance}"`.

The two schema files under `src/nucore/schemas/` mirror this split exactly —
`dynamic_profile_update.schema.json` (wire) and `nucore_profile_catalog.schema.json`
(catalog) — see §16 below and that directory's own `README.md` for the full provenance.

## 2. nucore-ai's internal model

Two data sources merge into one live picture, per hub connection:

- **The profile catalog** (fetched once, `GET /rest/profiles`, or a local file) — the *types*:
  which `Family → Instance → NodeDef`s exist, and each `NodeDef`'s declared
  properties/commands/links and the value space (`Editor`) each one accepts.
- **The live node list** (fetched once, `GET /rest/nodes` XML + `GET /api/groups/links`) — the
  *instances*: each `Node`'s address, raw property values, and its
  `(node_def_id, family, instance)` triple.

```
Profile (catalog, fetched once)
  └─ Family (id, name)               e.g. "1" = INSTEON, "10" = Plugin, "12" = Z-Wave
       └─ Instance (id)              a numbered config bundle within a family
            ├─ editors[]             value-space definitions (Editor)
            ├─ linkdefs[]            valid scene/link records (LinkDef)
            └─ nodedefs[]            valid device types (NodeDef)
                   ├─ properties{}   schema per property id (NodeProperty → Editor)
                   ├─ cmds.accepts[] commands sendable TO the device (Command → Editor per param)
                   ├─ cmds.sends[]   commands the device itself emits
                   └─ links.ctl/rsp[] valid link roles (LinkDef)

Live nodes (fetched once, per installation)
  └─ Node (address, family, instance, node_def_id, properties{})
       └─ .node_def  ───────────────► resolved via "{node_def_id}.{family}.{instance}"
                                       lookup into the Profile catalog above
```

`Profile.map_nodes()` is the join: it resolves each live `Node`'s key against the catalog to
attach the right `NodeDef`, turning a bag of raw values into a fully-typed device. A miss is
non-fatal — logged at debug level, `node_def` stays `None`.

### Node and NodeBase

`Node` (`src/nucore/node.py:29`) is one physical/logical device instance, built directly from a
`<node>` XML element (`node.py:66-99`) — not JSON. `NodeBase` (`node_base.py:38`) is a shared ABC
dataclass base for `Node`, `Group`, and `Folder`: `flag`, `node_def_id`, `address`, `name`,
`family: int`, `instance: int`, `enabled`, `parent`, `parent_type`, `hint`,
`node_def: NodeDef | None`, flag-bit predicates, and `NodeTypes`/`NodeHierarchy` bit-flag
constants. `Node` adds device-only fields: `type`, `deviceClass`, `wattage`, `dcPeriod`,
`startDelay`/`endDelay`, `pnode`/`rpnode` (primary-node addressing for multi-instance devices —
see §9), `sgid`, `typeInfo`, and the live `properties: dict[str, Property]`.

`node_def` is a resolved lookup, not composition — `node_def_id` is parsed straight off the XML,
`node_def` itself stays `None` until `Profile.map_nodes` runs the lookup. `Node.__hash__` hashes
solely on `address`. Two load paths: hub XML (`GET /rest/nodes`) or a local file
(`Node.load_from_file`).

### NodeDef — the type a Node is an instance of

| Field | Type | Meaning |
|---|---|---|
| `id` | `str` | First component of the `(node_def_id, family, instance)` lookup key |
| `properties` | `dict[str, NodeProperty]` | Schema per property id — see §4 |
| `cmds` | `NodeCommands` | `.accepts` / `.sends` lists of `Command` — see §3 |
| `name` | `str` | Populated from legacy `nls` or current `name` (same field, see §13's G-1) |
| `desc` | `str` (Optional) | Added for parity with the spec; unused by any code today |
| `customicon` | — | Populated from legacy `icon` or current `customicon` (same field, see §13's G-1) |
| `links` | `NodeLinks` | `.ctl` / `.rsp` lists of `LinkDef` — see §4 |

Origin: parsed out of the profile catalog in `Profile.__parse_profile__`
(`profile.py:234-309`), sourced from `GET /rest/profiles` or `Profile.load_from_file` when a
local `profile_path` is supplied.

### Command (`cmd.py`)

A single named operation a node type supports: `id` (e.g. `"DON"`, `"DOF"`), optional `name`,
`native` (string `"true"`/`"false"`, not a JSON boolean — added for parity, confirmed unused;
see §13's G-2), `desc` (same — added for parity, unused), optional `format` (§6), and ordered
`parameters: list[CommandParameter]`, each carrying its own `Editor` plus `init`/`optional`.

**Accepts vs. sends is explicit and first-class.** `NodeDef.cmds: NodeCommands` splits into
`accepts: list[Command]` (sent *to* the device — `resolve_command_id(..., direction="accepts")`,
invoked at `POST/GET .../rest/nodes/{device_id}/cmd/{command_id}[/value[/uom]]`) and
`sends: list[Command]` (device-initiated events, surfaced separately in `Node.json()` as
`"sends.commands"` vs `"accepts.commands"`).

`resolve_property_id`/`resolve_command_id` do a **strict, scoped exact-name match** —
deliberately not fuzzy, because the same display name (e.g. "On Level") can exist as both a
property and a command on one device; namespaces are kept separate to avoid ambiguity rather
than guessed at.

A `CommandParameter` (`cmd.py:7-24`): `id` (empty string means the default, only-required
parameter), `editor`, `name`, `init` (a Property id the UI seeds a default value from),
`optional`. Matches the spec's `Parameter` object except `desc`, which isn't present on the
internal side.

### LinkDef (`linkdef.py`)

An INSTEON/ISY-style link/scene record: `id`, `protocol`, optional `name`, `cmd: bool` (governs
whether `parameters` may be omitted), `format` (§6), `parameters: dict[str, LinkParameter]`.
`NodeDef.links: NodeLinks` holds `ctl` (controller-role links) and `rsp` (responder-role links).

Distinct from `Command`: a `LinkDef` describes the *structure of a scene/link-table record*
between a controller and responder node, not an invokable operation — no REST-invocation
counterpart; its `.json()` is a minimal `{"name": ...}` versus `Command.json()`'s fuller
`{name, format, parameters}`.

**The cleanest alignment between the internal model and the spec of any object**: `id, name,
protocol, cmd(bool), format, parameters` on both sides. **Matching rule**: all link definitions
for a responder that share the same `protocol` string as a controller supports become
natively linkable — e.g. INSTEON's `I_STD` (no retry support) and `I_STD_ADV` (retry support).
`cmd: true` on a responder's linkdef means any direct command works, no fixed parameter list
(e.g. a Z-Wave association command). This matches nucore-ai's own `Linktype` enum
(`group.py:19-35`): `LINK_TYPE_NATIVE` ("a direct link between the controller and the responder
... NuCore/IoX isn't in the data path at all") is the precise meaning of "native link" here —
distinct from `Cmd.native` above, an unrelated, still-unresolved per-command flag (§13's G-7
has the full disambiguation).

### Folder (`folder.py`)

A thin `NodeBase` subclass with no added fields — purely an organizational grouping node.
Membership is via the inherited `parent`/`parent_type`; `NuCoreInterface.get_node` falls back
through `self.nodes` → `self.groups` → `self.folders` by address.

### Properties — live value vs. schema

Two related dataclasses, both keyed by property id (e.g. `"ST"`, `"GV1"`):

- **`Property`** (`nodedef.py:16-45`) — the *live value* on a `Node`: `id`, `value` (raw
  string), `formatted` (human-readable — **the hub computes this, nucore does not**), `uom`,
  `uom_name`, `prec`, `name`. Built from `<property>` XML, stored on `Node.properties`.
- **`NodeProperty`** (`nodedef.py:49-67`) — the *schema*: `id`, `name`, `hide` (optional, hides
  it in the UI), `editor: Editor`, `desc` (added for parity, unused). Lives on
  `NodeDef.properties`, reached from a live node via `node.node_def.properties`.

A full picture of one property = live `Node.properties[id]` (value/formatted/uom) + schema
`NodeDef.properties[id]` (editor/name), joined via the resolved `node_def`.

## 3. UOM — units of measure (`uom.py`)

A fixed, frozen-dataclass registry, not computed. `UOMEntry` (`id`, `description`, `label`,
`name`, `category_id`) is immutable by design. `PREDEFINED_UOMS` covers **~150 entries**
(ids 0-154) — electrical, temperature, distance, volume, pressure, time, thermostat modes,
currency, raw byte widths, etc. `UNKNOWN_UOM = 0` is the "unit unknown" sentinel.

Representative entries: `"51"` = Percent; `"2"` = Boolean ("0 = False, 1 = True"); `"17"` =
Fahrenheit; `"4"` = Celsius; `"56"` = Raw ("the raw value used by the device"); `"78"` = Off/On
(Off=0, On=100, Unknown=101); `"25"` = "The list index of a value for a given list of values."

**Enumeration UOMs**: `is_enumeration_uom(uom_id)` is `True` for `25, 146, 148` — the value is an
*index* into a names table, never a literal quantity. An editor range using one of these UOMs
should use `subset` + `names`, not `min`/`max` — pairing an enumeration UOM with a numeric range
is a real, previously-seen authoring bug (`plugin_authoring`'s `validate_profile` now checks for
this explicitly — see [Design decisions and implementations](plugin_authoring_design.md)).

`get_uom_by_id(uom_id)` coerces to `str` and does a plain `.get`, returning `None` silently for
unmapped ids — no synthetic fallback entry. `uom.py` is a pure lookup table; it does not itself
convert a raw value into a display string (§4 below).

## 4. Editors — the legal value space (`editor.py`, `numeric_enum.py`)

An `Editor` defines the allowed value space for a property or command parameter: `id`,
`is_reference: bool` (a deferred/linked-editor placeholder — **dead in the current codebase**,
see §13's G-3), and `ranges: list[...]`, since one editor can offer more than one uom/range
choice (e.g. raw 0-255 alongside a percentage).

Two range shapes:
- **`EditorMinMaxRange`** — continuous numeric: `uom`, `min`, `max`, `prec`, `step`, optional
  sparse `names` overlay.
- **`EditorSubsetRange`** — discrete: a `subset` span string (e.g. `"0-5,7,9"`) plus
  `names: dict[value, label]` — the enum case.

Both range types also carry an `id` field, which is always a dead copy of the parent `Editor.id`
(§13's G-4 — never a real identity of its own). `Editor` provides
`to_dict`/`get_json_descriptions`/`write_descriptions`/`write_prompt_section` to render the
constraint into LLM prompt text, special-casing `is_enumeration_uom` labels. Built per range by
`Profile.__build_editor__`, which resolves each range's UOM via `get_uom_by_id` — a `None` UOM
(unmapped id) is accepted and only logged at debug level; any later `.id`/`.name` access on that
range's `uom` will raise.

**`numeric_enum.py` is not a generic editor subtype** — it's a narrow adapter for **three
hardcoded editor ids** whose label lists are numeric data in disguise: `I_NUM_255` (raw index =
value), `I_RR` (non-uniform ramp-rate time labels), `I_BL_KP` (packed "On n / Off m" composite
labels). `describe_numeric_enum` compacts these for prompt rendering; `resolve_numeric_enum`
reverses a customer value back to the raw wire index, always re-deriving from the live editor's
own label data, never a cached/assumed packing formula.

### Value resolution — inbound direction (`value_resolution.py`)

Solves the **command** direction: given a value/unit (or matched enum label) the model has
already parsed, resolve it deterministically against the real `Editor` into a wire value — unit
conversion, precision rounding, min/max validation, enum-label lookup. Deliberately narrow, not
a general unit system: only Fahrenheit↔Celsius and seconds/minutes/hours are converted; anything
else raises `ValueResolutionError` rather than guessing. Never parses free-form language — only
numbers/short unit tokens or an already-matched enum label string the model extracted.

Entry point: `resolve_value(editor, *, value, unit=None) -> ResolvedValue`, returning `{value,
uom, precision}`. Flow: string value → try `_match_enum_label` (normalized exact match across
every range's `names`) → else `float(value)` → pick the `EditorMinMaxRange` → category-aware
`_convert_numeric` → round to `prec` → validate `min`/`max`.

**Outbound (device → display)**: hub XML already includes `formatted` — `Node.__init__` just
stores it. When code must *re-derive* a label itself (no `formatted` available, e.g. explaining
a scene/link parameter that only carries `val`+`uom`): `get_uom_by_id(param.uom)` → if it's an
enumeration UOM, look up `node.node_def.properties[param.id].editor.ranges[0].names[str(int
(param.val))]`; otherwise `f"{param.val} {uom.name}"`.

**Inbound (customer instruction → wire value)**: the model selects a target property/command
and its `Editor` → `resolve_value(...)` or, for the three special editors,
`describe_numeric_enum`/`resolve_numeric_enum` → final wire value sent to the hub. Consumers:
`src/unified/handlers/command_control_status.py`, `routine_automation.py`, `src/iox/iox_wrapper.py`.

## 5. Profile, Family, Instance

**`Profile`** (`profile.py:63-397`) is the full catalog fetched once per hub connection — every
family/instance/nodedef/linkdef/editor the hub knows about. `build_lookup` indexes it as
`"{nodedef.id}.{family.id}.{instance.id}" → NodeDef`, the key `map_nodes` queries per live node.
`RuntimeProfile` is a secondary index — per-nodedef-id, the set of live nodes using it.

**Family** has two representations that are **not numerically aligned** — don't conflate them:
catalog-side `Family` (`id`/`name`/`instances`, from `/rest/profiles`) vs. runtime
`NodeBase.family: int` (read from `<node>/<family>` XML text, default `1`). The canonical
enumeration (`src/iox/iox_definitions.py:76-89`):

| Code | Constant | Meaning |
|---|---|---|
| `"1"` | `DEVICE_FAMILY_INSTEON` | INSTEON |
| `"4"` | `DEVICE_FAMILY_LEGACY_Z_WAVE` | Legacy Z-Wave (SOAP-backed; intentionally left unwired — raises `NuCoreError`) |
| `"10"` | `DEVICE_FAMILY_PLUGIN` | Plugin / node-server-originated devices |
| `"12"` | `DEVICE_FAMILY_Z_WAVE` | Z-Wave (Z-Matter generation) |
| `"14"` | `DEVICE_FAMILY_ZIGBEE` | Zigbee |
| `"15"` | `DEVICE_FAMILY_MATTER` | Matter |

> **Known doc/code mismatch, not fixed**: `node_base.py:49`'s docstring says "e.g. 1 = Insteon,
> 10 = Z-Wave" — stale against the table above, where `10` is Plugin and `12` is Z-Wave.

Family codes are hardcoded constants, never cross-validated against the catalog's own
`Family.id` values. Plugin-originated devices reuse the **same** family-indexed machinery as
native protocols (`_is_plug_in_family`/`_family_api_path`) — not a separate code path.

**Instance** = the multi-instance index within a family, read from the `instance` attribute on
the `<family>` XML element, default `1`. Not encoded in `address`; multi-button devices (e.g. a
KeypadLinc) are instead distinguished via `pnode`/`rpnode` alongside their own `address`.
Catalog-side, `Instance` is a numbered configuration bundle of `editors`/`linkdefs`/`nodedefs`
within one family. The same lookup pattern is reused for scene link resolution
(`group.py:179`: `f"{linkdef}.{node.family}.{node.instance}"`).

## 6. Dynamic Profiles — what a plugin's own backend sends

**Requirements**: IoX 6.0.6+, PG3x 3.4.5+, `udi_interface` 3.4.5+. A plugin uses *either* static
profile files *or* Dynamic Profiles, never both — sending a dynamic profile makes PG3/IoX ignore
any static profile files from then on. This is the wire shape (§1).

None of §6-9 below happens inside nucore-ai's own codebase — it all happens in the plugin's own
backend process, talking to PG3/IoX directly. nucore-ai only ever sees the result (the catalog,
§2-5). See [Lifecycle and runtime](plugin_lifecycle_and_runtime.md) for where this fits in the
full plugin lifecycle.

### Interface methods (`udi_interface`)

**`polyglot.getJsonProfile(options)`** — fetches the plugin's current profile as JSON.
`options.waitResponse` (default `False`): `True` blocks and returns the profile directly;
`False` returns immediately and the plugin must subscribe to `polyglot.PROFILE` to receive it.
Raises `TimeoutError` on a `waitResponse=True` timeout. Returns all nodedefs regardless of
whether the plugin was installed with static files or dynamic profiles — useful for migrating
an existing static profile to the dynamic format (§9).

**`polyglot.updateJsonProfile(update, options)`** — updates the profile with additions,
replacements, or deletions. `update.delete` (optional; per category `editors`/`nodedefs`/
`linkdefs`: `"*"` deletes everything in that category, or a list of ids deletes only those).
Any `editors`/`nodedefs`/`linkdefs` provided replace an existing item sharing that `id`, or add
a new one. `options.waitResponse` (default `False`): `True` blocks until the update completes
(useful if the plugin needs to create a node based on the new nodedef immediately after).
Raises `ValueError` on validation failure, `TimeoutError` as above. Confirmed against the real
installed `udi_interface==3.4.5` package: `Interface.updateJsonProfile` reads
`options.get('waitResponse', False)` exactly as documented here (its own docstring says
`waitCompletion` — a pre-existing inconsistency in `udi_interface` itself, not a second option).

```python
profile = polyglot.getJsonProfile({"waitResponse": True})
polyglot.updateJsonProfile({
  "delete": {"editors": ["I3_LOAD_4"], "nodedefs": ["*"], "linkdefs": ["*"]},
  "editors": [...], "nodedefs": [...], "linkdefs": [...],
}, {"waitResponse": True})
```

**`polyglot.getValidName(name)`** / **`polyglot.getValidAddress(address)`** — sanitize a
candidate node display name / address so IoX will accept it (invalid characters stripped or
replaced, length limits enforced) before constructing a `Node` and calling
`self.poly.addNode(...)`. Always run a dynamically-derived name/address (e.g. built from a
discovered device's own hostname, serial number, or user input) through these first — nothing
else in this pipeline validates them.

### Profile structure (the wire shape's objects)

| Object | Fields |
|---|---|
| `Profile` | `nodedefs: NodeDef[]`, `editors: Editor[]`, `linkdefs: LinkDef[]` |
| `NodeDef` | `id`, `name`, `properties: Property[]`, `cmds: {sends, accepts}`, `links: {ctl, rsp}`, `desc?`, `icon?` (UI icon), `customicon?` (future use) |
| `Property` | `id` (e.g. `ST`, `GV0`), `name`, `editor` (id), `desc?`, `hide?` |
| `Cmd` | `id`, `name`, `native` (string `"true"`/`"false"`), `desc?`, `format?` (§7), `parameters?: Parameter[]` |
| `Parameter` | `id` (empty string = the default, only-required parameter), `editor`, `optional?`, `init?` (a Property id the UI seeds a default from), `desc?`, `name?` |
| `Editor` | `id`, `ranges: Range[]` |
| `Range` (min/max) | `min`, `max`, `prec?`, `step?`, `uom`, `names?`, `desc?` |
| `Range` (subset) | `subset` (e.g. `"0-5,8-10,20"`), `uom`, `names?`, `desc?` |
| `LinkDef` | `id`, `name`, `protocol`, `cmd?` (bool, default `false`), `format?` (§7), `parameters?` (responder-only, same shape as `Cmd.parameters` minus `init`), `desc?` |

Worked example — one NodeDef (`CTL`) with two properties and three accepted commands:

```jsonc
{
  "editors": [
    {"id": "online", "ranges": [{"uom": "25", "subset": "0,1", "names": {"0": "Offline", "1": "Online"}}]},
    {"id": "test", "ranges": [{"uom": "25", "subset": "0-5", "names": {
      "0": "Not tested", "1": "Testing...", "2": "Success", "3": "Timeout", "4": "Failure", "5": "Auth. Failure"
    }}]}
  ],
  "linkdefs": [],
  "nodedefs": [{
    "id": "CTL", "name": "Controller", "icon": "GenericCtl",
    "properties": [
      {"id": "ST", "editor": "online", "name": "Plugin Status"},
      {"id": "GV0", "editor": "test", "name": "Test result"}
    ],
    "cmds": {"sends": [], "accepts": [
      {"id": "DISCOVER", "native": "false", "name": "Discover devices"},
      {"id": "QUERYALL", "name": "Query All"},
      {"id": "TEST", "name": "Test"}
    ]},
    "links": {"ctl": [], "rsp": []}
  }]
}
```

**LinkDef worked example** — dimmer/thermostat responders plus their controller-role
counterparts (INSTEON has two link protocols, `I_STD` for controllers without retry support and
`I_STD_ADV` for controllers with it):

```jsonc
{"linkdefs": [
  {"id": "I_DIMMER", "protocol": "I_STD", "name": "Insteon",
   "format": ";OL;;${v}; ;RR;; in ${v};",
   "parameters": [{"id": "OL", "editor": "I_OL", "name": "On Level"}, {"id": "RR", "editor": "I_RR", "name": "Ramp Rate"}]},
  {"id": "I_DIMMER_ADV", "protocol": "I_STD_ADV", "name": "Insteon",
   "format": ";OL;;${v}; ;RR;; in ${v}; ;CLNRT;;, ${v};",
   "parameters": [{"id": "OL", "editor": "I_OL", "name": "On Level"}, {"id": "RR", "editor": "I_RR", "name": "Ramp Rate"}, {"id": "CLNRT", "editor": "I_CLNRT", "name": "Retries"}]},
  {"id": "I_CTL_DIMMER", "protocol": "I_STD", "name": "Insteon"},
  {"id": "I_CTL_DIMMER_ADV", "protocol": "I_STD_ADV", "name": "Insteon"}
]}
```

A `cmd: true` linkdef (any direct command works for the responder, e.g. a Z-Wave association
command): `{"id": "ASSOC_CMD", "protocol": "ASSOC_CMD", "name": "Z-Wave Association Command", "cmd": true}`.

## 7. Formatting in Programs and Scenes

Each line of a program is formatted and displayed in different ways. A `Cmd`'s or `LinkDef`'s
`format` field controls how it renders in the program/scene editor.

**Pattern**: `/<param.id>/text if omitted/text if specified/ [... next parameter, ...]` — the
format string's **first character** defines the separator (normally `/`). `param.id` is the
parameter's id (blank for an unnamed parameter).

Variables usable inside the text segments:

| Variable | Meaning |
|---|---|
| `${c}` | Name of the command |
| `${v}` | Formatted value of the parameter (including UOM) |
| `${vo}` | Formatted value of the parameter (without UOM) |
| `${uom}` | Formatted UOM without the value |
| `${op}` | Operator used (conditions only) |

**Worked examples**, assuming commands are for node `'MyDevice'`:

- Format `/num//${c} Parameter ${v}/ /val/ default/ = ${v}/ /len// (${v} bytes)/` (three named
  parameters `num`, `val`, `len`): `num=1, len=4` (Config) → `Set 'MyDevice' Config Parameter 1
  = 20 (4 bytes)`; `num=5` (Config) → `Set 'MyDevice' Config Parameter 5 = 25`.
- Format `//default/${v}/` (one unnamed parameter): omitted → `Set 'MyDevice' default`; `=50
  percent` → `Set 'MyDevice' 50%`.
- Format `/level/${c}/to ${v}/ /ramprate// in ${v}/ /offtimer//, turn off ${v} later/` with
  `level=50%, ramprate=3 seconds, offtimer=5 minutes` → `Set 'MyDevice' to 50% in 3 seconds, turn
  off 5 minutes later`.

## 8. Developing a plugin's device model, step by step

The practical path for a plugin author (condensed — see §6-7 above for the full field tables and
grammar):

1. **Design the device model** — for each node type: its `NodeDef` (`id`/`name`/`icon`); its
   `properties[]`, each with an `id`, a `name`, and an `editor` reference (editors are defined
   once and referenced by id, never inlined); its `cmds.accepts[]`/`cmds.sends[]`, each
   optionally carrying `parameters[]` (also referencing editors); its `links.ctl[]`/`links.rsp[]`
   if the device participates in native scene links.
2. **Define the `editors[]`** the nodedefs above reference — each a named, reusable value-space:
   a `min`/`max`/`step`/`uom` numeric range, or a `subset` + `names` enum map. Define once,
   reference by `id` from as many properties/parameters as needed.
3. **Send the profile** — new plugin, no existing static files: build the full
   `{nodedefs, editors, linkdefs}` object and send it via `polyglot.updateJsonProfile(profile,
   {"waitResponse": True})` before `polyglot.ready()`. Incremental updates later (e.g. a newly
   discovered device needs a new nodedef): send only the changed items —
   `updateJsonProfile` add/replaces by matching `id`, no need to resend the whole profile.
4. **Use `format` strings** (§7) where a command or link parameter should render nicely in the
   program/scene editor.

## 9. Migrating a plugin off static profile files

1. **Retrieve the existing profile as JSON** — add a function that calls
   `polyglot.getJsonProfile({'waitResponse': True})` before `polyglot.ready()`, with a short
   delay first (PG3 uploads the plugin's static profile files on startup; the delay ensures
   they've landed before fetching). Print and save the result.
2. **Switch the plugin to the JSON format** — load the captured profile (inline or from a file)
   and call `polyglot.updateJsonProfile(profile, {"waitResponse": True})` before
   `polyglot.ready()`.
3. **Stop using the static format** — remove any `polyglot.updateProfile()`/`checkProfile()`
   calls, then **delete the plugin's static profile folder**. This step is critical: leaving the
   folder in place means PG3x re-uploads it on every startup, racing the JSON update
   unpredictably.
4. **Go fully dynamic** — once on the JSON format, call `updateJsonProfile()` to add/remove
   individual nodedefs/editors as needed (e.g. when a new device is discovered) — never resend
   the entire profile.

## 10. Known gotchas and resolved discrepancies

A record of field-level mismatches found while cross-referencing the wire-shape spec against
nucore-ai's internal model, plus internal-model-only quirks. Kept as a record even where
resolved, so the reasoning doesn't need re-deriving later.

- **G-1, resolved — `nls`/`icon` were never a second concept.** They're legacy names for
  `name`/`customicon`, never both populated on the same real profile. `NodeDef` has one field
  for each (`name`, `customicon`), not two; `Profile.__parse_profile__` populates each with a
  same-line fallback (`name=ndict.get("nls") or ndict.get("name")`,
  `customicon=ndict.get("icon") or ndict.get("customicon")`), so whichever raw key a source
  profile uses, the result lands in exactly one field. `desc` has no legacy counterpart.
- **G-2, resolved (partially) — `Cmd.native`/`desc` added for parity, confirmed unused.**
  `Command` now carries both, populated via `cdict.get(...)`, `None` when absent. `NodeProperty
  .desc` got the same treatment. **`native` specifically remains an open question**: across
  multiple real profiles inspected it's never been observed as anything but `"false"` —
  consistent with, not proof of, being vestigial. Whether it's still load-bearing anywhere on
  the PG3/IoX side is unknown from this repo alone. `CommandParameter.desc` was *not* added and
  remains open if parity is wanted there too.
- **G-3, resolved — `Editor.is_reference` is dead.** Original intent: let an editor be a
  lightweight pointer to a shared/global editor definition instead of repeating its range list
  inline every time. Still visible in branching logic (`get_python_description`,
  `get_json_descriptions`, `write_descriptions` all special-case `is_reference=True` into a
  placeholder), but nothing in the current codebase ever sets it `True` — the only real
  construction site, `Profile.__build_editor__`, hardcodes `is_reference=False`. Root cause: a
  retired "DedupeDevices" cross-device editor-sharing pass (`src/rag/profile_rag_formatter.py`
  documents the removal directly), the same one that left G-4 behind.
- **G-4, resolved — `Range.id` is dead weight.** Exists on both range types, labeled `#editor
  id` in source, but always a copy of the parent `Editor.id` — `Profile.__build_editor__` always
  sets it to the editor's own id. Matches the spec, whose `Range` table has no `id` field at
  all. `EditorMinMaxRange.id` is never read anywhere; `EditorSubsetRange.id` is read in exactly
  one dormant, untested place (`write_description`, only reachable via the non-default
  YAML prompt path). Same root cause as G-3 — and the same docstring records a real bug this
  caused: exposing the editor id in Python-literal output once led a routine to author
  `param(id=<editor_id>, ...)` instead of the real parameter id, "because the two looked equally
  id-shaped" — exactly why the current Python-literal rendering path deliberately excludes it.
- **G-5, resolved (platform knowledge, not derivable from this repo alone)** — the
  family/instance assignment rule, stated in full in §5 above.
- **G-6, resolved — plugin-originated devices reuse the ordinary family-indexed machinery.**
  `DEVICE_FAMILY_PLUGIN` is just code `"10"`, routed through the same generic
  `_is_plug_in_family`/`_family_api_path` paths used for every protocol — not a separate model.
- **G-7, partially resolved — "native" is overloaded across two unrelated concepts.** "Native
  links" is fully clear and confirmed in code (`group.py`'s `Linktype` enum, §4 above) — a real
  protocol/hardware link where NuCore/IoX isn't in the data path, matching the spec's `LinkDef`
  description exactly. **`Cmd.native` is the unresolved half** — no connection to `Linktype` was
  found anywhere (different module, no shared code path); its meaning as a per-command flag
  remains open (same gap as G-2). Don't conflate the two senses when reading either model.
- **Resolution is exact-match by design, not fuzzy** — both the `node_def` lookup and
  `resolve_property_id`/`resolve_command_id` require an exact key match; nothing here guesses at
  a close-enough id the way the plugin framework's id-resolution guards do for untrusted
  LLM-guessed input (see [Lifecycle and runtime](plugin_lifecycle_and_runtime.md)) — a different
  problem shape (untrusted input vs. this module's already-validated catalog keys).
- **Parsing is lenient, not strict** — malformed families/editors/linkdefs/properties are
  logged and skipped rather than raising; a partially malformed hub payload silently drops
  entries rather than failing the whole load.
- **Editor accumulation "hack"** (`profile.py:192`, `# mpg names hack`) — each `Instance
  .editors` ends up holding *all* editors seen so far for the family, not just its own
  instance's.
- **Unmapped UOM is silently `None`** from `get_uom_by_id` and when building an editor range — a
  later `.id`/`.name` access will raise; there's no synthetic fallback entry.
- **`group.py`'s explain/explain_json wrap link-explanation in a broad `except Exception`** —
  e.g. `uom.name` on a falsy `param.uom` would `AttributeError`, but this is silently swallowed
  into a generic "error occurred while explaining the link" message instead of surfacing the
  bug.

## 11. Public API surface

`src/nucore/__init__.py` is the intended public surface of this package — notably exports
`Profile`, `Family`, `Instance`, `RuntimeProfile` directly as first-class public types, alongside
`Node`, `NodeDef`, `Command`, `LinkDef`, etc.

## 12. JSON Schema validation (`schemas/`)

`src/nucore/schemas/` holds a JSON Schema (draft 2020-12) restructuring of
[`iox-vscode-plugin`](https://github.com/universaldevices/iox-vscode-plugin)'s validation
schemas onto this package's own Dynamic Profiles object model (see
[Design decisions and implementations](plugin_authoring_design.md) for the reuse decision).
Packaged data, not part of the Python API surface in §11 — nothing loads or enforces it at
import/parse time. Full provenance/design writeup, including the two top-level shapes (§1) and
every known doc/code mismatch the schemas had to design around: see
[`schemas/README.md`](../../src/nucore/schemas/README.md) (kept current, not duplicated here).

# The `src/nucore/` domain model — a developer reference

> Reference doc, not a proposal. It explains what's shipped today.

## 1. The shape of it, in one pass

Two data sources get merged into one live picture, per hub connection:

- **The profile catalog** — fetched once from the hub (`GET /rest/profiles`) or loaded from a
  local file, giving the *types* of things that can exist: which `Family` → `Instance` →
  `NodeDef`s exist, and for each `NodeDef`, which properties/commands/links it declares and
  what value space (`Editor`) each one accepts.
- **The live node list** — fetched once from the hub (`GET /rest/nodes` XML, plus
  `/api/groups/links`), giving the *instances* that actually exist on this installation: each
  `Node`'s address, raw property values, and a `(node_def_id, family, instance)` triple.

`Profile.map_nodes()` is the join: it resolves each live `Node`'s `node_def_id` + `family` +
`instance` against the catalog to attach the right `NodeDef`, turning a bag of raw values into
a fully-typed device. Everything below either belongs to the catalog side (`NodeDef`,
`Command`, `LinkDef`, `NodeProperty`, `Editor`, `Family`, `Instance`) or the live side (`Node`,
`Property`, `Group`, `Folder`).

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

## 2. Node and NodeBase

`Node` (`src/nucore/node.py:29`) represents one physical/logical device instance on the hub,
built directly from a `<node>` XML element (`node.py:66-99`) — not JSON.

**`NodeBase`** (`src/nucore/node_base.py:38`) is a shared ABC dataclass base for `Node`,
`Group` (`src/nucore/group.py`), and `Folder` (`src/nucore/folder.py:7`). It owns everything
common to a hub entity: `flag`, `node_def_id`, `address`, `name`, `family: int`,
`instance: int`, `enabled`, `parent`, `parent_type`, `hint`, `node_def: NodeDef | None`
(`node_base.py:57-67`), flag-bit predicates (`node_is_group`, `node_is_root`,
`node_is_in_err`, `node_is_device_primary`, `node_parent_is_*`, `node_base.py:107-133`), and the
`NodeTypes`/`NodeHierarchy` bit-flag constants (`node_base.py:15-30`).

`Node` adds the device-only fields: `type`, `deviceClass`, `wattage`, `dcPeriod`,
`startDelay`/`endDelay`, `pnode`/`rpnode` (primary-node addressing for multi-instance devices —
see §12), `sgid` (scene/group id), `typeInfo: list[TypeInfo]` (`node.py:17-25,51-64`), and the
live `properties: dict[str, Property]` (`node.py:83-94`).

**`node_def` is a resolved lookup, not composition.** `node_def_id` is parsed straight off the
XML (`node_base.py:82`); `node_def` itself stays `None` until `Profile.map_nodes` runs
`node.node_def = self.lookup.get(f"{node.node_def_id}.{node.family}.{node.instance}")`
(`profile.py:359-360`). A miss is non-fatal — logged at debug level, `node_def` stays `None`
(`profile.py:361-362`).

`Node.__hash__` hashes solely on `address` (`node.py:197-199`). Two load paths exist: hub XML
(`GET /rest/nodes`, `iox_wrapper.py:769-772`) or a local file (`Node.load_from_file`,
`node.py:137-153`).

## 3. NodeDef — the type a Node is an instance of

`NodeDef` (`src/nucore/nodedef.py:90`) is the template: "describes the properties, commands,
and links... defining its behavior and capabilities" (`nodedef.py:91-94`).

| Field | Type | Meaning |
|---|---|---|
| `id` | `str` | The nodedef id, first component of the `(node_def_id, family, instance)` lookup key |
| `properties` | `dict[str, NodeProperty]` | Schema per property id — see §8 |
| `cmds` | `NodeCommands` | `.accepts` / `.sends` lists of `Command` — see §4 |
| `nls` | — | i18n string-table key |
| `icon` | — | Display icon |
| `links` | `NodeLinks` | `.ctl` / `.rsp` lists of `LinkDef` — see §5 |

Origin: parsed out of the profile catalog in `Profile.__parse_profile__`
(`profile.py:234-309`), itself sourced from `GET /rest/profiles` (`iox_wrapper.py:758-761`) or
`Profile.load_from_file` (`profile.py:79-97`) when a local `profile_path` is supplied.

## 4. Command (`cmd.py`)

`Command` (`src/nucore/cmd.py:42`) is a single named operation a node type supports: `id`
(e.g. `"DON"`, `"DOF"`), optional `name`/`format`, and ordered `parameters: list[CommandParameter]`
(`cmd.py:6-24,53-56`), each parameter carrying its own `Editor` plus `init`/`optional`.

**Accepts vs. sends is explicit and first-class.** `NodeDef.cmds: NodeCommands`
(`nodedef.py:69-76,98`) splits into:
- `accepts: list[Command]` — sent *to* the device. Used by
  `NuCoreInterface.resolve_command_id(..., direction="accepts")` (`nucore_interface.py:158-180`)
  and invoked at `POST/GET .../rest/nodes/{device_id}/cmd/{command_id}[/value[/uom]]`
  (`iox_wrapper.py:1149,1204-1205`).
- `sends: list[Command]` — device-initiated events, surfaced separately in `Node.json()` as
  `"sends.commands"` vs `"accepts.commands"` (`node.py:131-132`).

`resolve_property_id`/`resolve_command_id` do a **strict, scoped exact-name match** — not a
fuzzy resolver — deliberately, because the same display name (e.g. "On Level") can exist as
both a property and a command on one device; namespaces are kept separate to avoid ambiguity
rather than guessed at (`nucore_interface.py:132-180`, docstring at `:137-144`).

## 5. LinkDef (`linkdef.py`)

An INSTEON/ISY-style link/scene record: `id`, `protocol`, optional `name`, `cmd: bool` (governs
whether `parameters` may be omitted, `linkdef.py:31-33`), `format`, `parameters: dict[str,
LinkParameter]`. `NodeDef.links: NodeLinks` holds `ctl` (controller-role links) and `rsp`
(responder-role links) — which link roles are valid for that node type, mirroring INSTEON
controller/responder scene semantics.

Distinct from `Command`: a `LinkDef` describes the *structure of a scene/link-table record*
between a controller and responder node, not an invokable, addressable operation — it has no
REST-invocation counterpart, and its `.json()` (`linkdef.py:52-60`) is a minimal `{"name":
...}`, versus `Command.json()`'s fuller `{name, format, parameters}` (`cmd.py:58-70`).

## 6. Folder (`folder.py`)

A thin `NodeBase` subclass with no added fields (`folder.py:7-12`) — purely an organizational
grouping node. Membership is via the inherited `parent`/`parent_type` fields; `NuCoreInterface
.get_node` falls back through `self.nodes` → `self.groups` → `self.folders` by address
(`nucore_interface.py:125-129`).

## 7. Properties — live value vs. schema

Two related dataclasses, both keyed by property id (e.g. `"ST"`, `"GV1"`):

- **`Property`** (`nodedef.py:16-45`) — the *live value* on a `Node`: `id`, `value` (raw
  string), `formatted` (human-readable — **the hub computes this, nucore does not**), `uom`,
  `uom_name`, `prec`, `name`. Built from `<property>` XML in `node.py:83-94`, stored on
  `Node.properties: dict[str, Property]`.
- **`NodeProperty`** (`nodedef.py:49-67`) — the *schema*: `id`, `name`, `hide`, `editor:
  Editor`. Lives on `NodeDef.properties`, reached from a live node via `node.node_def
  .properties`.

A full picture of one property = live `Node.properties[id]` (value/formatted/uom) + schema
`NodeDef.properties[id]` (editor/name), joined via the resolved `node_def` from §2.

## 8. UOM — units of measure (`uom.py`)

A fixed, frozen-dataclass registry, not computed. `UOMEntry` (`uom.py:10-23`: `id`,
`description`, `label`, `name`, `category_id`) is immutable by design. `PREDEFINED_UOMS`
(`uom.py:27-934`) covers **~150 entries** (ids 0-154) — electrical, temperature, distance,
volume, pressure, time, thermostat modes, currency, raw byte widths, etc.
`UNKNOWN_UOM = 0` is the "unit unknown" sentinel (`uom.py:25`).

Representative entries: `"51"` = Percent, `"%"` (`uom.py:351-356`); `"2"` = Boolean, `"0 =
False, 1 = True"` (`uom.py:41-46`); `"17"` = Fahrenheit (`uom.py:136-142`); `"4"` = Celsius
(`uom.py:54-60`); `"56"` = Raw — "the raw value used by the device" (`uom.py:373-378`); `"78"`
= Off/On (Off=0, On=100, Unknown=101, `uom.py:497-503`); `"25"` = "The list index of a value
for a given list of values" (`uom.py:186-191`).

**Enumeration UOMs**: `is_enumeration_uom(uom_id)` (`uom.py:936-952`) is `True` for `25, 146,
148` — the value is an *index* into a names table, never a literal quantity.

`get_uom_by_id(uom_id)` (`uom.py:954-969`) coerces to `str` and does a plain `.get`, returning
`None` — silently — for unmapped ids; no synthetic fallback entry. `uom.py` is a pure lookup
table; it does not itself convert a raw value into a display string (see §11).

## 9. Editors — the legal value space (`editor.py`, `numeric_enum.py`)

An `Editor` (`editor.py:237-334`) defines the allowed value space for a property or command
parameter (`editor.py:1-8`): `id`, `is_reference: bool` (a deferred/linked editor placeholder —
callers must resolve the reference elsewhere before range data is available,
`editor.py:15,259,273-275,296-299`), and `ranges: list[...]`, since one editor can offer more
than one uom/range choice (e.g. raw 0-255 alongside a percentage).

Two range shapes:
- **`EditorMinMaxRange`** (`editor.py:153-235`) — continuous numeric: `uom`, `min`, `max`,
  `prec`, `step`, optional sparse `names` overlay.
- **`EditorSubsetRange`** (`editor.py:17-151`) — discrete: a `subset` span string (e.g.
  `"0-5,7,9"`) plus `names: dict[value, label]` (`_enum_dict`, `editor.py:30-50`) — the
  NLS-backed enum case.

`Editor` provides `to_dict`/`get_json_descriptions`/`write_descriptions`/
`write_prompt_section` to render the constraint into LLM prompt text, special-casing
`is_enumeration_uom` labels (`editor.py:81-82`). Built per range by `Profile.__build_editor__`
(`profile.py:126-157`), which resolves each range's UOM via `get_uom_by_id` (`profile.py:131`)
— note: a `None` UOM (unmapped id) is still accepted here and only logged at debug level
(`profile.py:131-133`); any later `.id`/`.name` access on that range's `uom` will raise.

**`numeric_enum.py` is not a generic editor subtype** — it's a narrow adapter for **three
hardcoded editor ids** whose label lists are numeric data in disguise (`numeric_enum.py:1-24`):
`I_NUM_255` (raw index = value), `I_RR` (non-uniform ramp-rate time labels), `I_BL_KP`
(packed "On n / Off m" composite labels). `describe_numeric_enum(editor)`
(`numeric_enum.py:88-125`) compacts these for prompt rendering (e.g. "0-255" instead of 256
labels); `resolve_numeric_enum(editor, value, unit=...)` (`numeric_enum.py:128-192`) reverses a
customer value back to the raw wire index — always by re-deriving from the live editor's own
label data, never a cached/assumed packing formula (`numeric_enum.py:9-15`).

## 10. Value resolution — inbound direction (`value_resolution.py`)

Solves the **command** direction: given a value/unit (or matched enum label) the model has
already parsed from a customer instruction, resolve it deterministically against the real
`Editor` into a wire value — unit conversion, precision rounding, min/max validation, enum-label
lookup (`value_resolution.py:1-21`). Deliberately narrow, not a general unit system: only
Fahrenheit↔Celsius (`value_resolution.py:45-63`) and seconds/minutes/hours are converted;
anything else raises `ValueResolutionError` rather than guessing (`:109-127`).

Entry point: `resolve_value(editor, *, value, unit=None) -> ResolvedValue`
(`value_resolution.py:130-180`), returning `{value, uom, precision}`. Flow: string value →
try `_match_enum_label` (normalized exact match across every range's `names`,
`value_resolution.py:70-84`) → else `float(value)` → pick the `EditorMinMaxRange` → category-aware
`_convert_numeric` → round to `prec` → validate `min`/`max`.

## 11. Putting §7-10 together

**Outbound (device → display)**: hub XML already includes `formatted` — `Node.__init__` just
stores it (`node.py:86-93`). When code must *re-derive* a label itself (no `formatted`
available, e.g. explaining a scene/link parameter that only carries `val`+`uom`), the pattern
in `group.py:106-114,131-139` is: `get_uom_by_id(param.uom)` → if it's an enumeration UOM,
look up `node.node_def.properties[param.id].editor.ranges[0].names[str(int(param.val))]`;
otherwise `f"{param.val} {uom.name}"`.

**Inbound (customer instruction → wire value)**: the model selects a target property/command
and its `Editor` (built at profile-load time, §9) → `resolve_value(...)` (§10) or, for the
three special editors, `describe_numeric_enum`/`resolve_numeric_enum` (§9) → final wire
value sent to the hub. Consumers: `src/unified/handlers/command_control_status.py`,
`src/unified/handlers/routine_automation.py`, `src/iox/iox_wrapper.py`.

## 12. Profile, Family, Instance

**`Profile`** (`profile.py:63-397`) is the full catalog fetched once per hub connection — every
family/instance/nodedef/linkdef/editor the hub knows about — via `GET /rest/profiles`
(`iox_wrapper.py:752-761`) or a local file. `build_lookup` (`profile.py:116-124`) indexes it as
`"{nodedef.id}.{family.id}.{instance.id}" → NodeDef`, the key `map_nodes` queries per live node
(§2). `RuntimeProfile` (`profile.py:53-59`) is a secondary index — per-nodedef-id, the *set of
live nodes* using it — populated alongside node resolution (`profile.py:365-371`).

**Family** = the protocol/subsystem a node originates from. Two representations that are
**not numerically aligned** — don't conflate them:
- Catalog-side `Family` (`profile.py:43-51`): `id`/`name`/`instances`, from the `/rest/profiles`
  payload.
- Runtime `NodeBase.family: int` (`node_base.py:49,61,84-96`), read from `<node>/<family>` XML
  text, default `1`.

The canonical enumeration lives in `src/iox/iox_definitions.py:76-89`:

| Code | Constant | Meaning |
|---|---|---|
| `"1"` | `DEVICE_FAMILY_INSTEON` | INSTEON |
| `"4"` | `DEVICE_FAMILY_LEGACY_Z_WAVE` | Legacy Z-Wave (SOAP-backed; intentionally left unwired — raises `NuCoreError`, `iox_wrapper.py:2278-2284`) |
| `"10"` | `DEVICE_FAMILY_PLUGIN` | Plugin / node-server-originated devices |
| `"12"` | `DEVICE_FAMILY_Z_WAVE` | Z-Wave (Z-Matter generation) |
| `"14"` | `DEVICE_FAMILY_ZIGBEE` | Zigbee |
| `"15"` | `DEVICE_FAMILY_MATTER` | Matter |

> **Known doc/code mismatch, not fixed here**: `node_base.py:49`'s docstring says "e.g. 1 =
> Insteon, 10 = Z-Wave" — stale against the table above, where `10` is actually Plugin and
> `12` is Z-Wave. Worth correcting in a future pass.

`IoXWrapper._get_node_family` (`iox_wrapper.py:3007-3025`) resolves a node's family code to its
display name via `DEVICE_FAMILIES`; `_is_insteon_family`/`_is_z_wave_family`/
`_is_plug_in_family`/etc. (`iox_wrapper.py:3030-3070`) wrap it. Public accessor:
`NuCoreInterface.get_device_family` (`nucore_interface.py:730-738`) →
`IoXDiagnostics.get_device_family` (`src/iox/diagnostics/iox_diagnostics.py:350-354`). Family
codes are **hardcoded constants**, not fetched dynamically, and are never cross-validated
against the catalog's own `Family.id` values from `/rest/profiles`.

**Instance** = the multi-instance index within a family, for one physical/logical node — read
from the `instance` attribute on the same `<family>` XML element (`node_base.py:90-93`),
default `1`. It is **not** encoded in `address`; multi-instance/multi-button devices (e.g. a
KeypadLinc) are instead distinguished via `pnode`/`rpnode` (primary/real-primary node address,
`node.py:57-58,79-80`) alongside their own `address`. Catalog-side, `Instance`
(`profile.py:29-40`) is a numbered configuration bundle of `editors`/`linkdefs`/`nodedefs`
within one family. The two meanings meet at the same lookup key described above; the same
pattern is reused for scene link resolution — `group.py:179` looks up
`f"{linkdef}.{node.family}.{node.instance}"`.

## 13. Loading sequence (who calls whom)

`IoXWrapper.__load_profile__` (`iox_wrapper.py:2545-2571`) loads the profile (file or
`/rest/profiles`) → `__load_nodes__` (`iox_wrapper.py:2573-2593`) fetches `/rest/nodes` XML →
`__load_groups_links__` (`iox_wrapper.py:2596-2617`) fetches `/api/groups/links` →
`_load_devices` (`iox_wrapper.py:2510-2543`) calls `self.profile.map_nodes(root,
glinks_root)` and assigns the results into `self.runtime_profiles` / `self.nodes` /
`self.groups` / `self.folders`. Everything is an **eager, one-shot load** — nothing here
re-fetches per-node on demand; a full reload re-runs the same sequence.
`NuCoreInterface.__init__` seeds an empty `Profile(timestamp="", families=[])` placeholder
before any load (`nucore_interface.py:60`).

## 14. Notable design choices / gotchas (consolidated)

- **Resolution is exact-match by design, not fuzzy** — both the `node_def` lookup (§2) and
  `resolve_property_id`/`resolve_command_id` (§4) require an exact key match; nothing here
  guesses at a close-enough id the way, say, the plugin framework's id-resolution guards do
  (see [`runtime_plugin.md`](runtime_plugin.md)) — a different problem shape (untrusted
  LLM-guessed input vs. this module's already-validated catalog keys).
- **Parsing is lenient, not strict**: malformed families/editors/linkdefs/properties are
  logged and skipped rather than raising (`profile.py:169-190,196-198,208-214`) — a partially
  malformed hub payload silently drops entries rather than failing the whole load.
- **Editor accumulation "hack"**: `profile.py:192` (`# mpg names hack`) — each `Instance
  .editors` ends up holding *all* editors seen so far for the family (`editors_dict` accumulates
  across instances, `profile.py:168,315`), not just its own instance's editors.
- **Unmapped UOM is silently `None`**, both from `get_uom_by_id` and when building an editor
  range (`profile.py:131-133`) — a later `.id`/`.name` access will raise; there's no synthetic
  fallback entry.
- **`group.py`'s explain/explain_json wrap link-explanation in a broad `except Exception`**
  (`group.py:108/115,126/151`) — e.g. `uom = get_uom_by_id(param.uom) if param.uom else ""`
  (`group.py:131`) followed by `uom.name` would `AttributeError` on a falsy `param.uom` (plain
  `""` has no `.name`), but this gets silently swallowed into a generic "error occurred while
  explaining the link" message rather than surfacing the bug.
- **Plugin-originated devices reuse the same family-indexed machinery** as native protocols —
  `DEVICE_FAMILY_PLUGIN` is just code `"10"`, routed through the same generic
  `_is_plug_in_family` / `_family_api_path` code paths (`iox_wrapper.py:3067-3070,2270-2277`),
  not a separate model. See [`runtime_plugin.md`](runtime_plugin.md) for the plugin-management
  feature this connects to.
- **`value_resolution.py` never parses free-form language** — only numbers/short unit tokens or
  an already-matched enum label string the model extracted; NL understanding is deliberately
  kept out of this deterministic module (`value_resolution.py:1-21`).

## 15. Public API surface

`src/nucore/__init__.py` is the intended public surface of this package — notably exports
`Profile`, `Family`, `Instance`, `RuntimeProfile` directly (`__init__.py:16,25`) as first-class
public types, alongside `Node`, `NodeDef`, `Command`, `LinkDef`, etc.

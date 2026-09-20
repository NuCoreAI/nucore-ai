# Dynamic Profiles — PG3/IoX plugin API reference

> Reference doc, not a proposal. Reconstructed from
> [developer.isy.io/docs/API/pg/DynamicProfiles](https://developer.isy.io/docs/API/pg/DynamicProfiles)
> (Universal Devices' own Polyglot/PG3 plugin-development docs) — this describes the wire
> format a **plugin's own backend** sends to PG3/IoX, the counterpart to what
> [`nucore_domain_model.md`](nucore_domain_model.md) documents from nucore-ai's side (a
> `Profile`/`NodeDef`/`Editor`/`LinkDef` consumer, not a producer) and what
> [`runtime_plugin.md`](runtime_plugin.md) documents about how nucore-ai manages/calls plugins
> as a marketplace feature. All three docs describe different layers of the same overall system.

## 1. What this is

Dynamic Profiles let a plugin define its ISY/eisy profile — nodedefs, editors, linkdefs — **at
runtime via JSON**, sent directly to PG3/IoX over the wire, instead of shipping static profile
files with the plugin. A plugin uses **either** static profile files **or** dynamic profiles,
never both: once a dynamic profile is sent to PG3/IoX, any static profile files are ignored.

**Requirements**: IoX 6.0.6+, PG3x 3.4.5+, `udi_interface` 3.4.5+.

## 2. Interface methods

Two methods added to `udi_interface`'s polyglot interface class:

### `polyglot.getJsonProfile(options)`

Fetches the plugin's current profile as JSON.

- `options.waitResponse` (`bool`, optional, default `False`):
  - `True` — blocks and returns the current profile directly.
  - `False` — returns immediately with no data; the plugin must subscribe to the
    `polyglot.PROFILE` event to receive it.
- **Raises**: `TimeoutError` if `waitResponse=True` and no response is received from PG3/IoX.
- **Notes**: returns all nodedefs for the plugin regardless of whether it was installed with
  static profile files or dynamic profiles — this is what makes it useful for migrating an
  existing static profile to the dynamic format (see §5).

```python
profile = polyglot.getJsonProfile({ "waitResponse": True })
```

### `polyglot.updateJsonProfile(update, options)`

Updates the JSON profile with additions, replacements, or deletions.

- `update` (`dict`) — describes the changes:
  - `delete` (optional; if omitted, nothing is deleted) — per category (`editors`, `nodedefs`,
    `linkdefs`): `"*"` deletes all items in that category, or a list of ids deletes only those.
  - `editors` / `nodedefs` / `linkdefs` (any provided) — **add/replace rules**: replaces an
    existing item with the same `id`, or adds a new item if no matching `id` exists.
- `options.waitResponse` (`bool`, optional, default `False`):
  - `True` — blocks until the update completes; useful if the plugin needs to create a node
    based on the new/updated nodedef immediately after.
  - `False` — returns immediately; the plugin must subscribe to `polyglot.PROFILE` to know when
    the update has landed.
- **Raises**: `ValueError` if profile validation fails; `TimeoutError` under the same condition
  as above.

Example update (delete two specific editors, all nodedefs, all linkdefs — arrays left empty
here mean "no additions in this call"):

```jsonc
{
  "delete": {
    "editors": [ "I3_LOAD_4", "I3_ON_OFF" ], /* delete specific editors */
    "nodedefs": [ "*" ], /* delete all nodedefs */
    "linkdefs": [ "*" ]  /* delete all linkdefs */
  },
  "editors": [ /* editor definitions */ ],
  "nodedefs": [ /* node definitions */ ],
  "linkdefs": [ /* link definitions */ ]
}
```

## 3. Profile structure

### `Profile` object

| Field | Type | Description |
|---|---|---|
| `nodedefs` | `NodeDef[]` | Nodes each have a reference to a NodeDef ID. |
| `editors` | `Editor[]` | Properties and Command parameters have a reference to an editor. |
| `linkdefs` | `LinkDef[]` | NodeDefs can have a reference to LinkDefs. |

> Note: this JSON profile structure is **different** from the JSON structure the VS Code
> plugin-development extension generates (which still emits the static profile format as of
> this writing).

Profile example — one NodeDef (`CTL`) with two properties (one plain enum, one status-code
enum) and three accepted commands:

```jsonc
{
  "editors": [
    {
      "id": "online",
      "ranges": [
        {
          "uom": "25",
          "subset": "0,1",
          "names": { "0": "Offline", "1": "Online" }
        }
      ]
    },
    {
      "id": "test",
      "ranges": [
        {
          "uom": "25",
          "subset": "0-5",
          "names": {
            "0": "Not tested", "1": "Testing...", "2": "Success",
            "3": "Timeout", "4": "Failure", "5": "Auth. Failure"
          }
        }
      ]
    }
  ],
  "linkdefs": [],
  "nodedefs": [
    {
      "id": "CTL",
      "name": "Controller",
      "icon": "GenericCtl",
      "properties": [
        { "id": "ST", "editor": "online", "name": "Plugin Status" },
        { "id": "GV0", "editor": "test", "name": "Test result" }
      ],
      "cmds": {
        "sends": [],
        "accepts": [
          { "id": "DISCOVER", "native": "false", "name": "Discover devices" },
          { "id": "QUERYALL", "name": "Query All" },
          { "id": "TEST", "name": "Test" }
        ]
      },
      "links": { "ctl": [], "rsp": [] }
    }
  ]
}
```

### `NodeDef` object

```jsonc
{
  "id": "CTL",
  "name": "Controller",
  "properties": [ ... ],
  "cmds": {
   "sends": [ ... ],
   "accepts": [ ... ]
  },
  "links": {
   "ctl": [ ... ],
   "rsp": [ ... ]
  },
  "icon": "GenericCtl"
}
```

| Field | Type | Description |
|---|---|---|
| `id` | `string` | NodeDef id. |
| `name` | `string` | NodeDef name. |
| `properties` | `Property[]` | Properties for this node. |
| `cmds.sends` | `Cmd[]` | Commands the plugin can send. |
| `cmds.accepts` | `Cmd[]` | Commands the plugin can accept. |
| `links.ctl` | `LinkDef[]` | Link definitions (controllers). |
| `links.rsp` | `LinkDef[]` | Link definitions (responders). |
| `desc` | `string` (Optional) | Description |
| `icon` | `string` (Optional) | UI icon identifier. Ref: [Icons](https://developer.isy.io/docs/API/IoX/icons) |
| `customicon` | `string` (Optional) | Future use. |

### `Property` object

Defines a property of the node.

| Field | Type | Description |
|---|---|---|
| `id` | `string` | Property ID (e.g., `ST`, `GV0`). |
| `name` | `string` | Property display name. |
| `editor` | `string` | Editor ID. |
| `desc` | `string` (Optional) | Description |
| `hide` | `bool` (Optional) | If `true`, property is hidden in UI. |

### `Cmd` object

| Field | Type | Description |
|---|---|---|
| `id` | `string` | Command ID. |
| `name` | `string` | Command display name. |
| `native` | `string` | e.g. `"true"` / `"false"` — note the field is typed/rendered as a string, not JSON boolean, in the spec's own table (the example payload above uses `"native": "false"`). |
| `desc` | `string` (Optional) | Description |
| `format` | `string` (optional) | How this command is displayed in the program editor. See [Formatting](#4-formatting-in-programs-and-scenes). |
| `parameters` | `Parameter[]` (optional) | Parameter definitions for the command. |

### `Parameter` object

| Field | Type | Description |
|---|---|---|
| `id` | `string` | Parameter ID. If empty string, this is the default parameter (and the only required parameter). |
| `editor` | `string` | The editor ID used for this parameter. |
| `optional` | `bool` (optional) | If true, this parameter is optional when sending the command. |
| `init` | `string` (optional) | Property Id. If specified, the UI showing the command initializes its default value from this property. |
| `desc` | `string` (Optional) | Description |
| `name` | `string` (optional) | Rarely set — seems used only when the parameter id is non-empty. |

### `Editor` object

Editors define how values will be displayed or edited.

```jsonc
{
  "id": "mv",
  "ranges": [
    { "uom": "43", "min": 0, "max": 20000, "prec": 0, "step": 10 }
  ]
}
```

| Field | Type | Description |
|---|---|---|
| `id` | `string` | Editor ID used by node properties or commands. |
| `ranges` | `Range[]` | List of valid value ranges and constraints. |

### `Range` object

A range object can be defined using min/max, or a subset.

**Using min/max:**

| Field | Type | Description |
|---|---|---|
| `min` | `number` | Lowest value allowed. |
| `max` | `number` | Highest value allowed. |
| `prec` | `number` (Optional) | Precision: number of digits following the decimal point. |
| `step` | `number` (Optional) | Step increment the UI should use when increasing/decreasing the value. |
| `uom` | `string` | Unit of measure code. |
| `names` | `Object` (Optional) | Mapping of value to name. The object keys are the values as string. |
| `desc` | `string` (Optional) | Description |

**Using subset:**

| Field | Type | Description |
|---|---|---|
| `subset` | `string` | e.g. `"0-5,8-10,20"` |
| `uom` | `string` | Unit of measure code. |
| `names` | `Object` (Optional) | Mapping of value to name. Object keys are the numbers as string, values are the names. |
| `desc` | `string` (Optional) | Description |

### `LinkDef` object

Link definitions describe support for native links. They're scoped to a plugin and/or a
product family supported directly by the eisy (e.g. Insteon, Z-Wave, etc.).

| Field | Type | Description |
|---|---|---|
| `id` | `string` | LinkDef ID used by the NodeDef. |
| `name` | `string` | LinkDef name. |
| `protocol` | `string` | The protocol supported (may contain any alphanumeric text or underscore). |
| `cmd` | `bool` (Optional) | If true, any direct command for a responder can be used in the link (default `false`). The linkdef must not specify any parameters. |
| `format` | `string` (optional) | How this command is displayed in the scene, when used as a responder. See [Formatting](#4-formatting-in-programs-and-scenes). |
| `parameters` | `Parameter[]` (Optional) | Responders only — the parameters passed when the scene is triggered. Same shape as Cmd parameters, except `init` is not used here. |
| `desc` | `string` (Optional) | Description |

**Matching rule**: all link definitions for a responder that share the same `protocol` as
supported by a controller are available as native links between that controller and responder.
Example: INSTEON has two link protocols, `I_STD` (controllers without retry support) and
`I_STD_ADV` (controllers with retry support) — any Insteon controller supporting `I_STD` can
link to any responder supporting `I_STD`.

Example linkdefs (dimmer and thermostat responders, plus their controller-role counterparts):

```jsonc
{
  "linkdefs": [

    /* LinkDefs for Responders */

    {
      "id": "I_DIMMER", /* For dimmers where controller does not support retries */
      "protocol": "I_STD",
      "name": "Insteon",
      "format": ";OL;;${v}; ;RR;; in ${v};",
      "parameters": [
        { "id": "OL", "editor": "I_OL", "name": "On Level" },
        { "id": "RR", "editor": "I_RR", "name": "Ramp Rate" }
      ]
    },
    {
      "id": "I_DIMMER_ADV", /* For dimmers where controller does support retries */
      "protocol": "I_STD_ADV",
      "name": "Insteon",
      "format": ";OL;;${v}; ;RR;; in ${v}; ;CLNRT;;, ${v};",
      "parameters": [
        { "id": "OL", "editor": "I_OL", "name": "On Level" },
        { "id": "RR", "editor": "I_RR", "name": "Ramp Rate" },
        { "id": "CLNRT", "editor": "I_CLNRT", "name": "Retries" }
      ]
    },

    {
      "id": "I_TSTAT", /* For thermostats where controller does not support retries */
      "protocol": "I_STD",
      "name": "Insteon",
      "format": ";CLISPH;;Heat ${v}; ;CLISPC;; / Cool ${v}; ;CLIFS;; / Fan ${v}; ;CLIMD;; / Mode ${v};",
      "parameters": [
        { "id": "CLISPH", "editor": "I_CLISPH_DEG", "name": "Heat Setpoint" },
        { "id": "CLISPC", "editor": "I_CLISPC_DEG", "name": "Cool Setpoint" },
        { "id": "CLIFS", "editor": "I_TSTAT_FAN_MODE", "name": "Fan Mode" },
        { "id": "CLIMD", "editor": "I_TSTAT_MODE", "name": "Mode" }
      ]
    },
    {
      "id": "I_TSTAT_ADV", /* For thermostats where controller does support retries */
      "protocol": "I_STD_ADV",
      "name": "Insteon",
      "format": ";CLISPH;;Heat ${v}; ;CLISPC;; / Cool ${v}; ;CLIFS;; / Fan ${v}; ;CLIMD;; / Mode ${v}; ;CLNRT;; / ${v};",
      "parameters": [
        { "id": "CLISPH", "editor": "I_CLISPH_DEG", "name": "Heat Setpoint" },
        { "id": "CLISPC", "editor": "I_CLISPC_DEG", "name": "Cool Setpoint" },
        { "id": "CLIFS", "editor": "I_TSTAT_FAN_MODE", "name": "Fan Mode" },
        { "id": "CLIMD", "editor": "I_TSTAT_MODE", "name": "Mode" },
        { "id": "CLNRT", "editor": "I_CLNRT", "name": "Retries" }
      ]
    },

    /*
    LinkDefs for Controllers

        NOTE: linkdef's referenced in the <ctl> section of the <links> section of a <nodedef>
        are currently only used to indicate the 'protocol' the controller supports.
        Therefore in this example, we don't really need to distinguish between
        I_CTL_DIMMER and I_CTL_RELAY (we could just have I_CTL & I_CTL_ADV).
    */

    { "id": "I_CTL_DIMMER", "protocol": "I_STD", "name": "Insteon" },
    { "id": "I_CTL_DIMMER_ADV", "protocol": "I_STD_ADV", "name": "Insteon" },
    { "id": "I_CTL_RELAY", "protocol": "I_STD", "name": "Insteon" },
    { "id": "I_CTL_RELAY_ADV", "protocol": "I_STD_ADV", "name": "Insteon" }
  ]
}
```

A `cmd: true` linkdef, used when any direct command works for the responder (no fixed
parameter list) — a Z-Wave association command:

```jsonc
{
  "id": "ASSOC_CMD",
  "protocol": "ASSOC_CMD",
  "name": "Z-Wave Association Command",
  "cmd": true
}
```

## 4. Formatting in Programs and Scenes

Each line of a program is formatted and displayed in different ways. Custom formatting via the
`format` field is used for node conditions and commands.

Pattern: `/<param.id>/param text if omitted/param text if not omitted/ [... next parameter, ...]`

- The **first character** of the format string defines the separator (normally `/`).
- `param.id` — id of the parameter (e.g. `'level'`); blank for an unnamed parameter.
- **param text if omitted** — string shown if the parameter was omitted.
- **param text if not omitted** — string shown if the parameter was specified.

Variables usable inside the text segments:

| Variable | Meaning |
|---|---|
| `${c}` | Name of the command |
| `${v}` | Formatted value of the parameter (including UOM) |
| `${vo}` | Formatted value of the parameter (without UOM) |
| `${uom}` | Formatted UOM without the value |
| `${op}` | Operator used (conditions only) |

Example: `/level/${c}/to ${v}/ /ramprate// in ${v}/`

### Command Formatting Examples

Assume commands are for node `'MyDevice'`.

**1) A command with three named parameters** (`num`, `val`, `len`):

Format: `/num//${c} Parameter ${v}/ /val/ default/ = ${v}/ /len// (${v} bytes)/`

| Input | Rendered program action line |
|---|---|
| `num=1, len=4` (Config) | `Set 'MyDevice' Config Parameter 1 = 20 (4 bytes)` |
| `num=5` (Config) | `Set 'MyDevice' Config Parameter 5 = 25` |
| `num=5, len=2` (Device) | `Set 'MyDevice' Device Parameter 5 default (2 bytes)` |

**2) A command with one unnamed parameter**:

Format: `//default/${v}/`

- Omitted: `Set 'MyDevice' default`
- `= 50 percent`: `Set 'MyDevice' 50%`

**3) Another format example**:

Format: `/level/${c}/to ${v}/ /ramprate// in ${v}/ /offtimer//, turn off ${v} later/`

`level=50%, ramprate=3 seconds, offtimer=5 minutes` → `Set 'MyDevice' to 50% in 3 seconds, turn off 5 minutes later`

## 5. Migration from static profiles to dynamic profiles

If a plugin uses the static profile format, the easiest migration path is to retrieve the
existing profile in JSON and update the plugin to use that format going forward.

### 1. Retrieve the existing profile in the JSON format

Add this function and call it before `polyglot.ready()`:

```python
def get_profile():
    time.sleep(5)
    LOGGER.info('--- Get existing profile ---')
    profile = polyglot.getJsonProfile({ 'waitResponse': True})
    print(json.dumps(profile))
```

(Import `time` if not already imported.) The delay matters: on startup, PG3 uploads the profile
files from the plugin's profile folder; the delay ensures they've been uploaded and processed
by IoX before fetching. Start the plugin, check the log, and copy the printed profile somewhere
temporary — pretty-print it with an online tool if useful.

### 2. Update the plugin to use the JSON profile format

Use the profile captured in step 1.

**Load the profile into a variable** — either inline in code, or from a file:

```python
with open(os.path.join(os.path.dirname(__file__), 'profile.json')) as f:
    profile = json.load(f)
```

**Send the profile to IoX** — call this on startup, before `polyglot.ready()`:

```python
def update_profile():
    LOGGER.info('Updating profile in JSON format.')
    polyglot.updateJsonProfile(profile, {"waitResponse": True})
    print('JSON profile updated successfully.')
```

**Stop using the static profile format**: remove any reference to `polyglot.updateProfile()`
or `polyglot.checkProfile()`. Then rename or remove the plugin's profile folder — this step is
**CRITICAL**: if the profile folder still exists, PG3x will send it to IoX on startup, and if
that races a JSON profile update, results are unpredictable.

### 3. Enhance the plugin to use fully dynamic profiles

Once on the JSON profile format, use `polyglot.updateJsonProfile()` to add or remove individual
nodedefs or editors — the entire profile does not need to be resent on each update, only the
changes. For example, call `updateJsonProfile()` any time a new device is discovered, creating
the nodedef and then the new node immediately after.

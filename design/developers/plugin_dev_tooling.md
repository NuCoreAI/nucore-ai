# Developer tooling for plugin authoring — research + recommendation

> Research + recommendation, not a reference of shipped tooling — most of what this doc
> discusses is early/low-profile, not something nucore-ai itself builds or ships today. See
> [`plugin_concepts_and_lifecycle.md`](plugin_concepts_and_lifecycle.md) §2/§3 (Stage 0, the
> developer lifecycle) for where this fits: "test locally" and "publish to the production
> store" are two undocumented, unbuilt steps in that lifecycle, and this doc is about what
> tooling would actually make that lifecycle usable.

## 1. Ecosystem research

Researched live (GitHub + UDI's own wiki/PyPI/VS Code Marketplace), not just from internal
docs, since the actual plugin-developer experience lives entirely outside this repo, in
Universal Devices' Polyglot/PG3 ecosystem.

**Official and example repos found**:
- [`UniversalDevicesInc/udi-poly-template-python`](https://github.com/UniversalDevicesInc/udi-poly-template-python)
  — the official Python starter template. 15 files, ~34KB: an entry-point script, a
  `TemplateController`/`TemplateNode` class pair subclassing `udi_interface.Node`, static
  `profile/nodedef/nodedefs.xml` + `profile/editor/editors.xml` + `profile/nls/en_us.txt`,
  `server.json`, `requirements.txt`, `install.sh`.
- [`UniversalDevicesInc/udi_python_interface`](https://github.com/UniversalDevicesInc/udi_python_interface)
  — source of the `udi_interface` [PyPI package](https://pypi.org/project/udi-interface/)
  (~3.4.x). Its `API.md` documents `updateJsonProfile()`/`getJsonProfile()` (the Dynamic
  Profiles API — see [`plugin_model.md`](plugin_model.md)).
- [`bpaauwe/udi-owm-poly`](https://github.com/bpaauwe/udi-owm-poly) — a community plugin
  (OpenWeatherMap), 20 files, ~73KB, same subclass pattern.
- [`UniversalDevicesInc-PG3/udi-poly-kasa`](https://github.com/UniversalDevicesInc-PG3/udi-poly-kasa)
  and the wider [`UniversalDevicesInc-PG3`](https://github.com/UniversalDevicesInc-PG3) org —
  dozens of community plugins (Harmony, Ecobee, Kasa, MQTT, Tahoma, Hunter Douglas, Hue
  emulator, ELK, Somfy, ...) that UDI hosts under its own umbrella rather than leaving fully
  third-party.
- UDI's wiki has a practical cluster of pages:
  [Node server Tutorial](https://wiki.universal-devices.com/Node_server_Tutorial),
  [Testing and debugging node servers](https://wiki.universal-devices.com/Testing_and_debugging_node_servers),
  [Packaging a node server for distribution](https://wiki.universal-devices.com/Packaging_a_node_server_for_distribution).

**Correction on first pass**: an initial general-terms search ("polyglot vscode extension",
"node server scaffolding") turned up nothing and led to a "no scaffolding tool, no VS Code
extension exists" conclusion. A targeted search by exact name found that's wrong — see §2.

## 2. Existing UDI tooling: `ioxplugin` + `iox-vscode-plugin`

Both are real, actively maintained, and — this matters for how to read the rest of this
doc — **appear to be your own tooling**: sole GitHub commit author on both repos is "Michel
Kohanim," PyPI author is `Michel Kohanim <support@universal-devices.com>`, and the VS Code
Marketplace publisher is "Universal Devices." That email domain and name match this
conversation's user.

**Decision: both are frozen, not extended.** Per the team, the static-XML profile format these
tools generate is itself runtime-obsolete — at runtime, IoX regenerates any static profile
files a plugin ships into Dynamic Profiles and removes the static files. So static-XML
generation isn't a format worth continuing to invest in; it's a bootstrapping shim the platform
already routes around. `ioxplugin` and `iox-vscode-plugin` stay exactly as they are, serving
existing projects that still install/build against them, and get no further Dynamic-Profiles
work. A new, Dynamic-Profiles-focused core is being built instead (§4, §6) — reviewed against
both tools' source and *selectively* carrying forward what's still relevant, not extending or
forking either one wholesale.

### `ioxplugin` (Python package)

- [PyPI](https://pypi.org/project/ioxplugin/): v1.6.2 (latest as of this research), **41
  releases** back to v0.1.0, requires Python ≥3.9.
- [GitHub](https://github.com/universaldevices/ioxplugin): 106 commits, created 2024-05-03,
  **last push 2026-08-11** — actively maintained, not abandoned.
- License: repo's `LICENSE` — "Universal Devices Free/MIT License — free to use/distribute/
  share/modify with no attribution, AS-IS" (permissive, MIT-like).
- **What it generates**, confirmed by reading `ioxplugin/iox_profile.py`, `nodedef.py`,
  `iox_node_gen.py`, `main_gen.py`: the **classic static PG3 profile tree** —
  `profile/nodedef/nodedef.xml`, `profile/editor/editor.xml`, `profile/nls/en_us.txt` — plus
  **Python stub source files**, one per nodedef, AST-generated (via `astor`/`ast`) subclassing
  `udi_interface.Node`, with a `drivers` list and a controller template.
- **It does not touch the Dynamic Profiles JSON format at all** — it's purely a static
  XML/NLS + Python-stub generator. This is the same static-XML pattern every real-world plugin
  inspected in §1 uses; `ioxplugin` is presumably *why* — it's the officially-supported path,
  and it targets the older format.
- **Input**: a single `*.iox_plugin.json` file. Top-level keys: `plugin` (metadata — name,
  executableName, publisher, version, polling intervals, OAuth flags, `requirements`,
  `nodesAreStatic`, etc.), `editors` (reusable UOM/min/max/step/precision/index_names
  definitions), `nodedefs` (array: `id`, `name`, `parent`, `icon`, `properties[]` each with
  `id`/`name`/`is_settable`/`editor`, and `commands.accepts[]`/`commands.sends[]` with
  `params[]`). Concrete real examples in the repo:
  [`tests/dimmer.iox_plugin.json`](https://github.com/universaldevices/ioxplugin/blob/main/tests/dimmer.iox_plugin.json),
  `tests/modbus.iox_plugin.json`.
- **Note**: `plugin.py`'s `validate_json()` currently short-circuits and always returns `True`
  — real JSON-Schema validation inside the Python package itself is stubbed out (the code notes
  `fastjsonschema` doesn't support file references, so it skips validation). In practice,
  validation happens client-side, in the VS Code extension below.

### `iox-vscode-plugin` ("IoX Plugin Developer")

- [GitHub](https://github.com/universaldevices/iox-vscode-plugin): ~80+ commits, created
  2024-07-24, last push 2026-06-02.
- [VS Code Marketplace](https://marketplace.visualstudio.com/items?itemName=UniversalDevices.iox-plugin-ext):
  publisher "Universal Devices," v2.1.6, 83 installs, no ratings yet.
- License: MIT, plus a short note — "You can use and distribute code generated by this
  extension."
- **What it provides** (from `package.json`): a sidebar activity-bar view ("IoX Plugin
  Developer"), JSON-Schema-backed validation/autocomplete on any `*.iox_plugin.json` file
  (`contributes.jsonValidation` → `./schemas/plugin.schema.json`), 5 snippet templates (`none`,
  `tstat`, `dimmer`, `sensors`, `modbus`), and 5 commands that **wrap `ioxplugin`'s codegen**:
  Create Project → Generate Plugin Code → Add to Store & Install → Package and Publish → Help.
- This is exactly the "companion" tool described: a JSON-authoring UI with schema-driven valid
  values, calling `ioxplugin` to generate stub code.

### The JSON schemas — the highest-value find for reuse

All raw files live under
[`schemas/`](https://github.com/universaldevices/iox-vscode-plugin/tree/main/schemas) in the
`iox-vscode-plugin` repo (raw URL pattern:
`https://raw.githubusercontent.com/universaldevices/iox-vscode-plugin/main/schemas/<file>`):

| File | Covers |
|---|---|
| `plugin.schema.json` | Root: `{plugin, protocol, editors[], nodedefs[]}`; `if/then/else` switches `nodedefs[].$ref` to `modbus.node.schema.json` when `protocol.name == "Modbus"`, else `node.schema.json`. |
| `plugin.meta.schema.json` | Plugin metadata — name, publisher, executableName, version, polling, OAuth2, hardwareConfig, `requirements`, `nodesAreStatic`, `profileVersion: "3.0.0"` (const). |
| `common.node.schema.json`, `node.schema.json`, `node.properties.schema.json` | Node `id/name/parent/icon/commands`; properties array (`id` pattern `^[A-Z][A-Z0-9_]*$`, `name`, `is_settable`, `editor`, `hide`). |
| `editor.schema.json` | `oneOf` of `{idref}` / `{id,uom,min,max,step,precision,index_names}` / `{id,uom,subset,index_names}`, with conditional branches pulling in per-UOM index-name schemas (`uom.11/15/66/67/68/76/80/93-99/115.schema.json`) for enumerated UOMs. |
| `uom.schema.json` | Full enum of **~140 UOM strings**, formatted `"<Description> \| <numeric id>"` (e.g. `"Percent \| 51"`, `"Watt \| 73"`) — directly matching ISY's numeric UOM table (same table `nucore_domain_model.md` §8 documents from the consumption side). |
| `commands.schema.json` | `{accepts[], sends[]}`, each `{id, name, params[]}`, `params[].editor` reusing `editor.schema.json`. |
| `properties.schema.json` | Generic ID-string constraint (`^[A-Z][a-zA-Z0-9]*$`, max 30). |
| `modbus.node*.schema.json`, `protocol.modbus/generic.schema.json`, `serial.schema.json`, `tcp.schema.json` | Protocol-specific extensions (Modbus/serial/TCP transports). A `schemas/old/` folder shows a prior schema iteration. |

### What the new core actually carries forward

Given both tools are frozen (not extended), "reuse" means reviewing their source and keeping
only what's still relevant to a Dynamic-Profiles-focused, LLM-driven tool — not porting either
codebase wholesale. Per the team, this is deliberately a small list:

- **Keep: the JSON Schemas** (`iox-vscode-plugin`'s `schemas/` directory, table above) — the
  single highest-value asset here, primarily the `uom.schema.json` enum (a ready-made,
  UDI-authoritative UOM table) and the general editor-shape pattern (`oneOf` of idref/minmax/
  subset). The top-level dialect these schemas validate (`plugin/editors/nodedefs` with inline
  `editor` objects, `is_settable`, `idref`) is `ioxplugin`'s own bespoke shape, not the Dynamic
  Profiles shape (`nodedefs`/`editors`/`linkdefs` with `id`/`name`/`desc`/`properties`/`cmds`/
  `links`, `plugin_model.md` §3) — so these get restructured to validate the new shape, not
  copied as-is.
- **Drop: the `ast`/`astor`-based Python code-generation engine** (`iox_node_gen.py`,
  `main_gen.py`) entirely. Per the team: an LLM can generate the Python stub code directly from
  a description/schema, so there's no reason to carry forward a deterministic AST-templating
  engine for that job — it's exactly the kind of code an AI-assisted flow (front-end 3, and
  eventually a CLI) makes unnecessary. This removes a meaningful chunk of `ioxplugin`'s own
  complexity from what needs to exist going forward at all.
- **Drop: the static-XML/NLS emission layer** (`iox_profile.py`, `nodedef.toIoX()`) — not
  ported, not adapted. It stays exactly where it is, frozen, in the original `ioxplugin`, doing
  the job it already does for existing projects.
- **Not carried forward as code, but worth knowing existed**: `ioxplugin`'s JSON *parsing/model*
  classes (`NodeDefDetails`, `Editors`, `UOMs`, `Commands`) — reading them was useful context for
  understanding the domain, but the new core's input/output shape is different enough (Dynamic
  Profiles, not `ioxplugin`'s dialect) and the codegen step is LLM-driven rather than
  class-driven, so there's no clean seam to reuse them directly rather than writing new,
  smaller model code against the new shape.
- **`iox-vscode-plugin`'s wiring pattern** (`contributes.jsonValidation` + snippets) is worth
  knowing as a reference for *if* a VS Code front-end for the new core is ever built (§4, §6) —
  but that's explicitly not in scope now, so nothing from that extension is being touched or
  imported today.

## 3. Difficulty assessment

**Moderate-to-high — but not because the programming model itself is hard.** The core pattern
across every example repo inspected is a simple subclass: a `drivers` list (e.g.
`[{'driver':'ST','value':1,'uom':2}]`), a `commands` dict mapping ISY command names to Python
methods, and a `poll(flag)` method checking `'longPoll'`/`'shortPoll'`. That's roughly a day's
work to understand for anyone comfortable in Python. The real friction is environmental:

- **No testing *without* a physical hub exists anywhere — official or community.**
  `iox-vscode-plugin` does provide a real breakpoint/step-through debug loop for the generated
  code (see §4's legacy front-ends, §5) — but even that loop needs a live eisy connection; nothing
  simulates or mocks the hub side. UDI's own
  "Testing and debugging node servers" wiki page documents the actual workflow: Samba-mount the
  plugin's filesystem from a Windows IDE, use a "Local" node-server store entry with a `SYM://`
  symlink URL so edits are picked up live without reinstalling — against a real Polisy/eisy
  device. The page itself has a `TODO: Add steps to set up Samba`, i.e. even the official docs
  are incomplete here. The one test script found in `udi_python_interface` (`scripts/tests.py`)
  instantiates a real `udi_interface.Interface` object rather than mocking anything — not a
  usable offline test harness. Polyglot Cloud
  ([`pgc-nodejs-interface`](https://github.com/UniversalDevicesInc/pgc-nodejs-interface)) is a
  hosted alternative, but still requires linking a real ISY/eisy account — not hardware-free
  either.
- **Real-world plugins still hand-author/generate static XML profiles**, despite the better
  Dynamic Profiles JSON API being documented in `udi_interface`'s own `API.md`. Every example
  repo inspected (the official template included) ships `profile/nodedef|editor/*.xml`, not a
  call to `updateJsonProfile()` — and now that §2 identifies `ioxplugin` as the tooling actually
  in use, this makes sense: the tool that exists targets the older format, so that's what gets
  produced even when developers use tooling instead of hand-writing XML.
- **Direct evidence hand-authoring XML is real pain**: `bpaauwe/udi-owm-poly` includes a
  hand-written `write_profile.py` (6.8KB) that the author built himself to *generate*
  `nodedef.xml`/`editors.xml` from Python dicts. `ioxplugin`/`iox-vscode-plugin` (§2) are the
  officially-supported answer to exactly this problem — evidently not widely discovered by the
  community, since `bpaauwe` built his own rather than using them.
- **A scaffolding tool does exist**, just not under generic search terms — `ioxplugin` +
  `iox-vscode-plugin` (§2) cover "Create Project" → JSON authoring with schema validation →
  codegen → package/publish. The gap isn't "no tool," it's "the tool targets the older static
  profile format, not Dynamic Profiles."

## 4. One shared core, multiple thin front-ends — not a UI-vs-UI trade-off

The earlier framing of this section ("Option A: eisy-ai UI" vs. "Option B: VS Code extension")
was the wrong shape for the question, and the version after that (extend `ioxplugin` +
`iox-vscode-plugin` as two of three front-ends) is superseded too, now that both are frozen
(§2). Per the team, this should still follow the architecture Claude Code itself uses — one
UI-independent core, multiple thin front-ends, each serving a different population rather than
competing for the same one — but the core is a **new**, Dynamic-Profiles-focused artifact, not
the frozen `ioxplugin`.

### Legacy front-ends — frozen, unchanged, serving existing projects only

`ioxplugin`'s own CLI and `iox-vscode-plugin` continue to exist exactly as they are today, for
whoever already depends on the static-XML format. No Dynamic-Profiles work happens in either.

### Front-end 3 — a web/AI-assisted flow in eisy-ai (new, the primary near-term target)

Confirmed as the first thing to build. Serves **non-technical users building a local,
not-necessarily-published plugin** — the population neither legacy tool nor a future VS Code
front-end serves well. Feasible for a concrete reason: eisy-ai already runs hub-side with
direct PG3/IoX access (it's deployed as a Polyglot node-server itself, `runtime_plugin.md` §1).
For this persona, the "needs a live eisy" limitation (§5) isn't a limitation at all — they
already own the hub eisy-ai is running on. A conversational flow — describe the device, the LLM
generates the plugin's Dynamic Profiles JSON *and* its Python stub code directly (no AST
codegen step, §2), installs and runs the result on the same local hub — goes from "I have a
sensor" to "it's running," entirely in-browser, with no VS Code, no CLI, and no remote/
simulator infrastructure needed, because the hub is already local to begin with.

### Front-end 1-new — a CLI for the new core (confirmed in scope, not yet built)

Also confirmed: the new core should expose its own CLI alongside the web flow, so shell-only/
JetBrains/non-VS-Code developers get Dynamic-Profiles support too, rather than being left on
the frozen static-XML tool indefinitely. Sequencing-wise this follows front-end 3, since it
shares the same underlying core (§6), but it's explicitly scoped in, not deferred.

### A VS Code front-end for the new core — explicitly deferred

Since `iox-vscode-plugin` is frozen, VS Code support for Dynamic Profiles isn't "extend the
existing extension" — it would be a new extension, built against the new core, whenever it's
prioritized. Not scoped now. If and when it happens, `iox-vscode-plugin`'s wiring pattern
(`contributes.jsonValidation` + snippets, §2) is a reasonable reference for how to structure it.

**The structural payoff, once front-end 1-new exists too**: because every front-end over the
new core reads and writes the *same* artifact (a Dynamic-Profiles-shaped project directory), a
user who starts in eisy-ai's web flow can download/clone that project and pick it up from the
CLI, or later a VS Code front-end, with nothing to convert — same file, same schema, same
codegen approach. That's requirement 3 (web → VS Code, ready to go) satisfied by construction
once both front-ends exist, not by building an export/import feature.

## 5. Cross-cutting finding: the personal-hardware gap

**Correction to the original framing of this section**: the initial research pass concluded no
local test/debug capability existed anywhere, including in `iox-vscode-plugin`. Per the team,
that overstated the gap — `iox-vscode-plugin` already generates VS Code launch/debug
configuration for the `ioxplugin`-generated project, so a developer can set breakpoints and step
through the actual running plugin code today, not just edit and validate JSON. (This session's
own follow-up check found the repo structure — `code/new_project.py`, `code/install_on_iox.py`,
a compiled `extensions/extension.ts` — but didn't trace the exact debug-config generation to a
specific source line in a quick pass; this is recorded here as the team's own firsthand account
of how the tool works, not independently line-verified.)

The narrower, still-real gap: **that debug loop requires a live connection to a real eisy** —
UDI's own wiki-documented Samba-mount workflow and `ioxplugin`/`iox-vscode-plugin`'s run/debug
flow both still assume a physical hub is reachable. No offline/simulated PG3 endpoint exists
anywhere, official or community — every source checked (UDI's wiki, `udi_python_interface`,
every example repo, and the two tools in §2) confirms this specific piece, with zero
counterexamples found.

**This gap is scoped to CLI/IDE-based development (§4's legacy front-ends, the new core's own
CLI, and any future VS Code front-end), not front-end 3.** For a developer working from a
terminal or an IDE without their own hub on hand, it's real and unsolved. For front-end 3's
persona — a non-technical user building a local plugin *for their own eisy*, which is the hub
eisy-ai is already running on — it isn't a gap at all; the "live eisy" is already right there.

For CLI/IDE-based development, two fixes are both plausible, and not mutually exclusive:
- **A simulator**, since the whole stack (`udi_interface`, `ioxplugin`, PG3's own
  node-server-facing protocol) is Python — it would need to speak whatever `udi_interface`
  expects (MQTT/websocket-based) well enough to stand in for a real eisy.
- **Remote/shared access to a real hub**, the same pattern Claude Code's own Remote Control
  uses — decouple the debug *client* (VS Code, wherever it's running) from the execution
  environment (a real eisy), so a developer without personal hardware attaches to a pooled dev
  unit over the network instead of requiring one on their own LAN. This avoids building and
  maintaining a protocol-emulation engine at all, at the cost of needing shared hardware/network
  access instead.

Either way, this — enabling CLI/IDE-based developers to develop and debug without personal
hardware — remains the highest-leverage piece of net-new infrastructure identified in this
research, and deserves its own design regardless of which fix is chosen.

## 6. Recommendation

Confirmed scope and sequencing, per the team:

1. **Build a new, Dynamic-Profiles-focused core** — reviewed against `ioxplugin`/
   `iox-vscode-plugin`'s source (§2) and carrying forward only what's still relevant: the JSON
   Schemas (restructured to validate the Dynamic Profiles shape, not `ioxplugin`'s dialect), and
   nothing else code-wise. Explicitly **not** an extension of `ioxplugin` — both it and
   `iox-vscode-plugin` stay frozen (§2). Two deliberate simplifications versus the frozen tool:
   no `ast`/`astor` code-generation engine (an LLM generates the Python stub code directly), and
   no static-XML/NLS emission path at all (Dynamic Profiles only — the static format is
   runtime-obsolete, §2).
   > **Schemas landed**: the JSON Schema piece of this step is done — see
   > [`src/nucore/schemas/`](../../src/nucore/schemas/README.md) (restructured onto
   > [`plugin_model.md`](plugin_model.md) §3's object model, not copied from `iox-vscode-plugin`
   > as-is; that file's README covers what was kept/dropped/authored fresh and why). No validator
   > is wired into `validate_profile` yet — the schemas exist as a reference/reusable artifact for
   > whichever front-end (§4) ends up needing one.
2. **Build front-end 3** — the web/AI-assisted flow in eisy-ai — on top of that new core. This
   is the primary near-term target: it's the one population (non-technical, local-only) that
   nothing existing serves, and it's uniquely cheap to build well *because* eisy-ai already has
   hub-side IoX access — no remote-hardware or simulator problem to solve for this persona.
3. **Build a CLI for the new core** — confirmed in scope, sequenced after front-end 3 since it
   shares the same core, so shell-only/JetBrains/non-VS-Code developers get Dynamic-Profiles
   support without being left on the frozen static-XML tool indefinitely.
4. **A VS Code front-end for the new core is explicitly deferred** — not "extend
   `iox-vscode-plugin`" (frozen), a new extension if and when it's prioritized. Not scoped now.
5. **Design the personal-hardware gap fix** (§5 — simulator or shared/remote real-hub access)
   for CLI/IDE-based development, as its own priority, independent of steps 1-3. It doesn't
   block any of them, and both a simulator and a remote-access approach are legitimate options
   worth evaluating on their own merits (engineering cost and fidelity to real hub behavior vs.
   shared-hardware ops burden) rather than defaulting to either.

Steps 1-3 are one continuous effort, not separate decisions — the core exists to serve front-end
3 first, then the new CLI. Step 4 is a real future possibility, not a current commitment. Step 5
runs independently and doesn't gate anything above it.

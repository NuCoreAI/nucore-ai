# Conversational plugin authoring — an end-to-end pipeline design

> Design proposal, not shipped. This fleshes out "Front-end 3" from
> [`plugin_dev_tooling.md`](plugin_dev_tooling.md) §4/§6 (already confirmed as the primary
> near-term target) into a concrete, stage-by-stage pipeline with explicit error/retry semantics.
> Nothing described here exists yet. Read [`plugin_concepts_and_lifecycle.md`](plugin_concepts_and_lifecycle.md)
> first if you haven't — this document assumes its vocabulary (`NodeDef`/`Editor`/`LinkDef`,
> Stage 0-6 of the plugin lifecycle) without re-explaining it.

## 0. The goal, restated precisely

A chatbot lets a non-technical user describe a device or service they want to control, and the
system, without further hand-holding:

1. **Authors a plugin** — a Dynamic Profiles device model (`plugin_model.md`) plus a Python
   backend — grounded in whatever public API documentation exists for that device/service.
2. **Installs a local copy on the user's own eisy** (the hub eisy-ai is already running on —
   `plugin_dev_tooling.md` §4's "Front-end 3" persona).
3. **Runs pytest against the generated plugin in simulation** to catch bugs before anything talks
   to the real hub.
4. **Starts the plugin** (so its service comes up under `eisyui`, the same way any Polyglot
   node-server does — see [`runtime_plugin.md`](runtime_plugin.md) §1) **and exercises it live**
   over the real IoX API: send a few real commands, read back the resulting property values,
   confirm they match what was asked for.

Three named failure modes, each terminal for that attempt:

- **E1 — no usable public API.** Nothing found on the web can actually implement what the user
  asked for (no API at all, or only a private/undocumented one with no accessible credentials).
- **E2 — pytest never passes.** After 3 fix attempts, the generated plugin's own tests still fail.
- **E3 — live validation never passes.** After 3 fix attempts, commands sent to the *running*
  plugin on the real hub don't produce the expected property values.

## 1. What already exists to build this on

This is not a green field — most of the plumbing this pipeline needs is already shipped,
elsewhere in this repo:

| Need | Already exists as |
|---|---|
| A natural-language chat loop with tool-calling | `unified.loop.AgenticLoop`, already reused by `unified.dev_tools` (`src/unified/dev_tools/README.md`) for a plugin-*developer*-facing chat |
| Structural validation of a generated Dynamic Profile | `validate_profile` (`src/unified/dev_tools/handlers/profile_authoring.py`) — local, hub-free, parses with `nucore`'s own loader. Plus the JSON Schemas under `src/nucore/schemas/` (not wired into `validate_profile` yet — see [`nucore_domain_model.md`](nucore_domain_model.md) §16) |
| UOM lookup while authoring editors | `lookup_uom`, same package |
| Configuring an installed plugin (API keys, polling interval, ...) | `configure_plugin` (reused by `dev_tools` from `unified.handlers.plugin_management`) |
| Starting/stopping the plugin's own service | `plugin_ops(start/stop/restart)` |
| Calling into a plugin's own declared LLM tools, post-install | `get_plugin_capabilities` / `call_plugin` |
| Sending an ordinary device command and reading the result | The **standard** nucore command-dispatch path — `resolve_command_id` → `.../rest/nodes/{device_id}/cmd/{command_id}` — a plugin-originated node is not a special case here (`nucore_domain_model.md` §4, §14; `plugin_concepts_and_lifecycle.md` §3 Stage 5a) |
| A live hub to test against, with no simulator gap | eisy-ai already runs hub-side with direct PG3/IoX access — `plugin_dev_tooling.md` §4/§5 already establishes this persona has no "no hardware" problem, unlike CLI/IDE-based plugin development |
| The wire format to generate | `plugin_model.md` §3, in full |
| The programming pattern to generate code against | `plugin_dev_tooling.md` §1/§3 — a `udi_interface.Node` subclass, a `drivers` list, a `commands` dict, a `poll(flag)` method; "roughly a day's work to understand," not the hard part |

So the net-new work is smaller than "build a plugin pipeline from scratch" — it's: one new
research capability (§3.2), one new codegen step grounded in what's already documented (§3.3), a
retry-aware orchestrator sitting above the existing tool-calling loop (§5), and — the one real
blocker — §2 below.

## 2. The blocking gap: there is no automatable local-install path today

This has to be resolved before Stage 4 (§3.4) can be built as described, so it's called out here,
first, rather than buried in a stage.

`design/developers/plugin_dev_tooling.md`'s own "Recommendation" already names *why* this
pipeline is even feasible for this persona — but confirm the install step itself:
`unified.dev_tools`'s own README states it plainly:

> `install_plugin`'s handler is a purchase-flow stub that returns a URL rather than installing
> anything — not a real local-dev install path.

That's the **only** install mechanism anywhere in this codebase (`runtime_plugin.md` §4's
"dangerous-action pattern": `install_plugin`/`buy_plugin`/`delete_plugin` all hand back a URL for
a human to finish, deliberately, because those actions are irreversible/purchase-related).

Separately, `plugin_concepts_and_lifecycle.md` §3 Stage 0 names the step this pipeline actually
needs — **"test locally — publish to a local plugin store running on the developer's own
machine"** — and says outright: *"Steps 2 and 3 each need their own document; neither exists in
this folder today."* This pipeline's Stage 4 **is** that undocumented step, made concrete for the
first time.

Two ways forward, not mutually exclusive:

- **(a) Build a real local-store install path.** PG3's own local-store mechanics (the same thing
  UDI's "Local" node-server store entry / `SYM://` workflow in `plugin_dev_tooling.md` §3 points
  at) may already expose something scriptable outside this repo — worth checking
  developer.isy.io / `udi_interface`'s own docs before assuming new platform work is required.
  If it does, that's a new `NuCoreInterface` method + `IoXWrapper` implementation, same shape as
  every other plugin-lifecycle method in `runtime_plugin.md` §2-3.
- **(b) Keep the human-in-the-loop precedent.** Treat local install the same way
  `install_plugin` already treats marketplace install: the chatbot hands the user a link/step and
  waits, polling `list_installed_plugins` for completion, rather than the pipeline installing
  anything directly. This is less "automatic" than the stated goal, but it's consistent with the
  deliberate design choice `runtime_plugin.md` §4 documents for every other irreversible plugin
  action, and it ships without new platform-side work.

**Recommendation:** start with (b) — it's buildable today with zero new platform surface — and
scope (a) as a separate follow-up once it's clear how much friction (b) actually causes for a
non-technical user mid-conversation. Don't block the rest of this design on resolving it; every
other stage is independent of which option wins here.

## 3. The pipeline, stage by stage

### 3.1 Stage 1 — Conversational intake

Reuse the `AgenticLoop` + a `dev_tools`-shaped tool set, but with a system prompt aimed at "what
do you want this plugin to do," not "help me author a profile" — the target user doesn't know
what a `NodeDef` is. The chatbot's job here is to turn a vague ask ("I want to control my
[whatever] from IoX") into a structured **plugin brief**: target device/service, desired
capabilities (read status / send commands / both), any account or API-key the user already has,
a human-friendly name for the resulting node type. This is ordinary LLM-conversation work, no new
infrastructure — the interesting design work starts at 3.2.

### 3.2 Stage 2 — API feasibility research (→ E1)

New capability: a bounded web-research step (search + fetch, capped at some fixed number of
queries/pages so "no API found" is a decision, not just "gave up after one search") that looks
for: official API docs, auth model, the specific endpoints needed to cover the brief's requested
capabilities. Output is an **API contract summary** — endpoints, auth, request/response shapes —
that Stage 3 generates code against.

**E1 fires here**: if research turns up no publicly reachable API that can satisfy the brief (no
API exists, or only a private/reverse-engineered one with no accessible credentials), the
pipeline stops before ever generating a plugin. Report back to the user in plain language what's
missing — "X doesn't expose a documented API for that" — not a stack trace.

### 3.3 Stage 3 — Plugin authoring

Two generated artifacts, both grounded in `plugin_model.md` §3's exact shapes:

- **The Dynamic Profile JSON** (`nodedefs`/`editors`/`linkdefs`) describing the device model —
  run through `validate_profile` immediately (and the schemas under `src/nucore/schemas/`, per
  `nucore_domain_model.md` §16 — worth wiring an actual validator call for this pipeline even
  though nothing in the codebase does today) before ever leaving this stage.
- **The Python backend** — an `udi_interface.Node` subclass calling the Stage 2 API contract,
  per the pattern in `plugin_dev_tooling.md` §1/§3. LLM-authored directly, no AST codegen step —
  `plugin_dev_tooling.md` §6 already made this exact simplification deliberately ("an LLM can
  generate the Python stub code directly from a description/schema").

Structural failures here (schema violations, `validate_profile` errors) get fixed in a tight
local loop before anything is installed — cheap, offline, no hub or retry budget consumed.

### 3.4 Stage 4 — Local install (see §2's gap)

Once the install path is resolved (§2), this stage gets the plugin onto the user's eisy and
resolves its `plugin_id` via `list_installed_plugins`, same as every other plugin-lifecycle tool
already does (`_get_plugin_number`'s id-resolution guard, `runtime_plugin.md` §2).

### 3.5 Stage 5 — pytest simulation (→ E2)

**Scope this precisely, because "simulation" is ambiguous given `plugin_dev_tooling.md` §5's own
finding:** *no offline/simulated PG3 endpoint exists anywhere* — that gap is real, but it's scoped
to CLI/IDE-based development, not this persona (§5 there says so explicitly: "for front-end 3's
persona ... it isn't a gap at all; the 'live eisy' is already right there"). So this stage is
**not** a hub simulator. It's ordinary unit/integration testing of the generated plugin's own
Python logic, with the *target service's* external API mocked (recorded fixtures /
`responses`-style stubbing) — verifying command handlers translate correctly, property
parsing/formatting matches the declared editors/UOMs, and the plugin's own error handling covers
the external API's failure responses. The hub is not involved yet.

LLM generates the pytest file(s) alongside the plugin code in Stage 3 (or as an immediate
follow-up sub-step). Retry loop: run pytest → on failure, feed the failing output back to the LLM
to patch plugin code or tests → re-run. **Cap: 3 attempts total.** On the 3rd failure (**E2**):
stop, surface the last failure and the attempt history to the user, do not proceed to Stage 6.

### 3.6 Stage 6 — Run + live IoX validation (→ E3)

1. Configure (if the target API needs credentials) and `plugin_ops(start)` — reusing the
   already-shipped tools verbatim.
2. Wait for the plugin's nodedefs to land in the hub's catalog and for a live node/device
   instance to exist — per the worked example in `plugin_model.md` §3, this is typically the
   plugin's own `DISCOVER` command creating the node. Needs an explicit "ready" signal (poll with
   a timeout), since nothing today defines one for this use case.
3. Send a small number of real commands through the standard command-dispatch path and assert the
   resulting property values match what was asked for — this is exactly Stage 5a from
   `plugin_concepts_and_lifecycle.md` §3, nothing plugin-special about the mechanism itself.
4. On a failed assertion: feed the failure back to the LLM to patch the plugin code, restart the
   service, retry. **Cap: 3 attempts.** On the 3rd failure (**E3**): stop, leave the hub clean
   (stop the plugin service), report the failure detail to the user.
5. On success: report back in plain language that the plugin is installed and working, summarizing
   what was exercised.

## 4. Cross-cutting open questions

- **§2's install-path gap is the biggest one** — restated here only to keep this list complete;
  see §2 for the actual analysis.
- **This needs its own orchestrator, not just more tools.** Six stages, two independent
  three-attempt retry budgets, and state that has to survive across a start/stop/restart of the
  plugin under test don't fit the existing single-shot `AgenticLoop` tool-calling model cleanly —
  that loop reasons turn-by-turn within one conversation, it doesn't natively track "attempt 2 of
  3 of Stage 6." This wants an explicit state machine/driver sitting above the chat loop (current
  stage, attempt counters per retry-bounded stage, the generated artifacts so far), with the chat
  loop as its front-end rather than its engine. Worth designing deliberately rather than assuming
  it falls out of existing tool-calling for free.
- **Blast radius of generated code.** This pipeline's whole premise is LLM-generated code, calling
  an arbitrary third-party web API, executed on the user's real eisy — which, per
  [`shell-tool.md`](../shell-tool.md), already runs with elevated `eisyui`-group access. At
  minimum: never execute the generated plugin before Stage 5's tests pass, and the generated
  plugin should run under whatever constraints an ordinary marketplace plugin already runs under —
  nothing here should grant it more.
- **Cleanup on terminal failure.** E2 leaves a plugin installed but never started; E3 leaves one
  that was started and then stopped. Should either be uninstalled automatically, or left in place
  for the user/a real developer to inspect? Automatic cleanup has the same gap as §2 —
  `delete_plugin` is *also* a URL-returning stub today, not a direct action.
- **Research budget for Stage 2.** How many searches/pages before "no API found" is declared —
  needs a concrete bound so E1 is a real decision, not a coin flip on how persistent that turn's
  search happened to be.

## 5. Suggested next step

Resolve §2 first — specifically, find out whether PG3 already exposes *any* scriptable local-store
install mechanism outside this repo before assuming option (a) requires new platform work. Every
other stage in §3 is buildable independently of that answer and mostly wires together tooling that
already exists (Stage 1, 4, 6) or extends it in a well-scoped way (Stage 2's research capability,
Stage 3's codegen, Stage 5's mocked pytest harness). The orchestrator in §4 is the other piece
worth designing before writing code — it's the part that turns "a pile of tool calls" into "a
pipeline with retry budgets," and every stage above assumes it exists.

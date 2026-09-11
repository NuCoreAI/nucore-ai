<!-- TODO: Figure out whether we can use /api/sys for get_full_system_config -->

# Node-property history: tools, handlers, prompt/doc updates

_Replaces the previous plan in this file (the `_load_routines` duplication fix --
already implemented, merged). This is a new, unrelated feature._

## Context

`design/history.md` already captures the reverse-engineered backend contract for
INSTEON/IoX node-property history (from `NodeHistoryEmail.pdf`): a global on/off
recording toggle, a paginated query endpoint, and an `/api/sys` flag reporting
whether recording is currently on. This plan turns that design doc into real tools:
letting the model both control recording and answer "what was X's value over time"
questions, the historical counterpart to the existing live `get_property`.

Per your answers: these become **two new standalone top-level tools** (not
`run_diagnostic_step` steps) -- history querying is an ordinary device-data question
a customer might ask any time ("what was the temp yesterday"), not troubleshooting
reference material, so it belongs alongside `get_property`/`send_command`, not
gated behind `get_diagnostics_prompt`. And the query tool supports the **full
multi-device/multi-property cross-product** the raw REST API exposes, not just one
device at a time.

## Design

### 1. `NuCoreInterface` additions (`src/nucore/nucore_interface.py`)

Plain concrete methods with `raise NotImplementedError(...)` bodies -- the
`plugin_ops`/`configure_plugin` pattern (L558, L572), **not** `@abstractmethod`
(like `run_diagnostic_step`). Reason: 15 test-only `NuCoreInterface` fake subclasses
exist across `tests/`; an `@abstractmethod` would force every one of them to grow a
new stub just to stay instantiable. A plain method with a raising default costs
nothing for fakes that never exercise it.

```python
async def set_node_property_history_recording(self, enabled: bool) -> dict | None: ...
async def get_node_property_history(
    self, device_ids: list[str], properties: list[str], *,
    start: str | None = None, end: str | None = None,
    one_before: bool = False, one_after: bool = False, limit: int = 500,
) -> dict | None: ...
```

### 2. `IoXWrapper` concrete implementations (`src/iox/iox_wrapper.py`)

- `set_node_property_history_recording(enabled)`: `await self.get(f"/rest/history/node/properties/recording/{'on' if enabled else 'off'}")`, check `status_code == 200`.
- `get_node_property_history(...)`:
  - Resolve each `device_id` via `self.get_node(device_id)` (same as `get_property`'s handler) -- fail fast with a clear error if any doesn't resolve, matching `get_property`'s existing behavior, rather than silently dropping it.
  - Resolve each property *name* against **every** resolved device via `self.resolve_property_id(device_id, name)` (property resolution is device-scoped in this codebase); union the resolved ids across devices into one flat list. A name that resolves on none of the given devices is an unknown-property error, same shape as `get_property`'s.
  - Build the query string with `urllib.parse.urlencode` (`property`/`node` as comma-joined values per `design/history.md`'s documented list syntax; `start`/`end` passed through as given; `oneBefore`/`oneAfter` from `one_before`/`one_after`; `limit`).
  - Call `await self.get(f"/rest/history/node/properties/get?{query}")`.
  - **Response shape is unconfirmed** -- `design/history.md`'s own "Open questions" section flags this explicitly (no real sample response, only a guessed shape). Parse defensively: attempt `response.json()`, return a clear `{"error": ...}` if it doesn't parse or doesn't match the expected `{"history": [...]}`-ish shape, rather than crashing. **This needs verification against a live hub before considering the feature done** -- flag prominently in the PR/commit.

### 3. Two new top-level tools

- `src/unified/tools/tool_device_set_history_recording.json` -- `name: "set_node_property_history_recording"`, one required boolean `enabled`. Description covers: this is a *global* switch (all devices/properties), persists across restarts, defaults to Off on a fresh install.
- `src/unified/tools/tool_device_get_history.json` -- `name: "get_node_property_history"`, required `device_ids` (array of strings) + `properties` (array of strings, display names exactly as `get_property` expects), optional `start`/`end` (ISO-8601 with offset -- description tells the model to compute a real timestamp using the TIME & LOCATION context, same expectation as DEV.LOG's date handling), `one_before`/`one_after` (bool), `limit` (int, default ~500).
  - Description explicitly documents: the cross-product semantics (every requested property is queried against every requested device, not exact pairs), and the pagination contract (see below).
  - Following `tool_pair_device.json`'s precedent (long, thorough `description` carrying all the nuance) rather than a new dedicated prompt file -- this codebase has exactly one prose doc (`diagnose.md`, for diagnostics broadly), no precedent for a narrower per-feature one.

Handler module: new `src/unified/handlers/history.py` (`command_control_status.py`'s
own docstring scopes it to exactly `get_property`/`send_command`; a new capability
area gets its own file, same as `plugin_management.py`/`variable_ops.py` etc.).
Register both in `src/unified/dispatch.py`'s `TOOL_HANDLERS`.

**Pagination**: mirror `run_shell_command`'s truncation-signal pattern (already
proven, per `diagnose.md`'s DEV.LOG section) rather than inventing a new one: when
the returned record count equals `limit`, include `"more_available": true` and
`"next_start"` (the last returned event's timestamp) in the tool's result, so the
model can re-call with `start=next_start` -- explicit and structured instead of an
unbounded fetch or a bespoke retry paragraph.

### 4. `get_full_system_config` gains a passive status field

`src/iox/diagnostics/iox_diagnostics.py`'s `get_full_system_config` (L276-448)
already sets flat standalone fields outside the per-subsystem loop (e.g.
`full_config["IoT Provisioned"]`, L415). Add `full_config["Node Property History Enabled"]`
sourced from a **new**, separate `GET /api/sys` call (JSON; unrelated to the
existing SOAP `GetSystemOptions` call -- confirmed no existing caller of `/api/sys`
anywhere in `src/`) reading its `nodePropertyHistory` boolean. This is a read-only
convenience for "is history tracking even on" as a passing fact during a general
system check -- it does not replace `set_node_property_history_recording`, whose own
result should also just report the state it set, so the common "toggle it" flow
never needs a separate status check first.

## Critical files

- `src/nucore/nucore_interface.py` -- two new plain methods.
- `src/iox/iox_wrapper.py` -- concrete implementations; reuses `self.get`, `self.get_node`, `self.resolve_property_id` (existing).
- `src/unified/handlers/history.py` -- new handler module (two functions).
- `src/unified/tools/tool_device_set_history_recording.json`, `tool_device_get_history.json` -- new.
- `src/unified/dispatch.py` -- two new `TOOL_HANDLERS` entries.
- `src/iox/diagnostics/iox_diagnostics.py` -- `get_full_system_config`, one new field + one new `/api/sys` fetch.

## Verification

- `pytest tests/ -q` -- full suite.
- New tests mirroring `tests/unified/handlers/test_diagnostics.py`'s shape for the
  two new handlers: device/property resolution failures, cross-product property
  resolution across multiple devices, pagination hint fields when `limit` is hit.
- `tests/iox/` coverage for the two new `IoXWrapper` methods' URL/query-string
  construction and defensive JSON parsing (a fake `self.get` returning a
  non-JSON/unexpected-shape body must produce a clear error, not a crash).
- **Live-hub check required before shipping**: the actual `/rest/history/node/properties/get`
  response shape and the `/api/sys` `nodePropertyHistory` field are both unconfirmed
  against a real controller -- run one real query against an eisy/IoX hub with
  recording turned on and adjust the parsing in step 2 to match reality.

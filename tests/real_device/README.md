# Real-device intent handler fuzz-testing harness

Pulls the LIVE device/group/folder/routine inventory off a real IoX/ISY hub,
asks an LLM to generate adversarial corner-case queries grounded in that real
inventory, runs each one through the real `IntentRuntime`, and flags anything
that looks broken. There's no hand-written expected answer for a fuzzily
generated query -- nothing is asserted as "correct"; flagged cases are
written to `results/latest-failures.md` for a human/Claude to judge, and are
also appended to `seeds/regression.yaml` so they're replayed on every future
run rather than only ever found once by chance.

This talks to real hardware and generates its own test queries. Only point
it at a hub/devices you're OK exercising repeatedly and automatically.

## Running it

Connection args mirror `src/intent_handler/run_intent_runtime.py`'s CLI --
copy them straight from the `Claude-IntentHandler-LakeEncino` launch.json
config:

```bash
python tests/real_device/run_real_device_tests.py \
    --backend-api-base-url=https://192.168.4.21 \
    --backend-api-classpath=iox.IoXWrapper \
    --backend-api-username=<user> \
    --backend-api-password=<password> \
    --runtime-config=src/intent_handler/runtime_assets/nucore_runtime.example.json \
    --secrets-file=secrets/keys.json
```

Useful flags:

- `--only command_control_status,routine_automation` -- restrict to specific intent families.
- `--count-per-intent 5` -- more/fewer fresh fuzzy queries generated per family (default 3).
- `--seeds-only` -- skip fresh LLM generation, just replay `seeds/regression.yaml` (fast regression check).
- `--no-seeds` -- skip replaying seeds, only generate fresh queries.
- `--no-seed-capture` -- don't persist newly-flagged queries to `seeds/regression.yaml`.

No per-hub setup is required beyond having devices/routines that actually
exist on the target hub -- the harness discovers them live at run time.

## Output

- `results/latest-run.json` -- every case (ok/review/error), raw data (always written).
- `results/latest-failures.md` -- one section per flagged case (only written when something is flagged; removed on a clean run).
- `results/archive/` -- timestamped copies of both, kept for history.
- `seeds/regression.yaml` -- every case ever flagged, deduped by query text. Tracked in git (not gitignored) so discovered corner cases accumulate as a real regression suite over time. Prune an entry manually once you've confirmed the underlying bug is fixed.

All of `results/` is gitignored; `seeds/` is not.

## How it works

1. **Discover** -- refreshes and reads `nucore_interface.summary_rags` (the
   same compact device/group/folder summary the intent handlers themselves
   see) and `nucore_interface.condensed_routines`.
2. **Generate** -- feeds that live inventory, plus a corner-case taxonomy per
   intent family (see `harness/query_gen.py`: ambiguous references, vague
   quantities, nonexistent devices, conflicting routine logic, fuzzy
   references to existing routines, etc.), to the runtime's own LLM adapter
   and asks for a batch of adversarial queries grounded in real device/routine
   ids. Every seed from `seeds/regression.yaml` is queued up too.
3. **Execute** -- each query runs through the real `IntentRuntime.handle_query`
   exactly as a live user request would, with real backend calls.
4. **Flag, don't assert** (`harness/verify.py`) -- since there's no
   hand-written expected outcome, nothing is judged "correct." Cases are
   flagged `severity="error"` for exceptions, tool-call failures, or a silent
   no-op (no tool call AND no text response); flagged `severity="review"`
   when no tool call ran and the response doesn't read as a clarifying
   question (could be a legitimate direct answer, could be a silent wrong
   guess -- a human/Claude needs to read the actual text); left `severity="ok"`
   when a tool call executed cleanly or the system asked a clarifying
   question -- both are healthy outcomes for a corner-case query.
5. **Report + seed** -- flagged cases go into `results/latest-failures.md`
   with the query, why it was chosen, the actual response/tool result, and
   which `handler.py`/`prompt.md` to look at -- then get appended to
   `seeds/regression.yaml` for next time.

## Safety: snapshot & restore

Best-effort throughout, since the harness doesn't know in advance what a
generated query will touch:

- **Predicted targets**: the generator is asked to name the exact real
  device/routine `id`(s) each query targets; those are snapshotted before the
  call. Anything it couldn't attribute to a specific target (e.g.
  `nonexistent_device` queries) simply isn't snapshotted -- there's nothing
  real to protect.
- **Device properties**: restored via the common binary on/off convention
  (`ST` "0" vs non-"0" -> `DOF`/`DON`). Anything else -- a precise dim level,
  thermostat setpoint, color -- is deliberately left alone rather than
  guessed at, and reported as `restore_uncertain` in `restore_notes` so you
  can check/fix it manually. Before/after values are always in the report.
- **Node metadata** (`node_ops`): name/enabled/parent are snapshotted and
  restored automatically for predicted targets.
- **Node/group creation** (`node_ops`, `group_scene_ops`): node ids are
  diffed before/after every call in these two families (not just when
  predicted); anything new gets deleted automatically.
- **Group/scene membership changes to an EXISTING group**: not automatically
  restored -- flagged in `restore_notes["group_scene_membership"]` for
  manual verification. Reliably diffing/restoring an arbitrary group's
  membership list would need a dedicated "read current members" API call
  this harness doesn't have; out of scope for now.
- **Routines**: a routine matched to a real `target_routine_id` (editing, or
  status ops on an existing routine) has its full definition snapshotted via
  `get_routine` and restored via `update_routine` after -- this covers
  `routine_automation` edits and all of `routine_status_ops` uniformly,
  since `update_routine` overwrites the whole definition including enabled
  state. Routine ids are also diffed before/after every `routine_automation`
  call (whether or not a target was predicted); a newly created routine gets
  deleted (`nucore_interface.delete_routine`) after its content is captured
  for the report. `routine_ops`/`delete_routine`'s "delete" is intentionally
  never exposed to the LLM via any tool schema -- the harness calls it
  directly since it isn't going through the LLM/tool-call path.
- A query that acted on a device/routine the generator didn't predict (an
  "unscoped" target) is noted in the report's `details.unscoped_devices_touched`
  -- itself sometimes a bug (wrong device picked) -- but that device's
  before-state was never captured, so it can't be auto-restored.

Restore is always attempted, even after a flagged case or an exception. A
failed restore is reported loudly (`restore_error`) since it means the hub
may have been left in a modified state -- check it manually.

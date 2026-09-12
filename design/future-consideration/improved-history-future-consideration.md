# Replacing get_node_property_history with a DEV.LOG-backed SQLite index

**Note on this document:** this is a design/analysis memo, not a ready-to-execute plan. It captures
a design discussion and its open questions; nothing below should be built until the open questions
are resolved against real data (see "Open questions").

(Filed under `future-consideration/` rather than alongside `history.md`/`history_impl.md` because it
proposes replacing, not extending, the feature those two docs describe.)

**Source confirmation:** the DEV.LOG format and actor codes below are no longer inferred from log
samples -- they're confirmed directly against the ISY/eisy firmware source (`udi` repo,
`ISY/src/log/Logger.cpp` and `ISY/src/error/DevintiXErrors.h`). `Logger.cpp:142`'s format string --
`"%s\t%s\t%s\t%s\t%hd\t%d\r\n"` writing `node, control, action, time, uid, type` -- is the exact,
sole place every DEV.LOG line is generated, and matches `diagnose.md`'s documented column order
exactly.

## Event architecture (confirmed against firmware source)

DEV.LOG and NPH are not two read APIs onto one shared event -- they're two independent
instrumentation points that happen to share a single trigger, traced via an Explore agent against
the real `udi` firmware source:

1. **Shared trigger, then two branches.** `LogicalDevice::sendStatusUpdate`
   (`ISY/src/dev/LogicalDevice.cpp:1757-1767`) fires whenever a device's status value changes (via
   `HandleDeviceResponseCommon`, fed by protocol drivers through `U7Report::reportStatus` at
   `u7/U7Report.cpp:94`). Inside that one function: **Branch A (DEV.LOG)** -- line 1767 calls
   `udLogNodeEvent(DEVINTIX_LOG, SYSTEM_USER, ...)` directly (`log/Logger.cpp:425-434`), raw args
   straight onto the `Logger` queue, no filtering. **Branch B (NPH + MQTT + SOAP subscribers)** --
   lines 1759-1762 go through an `Updater` queue → `UpdateLogicalDeviceNow()`
   (`dev/UDLogicalDeviceUpdater.cpp:323`) → `udSubscriptions.publishEvent(...)`
   (`UDIncludes/security/Subscription.h:638-696`) → every registered `UDNodeEventListener`,
   including `IoXNPHEventQueue::onNodeEvent` (`nodePropertyHistory/IoXNPHEventQueue.cpp:71-118`).
   Each branch reconstructs the event independently (raw args vs. XML via `UDNodeEventParser`), so
   they can diverge.

2. **NPH filters events DEV.LOG does not.** `IoXNPHEventQueue::onNodeEvent`
   (`IoXNPHEventQueue.cpp:78,93`) explicitly excludes `_`-prefixed property names and `" UOM "`
   values before queuing. DEV.LOG's branch has no equivalent filter.

3. **NPH is gated; DEV.LOG isn't.** NPH only records while recording is toggled on (REST toggle →
   `U7ProcessCommand::enableNodePropertiesHistory`, `u7/U7ProcessCommand.cpp:145-155` →
   `IoXNPHEventQueue::setIsEnabled`, `IoXNPHEventQueue.cpp:44-65`, which adds/removes itself from
   the listener bus). DEV.LOG's branch logs unconditionally -- it has coverage for periods NPH would
   have silently missed, including any period before recording was ever turned on.

4. **Command dispatch never reaches NPH at all.** Commands are logged via a wholly separate call --
   `Log(DEVINTIX_LOG, isyUserType, cmd.getCmdName(), value, node_address)` at `u7/U7Driver.cpp:130`,
   fired when a command dispatches to a node -- not through `sendStatusUpdate`. NPH only listens for
   status/value-change broadcasts (branch B above), never command dispatch. This is source-level
   confirmation of the actor-attribution gap already established from live data: NPH structurally
   cannot answer "who/what caused this," not just as an observed limitation of its current output.

## Context

`get_node_property_history` (see `design/history.md`, `design/history_impl.md`) wraps the hub's
`/rest/history/node/properties/get` endpoint. Comparing its real output against a live payload
surfaced two structural limits, confirmed against the actual XML shape:

1. **Change-of-state only.** An event is recorded only when a property's value actually changes --
   a repeated command that doesn't change the value produces nothing. Good for value/trend
   questions, but it under-counts actual activity.
2. **No actor.** The recorded shape (`timestamp`, `value`, `formatted`, `uom`, `prec`) carries no
   equivalent of DEV.LOG's actor column -- there is no way to answer "who/what caused this" from
   history data alone, even for the transitions it does record.

Separately, the *current* way of answering "who/what caused this" -- `run_shell_command` grepping
`/var/isy/FILES/LOG/DEV.LOG` directly, per `diagnose.md`'s DEVICE ACTIVITY LOG section -- has its own
proven failure mode: a live forensic trace (session `46d582f3`, the "why didn't my pool pump run"
investigation) found a real production bug where a `grep -F` substring match silently pulled in an
unrelated sibling device's lines, got truncated before reaching the relevant rows, and the model
went on to falsely retract an accurate earlier finding because it never checked the `truncated` flag.
That bug is fixed in `diagnose.md` (exact-match `awk`, explicit truncation-awareness rules), but the
underlying tool is still "shell out and grep a growing flat text file," which has no indexing and no
hard guarantee against truncation for a large enough result.

DEV.LOG, unlike the hub's history endpoint, already carries **both** things the hub endpoint lacks:
every command (not just net value changes) and actor attribution (WEB/ROUTINE/SYSTEM). So instead of
building this tool on top of the hub's history API, the proposal is: ingest DEV.LOG itself into an
indexed local SQLite database, and make that the backing store for a history-query tool -- a
candidate **replacement** for `get_node_property_history`, not just a faster version of the DEV.LOG
grep pattern.

## Design

### 1. Ingest: DEV.LOG → normalized rows

Parse each tab-separated line into: `device_id` (resolved to a display name via the same lookup
`get_device_name` already uses), `control` (a property name or command name, resolved per the
actor-dispatch rule below), `value` (column 3, as-is), `timestamp_raw` (column 4, the original text)
plus a derived `timestamp` (column 4, parsed via `strptime("%a %Y/%m/%d %I:%M:%S %p")` into a
sortable ISO-8601/epoch value -- see "Indexing" below for why this derived column is required, not
optional), `actor` (column 5, normalized -- see actor codes below), and `type` (column 6, as-is).

**Actor codes (confirmed against firmware source, `enum _user_type` in `DevintiXErrors.h`):**
`0`=SYSTEM, `1`=SYSTEM_DRIVER (no confirmed call site in the current firmware), `2`=WEB,
`3`=SCHEDULER (X10 device commands specifically -- its only call site,
`UDTriggerRunner.h:886`), `4`=ROUTINE (D2D trigger/condition logic internally -- confirmed at its
call site, `U7TriggerCmd.cpp:69`), `5`=ELK (Elk M1 alarm-panel integration). Store the raw numeric
code plus a normalized label; an unrecognized code should fall back to the raw value rather than
being treated as an error.

**Name resolution (confirmed at the source level, not just inferred from log samples):** dispatch
on the actor column itself, not a "check command id first, fall back to property" cascade: actor
`0` (SYSTEM) rows look up column 2 in the property table; every other actor code looks up column 2
in the command table. This is safe as the *sole* resolution rule (no fallback lookup needed)
because it's structurally guaranteed, not just empirically true: `LogicalDevice.cpp`'s
`sendStatusUpdate` (`LogicalDevice.cpp:1767`) is the *only* place in the firmware that logs a node
event with actor `SYSTEM_USER`, and it always reports a property value -- it can never carry a
command. Dispatching on actor is both safer and simpler than a cascade (one lookup per row, not
two), and avoids the collision risk a cascade would have (a `SYSTEM` row silently mislabeled with a
command's display name if a command id and a property id ever happened to coincide).

**Rename handling:** device names are re-resolved against the *current* device database on every
rebuild (see lifecycle below), so a renamed device's historical rows show the new name once the next
rebuild happens. This isn't instantaneous -- a rebuild is triggered by DEV.LOG's size changing, not
by the rename itself, so a query run between a rename and the next log-driven rebuild would still
show the old name. Self-correcting, but not immediate.

**Commits:** chunked (e.g. every 50k rows), not one giant transaction for the whole file. Bounds how
long a rebuild holds a write lock, so a concurrent reader isn't blocked for the entire rebuild.
Benchmarked (synthetic 100k-row file, single-commit case): parse + name-lookup 0.15s, insert 0.36s,
index build 0.09s -- ~0.6s total. The naive per-row-commit alternative measured ~280s for the same
100k rows (355 rows/sec, dominated by per-commit fsync) -- this is the whole reason batching matters.

### 2. Indexing

The raw DEV.LOG timestamp (`Mon 2026/08/24 02:10:34 PM`) does not sort chronologically as a string,
so a derived sortable `timestamp` column (ISO-8601 or epoch) is required for range queries to work
at all -- indexing the raw text only accelerates exact-match lookups. Keep `timestamp_raw` alongside
it for display fidelity.

Recommended indexes, matching the query shapes already documented in `diagnose.md`:
- `(device_id, timestamp)` -- "all activity on device X over period P" (the "why didn't X happen" /
  "how many times" shapes).
- `(device_id, control, timestamp)` -- the narrower "why did property Y on device X change" shape.

Not recommending a third index (e.g. `(actor, timestamp)` for cross-device actor-only questions)
speculatively -- no known query shape needs it yet; add it if one surfaces.

### 3. Storage location and lifecycle

Lazy creation: built on first use, not bootstrapped separately. The byte size of DEV.LOG at build
time is recorded; every subsequent call compares DEV.LOG's current size against that recorded value
and does a full regenerate (drop + rebuild, chunked commits) if they differ **at all** -- covers both
growth and rotation/truncation with one check, no separate inode/identity tracking needed. Disk-size
growth of the DB itself is bounded because DEV.LOG's own size is capped.

Trade-off accepted: this means **every** size difference triggers a *full* re-parse of the entire
log, not an incremental delta-append -- even if only one new line was added. Bounded (DEV.LOG has a
size ceiling) but recurring: every query that follows any log growth pays the full-rebuild cost, not
just the cost of what changed. Worth confirming DEV.LOG's actual size ceiling to know the worst-case
rebuild latency being accepted on a recurring basis.

Minor: two concurrent first-callers could race to create the DB simultaneously; build to a temp file
and rename into place to avoid a reader seeing a half-written DB mid-build.

### 4. Concurrent access

WAL mode (`PRAGMA journal_mode=WAL`), as used in the benchmark -- lets readers proceed while a
rebuild is in progress, avoiding "database is locked" errors under concurrent access.

## Open questions (must resolve before implementing)

1. ~~Actor code mapping is unconfirmed and currently contradictory.~~ **Resolved, from the firmware
   source itself** (`udi/ISY/src/error/DevintiXErrors.h`'s `enum _user_type`), not just a log
   sample: `0`=SYSTEM_USER, `1`=SYSTEM_DRIVER_USER, `2`=WEB_USER, `3`=SCHEDULER_USER (X10 commands
   only, per its sole call site), `4`=D2D_USER (routines/programs), `5`=ELK_USER. `diagnose.md`'s
   `0`=SYSTEM was correct; a later message in this design discussion stating `3`=SYSTEM was wrong.

2. ~~Can a SYSTEM-attributed row ever carry a `DON`/`DOF` command?~~ **Resolved: no, and now
   confirmed structurally, not just empirically.** `LogicalDevice.cpp:1767` (`sendStatusUpdate`) is
   the only place in the firmware that logs a node event with actor `SYSTEM_USER` -- it always
   reports a property value and can never carry a command. The actor-dispatch name-resolution rule
   in section 1 (actor `0` → property table, everything else → command table, no fallback lookup)
   is sound as designed.

3. **DEV.LOG's actual size ceiling/rotation policy is asserted, not verified here.** Needed to know
   the worst-case full-rebuild latency implied by the lifecycle design in section 3.

4. ~~Whether DEV.LOG captures every property NuCore/the hub tracks, or only a curated subset.~~
   **Substantially resolved:** DEV.LOG has no equivalent of NPH's `_`-prefix/`" UOM "` filter (see
   "Event architecture" above), so it's a superset of anything NPH would ever record for a given
   property set, plus it isn't gated by the recording toggle NPH requires. New sub-question this
   raises, not yet decided: should the SQLite ingestion pipeline replicate NPH's filter (avoiding
   surfacing internal/engineering-only properties a customer-facing tool shouldn't show), or keep
   everything DEV.LOG has? Leaning toward replicating the filter by default -- cheap (two string
   checks per row) and matches what the hub's own equivalent feature already decided was fit for
   customer consumption -- but this is a call worth confirming, not decided here.

## Summary: why this over the current approaches

- vs. `get_node_property_history` (hub XML endpoint): gains actor attribution and full command
  history (not just net value changes); also gains historical coverage for any period before NPH
  recording was turned on, or while it was off, since DEV.LOG logs unconditionally and NPH does
  not (see "Event architecture" #3) -- a gap NPH cannot fill retroactively even if enabled today.
  Loses nothing confirmed so far, pending the filter-replication call in open question 4.
- vs. `run_shell_command` + DEV.LOG grep/awk (current `diagnose.md` approach): indexed range queries
  replace ad hoc text scans, eliminating the truncation failure mode that caused a real production
  bug (session `46d582f3`) -- a correctness improvement, not just a speed one.

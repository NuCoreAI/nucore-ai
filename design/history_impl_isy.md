# DEV.LOG structured capture: implemented in the ISY/eisy firmware

Companion to `history.md`/`history_impl.md` (the hub's `get_node_property_history` REST API) and
`future-consideration/improved-history-future-consideration.md` (a since-superseded design for a
Python-side tool that periodically re-parsed the flat DEV.LOG file into SQLite). **This document
covers a different, already-implemented feature**: a SQLite-backed structured capture of every
DEV.LOG-worthy event, built directly into the ISY/eisy hub firmware itself (the `udi` repo, a
separate C++ codebase from `nucore-ai`), not a nucore-ai tool.

## Why this exists, and why not the Python-side design

`improved-history-future-consideration.md` designed a Python process that periodically re-parsed
`/var/isy/FILES/LOG/DEV.LOG` into SQLite. That approach was rejected for two reasons stated
directly by the project owner:

1. A separate process re-deriving DEV.LOG's device/property/command id→name mappings duplicates
   what the firmware already holds live in memory -- fragile, and unnecessary.
2. Pruning must run on the **same thread as the existing Logger**, not a new dedicated thread, so
   it costs nothing extra on the real-time device-handling path.

Both constraints point at the same conclusion: this has to be a firmware change, not a nucore-ai
tool. Two research passes (an Explore agent tracing the Logger thread and SQLite wrapper, then a
Plan agent designing against those findings) produced the design below, with several claims
verified directly against the real `udi` source before implementing (see "Things verified, not
assumed" below) -- consistent with this project's general practice of tracing to real source rather
than trusting a comment or an inference from log samples.

## Architecture

**Every DEV.LOG line is written by `UDLogger::writeEntry`** (`ISY/src/log/Logger.cpp`), called from
`UDLogManager::writeLogEntry` inside `UDLogManager::processLogs`'s loop -- the dedicated "Logger"
thread. The new capture hooks in here, as a **composed collaborator** (`UDLoggerSQLCapture`, a
global singleton `udLoggerSQL`), not a new `UDLogger` subclass and not a new module like
`nodePropertyHistory/` (NPH is a full subsystem with its own thread; this feature must NOT get its
own thread):

```cpp
void UDLogger::writeEntry(LogMessage* message)
{
   char logm[MAX_LOG_ENTRY_SIZE];
   format(message,logm, MAX_LOG_ENTRY_SIZE);
   UDXLog::writeRaw(logm,strlen(logm));          // unmodified flat-file DEV.LOG write

   if (getId() == UD_LOG_REPORT_ID)              // only the DEV.LOG instance, not UDSysOutLogger
      udLoggerSQL.captureEntry(message);         // NEW: structured SQLite capture
}
```

This is a **dual-write**, not a cutover: `captureEntry()` runs strictly after the existing flat-file
write, so DEV.LOG itself is completely unmodified. That was a deliberate rollout choice -- it lets
the new database be diffed row-for-row against the still-live DEV.LOG to build confidence before
any future phase considers retiring the flat file (the stated long-term goal, not this phase's job).

### Schema (`FILES/LOG/DEVLOG.DB`)

```sql
CREATE TABLE IF NOT EXISTS DevLogEvents (
   Id           INTEGER PRIMARY KEY AUTOINCREMENT,
   EventTime    INTEGER NOT NULL,   -- msg->time: Unix epoch SECONDS (see verified facts below)
   NodeAddress  TEXT,  NodeName TEXT,
   ControlId    TEXT NOT NULL, ControlLabel TEXT,
   Action       TEXT,
   Actor        INTEGER NOT NULL,   -- raw _user_type (0=SYSTEM, 2=WEB, 4=ROUTINE/D2D, etc.)
   EventType    INTEGER NOT NULL,   -- raw _log_type
   IsCommand    INTEGER NOT NULL    -- derived: 0 if Actor==SYSTEM_USER, else 1
);
-- + indexes on (NodeAddress,EventTime), (NodeAddress,ControlId,EventTime), (EventTime)
```

`Id` is a real `AUTOINCREMENT` primary key, deliberately **not** NPH's own composite
`(EventTime,NodeAddress,PropertyId)` PK convention: NPH's `EventTime` is Unix microseconds, safe
against same-instant collisions; this table's `EventTime` is only second-resolution (see below), so
two same-second events for one node+control would collide under NPH's scheme. `Id` also gives the
prune logic a clean, race-free cutoff key.

### Name resolution -- live, in-process, no re-parsing

```cpp
UDNode *node = (nodeAddr && ud.nodeMgr) ? ud.nodeMgr->getAnyNode(nodeAddr) : nullptr;
const char *nodeName = node ? node->GetName() : nullptr;
DriverControl *dc = (dcName && u7Instance) ? u7Instance->util->getDriverControlByName(dcName) : nullptr;
const char *ctrlLabel = dc ? dc->GetLabel() : nullptr;
```

Both the raw id and the resolved label are stored for every field -- raw stays ground truth if a
device is renamed later; resolved makes the database directly human-readable with no join. This is
the entire reason the feature has to live in the firmware: these are the exact same live objects
`sendStatusUpdate`/`getFormattedAction` already use to write DEV.LOG's own text, not a re-derivation
from anything.

**Simplification found during implementation, not anticipated by the earlier Python-side design:**
the firmware's own `Controls::get_ctrl_by_name()` already resolves property AND command ids through
one unified per-device-profile lookup. There is no firmware-side equivalent of the Python design's
"two separate tables, dispatch by actor to know which one to check" problem -- `IsCommand` is kept
purely as a query-convenience column derived from `Actor`, not because resolution needs it.

### Idle tick for pruning -- same thread, no new thread

`UDLogManager::processLogs` blocked forever waiting for a message (`timeout=0`). Changed to a 30s
timeout (`UD_LOGGER_SQL_IDLE_TICK_TICKS`); when `OSQPend` times out with no message pending, the
loop now calls `udLoggerSQL.onIdleTick()` before continuing to wait -- a periodic "check DB size,
prune if needed" step running on the exact same thread that writes every log entry, exactly
satisfying the "no new thread" constraint.

### Prune-and-export -- byte-size threshold, export before delete

Deliberately different from NPH's own pruning (`IoXNPHPrune.cpp`), which is record-count based
(~11M rows ≈ 1GB) and just discards old rows with no backup. This feature prunes at a 20MB byte
threshold down to a 15MB low-water mark (hysteresis), and **exports before ever deleting**:

1. Select the oldest 5000 rows (`ORDER BY Id ASC LIMIT 5000`).
2. Write each as a literal, properly-escaped `INSERT INTO DevLogEvents (...) VALUES (...);`
   statement to a temp file (there's no CLI `.mode insert` equivalent in the C++ SQLite wrapper used
   here, so this is built by hand via `UDXSQLUtil::appendNullOrQuoted` for correct quote-escaping).
3. Only once the export file is confirmed flushed and renamed into place --
   `DEVLOG_PRUNED_<minDate>_<maxDate>.sql`, dates from the batch's actual min/max `EventTime` -- does
   the code delete those rows (`DELETE ... WHERE Id <= <cutoff>`) and run
   `PRAGMA incremental_vacuum` to actually shrink the file on disk.

`PRAGMA auto_vacuum = INCREMENTAL` is set at database creation (must precede any `CREATE TABLE`) --
without it, `DELETE` alone would never shrink the file, defeating the entire point of a byte-size
prune threshold. Incremental vacuum was chosen over a full `VACUUM` specifically because the latter
needs ~2x disk headroom and an exclusive lock that would block the `sqlite3` CLI's own read access --
a goal this project explicitly wants to preserve.

## Things verified against real source, not assumed

- **DEV.LOG's exact column format**, confirmed at `Logger.cpp`'s `format()`:
  `"%s\t%s\t%s\t%s\t%hd\t%d\r\n"` writing `node, control, action, time, uid, type` -- matches
  `diagnose.md`'s documented column order exactly.
- **Actor codes**, from `DevintiXErrors.h`'s `enum _user_type`: `0`=SYSTEM_USER, `1`=SYSTEM_DRIVER_USER
  (no confirmed call site), `2`=WEB_USER, `3`=SCHEDULER_USER (X10 commands only), `4`=D2D_USER
  (routines/programs), `5`=ELK_USER. This resolved a live discrepancy from an earlier design
  discussion (`0`=SYSTEM was correct; a claim that `3`=SYSTEM was wrong).
- **SYSTEM_USER structurally never carries a command** -- `LogicalDevice.cpp`'s `sendStatusUpdate`
  is the *only* place in the firmware that logs a node event with actor `SYSTEM_USER`, and it always
  reports a property value. This is what makes the actor-dispatch design safe without a fallback
  lookup.
- **The timestamp is a standard Unix epoch UTC value, despite a stale/misleading comment.**
  `Clock.cpp`'s `getCurrentDateTime()` is commented "seconds since 1900/01/01... IT'S IN LOCAL TIME",
  but tracing into `UDXDateTime::getLocalDateTime()`'s actual `time(NULL)` →`localtime_r`→`mktime`
  round-trip shows it returns a real Unix epoch UTC `time_t` in practice (rare ~1hr ambiguity only
  at DST-transition boundaries). This is why `EventTime` is stored as the raw integer directly, no
  `strptime`/parsing step needed at all -- a real simplification over the Python-side design, which
  needed to parse formatted text because it only ever had DEV.LOG's text output to work from.
- **NPH's own precedent for a related design decision**: `nodePropertyHistory/IoXNPHSQL.cpp`'s
  schema stores `EventTime INTEGER -- Unix Microseconds`, confirming epoch-integer storage (not
  formatted text) is this platform's own established convention, and directly informing the
  AUTOINCREMENT-vs-composite-PK decision above.
- **`u7.util` is not a global** -- it's a per-class member reference (`U7 &u7;`) that classes like
  `U7Report` wire up themselves from `u7Instance` in their own constructors. A free-standing class
  like `UDLoggerSQLCapture` has no such reference available and must use `u7Instance->util->...`
  directly, with a null check (confirmed necessary: `u7Instance` is null during early boot, and
  existing code elsewhere already defensively null-checks it for exactly that reason).

## Files changed (in `udi`, not `nucore-ai`)

- `ISY/src/log/UDLoggerSQL.h` / `.cpp` (new) -- the `UDLoggerSQLCapture` class: schema creation,
  live name resolution + insert, idle-tick DB-size check, prune-and-export.
- `ISY/src/log/Logger.h` -- `UDLogger::getFormattedAction` promoted `protected`→`public static` (no
  behavior change, just reuse); added `UD_LOGGER_SQL_IDLE_TICK_TICKS` (30s).
- `ISY/src/log/Logger.cpp` -- three additions: the `captureEntry()` call in `writeEntry` (gated on
  `getId()==UD_LOG_REPORT_ID`); a `message==NULL` branch in `processLogs`'s loop calling
  `udLoggerSQL.onIdleTick()`; `LoggerThread` now passes a real timeout instead of blocking forever.
- `ISY/Debug/src/log/subdir.mk` -- registered the new `.cpp` in the (Eclipse-CDT-generated) build.

## Open risks -- not resolved by the implementation, need an owner decision

1. **Boot-order null-pointer risk.** `UDLogger::addStartEntry()` fires the very first log entry
   synchronously during early `main.cpp` init, before `u7Instance` is guaranteed non-NULL and before
   `ud.nodeMgr`'s init ordering could be confirmed by reading the tree alone. The code already
   null-guards both, but whoever owns `main.cpp`/boot sequencing should confirm this ordering
   directly rather than relying on the guard alone.
2. **Export-file retention is undecided.** Nothing bounds how many `DEVLOG_PRUNED_*.sql` files
   accumulate over time -- left alone, they'd eventually recreate the disk-growth problem pruning
   was meant to solve.
3. **`DriverControl` label resolution isn't profile-scoped** -- `get_ctrl_by_name()` resolves
   against one device list with no per-profile disambiguation, so two device families sharing a raw
   control id could show the wrong label. Inherited from the cheapest available live-lookup path,
   not a defect introduced by this feature -- a conscious tradeoff, flagged so it stays conscious.
4. Unrelated bonus finding: DEV.LOG's own size ceiling (an open question in
   `improved-history-future-consideration.md`) is now confirmed from firmware source --
   `UDConstants.h`: `MIN_LOG_SIZE`=1MB, `DEFAULT_LOG_SIZE`=3MB, `MAX_LOG_SIZE`=128MB,
   user-configurable via `udConfig.SetLogSize()`.

## Verification status

Implemented and reviewed line-by-line against real, currently-compiling source (every non-trivial
call cited above was checked against its actual declaration in this checkout before use) -- but
**not compiled or run**. No FreeBSD embedded toolchain exists in the environment this was built in,
so there is no way to actually build `udi` or exercise `LoggerThread`/`OSQPend` there. Once built by
whoever owns the `udi` build pipeline, the real test plan is: diff DEV.LOG against the new database
row-for-row after triggering real commands/status changes; artificially lower the 20MB threshold to
exercise prune-and-export without waiting for organic growth, then confirm
`sqlite3 newdb < DEVLOG_PRUNED_*.sql` re-imports the exact pruned rows; confirm
`incremental_vacuum` actually shrinks the file on disk (not just the row count); confirm the
database stays readable via the `sqlite3` CLI while the Logger thread is actively writing (WAL
mode); kill -9 the process mid-prune and confirm no partial `.tmp` export file is ever treated as
authoritative and no row is ever lost that wasn't already exported.

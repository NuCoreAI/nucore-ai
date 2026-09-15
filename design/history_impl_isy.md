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

A second round of work (backfill of pre-existing history, schema versioning, `Actor`-as-text, an
export-filename rename, and a fix for a real control-label resolution bug) is documented in its own
"Boot-time reconciliation" section below, added after several more research/verification passes
against the same `udi` checkout.

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
   Actor        TEXT NOT NULL,     -- display text: "System"/"Web"/"Routine", else the raw _user_type number (see "Actor as display text" below)
   EventType    INTEGER NOT NULL,   -- raw _log_type
   IsCommand    INTEGER NOT NULL    -- derived: 0 if raw Actor==SYSTEM_USER, else 1
);
-- + indexes on (NodeAddress,EventTime), (NodeAddress,ControlId,EventTime), (EventTime)
```

`Id` is a real `AUTOINCREMENT` primary key, deliberately **not** NPH's own composite
`(EventTime,NodeAddress,PropertyId)` PK convention: NPH's `EventTime` is Unix microseconds, safe
against same-instant collisions; this table's `EventTime` is only second-resolution (see below), so
two same-second events for one node+control would collide under NPH's scheme. `Id` also gives the
prune logic a clean, race-free cutoff key.

`Actor` was originally the raw `_user_type` integer; it is now stored as display text -- see
"Actor as display text" under "Boot-time reconciliation" below for the mapping and rationale.

### Name resolution -- live, in-process, no re-parsing

```cpp
UDNode *node = (nodeAddr && ud.nodeMgr) ? ud.nodeMgr->getAnyNode(nodeAddr) : nullptr;
const char *nodeName = node ? node->GetName() : nullptr;
UDString ctrlLabelStr(128);
const char *ctrlLabel = nullptr;
if (dcName && node && u7Instance && u7Instance->nls)
{
   if (u7Instance->nls->appendStatusName(ctrlLabelStr, node, dcName))
      ctrlLabel = ctrlLabelStr.c_str();
}
```

Both the raw id and the resolved label are stored for every field -- raw stays ground truth if a
device is renamed later; resolved makes the database directly human-readable with no join. This is
the entire reason the feature has to live in the firmware: these are the exact same live objects
`sendStatusUpdate`/`getFormattedAction` already use to write DEV.LOG's own text, not a re-derivation
from anything.

**Correction (originally read as a simplification, turned out to be a real bug -- see "Fix
control-label resolution" below):** the `ControlLabel` snippet above (`u7Instance->nls->
appendStatusName(...)`) supersedes an earlier version of this code that called
`u7Instance->util->getDriverControlByName(dcName)->GetLabel()`. That earlier call resolves against
`Controls`, a **single flat name→label array shared by literally every device family in the
firmware** (confirmed: `local_devices[0]` is the only index ever used anywhere in this codebase) --
not "one unified per-device-profile lookup" as originally believed. `IsCommand` is still kept purely
as a query-convenience column derived from the raw `Actor` value, not because resolution needs it.

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
   `<from-date>-<to-date>-log.db-sql.LOG`, dates from the batch's actual min/max `EventTime` -- does
   the code delete those rows (`DELETE ... WHERE Id <= <cutoff>`) and run
   `PRAGMA incremental_vacuum` to actually shrink the file on disk. (Renamed from the original
   `DEVLOG_PRUNED_<minDate>_<maxDate>.sql` -- see "Rename prune-export files" below.)

`PRAGMA auto_vacuum = INCREMENTAL` is set at database creation (must precede any `CREATE TABLE`) --
without it, `DELETE` alone would never shrink the file, defeating the entire point of a byte-size
prune threshold. Incremental vacuum was chosen over a full `VACUUM` specifically because the latter
needs ~2x disk headroom and an exclusive lock that would block the `sqlite3` CLI's own read access --
a goal this project explicitly wants to preserve.

## Boot-time reconciliation: backfill, schema versioning, per-boot marker

Four requirements were added after the original dual-write/prune design above: pre-existing history
sitting in the flat file needs to be copied into the new table once (and again if the schema
changes), every boot needs to leave an explicit marker row, `DevLogEvents` needs a real schema
version rather than a one-time flag, and `Actor` needed to become display text. A fifth requirement
-- fix the control-label resolution bug noted above -- came out of investigating the second round.

### Trigger mechanism

A first draft hooked this at the literal top of `UDLogManager::processLogs()`, before its main loop.
That had a real flaw: at that point in boot, `u7Instance`/`ud.nodeMgr` are unconditionally NULL
(`StartLogger()` runs before `udStaticInit()`), so backfilled historical rows could never get
`NodeName`/`ControlLabel` resolution. Instead, a control message is posted onto the Logger thread's
own queue from `DevintiXController::Start()`, right after `ud.nodeMgr->flush()` (unconditionally --
not nested in the `queryOnInit`/safe-mode checks a few lines later, since a marker row is wanted for
every boot) and before the `queryScene("STARTUP",...)` call (which floods the Logger queue with live
status-update entries, so this reaches the queue ahead of that volume). By this point
`u7Instance`/`ud.nodeMgr` are long since valid, so backfilled rows *can* get full live name
resolution, exactly like normal live-captured rows:

```cpp
LogMessage *loggerStartupMsg = new LogMessage(DEVINTIX_SYSTEM_STARTUP, SYSTEM_USER);
loggerStartupMsg->future = 1;   // sentinel: control message, not a loggable DEV.LOG entry
OS_SEM loggerStartupDone;
OSSemInit(&loggerStartupDone, 0);
loggerStartupMsg->completionSem = &loggerStartupDone;
if (!UDQPost(&Logger, (void*)loggerStartupMsg, "LOG"))
{
   delete loggerStartupMsg;
}
else if (OSSemPend(&loggerStartupDone, 30 * TICKS_PER_SECOND) == OS_TIMEOUT)
{
   SYSTEM_STATUS2(NLS_LOG_DB_START_FAILED);
}
```

`LogMessage::future` (`ISY/src/log/LogMessage.h`) was a `UCHAR` field with zero references anywhere
else in the codebase -- confirmed genuinely unused, the least invasive discriminator available
(versus repurposing a `_log_type` value, which would touch `handlesLogEntry()`'s type-range checks).
Calling `UDQPost` directly from outside `Logger.cpp` already has precedent elsewhere in the codebase
(module loggers under `UDIncludes/module/...`), and `DevintiXController.h` already pulls in
`LogMessage`/`Logger.h`/`Logger` transitively via `build/UDInclude.h`, so no new include was needed
there.

**The main thread blocks on this**, with a 30s timeout, rather than firing-and-forgetting: the
STARTUP scene query (and the flood of live status-update `LogMessage`s it triggers) can't begin
until reconcile has fully finished, so there's no window where both compete for the Logger's
255-element queue. The mechanism is a real `OS_SEM` counting semaphore
(`UDX/src/UCOS/UDXtoUCOS.h`: `OSSemInit`/`OSSemPost`/`OSSemPend`/`OSSemPendNoWait`, backed by a real
POSIX `sem_t`), already used for exactly this producer/consumer shape elsewhere
(`UDIncludes/system/UDDHCPClient.h` -- kick off async work, then `OSSemPend` until another context
posts completion) -- simpler and more directly fitting than reusing `qSync`'s class (`U7QuerySync`,
`ISY/src/u7/U7Type.h`), which is a multi-family-bitmask *join* built for `queryScene`'s
fan-out-to-many-drivers shape, not a single one-shot signal. `LogMessage` is allocated via plain
`new`/`delete` today (`UD2_MEMORY_CODE_CONTENT`'s pool-allocator behavior only activates under
`SUPPORT_UD2_MEMORY_NEW_DELETE`, which is never defined anywhere in this codebase), so adding the new
`completionSem` pointer field costs nothing. Blocking indefinitely-ish inside `Start()` is already an
accepted, deliberate pattern here: the comment directly above the existing
`qSync.waitForQueryToFinish(0)` call states outright that unbounded blocking on a cross-thread signal
is expected ("this should never actually time out"), and no watchdog wraps `Start()`.

On timeout, `SYSTEM_STATUS2(NLS_LOG_DB_START_FAILED)` reports it via this codebase's existing
boot-status mechanism and the boot continues (not fatal). `NLS_LOG_DB_START_FAILED` (id `54`) is a
new entry in the hand-maintained, two-file `sysstatus` NLS table (`UDIncludes/nls/UDNlsConsts.h` +
`UDCommon/WEB/STDNLS.XML`) -- confirmed this table is runtime-loaded XML, not build-generated, and
is already used elsewhere for exactly this category of "boot-time subsystem init failure, logged,
boot continues" condition (e.g. `NLS_STD_HTTPS_NOT_AVAILABLE`, `NLS_STD_NETWORK_SUBSYSTEM_FAILED`).
The two files' ids must match by hand -- there's no build-time check tying them together (the
existing table already has an unrelated id-53/id-53 duplicate as a cautionary example of this).

### Dispatch interception in `UDLogManager::processLogs()`

```cpp
message=(LogMessage*)udata;
if (message == NULL)
{
   setThisThreadState("LOG-PRUNE");
   udLoggerSQL.onIdleTick();
   setThisThreadState("LOG-IDLE");
   continue;
}
if (message->future != 0)
{
   setThisThreadState("LOG-BACKFILL");
   udLoggerSQL.onLoggerStartup(message->time);
   setThisThreadState("LOG-IDLE");
   if (message->completionSem != nullptr)
      OSSemPost(message->completionSem);
   delete message;   // bypasses writeLogEntry(), which normally owns the delete
   continue;
}
setThisThreadState("LOG-ADD");
writeLogEntry(message);
```

This guarantees the trigger message never reaches `writeEntry()`/`format()` -- DEV.LOG's flat file is
never touched by it. The explicit `delete message;` is required since `writeLogEntry()` (which
normally frees the message) is skipped for this branch.

### `onLoggerStartup()` -- always-run marker, then version reconciliation

```cpp
void UDLoggerSQLCapture::onLoggerStartup(UDXTIME_T triggerTime)
{
   if (!ensureOpen())
      return;
   insertLoggerStartedMarker(triggerTime);
   reconcileSchemaVersion();
}
```

`insertLoggerStartedMarker()` does one plain autocommit `INSERT`, every boot, unconditional:
`EventTime` = the trigger message's own `time` (matching how every other row's `EventTime` is the
event's own timestamp, not dequeue time); `NodeAddress`/`NodeName`/`ControlLabel` = NULL;
`ControlId` = `"LoggerStartup"` (a literal sentinel, needed since the schema has `ControlId TEXT NOT
NULL`); `Action` = `"Logger started"`; `Actor` = `"System"`; `EventType` = `DEVINTIX_SYSTEM_STARTUP`;
`IsCommand` = 0. Query code that wants to exclude these marker rows from "real" device-event queries
needs to know to filter on this `ControlId` value -- it's a literal-string convention, not a
schema-level distinction.

### Schema versioning

`DevLogEvents`' `PRAGMA user_version` is now a real schema version, not just a one-time
"have we backfilled" flag:

```cpp
#define UD_LOGGER_SQL_SCHEMA_VERSION_1  1  // Initial DevLogEvents schema
#define UD_LOGGER_SQL_SCHEMA_VERSION_CURRENT UD_LOGGER_SQL_SCHEMA_VERSION_1
```

mirroring `U7Schema.cpp`'s exact naming/comment convention for the (unrelated) U7 profile database's
own schema-version handling. Read/write helpers are self-contained (`getSchemaVersion()`/
`setSchemaVersion()`, written directly against `UDXSQLite3QueryIter`, matching the exact idiom
`pruneAndExportPass()` already uses in this same file) rather than reusing
`U7DBOps::getDatabaseUserVersion()`/`setDatabaseUserVersion()` -- those are instance methods on
`U7DBOps`, requiring a live `U7&` purely to run this `PRAGMA`, an unnecessary cross-module dependency
for this unrelated database file.

```cpp
void UDLoggerSQLCapture::reconcileSchemaVersion()
{
   int version = 0;
   getSchemaVersion(version);

   if (version == UD_LOGGER_SQL_SCHEMA_VERSION_CURRENT)
      return;   // already current -- nothing to do

   sql.run("BEGIN TRANSACTION;");

   if (version != 0)
      migrate_log_db(version, UD_LOGGER_SQL_SCHEMA_VERSION_CURRENT);

   if (flatLogFileExists())
      backfillFromFlatFile();

   setSchemaVersion(UD_LOGGER_SQL_SCHEMA_VERSION_CURRENT);
   sql.run("COMMIT;");
}
```

`migrate_log_db()` always runs first on any real version transition (`version != 0`), bringing the
table to a clean, current-schema, *empty* state; `backfillFromFlatFile()` then always runs afterward
whenever the flat log exists -- independent of whether a migration just happened, so a version bump
on a device that still has its flat log always ends up with full history repopulated, not just an
empty current-schema table. `version == 0` (brand new DB, never initialized) skips `migrate_log_db()`
entirely -- there's nothing to migrate *from* -- but still runs `backfillFromFlatFile()` if a flat
log happens to already exist.

`migrate_log_db()`'s default (and, for now, only) behavior is to drop and recreate `DevLogEvents`
under the current schema, unconditionally:

```cpp
void UDLoggerSQLCapture::migrate_log_db(int fromVersion, int toVersion)
{
   (void)fromVersion;
   (void)toVersion;
   sql.run("DROP TABLE IF EXISTS DevLogEvents;");
   createDatabase();
}
```

No field-by-field migration path is defined for any `(fromVersion -> toVersion)` transition yet.
Existing historical rows are lost at this step, but since `reconcileSchemaVersion()` always calls
this *before* `backfillFromFlatFile()`, if a flat DEV.LOG file still exists, full history is
repopulated right after this runs -- data is only *permanently* lost when the flat log is also gone.
This matches this codebase's own `U7Schema` precedent exactly: confirmed by tracing its actual 1→2
version bump, no version bump there has ever had real per-version migration code either -- the
"migration" *is* dropping and recreating tables, then repopulating from the authoritative external
source (profile XML files, in that case). Add a real per-`fromVersion` transform to `migrate_log_db`
only if avoiding this reset ever becomes a real requirement for a specific bump.

**Crash-safety**: a crash/power-loss mid-reconciliation rolls back the entire transaction
automatically (SQLite recovery on next `open()`), including the `user_version` bump -- so the next
boot's check sees the old version again and retries the whole reconcile cleanly. No partial rows, no
separate completion flag needed beyond `user_version` itself.

### Backfill from the flat file

`backfillFromFlatFile()` streams `UD_DEVICE_STATE_LOG_FILE` (`/var/isy/FILES/LOG/DEV.LOG`) via
`FSUtil::readLine()` one line at a time -- confirmed the file has **no active size cap, truncation,
or rotation** compiled into the current ISY build (`DEFAULT_LOG_SIZE`/`MAX_LOG_SIZE`/`getLogSize()`
are dead code with zero call sites; `UDXLog`'s actual write path is a plain `fopen(path,"a")` kept
open forever), so it could realistically be very large -- do not load it into memory.

**Gotcha, confirmed by tracing, not assumed:** `FSUtil::readLine()` breaks on the first of `\r`/`\n`
and does not null-terminate except at EOF. Since DEV.LOG lines end `\r\n`, every real line is
followed by a stray one-byte `"\n"`-only pseudo-line on the very next read. This is silently detected
(trim trailing `\r`/`\n`, skip if now empty) and skipped -- no warning, since it's an expected
per-line artifact of this API, not corruption.

Each real line is split into its 6 tab-delimited fields (`Node\tControl\tAction\tTime\tUid\tType`)
via in-place `strchr('\t',...)` splitting, matching this file's existing hand-rolled-parsing style
(`Action` never itself contains a raw tab, since `getFormattedAction()` already replaces tabs with
spaces at write time). A line that fails to parse (fewer than 6 fields, or an unparseable time) is
skipped with a rate-limited warning (first 10 distinct occurrences) -- never aborts the whole
backfill.

**The `Time` field is not a raw epoch -- it's fully formatted, human-readable text, confirmed by
tracing `formatLogTime()` into `UDXDateTime::format()`'s actual `strftime` call:**
`"%a %Y/%m/%d %I:%M:%S %p"` (12-hour) or `"%a %Y/%m/%d %H:%M:%S "` (24-hour, trailing space, no
`%p`), depending on `udConfig.IsMilitary()` **at write time** -- meaning different lines in the same
file can use different formats if that setting changed historically. No existing function in this
codebase reverses this format (the only `strptime` call sites parse unrelated ISO-8601 strings), so
`parseLogTimeToEpoch()` is new: `sscanf(field, "%*s %d/%d/%d %d:%d:%d", ...)` (the `%*s` discards the
locale-dependent weekday token, which `mktime()` doesn't need as an input), detects 12h-vs-24h
per-line via `strstr(field,"AM"/"PM")`, applies standard 12→24h fixups, and calls `mktime()` with
`tm_isdst=-1` -- the same round-trip convention `UDXDateTime::format()` itself uses (inherits its
known rare DST-boundary ambiguity, not a new one introduced here).

For each successfully parsed line, `NodeName`/`ControlLabel` are resolved **live**, using the same
two-tier resolver `captureEntry()` uses (see "Fix control-label resolution" below) --
`u7Instance`/`ud.nodeMgr` are guaranteed valid by the time this runs, since the trigger fires after
`udStaticInit()` completes, unlike the (rejected) first-draft hook point at the literal top of the
Logger thread. `IsCommand` is computed from the raw parsed `Actor` integer (0 if `SYSTEM_USER`, else
1) *before* converting it to display text. Each row is inserted via a freshly prepared statement
(`UDXSQLite3::Stmt` has no reset/reuse API -- confirmed, only `run()` exists and it finalizes -- so
this matches `captureEntry()`'s own per-call `prepare()` pattern; the dominant cost is the single
transaction commit, not per-row prepare). All of this runs inside `reconcileSchemaVersion()`'s single
enclosing transaction -- no separate `BEGIN`/`COMMIT` of its own.

### `Actor` as display text

```cpp
static const char* actorToText(short uid, char *buf, size_t buflen)
{
   switch (uid)
   {
      case SYSTEM_USER: return "System";
      case WEB_USER:    return "Web";
      case D2D_USER:    return "Routine";
      default:
         snprintf(buf, buflen, "%d", (int)uid);
         return buf;
   }
}
```

Full `_user_type` enum, re-confirmed (corrects the count in "Actor codes" below, which stopped at
5): `SYSTEM_USER=0, SYSTEM_DRIVER_USER=1, WEB_USER=2, SCHEDULER_USER=3, D2D_USER=4, ELK_USER=5,
SEP_DEVICE_UMETER_USER=6, SEP_DEVICE_UPRICE_USER=7, SEP_DEVICE_UMSG_USER=8, SEP_DEVICE_UDR_USER=9,
GAS_METER_USER=10, OPEN_ADR_USER=11` -- everything besides System/Web/Routine falls through to the
raw-number-as-text branch. Used by both `captureEntry()` (live capture) and `backfillFromFlatFile()`
(historical rows), so there's one source of truth for the mapping. `captureEntry()`'s `Actor` bind
changed from `bindInt` to `bindText` accordingly; `IsCommand`'s derivation is unaffected -- it still
compares against the raw integer, not the display string.

### Fix control-label resolution

**Root cause, confirmed against source, not just re-flagged:** `u7Instance->util->
getDriverControlByName(dcName)` is a one-line pass-through to `local_devices[0]->controls.
get_ctrl_by_name(name)`. `local_devices[0]` is confirmed the **only** index ever used anywhere in
this codebase -- there is exactly **one global `Controls` array for the entire firmware**, shared by
every device family (Insteon, Z-Wave, Zigbee, UPB, X10, node-server profiles, etc.), searched by a
flat `name → DriverControl` binary search with no secondary key. `DriverControl` itself has no
profile/family field at all. This corrects the original "Simplification found during implementation"
note above, which believed this was "one unified per-device-profile lookup" -- **it cannot be fixed
by adding a parameter** to `get_ctrl_by_name`/`getDriverControlByName`; the backing structure has
nothing to scope against. A raw id like `"GV1"` resolves to the same single global label no matter
which device family actually emitted it.

**First attempt, and the regression it introduced (corrected below):** a profile-scoped resolver
already exists and is used a few lines away in `U7FormatAction.cpp`, just never called from the
logger -- `U7Nls::appendStatusName(string &out, UDNode *node, const char *ctl)` (where `string`
resolves to this codebase's own `UDString`, not `std::string`) derives `node->GetProductFamilyId()` +
the node's `nodeDefId` internally to build a compound `(nodeDefId, ctl)` key. The first version of
this fix called *only* `appendStatusName()`, on the (wrong) belief that it "falls back to the global
name if no per-profile entry was registered." **It doesn't.** Traced its full body: on no match it
does a plain `return false`, `out` untouched -- there is no fallback of any kind inside it. Worse, its
only data source is each product family's own `nls/en_us.txt` (built from
`U7Insteon_en_US.properties`/`U7ZWave_en_US.properties`/etc.), and those files were confirmed to
carry **zero** `ST-<ctl>-NAME` entries for any built-in driver control -- that job was always the old
global `Controls` table's alone. So calling only `appendStatusName()` made every built-in Insteon/
Z-Wave/etc. control's `ControlLabel` come back `NULL` (silently, not with a wrong-but-plausible
label) -- a real regression against the pre-existing behavior, confirmed while investigating why a
real control (`CLIHCS`, "Heat/Cool State") wasn't resolving in production output at all.

**The corrected fix is two-tier, not a straight swap**: try `appendStatusName()` first (correct,
profile-scoped, when a family/node-server explicitly registered an override for this exact control),
and only when it returns `false` fall back to the original `u7Instance->util->
getDriverControlByName(dcName)->GetLabel()` (family-ambiguous, but covers every built-in control,
which `appendStatusName()` alone cannot). This gets the more specific answer when one is registered,
and still gets *an* answer for the common built-in-control case instead of silently returning nothing:

```cpp
if (dcName != nullptr && node != nullptr && u7Instance != nullptr)
{
   if (u7Instance->nls != nullptr && u7Instance->nls->appendStatusName(ctrlLabelStr, node, dcName))
   {
      ctrlLabel = ctrlLabelStr.c_str();
   }
   else
   {
      DriverControl *dc = u7Instance->util->getDriverControlByName(dcName);
      if (dc != nullptr)
         ctrlLabel = dc->GetLabel();
   }
}
```

`node` was already resolved in scope in `captureEntry()` (used for `NodeName`) but never passed into
either label lookup before this fix; it now is, via tier 1 (confirmed `nls` is unconditionally
allocated alongside `util`/`driver` in `U7StaticInit()`, so a null-check on it is defensive
belt-and-suspenders, not a real possibility while `u7Instance` itself is valid). The identical
two-tier fix is used in `backfillFromFlatFile()`'s per-line resolution.

**Note for anyone chasing a specific "control X should say Y" report**: `CLIHCS` genuinely means
"Heat/Cool State" (values Off/Heat On/Cool On) everywhere in this codebase (`DRIVER.XML`, the Java
client constants, the NLS resource bundles) -- under *both* the old and new resolution paths. "Cool
Setpoint" is a different, adjacent control, `CLISPC`. Confirm the actual expected control id before
assuming a resolution bug.

**A second, genuinely distinct gap found via a real production report (`ZY008_143`'s `CV` control
logging `ControlLabel="CV"`):** this is *not* a bug in the two-tier resolver above -- it's a data
gap one layer further back, in the tier-2 fallback's own data source. `LogicalDevice::
parseDriverFile()` builds the single global `Controls` table (172 built-in controls, from
`udml_oem_controls[]` in `DriverXml.h`) at boot; for each one it defaults `label = name`, then
overwrites `label` only if it finds a matching `"NAME:Label"` entry in `STDNLS.XML`'s `<list
id="dc">`. That list had only **22** of the 172 entries, so the other 150 -- `CV` included --
kept `label == name` and rendered as their own raw id. (`DRIVER.XML`'s own `<label>Current
Voltage</label>` text is a dead end: it's never read at runtime -- `DriverXml.h`'s `#define DC_CV
"CV" // Current Voltage` reduces that same text to a discarded C++ comment; `STDNLS.XML`'s `dc`
list is the *only* real runtime label source for these 172 static controls.)

Two lookalike systems that came up while chasing this were confirmed **unrelated** and ruled out:
the per-family-instance `DEF/<familyId>/<instanceId>/nls/en_us.txt` files (loaded by
`U7NlsLoader`, consulted by tier 1's `appendStatusName()`), and the SQL profile database
(`EN_US`/`NodeDefStatus` tables, populated by `U7ProfileMover::moveNlsFilesToDatabase()` --
confirmed at boot inside `StartServicesMain()`, `DevintiXController.h:640`, well before the
Logger-startup trigger message in this doc's "Trigger mechanism" section is even posted). That DB
is a mirror of the *same* `en_us.txt` files tier 1 already reads in RAM -- not of `STDNLS.XML` --
so it can't help `CV` either; `parseDriverFile()` never consults it. Also corrected in this pass:
the `ZY` node-address prefix is ZMatter Z-Wave (migrated from the old `ZW` prefix), not Zigbee
(`ZB`/`UYB`).

**The fix**: a data change, not a code change. Enumerated all 172 `#define DC_*` entries in
`DriverXml.h:113-284` (each carries a same-line `// <label text>` comment -- the same source the
existing 22 `STDNLS.XML` entries' wording already matches almost verbatim, e.g. `CPW:Current
Power Usage` vs. `// Current Power Used`), diffed against the 22 already present, and added the
missing 150 as `<str id="23">` through `<str id="172">`, `"NAME:Label"` format, sourced from those
comments verbatim except four whose comments were full sentences/parentheticals rather than
labels (`ALARM`→"Alarm", `USRNUM`→"User Number", `VOCLVL`→"VOC Level"; `POP`'s comment was kept
as-is). Verified afterward: exactly 172 entries, no duplicate ids, no duplicate control names,
every name matches a real `DriverXml.h` control id, and the file still parses as valid XML.

### Embedding the `dc` list directly into the firmware binary

The `STDNLS.XML` `dc`-list fix above didn't reach a device even after rebuild/redeploy: its real
runtime home is a build-packaged, on-device path (`FILES/WEB/STDNLS.XML`) populated by an ant
`<copy>` step, and if that step (or the on-device persistence of `FILES/`) is skipped by whatever
build/deploy path was used, the device keeps running its old copy indefinitely with no code-level
signal that anything is stale. `dc` is the only list with a known incident like this --
`sysstatus`/`progress12` load from the same file with no reported problem -- so only `dc` was
compiled directly into the firmware; `sysstatus`/`progress12` keep loading from disk exactly as
before.

**Why not just parse less of the same file into the same buffer**: `UDNlsParser::onStartDocument()`
(`UDIncludes/nls/UDNlsParser.h:118-126`) unconditionally `memset()`s its entire `memBuff` and resets
`nextMem=4` at the start of every parse call, so two sequential parses into the same buffer/`NList`
array (e.g. `parseFile()` then `parseString()` to "override just dc") don't work -- the second
call's reset wipes out the first call's results. `UDNlsParser` instances are otherwise fully
self-contained (own `memBuff` pointer, own `NList` array), so the fix uses **two independent
parser instances**, each with its own buffer, instead.

**The fix**: `UDIncludes/nls/UDStdNlsDcXml.h` embeds the (already-fixed, 172-entry) `dc` list
verbatim from `STDNLS.XML`, wrapped in the same `<?xml...?><lists>...</lists>` structure the
parser expects, as a `R"UDSTDNLSDC(...)"` raw string literal (`UD_STD_NLS_DC_XML`). In
`UDIncludes/nls/UDStdNls.h`'s `loadStrings()`:
- a new `UDNlsParser` + dedicated `dcMemBuff[6*1024]` parses `UD_STD_NLS_DC_XML` via
  `parseString()` into `nls.dc` alone;
- the existing `memBuff[8*1024]` + parser now only targets `{ nls.sysStatus, nls.progress12 }`
  (relying on `Names`' declared field order -- `sysStatus`/`progress12` are contiguous after `dc`
  -- exactly as the pre-existing code already relied on `Names`' full 3-field layout) and still
  calls `parser.parseFile(STD_NLS_FILE)` unchanged.

`UDCommon/WEB/STDNLS.XML` itself did **not** need to be edited: `UDNlsParser::onStartTag()`'s
`T_STR` case calls `addString()` unconditionally for every `<str>` tag regardless of whether the
enclosing `<list id="...">` matched an id in the parser's `lists[]` array -- only the final
`nameOffsets` array is gated on a match. So the disk parser still scans past the file's now-inert
`dc` block (discarding it, since `dc` is no longer in its `lists[]`), which costs it ~3.6KB of
throwaway buffer space -- but `dc` is first in file order and the unreferenced `others` list is
last, so `sysstatus`/`progress12` (positions 2-3) finish safely (~7.05KB cumulative) well before
the buffer's 8KB limit, with only the already-unused `others` tail affected by any overflow.

### Rename prune-export files for `newsyslog` pickup

`pruneAndExportPass()`'s export filename changed from `DEVLOG_PRUNED_<minDate>_<maxDate>.sql` to
`<minDate>-<maxDate>-log.db-sql.LOG` -- same existing min/max-date range (unchanged same-day
collision resistance), just a different suffix/extension. **Confirmed**: `newsyslog` is part of the
device's base image (outside this `udi` checkout, which is why no `newsyslog.conf` appears anywhere
in a repo search) and rotates/archives any file ending `.LOG` automatically -- no separate config
change needed as long as the extension matches.

## Things verified against real source, not assumed

- **DEV.LOG's exact column format**, confirmed at `Logger.cpp`'s `format()`:
  `"%s\t%s\t%s\t%s\t%hd\t%d\r\n"` writing `node, control, action, time, uid, type` -- matches
  `diagnose.md`'s documented column order exactly.
- **Actor codes**, from `DevintiXErrors.h`'s `enum _user_type`: `0`=SYSTEM_USER, `1`=SYSTEM_DRIVER_USER
  (no confirmed call site), `2`=WEB_USER, `3`=SCHEDULER_USER (X10 commands only), `4`=D2D_USER
  (routines/programs), `5`=ELK_USER. This resolved a live discrepancy from an earlier design
  discussion (`0`=SYSTEM was correct; a claim that `3`=SYSTEM was wrong). (The full enum actually
  extends to `11` -- see "Actor as display text" above.)
- **SYSTEM_USER structurally never carries a command** -- `LogicalDevice.cpp`'s `sendStatusUpdate`
  is the *only* place in the firmware that logs a node event with actor `SYSTEM_USER`, and it always
  reports a property value. This is what makes the actor-dispatch design safe without a fallback
  lookup.
- **The timestamp is a standard Unix epoch UTC value, despite a stale/misleading comment.**
  `Clock.cpp`'s `getCurrentDateTime()` is commented "seconds since 1900/01/01... IT'S IN LOCAL TIME",
  but tracing into `UDXDateTime::getLocalDateTime()`'s actual `time(NULL)` →`localtime_r`→`mktime`
  round-trip shows it returns a real Unix epoch UTC `time_t` in practice (rare ~1hr ambiguity only
  at DST-transition boundaries). This is why `EventTime` is stored as the raw integer directly, no
  `strptime`/parsing step needed at all for *live* capture -- though the *backfill* path does need
  exactly this kind of parsing, since the flat file only ever has DEV.LOG's formatted text to work
  from (see "Backfill from the flat file" above) -- a real simplification over the Python-side
  design only for the live-capture path, not the backfill path added later.
- **NPH's own precedent for a related design decision**: `nodePropertyHistory/IoXNPHSQL.cpp`'s
  schema stores `EventTime INTEGER -- Unix Microseconds`, confirming epoch-integer storage (not
  formatted text) is this platform's own established convention, and directly informing the
  AUTOINCREMENT-vs-composite-PK decision above.
- **`u7.util` is not a global** -- it's a per-class member reference (`U7 &u7;`) that classes like
  `U7Report` wire up themselves from `u7Instance` in their own constructors. A free-standing class
  like `UDLoggerSQLCapture` has no such reference available and must use `u7Instance->util->...`
  directly, with a null check (confirmed necessary: `u7Instance` is null during early boot, and
  existing code elsewhere already defensively null-checks it for exactly that reason).
- **`u7Instance->nls`, like `util` and `driver`, is unconditionally allocated in `U7StaticInit()`**
  immediately after `u7Instance = new U7()` -- confirmed no code path sets `u7Instance` non-null
  without also setting `nls`, so a null-check on `u7Instance->nls` (when `u7Instance` is already
  known non-null) is defensive only, not a real possibility.
- **`Controls` (backing `get_ctrl_by_name()`/`getDriverControlByName()`) is a single flat array for
  the entire firmware, not a per-profile structure** -- confirmed via `local_devices[0]` being the
  only index used anywhere in the codebase, and `DriverControl` having no profile/family field at
  all. See "Fix control-label resolution" above.
- **`FSUtil::readLine()`'s CRLF gotcha** -- breaks on the first of `\r`/`\n`, doesn't null-terminate
  except at EOF, so DEV.LOG's `\r\n` line endings produce a stray one-byte `"\n"`-only pseudo-line
  after every real line. A genuine, non-obvious implementation detail discovered while designing the
  backfill, in the same "verified, not assumed" spirit as the rest of this section.
- **`LogMessage::future` is a genuinely unused `UCHAR` field** (confirmed via a repo-wide grep) --
  the least invasive discriminator available for the new Logger-thread control message, versus
  repurposing a `_log_type` value (which would touch `handlesLogEntry()`'s type-range checks).
  `LogMessage` itself is allocated via plain `new`/`delete` today (`SUPPORT_UD2_MEMORY_NEW_DELETE` is
  never defined anywhere in this codebase), so adding the new `completionSem` pointer field to it
  costs nothing.
- **`OS_SEM` (a real counting semaphore, backed by POSIX `sem_t`) already has a real
  producer/posts-work-then-blocks-for-completion precedent** in this codebase
  (`UDIncludes/system/UDDHCPClient.h`), confirming it's the right primitive to reuse for the
  main-thread-blocks-until-Logger-thread-finishes mechanism, rather than inventing a new one or
  reusing the heavier multi-family-bitmask `U7QuerySync`/`OS_FLAGS` class `qSync` itself uses.
- **The `SYSTEM_STATUS2(NLS_...)` mechanism is a hand-maintained, two-file, runtime-loaded XML string
  table** (`UDIncludes/nls/UDNlsConsts.h` + `UDCommon/WEB/STDNLS.XML`), not build-generated -- unlike
  the `UDOemHeaderCreator.java`/`Driver.xml` code-generation pattern used elsewhere in this codebase.
- **No `newsyslog.conf` exists anywhere in the `udi` repository** -- it must live on the device's
  base FreeBSD image outside this checkout, confirmed by an exhaustive repo-wide search.
- **`U7Nls::appendStatusName()` has no fallback of its own -- confirmed by tracing its full body.**
  On no match it does a plain `return false`, leaving the output string untouched; it never falls
  back to the flat `Controls` table. Its only data source, each product family's own `nls/en_us.txt`
  (built at compile time from the `U7<Family>_en_US.properties` Java resource bundles), was confirmed
  to carry zero `ST-<ctl>-NAME` entries for any built-in Insteon/Z-Wave/etc. driver control -- those
  per-family files exist only for custom/node-server-registered overrides. Discovered while
  investigating a production report that a real control (`CLIHCS`) wasn't resolving at all -- see
  "Fix control-label resolution" above for the corrected two-tier resolver this led to.
- **`CLIHCS` means "Heat/Cool State," not "Cool Setpoint," everywhere in this codebase** (`DRIVER.XML`,
  Java client constants, NLS resource bundles) -- `CLISPC` is "Cool Setpoint." Neither the old nor the
  new resolution path has ever produced "Cool Setpoint" for `CLIHCS`, since that pairing was never
  real; a production report expecting it was a mismatched-control-id expectation, not a resolution
  bug, though investigating it *did* surface the real `appendStatusName()`-has-no-fallback bug above.

## Files changed (in `udi`, not `nucore-ai`)

- `ISY/src/log/UDLoggerSQL.h` / `.cpp` -- the `UDLoggerSQLCapture` class: schema creation, live name
  resolution + insert, idle-tick DB-size check, prune-and-export, plus (added in the second round)
  `onLoggerStartup()`, `insertLoggerStartedMarker()`, `getSchemaVersion()`/`setSchemaVersion()`,
  `reconcileSchemaVersion()`, `migrate_log_db()`, `flatLogFileExists()`, `backfillFromFlatFile()`,
  `parseFlatLogLine()`, `parseLogTimeToEpoch()`, and the static `actorToText()` helper; the `Actor`
  column type change; the `ControlLabel` resolution fix; the export filename rename.
- `ISY/src/log/Logger.h` -- `UDLogger::getFormattedAction` promoted `protected`→`public static` (no
  behavior change, just reuse); added `UD_LOGGER_SQL_IDLE_TICK_TICKS` (30s).
- `ISY/src/log/Logger.cpp` -- the `captureEntry()` call in `writeEntry` (gated on
  `getId()==UD_LOG_REPORT_ID`); a `message==NULL` branch in `processLogs`'s loop calling
  `udLoggerSQL.onIdleTick()`; `LoggerThread` now passes a real timeout instead of blocking forever;
  (second round) a `message->future != 0` interception branch in `processLogs()` that calls
  `udLoggerSQL.onLoggerStartup()`, posts `completionSem` if set, and explicitly deletes the message.
- `ISY/src/log/LogMessage.h` -- (second round) new `OS_SEM *completionSem = nullptr;` field
  (default-initialized, zero behavior change elsewhere), plus a new `#include <UCOS/UDXtoUCOS.h>` so
  `OS_SEM` is visible regardless of include order.
- `UDIncludes/upnp/DevintiXController.h` -- (second round) new trigger-post-and-wait block in
  `Start()`, right after `ud.nodeMgr->flush()` and unconditional (not nested in the
  `queryOnInit`/safe-mode checks), before the `queryScene("STARTUP",...)` call.
- `UDIncludes/nls/UDNlsConsts.h` -- (second round) new `#define NLS_LOG_DB_START_FAILED 54` in the
  `sysstatus` list.
- `UDCommon/WEB/STDNLS.XML` -- (second round) matching new `<str id="54">...</str>` entry in the
  `sysstatus` list.
- `ISY/Debug/src/log/subdir.mk` -- registered the new `.cpp` in the (Eclipse-CDT-generated) build.

## Open risks -- not resolved by the implementation, need an owner decision

1. **Boot-order null-pointer risk.** `UDLogger::addStartEntry()` fires the very first log entry
   synchronously during early `main.cpp` init, before `u7Instance` is guaranteed non-NULL and before
   `ud.nodeMgr`'s init ordering could be confirmed by reading the tree alone. The code already
   null-guards both, but whoever owns `main.cpp`/boot sequencing should confirm this ordering
   directly rather than relying on the guard alone. (Unrelated to the second round's boot-time
   reconciliation work, which deliberately runs later in boot, after `udStaticInit()`, specifically
   to avoid this same class of risk for backfilled rows.)
2. **Export-file retention is undecided.** Nothing bounds how many `*-log.db-sql.LOG` files
   accumulate over time -- left alone, they'd eventually recreate the disk-growth problem pruning
   was meant to solve. (Renaming them for `newsyslog` pickup doesn't resolve this on its own unless
   the base image's `newsyslog.conf` also enforces a retention count/age for matching files --
   outside this checkout's visibility to confirm.)
3. ~~`DriverControl` label resolution isn't profile-scoped~~ -- **fixed via a two-tier resolver**, see
   "Fix control-label resolution" above (an earlier single-tier version of this fix was itself a
   regression -- reverted in favor of the two-tier approach). Residual: the fallback tier
   (`getDriverControlByName()->GetLabel()`) is still the original family-ambiguous global lookup, so
   two device families sharing a raw control id with no per-family NLS override registered for either
   could still show the wrong label for that shared id -- the fix guarantees *a* label for every
   built-in control and the *correct* label whenever a per-family override exists, not disambiguation
   for every possible built-in-control collision.
4. Unrelated bonus finding: DEV.LOG's own size ceiling (an open question in
   `improved-history-future-consideration.md`) is now confirmed from firmware source --
   `UDConstants.h`: `MIN_LOG_SIZE`=1MB, `DEFAULT_LOG_SIZE`=3MB, `MAX_LOG_SIZE`=128MB,
   user-configurable via `udConfig.SetLogSize()` -- though also confirmed dead/unenforced, see
   "Backfill from the flat file" above.
5. **`migrate_log_db()`'s drop-and-recreate default loses history.** If a future schema bump happens
   on a device whose flat DEV.LOG file is gone, existing rows are discarded (not preserved in old
   shape) to bring the table to a clean current-schema state. Accepted as the default per explicit
   product direction; a real per-`fromVersion` transform can be added later only if avoiding this
   loss becomes a real requirement.
6. **`UDQPost` failure path (Logger queue full at post time) silently skips reconcile for that boot,
   with no status reported.** Confirmed unlikely this early in boot (the Logger thread has been
   running and draining since well before `Start()` executes), but not impossible. Self-healing:
   since `user_version` isn't bumped, the next boot's trigger attempt retries the same reconcile.
   Unlike the 30s-timeout case (which reports `NLS_LOG_DB_START_FAILED`), this earlier failure mode
   doesn't report anything -- flagged in case that asymmetry should be closed the same way.
7. **A 30s reconcile timeout doesn't stop the Logger thread's work.**
   `SYSTEM_STATUS2(NLS_LOG_DB_START_FAILED)` only reports that the main thread gave up waiting;
   `onLoggerStartup()` keeps running to completion on the Logger thread regardless (it's not
   cancelled), so a slow reconcile (e.g. a very large flat DEV.LOG file) still finishes in the
   background, just without the main thread having waited for it -- meaning `queryScene("STARTUP",
   ...)` and the resulting status-update flood could overlap with the tail end of reconcile after
   all, for this specific slow-boot case.
8. **Logger-thread queue-blocking during backfill/rebuild, generalized.** `reconcileSchemaVersion()`
   blocks the Logger thread from draining its queue for however long parsing + per-line live
   resolution + insert takes on a potentially very large DEV.LOG file -- and this can now happen on
   *any* boot where a schema version bump occurred, not just the very first boot ever. Mitigated
   (not eliminated) by the main-thread wait in "Trigger mechanism" above, which delays the
   STARTUP-query flood until reconcile finishes -- but other threads besides the main one could still
   post to the same queue during a long reconcile.
9. **`insertLoggerStartedMarker()`'s `ControlId` sentinel** (`"LoggerStartup"`) is a literal string
   convention, not a schema-level distinction -- query code needs to know to filter/recognize it to
   exclude marker rows from "real" device-event queries.

## Verification status

Implemented and reviewed line-by-line against real, currently-compiling source (every non-trivial
call cited above was checked against its actual declaration in this checkout before use) -- but
**not compiled or run**. No FreeBSD embedded toolchain exists in the environment this was built in,
so there is no way to actually build `udi` or exercise `LoggerThread`/`OSQPend` there. Once built by
whoever owns the `udi` build pipeline, the real test plan is:

- diff DEV.LOG against the new database row-for-row after triggering real commands/status changes;
- artificially lower the 20MB threshold to exercise prune-and-export without waiting for organic
  growth, then confirm `sqlite3 newdb < <from-date>-<to-date>-log.db-sql.LOG` re-imports the exact
  pruned rows;
- confirm `incremental_vacuum` actually shrinks the file on disk (not just the row count);
- confirm the database stays readable via the `sqlite3` CLI while the Logger thread is actively
  writing (WAL mode);
- kill -9 the process mid-prune and confirm no partial `.tmp` export file is ever treated as
  authoritative and no row is ever lost that wasn't already exported;
- a fresh-install boot backfills correctly with resolved names;
- a second boot doesn't re-backfill but does insert a new marker row;
- the main thread's `queryScene("STARTUP",...)` observably doesn't fire until the Logger thread's
  reconcile has finished, on a boot where reconcile takes a long time;
- a simulated schema-version bump on a device with an existing flat log triggers a full drop+rebuild;
- the same bump with the flat log deleted drops/recreates via `migrate_log_db` and still advances
  `user_version`;
- a boot with a torn/partial last DEV.LOG line skips only that line;
- a boot with mixed 12h/24h historical lines (simulating a `udConfig.IsMilitary()` change during the
  device's lifetime) reverses both correctly;
- killing the process mid-reconcile leaves the old `user_version` and a clean retry next boot;
- `Actor` renders as `"System"`/`"Web"`/`"Routine"`/a raw number as expected for each `_user_type`;
- `ControlLabel` resolves per-device-family correctly for two device families that happen to share a
  raw control id;
- the renamed export files land with the `<from-date>-<to-date>-log.db-sql.LOG` shape and get
  rotated/archived by the base image's `newsyslog`.

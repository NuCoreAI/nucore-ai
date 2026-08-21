> **For future consideration.** This is a design proposal, not an approved or scheduled
> change. Nothing here has been implemented. Keep this note until a decision is made to build
> it, shelve it permanently, or revisit its risk trade-offs.

# Add a built-in shell-execution tool

## Context

Idea: give the nucore-ai LLM runtime its own built-in "bash tool" — a way for the agent's
tool-calling loop to run arbitrary unix commands on the host, the same general idea as how
Claude Code itself uses a Bash tool. Explored the existing tool system first: tools live in
`src/unified/` as a 3-layer stack (JSON schema in `tools/`, async handler in `handlers/`,
hand-maintained dispatch table in `dispatch.py`). There is **no existing subprocess/shell-exec
code anywhere in the repo** — this would be new capability, not a revival of something dormant.

**Important risk callout:** this is a customer-facing home-automation assistant — end-customer
chat text flows straight into the LLM's context, so this tool would be reachable via prompt
injection, not just deliberate developer use. The design below assumes **full arbitrary shell
access, no command-content allowlist** — the LLM can run *any* command; safety comes from
scoping *what the executing identity can reach*, not from filtering command text. That's a
deliberate divergence from the codebase's one existing precedent for dangerous actions —
`plugin_management.py`'s `install_plugin`/`buy_plugin`/`delete_plugin`, which never act
directly and instead hand back a URL for a human to finish.

Checked for off-the-shelf alternatives before designing this from scratch (Anthropic's native
`bash_20250124` tool type, MCP shell servers, LangChain's `ShellTool`) — none fit without
either being provider-locked, requiring new protocol infrastructure heavier than this design,
or pulling a large dependency for a thin wrapper.

### How this settled on its current shape

Earlier drafts of this doc explored isolating the shell tool *more* than the rest of the
nucore-ai process — a separate privilege-separated socket-server daemon under its own
restricted user (V2, superseded — see below), then a per-call `sudo -u <restricted-user>`
privilege drop invoked directly from the handler. Both were solving the wrong problem: they
tried to make the tool more restricted than the account nucore-ai itself runs as, which added
real complexity (a second daemon and its lifecycle, or cross-UID signal-delivery problems that
made the timeout/kill logic unreliable) to work around a boundary that a real deployment
decision already resolves more simply.

**Decided deployment model**: nucore-ai runs under a new dedicated OS account, `eisyai`
(group `eisyui`), deployed as a Polyglot/pg3 node server the same way other plugins on this
platform are (confirmed by inspecting a live eisy host: node servers like `audio-player.py`/
`bluetooth.py` each run under their own dedicated per-instance account, e.g.
`0021b9131313_6`, launched once by the `polyglot` supervisor via a narrowly-scoped
`Runas_Alias` sudoers entry — `polyglot ALL = (PGUSR) NOPASSWD: ALL` where `PGUSR` enumerates
only those specific accounts). `eisyai` additionally gets its own dedicated sudoers.d file
(mirroring the existing `/usr/local/etc/sudoers.d/admin` and `.../polyglot` pattern already on
this host) granting the specific privileged commands it needs beyond its native rights (e.g.
service start/stop/restart).

**The consequence for this tool's design is significant simplification**: since the shell
tool's intended blast radius *is* `eisyai`'s own scope, by design decision, the handler needs
**no privilege-awareness at all**. It just runs `bash -c command` as `eisyai`, plain and
direct. Whether a given command succeeds is entirely determined by the OS — `eisyai`'s own
sudoers.d file decides which `sudo`-prefixed commands (if the LLM issues them) actually work;
everything else runs with exactly `eisyai`'s native permissions. No `sudo -u` wrapping, no
separate daemon, no cross-UID signal handling, no code-level branching between "privileged"
and "ordinary" commands — the tool is a pure passthrough, and the sudoers policy is the entire
enforcement mechanism for the privileged subset.

**Known, accepted limitation — deferred, not solved here**: whatever credentials `eisyai`
itself must read at startup (LLM API keys, hub auth) remain reachable via this tool too, since
it executes as that same account — no version of "run the tool as the app's own identity"
changes that; it's inherent, not a bug to design around. Passing a scrubbed `env=` when
spawning the subprocess (rather than inheriting the parent's full `os.environ`) is still worth
doing as a cheap partial mitigation for the in-memory/env-var half of this — but the
secrets-on-disk exposure, and a full audit of what else `eisyai`'s `eisyui` group membership
grants access to (e.g. `/var/udx` is `root:eisyui` mode `rwx--x---`, so group members already
get real access there), are explicitly **deferred** rather than solved by this design — see V3.

---

## Current design — direct execution as `eisyai`

### Files to add / change

| File | Change |
|---|---|
| `src/unified/tools/tool_shell_run.json` | New. Tool schema (auto-discovered — `runtime.py:37` globs `tool_*.json`, no registration needed here). |
| `src/unified/handlers/shell.py` | New. Handler module, single function `run_shell_command`. |
| `src/unified/dispatch.py` | Add `shell` to the handler import block (~line 10-21), add `"run_shell_command": shell.run_shell_command,` to `TOOL_HANDLERS` (~line 43-70). |
| `tests/unified/handlers/test_shell.py` | New. Unit tests, following `test_plugin_management.py`'s pattern of calling the handler directly. |
| `src/unified/prompt/definitions.md` | Optional: short steering paragraph, same style as existing diagnostics/plugin sections — should mention that `sudo`-prefixed commands work only for the specific operations `eisyai`'s sudoers policy allows, and a permission-denied result from `sudo` is normal, expected output, not a bug. |

Tool name: `run_shell_command` (bare verb in JSON `"name"`, domain-prefixed filename — matches
existing `node_op`/`send_command`/`preference_op` convention).

**Session-scoping**: do *not* add it to `_DIAGNOSTICS_EXEMPT_TOOLS`/`_PLAN_EXEMPT_TOOLS` in
`dispatch.py`. It's stateless (no hub session), so its handler keeps the plain
`(nucore_interface, args)` signature. Side benefit of leaving it out of the exempt sets: it's
automatically refused while a diagnostics/plan session is active, for free.

### JSON schema (`tool_shell_run.json`)

Three input fields only — deliberately not adding `stdin`/`env`/`background` mode, none of
which were asked for and each adds real complexity for no current need:

- `command` (string, required) — full command line, run via `bash -c`. May include `sudo` for
  the specific operations `eisyai`'s sudoers policy permits.
- `cwd` (string, optional) — defaults to the backend process's own cwd at call time.
- `timeout_s` (number, optional) — overrides the default timeout; clamped to a hard max, not rejected.

Description (LLM-facing, following the diagnostics-tool's descriptive style) must say plainly:
this is a general host-level escape hatch unrelated to IoX/device tools; there's no
command-content allowlist, so never run destructive/irreversible commands the customer didn't
actually ask for; a non-zero `exit_code` or non-empty `stderr` is normal command output, not a
tool failure — including `sudo: a password is required`/`command not allowed` style refusals
for operations outside `eisyai`'s sudoers policy — only a top-level `error` key means the
command itself couldn't run at all (e.g. bad `cwd`); check `truncated`/`timed_out` before
assuming full output was captured, and narrow+retry (`| head`, `| tail`, `| wc -l`) rather than
re-running the same unbounded command.

### Handler implementation (`src/unified/handlers/shell.py`)

- **Subprocess**: `asyncio.create_subprocess_shell(command, executable="/bin/bash", cwd=..., stdin=DEVNULL, stdout=PIPE, stderr=PIPE, start_new_session=True)` — native asyncio subprocess, not `subprocess.run` in a thread, because a real timeout needs clean cancellation/kill, not just a blocking call in a thread that leaks on timeout. `start_new_session=True` so `os.killpg` can kill an entire pipeline/subshell tree on timeout — this works reliably here specifically *because* there's no privilege crossing per call: the spawned tree is owned by `eisyai` throughout, the same account that spawned it, so ordinary same-user process-group signaling just works (this was the specific problem the rejected `sudo -u`-per-call design ran into and this design avoids by construction).
- **Environment**: pass an explicit, minimal `env=` (PATH/locale/TERM only) rather than inheriting the parent process's full environment, so secret-bearing env vars already loaded into the running process (e.g. via `load_dotenv`, see `run_unified_runtime.py:33-35`) aren't handed to every spawned command by default. Cheap, worth doing now; does not address the on-disk exposure (see Context above / V3).
- **Output capture**: two bounded reader coroutines (one per stream), reading in 64KB chunks, **slicing the final chunk at the exact `_MAX_OUTPUT_BYTES` boundary** so accumulated `stdout`/`stderr` are never more than `_MAX_OUTPUT_BYTES` — not "cap plus up to one chunk over." Continue reading-and-discarding past the cap rather than stopping the read loop entirely (an unread pipe fills the OS buffer and can deadlock a chatty child). Track `truncated` per stream. Decode UTF-8 with `errors="replace"`.
- **Timeout**: wrap readers + `proc.wait()` in `asyncio.wait_for(timeout=resolved_timeout)`. On timeout: `os.killpg(proc.pid, SIGKILL)` (guard `ProcessLookupError`), reap via `proc.wait()`, set `timed_out=True`, `exit_code=None`, return partial output with `truncated=True`. **Before implementing**, confirm `_MAX_TIMEOUT_S` doesn't exceed whatever timeout budget `AgenticLoop`/the customer-facing transport (WebSocket/HTTP) tolerates for a single tool call.
- **`cwd` validation**: check `Path(cwd).is_dir()` *before* spawning; if invalid, return `{"error": "..."}` without touching the subprocess — the one genuine handler-level error case.
- **Command-not-found / sudo refusal**: neither is special-cased — under `bash -c` these are just a nonzero exit code plus stderr text (127 for command-not-found; whatever `sudo` itself emits for a disallowed command), normal structured output. Only a failure to spawn `/bin/bash` itself is a handler `{"error": ...}`.
- **Return shape**: `{"command", "cwd", "exit_code", "stdout", "stderr", "timed_out", "truncated", "duration_s"}`.
- **Logging**: log every invocation (command text, exit code, duration) via the existing `get_logger` pattern `dispatch.py` already uses — baseline, not deferred to future hardening. Pure visibility, doesn't change tool behavior, and without it there's no way to detect abuse after the fact.
- **Concurrency**: no built-in limit in this design — worth an explicit call-out (see Risks) rather than a silent gap. A simple `asyncio.Semaphore` capping concurrent shell executions is a cheap addition if this ships.
- **Signature**: `async def run_shell_command(nucore_interface: NuCoreInterface, args: dict[str, Any]) -> Any` — `nucore_interface` unused (tool doesn't touch the hub), note that in a one-line comment.
- **Module docstring**: explain this runs as whatever account nucore-ai itself runs as (`eisyai`), that privileged operations are gated entirely by that account's sudoers policy rather than by any code in this module, and that nonzero exit (including sudo refusals) is data, not error.

Defaults as module-level constants (matches `node_ops.py`'s `_CREATE_OPS`/`_SIMPLE_OPS` style):
```python
_DEFAULT_TIMEOUT_S = 30
_MAX_TIMEOUT_S = 120
_MAX_OUTPUT_BYTES = 20_000   # per stream
_READ_CHUNK_BYTES = 65_536
```
No config-file plumbing — self-contained, single-file change; can thread into `runtime_config.py` later if it needs to be operator-tunable.

### Deployment prerequisite (outside this code change)

- Create the `eisyai` account (group `eisyui`), deploy nucore-ai as a pg3 node server under it, same launch mechanism as other node servers on this platform.
- Add `/usr/local/etc/sudoers.d/eisyai` naming the specific privileged commands `eisyai` may run beyond its native rights (mirrors the existing `admin`/`polyglot` sudoers.d files already on this host) — e.g. service start/stop/restart. Keep this list as narrow and explicit as the commands actually needed; this file *is* the tool's entire privilege boundary for anything beyond `eisyai`'s native permissions.

### Risks (accepted, not silently mitigated)

- **Full RCE reachable from customer-facing chat** — untrusted end-user text flows into the LLM's context; any prompt-injection payload that convinces the model to call this tool runs with the full privileges of `eisyai`, plus whatever the sudoers.d file additionally allows.
- **No confirmation gate**, unlike the codebase's one existing dangerous-action precedent (plugin install/buy/delete hand back a URL instead of acting directly). This tool does the opposite on purpose.
- **Blast radius = `eisyai`'s native permissions plus its sudoers.d grants** — including its own group (`eisyui`) memberships and whatever those grant access to, and its own necessary secrets (see Context — deferred, not solved here).
- **No concurrency/rate limit** — nothing stops multiple simultaneous long-running commands from stacking up CPU/memory use.
- **Timeout/truncation are availability safeguards only**, not security controls.

**Explicitly out of scope for this design:** command allowlist/denylist, sandboxing beyond the `eisyai` account boundary itself (jails/Capsicum — see V3), resource limits (ulimit/cgroups), a confirmation-gate for destructive-looking commands, prompt-injection likelihood scoring (see V3).

### Verification

1. New `tests/unified/handlers/test_shell.py`, calling `shell.run_shell_command(None, args)` directly:
   - success (`echo hello`) → `exit_code==0`, expected stdout, `timed_out/truncated` both `False`.
   - failing command (bad path) → nonzero `exit_code`, non-empty `stderr`, **no** top-level `error`.
   - command not found → `exit_code==127`, no top-level `error`.
   - timeout (`sleep 5` with `timeout_s=1`) → `timed_out=True`, `exit_code is None`, and confirm the process is actually reaped (no lingering `sleep` via `pgrep`) — should be reliable here since no UID crossing is involved.
   - large output (`yes | head -c 5000000`) → `truncated=True`, output capped at exactly `_MAX_OUTPUT_BYTES`, completes well under timeout.
   - invalid `cwd` → top-level `{"error": ...}`, and (via monkeypatch) assert `create_subprocess_shell` was never called.
   - env scrub → assert a sentinel var present in the test's own `os.environ` is *not* visible to a command like `env` run through the tool.
2. Dispatch wiring: `execute_tool("run_shell_command", {"command": "echo ok"}, nucore_interface=<stub>, session_id=None)` end-to-end, confirming `TOOL_HANDLERS` registration.
3. Schema sanity: confirm `tool_shell_run.json` parses and round-trips through the same `LLMAdapter.tools_spec_from_file` path `UnifiedRuntime.__init__` uses.
4. **Sudoers policy test (run on an actual `eisyai`-provisioned host, not in unit tests)**: a command using a sudo-gated action that IS on `eisyai`'s allowlist succeeds; one that is NOT on the list is refused by `sudo` itself (nonzero exit, stderr from `sudo`, no top-level `error`) — confirms enforcement is real at the OS level, not assumed.
5. Manual (optional): run `src/unified/run_unified_runtime.py` locally, send a message that should trigger `run_shell_command`, confirm the round trip through `AgenticLoop` and a sensible relayed answer.

---

## Superseded designs (kept for reference, not recommended)

**Privilege-separated socket-server daemon.** A separate long-running daemon under a *more*
restricted user than the main process, reachable only over a peer-authenticated unix socket.
Rejected because the actual deployment decision (`eisyai` as its own dedicated account) already
gives the tool an appropriately scoped identity without a second service, its own protocol,
peer-UID verification, and supervised lifecycle.

**Per-call `sudo -u <restricted-user>` privilege drop.** Spawning `sudo -u shelluser -- bash -c
command` directly from the handler, still in-process, no daemon. Solves secrets-in-env the same
way scrubbing `env=` does, but introduces a real, hard-to-verify problem: `os.killpg` +
`SIGKILL` on timeout no longer reliably reaches the target command once it's running under a
different UID than the caller (SIGKILL cannot be relayed by any process including `sudo`, and
modern `sudo` commonly runs its target in its own session, so the caller's process-group kill
may only terminate the `sudo` wrapper, not the real work). Rejected in favor of running natively
as `eisyai`, which keeps the whole process tree under one UID and makes the existing
`killpg`+`SIGKILL` approach reliable again.

---

## V3 — reserved / deferred

Explicitly deferred rather than solved by the current design (per this doc's Context section):

- **Audit of `eisyai`'s `eisyui` group-membership reach** — enumerate what else is
  `eisyui`-group-accessible (e.g. `/var/udx`) beyond what a vanilla pg3 node server would have,
  and confirm none of it is sensitive in a way that shouldn't be shell-tool-reachable.
- **Secrets-on-disk exposure** — whatever `eisyai` must read at startup to function remains
  readable via this tool; no version of "run as the app's own identity" changes that. Revisit
  if this needs a stronger boundary (e.g. a bootstrap process that injects only derived
  short-lived tokens rather than `eisyai` holding standing read access to raw credentials).

Other future candidates, not yet designed:

- **Domain-aware prompt-injection likelihood scoring** — a service call that scores a command
  (and ideally other customer-controlled context) for injection likelihood before execution,
  feeding a tiered allow/confirm/block policy. Complements this design's account-scoping rather
  than replacing it — narrows *how often* a dangerous command runs at all, on top of this
  design narrowing *what* a dangerous command can reach.
- **Confirmation-gate** mirroring `plugin_management.py`'s deferred-URL pattern for
  destructive-looking commands, possibly triggered by the injection-scoring signal above.
- **Jail-based hardening** (FreeBSD jails are already available on this platform —
  `jail`/`jexec`/`jls` present, kernel jail support confirmed) if a stronger boundary than the
  `eisyai` account itself is ever needed — filesystem-namespace isolation instead of identity,
  which would also sidestep any future cross-boundary signal-delivery concerns the same way
  running natively as `eisyai` does today.
- **Resource limits** (ulimit/cgroups) on spawned commands, and outbound network restriction
  for the `eisyai` account.
- **Concurrency/rate limiting** — a semaphore or similar cap on simultaneous shell executions,
  flagged but not designed above.

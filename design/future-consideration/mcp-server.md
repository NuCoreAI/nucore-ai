# MCP SERVER (PROPOSAL, 2026-09-17)

> **FOR FUTURE CONSIDERATION ONLY -- NOT IN USE.** No implementation exists yet. This document
> captures a design that was worked out and reviewed with the user, then deliberately parked here
> rather than built, so the research and decisions aren't lost. Nothing here reflects the current
> system.

## Purpose

Expose this project's existing 33 tools (`src/unified/tools/tool_*.json` +
`src/unified/dispatch.py`) over the **Model Context Protocol (MCP)**, so that any MCP-compatible
host (Claude Desktop, Claude Code, or anything else speaking MCP) can call them directly --
without going through this project's own `AgenticLoop`/LLM orchestration, since an MCP host
supplies its own.

Three decisions were confirmed with the user before designing:
1. **Transport**: MCP's Streamable HTTP transport (not stdio), because the server must be
   reachable by hosts that aren't necessarily on the same machine as the NuCore/IoX backend
   ("accessible to everyone... from anywhere").
2. **Auth**: the user will separately run/provide an OAuth 2.1 **Authorization Server** (internet-
   hosted or local) -- this MCP server is a **Resource Server only**: it validates bearer tokens
   issued elsewhere, and does not implement login/consent/token issuance itself.
3. **Authorization model**: tiered scopes, not all-or-nothing, given the real blast radius of some
   tools (`run_shell_command` = arbitrary shell on the backend host; `buy_plugin`-family = spends
   money or acts on the customer's behalf; `send_command` = real device control).

## Design

### New files

```
src/unified/backend_wiring.py     # extracted from run_unified_runtime.py (shared by both entry points)
src/unified/mcp/__init__.py
src/unified/mcp/scopes.py         # TOOL_SCOPE_TIERS + is_authorized()
src/unified/mcp/auth.py           # JWTBearerTokenVerifier
src/unified/mcp/server.py         # build_mcp_server(...)
src/unified/run_mcp_server.py     # CLI entry point
```

The new server is a thin adapter, nothing more: `tool_*.json` -> `ToolSpec` (existing loader,
`LLMAdapter.tools_spec_from_files`, already used by `runtime.py`) -> MCP `Tool` objects for
`list_tools`; MCP `call_tool` -> scope check -> **the existing, unmodified**
`dispatch.execute_tool` -> MCP result. It does **not** touch `UnifiedRuntime`, `AgenticLoop`,
`SessionStore`, the LLM adapters, or the fabrication guard -- none of that exists for this use
case, since the MCP host supplies its own orchestration.

### `src/unified/backend_wiring.py`

Pure extraction, no behavior change: move `_load_secrets_file`/`_load_backend_api`/
`_load_backend_api_cached` out of `run_unified_runtime.py` (already free functions, not trapped in
`main()`) into this new module, dropping the leading underscore since they become genuinely shared
surface. `run_unified_runtime.py` imports them back. Rationale for extracting rather than
duplicating: the two entry points need byte-identical backend-wiring behavior, and duplicating ~40
lines is pure drift risk (a fix in one copy silently not reaching the other).

### `src/unified/mcp/scopes.py`

```python
SCOPE_READ = "mcp:read"
SCOPE_CONTROL = "mcp:control"
SCOPE_ADMIN = "mcp:admin"
_TIER_RANK = {SCOPE_READ: 0, SCOPE_CONTROL: 1, SCOPE_ADMIN: 2}

TOOL_SCOPE_TIERS: dict[str, str] = {
    # mcp:read -- no side effects, including the two diagnostics tools (they
    # query PLM/link-table state, never send a device command or change any
    # device's operational state; verified against insteon_diag.py/diagnostics.py)
    "get_device_detail": SCOPE_READ, "get_device_history": SCOPE_READ,
    "get_property": SCOPE_READ, "get_core_services_status": SCOPE_READ,
    "get_device_family": SCOPE_READ, "get_full_system_config": SCOPE_READ,
    "get_group_detail": SCOPE_READ, "list_preferences": SCOPE_READ,
    "get_plugin_capabilities": SCOPE_READ, "list_installed_plugins": SCOPE_READ,
    "list_purchased_plugins": SCOPE_READ, "list_store_plugins": SCOPE_READ,
    "get_routine_details": SCOPE_READ, "get_time_info": SCOPE_READ,
    "list_variables": SCOPE_READ, "diagnostics_not_responding": SCOPE_READ,
    "diagnostics_no_status_feedback": SCOPE_READ,
    # mcp:control -- bounded, reversible state changes. install/buy/delete_plugin
    # land here (not mcp:admin) because today they don't mutate anything --
    # confirmed in plugin_management.py, each just resolves a real id and hands
    # back an install_url/purchase_url/delete_url for the customer to finish on
    # the web ("For security reasons, installation/deletion must be completed on
    # the web, not by an LLM"); revisit if a real install/purchase/delete API is
    # ever wired in. plugin_ops (start/stop/restart ONE plugin) also lands here,
    # not mcp:admin, since it's scoped/reversible unlike restart_core_service.
    "send_command": SCOPE_CONTROL, "node_op": SCOPE_CONTROL,
    "multi_device_scene": SCOPE_CONTROL, "group_scene_op": SCOPE_CONTROL,
    "pair_device": SCOPE_CONTROL, "preference_op": SCOPE_CONTROL,
    "create_or_update_routine": SCOPE_CONTROL, "routine_status_op": SCOPE_CONTROL,
    "variable_op": SCOPE_CONTROL, "install_plugin": SCOPE_CONTROL,
    "buy_plugin": SCOPE_CONTROL, "delete_plugin": SCOPE_CONTROL,
    "plugin_ops": SCOPE_CONTROL,
    # mcp:admin -- high/unbounded blast radius.
    "run_shell_command": SCOPE_ADMIN, "restart_core_service": SCOPE_ADMIN,
    # call_plugin invokes an arbitrary third-party plugin's own tool with
    # model-supplied args -- its real blast radius is plugin-defined and not
    # audited by this codebase, so it's treated as the risky end of the scale
    # rather than bucketed with NuCore's own well-bounded control tools.
    "call_plugin": SCOPE_ADMIN,
}

def is_authorized(tool_name: str, granted_scopes: set[str]) -> bool:
    """Hierarchical: mcp:admin implies mcp:control implies mcp:read, so the
    Authorization Server only needs to issue a client's single highest scope.
    Unknown tool name -> deny (fail closed)."""
    required = TOOL_SCOPE_TIERS.get(tool_name)
    if required is None:
        return False
    required_rank = _TIER_RANK[required]
    return any(_TIER_RANK.get(s, -1) >= required_rank for s in granted_scopes)
```

This mapping was checked key-for-key against `dispatch.TOOL_HANDLERS`'s real 33 keys (read
directly from `src/unified/dispatch.py:30-64`) -- all 33 present, no typos, no drift from the
user's original tiering except four corrections made during design review: diagnostics tools moved
down to `mcp:read` (they're investigative, never mutate device state); plugin install/buy/delete/
ops moved down to `mcp:control` (they don't actually mutate anything today, just hand back a URL
for the customer to finish on the web); `call_plugin` moved up to `mcp:admin` (its real blast
radius is plugin-defined and not audited by this codebase). Add a test asserting
`set(TOOL_SCOPE_TIERS) == set(dispatch.TOOL_HANDLERS)` so a future 34th tool fails loudly instead
of silently landing unscoped (and thus denied-by-default for everyone).

### `src/unified/mcp/auth.py` -- `JWTBearerTokenVerifier`

Validates JWT access tokens issued by the externally-run Authorization Server (JWT chosen as the
default per the user's instruction, since it's what most modern OAuth servers -- Auth0/
Keycloak/Okta/authentik -- issue; RFC 7662 opaque-token introspection is a documented, not-built,
extension point for the rare AS that doesn't do JWTs):

- Fetches signing keys from the issuer's JWKS endpoint (`--oauth-jwks-uri` override, else
  `<issuer>/.well-known/jwks.json`) via `PyJWT`'s `PyJWKClient(cache_keys=True, lifespan=300)` --
  no separate caching layer needed, the client handles refresh-on-miss itself. Run the (blocking)
  key lookup via `asyncio.to_thread(...)` since `verify_token` is async and this is per-request I/O
  on a cache miss.
- Verifies `iss` (must equal `--oauth-issuer-url`), `aud` (must equal `--oauth-resource-server-url`
  unless `--oauth-audience` overrides it), `exp`, with `--oauth-clock-skew-s` (default 60) leeway.
- Extracts granted scopes from the `scope` claim (space-separated string, OAuth-standard) or `scp`
  claim (list form, seen on some providers) as fallback -- auto-detect both, no extra config flag.
- Any `jwt.PyJWTError` (bad signature, expired, wrong issuer/audience, malformed) -> return `None`
  -> SDK turns this into a 401 with the standard `WWW-Authenticate` challenge.

### `src/unified/mcp/server.py` -- `build_mcp_server(...)`

- Loads `ToolSpec`s once at startup via the existing `LLMAdapter.tools_spec_from_files(sorted(
  (tools_dir).glob("tool_*.json")))` -- same loader, same files, zero duplication with `runtime.py`.
- `list_tools` handler: returns **all 33 tools, unfiltered by caller identity**, each description
  annotated with its required tier (`"[requires scope: mcp:admin]"`). Deliberate: `tools/list` and
  `tools/call` are independent JSON-RPC methods, so hiding a tool from the list provides no actual
  security -- the only real boundary is the per-call scope check below. Filtering the list is a
  UX-only nicety, addable later, not built now.
- `call_tool` handler: unknown tool name -> raise (protocol error). Known name -> look up its tier
  in `TOOL_SCOPE_TIERS`, compare against the authenticated caller's token scopes (attached by the
  SDK's own auth middleware before this handler runs) via `is_authorized`. Insufficient scope ->
  `CallToolResult(isError=True, content=[TextContent(text="insufficient scope: ...")])` -- a normal
  recoverable tool-call failure the host's own LLM can explain to its user, not a crash. Authorized
  -> call `dispatch.execute_tool(name, args, nucore_interface=nucore_interface)` **unchanged** ->
  wrap the dict result as both `TextContent` (JSON-serialized, for text-only clients) and
  `structuredContent` (for clients that read typed results directly); map `execute_tool`'s existing
  `{"error": ...}` convention onto MCP's `isError` flag.

### `src/unified/run_mcp_server.py` -- CLI entry point

A small, **purpose-built** argparse parser -- do not reuse `run_unified_runtime.py`'s full parser,
which carries ~20 flags (`--runtime-config`, `--query`, `--websocket-port`, `--max-iterations`,
etc.) describing the LLM-agentic-loop path that has no meaning here and would just confuse
`--help`. New flags: `--host`/`--port` (HTTP listener), `--oauth-issuer-url`/
`--oauth-resource-server-url` (both required, no sane default), `--oauth-jwks-uri`/
`--oauth-audience`/`--oauth-clock-skew-s` (optional, sensible defaults). Reused verbatim from
`run_unified_runtime.py` (via `backend_wiring.py`): `--backend-api-classpath`/
`--backend-api-base-url`/`--backend-api-username`/`--backend-api-password`, `--preferences-dir`
(needed -- `list_preferences`/`preference_op` degrade silently without it), `--json-output`,
`--log-level`/`--log-file`.

Startup sequence: load backend API -> build `nucore_interface` (process-wide, one instance -- see
Concurrency below) -> build `JWTBearerTokenVerifier` -> `build_mcp_server(...)` -> run its ASGI app
via `uvicorn.run(...)`.

### `pyproject.toml` / `requirements.txt` changes

Add (both files stay in sync manually today, matching current practice; pin to latest stable *at
implementation time*, not decided here): `mcp` (official SDK -- confirm whether the base package or
its `[cli]` extra is needed for the Streamable HTTP ASGI app, see Verification step 1),
`uvicorn` (explicit direct dependency since the entry point calls it directly), `pyjwt`, and
`cryptography` (PyJWT's RS256/ES256 dependency -- declare directly rather than via extras syntax,
matching this repo's flat `dependencies = [...]` list style). Add a `[project.scripts]` block (none
exists today):
```toml
[project.scripts]
nucore-mcp-server = "unified.run_mcp_server:main"
```

## Concurrency/safety

No new serialization layer needed. Confirmed directly in `run_unified_runtime.py`
(`_run_websocket_server`'s own docstring, lines ~579 and ~784): `nucore_interface` is **already**
shared process-wide across every concurrent WebSocket connection in production today -- this isn't
a new risk the MCP server introduces, it's the existing model. The one genuinely non-reentrant
resource, the PLM serial line, is already self-protecting: `_begin_plm_op`/`_end_plm_op` in
`src/iox/diagnostics/insteon_diag.py` fail-fast (refuse a second concurrent caller immediately with
a clean error) rather than queue or corrupt state. `NuCoreInterface.__init__` also holds
`self._subscribe_lock`/`self._event_listeners_lock` (`threading.Lock`, confirmed at
`nucore_interface.py:70,72`) around its event-subscription machinery, independent of caller count.
One process-wide `NuCoreInterface` shared across many concurrent authenticated MCP callers is
therefore consistent with this codebase's existing model.

Non-blocking note: `run_shell_command` (`mcp:admin`) spawns real OS subprocesses with no relation
to `NuCoreInterface` -- many concurrent admin-scoped callers means many concurrent shell processes,
an OS resource-exhaustion concern. Rate limiting is explicitly out of scope (below); flagged here
only so it isn't mistaken for an oversight.

## Testing

Unit-testable without a live OAuth server or live hardware, following this repo's existing
`FakeBackend(NuCoreInterface)` pattern (e.g. `tests/unified/handlers/test_diagnostics.py`) and the
`object.__new__(Cls)` bare-instance pattern used throughout `tests/`:

- `tests/unified/mcp/test_scopes.py` -- `TOOL_SCOPE_TIERS` keys exactly match
  `dispatch.TOOL_HANDLERS` keys; `is_authorized`'s hierarchy behavior (an `mcp:admin` grant passes
  an `mcp:read`-tier tool; an `mcp:read`-only grant fails an `mcp:control`-tier tool).
- `tests/unified/mcp/test_server.py` -- `build_mcp_server(...)` against a `FakeBackend` and a stub
  `TokenVerifier` returning a fixed `AccessToken`; assert all 33 tools appear in `list_tools`; a
  read-scoped token calling `send_command` gets `isError=True`; a control-scoped token calling
  `send_command` reaches `execute_tool` (assert via `FakeBackend`'s call-recording); an unknown tool
  name raises.
- `tests/unified/mcp/test_auth.py` -- self-signed JWT + local JWKS (generate an RSA keypair with
  `cryptography` in the test, monkeypatch the JWKS lookup to return the test key): right scopes
  extracted from both `scope`- and `scp`-claim shapes; expired/wrong-issuer/wrong-audience tokens
  all rejected.
- `tests/unified/test_backend_wiring.py` -- the extracted functions, carrying over whatever
  coverage `run_unified_runtime.py`'s existing tests already have for the pre-extraction versions.

Necessarily manual: a real OAuth token flow against the user's actual Authorization Server (confirm
the 401 + `WWW-Authenticate` + RFC 9728 protected-resource-metadata document for an unauthenticated
request), and an actual MCP host completing a real tool call end-to-end.

## Out of scope (explicitly, not just omitted)

- **The OAuth Authorization Server itself** (login/consent/token issuance/client registration) --
  the user's separate infrastructure.
- **TLS termination** -- flagged as a hard deployment requirement, not optional polish: the bearer
  token rides in a plain `Authorization` header, and `uvicorn.run(...)` as sketched above serves
  plain HTTP. A reverse proxy (nginx/Caddy/Traefik) or `--ssl-certfile`/`--ssl-keyfile` in front is
  required for any deployment reachable off the local machine -- which is the entire reason
  Streamable HTTP was chosen over stdio. Shipping without it defeats the auth model.
- **Rate limiting** and **audit logging** (beyond existing `logger.error`/`.info` calls) -- both
  reasonable, cheap follow-ups (`AccessToken.subject`/`client_id` are already available in
  `call_tool`) but not built now.
- **Per-tool argument-level authorization** (e.g. scoping a token to only certain devices/folders)
  -- the three tool-level tiers gate which tools a token can call, not which arguments.

## Open questions to resolve before implementation

These come from official MCP Python SDK documentation fetched via an automated summarizing tool,
which can drift from the real, installed package -- **before writing `server.py`/`auth.py` for
real, install `mcp` and directly inspect it** (`python -c "import mcp.server.lowlevel as l;
help(l.Server)"`, read the installed source) rather than trusting the sketch above verbatim:

1. Exact server construction API -- constructor kwargs (`on_list_tools=`/`on_call_tool=`) vs.
   decorators (`@server.list_tools()`/`@server.call_tool()`) vs. a different class name entirely
   (docs inconsistently referred to `Server` and `MCPServer`).
2. Where `token_verifier`/`AuthSettings` actually get wired -- server constructor vs.
   `streamable_http_app(...)` call vs. separate ASGI middleware.
3. How the authenticated `AccessToken` is actually exposed inside the `call_tool` handler (a `ctx`
   attribute vs. a context-var-based helper like `mcp.server.auth.get_access_token()`).
4. Whether the base `mcp` package pulls in `starlette`/`uvicorn` transitively, or whether `mcp[cli]`
   is needed for the Streamable HTTP app to work -- affects the exact `pyproject.toml` line.
5. **Scope hierarchy default** (`mcp:admin` implicitly covers `mcp:control`/`mcp:read`) -- this is
   this design's default; confirm it matches how the user's Authorization Server will actually
   issue scopes (single highest scope per client vs. every scope explicitly listed) before relying
   on it.

## Verification (when this is picked up)

1. `pip install mcp pyjwt cryptography uvicorn` (or the finalized pinned versions) into the venv;
   resolve Open Questions 1-4 above against the real installed package before finalizing
   `server.py`/`auth.py`.
2. `python -m pytest tests/unified/mcp/ tests/unified/test_backend_wiring.py -q`.
3. `python -m pytest tests/ -q` -- confirm no regression to the existing suite.
4. Manually run `python -m unified.run_mcp_server --oauth-issuer-url ... --oauth-resource-server-url
   ... --backend-api-...` and hit `/.well-known/oauth-protected-resource/...` unauthenticated
   (expect 401 + `WWW-Authenticate`), then a real token from the user's Authorization Server against
   `tools/list` and one read-tier `tools/call`.
5. Point a real MCP host (Claude Desktop/Code configured for a remote Streamable HTTP + OAuth
   server) at it and complete one real tool call end-to-end against live IoX/NuCore hardware.

# MCP SERVER (Phase 2: real authentication and authorization, implemented 2026-10-26)

> **Both Phase 1 and Phase 2 are now implemented and tested.** nucore-ai: `src/unified/mcp/server.py`,
> `src/unified/run_mcp_server.py` (the `nucore-mcp-server` console script),
> `src/nucore/nucore_interface.py`/`src/iox/iox_wrapper.py` (the four new hub-auth methods, Phase 2),
> and `tests/unified/mcp/test_server.py`/`tests/iox/test_hub_auth.py`. eisy-ai (the consumer repo):
> `mcp_proxy/` (`auth.py`, `proxy.py`, `run_proxy.py`, `oauth.py`, `oauth_store.py`,
> `isy_direct_address.py`, all Phase 2 except `proxy.py`'s relay logic) and `tests/mcp_proxy/`, wired
> into `start_eisyai.py` behind the `mcp_server: true` runtime-config flag described under "Phase 1
> -- consumer repo" below. **`AllowAllAuthHook` is retired from production use** -- see "Phase 2 --
> real authentication and authorization" below for what replaced it, and that section's own "Threat
> model" subsection for what this does and does not protect against (it closes the "anyone on the
> LAN, zero credential" gap; it does not, and cannot, defend against code already executing inside
> eisy-ai's own process -- a pre-existing ceiling, not something this phase could have changed). This
> document still captures the full original Phase 1 design so the research and decisions aren't
> lost. It supersedes all earlier versions of this file.

## How this design got here

Earlier passes considered exposing nucore-ai's tools over MCP via a network-reachable Streamable
HTTP server authenticated by either an external OAuth 2.1 Authorization Server, or a static
pre-shared bearer token. Both were rejected: several tools (`run_shell_command`, `call_plugin`,
`restart_core_service`) are effectively full-system control, and the *existing* trust boundary for
anything that dangerous is not an application-level credential at all -- it's the Unix-socket +
OS-level peer-UID check `_run_websocket_server` already uses (`run_unified_runtime.py:898-1049`,
`_get_unix_peer_uid` at `:711-725`). A standalone bearer secret reachable from anywhere on the LAN
is a materially weaker boundary than that. The fix: don't touch that boundary at all. Model the MCP
server exactly like the existing websocket listener (Unix socket, peer-UID check, no app-level auth
needed on nucore's side), and put the one genuinely new, network-facing piece -- a pass-through
proxy -- in the consumer repo (eisy-ai), with authentication deliberately deferred to a later phase
behind a pluggable, swappable hook.

This reverses an intermediate decision from the same design conversation that nucore-ai should not
be touched at all for this feature -- nucore-ai now hosts the real MCP server, since that's where
the tool-dispatch logic and the Unix-socket pattern already live. It also drops the
`mcp:read`/`control`/`admin` scope-tiering concept from earlier versions of this doc: tiering
existed only to contain a weak standalone credential that no longer exists in this design. Nucore's
MCP server now trusts anything that reaches its Unix socket completely, exactly like the
chat/websocket path already does today.

**Phase 1 shipped zero authentication on the one real network port in this design, by deliberate
choice.** Its job was to prove the plumbing end-to-end (client -> TCP proxy -> Unix socket ->
nucore's real tool dispatch) with an authentication hook that was a structural no-op stub. Phase 2
(see that section below) implements the real hook -- mostly as a drop-in swap of that one hook, as
originally planned, plus a handful of narrow, explicitly-flagged exceptions where reality needed
slightly more than a single-method swap.

Verified directly against the real installed packages (not assumed): `uvicorn.run()` supports a
`uds=` parameter, `httpx.AsyncHTTPTransport` supports `uds=` (httpx 0.28.1), and `starlette` is
available for a minimal proxy ASGI app. The `mcp` SDK package is now a direct dependency
(`pyproject.toml`/`requirements.txt`) and its real API (`mcp.server.lowlevel.Server`,
`mcp.server.streamable_http_manager.StreamableHTTPSessionManager`, `mcp.types`) is what
`src/unified/mcp/server.py` is actually built against -- see that module for the resolved shape,
not the sketch below.

## Tool inventory (unchanged from the previous version's analysis)

Both tool sets stay merged: customer (`unified.dispatch.TOOL_HANDLERS`, 36 tools) + plugin-authoring
(`unified.plugin_authoring.dispatch.build_tool_handlers(...)`, 20 tools of its own, 8 more reused
verbatim from the customer set) = 56 unique tools, 57 with `search_web` configured. See "Tool
inventory and registry mechanics" in this design's history (git blame this file) for the full
per-tool breakdown and import-path details; none of that changed in this revision, only the
authentication/transport model and where the code lives.

## Architecture

```
MCP client (LAN, must complete an OAuth 2.1 login against the hub's own real credentials -- Phase 2)
   --HTTP over TLS (consumer's own cert)-->
consumer repo (e.g. eisy-ai) :8260 -- a pass-through proxy + OAuth 2.1 wrapper (Phase 2)
   - oauth.py: /.well-known/*, /register, /authorize, /token, /revoke -- wraps the hub's own
     /api/login, /api/jwt/refresh, /api/jwt/logout (design/iox_apis/authentication.md)
   - AuthHook.authenticate(request) -> bool   [Phase 2: HubBearerAuthHook, replays the
     presented bearer token against the hub's own GET /rest/time]
   - on True: forward request via httpx.AsyncClient(transport=AsyncHTTPTransport(uds=...)),
     stream the response back verbatim
   --Unix domain socket (new; peer-UID checked, same process's own uid)-->
nucore-ai -- unified.run_mcp_server (new)
   - build_mcp_server(...): merges customer_dispatch.TOOL_HANDLERS + plugin_authoring's
     build_tool_handlers(...), list_tools/call_tool handlers, NO scope tiering
   - also serves list_resources/read_resource -- both tool sets' full system prompts,
     read-only, see "Known issues found and fixed" below
   - call_tool independently forces confirmed:false for the 5 delete-type tools/operations
     runtime.py's chat-path wrapper also gates, before dispatch -- same section below
   - advertises itself to clients as "eisy-ai" (server_name), not "nucore-ai"
   - served via uvicorn with uds= instead of host/port
   - runs as a thread inside the SAME consumer process, started the same way that process
     already starts its existing unified-runtime thread
```

nucore-ai's half of this is consumer-agnostic: it only needs a Unix-socket path and backend-api
wiring, the same inputs `run_unified_runtime.main` already takes. The proxy half (TLS, the network
port, the auth hook, process wiring) is deliberately left to whichever repo embeds nucore-ai, since
that's where TLS certs, process lifecycle, and (eventually) the real authentication mechanism
already live.

## Phase 1 -- nucore-ai

**Status: implemented**, in `src/unified/mcp/server.py` (`build_mcp_server`/`build_mcp_protocol_server`),
`src/unified/run_mcp_server.py` (the `nucore-mcp-server` entry point and its own Unix-socket
bind/chmod/chown), and `tests/unified/mcp/test_server.py`. The steps below are the original
implementation plan, kept for the historical reasoning; where it differs from the real code, the
real code wins.

1. **Spike, first**: `pip install mcp` (uvicorn/httpx/starlette already available); inspect the
   real installed `mcp` API (`help(mcp.server.lowlevel.Server)`, etc.) before writing `server.py`
   for real. Also resolve here: how to get the equivalent of `_get_unix_peer_uid` for a
   uvicorn/ASGI-served Unix socket -- the raw `websockets` library exposed
   `websocket.transport.get_extra_info("socket")` directly; confirm whether uvicorn's ASGI `scope`
   exposes an equivalent raw socket handle, or whether the socket needs to be pre-created/bound/
   chmod'd manually (`socket.socket(AF_UNIX)`, bind, `chmod 0o660`, `chown` to this process's own
   uid/gid -- mirroring `_run_websocket_server`'s existing unix-socket handling at
   `run_unified_runtime.py:1027-1048`) and handed to uvicorn via its `fd=` parameter instead of
   `uds=`, so the peer-UID check can happen per-connection the same way it does today.
2. New module `src/unified/mcp/server.py` -- `build_mcp_server(nucore_interface,
   plugin_output_root, search_engine=None, search_engine_api_key=None, secret_values=None)`:
   - `merged_handlers = dict(customer_dispatch.TOOL_HANDLERS)`, updated with
     `plugin_authoring.dispatch.build_tool_handlers(ledger=EvidenceLedger(), ..., get_user_id=None)`.
   - Tool specs loaded from both tools directories (`Path(customer_dispatch.__file__).parent /
     "tools"` and `plugin_authoring.TOOLS_DIR`), filtered to `merged_handlers` keys -- drops both
     orphan switch-tool files (58 -> 56/57 specs).
   - `list_tools`: all filtered specs, unmodified (no scope annotation -- there are no tiers now).
   - `call_tool`: unknown name -> protocol error; known name -> `await
     plugin_authoring.dispatch.execute_tool(name, args, nucore_interface=nucore_interface,
     tool_handlers=merged_handlers)` unmodified; wrap as `TextContent`+`structuredContent`; map
     `{"error": ...}` to `isError=True`.
3. New entry point `src/unified/run_mcp_server.py` -- `main(args=None, poly=None)`, same
   pre-built-`Namespace` convention as `run_unified_runtime.main`. Builds `nucore_interface` via
   the sibling `_load_backend_api`/`_BACKEND_API_JSON_OUTPUT`. Serves `build_mcp_server(...)`'s app
   via `uvicorn.run(app, uds=args.unix_socket_path, ...)` (or `fd=`, per the spike's finding), then
   applies the same unlink-stale-socket / chmod 0o660 / chown-to-own-uid-gid handling
   `_run_websocket_server` already does, and the same `client_uid` peer check per connection.
4. Packaging: add `mcp` to `pyproject.toml`/`requirements.txt` (uvicorn already present); version
   bump. The production publish/upgrade mechanism for nucore-ai in whatever consumer deploys it was
   never confirmed in earlier design rounds -- still flagged, not resolved here.
5. Tests: `tests/unified/mcp/test_server.py` against the existing `FakeBackend` pattern --
   `list_tools` returns the full merged set (56, 57 with `search_web`), `call_tool` dispatches
   correctly for a member of each original tool set, unknown name -> protocol error. No scope
   tests (nothing to test).

## Phase 1 -- consumer repo (e.g. eisy-ai)

**Status: implemented**, in eisy-ai's `mcp_proxy/` package (`auth.py`, `proxy.py`, `run_proxy.py`)
and `tests/mcp_proxy/`, with `start_eisyai.py` spawning both the nucore-ai MCP thread and this
proxy's thread behind the `mcp_server: true` runtime-config flag (documented in eisy-ai's
`README.md`/`POLYGLOT_CONFIG.md`). Same caveat as the nucore-ai section above: the steps below are
the original plan, kept for the reasoning; the real code wins where it differs (it mostly doesn't --
this one came together close to the sketch).

1. New package (e.g. `mcp_proxy/`):
   - `auth.py`: `class AuthHook(Protocol): async def authenticate(self, request) -> bool` +
     `class AllowAllAuthHook: async def authenticate(self, request) -> bool: return True` -- the
     Phase 1 default, with a loud docstring/log line marking it as a stub with no real security,
     not something to ship toward a real customer-facing unit.
   - `proxy.py`: minimal Starlette ASGI app, one catch-all route: call
     `auth_hook.authenticate(request)` -> `False` -> `403`; `True` -> forward the request via
     `httpx.AsyncClient(transport=httpx.AsyncHTTPTransport(uds=socket_path))` to nucore's Unix
     socket, stream the response (status/headers/body, including chunked/SSE-style bodies for
     Streamable HTTP) back to the client verbatim.
   - `run_proxy.py`: `main(args=None)` -- builds the configured `AuthHook` (Phase 1:
     `AllowAllAuthHook()`), builds the proxy app, `uvicorn.run(app, host="0.0.0.0", port=8260,
     ssl_certfile=..., ssl_keyfile=...)`.
2. Process wiring in whatever launches the consumer's existing unified-runtime thread today:
   - Single config gate (e.g. a `mcp_server: true` boolean, default false/absent) wraps spawning of
     *both* new threads together -- nucore's Unix-socket MCP thread and the proxy thread. No
     partial-enablement state.
   - Agree on one Unix-socket path both sides use (e.g. `/tmp/mcp-listener`, sibling to whatever
     the existing unified-runtime thread already uses).
   - Spawn nucore's new MCP thread with a `client_uid` set to this same process's own uid (mirrors
     the existing websocket thread's `websocket_client_id` role) and backend-api args
     **byte-identical** to the existing runtime thread's args, so nucore's
     `_load_backend_api_cached` lru_cache hands both threads the same `NuCoreInterface` instance --
     no second backend connection.
   - Spawn the proxy thread bound to port 8260, with TLS cert/key from whatever self-signed-cert
     helper the consumer already has, and `auth_hook = AllowAllAuthHook()`.
3. Packaging: add `starlette`, `uvicorn`, `httpx` as direct dependencies (even though they may
   already be transitively present via nucore-ai, this repo's own new code imports them directly).
   If the consumer has an explicit packaged-file manifest (e.g. a release-packaging script), make
   sure the new proxy package is added to it -- easy to forget, would silently ship nothing.
4. Tests: `AllowAllAuthHook` returns `True`; a proxy test forwards a request to a trivial fake ASGI
   app bound to a temp Unix socket within the test itself and asserts the response is relayed
   verbatim -- no real nucore dependency needed for this specific test.

## Known issues found and fixed after initial implementation

Four real problems surfaced only once both halves existed and were actually exercised -- none of
them hypothetical, all reproduced and fixed directly:

1. **uvicorn's own logging setup crashed under eisy-ai's real embedding.** `udi_interface` (the
   third-party PG3 package eisy-ai is built on) unconditionally replaces `sys.stdout`/`sys.stderr`
   with its own `LoggerWriter` objects at import time (`write`/`flush` only, no `isatty()`). Both
   `run_mcp_server.py`'s `uvicorn.Config(...)` and `mcp_proxy/run_proxy.py`'s `uvicorn.run(...)`
   left `use_colors` at its default (`None`), so uvicorn's `DefaultFormatter.__init__` fell through
   to calling `sys.stdout.isatty()` directly -- `AttributeError`, re-raised by `logging.config
   .dictConfig` as `ValueError: Unable to configure formatter 'default'`, on every single startup
   once `mcp_server: true`. Fixed in both files: `use_colors = sys.stdout.isatty() if
   hasattr(sys.stdout, "isatty") else False`, passed explicitly instead of left as the default.
2. **The proxy's default 5-second timeout broke any slow tool call.** `mcp_proxy/proxy.py`'s
   `httpx.AsyncClient` had no `timeout=` at all, so it inherited httpx's flat 5-second default on
   every leg (connect/read/write/pool). Any MCP tool call that legitimately runs -- or goes quiet
   mid-stream for -- more than 5 seconds (any LLM-backed tool, easily) tripped `httpx.ReadTimeout`
   partway through `_stream_body`'s `aiter_raw()` loop, breaking the response mid-stream. Fixed:
   `timeout=None` on that client -- this proxy is explicitly "a dumb pass-through... transport
   only" by its own docstring, so it shouldn't impose a tighter deadline than the real MCP client's
   own patience, especially over a local, trusted Unix socket.
3. **The MCP transport silently dropped context and a safety floor that the chat path has.** Found
   while debugging a real interaction where an MCP-driven model had no device ids and shelled out to
   `/rest/nodes` directly instead. Two distinct gaps, both because `build_mcp_protocol_server`'s
   `call_tool` calls `plugin_authoring.dispatch.execute_tool(...)` directly, bypassing everything
   `UnifiedRuntime.handle_query` normally wraps around a tool call:
   - *Informational*: neither tool set's system prompt (CRITICAL RULES, per-concept rules, DEVICE
     DATABASE/ROUTINES DATABASE, host environment, developer-identity/evidence rules, ...) ever
     reached an MCP client -- the MCP server never called either `prompt_builder` module, and
     exposed no resources/prompts at all. Fixed by registering `list_resources`/`read_resource` on
     the same `Server`, returning `eisy-ai://system-prompt/unified` and
     `eisy-ai://system-prompt/plugin-authoring` -- each one calling that tool set's own existing
     prompt-builder function verbatim (`prompt_builder.build_system_prompt(nucore_interface)` /
     `plugin_authoring.build_system_prompt_sections(nucore_interface)`), so there's no new rendering
     logic and no drift risk from the chat path's own prompts. **Follow-up, found testing against
     Claude Desktop**: MCP resources are something a *human* attaches via that client's own UI --
     the model has no way to call `resources/read` on its own initiative there, so the fix above
     never actually reached it in practice (confirmed: the model's own narration never even
     mentioned trying). Added a second, tool-shaped way to get the exact same content: a new
     `get_system_prompt` tool (`tool_set: "unified" | "plugin_authoring"`), handled directly inside
     `_call_tool` (not routed through `plugin_authoring.dispatch.execute_tool`/`merged_handlers` --
     the chat path never needs it, since it already has this standing in context automatically) and
     calling the exact same `_render_system_prompt` helper the resources now also share, so both
     surfaces stay byte-identical by construction. Keep both: resources are still the spec-correct
     primitive and work on any client that does surface them; the tool is the one every MCP client's
     model can reliably call on its own, regardless of that client's resource-surfacing choices.
   - *Safety*: `runtime.py`'s `_wrap_dispatch_for_delete_confirmation` independently forces
     `confirmed` to `False` for `delete_plugin`/`delete_registered_plugin` and
     `node_op`/`routine_status_op`/`variable_op` (`operation == "delete"`) unless the *human's own
     literal chat message that turn* was exactly `"YES"` -- but it's only ever applied inside
     `UnifiedRuntime.handle_query`, which the MCP path never reaches. (Checked directly: all five
     already have their own independent handler-level preview-then-confirm gate, so an MCP caller
     was never able to one-shot delete -- but an MCP-driven model could preview-then-confirm on its
     own say-so, with no real human ever required to approve it.) Fixed by a new, MCP-only
     `_deny_mcp_delete_confirmation` helper in `server.py` that forces `confirmed: False` for that
     exact same allowlist (imported read-only from `runtime.py`) before every `call_tool`
     dispatch -- not an attempt to invent a weaker MCP-native "confirmation" concept (there's no
     human chat turn on this transport to check), just an honest "irreversible deletes aren't
     available over MCP in this phase."
4. **The proxy leaked its upstream connection on every downstream client disconnect.**
   `mcp_proxy/proxy.py`'s streaming generator (now `_stream_upstream_body`, originally an inline
   `_stream_body` closure) called `await upstream_response.aclose()` *after* its `async for`
   loop, with no `finally`. When the downstream MCP client disconnects while the proxy is still
   streaming bytes back from the upstream -- routine for a long-lived Streamable HTTP connection,
   not rare -- uvicorn cancels the whole per-request task mid-read, skipping that trailing
   `aclose()` entirely and leaking the upstream httpx response and its Unix-socket connection-pool
   entry. Surfaced as an uncaught `asyncio.CancelledError` propagating out of `_stream_body` during
   live testing. Fixed: wrapped the loop in `try`/`finally` so cleanup always runs regardless of how
   the generator exits; extracted the generator to module level (`_stream_upstream_body`, taking
   `upstream_response` as a parameter) so the cleanup guarantee has a direct unit test
   (`tests/mcp_proxy/test_proxy.py`) rather than depending on reproducing a real network disconnect.
5. **The delete-preview response's own wording told an MCP-driven model to do something that can
   never work.** Item 3's safety fix forces `confirmed: False` for the five gated delete
   tools/operations, so each one always resolves to its own handler's existing
   `confirmation_required: True` preview -- but that handler's message is written for the chat
   path, where retrying with a real human's literal "YES" *does* get the delete through. Over MCP
   there is no human chat turn to produce, so that promise is a dead end: a real client retried the
   call after relaying a real "YES" from the customer, got back the identical preview, and had no
   way to tell this apart from a transient failure worth retrying again. Fixed in `_call_tool`:
   whenever a dispatched call is one of the five gated tools/operations (`_is_delete_confirmation_attempt`,
   factored out of `_deny_mcp_delete_confirmation` so both share the exact same check) and the
   handler's own result comes back with `confirmation_required: True`, an `mcp_note` field is added
   to the result explaining plainly that this is always preview-only over MCP by design, retrying
   with `confirmed: true` will never change the outcome, and the customer needs to delete from the
   eisy's own UI directly (or use a non-destructive alternative like disable). Nothing about the
   underlying handler or the chat path changes -- the note is additive, MCP-only context.
6. **A non-dict handler result (e.g. a plain list) crashed instead of returning.** The SDK's own
   `call_tool` decorator auto-wraps a plain `dict` result as both `structuredContent` and a JSON
   `TextContent`, which `_call_tool` relied on for every result shape -- but `get_routine_details`'s
   own documented return shape is "a list, one entry per id", not a dict. Confirmed directly against
   the installed SDK: handed a plain list of business-data dicts, its auto-wrap heuristic assumes
   it's already a list of real MCP content blocks and tries to build `CallToolResult.content`
   straight from them, which fails pydantic validation with a wall of "Field required" errors -- a
   real client saw this as an opaque response-format error with no way to tell it was this server's
   bug, not theirs. Fixed: `_call_tool` now checks for a dict result explicitly and, for anything
   else, builds the `CallToolResult` itself (`structuredContent={"result": result}` -- confirmed
   against the installed SDK's own type annotation that `structuredContent` must itself be a dict --
   plus a JSON `TextContent`) instead of leaning on the SDK's two-shape auto-wrap heuristic.

**Explicit constraint this fix was built under, worth preserving here**: both fixes under item 3
live entirely inside `src/unified/mcp/server.py` (plus its own test file) -- zero edits to
`runtime.py`, either `prompt_builder.py`, either `dispatch.py`, or any handler file. The chat and
MCP paths stay two genuinely separate execution paths that happen to read from the same source
modules, not one path patched to special-case the other.

## Explicit modularity contract for the next phase

- `AuthHook` is the **only** seam the next phase touches in the consumer repo: implement a new
  class satisfying `authenticate(request) -> bool` (validating whatever real mechanism gets
  decided -- platform session, JWT, mTLS, etc.) and swap it into `run_proxy.main`'s construction.
  Zero changes to `proxy.py`'s relay logic. Zero changes to nucore-ai at all -- nucore's Unix
  socket keeps trusting anything that reaches it, exactly as it does today for the chat/websocket
  path.
- If a future phase needs to carry per-caller identity/role into nucore's tool-dispatch decisions
  (e.g. different customers get different tool access), that would require widening `AuthHook`'s
  return type beyond a boolean and reintroducing a scope-tiering layer on nucore's side -- noted as
  a possible later extension, not designed or built now. Phase 2 (below) deliberately stays
  all-or-nothing, consistent with this.

## Phase 2 -- real authentication and authorization

**Status: implemented.** Reuses what already existed rather than inventing a new identity system:
the hub (the eisy device itself) already has a real login -- `POST /api/login` and
`POST /api/jwt/refresh`/`POST /api/jwt/logout` (see `design/iox_apis/authentication.md`) -- the
same thing that already gates "a logged-in session" today. Wrapped in a thin OAuth 2.1 façade
because real MCP clients require it: Claude Desktop's remote-MCP connector performs full OAuth 2.1
discovery + dynamic client registration + PKCE with **no fallback** to a static bearer header
(confirmed via research), so a bare bearer-token scheme would have locked out the clients people
actually use.

1. **nucore-ai: four new `NuCoreInterface`/`IoXWrapper` methods** (`src/nucore/nucore_interface.py`,
   `src/iox/iox_wrapper.py`), one more than the modularity contract above literally anticipated,
   flagged here for that reason:
   - `verify_bearer_token(access_token)`: replays the token as `Authorization: Bearer` against
     `GET /rest/time` (unaffected by the relay restriction below -- `/rest` routes accept traffic
     over the same PG3-relay Unix socket `IoXWrapper`'s other methods already use). Returns
     `True`/`False`/`None` (hub-confirmed / hub-rejected / hub-unreachable -- callers must not
     conflate the last two).
   - `login_with_credentials(username, password, base_url)`, `refresh_access_token(refresh_token,
     base_url)`, `logout(refresh_token, base_url, access_token=None)`: thin wrappers around
     `/api/login` (Mode 3, `{"username", "password", "local": true}` -- local, offline-capable
     login), `/api/jwt/refresh`, `/api/jwt/logout`. **Each takes an explicit `base_url`, deliberately
     not `self.base_url`**: `design/iox_apis/authentication.md` documents `/api/login`/`/api/jwt/*`
     as "local access only" -- requests routed through the PG3-relay Unix socket
     (`/tmp/eisyui-listener`) that `IoXWrapper` is otherwise configured to use in eisy-ai's actual
     deployment are silently ignored. Resolving a genuinely direct hub address is therefore the
     caller's job (see eisy-ai's `isy_direct_address.py` below), not something these methods attempt
     themselves.
   - Tests: `tests/iox/test_hub_auth.py`, against a real local `http.server.HTTPServer` (same
     precedent as `tests/iox/test_http_delete_with_body.py`), not a stubbed client.
2. **eisy-ai: `mcp_proxy/isy_direct_address.py`** (new) -- `DirectHubAddressResolver`, resolving the
   hub's real, non-relayed `isy_ip`/`isy_port`/`isy_https` by replicating the exact
   `poly.subscribe(poly.ISY, ...)` + `poly.send({'getIsyInfo': {}}, 'system')` pattern already proven
   correct in two other places (`udi_interface`'s own `ISY` class, and `IoXWrapper`'s own poly-mode
   constructor branch) -- not a new mechanism, just invoked a second time from `start_eisyai.py`'s
   own scope, using the real `polyglot` object `start_eisyai()` already receives as a parameter but
   previously never used.
3. **eisy-ai: OAuth 2.1 wrapper, `mcp_proxy/oauth.py` + `mcp_proxy/oauth_store.py`** (new) --
   `GET /.well-known/oauth-authorization-server` (RFC 8414) and `GET /.well-known/oauth-protected-
   resource` (RFC 9728, added beyond the original scope -- current MCP clients discover the
   authorization server via this document, named in the 401 challenge below), `POST /register`
   (RFC 7591 dynamic client registration, persisted to `persist/mcp_oauth_clients.json` so a
   registered client survives a restart), `GET`/`POST /authorize` (exact-match `redirect_uri`
   validation; the POST calls `login_with_credentials` and mints a single-use `AuthorizationCode`
   on success, re-renders the login form with a distinct error for invalid credentials / hub still
   starting up / hub unreachable otherwise -- never one generic "login failed"), `POST /token`
   (`authorization_code` grant verifies PKCE S256 and repackages the code's already-obtained JWT
   pair; `refresh_token` grant calls `refresh_access_token` directly), and `POST /revoke` (optional,
   RFC 7009-shaped, maps to `logout`). `/authorize` renders its own minimal login form rather than
   redirecting to an existing hub-hosted one -- no such page was found in either repo; worth a
   direct check against the real hub if one does exist there.
4. **eisy-ai: the real `AuthHook`, `mcp_proxy/auth.py`'s `HubBearerAuthHook`** -- extracts
   `Authorization: Bearer <token>`, caches only confirmed-good results (keyed by a hash of the token
   itself, TTL safely below the hub's documented 1-hour access-token lifetime) to avoid hitting the
   hub on every single tool call within one session, and otherwise calls `verify_bearer_token`.
   `AllowAllAuthHook` is retired from `start_eisyai.py`'s production wiring entirely -- it remains
   only for this package's own unit tests and a bare local/dev invocation with no real
   `NuCoreInterface` to authenticate against at all.
5. **eisy-ai: one narrow, flagged exception to "zero changes to `proxy.py`'s relay logic."**
   `_ProxyApp.__call__`'s deny branch changed from a bare `403` to a `401` with
   `WWW-Authenticate: Bearer resource_metadata="<issuer>/.well-known/oauth-protected-resource"` --
   needed because an OAuth-requiring client relies on exactly this challenge to know to start the
   discovery flow at all; a bare 403 gives it nothing to act on.
6. **Wiring, `start_eisyai.py`**: calls the same `_load_backend_api(...)` the other two threads
   already call (byte-identical `backend_api_*` args), synchronously, in the main thread, to get a
   reference to the same cached `NuCoreInterface` singleton (`functools.lru_cache` is thread-safe,
   so it doesn't matter which of the three call sites populates the cache first) -- no new
   nucore-ai plumbing needed for this. Constructs `DirectHubAddressResolver(polyglot)` once, and
   passes both `nucore_interface` and the resolver's `.get` method into the proxy thread's
   `run_proxy.main(...)` call as new keyword arguments. **No new user-facing config key**:
   `mcp_server: true` always means the real `HubBearerAuthHook` from here on -- keeping an
   easily-reachable insecure fallback in the documented config surface would have recreated the
   exact footgun this phase exists to close.
7. **Delete policy: unchanged.** The 5 existing delete-gated tools/operations (item 3/5 under "Known
   issues" above) stay permanently preview-only over MCP regardless of authentication. Auth gates
   *access*, not that separate safety floor -- a deliberate decision, not an oversight.

### Threat model: what Phase 2 does and doesn't protect against

The right baseline is the system's own existing chatbox/websocket path, not an idealized "defend
against all RCE everywhere" bar that nothing in this codebase already meets. `IoXWrapper` only ever
obtains real hub credentials by being a PG3-registered, customer-approved plugin (via Polyglot's
`GetISYInfo`) -- so code execution inside eisy-ai's own process already has full tool-dispatch and
hub-credential access via the *existing* chat/websocket path, with or without MCP, with or without
any `AuthHook`. That ceiling is pre-existing and out of scope here; no auth mechanism on the MCP
proxy changes it, because it was never the threat an `AuthHook` addresses.

What Phase 2 *does* close: before it, reaching nucore-ai's tool-dispatch over MCP required nothing
at all (`AllowAllAuthHook`) -- "mere LAN reachability, zero skill, zero credential," a strictly
larger and easier attack surface than the chatbox ever had (which always required either a
legitimate customer hub login, or that same pre-existing RCE-level plugin compromise). The real
OAuth 2.1 + JWT layer restores the "legitimate customer hub login" requirement for MCP, bringing its
exposure back to the same ceiling the rest of the system already accepts. One honest, non-blocking
asymmetry: the chatbox's Unix-socket mode gets a second, bonus layer (the `getpeereid()` UID check
restricting callers to specifically eisy-ui) that MCP structurally can't replicate, since it's open
to arbitrary external clients rather than one fixed first-party caller.

Explicitly out of scope, left as a separate future hardening effort, not a blocker for this phase:
process/jail-separating nucore-ai's MCP server from eisy-ai's own process, which is the only thing
that would ever change the pre-existing in-process-RCE ceiling described above.

## Concurrency/safety (carried over, still applies)

No new serialization layer needed. `nucore_interface` is already shared process-wide across every
concurrent WebSocket connection in production today (`_run_websocket_server`'s own docstring); one
process-wide `NuCoreInterface` shared across many concurrent MCP callers is consistent with this
codebase's existing model. The PLM serial line is already self-protecting
(`_begin_plm_op`/`_end_plm_op` in `src/iox/diagnostics/insteon_diag.py` fail-fast on a second
concurrent caller). `run_shell_command` spawns real OS subprocesses with no relation to
`NuCoreInterface` -- many concurrent callers reaching it means many concurrent shell processes, an
OS resource-exhaustion concern independent of the auth question. Rate limiting and audit logging
remain explicitly out of scope for this phase, flagged so they aren't mistaken for an oversight.

## Verification

Automated: `pytest` in both repos for the new/updated modules (`tests/unified/mcp/test_server.py`,
`tests/iox/test_hub_auth.py`, `tests/mcp_proxy/test_oauth.py`, `test_auth.py`, `test_proxy.py`).

Manual, end-to-end, with the config flag on in a test environment:
1. Confirm the Unix socket exists with the expected permissions, and that a connection attempt from
   a different uid is rejected (peer-UID check working -- mirrors today's websocket behavior).
2. Confirm the proxy is listening on `0.0.0.0:8260` over TLS.
3. Confirm `GET /.well-known/oauth-authorization-server` and `-protected-resource` resolve; confirm
   `POST /register` -> `GET /authorize` (exact-match `redirect_uri` enforced) -> a real hub login ->
   `POST /token` yields a bearer token that a direct MCP `tools/list` call accepts, returning the
   full tool set and both system-prompt resources. Confirm a missing/garbage/expired bearer token
   gets a `401` with the `WWW-Authenticate` challenge, not a bare `403`.
4. Confirm a `node_op` (or any of the other 4 gated tools/operations) call with `confirmed: true`
   still comes back as a preview over MCP, never a real delete, and that the preview's
   `structuredContent` carries an `mcp_note` explaining this is permanent and not worth
   retrying -- see "Known issues found and fixed" above. Unchanged by Phase 2 by design.
5. Attempt a real connection from Claude Desktop against a test hub, since its OAuth dynamic-
   client-registration behavior was the reason the OAuth wrapper exists at all -- confirm it
   completes the full discovery/registration/authorize/token flow and can call tools, not just that
   unit tests pass.

## Open questions

Resolved, against the real installed packages, during nucore-ai-side implementation:

- **Exact `mcp` server construction API**: `mcp.server.lowlevel.Server` with `@server.list_tools()`/
  `@server.call_tool()` decorators, wrapped by `mcp.server.streamable_http_manager
  .StreamableHTTPSessionManager` for the actual Streamable HTTP ASGI app -- see
  `src/unified/mcp/server.py`.
- **Per-connection peer-UID check for a uvicorn/ASGI-served Unix socket**: confirmed not reachable.
  uvicorn's ASGI `scope["client"]` is a parsed address tuple computed once at connection time, never
  the raw socket, so there is no supported hook to call `getpeereid()` from inside request handling
  (unlike the raw `websockets` library's `_run_websocket_server` path, which can). The deliberate
  tradeoff this server ships with instead: filesystem permissions alone (`chmod 0o660`, owned by
  this process's own uid/gid, set before uvicorn ever binds -- see `run_mcp_server.py`'s
  `_bind_unix_socket`) are airtight for this server's one expected client (the proxy process that
  embeds this library); revisit only if more than one distinct process ever embeds it. See
  `run_mcp_server.py`'s module docstring for the full reasoning.

The real `AuthHook` implementation is now built -- see "Phase 2 -- real authentication and
authorization" above. Still open, deliberately out of scope for Phase 2 (see that section's own
"Threat model" subsection): process/jail-separating nucore-ai's MCP server from eisy-ai's own
process, the only thing that would change the pre-existing in-process-RCE ceiling neither phase
ever addressed.

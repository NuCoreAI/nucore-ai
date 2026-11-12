# Release Notes

## 3.6.0 - 2026-11-12

- Add `setup_github_ssh` to plugin authoring: configures the system's SSH access to GitHub if it isn't already, so `setup_github_repo`'s plain `git push` works non-interactively. Defaults to a dedicated per-repository deploy key (own key file plus a `~/.ssh/config` Host alias, returned as `ssh_remote_url`); `key_scope="account"` reuses or creates the host's default key instead. Pre-trusts github.com in `known_hosts` only after its ed25519 fingerprint matches GitHub's published one, and detects success from GitHub's "successfully authenticated" message because `ssh -T` exits 1 even on success. Idempotent. The one step it can't do is adding the public key in GitHub's web UI, so it returns `needs_key_upload` with the key and step-by-step instructions, then verifies on a re-call.
- `setup_vscode_debug_config` now writes `launch.json` with `program` set to `${workspaceFolder}/main.py` (the full path) instead of a bare `main.py`; re-run it to fix an existing plugin.
- `setup_github_ssh` requires a signed-in user and a `github_email` equal to the signed-in user's email (an attestation, since SSH only reveals a GitHub username); otherwise it refuses with plain-text instructions.

## 3.5.0 - 2026-11-11

- Fix `package_generated_plugin` shipping a system-issued plugin SSL certificate (`*.cert`, not matched by the existing `*.cer`/`*.crt` exclusions) and the `.pytest_cache/` directory in the built archive.
- Stop calling the installation "a hub" anywhere in the customer-facing or plugin-authoring system prompts or tool descriptions -- say "the system"/"your installation" generically, or the real product name and model number when the customer specifically asks what they're running. Add `host_identity`, fetched once from the real system config and cached for the process's lifetime, to supply that real product/model/OS fact to both prompt builders; fixes `IoXDiagnostics` having stopped actually populating Model Name/Model Number.
- `configure_developer`/`generate_plugin_scaffold` now refuse outright when a login concept applies to the session (eisyUI) but nobody is logged in yet, instead of letting an unauthenticated session commission a developer identity or generate a plugin blind. `get_developer_config` now also surfaces the session's `authenticated_user_id` so a caller learns the login state up front.
- `generate_plugin_scaffold`'s `server_entry.devd` no longer takes a separate `name` field -- the devd.conf hardware-attach rule is derived from the plugin's own name instead, and its rendered rule text is now written to a sibling `{name}.conf` file in the plugin's directory rather than inlined into `server_entry.json`'s `devd` field (now just a `{"filename": ...}` pointer).
- Fix a stale test (`test_merged_handlers_union_customer_and_plugin_authoring`) still asserting 19 plugin_authoring-only tools; `package_generated_plugin` (3.4.0) brought that to 20.

## 3.4.0 - 2026-11-10

- Add `NuCoreInterface.setup_eisy_ai_vscode_remote`, implemented directly on `IoXWrapper` (and move the existing `refresh_eisy_ai_permissions` there too, out of `IoXDiagnostics` -- neither is a diagnostic).
- `setup_vscode_debug_config` now also gates eisy-ai's VS Code *Remote* hub-side setup (installs missing packages, configures the environment) once per plugin output root: asks for confirmation the first time (or whenever `force` is passed), then on a follow-up call either runs it -- returning ready-to-relay Remote-SSH instructions built from the hub's own real IP/friendly name -- or skips it, before continuing to its existing `.iox_env`/`launch.json` write.
- Add `package_generated_plugin`: builds a publish-ready `dist/<plugin-name>-<version>.tar.gz` for a generated plugin -- excludes secrets/keys/certificates, runtime temp files, persistent/data directories, and internal bookkeeping (`server_entry.json`, `sources.md`, `context.md`, `tests/`, `generation_inputs.json`), and refuses to write anything if a configured secret value turns up in any included file's content.

## 3.3.2 - 2026-11-06

- Handle Claude's `pause_turn` stop reason (a long-running server tool, e.g. web search, still in progress) by resending the paused content back unmodified and continuing the turn, instead of treating it as a final answer.
- Fix `/new-session` and other recovery commands acting on the wrong tool set after a turn fails mid-way through a tool-set switch (e.g. via `switch_to_developer_mode`) -- the sticky active tool set now reflects the tool set the turn actually ended in, not the one it started in.

## 3.3.1 - 2026-11-02

- Fix the PLM link-count sanity check (`quick_plm_sanity_check`) to compare against an Insteon-only expected count -- 2 links per primary Insteon node, 1 per non-primary (multi-button/multi-instance) Insteon node, 1 per group, 1 per Insteon group membership -- instead of a raw, unfiltered node/group count that was wildly inflated on any mixed-protocol (Insteon + Z-Wave/Zigbee/etc.) installation. Tolerance tightened from 20% to 10% now that the count reflects real link-table structure.
- Add Claude-native web search to the customer-facing `unified` tool set, mirroring `plugin_authoring`'s existing Claude-native search -- previously `unified` only got native search automatically when its provider was `openai-responses`.
- Preserve native web-search (and extended-thinking) provenance in conversation history instead of flattening it to plain text, so a later turn can tell whether a past turn actually searched rather than guessing from the reply's wording alone.
- Add `/delete-all-sessions` to clear every saved session in the current mode at once, starting a fresh one in its place.
- Let a customer know, right when a WebSocket connection opens, that they're continuing their previous session, and that `/new-session` starts a fresh one.
- Drive eisy-ui's "AI busy" chatbox animation directly via a new `POST /api/ai/busy` call instead of a plain WebSocket text notice -- fires once (with a randomly chosen status word, e.g. "Thinking"/"Pondering"/"Calculating") at the start of a turn and clears once when the turn fully finishes, regardless of how many tool-calling rounds or tool-set switches happened inside it, instead of repeating a "Thinking..." text chunk once per round.
- Warn as a session's conversation history climbs toward its token budget (every 10% from 50% up, then a distinct notice once it's actually compacted), and add `/compact-session` so a customer can compact it manually any time instead of waiting.

## 3.3.0 - 2026-11-01

- Add routine folders: create folders (gated by an `if` condition, no `then`/`else`), create routines directly inside a folder, move routines between folders, and delete a folder with an explicit choice to delete or move its contents (always confirmed before any delete).
- Fix `ROUTINES DATABASE` going stale after routine update/enable/disable/delete and folder delete/move operations.
- Fix Claude API `400 Invalid signature in thinking block` errors by opting into `thinking.block_binding` on the beta API surface instead of dropping thinking blocks outright.
- Document `polyglot.udm_alert`'s new `group=`/`sound=` parameters in the plugin-authoring prompt and plugin lifecycle design doc.

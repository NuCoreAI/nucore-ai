# Release Notes

## 3.3.1 - 2026-11-02

- Fix the PLM link-count sanity check (`quick_plm_sanity_check`) to compare against an Insteon-only expected count -- 2 links per primary Insteon node, 1 per non-primary (multi-button/multi-instance) Insteon node, 1 per group, 1 per Insteon group membership -- instead of a raw, unfiltered node/group count that was wildly inflated on any mixed-protocol (Insteon + Z-Wave/Zigbee/etc.) installation. Tolerance tightened from 20% to 10% now that the count reflects real link-table structure.
- Add Claude-native web search to the customer-facing `unified` tool set, mirroring `plugin_authoring`'s existing Claude-native search -- previously `unified` only got native search automatically when its provider was `openai-responses`.
- Preserve native web-search (and extended-thinking) provenance in conversation history instead of flattening it to plain text, so a later turn can tell whether a past turn actually searched rather than guessing from the reply's wording alone.
- Add `/delete-all-sessions` to clear every saved session in the current mode at once, starting a fresh one in its place.
- Let a customer know, right when a WebSocket connection opens, that they're continuing their previous session, and that `/new-session` starts a fresh one.
- Stream a "Thinking..." notice over WebSocket connections while a turn's tool-calling rounds are still in flight, so a slower multi-step reply doesn't look stalled.
- Warn as a session's conversation history climbs toward its token budget (every 10% from 50% up, then a distinct notice once it's actually compacted), and add `/compact-session` so a customer can compact it manually any time instead of waiting.

## 3.3.0 - 2026-11-01

- Add routine folders: create folders (gated by an `if` condition, no `then`/`else`), create routines directly inside a folder, move routines between folders, and delete a folder with an explicit choice to delete or move its contents (always confirmed before any delete).
- Fix `ROUTINES DATABASE` going stale after routine update/enable/disable/delete and folder delete/move operations.
- Fix Claude API `400 Invalid signature in thinking block` errors by opting into `thinking.block_binding` on the beta API surface instead of dropping thinking blocks outright.
- Document `polyglot.udm_alert`'s new `group=`/`sound=` parameters in the plugin-authoring prompt and plugin lifecycle design doc.

# Release Notes

## 3.3.0 - 2026-11-01

- Add routine folders: create folders (gated by an `if` condition, no `then`/`else`), create routines directly inside a folder, move routines between folders, and delete a folder with an explicit choice to delete or move its contents (always confirmed before any delete).
- Fix `ROUTINES DATABASE` going stale after routine update/enable/disable/delete and folder delete/move operations.
- Fix Claude API `400 Invalid signature in thinking block` errors by opting into `thinking.block_binding` on the beta API surface instead of dropping thinking blocks outright.
- Document `polyglot.udm_alert`'s new `group=`/`sound=` parameters in the plugin-authoring prompt and plugin lifecycle design doc.

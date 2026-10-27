"""``regenerate_plugin_boilerplate`` -- picks up a template/tooling fix
(e.g. a change to ``plugin_skeleton.py``'s ``__start``/``configDone``
wiring, or ``render_main_py``'s own re-exec logic) on a plugin that was
already generated, without the caller re-supplying the whole original
``generate_plugin_scaffold`` call -- or, when ``version`` is given, performs
a pure version bump the same cheap way.

Reads back ``generation_inputs.json`` (written by ``generate_plugin_scaffold``
itself, see ``scaffold.py``) and re-renders only the files that are purely
template-driven -- ``plugin.py``, ``main.py``, ``version.py``, ``install.sh``
-- with *today's* template code. Deliberately never touches
``server_entry.json`` (carries the live, host-assigned ``nsid`` once
installed -- see ``handlers/install.py``), ``profile.json``/``README.md``/
tests/node-class files (LLM-authored or evidence-derived content, not
template output that a code change here would ever affect).

Re-rendering has to happen unconditionally -- it's the only way to find out
whether ``plugin_skeleton.py``'s templates drifted since this plugin was
last generated. *Writing* the result does not: each of the four files is
compared against what's already on disk, and only a file whose rendered
content actually differs gets written (and listed in ``regenerated`` below)
-- the rest are reported as ``unchanged``. This matters most for the
version-bump case, where none of ``plugin.py``/``main.py``/``install.sh``'s
inputs depend on the version number at all, so a pure version bump that
hasn't also coincided with a template change leaves all three untouched on
disk (and un-notified -- see ``notify_files_changed`` below).

An optional ``version`` argument, when given and different from what's
already stored, additionally updates ``generation_inputs.json``'s own
``version`` field and appends a ``context.md`` entry -- the only two files
beyond the four template ones this tool ever touches, and only when the
version genuinely changes (a resupplied-but-identical version is a no-op).
This needs none of ``generate_plugin_scaffold``'s machinery -- no evidence,
no developer-identity check, no secret scan -- because a version bump
introduces no new authored content; it's exactly the "template/tooling fix"
case above, just triggered by a caller-supplied value instead of a code
change.

Plugins generated before this mechanism existed have no
``generation_inputs.json`` and can't be refreshed this way -- one full
``generate_plugin_scaffold`` call (which starts writing it) is required
first.
"""

from __future__ import annotations

import ast
import json
from typing import Any

from nucore import NuCoreInterface

from .. import plugin_skeleton
from ..file_change_notify import notify_files_changed
from ..path_confinement import confine_path
from ..permissions_refresh import refresh_permissions_after_write
from .scaffold import (
    _CONTEXT_FILENAME,
    _DEFAULT_CONTROLLER_CLASS,
    _DEFAULT_CONTROLLER_MODULE,
    _DEFAULT_VERSION,
    _GENERATION_INPUTS_FILENAME,
    _INSTALL_FILENAME,
    _INSTALL_SCRIPT_CONTENT,
    _MAIN_FILENAME,
    _PLUGIN_FILENAME,
    _VERSION_FILENAME,
    _assemble_plugin_py,
    _class_method_names,
    _context_entry,
    _resolve_controller_command_dispatch,
)


async def regenerate_plugin_boilerplate(
    nucore_interface: NuCoreInterface, args: dict[str, Any], *, plugin_output_root: str
) -> Any:
    location = (args.get("location") or "").strip()
    if not location:
        return {"error": "location is required"}

    try:
        plugin_dir = confine_path(plugin_output_root, location)
    except ValueError as exc:
        return {"error": str(exc)}

    inputs_path = plugin_dir / _GENERATION_INPUTS_FILENAME
    if not inputs_path.is_file():
        return {
            "error": f"no {_GENERATION_INPUTS_FILENAME} found at '{location}' -- this plugin was "
            "generated before this tool existed; call generate_plugin_scaffold once (with "
            "confirm_overwrite: true) to create one, then this tool will work on it going forward"
        }
    try:
        generation_inputs = json.loads(inputs_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"error": f"{_GENERATION_INPUTS_FILENAME} at '{location}' is not valid JSON: {exc}"}

    server_entry_input = generation_inputs.get("server_entry") or {}
    # Same derivation generate_plugin_scaffold uses -- the Controller's display name must include
    # the project's own name, never the bare word "Controller". A plugin that successfully
    # generated once should always have this, but a hand-edited/corrupted generation_inputs.json
    # is still worth guarding against explicitly rather than rendering a blank/invalid name.
    project_name = server_entry_input.get("name")
    if not isinstance(project_name, str) or not project_name.strip():
        return {
            "error": f"{_GENERATION_INPUTS_FILENAME} at '{location}' has no server_entry.name -- "
            "regenerate once with generate_plugin_scaffold (confirm_overwrite: true) first"
        }
    controller_node_name = f"{project_name.strip()} Controller"
    authorize = bool(server_entry_input.get("authorize"))
    ai_enabled = bool(server_entry_input.get("ai_enabled"))
    file_upload = bool(server_entry_input.get("fileUpload"))
    webhook_enabled = bool(server_entry_input.get("webhook_enabled"))
    ai_tool_names = [tool["name"] for tool in (server_entry_input.get("aiTools") or []) if isinstance(tool, dict) and tool.get("name")]

    override_bodies = generation_inputs.get("override_bodies") or {}
    node_classes = generation_inputs.get("node_classes") or {}
    custom_param_docs = generation_inputs.get("custom_param_docs")
    custom_typed_params = generation_inputs.get("custom_typed_params")
    controller_class = generation_inputs.get("controller_class") or _DEFAULT_CONTROLLER_CLASS
    controller_module = generation_inputs.get("controller_module") or _DEFAULT_CONTROLLER_MODULE

    version_override = args.get("version")
    if version_override is not None and not isinstance(version_override, str):
        return {"error": "version must be a string"}
    version_override = version_override.strip() if isinstance(version_override, str) else ""
    iteration_note = args.get("iteration_note")
    if iteration_note is not None and not isinstance(iteration_note, str):
        return {"error": "iteration_note must be a string"}

    stored_version = generation_inputs.get("version") or _DEFAULT_VERSION
    version = version_override or stored_version
    version_changed = bool(version_override) and version_override != stored_version

    stored_profile = generation_inputs.get("profile") or {}
    controller_node_id = "controller"
    controller_address = "ctl"
    controller_nodedef = next(
        (nd for nd in (stored_profile.get("nodedefs") or []) if isinstance(nd, dict) and nd.get("id") == controller_node_id),
        None,
    )
    if controller_nodedef is None:
        return {
            "error": f"{_GENERATION_INPUTS_FILENAME} at '{location}' has no stored nodedef with id "
            "'controller' -- regenerate once with generate_plugin_scaffold (confirm_overwrite: true) first"
        }
    controller_accepts = ((controller_nodedef or {}).get("cmds") or {}).get("accepts") or []
    controller_command_dispatch, dispatch_error = _resolve_controller_command_dispatch(controller_accepts)
    if dispatch_error:
        return {"error": f"{_GENERATION_INPUTS_FILENAME} at '{location}' has an invalid stored profile: {dispatch_error}"}
    new_command_override_names = {
        name for name in controller_command_dispatch.values() if name not in plugin_skeleton.BASE_OVERRIDE_METHODS
    }

    plugin_py = _assemble_plugin_py(
        controller_class=controller_class,
        controller_node_id=controller_node_id,
        controller_address=controller_address,
        controller_node_name=controller_node_name,
        node_classes=list(node_classes.keys()),
        authorize=authorize,
        ai_enabled=ai_enabled,
        file_upload=file_upload,
        webhook_enabled=webhook_enabled,
        override_bodies=override_bodies,
        ai_tool_names=ai_tool_names,
        controller_command_dispatch=controller_command_dispatch,
        custom_param_docs=custom_param_docs,
        custom_typed_params=custom_typed_params,
    )
    main_py = plugin_skeleton.render_main_py(
        controller_module,
        controller_class,
        controller_address=controller_address,
        controller_node_name=controller_node_name,
        webhook_enabled=webhook_enabled,
    )
    version_py = plugin_skeleton.render_version_py(version)

    for filename, source in {_PLUGIN_FILENAME: plugin_py, _MAIN_FILENAME: main_py, _VERSION_FILENAME: version_py}.items():
        try:
            ast.parse(source)
        except SyntaxError as exc:
            return {"error": f"regenerated '{filename}' is not valid Python: {exc}"}

    expected_methods = set(plugin_skeleton.BASE_OVERRIDE_METHODS)
    expected_methods.update(new_command_override_names)
    if ai_enabled:
        expected_methods.add(plugin_skeleton.AI_REQUEST_OVERRIDE_METHOD)
        expected_methods.update(plugin_skeleton.ai_tool_helper_name(n) for n in ai_tool_names)
    if webhook_enabled:
        expected_methods.add(plugin_skeleton.WEBHOOK_OVERRIDE_METHOD)
    if custom_typed_params:
        expected_methods.add(plugin_skeleton.CUSTOM_TYPED_DATA_OVERRIDE_METHOD)
    actual_methods = _class_method_names(plugin_py, controller_class) or set()
    missing_methods = expected_methods - actual_methods
    if missing_methods:
        return {
            "error": f"regenerated plugin.py's '{controller_class}' class is missing method(s) "
            f"{sorted(missing_methods)} -- generation_inputs.json may be stale or corrupt"
        }

    rendered_by_filename = {
        _PLUGIN_FILENAME: plugin_py,
        _MAIN_FILENAME: main_py,
        _VERSION_FILENAME: version_py,
        _INSTALL_FILENAME: _INSTALL_SCRIPT_CONTENT,
    }

    regenerated: list[str] = []
    unchanged: list[str] = []
    for filename, rendered in rendered_by_filename.items():
        file_path = plugin_dir / filename
        try:
            current = file_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            current = None
        if current == rendered:
            unchanged.append(filename)
            continue
        file_path.write_text(rendered, encoding="utf-8")
        regenerated.append(filename)

    if version_changed:
        generation_inputs["version"] = version
        inputs_path.write_text(json.dumps(generation_inputs, indent=2) + "\n", encoding="utf-8")
        regenerated.append(_GENERATION_INPUTS_FILENAME)

        context_path = plugin_dir / _CONTEXT_FILENAME
        note = iteration_note.strip() if iteration_note and iteration_note.strip() else f"Version bumped {stored_version} -> {version}."
        with context_path.open("a", encoding="utf-8") as fh:
            fh.write(_context_entry(iteration_note=note))
        regenerated.append(_CONTEXT_FILENAME)

    permissions_refresh_error = None
    files_changed_notify_error = None
    if regenerated:
        permissions_refresh_error = await refresh_permissions_after_write(nucore_interface)
        files_changed_notify_error = await notify_files_changed(
            nucore_interface, [str(plugin_dir / filename) for filename in regenerated]
        )

    result = {
        "location": location,
        "regenerated": regenerated,
        "unchanged": unchanged,
    }
    if permissions_refresh_error is not None:
        result["permissions_refresh_error"] = permissions_refresh_error
    if files_changed_notify_error is not None:
        result["files_changed_notify_error"] = files_changed_notify_error
    return result

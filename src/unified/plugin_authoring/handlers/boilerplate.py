"""``regenerate_plugin_boilerplate`` -- picks up a template/tooling fix
(e.g. a change to ``plugin_skeleton.py``'s ``__start``/``configDone``
wiring, or ``render_main_py``'s own re-exec logic) on a plugin that was
already generated, without the caller re-supplying the whole original
``generate_plugin_scaffold`` call.

Reads back ``generation_inputs.json`` (written by ``generate_plugin_scaffold``
itself, see ``scaffold.py``) and re-renders only the files that are purely
template-driven -- ``plugin.py``, ``main.py``, ``version.py``, ``install.sh``
-- with *today's* template code. Deliberately never touches
``server_entry.json`` (carries the live, host-assigned ``nsid`` once
installed -- see ``handlers/install.py``), ``profile.json``/``README.md``/
tests/node-class files (LLM-authored or evidence-derived content, not
template output that a code change here would ever affect).

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
from ..path_confinement import confine_path
from .scaffold import (
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
    authorize = bool(server_entry_input.get("authorize"))
    ai_enabled = bool(server_entry_input.get("ai_enabled"))
    file_upload = bool(server_entry_input.get("fileUpload"))
    ai_tool_names = [tool["name"] for tool in (server_entry_input.get("aiTools") or []) if isinstance(tool, dict) and tool.get("name")]

    override_bodies = generation_inputs.get("override_bodies") or {}
    node_classes = generation_inputs.get("node_classes") or {}
    custom_param_docs = generation_inputs.get("custom_param_docs")
    controller_class = generation_inputs.get("controller_class") or _DEFAULT_CONTROLLER_CLASS
    controller_module = generation_inputs.get("controller_module") or _DEFAULT_CONTROLLER_MODULE
    version = generation_inputs.get("version") or _DEFAULT_VERSION

    plugin_py = _assemble_plugin_py(
        controller_class=controller_class,
        node_classes=list(node_classes.keys()),
        authorize=authorize,
        ai_enabled=ai_enabled,
        file_upload=file_upload,
        override_bodies=override_bodies,
        ai_tool_names=ai_tool_names,
        custom_param_docs=custom_param_docs,
    )
    main_py = plugin_skeleton.render_main_py(controller_module, controller_class)
    version_py = plugin_skeleton.render_version_py(version)

    for filename, source in {_PLUGIN_FILENAME: plugin_py, _MAIN_FILENAME: main_py, _VERSION_FILENAME: version_py}.items():
        try:
            ast.parse(source)
        except SyntaxError as exc:
            return {"error": f"regenerated '{filename}' is not valid Python: {exc}"}

    expected_methods = set(plugin_skeleton.BASE_OVERRIDE_METHODS)
    if ai_enabled:
        expected_methods.add(plugin_skeleton.AI_REQUEST_OVERRIDE_METHOD)
        expected_methods.update(plugin_skeleton.ai_tool_helper_name(n) for n in ai_tool_names)
    actual_methods = _class_method_names(plugin_py, controller_class) or set()
    missing_methods = expected_methods - actual_methods
    if missing_methods:
        return {
            "error": f"regenerated plugin.py's '{controller_class}' class is missing method(s) "
            f"{sorted(missing_methods)} -- generation_inputs.json may be stale or corrupt"
        }

    (plugin_dir / _PLUGIN_FILENAME).write_text(plugin_py, encoding="utf-8")
    (plugin_dir / _MAIN_FILENAME).write_text(main_py, encoding="utf-8")
    (plugin_dir / _VERSION_FILENAME).write_text(version_py, encoding="utf-8")
    (plugin_dir / _INSTALL_FILENAME).write_text(_INSTALL_SCRIPT_CONTENT, encoding="utf-8")

    return {
        "location": location,
        "regenerated": [_PLUGIN_FILENAME, _MAIN_FILENAME, _VERSION_FILENAME, _INSTALL_FILENAME],
    }

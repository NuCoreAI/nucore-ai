"""``generate_plugin_scaffold`` (design/developers/impl_plan.md Phase 4,
rewritten in full per design/developers/plugin_authoring_p4_impl.md's Stage
2): turns a structured plugin spec into every artifact
``install_generated_plugin`` (Stage 3) needs to actually register, install,
and start the plugin on a real hub -- ``profile.json``, ``plugin.py``
(skeleton + spliced LLM-authored override bodies), any extra node-class
``.py`` files, ``main.py``, ``version.py``, ``server_entry.json``,
``README.md``, ``LICENSE.md`` (third-party attribution for any licensed
source referenced, plus a plain MIT grant for this plugin's own code --
see ``_render_license``), ``requirements.txt``, ``install.sh`` (the
executable script ``server_entry.json``'s ``install`` field names by
filename -- confirmed against the real ``ioxplugin`` tooling, which
defaults to this same name), ``tests/test_*.py``, ``generation_inputs.json``
(a faithful snapshot of this call's own normalized inputs -- what
``handlers/boilerplate.py``'s ``regenerate_plugin_boilerplate`` reads back
to re-render just the template-driven files with today's template code,
without the caller re-supplying everything) -- plus an append-only
``context.md`` iteration log and a deduped, cross-session ``sources.md``
evidence record (every source ever examined for this plugin -- see
``evidence_ledger.py``'s
``merge_sources``/``render_sources_md``/``parse_sources_md``).

Several independent guards run, in order, before anything is written -- any
one failing writes nothing:

1. ``location`` is required and confined under ``plugin_output_root``
   (``path_confinement.confine_path``).
2. A developer must already be commissioned for this ``plugin_output_root``
   (``developer_config.load_developer_config``) -- refuses with a clear
   pointer to ``configure_developer`` otherwise. When the loaded config's
   email doesn't match this session's own known authenticated identity (if
   any is known at all), refuses rather than attributing a generated
   plugin to the wrong developer.
3. ``ledger.has_evidence()`` -- an empty ledger (nothing looked up this
   session) means refuse outright, no files written.
4. ``profile`` must pass the exact same check
   ``profile_authoring.validate_profile`` runs -- reused directly rather
   than re-derived, so the two tools can never disagree on what's valid.
4a. Every nodedef id in ``profile.nodedefs`` is a plain, LLM-chosen
    placeholder (e.g. ``"controller"``, ``"switch"``) -- ``"controller"``
    is required (the reserved placeholder for the Controller's own
    nodedef). This guard fetches the connected hub's own MAC address
    (``nucore_interface.get_device_mac_address()``; refuses outright if
    the hub is unreachable) and rewrites every placeholder, in place, into
    its real ``{last-6-hex-of-the-mac}_{placeholder}`` id -- in
    ``profile.nodedefs`` and in every matching ``id = "<placeholder>"``
    class variable found in ``node_classes``' source (see
    ``_rewrite_id_literals``) -- before anything downstream (validation,
    guard 7, file-writing) ever sees the placeholder form. This keeps
    nodedef ids unique across every node-server sharing one ISY, computed
    once at generation time rather than guessed by the model.
4b. The Controller's own nodedef's ``cmds.accepts`` ids must each resolve
    to a legal, non-keyword, non-dunder Python method name (lowercased,
    reusing the matching ``plugin_skeleton.BASE_OVERRIDE_METHODS`` entry
    on a case-insensitive collision -- e.g. ``"DISCOVER"`` reuses the
    already-wired ``discover()`` override -- see
    ``_resolve_controller_command_dispatch``), with no two ids colliding on
    the same derived name. ``_assemble_plugin_py`` uses this resolved
    mapping to generate a class-level ``commands = {...}`` dispatch dict
    (see ``udi_interface.Node.runCmd``) plus a stub method for every
    non-colliding entry.
5. ``override_bodies``' keys must be a subset of this spec's allowed
   override-method names (the fixed set in ``plugin_skeleton
   .BASE_OVERRIDE_METHODS``, plus ``handle_custom_request`` and one helper
   name per declared AI tool when ``ai_enabled``, plus guard 4b's
   non-colliding per-command names) -- and, when ``ai_enabled``, every
   declared AI tool must have a matching helper and vice versa (Stage 5's
   "generated together" rule: one without the other is a guard failure,
   not a silent gap).
6. The assembled ``plugin.py`` and every extra node-class file must
   round-trip through ``ast.parse`` -- generated code is written, never
   imported or executed. Every override method, after being normalized to
   a fixed indentation (see ``_reindent_method``), must come back out as
   an actual method of ``controller_class`` in the assembled module's AST
   -- catching the case where ``ast.parse`` alone would not: a
   mis-indented override that parses fine as a *module-level* function,
   silently never called by anything at runtime.
7. Every class defined in the assembled ``plugin.py`` and in every extra
   node-class file must carry a class-level ``id = "<...>"`` whose
   (already-rewritten, per guard 4a) value matches a real nodedef id in
   ``profile.nodedefs`` (see ``_node_class_ids``) -- ``udi_interface.Node``
   itself defaults ``id`` to ``''``, and ``Interface.addNode()`` sends
   ``node.id`` to ISY/PG3 as ``nodeDefId``, so a missing or dangling ``id``
   would otherwise silently report the wrong (or no) node type to the hub.
7b. Every class resolved by guard 7 must also carry a class-level
    ``commands = {...}`` dict literal (see ``_node_class_command_ids``)
    whose keys cover every id in that class's own nodedef's
    ``cmds.accepts`` -- a dynamically-assigned ``self.commands = {...}``
    in ``__init__`` does not satisfy this (treated the same as missing;
    see ``_node_class_command_ids``'s docstring for why there's no
    legitimate dynamic alternative). Applies to both the generated
    Controller (where it should never actually fail, since
    ``_assemble_plugin_py`` shares guard 4b's own resolved mapping -- this
    is defense in depth) and every free-text ``node_classes`` file (where
    it's the actual enforcement point, since nothing else requires an
    LLM-authored device class to dispatch its own declared commands).
8. No LLM-authored text (override bodies, node-class files, readme_body,
   tests, aiPrompt, extra_requirements) and no ``server_entry`` value may
   contain a configured secret literally (``secret_guard.find_secret``);
   when ``authorize`` is set, ``server_entry.oauth.name``/``auth_endpoint``/
   ``token_endpoint`` must be present (the real PG3 Oauth Configuration's
   own required fields, confirmed against the upstream ``udi_python_
   interface`` docs -- never enforced until this check existed), and
   ``client_id``/``client_secret`` must equal ``secret_guard.
   OAUTH_PLACEHOLDER`` exactly -- any other literal value (including an
   omitted one, which this tool fills in with the placeholder itself) is
   refused outright.

Overwrite flow, unchanged from the Phase 3 draft this replaces: if any
target file already exists and the caller hasn't passed
``confirm_overwrite: true``, the call returns the conflict list and writes
nothing; ``context.md`` and ``sources.md`` are the exceptions -- the former
is append-only, the latter is a merged/deduped rewrite of its own prior
content -- neither is ever part of the conflict check.
"""

from __future__ import annotations

import ast
import copy
import json
import keyword
import textwrap
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from nucore import NuCoreInterface

from .. import developer_config, plugin_skeleton
from ..evidence_ledger import EvidenceLedger, merge_sources, parse_sources_md, render_sources_md
from ..path_confinement import confine_path
from ..secret_guard import OAUTH_PLACEHOLDER, find_secret
from . import profile_authoring

_PROFILE_FILENAME = "profile.json"
_PLUGIN_FILENAME = "plugin.py"
_MAIN_FILENAME = "main.py"
_VERSION_FILENAME = "version.py"
_SERVER_ENTRY_FILENAME = "server_entry.json"
_README_FILENAME = "README.md"
_LICENSE_FILENAME = "LICENSE.md"
_CONTEXT_FILENAME = "context.md"
_SOURCES_FILENAME = "sources.md"
_REQUIREMENTS_FILENAME = "requirements.txt"
_GENERATION_INPUTS_FILENAME = "generation_inputs.json"
_DATA_DIRNAME = "data"
_PERSIST_DIRNAME = "persist"
_DEFAULT_CONTROLLER_MODULE = "plugin"
_DEFAULT_CONTROLLER_CLASS = "Controller"
# Every Controller always reports its own connection status via this one reserved property/editor
# -- generate_plugin_scaffold injects both unconditionally (guard 4a), matching the Python-side
# drivers=[...]/addNode(conn_status='ST') wiring plugin_skeleton.py always emits. PG3 manages ST's
# actual connected/disconnected value itself once conn_status='ST' is set; neither the LLM nor any
# generated override body ever sets it directly.
_CONTROLLER_ST_EDITOR_ID = "ED_CONTROLLER_ST"
_CONTROLLER_ST_PROPERTY = {"id": "ST", "name": "Status", "editor": _CONTROLLER_ST_EDITOR_ID}
_CONTROLLER_ST_EDITOR = {
    "id": _CONTROLLER_ST_EDITOR_ID,
    "ranges": [{"subset": "0,1", "uom": "25", "names": {"0": "Not Connected", "1": "Connected"}}],
}
_DEFAULT_RUN_AS = "admin"
_DEFAULT_VERSION = "1.0.0"
_BASE_REQUIREMENT = "udi_interface>=3.4.7"
_INSTALL_FILENAME = "install.sh"
# .venv/bin/pip3 branch is local-dev-only (see setup_dev_venv) -- production
# never has a .venv here, so the --user fallback is the only path ever taken
# there. The host runs this script unconditionally on every install (dev or
# prod), so the check has to live here too, not just in main.py's own.
_INSTALL_SCRIPT_CONTENT = (
    "#!/usr/bin/env bash\n"
    'cd "$(dirname "$0")"\n'
    'if [ -x ".venv/bin/pip3" ]; then\n'
    f'    .venv/bin/pip3 install -r {_REQUIREMENTS_FILENAME}\n'
    "else\n"
    f"    pip3 install -r {_REQUIREMENTS_FILENAME} --user\n"
    "fi\n"
)


def _ensure_controller_st_property(raw_profile: dict[str, Any]) -> None:
    """Unconditionally makes the controller's own nodedef (``id ==
    "controller"``, still the plain placeholder at this point -- called
    before guard 4a's id rewrite) declare a property
    ``{"id": "ST", "name": "Status", "editor": _CONTROLLER_ST_EDITOR_ID}``, and makes
    ``raw_profile["editors"]`` contain a matching ``_CONTROLLER_ST_EDITOR``
    -- removing every existing ``"ST"``-id property first (not just the
    first match), so an author-supplied duplicate can't survive injection,
    then appending the one canonical property; the editor is replaced if
    something with that id already exists, appended otherwise. Guarantees
    the Controller's Python-side
    ``drivers = [...]`` (``_assemble_plugin_py``) and
    ``conn_status='ST'`` (``render_init``) always have a real nodedef
    property/editor behind them, regardless of what the LLM did or didn't
    author -- mutates *raw_profile* in place, same as the id rewrite just
    below it."""
    for nodedef in raw_profile.get("nodedefs") or []:
        if isinstance(nodedef, dict) and nodedef.get("id") == "controller":
            properties = nodedef.setdefault("properties", [])
            # Drop every existing "ST" entry (not just the first) before appending the one
            # canonical property -- a profile with two author-supplied "ST" properties here would
            # otherwise leave the second one behind as an undetected duplicate.
            properties[:] = [p for p in properties if not (isinstance(p, dict) and p.get("id") == "ST")]
            properties.append(dict(_CONTROLLER_ST_PROPERTY))
            break

    editors = raw_profile.setdefault("editors", [])
    for i, editor in enumerate(editors):
        if isinstance(editor, dict) and editor.get("id") == _CONTROLLER_ST_EDITOR_ID:
            editors[i] = copy.deepcopy(_CONTROLLER_ST_EDITOR)
            break
    else:
        editors.append(copy.deepcopy(_CONTROLLER_ST_EDITOR))


def _wrap_wire_profile_as_catalog(wire_profile: dict) -> dict:
    """``profile`` here (and the stored ``profile.json`` ``workspace.py``
    already reads back, per its shipped Phase 3 tests) is the Dynamic
    Profiles **wire** shape -- ``editors``/``nodedefs``/``linkdefs`` as
    top-level lists, the same document ``polyglot.getJsonProfile()``
    returns. ``nucore.Profile.load_from_json`` -- and therefore
    ``profile_authoring.validate_profile`` -- expects nucore's internal
    **catalog** shape instead (``families: [{instances: [...]}]``); passing
    the wire shape straight through would silently validate nothing at all
    (``raw.get("families", [])`` is just an empty list, no error). Wrap it
    in one synthetic family/instance -- id/name are required by
    ``Profile.__parse_profile__`` but meaningless here, so both are a fixed
    placeholder -- purely so the real structural checks (dangling editor
    references, malformed properties/commands) still run."""
    return {
        "families": [
            {
                "id": "generated",
                "name": "generated",
                "instances": [
                    {
                        "id": "generated",
                        "name": "generated",
                        "editors": wire_profile.get("editors", []),
                        "nodedefs": wire_profile.get("nodedefs", []),
                        "linkdefs": wire_profile.get("linkdefs", []),
                    }
                ],
            }
        ]
    }


def _reindent_method(text: str) -> str:
    """Normalizes one override method's source to exactly one class-body
    indent level (4 spaces), regardless of whether the caller wrote it
    starting at column 0 (as if it were a top-level function) or already
    indented to class-method depth -- ``textwrap.dedent`` strips whatever
    common leading whitespace is actually present (0 in the first case, 4
    in the second) and then a uniform 4-space prefix is added back, so
    either input shape lands in the same place. This exists because
    ``ast.parse`` alone cannot catch a method that's indented one level too
    shallow: that parses as a perfectly valid *module-level* function,
    never called by anything -- the real failure mode this normalizes
    away rather than just detects."""
    dedented = textwrap.dedent(text.strip("\n"))
    return "\n".join(("    " + line if line.strip() else line) for line in dedented.splitlines()) + "\n"


def _class_method_names(module_source: str, class_name: str) -> set[str] | None:
    """The direct (non-nested) method names actually defined on
    *class_name* in *module_source*, or ``None`` if no such class exists.
    Used to confirm every expected override landed inside the class, not
    beside it at module level (see ``_reindent_method``)."""
    tree = ast.parse(module_source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return {
                child.name
                for child in node.body
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
    return None


def _mac6(mac_address: str | None) -> str | None:
    """Last 6 hex digits of *mac_address*, lowercased, with any colons/
    dashes/other separators stripped -- or ``None`` if fewer than 6 hex
    characters are present (no MAC at all, or a malformed one). Used to
    build a nodedef id prefix that's unique across every node-server
    sharing one ISY (guard 4a)."""
    if not mac_address:
        return None
    hex_only = "".join(c for c in mac_address if c in "0123456789abcdefABCDEF")
    if len(hex_only) < 6:
        return None
    return hex_only[-6:].lower()


def _node_class_ids(module_source: str) -> dict[str, str | None]:
    """Maps each top-level class name in *module_source* to its class-level
    ``id = "..."`` string literal, or ``None`` if that class never assigns
    one. Used by guard 7 to confirm every generated Node subclass declares
    an ``id`` that matches a real nodedef -- ``udi_interface.Node``
    defaults ``id`` to ``''``, which ``Interface.addNode()`` would
    otherwise silently send to ISY/PG3 as the node's ``nodeDefId``."""
    tree = ast.parse(module_source)
    result: dict[str, str | None] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        declared: str | None = None
        for stmt in node.body:
            if (
                isinstance(stmt, ast.Assign)
                and len(stmt.targets) == 1
                and isinstance(stmt.targets[0], ast.Name)
                and stmt.targets[0].id == "id"
                and isinstance(stmt.value, ast.Constant)
                and isinstance(stmt.value.value, str)
            ):
                declared = stmt.value.value
        result[node.name] = declared
    return result


def _node_class_command_ids(module_source: str) -> dict[str, set[str] | None]:
    """Maps each top-level class name in *module_source* to the set of
    string keys in its class-level ``commands = {...}`` dict literal, or
    ``None`` if that class never assigns one as a literal -- no assignment
    at all, a dynamic ``self.commands = {...}`` written in ``__init__``
    instead, or a dict with a non-string key. Used by guard 7b to confirm
    every generated Node subclass dispatches every command its own nodedef
    declares in ``cmds.accepts`` -- deliberately strict, not a graceful
    skip: the only "dynamic" alternative to a class-body literal is a
    ``self.xxx`` bound-method reference, which is itself wrong for
    ``udi_interface.Node.runCmd``'s ``fun(self, command)`` call convention
    (see ``_resolve_controller_command_dispatch``), so there is no
    legitimate dynamic pattern worth being lenient about."""
    tree = ast.parse(module_source)
    result: dict[str, set[str] | None] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        declared: set[str] | None = None
        for stmt in node.body:
            if (
                isinstance(stmt, ast.Assign)
                and len(stmt.targets) == 1
                and isinstance(stmt.targets[0], ast.Name)
                and stmt.targets[0].id == "commands"
            ):
                if isinstance(stmt.value, ast.Dict) and all(
                    isinstance(k, ast.Constant) and isinstance(k.value, str) for k in stmt.value.keys
                ):
                    declared = {k.value for k in stmt.value.keys}
                else:
                    declared = None
        result[node.name] = declared
    return result


def _resolve_controller_command_dispatch(accepts: list[Any]) -> tuple[dict[str, str] | None, str | None]:
    """Resolves a nodedef's raw ``cmds.accepts`` list into an ordered
    ``{cmd_id: method_name}`` mapping -- *method_name* is the existing
    ``plugin_skeleton.BASE_OVERRIDE_METHODS`` entry it collides with
    case-insensitively (reused, never re-stubbed -- see
    ``plugin_skeleton.controller_command_method_name``), or else the id
    lowercased. Returns ``(None, error)`` if any entry isn't an object with
    a non-empty string ``id``, any resolved name isn't a legal/non-keyword/
    non-dunder-shaped Python identifier, or two different ids resolve to
    the same method name."""
    dispatch: dict[str, str] = {}
    first_id_for_name: dict[str, str] = {}
    for entry in accepts:
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str) or not entry["id"]:
            return None, f"cmds.accepts entry {entry!r} has no non-empty string 'id'"
        cmd_id = entry["id"]
        method_name = plugin_skeleton.controller_command_method_name(cmd_id)
        if method_name not in plugin_skeleton.BASE_OVERRIDE_METHODS:
            if not method_name.isidentifier() or keyword.iskeyword(method_name):
                return None, (
                    f"cmds.accepts command id '{cmd_id}' does not lowercase to a legal, "
                    "non-reserved Python identifier -- cannot generate a commands dispatch method for it"
                )
            if method_name.startswith("__") and method_name.endswith("__"):
                return None, (
                    f"cmds.accepts command id '{cmd_id}' lowercases to '{method_name}', which looks "
                    "like a reserved Python dunder method name -- choose a different id"
                )
        if method_name in first_id_for_name and first_id_for_name[method_name] != cmd_id:
            return None, (
                f"cmds.accepts command ids '{first_id_for_name[method_name]}' and '{cmd_id}' both "
                f"lowercase to the same method name '{method_name}' -- rename one"
            )
        first_id_for_name[method_name] = cmd_id
        dispatch[cmd_id] = method_name
    return dispatch, None


def _rewrite_id_literals(module_source: str, id_rewrite_map: dict[str, str]) -> str:
    """Splices every class-level ``id = "<placeholder>"`` string literal in
    *module_source* into ``id_rewrite_map[placeholder]``, for every
    placeholder present in the map -- via exact AST source-span
    replacement (``lineno``/``col_offset``/``end_lineno``/
    ``end_col_offset``), not a blind string replace, so it can't
    accidentally touch an unrelated string elsewhere in the file. Leaves a
    class with no ``id`` assignment, or an ``id`` not in the map, untouched
    -- guard 7 catches those as either a missing or an unrecognized id.
    Returns *module_source* unchanged on a syntax error -- guard 6's own
    ``ast.parse`` call reports that cleanly moments later; this function
    isn't the place to raise it."""
    try:
        tree = ast.parse(module_source)
    except SyntaxError:
        return module_source
    edits: list[tuple[int, int, int, int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for stmt in node.body:
            if (
                isinstance(stmt, ast.Assign)
                and len(stmt.targets) == 1
                and isinstance(stmt.targets[0], ast.Name)
                and stmt.targets[0].id == "id"
                and isinstance(stmt.value, ast.Constant)
                and isinstance(stmt.value.value, str)
                and stmt.value.value in id_rewrite_map
            ):
                real_id = id_rewrite_map[stmt.value.value]
                edits.append(
                    (stmt.value.lineno, stmt.value.col_offset, stmt.value.end_lineno, stmt.value.end_col_offset, repr(real_id))
                )
    if not edits:
        return module_source
    lines = module_source.splitlines(keepends=True)
    for lineno, col, end_lineno, end_col, replacement in sorted(edits, reverse=True):
        if lineno == end_lineno:
            line = lines[lineno - 1]
            lines[lineno - 1] = line[:col] + replacement + line[end_col:]
        else:
            first = lines[lineno - 1][:col]
            last = lines[end_lineno - 1][end_col:]
            lines[lineno - 1 : end_lineno] = [first + replacement + last]
    return "".join(lines)


def _module_header(title: str) -> str:
    return f'"""{title} -- generated by plugin_authoring. See context.md for this plugin\'s iteration history."""\n\n'


def _assemble_plugin_py(
    *,
    controller_class: str,
    controller_node_id: str,
    controller_address: str,
    controller_node_name: str,
    node_classes: list[str],
    authorize: bool,
    ai_enabled: bool,
    file_upload: bool,
    webhook_enabled: bool,
    override_bodies: dict[str, str],
    ai_tool_names: list[str],
    controller_command_dispatch: dict[str, str],
    custom_param_docs: str | None = None,
    custom_typed_params: list[dict] | None = None,
) -> str:
    parts = [
        _module_header(f"{controller_class} controller"),
        "from __future__ import annotations\n\n",
        "import json\n",
        "import os\n",
        "import threading\n",
        "import udi_interface\n\n",
        "LOGGER = udi_interface.LOGGER\n\n\n",
        f"class {controller_class}(udi_interface.Node):\n",
        f"    id = {controller_node_id!r}\n",
        "    drivers = [{'driver': 'ST', 'value': 0, 'uom': 25, 'name': 'Status'}]\n",
        plugin_skeleton.render_init(
            node_classes=node_classes,
            authorize=authorize,
            ai_enabled=ai_enabled,
            file_upload=file_upload,
            webhook_enabled=webhook_enabled,
            custom_param_docs=custom_param_docs,
            custom_typed_params=custom_typed_params,
            controller_address=controller_address,
            controller_node_name=controller_node_name,
        ),
        "\n",
        plugin_skeleton.render_private_wiring_methods(authorize=authorize),
        "\n    # --- developer-authored overrides (edit freely; regeneration preserves them) ---\n\n",
    ]
    for name in plugin_skeleton.BASE_OVERRIDE_METHODS:
        body = override_bodies.get(name) or plugin_skeleton.default_override_method(name)
        parts.append(_reindent_method(body) + "\n")
    for cmd_id, method_name in controller_command_dispatch.items():
        if method_name in plugin_skeleton.BASE_OVERRIDE_METHODS:
            continue  # reuses the override stub already emitted above, never re-stubbed
        body = override_bodies.get(method_name) or plugin_skeleton.default_override_method(method_name)
        parts.append(_reindent_method(body) + "\n")
    if ai_enabled:
        body = override_bodies.get(plugin_skeleton.AI_REQUEST_OVERRIDE_METHOD) or plugin_skeleton.default_override_method(
            plugin_skeleton.AI_REQUEST_OVERRIDE_METHOD
        )
        parts.append(_reindent_method(body) + "\n")
        for tool_name in ai_tool_names:
            helper_name = plugin_skeleton.ai_tool_helper_name(tool_name)
            body = override_bodies.get(helper_name) or plugin_skeleton.default_override_method(helper_name)
            parts.append(_reindent_method(body) + "\n")
    if webhook_enabled:
        body = override_bodies.get(plugin_skeleton.WEBHOOK_OVERRIDE_METHOD) or plugin_skeleton.default_override_method(
            plugin_skeleton.WEBHOOK_OVERRIDE_METHOD
        )
        parts.append(_reindent_method(body) + "\n")
    if custom_typed_params:
        body = override_bodies.get(
            plugin_skeleton.CUSTOM_TYPED_DATA_OVERRIDE_METHOD
        ) or plugin_skeleton.default_override_method(plugin_skeleton.CUSTOM_TYPED_DATA_OVERRIDE_METHOD)
        parts.append(_reindent_method(body) + "\n")
    if controller_command_dispatch:
        # commands' values must be bare, unbound function names defined earlier in this same
        # class body -- udi_interface.Node.runCmd calls fun(self, command) where
        # fun = self.commands[command['cmd']], so a bound self.xxx reference would pass self
        # twice, and self.xxx can't even appear in a class-body literal in the first place.
        # This dict must therefore come after every method name it references.
        parts.append("    # --- command dispatch (profile cmds.accepts; see udi_interface.Node.runCmd) ---\n")
        entries = ", ".join(f"{cmd_id!r}: {method_name}" for cmd_id, method_name in controller_command_dispatch.items())
        parts.append(f"    commands = {{{entries}}}\n")
    return "".join(parts)


_CUSTOM_TYPED_PARAM_TYPES = {"NUMBER", "STRING", "BOOLEAN"}


def _validate_custom_typed_param_entries(entries: Any, *, nested: bool = False) -> str | None:
    """Validates ``custom_typed_params``/``params`` against the
    ``udi_interface`` ``customtypedparams`` shape (the only place this is
    documented: the installed package's ``Interface.saveTypedParams``
    docstring, confirmed against ``Custom.load``'s own special-cased
    handling of the ``'customtypedparams'``/``'customtypeddata'`` keys) --
    a list of ``{name, title, type?, defaultValue?, desc?, isRequired?,
    isList?, params?}`` dicts. Only ``params`` (one level, matching the
    tool schema's own one-level-deep ``params`` -- see ``tool_generate_
    plugin_scaffold.json``) recurses with *nested* True, so a ``params``
    entry can't itself declare a further ``params``. Returns an error
    message, or ``None`` if valid."""
    if not isinstance(entries, list):
        return "custom_typed_params must be a list of {name, title, ...} objects"
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            return f"custom_typed_params[{i}] must be an object"
        name = entry.get("name")
        if not isinstance(name, str) or not name:
            return f"custom_typed_params[{i}].name is required and must be a non-empty string"
        title = entry.get("title")
        if not isinstance(title, str) or not title:
            return f"custom_typed_params[{i}].title is required and must be a non-empty string"
        if "type" in entry and entry["type"] not in _CUSTOM_TYPED_PARAM_TYPES:
            return f"custom_typed_params[{i}].type must be one of {sorted(_CUSTOM_TYPED_PARAM_TYPES)}"
        for bool_field in ("isRequired", "isList"):
            if bool_field in entry and not isinstance(entry[bool_field], bool):
                return f"custom_typed_params[{i}].{bool_field} must be a boolean"
        if "desc" in entry and not isinstance(entry["desc"], str):
            return f"custom_typed_params[{i}].desc must be a string"
        if "params" in entry:
            if nested:
                return f"custom_typed_params[{i}].params cannot itself declare a nested 'params' (one level deep only)"
            nested_error = _validate_custom_typed_param_entries(entry["params"], nested=True)
            if nested_error:
                return f"custom_typed_params[{i}].{nested_error}"
    return None


def _validate_override_keys(
    *,
    override_bodies: dict[str, str],
    ai_enabled: bool,
    ai_tool_names: list[str],
    webhook_enabled: bool,
    custom_typed_params_enabled: bool,
    new_command_override_names: set[str],
) -> str | None:
    """Returns an error message, or ``None`` if ``override_bodies``' keys
    are exactly the allowed set for this spec (guard 5). *new_command_
    override_names* (guard 4b's non-``BASE_OVERRIDE_METHODS`` controller
    ``cmds.accepts`` names) is added to ``allowed`` the same way AI-tool
    helpers are, but -- unlike AI-tool helpers -- never required to be
    present: a missing one just defaults to ``plugin_skeleton.
    default_override_method``'s no-op stub, same as any ``BASE_OVERRIDE_
    METHODS`` entry. *custom_typed_params_enabled* (``custom_typed_params``
    non-empty) requires ``CUSTOM_TYPED_DATA_OVERRIDE_METHOD`` present, same
    reasoning as *webhook_enabled* below -- a no-op default there would
    silently discard every value the customer enters in the typed-params
    config UI."""
    allowed = set(plugin_skeleton.BASE_OVERRIDE_METHODS)
    allowed.update(new_command_override_names)
    if ai_enabled:
        allowed.add(plugin_skeleton.AI_REQUEST_OVERRIDE_METHOD)
        allowed.update(plugin_skeleton.ai_tool_helper_name(n) for n in ai_tool_names)
    if webhook_enabled:
        allowed.add(plugin_skeleton.WEBHOOK_OVERRIDE_METHOD)
    if custom_typed_params_enabled:
        allowed.add(plugin_skeleton.CUSTOM_TYPED_DATA_OVERRIDE_METHOD)

    unknown = set(override_bodies) - allowed
    if unknown:
        return f"override_bodies contains unknown method name(s): {sorted(unknown)}"

    if ai_enabled:
        helper_names = {plugin_skeleton.ai_tool_helper_name(n) for n in ai_tool_names}
        missing_helpers = helper_names - set(override_bodies)
        if missing_helpers:
            return f"aiTools declared without a matching override_bodies helper: {sorted(missing_helpers)}"
    if webhook_enabled and plugin_skeleton.WEBHOOK_OVERRIDE_METHOD not in override_bodies:
        return (
            "server_entry.webhook_enabled is true but override_bodies has no "
            f"'{plugin_skeleton.WEBHOOK_OVERRIDE_METHOD}' entry -- a plugin that enables webhooks "
            "must author its own handler for incoming webhook calls"
        )
    if custom_typed_params_enabled and plugin_skeleton.CUSTOM_TYPED_DATA_OVERRIDE_METHOD not in override_bodies:
        return (
            "custom_typed_params was given but override_bodies has no "
            f"'{plugin_skeleton.CUSTOM_TYPED_DATA_OVERRIDE_METHOD}' entry -- a plugin that declares "
            "typed custom params must author its own handler for the customer-entered values "
            "(typically at least self.CustomTypedData.load(custom_params))"
        )
    return None


def _build_server_entry(
    *,
    location: str,
    plugin_dir: Path,
    server_entry: dict[str, Any],
    developer: str,
    author: str,
    default_run_as: str,
) -> tuple[dict[str, Any] | None, str | None]:
    """Builds the exact ``PUT /api/plugins/store/local/entry`` body (guard
    7 for the oauth-placeholder rule is enforced here too) -- returns
    ``(entry, None)`` on success or ``(None, error)``. Only explicitly
    known fields are copied across; nothing in *server_entry* passes
    through un-vetted.

    ``customParams``/``oauth``/``nsdata``/``aiTools`` are JSON-encoded
    *strings* in the real wire body, not raw objects/arrays -- confirmed
    against a real failed registration (the host rejects a raw array) and
    against the real ``StoreEntry`` schema. ``devd`` already follows this
    same "structured input -> plain string value" shape via
    ``render_devd_rule``, below -- this just extends the same precedent to
    the other four fields, via ``json.dumps`` instead of a template.

    ``customParams`` itself (before that encoding) is a *flat*
    ``{field_name: value}`` object -- PG3's actual ``customparams`` shape
    (see ``design/developers/plugin_apis.md``'s "What each ``custom`` key
    is for" table), not the richer ``[{name, type, isRequired,
    defaultValue, ...}]`` list ``customtypedparams`` uses. *value* is each
    field's seeded initial value (commonly ``""``, sometimes a minimal
    placeholder-like hint) -- never rich documentation; that's the
    separate, LLM-authored ``custom_param_docs`` input
    ``generate_plugin_scaffold`` renders into ``setCustomParamsDoc`` (see
    ``plugin_skeleton.render_custom_param_docs_call``). Validated by
    ``generate_plugin_scaffold`` before this is ever called.

    ``install`` is the **filename** of an executable script in the
    plugin's own directory that the host runs once after registering,
    before installing -- confirmed against the real ``ioxplugin`` tooling
    (``getStoreEntryContent()``), which defaults this to ``"install.sh"``
    and puts that filename, not a literal command, into the field. This
    tool always generates that file (``_INSTALL_FILENAME``/
    ``_INSTALL_SCRIPT_CONTENT`` below) and doesn't support naming it
    anything else."""
    name = (server_entry.get("name") or "").strip()
    if not name:
        return None, "server_entry.name is required"
    if len(name) > 15:
        return None, f"server_entry.name must be at most 15 characters, got {len(name)!r}"

    entry: dict[str, Any] = {
        "name": name,
        "status": "active",
        "developer": developer,
        "author": author,
        "purchaseOptions": [],
        "type": server_entry.get("type") or "python3",
        "path": str(plugin_dir),
        "executable": server_entry.get("executable") or _MAIN_FILENAME,
        "runAs": server_entry.get("runAs") or default_run_as,
        "install": _INSTALL_FILENAME,
    }
    if server_entry.get("desc"):
        entry["desc"] = server_entry["desc"]
    if server_entry.get("customParams"):
        entry["customParams"] = json.dumps(server_entry["customParams"])
    if server_entry.get("nsdata"):
        entry["nsdata"] = json.dumps(server_entry["nsdata"])
    if server_entry.get("discover") is not None:
        entry["discover"] = bool(server_entry["discover"])
    if server_entry.get("fileUpload") is not None:
        entry["fileUpload"] = bool(server_entry["fileUpload"])
    if server_entry.get("isyAccess") is not None:
        entry["isyAccess"] = bool(server_entry["isyAccess"])
    if server_entry.get("requireEisyui") is not None:
        entry["requireEisyui"] = bool(server_entry["requireEisyui"])
    if server_entry.get("shortPoll") is not None:
        entry["shortPoll"] = server_entry["shortPoll"]
    if server_entry.get("longPoll") is not None:
        entry["longPoll"] = server_entry["longPoll"]

    authorize = bool(server_entry.get("authorize"))
    if authorize:
        entry["authorize"] = True
        oauth = dict(server_entry.get("oauth") or {})
        if not (oauth.get("name") and oauth.get("auth_endpoint") and oauth.get("token_endpoint")):
            return None, (
                "server_entry.oauth requires name, auth_endpoint, and token_endpoint when "
                "authorize is true"
            )
        for secret_field in ("client_id", "client_secret"):
            configured = oauth.get(secret_field)
            if configured is not None and configured != OAUTH_PLACEHOLDER:
                return None, (
                    f"server_entry.oauth.{secret_field} must be the literal placeholder "
                    f"'{OAUTH_PLACEHOLDER}' at generation time, never a real value -- "
                    "real values are set after install via configure_plugin(key='oauth')"
                )
            oauth[secret_field] = OAUTH_PLACEHOLDER
        entry["oauth"] = json.dumps(oauth)

    ai_enabled = bool(server_entry.get("ai_enabled"))
    if ai_enabled:
        if server_entry.get("aiPrompt"):
            entry["aiPrompt"] = server_entry["aiPrompt"]
        entry["aiTools"] = json.dumps(server_entry.get("aiTools") or [])

    devd = server_entry.get("devd")
    if devd:
        if not isinstance(devd, dict):
            return None, "server_entry.devd must be an object with vendor_id/product_id/name"
        vendor_id, product_id, devd_name = devd.get("vendor_id"), devd.get("product_id"), devd.get("name")
        if not (vendor_id and product_id and devd_name):
            return None, "server_entry.devd requires vendor_id, product_id, and name"
        entry["devd"] = plugin_skeleton.render_devd_rule(vendor_id=vendor_id, product_id=product_id, name=devd_name)

    return entry, None


def _render_readme(*, server_entry: dict[str, Any], readme_body: str, sources: list[dict[str, Any]]) -> str:
    """*sources* is the full merged ``sources.md`` history for this plugin
    (not just this session's ``ledger.sources``) -- a flagged license
    concern must stay visible across regenerations, not disappear just
    because a later session didn't happen to re-examine that source."""
    lines = [f"# {server_entry.get('name') or 'Generated plugin'}\n\n", readme_body.rstrip("\n") + "\n"]
    flagged = [s for s in sources if s.get("license_ok") is False]
    if flagged:
        lines.append(
            "\n## License Notice\n\n"
            "This plugin's development referenced the following source(s), whose license is "
            "not on the standard permissive allowlist. Review their license terms before "
            "relying on or redistributing this plugin:\n\n"
        )
        for source in flagged:
            title = source.get("title") or source.get("url") or "source"
            url = source.get("url")
            license_ = source.get("license") or "unknown"
            entry_line = f"- [{title}]({url})" if url else f"- {title}"
            lines.append(f"{entry_line} -- license: {license_}\n")
        lines.append("\nSee LICENSE.md for full third-party attribution.\n")
    return "".join(lines)


_MIT_LICENSE_BODY = (
    "Permission is hereby granted, free of charge, to any person obtaining a copy of this "
    "software and associated documentation files (the \"Software\"), to deal in the Software "
    "without restriction, including without limitation the rights to use, copy, modify, merge, "
    "publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons "
    "to whom the Software is furnished to do so, subject to the following conditions:\n\n"
    "The above copyright notice and this permission notice shall be included in all copies or "
    "substantial portions of the Software.\n\n"
    "THE SOFTWARE IS PROVIDED \"AS IS\", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, "
    "INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR "
    "PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE "
    "FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR "
    "OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER "
    "DEALINGS IN THE SOFTWARE.\n"
)


def _render_license(*, sources: list[dict[str, Any]], author: str) -> str:
    """``LICENSE.md``: a "Third-Party Attributions" section (every source
    in *sources* -- the full merged ``sources.md`` history, same scope as
    ``_render_readme``'s notice -- that has a known ``license``, regardless
    of ``license_ok``; attribution is owed per a source's own license
    terms independent of whether it's on the permissive allowlist, which
    is a separate "safe to copy from" judgment README's own notice already
    flags) followed by an unconditional plain MIT grant for this plugin's
    own original code. The two are kept clearly separate so an MIT grant
    is never implied to cover attributed third-party material."""
    lines = ["# License\n\n"]
    attributed = [s for s in sources if s.get("license")]
    if attributed:
        lines.append(
            "## Third-Party Attributions\n\n"
            "This plugin's development referenced the following licensed source(s). Attribution "
            "is provided per each source's own license terms -- review those terms directly "
            "before relying on or redistributing material from any of them:\n\n"
        )
        for source in attributed:
            title = source.get("title") or source.get("url") or "source"
            url = source.get("url")
            entry_line = f"- [{title}]({url})" if url else f"- {title}"
            lines.append(f"{entry_line} -- License: {source['license']}\n")
        lines.append("\n")
    year = datetime.now(timezone.utc).year
    lines.append(
        f"## MIT License (this plugin's own code)\n\nCopyright (c) {year} {author}\n\n{_MIT_LICENSE_BODY}"
    )
    return "".join(lines)


def _context_entry(*, iteration_note: str) -> str:
    timestamp = datetime.now(timezone.utc).isoformat()
    return f"## {timestamp}\n\n{iteration_note.rstrip()}\n\n"


async def generate_plugin_scaffold(
    nucore_interface: NuCoreInterface,
    args: dict[str, Any],
    *,
    ledger: EvidenceLedger,
    secret_values: list[str],
    plugin_output_root: str,
    get_user_id: Callable[[], str | None] | None = None,
) -> Any:
    location = (args.get("location") or "").strip()
    if not location:
        return {"error": "location is required"}
    try:
        plugin_dir = confine_path(plugin_output_root, location)
    except ValueError as exc:
        return {"error": str(exc)}

    sources_path = plugin_dir / _SOURCES_FILENAME
    existing_sources_text = sources_path.read_text(encoding="utf-8") if sources_path.is_file() else ""
    merged_sources = merge_sources(parse_sources_md(existing_sources_text), ledger.sources)

    # Guard 2
    developer_entry = developer_config.load_developer_config(plugin_output_root)
    if developer_entry is None:
        return {
            "error": "no developer config found for this plugin_output_root -- "
                     "call configure_developer first (email + name) before generating a scaffold"
        }
    authenticated_user_id = get_user_id() if get_user_id is not None else None
    if authenticated_user_id and authenticated_user_id.strip().lower() != developer_entry["email"].strip().lower():
        return {
            "error": f"this session's authenticated user ('{authenticated_user_id}') does not match "
                     f"the commissioned developer ('{developer_entry['email']}') for this "
                     "plugin_output_root -- refusing to attribute a generated plugin to the wrong developer"
        }

    # Guard 3
    if not ledger.has_evidence():
        # Never names "search_web" specifically -- it's only a real, callable
        # tool some sessions (Brave/Tavily fallback configured); other
        # sessions get Claude's own native web search instead (no tool call
        # at all) or no web search tier whatsoever. Naming it unconditionally
        # here once led a model to call a tool that didn't exist this
        # session, since this exact message is the most direct source of
        # that specific wrong name.
        hint = "search_store_plugins, search_github_plugins, or fetch_reference"
        if ledger.web_search_available:
            hint += ", or a web search (a dedicated tool when offered, or your own native search otherwise)"
        return {"error": f"no evidence gathered this session -- {hint} must find something before a scaffold can be generated"}

    # Guard 4
    raw_profile = args.get("profile")
    if not isinstance(raw_profile, dict):
        return {"error": "profile must be a JSON object"}
    # Deep-copied before any rewriting below -- never mutate the caller's own
    # profile dict in place (args is the caller's, not ours to modify).
    raw_profile = copy.deepcopy(raw_profile)

    # Guard 4a
    mac6 = _mac6(await nucore_interface.get_device_mac_address())
    if mac6 is None:
        return {
            "error": "could not determine the authoring hub's MAC address (hub unreachable, or "
                     "/desc did not return one) -- a live, reachable hub is required so this "
                     "plugin's nodedef ids stay globally unique"
        }
    placeholders = [
        nd["id"] for nd in (raw_profile.get("nodedefs") or []) if isinstance(nd, dict) and isinstance(nd.get("id"), str)
    ]
    if "controller" not in placeholders:
        return {
            "error": "profile.nodedefs must include a nodedef with id 'controller' -- the "
                     "generated Controller class always claims this reserved placeholder, which "
                     "gets rewritten to a MAC-derived real id at generation time"
        }
    _ensure_controller_st_property(raw_profile)
    id_rewrite_map = {placeholder: f"{mac6}_{placeholder}" for placeholder in placeholders}
    for nodedef in raw_profile["nodedefs"]:
        if isinstance(nodedef, dict) and isinstance(nodedef.get("id"), str):
            nodedef["id"] = id_rewrite_map[nodedef["id"]]
    controller_node_id = id_rewrite_map["controller"]
    controller_address = f"{mac6}_ctl"

    catalog_profile = _wrap_wire_profile_as_catalog(raw_profile)
    profile_result = await profile_authoring.validate_profile(nucore_interface, {"profile": catalog_profile})
    if not profile_result.get("valid"):
        return {"error": "profile is invalid", "details": profile_result.get("errors")}

    # Guard 4b
    controller_nodedef = next(
        (nd for nd in raw_profile["nodedefs"] if isinstance(nd, dict) and nd.get("id") == controller_node_id),
        None,
    )
    controller_accepts = ((controller_nodedef or {}).get("cmds") or {}).get("accepts") or []
    controller_command_dispatch, dispatch_error = _resolve_controller_command_dispatch(controller_accepts)
    if dispatch_error:
        return {"error": dispatch_error}
    new_command_override_names = {
        name for name in controller_command_dispatch.values() if name not in plugin_skeleton.BASE_OVERRIDE_METHODS
    }

    server_entry_input = args.get("server_entry")
    if not isinstance(server_entry_input, dict):
        return {"error": "server_entry must be a JSON object"}
    # Validated again, more fully, by _build_server_entry below -- needed this early too, since
    # the Controller's own display name (never the bare word "Controller" -- must include the
    # project's own name) is built from it before that later call ever runs.
    project_name = server_entry_input.get("name")
    if not isinstance(project_name, str) or not project_name.strip():
        return {"error": "server_entry.name is required"}
    controller_node_name = f"{project_name.strip()} Controller"
    readme_body = args.get("readme_body")
    if not isinstance(readme_body, str) or not readme_body.strip():
        return {"error": "readme_body is required"}
    iteration_note = args.get("iteration_note")
    if not isinstance(iteration_note, str) or not iteration_note.strip():
        return {"error": "iteration_note is required"}

    override_bodies = args.get("override_bodies") or {}
    if not isinstance(override_bodies, dict):
        return {"error": "override_bodies must be a JSON object"}
    node_classes = args.get("node_classes") or {}
    if not isinstance(node_classes, dict):
        return {"error": "node_classes must be a JSON object"}
    node_classes = {name: _rewrite_id_literals(source, id_rewrite_map) for name, source in node_classes.items()}
    tests = args.get("tests") or {}
    if not isinstance(tests, dict):
        return {"error": "tests must be a JSON object"}
    extra_requirements = server_entry_input.get("extra_requirements") or []
    if not isinstance(extra_requirements, list) or not all(
        isinstance(req, str) and req.strip() for req in extra_requirements
    ):
        return {"error": "server_entry.extra_requirements must be a list of non-empty strings"}

    custom_params = server_entry_input.get("customParams")
    if custom_params is not None and (
        not isinstance(custom_params, dict)
        or not all(isinstance(k, str) and k and isinstance(v, str) for k, v in custom_params.items())
    ):
        return {
            "error": "server_entry.customParams must be a flat object of {field_name: value} strings "
                     "(value is the field's seeded initial value, often '' -- not a list of richer "
                     "{name, type, isRequired, ...} objects, and not documentation)"
        }

    custom_param_docs = args.get("custom_param_docs")
    if custom_param_docs is not None and not isinstance(custom_param_docs, str):
        return {"error": "custom_param_docs must be a string"}
    if custom_params and not (custom_param_docs and custom_param_docs.strip()):
        return {
            "error": "server_entry.customParams was given but custom_param_docs is missing -- rich, "
                     "user-friendly documentation (markdown/html/text) explaining each field is "
                     "required whenever there are custom parameters for the customer to fill in"
        }

    # custom_typed_params is its own top-level input, not a server_entry field -- same reasoning
    # as custom_param_docs above (render_custom_param_docs_call's docstring): it backs a runtime
    # call the plugin itself makes (Custom(...).load(..., True)), not part of the StoreEntry
    # registration payload. Unlike customParams, no matching custom_param_docs is required --
    # each entry's own 'desc' field is already its documentation.
    custom_typed_params = args.get("custom_typed_params")
    if custom_typed_params is not None:
        schema_error = _validate_custom_typed_param_entries(custom_typed_params)
        if schema_error:
            return {"error": schema_error}

    authorize = bool(server_entry_input.get("authorize"))
    ai_enabled = bool(server_entry_input.get("ai_enabled"))
    file_upload = bool(server_entry_input.get("fileUpload"))
    webhook_enabled = bool(server_entry_input.get("webhook_enabled"))
    ai_tools = server_entry_input.get("aiTools") or []
    if not isinstance(ai_tools, list):
        return {"error": "server_entry.aiTools must be a list"}
    ai_tool_names = []
    for tool in ai_tools:
        if not isinstance(tool, dict) or not tool.get("name"):
            return {"error": "every server_entry.aiTools entry must be an object with a 'name'"}
        ai_tool_names.append(tool["name"])
    if ai_tool_names and not ai_enabled:
        return {"error": "server_entry.aiTools was given but server_entry.ai_enabled is not true"}

    # Guard 5
    key_error = _validate_override_keys(
        override_bodies=override_bodies,
        ai_enabled=ai_enabled,
        ai_tool_names=ai_tool_names,
        webhook_enabled=webhook_enabled,
        custom_typed_params_enabled=bool(custom_typed_params),
        new_command_override_names=new_command_override_names,
    )
    if key_error:
        return {"error": key_error}

    controller_class = args.get("controller_class") or _DEFAULT_CONTROLLER_CLASS
    controller_module = args.get("controller_module") or _DEFAULT_CONTROLLER_MODULE
    version = args.get("version") or _DEFAULT_VERSION

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

    # Guard 6
    for filename, source in {_PLUGIN_FILENAME: plugin_py, _MAIN_FILENAME: main_py, _VERSION_FILENAME: version_py, **{f"{n}.py": c for n, c in node_classes.items()}}.items():
        try:
            ast.parse(source)
        except SyntaxError as exc:
            return {"error": f"generated '{filename}' is not valid Python: {exc}"}

    # Guard 7
    nodedef_ids = set(id_rewrite_map.values())
    for filename, source in {_PLUGIN_FILENAME: plugin_py, **{f"{n}.py": c for n, c in node_classes.items()}}.items():
        for class_name, declared_id in _node_class_ids(source).items():
            if declared_id is None:
                return {
                    "error": f"generated '{filename}''s '{class_name}' class has no class-level "
                             "id = '<nodedef id>' -- udi_interface.Node defaults id to '', which "
                             "poly.addNode() would send to ISY/PG3 as an empty nodeDefId"
                }
            if declared_id not in nodedef_ids:
                return {
                    "error": f"generated '{filename}''s '{class_name}' class declares "
                             f"id = '{declared_id}', which is not any nodedef id in profile.nodedefs "
                             "-- poly.addNode() sends this value as the node's nodeDefId, so it must "
                             "exactly match a nodedef actually defined in the profile"
                }

    # Guard 7b
    nodedef_accepts_by_id = {
        nd["id"]: ((nd.get("cmds") or {}).get("accepts") or [])
        for nd in raw_profile["nodedefs"]
        if isinstance(nd, dict) and isinstance(nd.get("id"), str)
    }
    for filename, source in {_PLUGIN_FILENAME: plugin_py, **{f"{n}.py": c for n, c in node_classes.items()}}.items():
        class_command_keys = _node_class_command_ids(source)
        for class_name, declared_id in _node_class_ids(source).items():
            if declared_id not in nodedef_ids:
                continue  # already rejected by guard 7
            required_accepts = nodedef_accepts_by_id.get(declared_id, [])
            if not required_accepts:
                continue
            required_dispatch, name_error = _resolve_controller_command_dispatch(required_accepts)
            if name_error:
                return {"error": f"nodedef '{declared_id}' (class '{class_name}' in '{filename}'): {name_error}"}
            required_ids = set(required_dispatch)
            declared_commands = class_command_keys.get(class_name)
            if declared_commands is None:
                return {
                    "error": f"generated '{filename}''s '{class_name}' class has no class-level "
                             f"commands = {{...}} dict literal, but nodedef '{declared_id}'s "
                             f"cmds.accepts requires dispatch entries for {sorted(required_ids)} -- "
                             "udi_interface.Node.runCmd() silently does nothing for a command id "
                             "missing from self.commands"
                }
            missing = required_ids - declared_commands
            if missing:
                return {
                    "error": f"generated '{filename}''s '{class_name}' class's commands = {{...}} dict "
                             f"is missing dispatch entry/entries for command id(s) {sorted(missing)} "
                             f"declared in nodedef '{declared_id}'s cmds.accepts"
                }

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
            "error": f"generated plugin.py's '{controller_class}' class is missing method(s) "
                     f"{sorted(missing_methods)} -- likely an indentation issue in override_bodies"
        }

    default_run_as = developer_entry.get("default_run_as") or _DEFAULT_RUN_AS
    entry, entry_error = _build_server_entry(
        location=location,
        plugin_dir=plugin_dir,
        server_entry=server_entry_input,
        developer=developer_entry["email"],
        author=developer_entry["name"],
        default_run_as=default_run_as,
    )
    if entry_error:
        return {"error": entry_error}

    # Carry forward a previously-persisted nsid (install_generated_plugin
    # writes one into server_entry.json after a successful registration --
    # see handlers/install.py) across regeneration. Without this, the next
    # "fix a bug, regenerate the scaffold" cycle -- the single most common
    # real workflow -- would silently erase that fact, and
    # install_generated_plugin would register a second, orphaned dev-store
    # entry instead of detecting the existing one. An unreadable/corrupt/
    # missing old file just means nothing to preserve, never a failure.
    existing_entry_path = plugin_dir / _SERVER_ENTRY_FILENAME
    if existing_entry_path.is_file():
        try:
            existing_nsid = json.loads(existing_entry_path.read_text(encoding="utf-8")).get("nsid")
        except Exception:
            existing_nsid = None
        if isinstance(existing_nsid, str) and existing_nsid:
            entry["nsid"] = existing_nsid

    requirements_lines = [_BASE_REQUIREMENT]
    seen_requirements = {_BASE_REQUIREMENT.lower()}
    for req in extra_requirements:
        normalized = req.strip()
        if normalized.lower() not in seen_requirements:
            requirements_lines.append(normalized)
            seen_requirements.add(normalized.lower())
    requirements_txt = "\n".join(requirements_lines) + "\n"

    # Guard 8
    texts_to_scan = [plugin_py, main_py, version_py, readme_body, json.dumps(entry), requirements_txt, json.dumps(ledger.sources)]
    texts_to_scan.extend(node_classes.values())
    texts_to_scan.extend(tests.values())
    if server_entry_input.get("aiPrompt"):
        texts_to_scan.append(str(server_entry_input["aiPrompt"]))
    secret_hit = find_secret(texts_to_scan, secret_values)
    if secret_hit:
        return {"error": "generated content appears to contain a configured secret value; refusing to write it to disk"}

    readme = _render_readme(server_entry=entry, readme_body=readme_body, sources=merged_sources)
    license_md = _render_license(sources=merged_sources, author=developer_entry["name"])

    # Overwrite flow
    confirm_overwrite = bool(args.get("confirm_overwrite"))
    generation_inputs = {
        "profile": raw_profile,
        "server_entry": server_entry_input,
        "readme_body": readme_body,
        "override_bodies": override_bodies,
        "node_classes": node_classes,
        "tests": tests,
        "custom_param_docs": custom_param_docs,
        "custom_typed_params": custom_typed_params,
        "controller_class": controller_class,
        "controller_module": controller_module,
        "version": version,
    }
    candidate_files: dict[Path, str] = {
        plugin_dir / _PROFILE_FILENAME: json.dumps(raw_profile, indent=2) + "\n",
        plugin_dir / _PLUGIN_FILENAME: plugin_py,
        plugin_dir / _MAIN_FILENAME: main_py,
        plugin_dir / _VERSION_FILENAME: version_py,
        plugin_dir / _SERVER_ENTRY_FILENAME: json.dumps(entry, indent=2) + "\n",
        plugin_dir / _README_FILENAME: readme,
        plugin_dir / _LICENSE_FILENAME: license_md,
        plugin_dir / _REQUIREMENTS_FILENAME: requirements_txt,
        plugin_dir / _INSTALL_FILENAME: _INSTALL_SCRIPT_CONTENT,
        plugin_dir / _GENERATION_INPUTS_FILENAME: json.dumps(generation_inputs, indent=2) + "\n",
    }
    for node_name, node_source in node_classes.items():
        candidate_files[plugin_dir / f"{node_name}.py"] = node_source
    for test_name, test_source in tests.items():
        candidate_files[plugin_dir / "tests" / test_name] = test_source

    if not confirm_overwrite:
        conflicts = sorted(str(p.relative_to(plugin_dir)) for p in candidate_files if p.is_file())
        if conflicts:
            return {"conflicts": conflicts, "message": "these files already exist -- call again with confirm_overwrite: true to overwrite them"}

    for path, content in candidate_files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    (plugin_dir / _INSTALL_FILENAME).chmod(0o755)

    context_path = plugin_dir / _CONTEXT_FILENAME
    context_path.parent.mkdir(parents=True, exist_ok=True)
    with context_path.open("a", encoding="utf-8") as fh:
        fh.write(_context_entry(iteration_note=iteration_note))

    sources_path.parent.mkdir(parents=True, exist_ok=True)
    sources_path.write_text(render_sources_md(merged_sources), encoding="utf-8")

    # self.persist_dir (plugin_skeleton.render_init) always points at
    # persist/, created unconditionally here, outside the overwrite-conflict
    # check, same as context.md above: an existing directory from a prior
    # generation is never something to warn about or touch, only to keep.
    # self.data_dir only exists when fileUpload is true -- it's the File
    # Manager API's directory, not a general persistence location, so it's
    # only created to match.
    (plugin_dir / _PERSIST_DIRNAME).mkdir(parents=True, exist_ok=True)
    extra_dirs = [f"{_PERSIST_DIRNAME}/"]
    if file_upload:
        (plugin_dir / _DATA_DIRNAME).mkdir(parents=True, exist_ok=True)
        extra_dirs.append(f"{_DATA_DIRNAME}/")

    return {
        "location": location,
        "absolute_path": str(plugin_dir),
        "files_written": sorted(str(p.relative_to(plugin_dir)) for p in candidate_files)
        + [_CONTEXT_FILENAME, _SOURCES_FILENAME]
        + extra_dirs,
    }

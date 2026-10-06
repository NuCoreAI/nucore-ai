"""``generate_plugin_scaffold`` (design/developers/impl_plan.md Phase 4,
rewritten in full per design/developers/plugin_authoring_p4_impl.md's Stage
2): turns a structured plugin spec into every artifact
``install_generated_plugin`` (Stage 3) needs to actually register, install,
and start the plugin on a real hub -- ``profile.json``, ``plugin.py``
(skeleton + spliced LLM-authored override bodies), any extra node-class
``.py`` files, ``main.py``, ``version.py``, ``server_entry.json``,
``README.md``, ``tests/test_*.py`` -- plus an append-only ``context.md``
iteration log.

Six independent guards run, in order, before anything is written -- any one
failing writes nothing:

1. ``location`` is required and confined under ``plugin_output_root``
   (``path_confinement.confine_path``).
2. ``ledger.has_evidence()`` -- an empty ledger (nothing looked up this
   session) means refuse outright, no files written.
3. ``profile`` must pass the exact same check
   ``profile_authoring.validate_profile`` runs -- reused directly rather
   than re-derived, so the two tools can never disagree on what's valid.
4. ``override_bodies``' keys must be a subset of this spec's allowed
   override-method names (the fixed set in ``plugin_skeleton
   .BASE_OVERRIDE_METHODS``, plus ``handle_custom_request`` and one helper
   name per declared AI tool when ``ai_enabled``) -- and, when
   ``ai_enabled``, every declared AI tool must have a matching helper and
   vice versa (Stage 5's "generated together" rule: one without the other
   is a guard failure, not a silent gap).
5. The assembled ``plugin.py`` and every extra node-class file must
   round-trip through ``ast.parse`` -- generated code is written, never
   imported or executed. Every override method, after being normalized to
   a fixed indentation (see ``_reindent_method``), must come back out as
   an actual method of ``controller_class`` in the assembled module's AST
   -- catching the case where ``ast.parse`` alone would not: a
   mis-indented override that parses fine as a *module-level* function,
   silently never called by anything at runtime.
6. No LLM-authored text (override bodies, node-class files, readme_body,
   tests, aiPrompt) and no ``server_entry`` value may contain a configured
   secret literally (``secret_guard.find_secret``); when ``authorize`` is
   set, ``server_entry.oauth.client_id``/``client_secret`` must equal
   ``secret_guard.OAUTH_PLACEHOLDER`` exactly -- any other literal value
   (including an omitted one, which this tool fills in with the
   placeholder itself) is refused outright.

Overwrite flow, unchanged from the Phase 3 draft this replaces: if any
target file already exists and the caller hasn't passed
``confirm_overwrite: true``, the call returns the conflict list and writes
nothing; ``context.md`` is the one exception -- it's append-only and never
part of the conflict check.
"""

from __future__ import annotations

import ast
import json
import textwrap
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from nucore import NuCoreInterface

from .. import plugin_skeleton
from ..evidence_ledger import EvidenceLedger
from ..path_confinement import confine_path
from ..secret_guard import OAUTH_PLACEHOLDER, find_secret
from . import profile_authoring

_PROFILE_FILENAME = "profile.json"
_PLUGIN_FILENAME = "plugin.py"
_MAIN_FILENAME = "main.py"
_VERSION_FILENAME = "version.py"
_SERVER_ENTRY_FILENAME = "server_entry.json"
_README_FILENAME = "README.md"
_CONTEXT_FILENAME = "context.md"
_DATA_DIRNAME = "data"
_DEFAULT_CONTROLLER_MODULE = "plugin"
_DEFAULT_CONTROLLER_CLASS = "Controller"
_DEFAULT_RUN_AS = "eisyai"
_DEFAULT_VERSION = "1.0.0"


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


def _module_header(title: str) -> str:
    return f'"""{title} -- generated by plugin_authoring. See context.md for this plugin\'s iteration history."""\n\n'


def _assemble_plugin_py(
    *,
    controller_class: str,
    node_classes: list[str],
    authorize: bool,
    ai_enabled: bool,
    override_bodies: dict[str, str],
    ai_tool_names: list[str],
) -> str:
    parts = [
        _module_header(f"{controller_class} controller"),
        "from __future__ import annotations\n\n",
        "import json\n",
        "import os\n",
        "import udi_interface\n\n",
        "LOGGER = udi_interface.LOGGER\n\n\n",
        f"class {controller_class}(udi_interface.Node):\n",
        plugin_skeleton.render_init(node_classes=node_classes, authorize=authorize, ai_enabled=ai_enabled),
        "\n",
        plugin_skeleton.render_private_wiring_methods(authorize=authorize),
        "\n    # --- developer-authored overrides (edit freely; regeneration preserves them) ---\n\n",
    ]
    for name in plugin_skeleton.BASE_OVERRIDE_METHODS:
        body = override_bodies.get(name) or plugin_skeleton.default_override_method(name)
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
    return "".join(parts)


def _validate_override_keys(
    *, override_bodies: dict[str, str], ai_enabled: bool, ai_tool_names: list[str]
) -> str | None:
    """Returns an error message, or ``None`` if ``override_bodies``' keys
    are exactly the allowed set for this spec (guard 4)."""
    allowed = set(plugin_skeleton.BASE_OVERRIDE_METHODS)
    if ai_enabled:
        allowed.add(plugin_skeleton.AI_REQUEST_OVERRIDE_METHOD)
        allowed.update(plugin_skeleton.ai_tool_helper_name(n) for n in ai_tool_names)

    unknown = set(override_bodies) - allowed
    if unknown:
        return f"override_bodies contains unknown method name(s): {sorted(unknown)}"

    if ai_enabled:
        helper_names = {plugin_skeleton.ai_tool_helper_name(n) for n in ai_tool_names}
        missing_helpers = helper_names - set(override_bodies)
        if missing_helpers:
            return f"aiTools declared without a matching override_bodies helper: {sorted(missing_helpers)}"
    return None


def _build_server_entry(*, location: str, plugin_dir: Path, server_entry: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    """Builds the exact ``PUT /api/plugins/store/local/entry`` body (guard
    6 for the oauth-placeholder rule is enforced here too) -- returns
    ``(entry, None)`` on success or ``(None, error)``. Only explicitly
    known fields are copied across; nothing in *server_entry* passes
    through un-vetted."""
    name = (server_entry.get("name") or "").strip()
    if not name:
        return None, "server_entry.name is required"
    if len(name) > 15:
        return None, f"server_entry.name must be at most 15 characters, got {len(name)!r}"

    entry: dict[str, Any] = {
        "name": name,
        "type": server_entry.get("type") or "python3",
        "path": str(plugin_dir),
        "executable": server_entry.get("executable") or _MAIN_FILENAME,
        "runAs": server_entry.get("runAs") or _DEFAULT_RUN_AS,
    }
    if server_entry.get("desc"):
        entry["desc"] = server_entry["desc"]
    if server_entry.get("customParams"):
        entry["customParams"] = server_entry["customParams"]
    if server_entry.get("discover") is not None:
        entry["discover"] = bool(server_entry["discover"])
    if server_entry.get("fileUpload") is not None:
        entry["fileUpload"] = bool(server_entry["fileUpload"])
    if server_entry.get("isyAccess") is not None:
        entry["isyAccess"] = bool(server_entry["isyAccess"])
    if server_entry.get("shortPoll") is not None:
        entry["shortPoll"] = server_entry["shortPoll"]
    if server_entry.get("longPoll") is not None:
        entry["longPoll"] = server_entry["longPoll"]

    authorize = bool(server_entry.get("authorize"))
    if authorize:
        entry["authorize"] = True
        oauth = dict(server_entry.get("oauth") or {})
        for secret_field in ("client_id", "client_secret"):
            configured = oauth.get(secret_field)
            if configured is not None and configured != OAUTH_PLACEHOLDER:
                return None, (
                    f"server_entry.oauth.{secret_field} must be the literal placeholder "
                    f"'{OAUTH_PLACEHOLDER}' at generation time, never a real value -- "
                    "real values are set after install via configure_plugin(key='oauth')"
                )
            oauth[secret_field] = OAUTH_PLACEHOLDER
        entry["oauth"] = oauth

    ai_enabled = bool(server_entry.get("ai_enabled"))
    if ai_enabled:
        if server_entry.get("aiPrompt"):
            entry["aiPrompt"] = server_entry["aiPrompt"]
        entry["aiTools"] = server_entry.get("aiTools") or []

    devd = server_entry.get("devd")
    if devd:
        if not isinstance(devd, dict):
            return None, "server_entry.devd must be an object with vendor_id/product_id/name"
        vendor_id, product_id, devd_name = devd.get("vendor_id"), devd.get("product_id"), devd.get("name")
        if not (vendor_id and product_id and devd_name):
            return None, "server_entry.devd requires vendor_id, product_id, and name"
        entry["devd"] = plugin_skeleton.render_devd_rule(vendor_id=vendor_id, product_id=product_id, name=devd_name)

    return entry, None


def _render_readme(*, server_entry: dict[str, Any], readme_body: str, ledger: EvidenceLedger) -> str:
    lines = [f"# {server_entry.get('name') or 'Generated plugin'}\n\n", readme_body.rstrip("\n") + "\n"]
    if ledger.sources:
        lines.append("\n## Sources\n\n")
        for source in ledger.sources:
            title = source.get("title") or source.get("url") or "source"
            url = source.get("url")
            lines.append(f"- [{title}]({url})\n" if url else f"- {title}\n")
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
) -> Any:
    location = (args.get("location") or "").strip()
    if not location:
        return {"error": "location is required"}
    try:
        plugin_dir = confine_path(plugin_output_root, location)
    except ValueError as exc:
        return {"error": str(exc)}

    # Guard 2
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

    # Guard 3
    raw_profile = args.get("profile")
    if not isinstance(raw_profile, dict):
        return {"error": "profile must be a JSON object"}
    catalog_profile = _wrap_wire_profile_as_catalog(raw_profile)
    profile_result = await profile_authoring.validate_profile(nucore_interface, {"profile": catalog_profile})
    if not profile_result.get("valid"):
        return {"error": "profile is invalid", "details": profile_result.get("errors")}

    server_entry_input = args.get("server_entry")
    if not isinstance(server_entry_input, dict):
        return {"error": "server_entry must be a JSON object"}
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
    tests = args.get("tests") or {}
    if not isinstance(tests, dict):
        return {"error": "tests must be a JSON object"}

    authorize = bool(server_entry_input.get("authorize"))
    ai_enabled = bool(server_entry_input.get("ai_enabled"))
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

    # Guard 4
    key_error = _validate_override_keys(override_bodies=override_bodies, ai_enabled=ai_enabled, ai_tool_names=ai_tool_names)
    if key_error:
        return {"error": key_error}

    controller_class = args.get("controller_class") or _DEFAULT_CONTROLLER_CLASS
    controller_module = args.get("controller_module") or _DEFAULT_CONTROLLER_MODULE
    version = args.get("version") or _DEFAULT_VERSION

    plugin_py = _assemble_plugin_py(
        controller_class=controller_class,
        node_classes=list(node_classes.keys()),
        authorize=authorize,
        ai_enabled=ai_enabled,
        override_bodies=override_bodies,
        ai_tool_names=ai_tool_names,
    )
    main_py = plugin_skeleton.render_main_py(controller_module, controller_class)
    version_py = plugin_skeleton.render_version_py(version)

    # Guard 5
    for filename, source in {_PLUGIN_FILENAME: plugin_py, _MAIN_FILENAME: main_py, _VERSION_FILENAME: version_py, **{f"{n}.py": c for n, c in node_classes.items()}}.items():
        try:
            ast.parse(source)
        except SyntaxError as exc:
            return {"error": f"generated '{filename}' is not valid Python: {exc}"}

    expected_methods = set(plugin_skeleton.BASE_OVERRIDE_METHODS)
    if ai_enabled:
        expected_methods.add(plugin_skeleton.AI_REQUEST_OVERRIDE_METHOD)
        expected_methods.update(plugin_skeleton.ai_tool_helper_name(n) for n in ai_tool_names)
    actual_methods = _class_method_names(plugin_py, controller_class) or set()
    missing_methods = expected_methods - actual_methods
    if missing_methods:
        return {
            "error": f"generated plugin.py's '{controller_class}' class is missing method(s) "
                     f"{sorted(missing_methods)} -- likely an indentation issue in override_bodies"
        }

    entry, entry_error = _build_server_entry(location=location, plugin_dir=plugin_dir, server_entry=server_entry_input)
    if entry_error:
        return {"error": entry_error}

    # Guard 6
    texts_to_scan = [plugin_py, main_py, version_py, readme_body, json.dumps(entry)]
    texts_to_scan.extend(node_classes.values())
    texts_to_scan.extend(tests.values())
    if server_entry_input.get("aiPrompt"):
        texts_to_scan.append(str(server_entry_input["aiPrompt"]))
    secret_hit = find_secret(texts_to_scan, secret_values)
    if secret_hit:
        return {"error": "generated content appears to contain a configured secret value; refusing to write it to disk"}

    readme = _render_readme(server_entry=entry, readme_body=readme_body, ledger=ledger)

    # Overwrite flow
    confirm_overwrite = bool(args.get("confirm_overwrite"))
    candidate_files: dict[Path, str] = {
        plugin_dir / _PROFILE_FILENAME: json.dumps(raw_profile, indent=2) + "\n",
        plugin_dir / _PLUGIN_FILENAME: plugin_py,
        plugin_dir / _MAIN_FILENAME: main_py,
        plugin_dir / _VERSION_FILENAME: version_py,
        plugin_dir / _SERVER_ENTRY_FILENAME: json.dumps(entry, indent=2) + "\n",
        plugin_dir / _README_FILENAME: readme,
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

    context_path = plugin_dir / _CONTEXT_FILENAME
    context_path.parent.mkdir(parents=True, exist_ok=True)
    with context_path.open("a", encoding="utf-8") as fh:
        fh.write(_context_entry(iteration_note=iteration_note))

    # self.data_dir (plugin_skeleton.render_init) always points here --
    # created unconditionally, outside the overwrite-conflict check, same as
    # context.md above: an existing data/ directory from a prior generation
    # is never something to warn about or touch, only to keep.
    (plugin_dir / _DATA_DIRNAME).mkdir(parents=True, exist_ok=True)

    return {
        "location": location,
        "files_written": sorted(str(p.relative_to(plugin_dir)) for p in candidate_files) + [_CONTEXT_FILENAME, f"{_DATA_DIRNAME}/"],
    }

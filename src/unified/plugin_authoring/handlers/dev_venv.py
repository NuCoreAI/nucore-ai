"""``setup_dev_venv`` -- local/dev-testing only: creates a per-plugin
``.venv`` and installs ``requirements.txt`` into it. Never a side effect of
``generate_plugin_scaffold``/``install_generated_plugin`` -- a developer
calls this explicitly, so a real production install can never end up with
a ``.venv`` it didn't ask for.

Call this *before* ``install_generated_plugin``, not after: the real host
runs ``install.sh`` unconditionally as part of that call's own install
step, and ``install.sh``'s own ``.venv`` check (``handlers/scaffold.py``'s
``_INSTALL_SCRIPT_CONTENT``) only helps if ``.venv`` already exists by the
time the host gets there. ``main.py``'s matching re-exec check
(``plugin_skeleton.render_main_py``) then picks it up on every subsequent
start, with no further configuration.

Also call this before running a generated plugin's own ``tests/`` via
``run_shell_command`` (e.g. ``.venv/bin/python3 -m pytest tests/``) --
those tests import ``plugin.py``, which needs ``requirements.txt``'s
packages (``udi_interface`` and friends) importable, exactly like a real
run does.

Idempotent by design, since both call sites above may call it many times
in a session (once per test run, once before every install attempt): if a
working ``.venv`` already exists, this is a cheap no-op rather than a full
recreate-and-reinstall. Pass ``force`` to rebuild it anyway (e.g. after
editing ``requirements.txt``).

Reuses ``unified.handlers.shell.run_shell_command``'s subprocess machinery
(timeout, bounded output capture) rather than reimplementing it -- the
command run here is always a fixed, hardcoded string, never LLM-supplied
text, so this is a safe internal reuse of that tool's implementation, not
an exposure of its general-purpose arbitrary-command surface.
"""

from __future__ import annotations

import os
from typing import Any

from nucore import NuCoreInterface

from ...handlers import shell
from ..path_confinement import confine_path

_REQUIREMENTS_FILENAME = "requirements.txt"
_VENV_DIRNAME = ".venv"
_VENV_TIMEOUT_S = 120  # shell.py's own _MAX_TIMEOUT_S ceiling -- venv creation plus installing udi_interface and friends can take a while.


async def setup_dev_venv(
    nucore_interface: NuCoreInterface, args: dict[str, Any], *, plugin_output_root: str
) -> Any:
    location = (args.get("location") or "").strip()
    if not location:
        return {"error": "location is required"}

    try:
        plugin_dir = confine_path(plugin_output_root, location)
    except ValueError as exc:
        return {"error": str(exc)}

    if not plugin_dir.is_dir():
        return {"error": f"no plugin directory found at '{location}'"}

    requirements_path = plugin_dir / _REQUIREMENTS_FILENAME
    if not requirements_path.is_file():
        return {
            "error": f"no {_REQUIREMENTS_FILENAME} found at '{location}' -- call generate_plugin_scaffold first"
        }

    venv_python = plugin_dir / _VENV_DIRNAME / "bin" / "python3"
    force = bool(args.get("force"))
    if not force and venv_python.is_file() and os.access(venv_python, os.X_OK):
        return {
            "location": location,
            "venv_created": False,
            "already_set_up": True,
            "venv_path": str(plugin_dir / _VENV_DIRNAME),
        }

    command = (
        f"python3 -m venv --system-site-packages --clear {_VENV_DIRNAME} && "
        f"{_VENV_DIRNAME}/bin/pip3 install -r {_REQUIREMENTS_FILENAME}"
    )
    result = await shell.run_shell_command(
        nucore_interface, {"command": command, "cwd": str(plugin_dir), "timeout_s": _VENV_TIMEOUT_S}
    )
    if result.get("error"):
        return result

    if result.get("exit_code") != 0 or not venv_python.is_file():
        return {
            "error": "failed to create .venv or install requirements.txt into it -- see stdout/stderr",
            "exit_code": result.get("exit_code"),
            "stdout": result.get("stdout"),
            "stderr": result.get("stderr"),
        }

    return {
        "location": location,
        "venv_created": True,
        "venv_path": str(plugin_dir / _VENV_DIRNAME),
        "stdout": result.get("stdout"),
        "stderr": result.get("stderr"),
    }

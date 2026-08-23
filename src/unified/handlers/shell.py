"""``run_shell_command`` -- the one deliberately unsandboxed, arbitrary-exec
tool in this codebase: runs a shell command as whatever OS account this
process itself runs under (see ``design/shell-tool-future-consideration.md``
for the deployment decision behind that -- a dedicated node-server account
whose sudoers.d file, not this module, is what gates any privileged
operation). There is no command-content allowlist here by design; a
non-zero exit code (including a ``sudo`` refusal for an operation outside
that account's sudoers policy) is normal command output, not a handler
error -- only a genuine plumbing failure (bad ``cwd``, failure to spawn
the shell itself) produces ``{"error": ...}``.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import signal
import time
from pathlib import Path
from typing import Any

from nucore import NuCoreInterface
from utils import get_logger

logger = get_logger(__name__)

# Resolved at call time, not hardcoded to /bin/bash -- its real location
# varies by host (e.g. /usr/local/bin/bash on FreeBSD-based deployments).
_BASH = shutil.which("bash") or "/bin/bash"

_DEFAULT_TIMEOUT_S = 30
_MAX_TIMEOUT_S = 120
_MAX_OUTPUT_BYTES = 20_000  # per stream
_READ_CHUNK_BYTES = 65_536

# Deliberately minimal -- the running process's own os.environ may hold
# secrets (e.g. LLM/backend API credentials loaded via load_dotenv, see
# run_unified_runtime.py) that spawned commands shouldn't inherit by default.
_CHILD_ENV = {
    "PATH": os.environ.get("PATH", "/usr/bin:/bin:/usr/local/bin:/usr/local/sbin"),
    "LANG": os.environ.get("LANG", "C.UTF-8"),
    "TERM": os.environ.get("TERM", "xterm"),
}


class _BoundedReader:
    """Accumulates up to ``_MAX_OUTPUT_BYTES`` from a stream, continuing to
    read-and-discard past that cap rather than stopping -- an unread pipe
    fills the OS buffer and can deadlock a chatty child. A plain mutable
    object (not a return value) so a timeout can cancel ``read()`` mid-loop
    and still recover whatever was accumulated in earlier iterations."""

    def __init__(self) -> None:
        self.chunks: list[bytes] = []
        self.total = 0
        self.truncated = False

    async def read_all(self, stream: asyncio.StreamReader) -> None:
        while True:
            chunk = await stream.read(_READ_CHUNK_BYTES)
            if not chunk:
                break
            if self.total < _MAX_OUTPUT_BYTES:
                remaining = _MAX_OUTPUT_BYTES - self.total
                self.chunks.append(chunk[:remaining])
                if len(chunk) > remaining:
                    self.truncated = True
            else:
                self.truncated = True
            self.total += len(chunk)

    def text(self) -> str:
        return b"".join(self.chunks).decode("utf-8", errors="replace")


async def run_shell_command(nucore_interface: NuCoreInterface, args: dict[str, Any]) -> Any:
    """*nucore_interface* is unused -- this tool never touches the hub."""
    command = args.get("command")
    if not command:
        return {"error": "command is required"}

    cwd = args.get("cwd")
    if cwd and not Path(cwd).is_dir():
        return {"error": f"cwd '{cwd}' does not exist or is not a directory"}

    timeout_s = args.get("timeout_s") or _DEFAULT_TIMEOUT_S
    timeout_s = min(float(timeout_s), _MAX_TIMEOUT_S)

    start = time.monotonic()
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            executable=_BASH,
            cwd=cwd,
            env=_CHILD_ENV,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
    except OSError as exc:
        return {"error": f"failed to start command: {exc}"}

    stdout_reader = _BoundedReader()
    stderr_reader = _BoundedReader()
    timed_out = False
    try:
        await asyncio.wait_for(
            asyncio.gather(stdout_reader.read_all(proc.stdout), stderr_reader.read_all(proc.stderr)),
            timeout=timeout_s,
        )
        await proc.wait()
    except asyncio.TimeoutError:
        timed_out = True
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await proc.wait()

    duration_s = time.monotonic() - start
    exit_code = None if timed_out else proc.returncode
    truncated = timed_out or stdout_reader.truncated or stderr_reader.truncated

    logger.info(f"run_shell_command: '{command}' -> exit_code={exit_code} timed_out={timed_out} duration_s={duration_s:.2f}")

    return {
        "command": command,
        "cwd": cwd or os.getcwd(),
        "exit_code": exit_code,
        "stdout": stdout_reader.text(),
        "stderr": stderr_reader.text(),
        "timed_out": timed_out,
        "truncated": truncated,
        "duration_s": round(duration_s, 3),
    }

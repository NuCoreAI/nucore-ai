"""CLI/embeddable entry point for the MCP server (``unified.mcp.server``).

Serves over a Unix domain socket only -- no TCP, no app-level authentication.
Access control is the socket file's own permissions (``chmod 0o660``, owned
by this process's own uid/gid, set *before* uvicorn ever binds it -- see
``_bind_unix_socket`` below). This mirrors ``run_unified_runtime.py``'s
``_run_websocket_server``'s Unix-socket mode, with one deliberate
difference: that function also checks each connection's peer UID via
``getpeereid()``, on top of filesystem permissions, because its socket's
real clients are a genuinely different OS process that the filesystem
permissions alone had to be left wide enough to admit. This server's only
ever-expected client is the one proxy process that embeds this library
(see design/mcp-server.md) -- filesystem permissions alone are airtight for
that case, and a per-connection peer-UID check is not reachable through
uvicorn's ASGI abstraction without patching its internals (confirmed:
uvicorn's ASGI ``scope["client"]`` is a parsed address tuple computed once
at connection time, never the raw socket -- there is no supported hook to
call ``getpeereid()`` from inside request handling). If this server is ever
embedded by more than one distinct process, revisit this.
"""

from __future__ import annotations

import argparse
import os
import socket
import sys
from pathlib import Path
from typing import Any

import uvicorn

from utils import configure_logging, get_logger, register_secret

from .mcp.server import build_mcp_server
from .run_unified_runtime import _BACKEND_API_JSON_OUTPUT, _load_backend_api

logger = get_logger(__name__)


def _build_mcp_parser() -> argparse.ArgumentParser:
    """Small, purpose-built parser -- deliberately not
    ``run_unified_runtime._build_parser()``, which carries ~20 flags for the
    LLM-agentic-loop path that have no meaning here."""
    parser = argparse.ArgumentParser(description="Run the MCP server over a Unix domain socket")
    parser.add_argument(
        "--unix-socket-path",
        type=str,
        default=None,
        help=(
            "Required. Filesystem path to serve the MCP Streamable HTTP server on, as a Unix "
            "domain socket. Not argparse-required: an in-process caller building a Namespace via "
            "parse_args([]) and setting attributes manually (the pattern this repo's other entry "
            "points use) would otherwise fail before ever getting the chance to set it."
        ),
    )
    parser.add_argument(
        "--plugin-output-root",
        type=str,
        default=None,
        help="Required. Allowed root directory for generated plugin scaffolds (the plugin_authoring tool set).",
    )
    parser.add_argument("--search-engine", type=str, default=None, help="'brave' or 'tavily'; enables search_web.")
    parser.add_argument(
        "--search-engine-api-key", type=str, default=None, help="API key for --search-engine; enables search_web."
    )
    parser.add_argument(
        "--backend-api-classpath", type=str, default=None, help="Backend API class path (e.g., 'iox.IoXWrapper')"
    )
    parser.add_argument("--backend-api-base-url", type=str, default=None, help="Backend API base URL")
    parser.add_argument("--backend-api-username", type=str, default=None, help="Backend API username")
    parser.add_argument("--backend-api-password", type=str, default=None, help="Backend API password")
    parser.add_argument(
        "--preferences-dir",
        type=str,
        default=None,
        help="Directory to store this installation's customer preferences (aliases/events) in.",
    )
    parser.add_argument("--log-level", type=str, default=None, help="Logger level override")
    parser.add_argument("--log-file", type=str, default=None, help="Optional log file path")
    parser.add_argument(
        "--public-base-url",
        type=str,
        default=None,
        help=(
            "The scheme://host:port the network-facing proxy embedding this server is actually "
            "reachable at (e.g. 'https://eisy.local:8260') -- this server only ever binds a Unix "
            "socket and has no way to know that address itself. Used only to build a correct "
            "absolute URL in get_system_prompt's plain-HTTP retrieval instructions; omit it and "
            "those instructions fall back to a relative path."
        ),
    )
    return parser


def _bind_unix_socket(path: str) -> socket.socket:
    """Create, bind, permission, and listen on the Unix socket ourselves
    (rather than letting uvicorn's own ``uds=`` do it) specifically to
    control its permissions deterministically: uvicorn's ``uds=`` path only
    preserves an *existing* file's mode if one is already there when it
    binds, and defaults to world-read/write (0o666) on a fresh path --
    verified directly against the installed uvicorn, not assumed. Binding
    and chmod'ing first, then handing uvicorn the already-bound, already
    -listening file descriptor via ``fd=``, avoids that window entirely.
    """
    if os.path.exists(path):
        os.remove(path)  # stale socket from a crashed prior run, same as _run_websocket_server does
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.bind(path)
    os.chmod(path, 0o660)
    os.chown(path, os.getuid(), os.getgid())
    sock.listen(100)
    return sock


def main(args: Any = None, poly: Any = None) -> None:
    if args is None:
        args = _build_mcp_parser().parse_args()

    configure_logging(level=args.log_level, log_file=args.log_file, force=True)

    if not args.unix_socket_path:
        raise ValueError("--unix-socket-path is required")
    if not args.plugin_output_root:
        raise ValueError("--plugin-output-root is required")

    nucore_interface = _load_backend_api(
        classpath=args.backend_api_classpath,
        base_url=args.backend_api_base_url,
        username=args.backend_api_username,
        password=args.backend_api_password,
        json_output=_BACKEND_API_JSON_OUTPUT,
        poly=poly,
    )
    if nucore_interface is None:
        raise ValueError("Backend API failed to load. Please check your parameters and try again.")
    nucore_interface.preferences_dir = args.preferences_dir

    secret_values = [v for v in (args.backend_api_password, args.search_engine_api_key) if v]
    for secret in secret_values:
        register_secret(secret)

    app = build_mcp_server(
        nucore_interface=nucore_interface,
        plugin_output_root=args.plugin_output_root,
        search_engine=args.search_engine,
        search_engine_api_key=args.search_engine_api_key,
        secret_values=secret_values,
        public_base_url=args.public_base_url,
    )

    sock = _bind_unix_socket(args.unix_socket_path)
    logger.info("MCP server listening on unix socket %s", args.unix_socket_path)
    # sys.stdout may have been replaced by something with no isatty() (e.g.
    # udi_interface's LoggerWriter, when embedded under eisy-ai's polyglot
    # process) -- uvicorn's own logging setup calls sys.stdout.isatty()
    # unconditionally unless use_colors is explicitly True/False.
    use_colors = sys.stdout.isatty() if hasattr(sys.stdout, "isatty") else False
    config = uvicorn.Config(
        app, fd=sock.fileno(), log_level=(args.log_level or "info").lower(), use_colors=use_colors
    )
    uvicorn.Server(config).run()


if __name__ == "__main__":
    main()

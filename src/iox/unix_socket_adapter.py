"""requests.adapters.HTTPAdapter that routes traffic over a Unix domain
socket instead of TCP.

``requests``/``urllib3`` have no built-in Unix-socket support, and the old
``requests-unixsocket`` package's approach (overriding only
``HTTPAdapter.get_connection``) is broken on modern requests, whose
``HTTPAdapter.send()`` calls ``get_connection_with_tls_context`` instead.
This adapter overrides both hooks and simply returns a fixed connection pool
that dials the given socket path, ignoring the request's actual host/port.
"""

from __future__ import annotations

import socket

from requests.adapters import HTTPAdapter
from urllib3.connection import HTTPConnection
from urllib3.connectionpool import HTTPConnectionPool


class _UnixHTTPConnection(HTTPConnection):
    def __init__(self, socket_path: str, *args, **kwargs) -> None:
        super().__init__("localhost", *args, **kwargs)
        self._socket_path = socket_path

    def connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        if isinstance(self.timeout, (int, float)):
            sock.settimeout(self.timeout)
        sock.connect(self._socket_path)
        self.sock = sock


class _UnixHTTPConnectionPool(HTTPConnectionPool):
    def __init__(self, socket_path: str, **kwargs) -> None:
        self._socket_path = socket_path
        super().__init__("localhost", **kwargs)

    def _new_conn(self) -> _UnixHTTPConnection:
        return _UnixHTTPConnection(self._socket_path)


class UnixSocketAdapter(HTTPAdapter):
    """Mount on a ``requests.Session`` (e.g. ``session.mount("http://",
    UnixSocketAdapter(path))``) to route every request through *socket_path*
    regardless of the request URL's host/port."""

    def __init__(self, socket_path: str, **kwargs) -> None:
        self._pool = _UnixHTTPConnectionPool(socket_path)
        super().__init__(**kwargs)

    def get_connection_with_tls_context(self, request, verify, proxies=None, cert=None):
        return self._pool

    def get_connection(self, url, proxies=None):
        # Pre-2.32 requests fallback; not exercised on the pinned version but
        # cheap to keep correct in case of a downgrade.
        return self._pool

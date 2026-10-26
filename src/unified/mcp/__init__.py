"""MCP (Model Context Protocol) server exposing this project's tools over a
Unix domain socket -- see design/mcp-server.md for the full design and why
there is no application-level authentication here (the socket's filesystem
permissions are the access control; see run_mcp_server.py)."""

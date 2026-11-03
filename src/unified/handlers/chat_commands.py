"""``list_chat_commands`` -- the two mode-switch commands (`/developer`/
`/customer`) and the seven session-management commands (`/sessions`,
`/new-session`, `/load-session`, `/rename-session`, `/delete-session`,
`/delete-all-sessions`, `/compact-session`), all of which are recognized as
literal strings in `run_unified_runtime.EisyUIContext._apply_switch_command_or_return`
*before* ever reaching the LLM/tool-calling layer -- so, without this tool,
the model has no way to answer a customer who asks what commands exist in
plain language. Purely static: no backend dependency, nothing to compute.
"""

from __future__ import annotations

from typing import Any

from nucore import NuCoreInterface

# Mode-switch commands already have a tool-driven equivalent
# (switch_to_developer_mode/switch_to_customer_mode) -- these are a typed
# shortcut for the same effect, not the only way to do it. See
# mode_commands_note below and this tool's own description for why the
# model should mention, not substitute, the typed form.
_MODE_COMMANDS = [
    {
        "command": "/developer",
        "usage": "/developer",
        "description": (
            "Switches this conversation into developer/plugin-authoring mode -- the "
            "typed-shortcut equivalent of switch_to_developer_mode."
        ),
    },
    {
        "command": "/customer",
        "usage": "/customer",
        "description": (
            "Switches this conversation back to customer mode -- the typed-shortcut "
            "equivalent of switch_to_customer_mode."
        ),
    },
]

_MODE_COMMANDS_NOTE = (
    "These are a typed shortcut for something you can already do yourself via "
    "switch_to_developer_mode/switch_to_customer_mode -- when the customer just asks in plain "
    "language to switch modes, call that tool directly rather than telling them to type the "
    "command."
)

# Wording matches eisy-ai/README.md's "Managing saved sessions" table -- keep
# the two in sync if either changes.
_SESSION_COMMANDS = [
    {
        "command": "/sessions",
        "usage": "/sessions",
        "description": (
            "Lists the customer's saved sessions in the current mode as a table (id, name, "
            "description), marking which one is current."
        ),
    },
    {
        "command": "/new-session",
        "usage": "/new-session",
        "description": (
            "Starts a fresh, empty session in the current mode. The previous one isn't lost -- "
            "it's just no longer the one being talked in."
        ),
    },
    {
        "command": "/load-session",
        "usage": "/load-session <id>",
        "description": (
            "Switches to a saved session by its id (see /sessions), replaying its history."
        ),
    },
    {
        "command": "/rename-session",
        "usage": "/rename-session <id> <name>",
        "description": "Gives a saved session a more memorable name.",
    },
    {
        "command": "/delete-session",
        "usage": "/delete-session <id>",
        "description": (
            "Removes a saved session. Deleting the current session automatically starts a new "
            "empty one in its place -- there's always at least one."
        ),
    },
    {
        "command": "/delete-all-sessions",
        "usage": "/delete-all-sessions",
        "description": (
            "Removes every saved session in the current mode at once, then starts a fresh "
            "empty one in its place."
        ),
    },
    {
        "command": "/compact-session",
        "usage": "/compact-session",
        "description": (
            "Summarizes the current session's older turns right away to free up room, even if "
            "it isn't full yet (this also happens automatically once a session gets large)."
        ),
    },
]

_SESSION_COMMANDS_NOTE = (
    "Each of these only works when the customer sends it as their own standalone message, "
    "exactly as shown in `usage`, including the leading '/'. There is no tool to perform any of "
    "these on the customer's behalf."
)


async def list_chat_commands(nucore_interface: NuCoreInterface, args: dict[str, Any]) -> Any:
    return {
        "mode_commands": _MODE_COMMANDS,
        "mode_commands_note": _MODE_COMMANDS_NOTE,
        "session_commands": _SESSION_COMMANDS,
        "session_commands_note": _SESSION_COMMANDS_NOTE,
    }

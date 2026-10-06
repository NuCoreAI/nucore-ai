"""Deterministic half of a generated plugin's ``plugin.py``/``main.py``/
``version.py`` -- the ``udi_interface`` wiring that has the exact same shape
across every generated plugin, independently verified against the
actually-installed ``udi_interface`` package and ``design/developers/
plugin-api.md`` (see ``design/developers/plugin_authoring_p4_impl.md``'s
"Explored and confirmed" section for the full trail -- this module never
reuses ``ioxplugin``'s package or its AST-templating engine, only the
re-verified facts).

``handlers/scaffold.py`` splices LLM-authored override-method **bodies**
(``start``/``stop``/``discover``/``shortPoll``/``longPoll``/``processConfig``/
``parameterHandler``/``customParamHandler``/per-declared-AI-tool helpers) in
after the pieces rendered here -- this module only ever emits the
boilerplate half: ``__init__``, the private (name-mangled) wiring methods
that ``subscribe()`` actually calls, and the two tiny bootstrap files.
"""

from __future__ import annotations

# Override methods every generated plugin always gets a stub for (even an
# empty one), regardless of authorize/ai_enabled -- handlers/scaffold.py
# validates override_bodies keys against this plus, when ai_enabled, one
# more name per declared AI tool (see ai_tool_helper_name below).
BASE_OVERRIDE_METHODS = (
    "start",
    "stop",
    "discover",
    "shortPoll",
    "longPoll",
    "processConfig",
    "parameterHandler",
    "customParamHandler",
)

# The one AI-tool-wiring override, present only when ai_enabled (Stage 5) --
# listed separately from BASE_OVERRIDE_METHODS since it's conditional.
AI_REQUEST_OVERRIDE_METHOD = "handle_custom_request"

def default_override_method(name: str) -> str:
    """A safe, uniform stand-in for an override ``handlers/scaffold.py``
    didn't receive a body for -- ``*args, **kwargs`` accepts whatever
    ``udi_interface`` actually passes regardless of the real per-event
    signature, so a missing override degrades to a no-op rather than a
    ``TypeError`` at runtime."""
    return f"    def {name}(self, *args, **kwargs):\n        pass\n"


def ai_tool_helper_name(tool_name: str) -> str:
    """The dispatch-helper method name for a declared AI tool, matching the
    user-supplied canonical pattern exactly (tool ``get_dates`` -> helper
    ``_get_dates``, called as ``self._get_dates(payload)`` from
    ``handle_custom_request``)."""
    return f"_{tool_name}"


def render_init(*, node_classes: list[str], authorize: bool, ai_enabled: bool) -> str:
    """The Controller's ``__init__`` method body (full ``def __init__...``
    through the final ``self.poly.addNode(self)``) -- the exact verified
    ``subscribe()`` list, plus the OAuth service construction/subscription
    when *authorize* (closing ``ioxplugin``'s dead-code bug: its own
    template subscribed ``CUSTOMNS`` but left ``OAUTH`` commented out; this
    one does not), plus the ``CUSTOMREQUEST`` subscription when
    *ai_enabled*. *node_classes* lists any additional device Node subclasses
    the profile needs beyond the Controller itself -- actually constructing
    and ``addNode``-ing them needs profile-specific addresses/names that
    only the LLM-authored ``discover``/``start`` override bodies have, so
    this just leaves a pointer comment, not a wiring guess.

    ``self.data_dir`` is always set, next to this plugin's own files -- any
    override body that needs to persist something beyond ``customParams``/
    ``customData`` (a local cache, session tokens, etc.) writes it under
    there, never loose in the plugin's own directory. ``generate_plugin_scaffold``
    creates the directory on disk at generation time; the ``makedirs`` here
    is just a runtime safety net in case it's ever missing."""
    lines = [
        "    def __init__(self, polyglot, primary, address, name):",
        "        super().__init__(polyglot, primary, address, name)",
        "        self.poly = polyglot",
        "        self.data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), \"data\")",
        "        os.makedirs(self.data_dir, exist_ok=True)",
    ]
    if node_classes:
        names = ", ".join(node_classes)
        lines.append(
            f"        # Additional node classes ({names}) -- construct and self.poly.addNode()"
            " them from discover()/start() once their real addresses/names are known."
        )
    if authorize:
        lines.append("        self.oauthService = udi_interface.OAuth(self.poly)")
    lines.extend(
        [
            "        self.poly.subscribe(self.poly.START, self.__start, address)",
            "        self.poly.subscribe(self.poly.STOP, self.__stop)",
            "        self.poly.subscribe(self.poly.POLL, self.__poll)",
            "        self.poly.subscribe(self.poly.CONFIG, self.__configHandler)",
            "        self.poly.subscribe(self.poly.CONFIGDONE, self.__configDoneHandler)",
            "        self.poly.subscribe(self.poly.CUSTOMPARAMS, self.parameterHandler)",
            "        self.poly.subscribe(self.poly.CUSTOMNS, self.__customNSHandler)",
            "        self.poly.subscribe(self.poly.CUSTOMDATA, self.__customDataHandler)",
            "        self.poly.subscribe(self.poly.ADDNODEDONE, self.__addNodeDoneHandler)",
            "        self.poly.subscribe(self.poly.DELNODEDONE, self.__removeNodeDoneHandler)",
            "        self.poly.subscribe(self.poly.DISCOVER, self.discover)",
            "        self.poly.subscribe(self.poly.BONJOUR, self.__bonjourHandler)",
        ]
    )
    if authorize:
        lines.append("        self.poly.subscribe(self.poly.OAUTH, self.__oauthHandler)")
    if ai_enabled:
        lines.append(f"        self.poly.subscribe(self.poly.CUSTOMREQUEST, self.{AI_REQUEST_OVERRIDE_METHOD})")
    lines.append("        self.poly.addNode(self)")
    return "\n".join(lines) + "\n"


def render_private_wiring_methods(*, authorize: bool) -> str:
    """The private (name-mangled, ``__``-prefixed) wiring methods every
    ``subscribe()`` call in ``render_init`` actually points at -- unchanged
    across every generated plugin, so there is nothing LLM-authored here.
    Each delegates to the matching public override where one exists
    (``__poll`` -> ``shortPoll``/``longPoll``, ``__configHandler`` ->
    ``processConfig``) so the LLM-authored half never has to know
    ``udi_interface``'s own event names."""
    methods = [
        '''    def __start(self):
        LOGGER.info("%s: start", self.name)
        self.start()
''',
        '''    def __stop(self):
        LOGGER.info("%s: stop", self.name)
        self.stop()
''',
        '''    def __poll(self, polltype):
        if polltype == "shortPoll":
            self.shortPoll()
        elif polltype == "longPoll":
            self.longPoll()
''',
        '''    def __configHandler(self, config):
        self.processConfig(config)
''',
        '''    def __configDoneHandler(self):
        LOGGER.info("%s: config done", self.name)
''',
        '''    def __addNodeDoneHandler(self, data):
        LOGGER.debug("%s: add node done: %s", self.name, data)
''',
        '''    def __removeNodeDoneHandler(self, data):
        LOGGER.debug("%s: remove node done: %s", self.name, data)
''',
    ]
    if authorize:
        methods.append(
            '''    def __customNSHandler(self, key, data):
        if key == "oauth":
            self.oauthService.customNsHandler(key, data)
            return
        self.customParamHandler(key, data)
'''
        )
        methods.append(
            '''    def __oauthHandler(self, token):
        LOGGER.info("%s: oauth token refreshed", self.name)
'''
        )
    else:
        methods.append(
            '''    def __customNSHandler(self, key, data):
        self.customParamHandler(key, data)
'''
        )
    methods.append(
        '''    def __customDataHandler(self, key, data):
        LOGGER.debug("%s: custom data '%s': %s", self.name, key, data)
'''
    )
    methods.append(
        '''    def __bonjourHandler(self, response):
        LOGGER.debug("%s: bonjour: %s", self.name, response)
'''
    )
    return "\n".join(methods)


def render_main_py(controller_module: str, controller_class: str) -> str:
    """Fully tool-generated, never LLM-authored -- bootstrap is nearly
    identical for every plugin: start Polyglot, send this plugin's Dynamic
    Profile (profile.json, written next to this file) via
    ``updateJsonProfile`` -- writing that file to disk does nothing on its
    own (design/developers/plugin_model.md §1/§2/§5); PG3/IoX only learns
    the plugin's nodedefs/editors/linkdefs once the plugin's own code sends
    them over the wire, documented as happening on startup, before
    ``polyglot.ready()`` -- then construct the Controller, ``ready()``,
    ``runForever()``. Sent unconditionally on every startup, not diffed
    against what PG3 already has: ``updateJsonProfile``'s own add/replace-
    by-id semantics make resending the same profile a no-op."""
    return f'''"""Generated by plugin_authoring -- bootstraps the plugin process. Do not
edit by hand; regenerate via generate_plugin_scaffold instead. See
context.md for this plugin's iteration history."""

from __future__ import annotations

import json
import os
import sys

import udi_interface

import version
from {controller_module} import {controller_class}

LOGGER = udi_interface.LOGGER

if __name__ == "__main__":
    try:
        polyglot = udi_interface.Interface([])
        polyglot.start(version.ud_plugin_version)
        profile_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "profile.json")
        with open(profile_path, "r", encoding="utf-8") as f:
            polyglot.updateJsonProfile(json.load(f), {{"waitResponse": True}})
        {controller_class}(polyglot, "controller", "controller", "{controller_class}")
        polyglot.ready()
        polyglot.runForever()
    except (KeyboardInterrupt, SystemExit):
        sys.exit(0)
'''


def render_version_py(version: str) -> str:
    """Fully tool-generated -- bump ``ud_plugin_version`` on every
    regeneration; ``main.py`` passes it straight to ``polyglot.start()``."""
    return f'''"""Generated by plugin_authoring. Bump on every regeneration."""

ud_plugin_version = "{version}"
'''


_DEVD_TEMPLATE = '''attach 100 {{
        match "vendor" "0x{vendor_id}";
        match "product" "0x{product_id}";
        action "chown UDX_OWNER_PLACE_HOLDER /dev/tty$ttyname";
        action "chmod UDX_PERMISSION_PLACE_HOLDER /dev/tty$ttyname";
        action "/usr/local/etc/udx.d/static/pg3.serial.dev.ops /dev/tty$ttyname pg3.{name}";
}};
'''


def render_devd_rule(*, vendor_id: str, product_id: str, name: str) -> str:
    """A ``devd.conf``-style hardware-attach rule for a serial/USB device
    (Stage 6), substituting only *vendor_id*/*product_id*/*name* into the
    fixed template given directly by the user -- the ``UDX_OWNER_PLACE_
    HOLDER``/``UDX_PERMISSION_PLACE_HOLDER`` tokens stay literal, since the
    platform substitutes those at runtime, never this tool. *vendor_id*/
    *product_id* are plain 4-hex-digit strings without a leading '0x'
    (matching ``detect_usb_device``'s output); the template adds it."""
    return _DEVD_TEMPLATE.format(vendor_id=vendor_id, product_id=product_id, name=name)

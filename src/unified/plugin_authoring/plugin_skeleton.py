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


def render_custom_param_docs_call(custom_param_docs: str | None) -> str | None:
    """A single ``self.poly.setCustomParamsDoc(...)`` call wrapping
    *custom_param_docs* verbatim -- deliberately **not** derived from
    ``server_entry.customParams``' own ``{field_name: value}`` map:
    that map's value is the field's initial/default value (often just
    ``""``, sometimes a minimal placeholder-like hint) seeded into the UI,
    never rich documentation -- reusing it as the config-page help text
    produced thin, unhelpful docs. ``custom_param_docs`` is its own,
    separately LLM-authored rich markdown/html/text explaining each field
    to the installer in plain, friendly language -- a sibling top-level
    `generate_plugin_scaffold` input, not a server_entry field (it's a
    runtime call the plugin itself makes, not part of the registration
    payload). Returns ``None`` (no line emitted) when none was given --
    preserves the ``POLYGLOT_CONFIG.md``-file fallback ``setCustomParamsDoc``
    has when never called at all."""
    if not custom_param_docs:
        return None
    return f"        self.poly.setCustomParamsDoc({custom_param_docs!r})"


def render_init(
    *,
    node_classes: list[str],
    authorize: bool,
    ai_enabled: bool,
    file_upload: bool,
    custom_param_docs: str | None = None,
) -> str:
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
    this just leaves a pointer comment, not a wiring guess (that comment also
    points at ``self.poly.getValidName()``/``getValidAddress()`` -- those two
    must sanitize any dynamically-derived name/address before it's used).

    ``self.persist_dir`` is always set, next to this plugin's own files -- any
    override body that needs to persist something beyond ``customParams``/
    ``customData`` (a local cache, session tokens, etc.) writes it under
    there, never loose in the plugin's own directory; it's included in the
    host's plugin backup the same as ``data/`` below. ``self.data_dir`` is
    only set when *file_upload* (``server_entry.fileUpload``) is true -- it's
    the directory the host's File Manager API/UI operate on, not a general
    persistence location. ``generate_plugin_scaffold`` creates whichever
    directories apply on disk at generation time; the ``makedirs`` calls here
    are just a runtime safety net in case either is ever missing.

    When *custom_param_docs* is given, a ``self.poly.setCustomParamsDoc(...)``
    call (see ``render_custom_param_docs_call``) is emitted right after
    ``persist_dir``/``data_dir`` setup -- see that function for why this is
    its own separately-authored rich-text input, not something derived
    from ``customParams``' own minimal ``{field_name: value}`` map.

    ``self.configDone``/``self.configDoneAlready`` back ``__start``'s
    wait-for-configDone gate (``render_private_wiring_methods``, below) --
    nothing is actually configured yet when the real ``START`` event fires
    (no custom params, no previously-discovered nodes, no OAuth token), so
    the LLM-authored ``start()`` override must not run until ``CONFIGDONE``
    has fired, or a timeout gives up."""
    lines = [
        "    def __init__(self, polyglot, primary, address, name):",
        "        super().__init__(polyglot, primary, address, name)",
        "        self.poly = polyglot",
        "        self.persist_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), \"persist\")",
        "        os.makedirs(self.persist_dir, exist_ok=True)",
        "        # Synchronization for __start()'s wait-for-configDone gate (see __configDoneHandler).",
        "        self.configDone = threading.Condition()",
        "        self.configDoneAlready = False",
    ]
    if file_upload:
        lines.extend(
            [
                "        self.data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), \"data\")",
                "        os.makedirs(self.data_dir, exist_ok=True)",
            ]
        )
    custom_param_docs_call = render_custom_param_docs_call(custom_param_docs)
    if custom_param_docs_call:
        lines.append(custom_param_docs_call)
    if node_classes:
        names = ", ".join(node_classes)
        lines.append(
            f"        # Additional node classes ({names}) -- construct and self.poly.addNode()"
            " them from discover()/start() once their real addresses/names are known"
            " (run candidate names/addresses through self.poly.getValidName()/getValidAddress() first)."
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
    ``udi_interface``'s own event names.

    ``__start``/``__configDoneHandler`` additionally enforce the real event
    order: ``START`` fires before any config/params/OAuth token have
    arrived, so ``__start`` blocks on ``self.configDone`` until
    ``__configDoneHandler`` signals it (or a 10s timeout gives up and logs,
    rather than calling the LLM-authored ``start()`` override too early).
    For an *authorize* plugin, ``__configDoneHandler`` additionally confirms
    a real OAuth access token is available first (``self.oauthService.
    getAccessToken()`` -- raises ``ValueError`` until one has arrived,
    confirmed against the installed ``udi_interface`` package directly),
    surfacing a ``Notices['auth']`` banner and leaving ``configDoneAlready``
    false (so ``__start`` times out rather than hangs) when it isn't ready
    yet."""
    methods = [
        '''    def __start(self):
        LOGGER.info("%s: start", self.name)
        try:
            if not self.configDoneAlready:
                with self.configDone:
                    if not self.configDone.wait(timeout=10):
                        LOGGER.error("%s: timed out waiting for configDone", self.name)
                        return
            self.start()
        except Exception as ex:
            LOGGER.error("%s: start failed: %s", self.name, ex)
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
    ]
    if authorize:
        methods.append(
            '''    def __configDoneHandler(self):
        LOGGER.info("%s: config done", self.name)
        try:
            self.oauthService.getAccessToken()
        except ValueError:
            LOGGER.warning("%s: access token not yet available -- waiting for authentication", self.name)
            self.poly.Notices["auth"] = "Please authenticate using the Authorize button on this plugin's configuration page."
            return
        with self.configDone:
            self.configDoneAlready = True
            self.configDone.notifyAll()
'''
        )
    else:
        methods.append(
            '''    def __configDoneHandler(self):
        LOGGER.info("%s: config done", self.name)
        with self.configDone:
            self.configDoneAlready = True
            self.configDone.notifyAll()
'''
        )
    methods.append(
        '''    def __addNodeDoneHandler(self, data):
        LOGGER.debug("%s: add node done: %s", self.name, data)
'''
    )
    methods.append(
        '''    def __removeNodeDoneHandler(self, data):
        LOGGER.debug("%s: remove node done: %s", self.name, data)
'''
    )
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
        '''    def __customDataHandler(self, data):
        LOGGER.debug("%s: custom data: %s", self.name, data)
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
    own (design/developers/legacy/plugin_model.md §1/§2/§5); PG3/IoX only
    learns the plugin's nodedefs/editors/linkdefs once the plugin's own
    code sends them over the wire, documented as happening on startup,
    before constructing the Controller and calling ``polyglot.ready()`` --
    then ``runForever()``. Sent unconditionally on every startup, not
    diffed against what PG3 already has: ``updateJsonProfile``'s own
    add/replace-by-id semantics make resending the same profile a no-op.
    The response is logged (not just discarded) so a failed/rejected
    update is visible in the plugin's own log rather than silently
    swallowed.

    The very first thing this file does, before even ``import udi_interface``,
    is check for a sibling ``.venv`` (created by the ``setup_dev_venv`` tool,
    either called directly or automatically by ``install_generated_plugin`` --
    see that tool's own docstring) and ``os.execv`` into its interpreter if
    present --
    the real host (confirmed against a live ``/usr/local/etc/rc.d/plugin_N``
    script) always launches this file with the bare system ``python3``,
    regardless of dev or production, so this is the only reliable place a
    local dev venv can actually take effect once a plugin is registered and
    started through the normal install flow. Production never has a
    ``.venv`` here, so this check is always a no-op there -- zero behavior
    change, nothing to opt into or out of at generation time."""
    return f'''"""Generated by plugin_authoring -- bootstraps the plugin process. Do not
edit by hand; regenerate via generate_plugin_scaffold instead. See
context.md for this plugin's iteration history."""

from __future__ import annotations

import os
import sys

_PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
_VENV_PYTHON = os.path.join(_PLUGIN_DIR, ".venv", "bin", "python3")
if sys.prefix == sys.base_prefix and os.access(_VENV_PYTHON, os.X_OK):
    # Local dev only -- see setup_dev_venv. Never present in production, so
    # this re-exec never happens there; the real host always launches this
    # file with its own bare system python3 regardless. sys.prefix ==
    # sys.base_prefix (not a realpath/executable-path comparison) is the
    # correct "not already running inside a venv" check -- a venv's own
    # python3 is typically a symlink to the exact same real interpreter
    # binary as the system one, so comparing resolved paths would never
    # see a difference and this would silently never fire.
    #
    # argv[0] is the literal "python3" here, NOT _VENV_PYTHON -- confirmed
    # against a live host: rc.subr's check_pidfile()/_find_processes()
    # (/etc/rc.subr) matches the running process's own argv[0] against the
    # service's procname (the real system python3's path, from a live
    # /usr/local/etc/rc.d/plugin_N's udx_command), accepting either that
    # exact path or its bare basename ("python3") -- never an unrelated
    # full path like this venv's own. Passing _VENV_PYTHON as argv[0]
    # breaks that match even though the PID is exactly right and the
    # process is genuinely alive, which makes rc.subr conclude the service
    # isn't running and spawn a duplicate on every subsequent start,
    # orphaning this one. The actual interpreter binary that runs is still
    # _VENV_PYTHON (os.execv's own first argument, unaffected by argv[0]).
    os.execv(_VENV_PYTHON, ["python3"] + sys.argv)

import json

import udi_interface

import version
from {controller_module} import {controller_class}

LOGGER = udi_interface.LOGGER

if __name__ == "__main__":
    try:
        polyglot = udi_interface.Interface([])
        polyglot.start(version.ud_plugin_version)
        profile_path = os.path.join(_PLUGIN_DIR, "profile.json")
        with open(profile_path, "r", encoding="utf-8") as f:
            response = polyglot.updateJsonProfile(json.load(f), {{"waitResponse": True}})
            LOGGER.info("Profile update response: %s", response)
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

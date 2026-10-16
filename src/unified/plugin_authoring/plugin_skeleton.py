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
``parameterHandler``/``customParamHandler``/per-declared-AI-tool helpers/
per-controller-nodedef-``cmds.accepts`` command helpers) in after the pieces
rendered here -- this module only ever emits the boilerplate half:
``__init__``, the private (name-mangled) wiring methods that ``subscribe()``
actually calls, and the two tiny bootstrap files.
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

# The one webhook-wiring override, present only when webhook_enabled (Stage 7) -- listed
# separately from BASE_OVERRIDE_METHODS since it's conditional, same reasoning as
# AI_REQUEST_OVERRIDE_METHOD.
WEBHOOK_OVERRIDE_METHOD = "handle_webhook"

# The one customTypedParams-wiring override, present only when custom_typed_params was given
# (generate_plugin_scaffold's own top-level input, not a server_entry field -- see
# render_custom_param_docs_call for why that split exists) -- listed separately from
# BASE_OVERRIDE_METHODS for the same reason as WEBHOOK_OVERRIDE_METHOD: it only applies to
# some plugins, never stubbed for one that didn't ask for it. Receives the customer-entered
# values matching the pushed schema (the CUSTOMTYPEDDATA event's payload) as a single
# positional argument, named to read clearly next to CUSTOMPARAMS' own parameterHandler.
CUSTOM_TYPED_DATA_OVERRIDE_METHOD = "customTypedDataHandler"

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


def controller_command_method_name(command_id: str) -> str:
    """The dispatch method name for one controller nodedef ``cmds.accepts``
    id -- the ``BASE_OVERRIDE_METHODS`` entry it collides with
    case-insensitively, if any (e.g. accepts id ``"DISCOVER"`` reuses the
    already-wired ``discover()`` override -- ``self.poly.subscribe(self.poly
    .DISCOVER, self.discover)`` in ``render_init`` -- rather than generating
    a second method for the same event), or else the id lowercased
    (``"QUERYALL"`` -> ``"queryall"``). Pure naming only -- the caller
    (``handlers/scaffold.py``'s ``_resolve_controller_command_dispatch``)
    validates the result is a legal, non-keyword, non-dunder Python
    identifier and that no two command ids on the same class collide on the
    same derived name."""
    lowered = command_id.lower()
    for base_name in BASE_OVERRIDE_METHODS:
        if base_name.lower() == lowered:
            return base_name
    return lowered


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
    webhook_enabled: bool,
    controller_address: str,
    controller_node_name: str,
    custom_param_docs: str | None = None,
    custom_typed_params: list[dict] | None = None,
) -> str:
    """The Controller's ``__init__`` method body (full ``def __init__...``
    through the final ``self.poly.addNode(self, conn_status='ST')``) -- the
    exact verified ``subscribe()`` list, plus the OAuth service construction/subscription
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

    When *custom_typed_params* is given (a list of ``{name, title, type?,
    defaultValue?, desc?, isRequired?, isList?, params?}`` dicts -- the
    richer, schema-driven alternative to flat ``customParams``, confirmed
    against the installed ``udi_interface`` package's ``Custom.load`` and
    ``Interface.saveTypedParams`` docstring), two ``udi_interface.Custom``
    instances are constructed right after the ``custom_param_docs_call``
    line: ``self.CustomTypedParams`` (the schema, bucket ``'customtypedparams'``)
    and ``self.CustomTypedData`` (the customer-entered values matching it,
    bucket ``'customtypeddata'``) -- this exact pairing of names was chosen to
    mirror ``udi_interface``'s own paired bucket names. *custom_typed_params*
    itself is then pushed to PG3 via ``self.CustomTypedParams.load(..., True)``
    -- the ``True`` matters: ``Custom.load``'s ``save`` argument defaults to
    ``False``, which would leave the schema registered only in local memory
    and never actually sent, so PG3 would never render the config UI for it.
    The schema is embedded via ``!r`` (Python's ``repr()``), not a hand-built
    literal -- every field in the shape above is a plain str/bool/None/list/
    dict, so its repr is already valid Python source. A matching
    ``self.poly.subscribe(self.poly.CUSTOMTYPEDDATA, self.customTypedDataHandler)``
    is added to the subscribe block below; ``customTypedDataHandler`` is an
    LLM-authored override (see ``CUSTOM_TYPED_DATA_OVERRIDE_METHOD``), not
    wired here, since only the override body knows what to do with a given
    plugin's own customer-entered values (typically at least
    ``self.CustomTypedData.load(custom_params)`` to cache them).

    ``self.configDone``/``self.configDoneAlready`` back ``__start``'s
    wait-for-configDone gate (``render_private_wiring_methods``, below) --
    nothing is actually configured yet when the real ``START`` event fires
    (no custom params, no previously-discovered nodes, no OAuth token), so
    the LLM-authored ``start()`` override must not run until ``CONFIGDONE``
    has fired, or a timeout gives up.

    When *webhook_enabled*, both the ``WEBHOOK`` subscription and the paired
    ``self.poly.webhookStart()`` call fire unconditionally right here in
    ``__init__`` -- deliberately *not* gated behind the ``configDone`` wait
    ``start()``'s override is (confirmed against the installed
    ``udi_interface`` package: ``Interface.send()``, which both
    ``webhookStart()``/``webhookResponse()`` call underneath, only enqueues
    to an in-memory queue a background thread drains once connected -- there
    is no connection-ordering hazard to wait for here, unlike OAuth's
    genuinely-external "token not ready yet" case). ``webhookStart()`` itself
    raises unless the matching ``{"enableWebhook": True}`` option was passed
    to ``Interface(...)`` -- ``render_main_py`` always passes it whenever
    *webhook_enabled* is true, so a raise here can only mean this tool's own
    codegen let the two flags fall out of sync, which should fail loudly, not
    be swallowed.

    *controller_address* is this plugin's own MAC-derived, generation-time-
    computed address (see ``handlers/scaffold.py``'s guard 4a) -- ``primary``
    and ``address`` both default to it (this Controller is its own primary,
    a self-referential singleton, same as the one real construction call in
    ``render_main_py`` always passes). ``name`` defaults to
    *controller_node_name* (``"<project name> Controller"`` -- never the bare
    word ``"Controller"``, which would be meaningless once a customer has
    more than one node-server installed) -- that default is never actually
    relied on, since ``render_main_py``'s construction call always passes all
    four arguments explicitly, but it still shouldn't carry a generic
    placeholder in human-readable generated source.

    ``self.poly.addNode(self, conn_status='ST')`` (the final line) tells PG3
    to manage the Controller's own ``ST`` driver's connected/disconnected
    value automatically (``Interface.setController`` -- confirmed against
    the installed ``udi_interface`` package: there is no local Python logic
    that toggles it, PG3 does this entirely server-side once told which
    driver to use) -- paired with the ``drivers = [...]`` class variable
    ``_assemble_plugin_py`` always emits for the Controller
    (``{'driver': 'ST', 'value': 0, 'uom': 25, 'name': 'Status'}``) and the
    matching ``ST`` nodedef property ``handlers/scaffold.py``'s guard 4a
    always injects. No LLM-authored override body should ever call
    ``self.setDriver('ST', ...)`` on the Controller itself -- that would
    just fight PG3's own automatic management of it."""
    lines = [
        f"    def __init__(self, polyglot, primary={controller_address!r}, address={controller_address!r}, "
        f"name={controller_node_name!r}):",
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
    if custom_typed_params:
        lines.extend(
            [
                "        self.CustomTypedParams = udi_interface.Custom(self.poly, 'customtypedparams')",
                "        self.CustomTypedData = udi_interface.Custom(self.poly, 'customtypeddata')",
                f"        self.CustomTypedParams.load({custom_typed_params!r}, True)",
            ]
        )
    if node_classes:
        names = ", ".join(node_classes)
        lines.append(
            f"        # Additional node classes ({names}) -- construct and self.poly.addNode()"
            " them from discover()/start() once their real addresses/names are known"
            " (run candidate names/addresses through self.poly.getValidName()/getValidAddress() first)."
        )
    if authorize:
        lines.append("        self.oauthService = udi_interface.OAuth(self.poly)")
        lines.append("        self.startCalled = False")
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
        ]
    )
    if custom_typed_params:
        lines.append("        self.poly.subscribe(self.poly.CUSTOMTYPEDDATA, self.customTypedDataHandler)")
    lines.extend(
        [
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
    if webhook_enabled:
        lines.append(f"        self.poly.subscribe(self.poly.WEBHOOK, self.{WEBHOOK_OVERRIDE_METHOD})")
        lines.append("        self.poly.webhookStart()")
    lines.append("        self.poly.addNode(self, conn_status='ST')")
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
    yet.

    For an *authorize* plugin specifically, a human may not click
    "Authenticate" within ``__start``'s 10s window at all (the realistic
    case on first boot) -- or ``OAuth._updateRefreshTimer()``'s own
    synchronous token-refresh HTTP call (confirmed against the installed
    package) can delay ``CONFIGDONE`` itself past that same window on a
    restart with an already-expired token. Either way, ``__start``'s own
    wait gives up permanently without ever calling ``self.start()``. So for
    *authorize* only, ``__start``/``__configDoneHandler``/``__oauthHandler``
    all funnel through ``__runStartOnce`` (guarded by ``self.startCalled``,
    set up in ``render_init``) instead of ``__start`` calling ``self.
    start()`` directly -- whichever of the three actually observes a real
    access token first (an on-time ``configDone``, a delayed one, or a
    fresh ``OAUTH`` event arriving well after boot) is the one that runs
    it, exactly once, regardless of ordering. ``__oauthHandler`` additionally
    must call ``self.oauthService.oauthHandler(token)`` and
    ``__customNSHandler`` must forward the ``"oauthTokens"`` ``CUSTOMNS``
    key the same way it already forwards ``"oauth"`` -- both confirmed
    against the installed package as the only two paths that ever populate
    ``self.oauthService``'s saved tokens at all; omitting either leaves
    ``getAccessToken()`` permanently raising ``ValueError`` even after a
    real, successful authentication."""
    if authorize:
        methods = [
            '''    def __start(self):
        LOGGER.info("%s: start", self.name)
        if not self.configDoneAlready:
            with self.configDone:
                if not self.configDone.wait(timeout=10):
                    LOGGER.error(
                        "%s: timed out waiting for configDone -- will call start() once "
                        "authentication completes instead", self.name,
                    )
                    return
        self.__runStartOnce()
''',
        ]
    else:
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
        ]
    methods.extend([
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
    ])
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
        self.__runStartOnce()
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
        if key in ("oauth", "oauthTokens"):
            self.oauthService.customNsHandler(key, data)
            return
        self.customParamHandler(key, data)
'''
        )
        methods.append(
            '''    def __oauthHandler(self, token):
        LOGGER.info("%s: oauth token refreshed", self.name)
        self.oauthService.oauthHandler(token)
        try:
            self.oauthService.getAccessToken()
        except ValueError:
            LOGGER.warning("%s: oauth token refresh did not yield a usable access token yet", self.name)
            return
        with self.configDone:
            self.configDoneAlready = True
            self.configDone.notifyAll()
        self.__runStartOnce()
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
    if authorize:
        methods.append(
            '''    def __runStartOnce(self):
        with self.configDone:
            if self.startCalled:
                return
            self.startCalled = True
        try:
            self.start()
        except Exception as ex:
            LOGGER.error("%s: start failed: %s", self.name, ex)
'''
        )
    return "\n".join(methods)


def render_main_py(
    controller_module: str,
    controller_class: str,
    *,
    controller_address: str,
    controller_node_name: str,
    webhook_enabled: bool,
) -> str:
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
    change, nothing to opt into or out of at generation time.

    The re-exec additionally only fires when this process was launched
    directly (a terminal, the VSCode debugger via ``setup_vscode_debug_
    config``) -- never when the real host's own rc.d/``init``-managed
    service framework started it **as a genuine production install**.
    Confirmed on a live host: a daemonized service process is reparented to
    ``init`` (PID 1) once its original forking parent exits, so a genuinely
    framework-launched process's own parent command is ``"/sbin/init"``
    (checked via ``ps -o command= -p <ppid>``) -- but that alone isn't
    enough to tell a real production install apart from a developer's own
    ``install_generated_plugin`` test-on-hub, since **both** start the
    plugin through the identical rc.d path and both get reparented the same
    way. The two are told apart by which user the process runs as instead:
    a real production install's service user is provisioned by the host
    framework and its name starts with ``plugin_``; a developer testing
    their own plugin runs as their own account. The re-exec is skipped only
    when *both* the parentage check **and** the username check say this is
    a framework-launched, framework-provisioned process -- a developer's
    hub test, despite matching the parentage check, still runs under their
    own username, so it still re-execs into ``.venv``, matching what
    ``install.sh`` actually installed their dependencies into. Both checks
    are wrapped defensively (each independently defaulting to ``False``,
    i.e. "not a framework-launched production install", which preserves
    today's old behavior of allowing the re-exec) since they run before
    ``udi_interface.LOGGER`` exists -- nowhere safe to surface a warning if
    either ever fails.

    Immediately before the actual ``os.execv`` call, ``PATH`` is also
    prepended with ``.venv/bin`` -- ``os.execv``'s own first argument
    (``_VENV_PYTHON``, an absolute path) already selects the right
    interpreter for *this* exec regardless of ``PATH``, but ``argv[0]`` is
    deliberately left as the bare string ``"python3"`` (not a path) for the
    rc.subr service-identity reason documented next to that call. Anything
    downstream that re-resolves that bare ``"python3"`` by searching
    ``PATH`` instead of using the exact PID (rc.subr's own restart handling,
    or any later subprocess/exec call by name) would otherwise find the
    system interpreter first and silently lose the venv again -- the same
    class of problem the argv[0] handling next to it addresses, one step
    further removed.

    *controller_address* is this plugin's own MAC-derived, generation-time-
    computed address (see ``handlers/scaffold.py``'s guard 4a) -- used for
    both ``primary`` and ``address`` in the one real construction call
    below, matching this singleton's self-referential convention.
    *controller_node_name* (``"<project name> Controller"``, built from
    ``server_entry.name``) is this Controller's display name -- never the
    bare word ``"Controller"``, which would be meaningless once a customer
    has more than one node-server installed.

    *webhook_enabled* controls the one feature-conditional piece of this
    otherwise flag-independent bootstrap: when true, ``Interface(...)`` is
    constructed with a second ``{{"enableWebhook": True}}`` options argument
    -- required before ``render_init``'s ``self.poly.webhookStart()`` call
    will do anything but raise (see ``render_init``). When false, the
    rendered line is byte-identical to this file's own pre-webhook-support
    output (``udi_interface.Interface([])``), so existing generated plugins
    are unaffected."""
    interface_options = ', {"enableWebhook": True}' if webhook_enabled else ""
    return f'''"""Generated by plugin_authoring -- bootstraps the plugin process. Do not
edit by hand; regenerate via generate_plugin_scaffold instead. See
context.md for this plugin's iteration history."""

from __future__ import annotations

import os
import subprocess
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
    # Only re-exec when this process was launched directly (a terminal, the
    # VSCode debugger) -- never when the real host's own rc.d/init-managed
    # service framework started it as a genuine production install. A
    # daemonized service process is reparented to init (PID 1) once its
    # original forking parent exits, so a genuinely framework-launched
    # process's own parent command is "/sbin/init" (confirmed on a live
    # host) -- but that alone is true for both a real production install
    # and a local install_generated_plugin test-on-hub, since both go
    # through the identical rc.d start path. Tell them apart by the user
    # the process runs as instead: a real production install's service
    # user is provisioned by the host framework and starts with "plugin_";
    # a developer's own hub test runs as their own account, and still
    # needs the re-exec -- install.sh installed their dependencies into
    # .venv, not system/--user site-packages. Skip the re-exec only when
    # BOTH checks say this is a framework-launched, framework-provisioned
    # process. Each check defaults to False (preserves the old behavior:
    # allow the re-exec) if it fails for any reason -- this runs before
    # LOGGER exists, so there is nowhere safe to surface a warning yet.
    _is_framework_launch = False
    try:
        _ppid_command = subprocess.run(
            ["ps", "-o", "command=", "-p", str(os.getppid())], capture_output=True, text=True
        ).stdout.strip()
        _is_framework_launch = _ppid_command.startswith("/sbin/init")
    except Exception:
        pass

    _is_framework_user = False
    try:
        import getpass
        _is_framework_user = getpass.getuser().startswith("plugin_")
    except Exception:
        pass

    if not (_is_framework_launch and _is_framework_user):
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
        # See the argv[0] reasoning just above -- same reason, same fix, needed
        # here too: anything that re-resolves the bare "python3" argv[0] by
        # searching PATH instead of using the exact PID (rc.subr's own restart
        # handling, or any later subprocess/exec call by name) should still
        # find this venv's interpreter first, not the system one.
        os.environ["PATH"] = os.path.dirname(_VENV_PYTHON) + os.pathsep + os.environ.get("PATH", "")
        os.execv(_VENV_PYTHON, ["python3"] + sys.argv)

import json

import udi_interface

import version
from {controller_module} import {controller_class}

LOGGER = udi_interface.LOGGER

if __name__ == "__main__":
    try:
        polyglot = udi_interface.Interface([]{interface_options})
        polyglot.start(version.ud_plugin_version)
        profile_path = os.path.join(_PLUGIN_DIR, "profile.json")
        with open(profile_path, "r", encoding="utf-8") as f:
            response = polyglot.updateJsonProfile(json.load(f), {{"waitResponse": True}})
            LOGGER.info("Profile update response: %s", response)
        {controller_class}(polyglot, {controller_address!r}, {controller_address!r}, {controller_node_name!r})
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

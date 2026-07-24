"""Abstract interface and event-handling base for NuCore backends.

Defines :class:`NuCoreInterface` — the ABC that all concrete backends
(e.g. :class:`IoXWrapper`) must implement — and :class:`PromptFormatTypes`
which controls how device data is formatted into LLM prompts.
"""

from __future__ import annotations

from time import sleep
import threading

import asyncio

from .profile import Profile
from .group import Group, GroupMemberType
from .node import Node
from .folder import Folder
from .nodedef import Property
from .cmd import Command
from typing import Any, Literal
from abc import ABC, abstractmethod
from utils import get_logger

logger = get_logger(__name__)

class PromptFormatTypes:
    """Constants for the two supported device-data prompt formats."""
    DEVICE = "per-device"
    PROFILE = "shared-features"


def _normalize_name(name: str | None) -> str:
    """Case/whitespace-normalize a display name for exact-match comparison."""
    return " ".join((name or "").strip().casefold().split())

class NuCoreInterface(ABC):

    def __init__(self, json_output:bool, formatter_type:str):
        self.device_structure_changed = True # flag to track if device structure has changed and needs refreshing
        self.routines_changed = True # flag to track if programs have changed so that we can refresh them 
        self.is_subscribed = False
        self.formatter_type = formatter_type
        self.json_output = json_output
        #we manage all the objects and device information. Subclasses must fill these out upon refresh
        self.nodes = {}
        self.groups = {}
        self.folders = {} 
        self.rags = None
        self.summary_rags = None
        self.profile = Profile(timestamp="", families=[])
        self.all_routines: dict[str, Any] = {}
        self.condensed_routines: list = []
        # Variables (integer/state counters routines can reference), keyed
        # "<type>:<id>" (type 1=integer, 2=state) to keep the two id spaces
        # from colliding -- see _load_variables/variable_ops.
        self.variables: dict[str, Any] = {}
        self.condensed_variables: list = []
        self.json_output = json_output
        self._subscribe_thread: threading.Thread | None = None
        self._subscribe_lock = threading.Lock()

    def get_groups_for_device(self, device_address: str, controller_only: bool = False) -> list[Group]:
        """Return all groups that contain the given device address.

        Args:
            device_address: Device/group node address to search for.
            controller_only: When True, only return groups where the device is
                a controller member.

        Returns:
            List of :class:`~nucore.group.Group` instances containing the
            device.
        """
        address = (device_address or "").strip()
        if not address:
            return []

        out: list[Group] = []
        for group in self.groups.values():
            if not isinstance(group, Group):
                continue

            member = group.members.get(address, None)
            if member is None:
                continue

            if controller_only and member.type != GroupMemberType.MEMBER_IS_CONTROLLER:
                continue

            out.append(group)

        return out

    def get_node(self, device_id: str) -> Node | Group | Folder | None:
        """Resolve a device_id (as emitted by the compact DEVICE DATABASE) to
        its real Node/Group/Folder object.

        Checks ``self.nodes``, then ``self.groups``, then ``self.folders`` --
        the same three-dict-fallback order used throughout ``IoXWrapper``
        (e.g. ``get_device_name``, ``_get_node_type``). ``device_id`` is
        decoded first (a no-op today, correct if id-encoding is ever turned
        on) so this stays compatible with the same ids the compact device
        database and every existing lookup already use.

        Returns:
            The matching ``Node``/``Group``/``Folder``, or ``None``.
        """
        # Deferred import: src/rag imports from src/nucore at module load
        # time, so importing it eagerly here would be a circular import.
        from rag.profile_rag_formatter import ProfileRagFormatter

        address = ProfileRagFormatter.decode_id(device_id)
        node = self.nodes.get(address)
        if node is None:
            node = self.groups.get(address)
        if node is None:
            node = self.folders.get(address)
        return node

    def resolve_property_id(self, device_id: str, name: str) -> str | None:
        """Exact-match a property display name (as read from the compact
        DEVICE DATABASE) to its real property id, scoped to this one
        device's own NodeDef.

        Case/whitespace-normalized exact match only -- never fuzzy. Never
        looks outside this device's own ``properties``, which is what keeps
        a name shared between a property and an accepts/sends command (a
        real, confirmed case in this system -- e.g. "On Level" is both a
        property and a command on the same device) from resolving
        ambiguously: the caller picks the namespace by which resolver it
        calls, the same way ``get_property``/``send_command`` are separate
        tools.

        Returns:
            The real property id, or ``None`` if no exact match was found.
        """
        node = self.get_node(device_id)
        if node is None or not getattr(node, "node_def", None):
            return None
        target = _normalize_name(name)
        for prop in node.node_def.properties.values():
            if prop.name and _normalize_name(prop.name) == target:
                return prop.id
        return None

    def resolve_command_id(
        self, device_id: str, name: str, direction: Literal["accepts", "sends"] = "accepts"
    ) -> Command | None:
        """Exact-match a command display name to its real ``Command``,
        scoped to this device's own ``accepts`` or ``sends`` list (never
        both combined, and never a device's ``properties`` -- same
        namespace-scoping rationale as :meth:`resolve_property_id`).

        Returns the matched ``Command`` object (not just its id) since
        callers need its ``.parameters`` to resolve/validate a value too.

        Returns:
            The matched ``Command``, or ``None`` if no exact match was found.
        """
        node = self.get_node(device_id)
        if node is None or not getattr(node, "node_def", None):
            return None
        commands = node.node_def.cmds.accepts if direction == "accepts" else node.node_def.cmds.sends
        target = _normalize_name(name)
        for cmd in commands:
            if cmd.name and _normalize_name(cmd.name) == target:
                return cmd
        return None

    async def _refresh_device_structure(self) -> bool:
        """
        Refresh device structure if necessary.
        Check for changes in device structure and update internal state if changes are detected.
        :return: True if device structure has changed, False otherwise.
        """
        if not self.device_structure_changed:
            return False #already refreshed no need to check again

        while not self.is_subscribed:
        ## subscribe to get events from devices
            self.subscribe_events(self._on_device_event, self._on_connect_callback, self._on_disconnect_callback)
            sleep(1) # wait a bit for the subscription to be established

        await self._load(include_profiles=True)
        self.device_structure_changed = False 
        return True

    @abstractmethod 
    async def _load(self, **kwargs):
        """
        Load devices and profiles from the specified paths or URL.
        :param kwargs: Optional parameters for loading.
        - profile_path: Path to the profile file. If not provided, will use the configured URL.
        - nodes_path: Path to the nodes XML file. If not provided, will use the configured URL.
        - dump: If True, dump the processed RAG documents to a file.
        - include_profiles: If True, include profiles in the loading process.
        """
        raise NotImplementedError("Subclasses must implement the _load method.")
    
    async def _refresh_routines_database(self):
        """
            Refresh routines (and variables) database if necessary.
            :return True if routines were refreshed, False otherwise.
            return is mandatory because we want to make sure the caller knows whether the routines were refreshed or not so that it can decide whether to refresh the prompt or not.
        """
        await self._refresh_device_structure() # make sure we have the latest device structure before refreshing routines
        if not self.routines_changed:
            return False # already refreshed no need to check again
        # Variables load first -- routines cross-reference which variables
        # they use (variable_names on each condensed routine), which needs
        # self.variables already populated. Both are gated by the same
        # routines_changed flag: variables and routines always refresh
        # together, no separate dirty flag needed.
        await self._load_variables()
        await self._load_routines() # load routines from the device
        self.routines_changed = False
        return True

    @abstractmethod
    async def _load_routines(self):
        """
        Load routines from the device and update internal state.
        :return: True if routines were successfully loaded, False otherwise.
        """
        raise NotImplementedError("Subclasses must implement the _load_routines method.")

    @abstractmethod
    async def _load_variables(self):
        """
        Load all variables (integer and state) from the device and populate
        self.variables/self.condensed_variables.
        """
        raise NotImplementedError("Subclasses must implement the _load_variables method.")

    def get_variable(self, var_type: int | str, var_id: int | str) -> dict | None:
        """Resolve a (type, id) pair to its variable record (see
        self.variables' "<type>:<id>" keying) -- parallel to get_node."""
        return self.variables.get(f"{var_type}:{var_id}")

    @abstractmethod
    async def send_commands(self, commands:list):
        """
        Send commands to the device using the nucore API.
        :param commands: A list of commands to send. Each command should be a dictionary containing the command details.
        :return: The response from the API or raises an error if the command fails.
        """
        raise NotImplementedError("Subclasses must implement the send_commands method.")

    @abstractmethod 
    async def create_automation_routine(self, routine:dict):
        """
        Create automation routines using the nucore API.
        """
        raise NotImplementedError("Subclasses must implement the create_automation_routine method.")

    @abstractmethod 
    async def update_routine(self, routine:dict):
        """
        Update an existing automation routine using the nucore API.
        """
        raise NotImplementedError("Subclasses must implement the create_automation_routine method.")

    @abstractmethod
    async def get_properties(self, device_id:str)-> dict[str, Property]:
        """
        Get properties of a device by its ID.
        
        Args:
            device_id (str): The ID of the device to get properties for.
        
        Returns:
            dict[str, Property]: A dictionary of properties for the device.
        Raises:
            NuCoreError: If the device_id is empty or if the response cannot be parsed.
        """
        raise NotImplementedError("Subclasses must implement the get_properties method.")

    @abstractmethod
    def get_device_name(self, device_id:str)-> str:
        """
        Get the name of a device by its ID.
        
        Args:
            device_id (str): The ID of the device to get the name for.
        
        Returns:
            str: The name of the device, or None if not found.
        """
        raise NotImplementedError("Subclasses must implement the get_device_name method.")

    @abstractmethod
    def get_device_id(self, device_str:str)-> str:
        """
        Get the id of a device by a string. It searches id first, if not by name 
        
        Args:
            device_str (str): The string to identify the device (either ID or name).
        
        Returns:
            str: The ID of the device, or None if not found.
        """
        raise NotImplementedError("Subclasses must implement the get_device_id method.")

    @abstractmethod
    async def get_all_routines_summary(self):
        """
        Get the runtime-state summary (enabled/running/last-run-time/etc.) for
        every routine and folder from the IoX device -- not the if/then/else
        logic itself (see get_all_routines() for that).
        :return: JSON response containing all routine/folder summaries or None if failure.
        """
        raise NotImplementedError("Subclasses must implement the get_all_routines_summary method.")

    @abstractmethod
    async def get_routine_summary(self, routine_id:str):
        """
        Get all the runtime information for a specific routine from the IoX device.
        :param routine_id: The ID of the program to retrieve.
        :return: JSON response containing the routine information or None if failure.
        """
        raise NotImplementedError("Subclasses must implement the get_routine_summary method.")

    @abstractmethod
    async def get_all_routines(self):
        """
        Get complete information for all routines from the IoX device including their logic, triggers, and actions. 
        :return: JSON response containing all routines or None if failure
        """
        raise NotImplementedError("Subclasses must implement the get_all_routines method.")

    @abstractmethod  
    async def get_routine(self, routine_id:str):
        """
        Get complete information for a specific routine from the IoX device including its logic, triggers, and actions. 
        :param routine_id: The ID of the program to retrieve.
        :return: JSON response containing the routine information or None if failure
        """
        raise NotImplementedError("Subclasses must implement the get_routine method.")

    @abstractmethod 
    async def add_node(self, node_name:str, type:Literal["folder", "group"]):
        """
        Add a new node (folder or group) to the device structure.
        :param node_name: The name of the node to add.
        :param type: The type of the node, either "folder" or "group".
        :return: response from the API or None if failure 
        """
        raise NotImplementedError("Subclasses must implement the add_node method.")
    
    @abstractmethod
    async def node_ops(self, node_id:str, operation:Literal["delete", "enable", "disable", "rename", "move", "group"], **kwargs):
        """
        Perform an operation on a node (folder or group).
        :param node_id: The ID of the node to operate on.
        :param operation: The operation to perform (e.g., "delete", "enable", "disable", "rename", "move").
        :param kwargs: Additional parameters for the operation:
          new_name for rename
          new_parent_id for move
        :return: response from the API or None if failure 
        """
        raise NotImplementedError("Subclasses must implement the node_ops method.")

    @abstractmethod 
    async def routine_ops(self, routine_id:int, operation:Literal["runIf", "runThen", "runElse", "stop", "enable", "disable", "enableRunAtStartup", "disableRunAtStartup"]):
        """
        Perform an operation on a program.
        :param routine_id: The ID of the program/routine to operate on.
        :param operation: The operation to perform (e.g., "runIf", "runThen", "runElse", "stop", "enable", "disable", "enableRunAtStartup", "disableRunAtStartup").
        :return: response from the API or None if failure 
        """
        raise NotImplementedError("Subclasses must implement the routine_ops method.")

    @abstractmethod
    async def variable_ops(self, var_type: int, var_id: str | None, operation: Literal["create", "update", "delete"], **kwargs):
        """
        Create, update, or delete a NuCore variable.
        :param var_type: 1 (integer variable) or 2 (state variable).
        :param var_id: The variable's id -- required for "update"/"delete", ignored for "create".
        :param operation: "create", "update", or "delete".
        :param kwargs: For "create"/"update": name, prec, value, init (all optional).
        :return: response from the API or None if failure
        """
        raise NotImplementedError("Subclasses must implement the variable_ops method.")

    # ------------------------------------------------------------------
    # Group/scene API orchestration
    # ------------------------------------------------------------------

    @abstractmethod
    def group_scene_add_member(
        self,
        group_address: str,
        link_address: str,
        is_controller: bool,
        name: str | None = None,
    ) -> dict[str, Any]:
        """Add a node as controller/responder member to a group."""
        raise NotImplementedError("Subclasses must implement group_scene_add_member.")

    @abstractmethod
    def group_scene_remove_member(self, group_address: str, link_address: str) -> dict[str, Any]:
        """Remove a node member from a group."""
        raise NotImplementedError("Subclasses must implement group_scene_remove_member.")

    @abstractmethod
    def group_scene_update_link(
        self,
        group_address: str,
        controller_address: str,
        link: dict[str, Any],
    ) -> dict[str, Any]:
        """Create or update link behavior for a group member pair."""
        raise NotImplementedError("Subclasses must implement group_scene_update_link.")

    @abstractmethod
    def group_scene_get_node_roles(self, node_address: str) -> dict[str, Any] | None:
        """Fetch node role capability details for a group member candidate."""
        raise NotImplementedError("Subclasses must implement group_scene_get_node_roles.")

    @abstractmethod
    def group_scene_get_link_types(self, controller_address: str, link_address: str) -> dict[str, Any] | None:
        """Fetch supported link types for a controller/responder pair."""
        raise NotImplementedError("Subclasses must implement group_scene_get_link_types.")

    # ------------------------------------------------------------------
    # Timezone management 
    # ------------------------------------------------------------------
    async def get_timespecs(self) -> dict[str, str]:
        """
        Get time configuration and timezone information from the device. 

        API:
        /rest/time
        """
        raise NotImplementedError("Subclasses must implement the get_timespecs method.")

    # ------------------------------------------------------------------
    # Plugin management  
    # ------------------------------------------------------------------
    
    async def get_active_plugins(self) -> dict[str, str]:
        """
        Get a list of active plugins that can be installed on the device.
        :return: Dictionary of active plugins or None if failure

        API:
        /api/plugins/store/list/active
        """
        raise NotImplementedError("Subclasses must implement the get_active_plugins method.")

    async def get_purchased_plugins(self) -> dict[str, str]:
        """
        Get the licenses this installation has purchased for plugins (may
        include more than one license row per nsid, e.g. Free + Standard
        editions). Licenses only carry ``nsid`` -- pair with
        ``get_active_plugins()`` to resolve a human-readable name.
        :return: Dictionary of purchased plugin licenses or None if failure

        API:
        /api/plugins/licenses
        """
        raise NotImplementedError("Subclasses must implement the get_purchased_plugins method.")

    async def get_installed_plugins(self) -> dict[str, str]:
        """
        Get a list of plugins installed on this device.
        :return: Dictionary of installed plugins or None if failure

        API:
        /api/plugins

        Response shape:
        {"successful": true, "data": [{"profileNum": 3, "name": "YouTube", "isLocal": false}, ...]}

        ``profileNum`` is this plugin's id -- used as ``plugin_id`` for
        subsequent plugin_ops()/configure_plugin() calls.
        """
        raise NotImplementedError("Subclasses must implement the get_installed_plugins method.")
    
    async def plugin_ops(self, plugin_id:str, operation:Literal["details", "install", "uninstall", "status", "start", "stop", "restart"]):
        """
        Perform an operation on a plugin.
        :param plugin_id: The ID of the plugin to operate on -- profileNum
                           from get_installed_plugins() for start/stop/restart
                           (and, once implemented, install/uninstall/status);
                           nsid for details.
        :param operation: The operation to perform.
        :return: response from the API or None if failure

        Details API:
        /api/plugins/store/entry/:nsid

        Start/Stop/Restart API:
        /api/plugins/<profileNum>/start
        /api/plugins/<profileNum>/stop
        /api/plugins/<profileNum>/restart
        """
        raise NotImplementedError("Subclasses must implement the plugin_ops method.")
    
    async def configure_plugin(self, plugin_id:str, config:dict[str, Any]):
        """
        Configure a plugin on the device. 
        :param plugin_id: The ID of the plugin to configure.
        :param config: A dictionary containing the configuration parameters.
        :return: response from the API or None if failure 
        """
        raise NotImplementedError("Subclasses must implement the configure_plugin method.")

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    @abstractmethod
    async def start_diagnostics(self, *, session_id: str | None = None, **kwargs):
        """
        Open (or re-show) the one diagnostic session -- there's a single
        diagnostics flow, not a menu of named plans. The response carries an
        "instruction" (loaded from a prompt) plus the shared catalog of steps
        the model can call via run_diagnostic_step, guided by that
        instruction and by what the customer actually described, instead of
        the backend pre-mapping every complaint to a canned plan.

        Only one session may be open at a time, system-wide, and it's owned
        by whichever session_id started it -- calling this again with the
        SAME session_id while it's in progress just re-shows the
        instruction/steps; a DIFFERENT session_id is refused (a real
        hub-level diagnostic shouldn't be interruptible/restartable by an
        unrelated conversation).

        :param session_id: Identifies which conversation is starting/driving
                       this session. unified.dispatch.execute_tool enforces
                       the actual ownership gate (blocking every other tool
                       for every OTHER session while one is active); backends
                       should still track and check it themselves too.
        :param kwargs: Optional candidate_devices/candidate_routines -- fuzzy
                       devices/scenes/routines the caller identified as
                       relevant, echoed back in every response for the
                       session.
        :return: {"status": "in_progress", "instruction", "available_tools", "candidates"?}
                 or {"error": ...} if a different session already owns the active one.
        """
        raise NotImplementedError("Subclasses must implement the start_diagnostics method.")

    @abstractmethod
    async def run_diagnostic_step(self, step: str, *, session_id: str | None = None, **params):
        """
        Run one step of the diagnostic session currently in progress (see
        start_diagnostics) -- the model picks which step to call, guided by
        the standing instruction.
        :param step: One of the step names from start_diagnostics' "available_tools".
        :param session_id: Must match the session that started the current
                       session -- see start_diagnostics.
        :param params: Forwarded to the step's underlying function.
        :return: {"step", "result"} on success, or {"error": ...}. The
                 dedicated "conclude"/"stop" steps end the session instead,
                 returning {"status": "completed", "summary"?} or
                 {"status": "stopped", "result"}.
        """
        raise NotImplementedError("Subclasses must implement the run_diagnostic_step method.")

    @abstractmethod
    def get_running_diagnostic(self) -> dict[str, Any] | None:
        """
        Return info about the diagnostic session currently in flight, if
        any -- used to gate every other tool call while one is running (see
        unified.dispatch.execute_tool): one diagnostic system-wide, but
        scoped by session_id so only the owning conversation's calls to
        start_diagnostics/run_diagnostic_step get through -- every other
        session's calls, of any tool, are refused. Counts as "in flight" for
        its whole multi-step duration, not just its initial call.
        :return: {"status": "in_progress", "elapsed_s": <int>, "session_id": <str|None>}
                 or None if nothing is running (including a stale/timed-out one).
        """
        raise NotImplementedError("Subclasses must implement the get_running_diagnostic method.")

    def subscribe_events(self, on_message_callback, on_connect_callback=None, on_disconnect_callback=None):
        """
        Subscribe to device events using the nucore API.
        
        Args:
            on_message_callback (callable): Callback function to handle incoming messages.
            on_connect_callback (callable, optional): Callback function to handle connection events.
            on_disconnect_callback (callable, optional): Callback function to handle disconnection events.
        """
        with self._subscribe_lock:
            if self._subscribe_thread is not None and self._subscribe_thread.is_alive():
                return

            def _runner() -> None:
                try:
                    asyncio.run(
                        self._subscribe_events(
                            on_message_callback=on_message_callback,
                            on_connect_callback=on_connect_callback,
                            on_disconnect_callback=on_disconnect_callback,
                        )
                    )
                except Exception as ex:
                    logger.debug(f"Failed to subscribe to events: {str(ex)}")
                finally:
                    with self._subscribe_lock:
                        self._subscribe_thread = None

            self._subscribe_thread = threading.Thread(target=_runner, name="NuCoreEventSubscriber", daemon=True)
            self._subscribe_thread.start()

    def shutdown(self, timeout_s: float = 1.0) -> None:
        """Best-effort shutdown for background subscription worker threads."""
        self.is_subscribed = False
        with self._subscribe_lock:
            thread = self._subscribe_thread
        if thread is not None and thread.is_alive() and not thread.daemon:
            thread.join(timeout=timeout_s)
    
    @abstractmethod 
    async def _subscribe_events(self, on_message_callback, on_connect_callback=None, on_disconnect_callback=None): 
        """
        Subscribe to device events using the nucore API.
        
        Args:
            on_message_callback (callable): Callback function to handle incoming messages.
            on_connect_callback (callable, optional): Callback function to handle connection events.
            on_disconnect_callback (callable, optional): Callback function to handle disconnection events.
        """
        raise NotImplementedError("Subclasses must implement the subscribe_events method.")

    
    async def _on_device_event(self, message:dict):
        """
        Callback function to handle device events.
        What we are looking for are events that change device structure such as device added/removed, property added/removed, etc.
        :param event: The event data received.
        """
        raise NotImplementedError("Subclasses must implement the subscribe_events method.")

    async def _on_connect_callback(self):
        """
        Callback function to handle connection established event.
        Subclasses may override this method
        """
        self.is_subscribed = True
        self.device_structure_changed = True # just to be on the safe side
        self.routines_changed = True # just to be on the safe side

    async def _on_disconnect_callback(self):
        """
        Callback function to handle disconnection event.
        Subclasses may override this method
        """
        self.is_subscribed = False

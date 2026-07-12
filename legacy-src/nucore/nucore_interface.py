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
from .nodedef import Property
from typing import Any, Literal
from abc import ABC, abstractmethod
from utils import get_logger

logger = get_logger(__name__)

class PromptFormatTypes:
    """Constants for the two supported device-data prompt formats."""
    DEVICE = "per-device"
    PROFILE = "shared-features"

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
            Refresh routines database if necessary.
            :return True if routines were refreshed, False otherwise.
            return is mandatory because we want to make sure the caller knows whether the routines were refreshed or not so that it can decide whether to refresh the prompt or not.
        """
        await self._refresh_device_structure() # make sure we have the latest device structure before refreshing routines
        if not self.routines_changed:
            return False # already refreshed no need to check again
        if await self._load_routines(): # load routines from the device
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
        Get all the runtime information for routines from the IoX device.
        :return: JSON response containing all routines or None if failure.
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

    async def get_installed_plugins(self) -> dict[str, str]:
        """
        Get a list of installed plugins on the device. 
        :return: Dictionary of installed plugins or None if failure
        """
        raise NotImplementedError("Subclasses must implement the get_installed_plugins method.")
    
    async def plugin_ops(self, plugin_id:str, operation:Literal["details", "install", "uninstsall", "status", "start", "stop"]):
        """
        Perform an operation on a plugin.
        :param plugin_id: The ID of the plugin to operate on.
        :param operation: The operation to perform (e.g., "start", "stop", "uninstall").
        :return: response from the API or None if failure 

        Details API: 
        /api/plugins/store/entry/:nsid
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
        if message is None or 'node' not in message or 'control' not in message:
            logger.debug(f"Received invalid message format {message}")
            return
        
        control = message['control']
        action = message.get('action', '')
        if control == "_3": #node updated event
            if action and action in [ 'NX', 'PI', 'WD' ]:
                #we may want to use 'WD' (device write pending) later for diagnostics 
                return
            self.device_structure_changed = True # just to be on the safe side
        elif control == "_1": #programs updated event
            self.routines_changed = True # just to be on the safe side

    async def _on_connect_callback(self):
        """
        Callback function to handle connection established event.
        """
        self.is_subscribed = True
        self.device_structure_changed = True # just to be on the safe side
        self.routines_changed = True # just to be on the safe side

    async def _on_disconnect_callback(self):
        """
        Callback function to handle disconnection event.
        """
        self.is_subscribed = False

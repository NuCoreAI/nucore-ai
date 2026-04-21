from __future__ import annotations

from time import sleep
import threading

import logging, json
import asyncio

from nucore import Profile, Node, NuCoreBackendAPI, NuCoreError, Property
from rag import RAGFormatter, ProfileRagFormatter, MinimalRagFormatter


logger = logging.getLogger(__name__)

class PromptFormatTypes:
    DEVICE = "per-device"
    PROFILE = "shared-features"


def debug(msg):
    logger.debug(f"[PROFILE FORMAT ERROR] {msg}")

class NuCoreInterface:

    def __init__(self, nucore_api:NuCoreBackendAPI=None, formatter_type:str=PromptFormatTypes.PROFILE):
        self.device_structure_changed = True # flag to track if device structure has changed and needs refreshing
        self.nodes = {}
        self.groups = {}
        self.folders = {} 
        self.runtime_profiles = {}
        self.rags = None
        self.summary_rags = None
        self.profile = Profile(timestamp="", families=[])
        self.formatter_type = formatter_type
        self.nucore_api = nucore_api
        self.is_subscribed = False

    def _refresh_device_structure(self) -> bool:
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

        if not self.load(include_profiles=True):
            raise ValueError("Failed to load devices from NuCore. Please check your configuration.")

        self.rags= self.format_nodes() 
        if not self.rags:
            raise ValueError(f"Warning: No RAG documents found for node {self.nuCore.url}. Skipping.")
        self.summary_rags = self.format_nodes_summary(False)
        self.device_structure_changed = False 
        return True

    def format_nodes(self):
        """
        Format nodes for fine tuning or other purposes 
        :return: List of formatted nodes.
        """
        if not self.nodes:
            raise NuCoreError("No nodes loaded.")
        device_rag_formatter = ProfileRagFormatter(json_output=self.nucore_api.json_output)
        return self._format_nodes(device_rag_formatter)

    def format_nodes_summary(self, condense_profiles:bool):
        """
        Format nodes for fine tuning or other purposes 
        :param condense_profiles: If True, condense profiles in the summary to:
        {
            "devices": [
                "Nest Matter Family Room", "Meros Smart Plug", ...
                ],
            "cmds": {
                "Cool Setpoint": [0, 8, 19],
                "On":            [1, 3, 4, 5, 13, 14, 20, 21],
                "Brighten":      [3, 14],
                ...
            },
            "props": {
                "Temperature":   [0, 8, 9, 10, 11],
                "Mode":          [0, 8, 19],
                ...
            },
            "enums": {
                "Off":    [0, 3, 4, 8, 13, 14, 19, 20, 21],
                "On":     [4, 13, 15, 17, 21],
                ...
            }
        }
        :return: List of formatted nodes.
        """
        if not self.nodes:
            raise NuCoreError("No nodes loaded.")
        device_rag_formatter = MinimalRagFormatter(json_output=self.nucore_api.json_output, condense=condense_profiles)
        return self._format_nodes(device_rag_formatter)
    
    def load(self, **kwargs):
        
        """
        Load devices and profiles from the specified paths or URL.
        :param kwargs: Optional parameters for loading.
        - profile_path: Path to the profile file. If not provided, will use the configured URL.
        - nodes_path: Path to the nodes XML file. If not provided, will use the configured URL.
        - dump: If True, dump the processed RAG documents to a file.
        - include_profiles: If True, include profiles in the loading process.
        :return: Loaded devices and profiles.
        :raises NuCoreError: If no valid profile or nodes source is provided.
        :raises NuCoreError: If the RAG processor is not initialized.
        """
        
        include_profiles = kwargs.get("include_profiles", True)

        return self.load_devices(include_profiles=include_profiles, profile_path=kwargs.get("profile_path"), nodes_path=kwargs.get("nodes_path"))

    # To have the latest state, we need to load devices only
    def load_devices(self, include_profiles=True, profile_path:str=None, nodes_path:str=None, groups_path:str=None):
        if include_profiles:
            if not self.__load_profile__(profile_path):
                return None
        
        root = self.__load_nodes__(nodes_path)
        if root == None:
            return None

        glinks_root = self.__load_groups_links__(groups_path) 
        self.runtime_profiles, self.nodes, self.groups, self.folders = self.profile.map_nodes(root, glinks_root) 

        return self.nodes
        

    def __load_profile__(self, profile_path:str=None):
        """Load profile from the specified path or URL.
        :param profile_path: Optional path to the profile file. If not provided, will use the configured url in consturctor
        :return: True if profile is loaded successfully, False otherwise. 
        :raises NuCoreError: If no valid profile source is provided.
        """
        try:
            if profile_path:
                self.profile.load_from_file(profile_path)
            elif not self.nucore_api:
                raise NuCoreError("No valid profile source provided.")
            else:
                response = self.nucore_api.get_profiles()
                if response is None:
                    raise NuCoreError("Failed to fetch profile from URL.")
                self.profile.load_from_json(response)
                return True
        except Exception as e:
            raise NuCoreError(f"Failed to load profile: {str(e)}")

        return False 
        
    def __load_nodes__(self, nodes_path:str=None):
        """Load nodes from the specified path or URL.
        :param nodes_path: Optional path to the XML file containing nodes. If not provided, will use the configured url in constructor.
        :return: Parsed XML root element containing nodes.
        :raises NuCoreError: If no valid nodes source is provided.
        
        This method will first try to load nodes from a file if `nodes_path` is provided, 
        otherwise it will attempt to load from the configured URL.
        """
        if nodes_path:
            return Node.load_from_file(nodes_path)
        
        if self.nucore_api:
            response = self.nucore_api.get_nodes()
            if response is None:
                raise NuCoreError("Failed to fetch nodes from URL.")
            return Node.load_from_xml(response)
        
        raise NuCoreError("No valid nodes source provided.")

    def __load_groups_links__(self, groups_path:str=None):
        """Load group links from the specified path or URL.
        :param groups_path: Optional path to the JSON file containing group links. If not provided, will use the configured url in constructor.
        :return: Parsed JSON object containing group links.
        :raises NuCoreError: If no valid group links source is provided.
        
        This method will first try to load groups from a file if `groups_path` is provided, 
        otherwise it will attempt to load from the configured URL.
        """
        if groups_path:
            return Node.load_from_json(groups_path)
        
        if self.nucore_api:
            response = self.nucore_api.get_group_links()
            if response is None:
                raise NuCoreError("Failed to fetch group links from URL.")
            return Node.load_from_json(response)
        
        raise NuCoreError("No valid groups source provided.")
    
    def _format_nodes(self, device_rag_formatter:RAGFormatter):
        """
        Summary list of nodes to reduce the context window size: 
        :return: List of summary formatted nodes.
        """
        if not self.nodes:
            raise NuCoreError("No nodes loaded.")
        if not device_rag_formatter:
            raise NuCoreError("No device rag formatter provided.")

        if self.formatter_type == PromptFormatTypes.PROFILE:
            return device_rag_formatter.format(profiles=self.runtime_profiles, nodes=self.nodes, groups=self.groups, folders=self.folders ) 
         
        if self.formatter_type == PromptFormatTypes.DEVICE:
            return device_rag_formatter.format(nodes=self.nodes, groups=self.groups, folders=self.folders ) 
        
        print (f"Unknown formatter type: {self.formatter_type}, defaulting to per-device format.")
        return device_rag_formatter.format(nodes=self.nodes, groups=self.groups, folders=self.folders)

    async def send_commands(self, commands:list):
        for cmd in commands:
            if "device" in cmd:
                #device ids are in base64 encoded, decode it
                device_id = cmd["device"]
                cmd["device"] = ProfileRagFormatter.decode_id(device_id)
        response = self.nucore_api.send_commands(commands)
        if response is None:
            raise NuCoreError("Failed to send commands.")
        return response
    
    async def create_automation_routine(self, routine:dict):
        """
        Create automation routines using the nucore API.
        
        Args:
            routine (dict): A routine to create.
        """
        if not routine:
            raise NuCoreError ("No valid routine provided.")
        try: 
            out_routine={
                "name": f"{routine['name']}",
                "parent": routine['parent'],
                "enabled": routine['enabled'] ,
                "if": [],
                "then": [],
                "else": []
            }
            ifs = routine.get("if", None)
            if ifs is not None and len (ifs) > 0:
                for if_ in ifs:
                    keys = list(if_.keys())
                    if "comp" in keys or "eq" in keys:
                        condition = if_
                        if not isinstance(condition, dict):
                            continue
                        if not "device" in condition or not "precision" in condition or not "value" in condition or not "uom" in condition:
                            continue
                        device_id = condition.get("device", None)
                        if device_id is None:
                            continue
                        device_id = self.get_device_id(device_id)
                        if device_id is None: 
                            continue
                        # device ids are in base64 encoded, decode it
                        device_id = ProfileRagFormatter.decode_id(device_id)
                        condition["device"] = device_id
                        uom_id = condition.get("uom", None)
                        precision = condition.get("precision", None)
                        value = condition.get("value", None)
                        if uom_id is None or int(uom_id) == 25 or precision is None or value is None:
                            continue
                        value = value * (10 ** precision)
                        condition["value"] = int(value)
                    out_routine['if'].append(if_)
            
            thens = routine.get("then", None)
            if thens is not None and len (thens) > 0:
                for then in thens:
                    device_id = then.get("device", None)
                    if device_id is not None:
                        device_id = self.get_device_id(device_id)
                        if device_id is None: 
                            continue
                        # device ids are in base64 encoded, decode it
                        device_id = ProfileRagFormatter.decode_id(device_id)
                        then["device"] = device_id
                    parameters = then.get("parameters", None)
                    if parameters is not None:
                        for param in parameters:
                            uom_id = param.get("uom", None)
                            precision = param.get("precision", None)
                            value = param.get("value", None)
                            if precision is not None:
                                prec = int(precision)
                                if uom_id is not None and int(uom_id) != 25: 
                                    value = value * (10 ** prec)
                                    param["value"] = value 
                    out_routine['then'].append(then)
            elses = routine.get("else", None)
            if elses is not None and len (elses) > 0:
                for else_ in elses:
                    device_id = else_.get("device", None)
                    if device_id is not None:
                        device_id = self.get_device_id(device_id)
                        if device_id is None:
                            #remove this else from elses
                            continue
                        # device ids are in base64 encoded, decode it
                        device_id = ProfileRagFormatter.decode_id(device_id)
                        else_["device"] = device_id
                    parameters = else_.get("parameters", None)
                    if parameters is not None:
                        for param in parameters:
                            uom_id = param.get("uom", None)
                            precision = param.get("precision", None)
                            value = param.get("value", None)
                            if precision is not None:
                                prec = int(precision)
                                if uom_id is not None and int(uom_id) != 25: 
                                    value = value * (10 ** prec)
                                    param["value"] = value
                    out_routine['else'].append(else_)

        except Exception as e:
            print(f"Failed to process routine: {str(e)}")
            return None

        print( "****Routine after processing:") 
        print(json.dumps(out_routine, indent=4))
        response=self.nucore_api.create_routine(out_routine)
        return response

    
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
        # Use nucoreAPI to fetch properties
        if not device_id:
            raise NuCoreError("Device ID is empty.")
        # Decode base64 encoded device_id
        device_id = ProfileRagFormatter.decode_id(device_id)
        properties = self.nucore_api.get_properties(device_id)
        if properties is None:
            raise NuCoreError(f"Failed to get properties for device {device_id}.")
        return properties
    
    def get_device_name(self, device_id:str)-> str:
        """
        Get the name of a device by its ID.
        
        Args:
            device_id (str): The ID of the device to get the name for.
        
        Returns:
            str: The name of the device, or None if not found.
        """
        if not self.nodes:
            raise NuCoreError("No nodes loaded.")
        #device id is base64 encoded, decode it
        device_id = ProfileRagFormatter.decode_id(device_id)
        node = self.nodes.get(device_id, None)  # Return None if device_id not found
        return node.name if node.name else device_id

    def get_device_id(self, device_str:str)-> str:
        """
        Get the id of a device by a string. It searches id first, if not by name 
        
        Args:
            device_str (str): The string to identify the device (either ID or name).
        
        Returns:
            str: The ID of the device, or None if not found.
        """
        if not self.nodes:
            raise NuCoreError("No nodes loaded.")
        #device id is base64 encoded, decode it
        node = self.nodes.get(device_str, None)  # Return None if device_id not found
        if node:
            return node.address

        for node in self.nodes.values():
            if node.name == device_str:
                return node.address
        return None
 
    
    
    def subscribe_events(self, on_message_callback, on_connect_callback=None, on_disconnect_callback=None): 
        """
        Subscribe to device events using the nucore API.
        
        Args:
            on_message_callback (callable): Callback function to handle incoming messages.
            on_connect_callback (callable, optional): Callback function to handle connection events.
            on_disconnect_callback (callable, optional): Callback function to handle disconnection events.
        """
        try:
            threading.Thread(target=asyncio.run, args=(self.nucore_api.subscribe_events(
                on_message_callback=on_message_callback,
                on_connect_callback=on_connect_callback,
                on_disconnect_callback=on_disconnect_callback),)).start()
        except Exception as ex:
            print(f"Failed to subscribe to events: {str(ex)}")

    
    async def _on_device_event(self, message:dict):
        """
        Callback function to handle device events.
        What we are looking for are events that change device structure such as device added/removed, property added/removed, etc.
        :param event: The event data received.
        """
        if message is None or 'node' not in message or 'control' not in message:
            print(f"Received invalid message format {message}")
            return
        
        control = message['control']
        if control == "_3": #node updated event
            self.device_structure_changed = True # just to be on the safe side

    async def _on_connect_callback(self):
        """
        Callback function to handle connection established event.
        """
        self.is_subscribed = True
        self.device_structure_changed = True # just to be on the safe side

    async def _on_disconnect_callback(self):
        """
        Callback function to handle disconnection event.
        """
        self.is_subscribed = False

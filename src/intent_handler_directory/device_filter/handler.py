from __future__ import annotations
from typing import Any

from intent_handler import BaseIntentHandler
from time import sleep
from intent_handler.base import IntentHandlerResult
from nucore import Profile
import threading

import logging
import asyncio


from nucore import Profile
from nucore import Node
from nucore import NuCoreError
from rag import RAGFormatter, ProfileRagFormatter, MinimalRagFormatter


logger = logging.getLogger(__name__)

class PromptFormatTypes:
    DEVICE = "per-device"
    PROFILE = "shared-features"


def debug(msg):
    logger.debug(f"[PROFILE FORMAT ERROR] {msg}")

class DeviceFilterIntentHandler(BaseIntentHandler):

    def get_prompt_runtime_replacements(self, query, *, framework_context=None, route_result=None):
        self.nucore_interface._refresh_device_structure() # ensure we have the latest device structure before handling the intent   
        return {
            "<<device_database>>": self.nucore_interface.summary_rags
        }
    
    async def handle(self, query, *, route_result=None, framework_context:str=None, dependency_outputs:IntentHandlerResult | str | dict[str, Any] | None = None):
        messages = self.build_messages(
            query,
            framework_context=framework_context,
            route_result=route_result,
        )
        response = await self.call_llm(messages=messages, expect_json=True)
        return self.as_result(
            response,
            route_result=route_result,
            metadata={"stage": "device_filter"},
        )

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
        
    async def subscribe_events(self, on_message_callback, on_connect_callback=None, on_disconnect_callback=None): 
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

    
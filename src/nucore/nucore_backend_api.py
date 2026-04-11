#simple class to communicate with nucore backends such as eisy/iox

# Method 1: Using requests (recommended)
import xml.etree.ElementTree as ET
from abc import ABC, abstractmethod
from typing import Literal

from .nodedef import Property
from .uom import PREDEFINED_UOMS, UNKNOWN_UOM

class NuCoreBackendAPI(ABC):
    """
    Abstract base class for NuCore backend API implementations.
    This class defines the interface for interacting with nucore backends such as eisy/iox.
    Subclasses must implement all abstract methods.
    """
    
    def __init__(self, json_output: bool=True):
        """
        Initializes the NuCoreBackendAPI
        """
        self.json_output = json_output

    def _get_uom(self, uom):
        """
        checks to see if UOM is an integer and it belongs to a known UOM. 
        if not, it uses string to find the UOM_ID.
        Args:
            uom (str or int): The unit of measure to check.
        
        Returns:
            int: The UOM ID if found, otherwise None.
        """
        try:
            if isinstance(uom, int):
                # If uom is an integer, check if it is in the predefined UOMs
                uom = str(uom)
            if uom in PREDEFINED_UOMS.keys():
                return int(uom)
            else:
                for _, uom_entry in PREDEFINED_UOMS.items(): 
                    if uom_entry.label.upper() == uom.upper() or uom_entry.name.upper() == uom.upper():
                        return int(uom_entry.id)

                print(f"UOM {uom} is not a known UOM")
                return UNKNOWN_UOM 
        except ValueError:
            if isinstance(uom, str):
                if uom.upper() == "ENUM" or uom.upper() == "INDEX":
                    return 25 #index
                else:
                    for uom_id, uom_entry in PREDEFINED_UOMS.items():
                        if uom_entry.label.upper() == uom.upper() or uom_entry.name.upper() == uom.upper():
                            return int (uom_entry.id)

        return  UNKNOWN_UOM
    
    @abstractmethod
    def get_profiles(self):
        """Get profiles from the backend."""
        pass

    @abstractmethod
    def get_nodes(self):
        """Get nodes from the backend."""
        pass

    @abstractmethod
    def get_group_links(self):
        """Get group definitions/links/scenes from the backend."""
        pass

    @abstractmethod
    def get_properties(self, device_id:str)-> dict[str, Property]:
        """
        Get properties of a device by its ID.
        
        Args:
            device_id (str): The ID of the device to get properties for.
        
        Returns:
            dict[str, Property]: A dictionary of properties for the device.
        Raises:
            ValueError: If the device_id is empty or if the response cannot be parsed.
        """
        pass

    @abstractmethod
    def send_commands(self, commands:list):
        """
        Send commands to a device.

        Args:
            commands (list): A list of command dictionaries to send.
        
        Returns:
            str: The response from the server.

        NOTE: device ids are URL encoded. backend must decode them.
        
        Raises:
            ValueError: If the command format is invalid or if required fields are missing.
        """
        pass

    @abstractmethod
    def get_all_routines_summary(self):
        """
        Get all the runtime information for routines from the IoX device.
        :return: JSON response containing all routines or None if failure.
        """
        pass

    @abstractmethod
    def get_routine_summary(self, routine_id:str):
        """
        Get all the runtime information for a specific routine from the IoX device.
        :param routine_id: The ID of the program to retrieve.
        :return: JSON response containing the routine information or None if failure.
        """
        pass

    @abstractmethod
    def get_all_routines(self):
        """
        Get complete information for all routines from the IoX device including their logic, triggers, and actions. 
        :return: JSON response containing all routines or None if failure
        """
        pass
   
    @abstractmethod 
    def get_routine(self, routine_id:str):
        """
        Get complete information for a specific routine from the IoX device including its logic, triggers, and actions. 
        :param routine_id: The ID of the program to retrieve.
        :return: JSON response containing the routine information or None if failure
        """
        pass
    
    @abstractmethod
    def create_routine(self, program:dict):
        """
        Create a routine on the backend with the given program content.
        
        Args:
            program (dict): The program content to upload.
            
        Returns:
            str: The response from the backend for the uploaded program.
        """
        pass

    @abstractmethod
    def update_routine(self, program:dict):
        """
        Update a routine on the backend with the given program content. 
        Args:
            program (dict): The program content to update.
        Returns:
            response: The response object from the backend for the updated program. 
        """
        pass

    @abstractmethod
    def delete_routine(self, routine_id:str):
        """
        Delete a program/routine by its ID.
        :param routine_id: The ID of the program to delete.
        :return: The response from the server, or None if the routine_id is invalid.
        """
        pass

    @abstractmethod
    def routine_ops(self, routine_id:str, operation:Literal["runIf", "runThen", "runElse", "stop", "enable", "disable", "enableRunAtStartup", "disableRunAtStartup"]):
        """
        Perform an operation on a program.
        :param routine_id: The ID of the program to operate on.
        :param operation: The operation to perform (e.g., "run", "stop", "enable", "disable", etc.).
        :return: The response from the server, or None if the routine_id is invalid.
        """
        pass


    @abstractmethod
    async def subscribe_events(self, on_message_callback, on_connect_callback=None, on_disconnect_callback=None): 
        """
        Subscribe to events
        :param on_message_callback: function to call when an event is received
        :param on_connect_callback: function to call when connection is established
        :param on_disconnect_callback: function to call when connection is lost
        """
        pass

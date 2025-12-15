#simple class to communicate with nucore backends such as eisy/iox

# Method 1: Using requests (recommended)
import requests
import re
import xml.etree.ElementTree as ET
from abc import ABC, abstractmethod
from .nodedef import Property
from .uom import PREDEFINED_UOMS, UNKNOWN_UOM

class NuCoreBackendAPI(ABC):
    """
    Abstract base class for NuCore backend API implementations.
    This class defines the interface for interacting with nucore backends such as eisy/iox.
    Subclasses must implement all abstract methods.
    """
    
    def __init__(self):
        """
        Initializes the NuCoreBackendAPI
        """

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
        
        Raises:
            ValueError: If the command format is invalid or if required fields are missing.
        """
        pass

    @abstractmethod
    def upload_program(self, program:dict):
        """
        Upload a single program to the backend. 
        
        Args:
            program (dict): The program content to upload.
            
        Returns:
            str: The response from the backend for the uploaded program.
        """
        pass

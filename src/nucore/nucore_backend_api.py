#simple class to communicate with nucore backends such as eisy/iox

# Method 1: Using requests (recommended)
import requests
import re
import xml.etree.ElementTree as ET
from .nodedef import Property
from .uom import PREDEFINED_UOMS, UNKNOWN_UOM

class NuCoreBackendAPI:
    def __init__(self, base_url:str, username:str, password:str):
        """
        Initializes the NuCoreBackendAPI with the base URL and credentials.
        This class provides methods to interact with the nucore backend API, including fetching nodes, properties, and sending commands.
        This class can be replaced by anything else that implements the same methods.

        Args:
            base_url (str): The base URL of the nucore API.
            username (str): The username for authentication.
            password (str): The password for authentication.
        """
        if not base_url or not username or not password:
            raise ValueError("Base URL, username, and password must be provided")
        
        self.base_url = base_url.rstrip('/')  # Ensure base URL does not end with a slash
        self.username = username
        self.password = password

    def __get(self, path:str):
        try:
            url=f"{self.base_url}/{path}"
            # Method 1a: Using auth parameter (simplest)
            response = requests.get(
            url,
            auth=(self.username, self.password)
            )
            if response.status_code != 200:
                print (f"invalid url status code = {response.status_code}")
                return None
            return response
        except Exception as ex:
            print (f"failed connection {ex}")
            return None
    
    def __post(self, path:str, body:str, headers):
        try:
            url=f"{self.base_url}{path}"
            response = requests.post(url, auth=(self.username, self.password), data=body, headers=headers,  verify=False)
            if response.status_code != 200:
                print (f"invalid url status code = {response.status_code}")
                return None
            return response
        except Exception as ex:
            print (f"failed post: {ex}")
            return None
        
    def __get_uom(self, uom):
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
    
    def get_profiles(self):
        response = self.__get("/rest/profiles")
        if response == None:
            return None
        return response.json()
        #return json.dumps(response.json(), indent=2)

    def get_nodes(self):
        response = self.__get("/rest/nodes")
        if response == None:
            return None
        return response.text

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
        if not device_id:
            raise ValueError("Device ID cannot be empty")
        
        response = self.__get(f"/rest/nodes/{device_id}")
        if response == None:
            return None
        try:
            root = ET.fromstring(response.text)
            property_elems = root.findall(".//property")
            properties = {}
            for p_elem in property_elems:
                prop = Property(
                    id=p_elem.get("id"),
                    value=p_elem.get("value"),
                    formatted=p_elem.get("formatted"),
                    uom=p_elem.get("uom"),
                    prec=int(p_elem.get("prec")) if p_elem.get("prec") else None,
                    name=p_elem.get("name"),
                )
                properties[prop.id] = prop 
        except ET.ParseError as e:
            print(f"Error parsing XML response: {e}")
            return None
        except Exception as e:
            print(f"Error processing properties: {e}")
            return None

        return properties

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

        """"
        __BEGIN_NUCORE_COMMAND__
        {
        "device_id": "<DEVICE_ID>",
        "command_id": "<COMMAND_ID>",
        "command_params": [
            {
            "id": "<PARAM_ID>",
            "value": <VALUE>,
            "uom": "<UNIT>",
            "precision": <PRECISION>
            }
        ]
        }
        __END_NUCORE_COMMAND__
        """
        responses = []
        if not commands or len(commands) == 0:
            print("No commands to send")
            return None
        
        for command in commands:
            if not isinstance(command, dict):
                print(f"Invalid command format: {command}")
                continue

            device_id = command.get("device_id")
            if not device_id:
                raise ValueError("No device ID found in command")
            command_id = command.get("command_id")
            if not command_id:
                raise ValueError("No command ID found in command")
            command_params = command.get("command_params", [])
            
            # Construct the url: /rest/nodes/<device_id>/cmd/<command_id>/<params[value] 

            url = f"/rest/nodes/{device_id}/cmd/{command_id}"
            if len(command_params) == 1:
                param = command_params[0]
                id = param.get("id", None)
                uom = param.get("uom", None)
                value = param.get("value", None)
                if value is not None:
                    if id is None or id == '' or id == "n/a" or id == "N/A":
                        url += f"/{value}"
                        if uom is not None:
                            url += f"/{self.__get_uom(uom)}"
                    else:
                        url += f"?{id}"
                        if uom is not None:
                            url += f".{self.__get_uom(uom)}"
                        url += f"={value}"
            elif len(command_params) > 1:
                unamed_params = [p for p in command_params if not p.get("id")]
                named_params = [p for p in command_params if p.get("id")]

                # Add all parameters to the url
                for param in unamed_params:
                    value = param.get("value", None)
                    if value is None:
                        print(f"No value found for unnamed parameter in command {command_id}")
                        continue
                    url += f"/{value}"
                    uom = param.get("uom", None)
                    if uom is not None:
                        url += f"/{self.__get_uom(uom)}"

                no_name_param1 = False
                if len(named_params) > 0:
                    i = 0
                    # Add named parameters to the url
                    for param in named_params:
                        the_rest_of_the_url = ""
                        id = param.get("id", None)
                        value = param.get("value", None)
                        if value is None:
                            print(f"No value found for named parameter {id} in command {command_id}")
                            continue
                        if id is None or id == '' or id == "n/a" or id == "N/A":
                            if i == 0:
                                no_name_param1 = True
                                url+= f"/{value}/"
                                i+= 1
                                continue

                            print(f"No id found for named parameter in command {command_id}")
                            continue

                        the_rest_of_the_url = f"?{id}" if i == 0 else f"?{id}" if no_name_param1 else f"&{id}"
                        uom = param.get("uom", None)
                        if uom is not None:
                            the_rest_of_the_url += f".{self.__get_uom(uom)}"
                        the_rest_of_the_url += f"={value}"
                        url += the_rest_of_the_url
                        i += 1
            responses.append(self.__get(url))
        print(responses)
        return responses

    
    def get_d2d_key(self):
        """
        Sends a SOAP request for GetAllD2D, prints the full response, and returns the key value.

        Returns:
            str: The key value from the response (e.g., '07FF2D.BBC3F1'), or None if not found.

        Raises:
            requests.RequestException: If the SOAP request fails.
        """
        # SOAP request envelope (exact match to your provided envelope)
        soap_request = '''<?xml version="1.0" encoding="utf-8"?>
        <s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">
            <s:Body>
                <u:GetAllD2D xmlns:u="urn:udi-com:service:X_IoX_Service:1"></u:GetAllD2D>
            </s:Body>
        </s:Envelope>'''

        # Headers matching the provided SOAP request
        headers = {
            'Content-Type': 'text/xml; charset=utf-8',
            'SOAPACTION': 'urn:udi-com:service:X_IoX_Service:1#GetAllD2D',  # Exact match
            'Connection': 'close'
        #    'Host': '192.168.0.105:8443'
        }

        # Add Authorization header if provided
        #if authorization:
        #    headers['Authorization'] = authorization

        try:

            # Send the SOAP request
            response = self.__post('/services', body=soap_request, headers=headers)

            xml_response = response.text

            # Print the full XML response
            #print("Full SOAP Response:")
            #print(xml_response)
            #print("-" * 50)  # Separator for readability

            # Extract the key using regex since XML is malformed
            key_match = re.search(r'<key>(.*?)</key>', xml_response)
            if key_match:
                return key_match.group(1)  # Return the key value, e.g., '07FF2D.BBC3F1'
            else:
                return None  # Key not found in response

        except requests.RequestException as e:
            raise Exception(f"SOAP request failed: {str(e)}")

    def upload_programs(self, programs:dict):
        if not programs:
            return False
        key=self.get_d2d_key()
        if not key:
            print ("couldn't get d2d key")
            return False
        headers = {
            'Content-Type': 'text/xml; charset=utf-8',
            'Connection': 'close'
        }

        for program_file_name, program_content in programs.items():
            try:
                self.__post(f'/program/upload/{program_file_name}?key={key}', body=program_content, headers=headers)
            except Exception as ex:
                print (ex)
                return False
        return True

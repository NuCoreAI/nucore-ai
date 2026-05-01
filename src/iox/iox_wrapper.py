import json
import requests, xml.etree.ElementTree as ET
import sys
import os
import websockets, base64

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from nucore.nucore_interface import NuCoreInterface, PromptFormatTypes
from nucore.nodedef import Property
from nucore.node import Node
from nucore.uom import get_uom_by_id, PREDEFINED_UOMS, UNKNOWN_UOM
from nucore.nucore_error import NuCoreError
from rag import ProfileRagFormatter, MinimalRagFormatter
import xml.etree.ElementTree as ET
from typing import Literal
from utils import get_logger

logger = get_logger(__name__)
def debug(msg):
    logger.debug(f"[PROFILE FORMAT ERROR] {msg}")


import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class IoXWrapper(NuCoreInterface):
    ''' 
        Wrapper class for ISY interaction 
        It only works if the customer gives explicit permission for the plugin to access IoX directly
    '''

    def __init__(self, json_output:bool, prompt_format_type:str, poly=None, base_url=None, username=None, password=None):
        """
        Initializes the IoXWrapper instance.
        Either use poly to get ISY info or provide base_url, username, and password directly.
        Args:
            poly: The poly interface instance (optional).
            base_url (str): The base URL of the ISY device (optional).
            username (str): The username for ISY authentication (optional).
            password (str): The password for ISY authentication (optional).
        """
        super().__init__(json_output, prompt_format_type)  # Initialize parent with no parameters
        if poly:
            # import only in case we are running in polglot context since 
            # udi_interface redirects standard input/output to polyglot LOGGER
            from udi_interface import udi_interface, unload_interface
            from udi_interface import LOGGER
            self.poly = poly
            self.poly.subscribe(self.poly.ISY, self.__info__)
            message = {'getIsyInfo': {}}
            self.poly.send(message, 'system')
        elif base_url and username and password:
            self.base_url = base_url.rstrip('/')
            self.username= username
            self.password= password
        else:
            logger.error("Either poly or base_url, username, and password must be provided")
            raise ValueError("Either poly or base_url, username, and password must be provided")
        
        self.unauthorized = False

    def __info__(self, info):
        if info is not None:
            isy_ip = info['isy_ip_address']
            isy_port = info['isy_port']
            if 'isy_https' in info:
                isy_https = info['isy_https'] == 1
            else:
                isy_https = False
            self.base_url = f"{'https' if isy_https else 'http'}://{isy_ip}:{isy_port}"
            self.username = info['isy_username']
            self.password = info['isy_password']
        else:
            self.unauthorized = True
    
    def delete(self, path:str):
        try:
            path = path if path.startswith("/") else f"/{path}"
            url=f"{self.base_url}{path}" 
            # Method 1a: Using auth parameter (simplest)
            response = requests.delete(
            url,
            auth=(self.username, self.password),
            verify=False
            )
            if response.status_code != 200:
                logger.error(f"invalid url status code = {response.status_code}")
            return response
        except Exception as ex:
            logger.error (f"failed connection {ex}")
            return None

    def get(self, path:str):
        try:
            path = path if path.startswith("/") else f"/{path}"
            url=f"{self.base_url}{path}" 
            # Method 1a: Using auth parameter (simplest)
            response = requests.get(
            url,
            auth=(self.username, self.password),
            verify=False
            )
            if response.status_code != 200:
                logger.error(f"invalid url status code = {response.status_code}")
            return response
        except Exception as ex:
            logger.error(f"failed connection {ex}")
            return None
    
    def put(self, path:str, body:str, headers):
        try:
            url=f"{self.base_url}{path}"
            response = requests.put(url, auth=(self.username, self.password), data=body, headers=headers,  verify=False)
            if response.status_code != 200:
                logger.error(f"invalid url status code = {response.status_code}")
            return response
        except Exception as ex:
            logger.error(f"failed put: {ex}")
            return None

    def post(self, path:str, body:str, headers):
        try:
            url=f"{self.base_url}{path}"
            response = requests.post(url, auth=(self.username, self.password), data=body, headers=headers,  verify=False)
            if response.status_code != 200:
                logger.error(f"invalid url status code = {response.status_code}")
            return response
        except Exception as ex:
            logger.error(f"failed post: {ex}")
            return None
        
    def get_profiles(self):
        """
        Get all profiles from the IoX device.
        :return: JSON response containing all profiles.
        """
        response = self.get("/rest/profiles")
        if response == None or response.status_code != 200:
            return None
        return response.json()

    def get_nodes(self):
        """
        Get all nodes from the IoX device.
        :return: XML response containing all nodes.
        """
        response = self.get("/rest/nodes")
        if response == None or response.status_code != 200:
            return None
        return response.text

    def get_group_links(self):
        """
        Get all groups/links/scenes from the IoX device.
        :return: XML response containing all groups/links/scenes.
        """
        response = self.get("/api/groups")
        if response == None or response.status_code != 200:
            return None
        try:
            return response.json()
        except json.JSONDecodeError as e:
            logger.error(f"Error parsing JSON response for group links: {e}")
            return None

    async def get_properties(self, device_id:str)-> dict[str, Property]:
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
            logger.error("Device ID is empty.")
            raise ValueError("Device ID is empty.")
        
        device_id = ProfileRagFormatter.decode_id(device_id)
        response = self.get(f"/rest/nodes/{device_id}")
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
            logger.error(f"Error parsing XML response: {e}")
            return None
        except Exception as e:
            logger.error(f"Error processing properties: {e}")
            return None

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
            logger.error("No nodes loaded.")
            raise NuCoreError("No nodes loaded.")
        #device id is base64 encoded, decode it
        device_id = ProfileRagFormatter.decode_id(device_id)
        node = self.nodes.get(device_id, None)  # Return None if device_id not found
        if not node:
            node = self.groups.get(device_id, None)
        if not node:
            node = self.folders.get(device_id, None)
        if not node:
            return None
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
            logger.error("No nodes loaded.")
            raise NuCoreError("No nodes loaded.")
        #device id is base64 encoded, decode it
        node = self.nodes.get(device_str, None)  # Return None if device_id not found
        if node:
            return node.address

        for node in self.nodes.values():
            if node.name == device_str:
                return node.address
        logger.error(f"Device not found: {device_str}")
        return None
    
    async def send_commands(self, commands:list):
        for cmd in commands:
            if "device" in cmd:
                #device ids are in base64 encoded, decode it
                device_id = cmd["device"]
                cmd["device"] = ProfileRagFormatter.decode_id(device_id)
        response = await self._send_commands(commands)
        if response is None:
            raise NuCoreError("Failed to send commands.")
        return response

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

                logger.error(f"UOM {uom} is not a known UOM")
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
    
    async def _send_commands(self, commands:list):
        """
        Send commands to a device (IoX-specific implementation).
        This is a simplified version - extend as needed.
        
        Args:
            commands (list): A list of command dictionaries to send.
        
        Returns:
            list: List of responses from the server.
        """
        responses = []
        if not commands or len(commands) == 0:
            logger.warning("No commands to send")
            return None

        try:
            if isinstance(commands, list) and 'commands' in commands[0]:
                commands = commands[0].get("commands", commands)
            elif isinstance(commands[0], list):
                commands = commands[0] 
        except Exception as ex:
            logger.error(f"Error processing commands: {ex}")
            pass
        
        for command in commands:
            if not isinstance(command, dict):
                logger.error(f"Invalid command format: {command}")
                continue

            device_id = command.get("device") or command.get("device_id")
            if not device_id:
                raise ValueError("No device ID found in command")
            command_id = command.get("command") or command.get("command_id")
            if not command_id:
                raise ValueError("No command ID found in command")
            command_params = command.get("command_params", []) or command.get("parameters", [])
            
            # Construct the url: /rest/nodes/<device_id>/cmd/<command_id>/<params[value]>
            url = f"/rest/nodes/{device_id}/cmd/{command_id}"
            if len(command_params) == 1:
                param = command_params[0]
                id = param.get("id", None) or param.get("name", None)
                uom = param.get("uom", None)
                value = param.get("value", None)
                if value is not None:
                    if id is None or id == '' or id == "n/a" or id == "N/A":
                        url += f"/{value}"
                        if uom is not None and uom != '':
                            url += f"/{self._get_uom(uom)}"
                    else:
                        url += f"?{id}"
                        if uom is not None and uom != '':
                            url += f".{self._get_uom(uom)}"
                        url += f"={value}"
            elif len(command_params) > 1:
                unamed_params = [p for p in command_params if not (p.get("id") or p.get("name"))]
                named_params = [p for p in command_params if (p.get("id") or p.get("name"))]

                for param in unamed_params:
                    value = param.get("value", None)
                    if value is None:
                        logger.error(f"No value found for unnamed parameter in command {command_id}")
                        continue
                    url += f"/{value}"
                    uom = param.get("uom", None)
                    if uom is not None and uom != '':
                        url += f"/{self._get_uom(uom)}"

                no_name_param1 = False
                if len(named_params) > 0:
                    i = 0
                    for param in named_params:
                        the_rest_of_the_url = ""
                        id = param.get("id", None) or param.get("name", None)
                        value = param.get("value", None)
                        if value is None:
                            logger.error(f"No value found for named parameter {id} in command {command_id}")
                            continue
                        if id is None or id == '' or id == "n/a" or id == "N/A":
                            if i == 0:
                                no_name_param1 = True
                                url+= f"/{value}/"
                                i+= 1
                                continue

                            logger.error(f"No id found for named parameter in command {command_id}")
                            continue

                        the_rest_of_the_url = f"?{id}" if i == 0 else f"?{id}" if no_name_param1 else f"&{id}"
                        uom = param.get("uom", None)
                        if uom is not None and uom != '':
                            the_rest_of_the_url += f".{self._get_uom(uom)}"
                        the_rest_of_the_url += f"={value}"
                        url += the_rest_of_the_url
                        i += 1
            responses.append(self.get(url))
        return responses
    
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
                            logger.error(f"Device not found for else condition: {else_}")
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
            logger.error(f"Failed to process routine: {str(e)}")
            return None

        logger.info("****Routine after processing:") 
        logger.info(json.dumps(out_routine, indent=4))
        response=self._create_routine(out_routine)
        return response

    def _create_routine(self, program:dict):
        if not program:
            return False
        response=None
        try:
            program_content = {
                'routine': program
            }
            headers = {
                "Content-Type": "application/json"
            }
            response = self.put(f'/api/ai/trigger', body=json.dumps(program_content), headers=headers)
        except Exception as ex:
            logger.error(f"Error creating routine: {ex}")
        
        return response

    
    async def get_all_routines_summary(self):
        """
        Get all the runtime information for routines from the IoX device.
        :return: JSON response containing all routines.
        """
        response = self.get("/api/ai/programs")
        if response == None or response.status_code != 200:
            return response if response else None
        return response.json()['data']

    async def get_routine_summary(self, program_id:int):
        """
        Get all the runtime information for a specific routine from the IoX device.
        :param program_id: The ID of the program to retrieve.
        :return: JSON response containing the routine information.
        WARNING: it returns all the folders too.
        """
        if program_id is None or program_id == '':
            logger.error("Program ID cannot be empty")
            return None
        #if it's a hex string, convert it to int since the API expects int ids
        if not isinstance(program_id, int):
            try:            
                program_id = int(program_id)
            except ValueError:
                if isinstance(program_id, str):
                    try:
                        program_id = int(program_id)
                    except ValueError:
                        #probaby hex, convert using hex
                        try:
                            program_id = int(program_id, 16)
                        except ValueError:
                            logger.error(f"Invalid program ID format: {program_id}. It should be an integer or a hex string.")
                            return None

        response = self.get(f"/api/ai/program/{program_id}")
        if response == None or response.status_code != 200:
            return response if response else None
        try:
            return response.json()['data']
        except Exception as ex:
            logger.error(f"Error retrieving routine summary: {ex}")
            return None

    async def get_all_routines(self):
        """
        Get complete information for all routines from the IoX device including their logic, triggers, and actions. 
        :return: JSON response containing all routines.
        """
        response = self.get("/api/ai/triggers")
        if response == None or response.status_code != 200:
            return response if response else None
        try:
            return response.json()['data']
        except Exception as ex:
            logger.error(f"Error retrieving all routines: {ex}")
            return None
    
    async def get_routine(self, program_id:str):
        """
        Get complete information for a specific routine from the IoX device including its logic, triggers, and actions. 
        :param program_id: The ID of the program to retrieve.
        :return: JSON response containing the routine information.
        """
        response = self.get(f"/api/ai/trigger/{program_id}")
        if response == None or response.status_code != 200:
            return response if response else None
        try:
            return response.json()['data']
        except Exception as ex:
            logger.error(f"Error retrieving routine: {ex}")
            return None

    def update_routine(self, program:dict):
        if not program:
            return False
        response=None
        try:
            program_content = {
                'routine': program
            }
            headers = {
                "Content-Type": "application/json"
            }
            response = self.post(f'/api/ai/trigger', body=json.dumps(program_content), headers=headers)
        except Exception as ex:
            logger.error(f"Error updating routine: {ex}")
        
        return response 

    def delete_routine(self, program_id:str):
        """
        Delete a program/routine by its ID.
        :param program_id: The ID of the program to delete.
        :return: The response from the server, or False if the program_id is invalid.
        """
        if not program_id:
            return None
        try:
            response = self.delete(f'/api/ai/trigger/{program_id}')
        except Exception as ex:
            logger.error(f"Error deleting routine: {ex}")
        
        return response 

    async def routine_ops(self, routine_id:int, operation:Literal["runIf", "runThen", "runElse", "stop", "enable", "disable", "enableRunAtStartup", "disableRunAtStartup"]):
        """
        Perform an operation on a program.
        :param routine_id: The ID of the program to operate on.
        :param operation: The operation to perform (e.g., "run", "stop", "enable", "disable", etc.).
        :return: The response from the server, or False if the program_id is invalid.
        """
        if not routine_id:
            return None
        if operation not in ["delete", "runIf", "runThen", "runElse", "stop", "enable", "disable", "enableRunAtStartup", "disableRunAtStartup"]:
            logger.error(f"Invalid operation: {operation}")
            return None
        try:
            if operation == "delete":
                response = self.delete(f'/api/ai/trigger/{routine_id}')
            else:
                if isinstance(routine_id, str):
                    try:
                        routine_id = int(routine_id)
                        #convert it to 4 digit hex string without 0x prefix since that's what the API expects
                        routine_id = format(routine_id, '04x')
                    except ValueError:
                        #already in hex
                        pass
                response = self.get(f'/rest/programs/{routine_id}/{operation}')
        except Exception as ex:
            logger.error(f"Error performing routine operation: {ex}")
        
        return response
    
    async def _subscribe_events(self, on_message_callback, on_connect_callback=None, on_disconnect_callback=None): 
        """
        Subscribe to events
        :param on_message_callback: function to call when an event is received
        :param on_connect_callback: function to call when connection is established
        :param on_disconnect_callback: function to call when connection is lost
        All callback functions should be async
        :return: True if subscription is successful, False otherwise
        The format for event data is a dictionary of the following structure:
        {
            'seqnum': str or None,
            'sid': str or None,
            'timestamp': str or None,
            'control': str,
            'action': {
                'value': str,
                'uom': str or None,
                'prec': str or None
            },
            'node': str,
            'fmtAct': str,
            'fmtName': str
        }
        """

        try:
            import ssl
            if self.base_url.startswith("https"):
                ws_url = self.base_url.replace("https", "wss") + "/rest/subscribe"
                ssl_context= ssl.create_default_context()
                ssl_context.check_hostname = False
                ssl_context.verify_mode = ssl.CERT_NONE
            else:
                ws_url = self.base_url.replace("http", "ws") + "/rest/subscribe"
                ssl_context=None
            #make base64 authorization header
            credentials = f"{self.username}:{self.password}"
            encoded_credentials = base64.b64encode(credentials.encode()).decode()
            headers = {
                "Authorization": f"Basic {encoded_credentials}"
            }
            async with websockets.connect(ws_url, ssl=ssl_context, additional_headers=headers) as websocket:
                if on_connect_callback:
                    await on_connect_callback()
                try:
                    async for message in websocket:
                        if on_message_callback:
                            try:
                                #parse the xml message
                                root = ET.fromstring(message)
                                control = root.find('control')
                                action = root.find('action')
                                node = root.find('node')
                                fmtAct = root.find('fmtAct')
                                fmtName = root.find('fmtName')
                                eventInfo = root.find('eventInfo')
                                event_data = {
                                    'seqnum': root.get('seqnum', None ),
                                    'sid': root.get('sid', None),
                                    'timestamp': root.get('timestamp', None),
                                    'control': control.text if control is not None else None,
                                    'action': {
                                        'value': action.text if action is not None else None,
                                        'uom': action.get('uom', None) if action is not None else None,
                                        'prec': action.get('prec', None) if action is not None else None
                                    },
                                    'node': node.text if node is not None else None,
                                    'fmtAct': fmtAct.text if fmtAct is not None else None,
                                    'fmtName': fmtName.text if fmtName is not None else None,
                                    'eventInfo': ET.tostring(eventInfo) if eventInfo is not None else None
                                }
                                if on_message_callback:
                                    await on_message_callback(event_data)
                            except Exception as ex:
                                logger.error(f"Failed to process incoming message: {str(ex)}: {message}")
                                continue
                #except websockets.ConnectionClosed:
                except websockets.ConnectionClosed :
                    logger.error("WebSocket connection closed")
                    if on_disconnect_callback:
                        await on_disconnect_callback()
        except Exception as ex:
            logger.error(f"Failed to subscribe to events: {str(ex)}")
            return False
        return True

    async def _load(self, **kwargs):
        
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

        self._load_devices(include_profiles=include_profiles, profile_path=kwargs.get("profile_path"), nodes_path=kwargs.get("nodes_path"))
        self.rags= self._format_nodes() 
        if not self.rags:
            logger.warning(f"No RAG documents found for node {self.nuCore.url}. Skipping.")
        self.summary_rags = self.format_nodes_summary(False)
        return True

    # To have the latest state, we need to load devices only
    def _load_devices(self, include_profiles=True, profile_path:str=None, nodes_path:str=None, groups_path:str=None):
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
            else:
                response = self.get_profiles()
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
        
        response = self.get_nodes()
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
        
        response = self.get_group_links()
        if response is None:
            raise NuCoreError("Failed to fetch group links from URL.")
        return Node.load_from_json(response)

    def _formatter_format_nodes(self, device_rag_formatter:ProfileRagFormatter=None):
        """
        Format nodes for fine tuning or other purposes 
        :return: List of formatted nodes.
        """
        if not self.nodes or device_rag_formatter is None:
            raise NuCoreError("No nodes loaded.")
        
        if self.formatter_type == PromptFormatTypes.PROFILE:
            return device_rag_formatter.format(profiles=self.runtime_profiles, nodes=self.nodes, groups=self.groups, folders=self.folders ) 
        if self.formatter_type == PromptFormatTypes.DEVICE:
            return device_rag_formatter.format(nodes=self.nodes, groups=self.groups, folders=self.folders ) 
        
        debug(f"Unknown formatter type: {self.formatter_type}, defaulting to per-device format.")
        return device_rag_formatter.format(nodes=self.nodes, groups=self.groups, folders=self.folders)
    
    def _format_nodes(self):
        """
        Format nodes for fine tuning or other purposes 
        :return: List of formatted nodes.
        """
        if not self.nodes:
            raise NuCoreError("No nodes loaded.")
        device_rag_formatter = ProfileRagFormatter(json_output=self.json_output)
        return self._formatter_format_nodes(device_rag_formatter)

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
        device_rag_formatter = MinimalRagFormatter(json_output=self.json_output, condense=condense_profiles)
        return self._formatter_format_nodes(device_rag_formatter)
    
    async def _load_routines(self):
        try:
            all_routines = await self.get_all_routines()

            # now go thorugh the list and create both the full and condensed versions of the routines database 
            # codensed version is used for filtering using device names, while the full version is sent to intent handlers for full processing
            for r in all_routines:
                routine = r.get("routine", {})
                routine_id = routine.get("id", "")
                if not routine_id:
                    continue
                condensed_routine = {
                    "id": routine_id, 
                    "name": routine.get("name"),
                    "comment": routine.get("comment"),
                    "device_names": self._get_device_name_list_from_routine(routine) 
                }

                if "invalid" in r:
                    routine["invalid"]=r.get("invalid", False)
                    routine["invalid_reason"]=r.get("error", "")
                    condensed_routine["invalid"]=r.get("invalid", False)
                    condensed_routine["invalid_reason"]=r.get("error", "")
                self.all_routines[routine_id] = routine
                self.condensed_routines.append(condensed_routine)

            self.routines_changed = False
        except Exception as ex:
            pass

    def _get_device_name_list_from_routine(self, routine: dict) -> list[str]: 
        if routine is None:
            return []

        #first check the if section:        
        if_section: list[dict] = routine.get("if", [])
        then_section: list[dict] = routine.get("then", [])
        else_section: list[dict] = routine.get("else", [])
        device_id_list = []
        for condition in if_section:
            if "device" in condition:
                device = condition.get("device", None)
                if device:                    
                    device_id_list.append(device)
        
        for action in then_section:
            if "device" in action:
                device = action.get("device", None)
                if device:
                    device_id_list.append(device)
   
        for action in else_section:
            if "device" in action:
                device = action.get("device", None)
                if device:
                    device_id_list.append(device)

        device_names: list[str] = []
        for device_id in device_id_list:
            try:
                device_name = self.get_device_name(device_id)
                if device_name:
                    device_names.append(device_name)
            except Exception as ex:
                pass

        return device_names

    

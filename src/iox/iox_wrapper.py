import json
import requests, re, xml.etree.ElementTree as ET
import sys
import os
import asyncio, websockets, base64
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from nucore.nucore_backend_api import NuCoreBackendAPI
from nucore.nodedef import Property
import xml.etree.ElementTree as ET

class IoXWrapper(NuCoreBackendAPI):
    ''' 
        Wrapper class for ISY interaction 
        It only works if the customer gives explicit permission for the plugin to access IoX directly
    '''

    def __init__(self, poly=None, base_url=None, username=None, password=None):
        """
        Initializes the IoXWrapper instance.
        Either use poly to get ISY info or provide base_url, username, and password directly.
        Args:
            poly: The poly interface instance (optional).
            base_url (str): The base URL of the ISY device (optional).
            username (str): The username for ISY authentication (optional).
            password (str): The password for ISY authentication (optional).
        """
        super().__init__()  # Initialize parent with no parameters
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
                print (f"invalid url status code = {response.status_code}")
                return None
            return response
        except Exception as ex:
            print (f"failed connection {ex}")
            return None
    
    def put(self, path:str, body:str, headers):
        try:
            url=f"{self.base_url}{path}"
            response = requests.put(url, auth=(self.username, self.password), data=body, headers=headers,  verify=False)
            if response.status_code != 200:
                print (f"invalid url status code = {response.status_code}")
                return None
            return response
        except Exception as ex:
            print (f"failed put: {ex}")
            return None

    def post(self, path:str, body:str, headers):
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
        
    def get_profiles(self):
        """
        Get all profiles from the IoX device.
        :return: JSON response containing all profiles.
        """
        response = self.get("/rest/profiles")
        if response == None:
            return None
        return response.json()

    def get_nodes(self):
        """
        Get all nodes from the IoX device.
        :return: XML response containing all nodes.
        """
        response = self.get("/rest/nodes")
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
            print(f"Error parsing XML response: {e}")
            return None
        except Exception as e:
            print(f"Error processing properties: {e}")
            return None

        return properties
    
    def send_commands(self, commands:list):
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
            print("No commands to send")
            return None

        try:
            if isinstance(commands, list) and 'commands' in commands[0]:
                commands = commands[0].get("commands", commands)
            elif isinstance(commands[0], list):
                commands = commands[0] 
        except Exception as ex:
            pass
        
        for command in commands:
            if not isinstance(command, dict):
                print(f"Invalid command format: {command}")
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
                        print(f"No value found for unnamed parameter in command {command_id}")
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
                        if uom is not None and uom != '':
                            the_rest_of_the_url += f".{self._get_uom(uom)}"
                        the_rest_of_the_url += f"={value}"
                        url += the_rest_of_the_url
                        i += 1
            responses.append(self.get(url))
        return responses

    def upload_programs(self, programs:list):
        if not programs:
            return False

        for program_content in programs:
            try:
                self.put(f'/api/ai/trigger', body=program_content, headers=None)
            except Exception as ex:
                print (ex)
                return False
        return True

    async def subscribe_events(self, on_message_callback, on_connect_callback=None, on_disconnect_callback=None): 
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
                                await on_message_callback(event_data)
                            except Exception as ex:
                                print(f"Failed to process incoming message: {str(ex)}: {message}")
                                continue
                #except websockets.ConnectionClosed:
                except websockets.ConnectionClosed :
                    print("WebSocket connection closed")
                    if on_disconnect_callback:
                        await on_disconnect_callback()
        except Exception as ex:
            print(f"Failed to subscribe to events: {str(ex)}")
            return False
        return True
    

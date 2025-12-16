# This class manages nodes/profiles/programs in the nucore platform


import json
import logging
import xml.etree.ElementTree as ET


from nucore import Profile
from nucore import Property, Node
from nucore import get_uom_by_id
from nucore import NuCoreBackendAPI 
from nucore import NuCoreError
from config import AIConfig


logger = logging.getLogger(__name__)
config = AIConfig()



def debug(msg):
    logger.debug(f"[PROFILE FORMAT ERROR] {msg}")


class NuCore:
    """Class to handle nucore backend operations such as loading profiles and nodes."""
    def __init__(self, collection_path, collection_name:str, nucore_api:NuCoreBackendAPI, embedder_url:str=None, reranker_url:str=None):
        """
        Initialize the NuCore instance with backend API and RAG processor. 
        :param collection_path: The path to the collection file. This is used to store all the embeddings. (mandatory)
        :param collection_name: The name of the collection to be used. This is used to store all the embeddings. (mandatory)
        :param nucore_api: An instance of NuCoreBackendAPI to interact with the backend. (mandatory)
        :param reranker_url (str): The URL of the reranker service. If not provided, reranking will not be performed.
        :param static_docs_path (str): The path to the static information directory. If not provided, static information will not be included.
        
        Note: Make sure that the collection_path and collection_name are set correctly.
        You will need to call load() after you are ready to use this object
        """
        if not collection_name or not collection_path:
            raise NuCoreError("collection_name/path are mandatory parameters.")

        self.name = collection_name     
        self.nucore_api = nucore_api
        self.nodes = [] 
        from rag import RAGProcessor
        self.rag_processor = RAGProcessor(collection_path, collection_name, embedder_url=embedder_url, reranker_url=reranker_url)
        self.profile = Profile(timestamp="", families=[])

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
    
    def format_nodes(self):
        """
        Format nodes for fine tuning or other purposes 
        :return: List of formatted nodes.
        """
        if not self.nodes:
            raise NuCoreError("No nodes loaded.")
        from rag import DeviceRagFormatter
        device_rag_formatter = DeviceRagFormatter(indent_str=" ", prefix="-")
        return device_rag_formatter.format(nodes=self.nodes) 
    
    def format_tools(self):
        """
        Format tools for fine tuning or other purposes.
        :return: List of formatted tools.
        """
        if not self.profile:
            raise NuCoreError("No profile loaded.")
        from rag import ToolsRAGFormatter
        tools_rag_formatter = ToolsRAGFormatter(indent_str=" ", prefix="-")
        return tools_rag_formatter.format(tools_path=config.getToolsFile())
    
    def format_static_info(self, path:str):
        """
        Format static information for fine tuning or other purposes.
        :param path: Path to the static information directory.
        :return: List of formatted static information to be used for embeddings.
        """
        from rag import StaticInfoRAGFormatter 
        static_info_rag_formatter = StaticInfoRAGFormatter(indent_str=" ", prefix="-")
        return static_info_rag_formatter.format(static_info_path=path)

    def load_rag_docs(self, **kwargs):
        """
        Load RAG documents from the specified nodes and profile.
        :param kwargs: Optional parameters for formatting.
        - embed: If True, embed the RAG documents.
        - include_rag_docs: If True, include RAG documents in the output.
        - tools: If True, include tools in the RAG documents.
        - static_info: If True, include static information in the RAG documents.
        - dump: If True, dump the processed RAG documents to a file.
        :raises NuCoreError: If no nodes or profile are loaded.
        :return: Processed RAG documents.
        """
        device_rag_docs = self.format_nodes()
        embed = kwargs.get("embed", False) 
        all_docs = device_rag_docs
        tools = kwargs.get("tools", False)
        static_path = kwargs.get("static_docs_path", False)
        dump = kwargs.get("dump", False)
        if tools: 
            tools_rag_docs = self.format_tools()
            if tools_rag_docs:
                all_docs += tools_rag_docs

        if static_path: 
            static_info_rag_docs = self.format_static_info(static_path)
            if static_info_rag_docs:
                all_docs += static_info_rag_docs

        processed_docs = all_docs
        if embed:
            processed_docs = self.rag_processor.process(all_docs)
        if dump:
            self.rag_processor.dump()
        return processed_docs


    def load(self, **kwargs):
        
        """
        Load devices and profiles from the specified paths or URL.
        :param kwargs: Optional parameters for loading.
        - profile_path: Path to the profile file. If not provided, will use the configured URL.
        - nodes_path: Path to the nodes XML file. If not provided, will use the configured URL.
        - include_rag_docs: If True, include RAG documents in the output.
        - dump: If True, dump the processed RAG documents to a file.
        :return: Loaded devices and profiles.
        :raises NuCoreError: If no valid profile or nodes source is provided.
        :raises NuCoreError: If the RAG processor is not initialized.
        """
        
        include_rag_docs = kwargs.get("include_rag_docs", False)
        dump = kwargs.get("dump", False)
        static_docs_path = kwargs.get("static_docs_path", None)
        embed = kwargs.get("embed", False)

        
        rc = self.load_devices(profile_path=kwargs.get("profile_path"), nodes_path=kwargs.get("nodes_path"))
        if include_rag_docs:
            rc = self.load_rag_docs(dump=dump, static_docs_path=static_docs_path, embed=embed)
        return rc

    # To have the latest state, we need to load devices only
    def load_devices(self, include_profiles=True, profile_path:str=None, nodes_path:str=None):
        if include_profiles:
            if not self.__load_profile__(profile_path):
                return None
        
        root = self.__load_nodes__(nodes_path)
        if root == None:
            return None
        
        self.nodes = self.profile.map_nodes(root) 

        return self.nodes
        
    def query(self, query_text:str, num_results=5, rerank=True):
        """
        Query the loaded nodes and profiles using the RAG processor.
        :param query_text: The query string to search for.
        :param num_results: The number of results to return. Default is 5.
        :param rerank: Whether to rerank the results based on relevance. Default is True.
        :return: RAGData object containing the results.
        :raises NuCoreError: If the RAG processor is not initialized.
        :raises NuCoreError: If the query fails. 
        """
        if not self.rag_processor:
            raise NuCoreError("RAG processor is not initialized.")
        
        return self.rag_processor.query(query_text, num_results, rerank=rerank)

    async def send_commands(self, commands:list):
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
        
        thens = routine.get("then", None)
        if thens is not None and len (thens) > 0:
            for then in thens:
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
        elses = routine.get("else", None)
        if elses is not None and len (elses) > 0:
            for else_ in elses:
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

        response=self.nucore_api.upload_program(routine)
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

        node = self.nodes.get(device_id, None)  # Return None if device_id not found
        return node.name if node.name else device_id

    def __str__(self):
        if not self.profile:
            return  "N/A"
        if not self.nodes:
            return  "N/A"
        return "\n".join(str(node) for node in self.nodes)

    def json(self):
        if not self.profile:
            return None 
        if not self.nodes:
            return  None
        return [node.json() for node in self.nodes]
    
    def dump_json(self):
        return json.dumps(self.json())
    

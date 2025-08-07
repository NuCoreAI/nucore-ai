# This class manages nodes/profiles/programs in the nucore platform


import json
import logging
import xml.etree.ElementTree as ET


from nucore import Profile, Family, Instance
from nucore import Editor, EditorSubsetRange, EditorMinMaxRange
from nucore import LinkDef, LinkParameter
from nucore import NodeDef, NodeProperty, NodeCommands, NodeLinks
from nucore import TypeInfo, Property, Node
from nucore import Command, CommandParameter
from nucore import get_uom_by_id
from nucore import NuCoreBackendAPI as nucoreAPI
from nucore import NuCorePrograms as nucorePrograms
from config import AIConfig
from rag import DeviceRagFormatter
from rag import ToolsRAGFormatter
from rag import StaticInfoRAGFormatter 
from rag import RAGProcessor


logger = logging.getLogger(__name__)
config = AIConfig()

class NuCoreError(Exception):
    """Base exception for nucore backend errors."""
    pass


def debug(msg):
    logger.debug(f"[PROFILE FORMAT ERROR] {msg}")


class NuCore:
    """Class to handle nucore backend operations such as loading profiles and nodes."""
    def __init__(self, collection_path, collection_name:str, backend_url:str, backend_username:str=None, backend_password:str=None, reranker_url:str=None):
        """
        Initialize the NuCore instance with backend URL, username, and password.
        @param collection_path: The path to the collection file. This is used to store all the embeddings. (mandatory)
        @param collection_name: The name of the collection to be used. This is used to store all the embeddings. (mandatory)
        @param backend_url: The URL of the nucore backend. (mandatory)
        @param backend_username (str): The username for the nucore backend. (optional)
        @param backend_password (str): The password for the nucore backend. (optional)
        @param reranker_url (str): The URL of the reranker service. If not provided, reranking will not be performed.
        
        Note: Make sure that the collection_path and collection_name are set correctly to
        """
        if not collection_name or not collection_path or not backend_url:
            raise NuCoreError("collection_name and backend_url are mandatory parameters.")

        self.name = collection_name     
        self.url = backend_url
        self.username = backend_username
        self.password = backend_password
        self.nodes = [] 
        self.lookup = {}
        self.rag_processor = RAGProcessor(collection_path, collection_name, reranker_url=reranker_url)

    def __load_profile_from_url__(self):
        """Load profile from the specified URL."""
        if not self.url:
            raise NuCoreError("URL is not set.")
        if not self.username or not self.password:
            raise NuCoreError("Username and password must be provided for URL access.")

        nucore_api = nucoreAPI(base_url=self.url, username=self.username, password=self.password)
        response = nucore_api.get_profiles()
        if response is None:
            raise NuCoreError("Failed to fetch profile from URL.")
        return self.__parse_profile__(response)

    def __parse_profile__(self, raw):
        """Build Profile from dict, with type/checking and lookups"""
        families = []
        for fidx, f in enumerate(raw.get("families", [])):
            # Validate keys / format
            if "id" not in f:
                debug(f"Family {fidx} missing 'id'")
            if isinstance(f, str):
                debug(f"Family {fidx} is a string, expected dict")
                continue
            instances = []
            #mpg names hack
            mpg_index = 0
            for iidx, i in enumerate(f.get("instances", [])):
                # Build Editors for reference first
                editors_dict = {}
                for edict in i.get("editors", []):
                    if "id" not in edict:
                        debug("Editor missing 'id'")
                        continue
                    editors_dict[edict["id"]] = self.__build_editor__(edict)
                # Build LinkDefs
                linkdefs = []
                for ldict in i.get("linkdefs", []):
                    # parameters resolution below
                    params = []
                    for p in ldict.get("parameters", []):
                        if "editor" not in p:
                            debug(f"LinkDef param missing 'editor': {p}")
                            continue
                        eid = p["editor"]
                        editor = editors_dict.get(eid)
                        if not editor:
                            debug(f"Editor '{eid}' not found for linkdef param")
                        params.append(
                            LinkParameter(
                                id=p["id"],
                                editor=editor,
                                optional=p.get("optional"),
                                name=p.get("name"),
                            )
                        )
                    linkdefs.append(
                        LinkDef(
                            id=ldict["id"],
                            protocol=ldict["protocol"],
                            name=ldict.get("name"),
                            cmd=ldict.get("cmd"),
                            format=ldict.get("format"),
                            parameters=params,
                        )
                    )
                # Build NodeDefs
                nodedefs = []
                for ndict in i.get("nodedefs", []):
                    # NodeProperties
                    props = []
                    for pdict in ndict.get("properties", []):
                        eid = pdict["editor"]
                        editor = editors_dict.get(eid)
                        if not editor:
                            debug(
                                f"Editor '{eid}' not found for property '{pdict.get('id')}' in nodedef '{ndict['id']}'"
                            )

                        props.append(
                            NodeProperty(
                                id=pdict.get("id"),
                                editor=editor,
                                name=pdict.get("name"),
                                hide=pdict.get("hide"),
                            )
                        )
                    # NodeCommands
                    cmds_data = ndict.get("cmds", {})
                    sends = []
                    accepts = []
                    for ctype, clist in [
                        ("sends", cmds_data.get("sends", [])),
                        ("accepts", cmds_data.get("accepts", [])),
                    ]:
                        for cdict in clist:
                            params = []
                            for p in cdict.get("parameters", []):
                                eid = p["editor"]
                                editor = editors_dict.get(eid)
                                if not editor:
                                    debug(
                                        f"Editor '{eid}' not found for command param"
                                    )
                                params.append(
                                    CommandParameter(
                                        id=p["id"],
                                        editor=editor,
                                        name=p.get("name"),
                                        init=p.get("init"),
                                        optional=p.get("optional"),
                                    )
                                )
                            (sends if ctype == "sends" else accepts).append(
                                Command(
                                    id=cdict["id"],
                                    name=cdict.get("name"),
                                    format=cdict.get("format"),
                                    parameters=params,
                                )
                            )
                    cmds = NodeCommands(sends=sends, accepts=accepts)
                    # NodeLinks
                    links = ndict.get("links", None)
                    node_links = None
                    if links:
                        node_links = NodeLinks(
                            ctl=links.get("ctl") or [], rsp=links.get("rsp") or []
                        )
                    # Build NodeDef
                    nodedefs.append(
                        NodeDef(
                            id=ndict.get("id"),
                            properties=props,
                            cmds=cmds,
                            nls=ndict.get("nls"),
                            icon=ndict.get("icon"),
                            links=node_links,
                        )
                    )
                # Final Instance
                instances.append(
                    Instance(
                        id=i["id"],
                        name=i["name"],
                        editors=list(editors_dict.values()),
                        linkdefs=linkdefs,
                        nodedefs=nodedefs,
                    )
                )
            families.append(
                Family(id=f["id"], name=f.get("name", ""), instances=instances)
            )
        return Profile(timestamp=raw.get("timestamp", ""), families=families)

    def load_profile(self):
        self.profile = self.__load_profile_from_url__()
        return self.profile
        
    def __load_nodes_from_url__(self):
        """Load nodes from the specified URL."""
        if not self.url:
            raise NuCoreError("URL is not set.")
        if not self.username or not self.password:
            raise NuCoreError("Username and password must be provided for URL access.")

        nucore_api = nucoreAPI(base_url=self.url, username=self.username, password=self.password)
        response = nucore_api.get_nodes()
        if response is None:
            raise NuCoreError("Failed to fetch nodes from URL.")
        return ET.fromstring(response)

    def __build_nodedef_lookup__(self):
        for family in self.profile.families:
            for instance in family.instances:
                for nodedef in getattr(instance, "nodedefs", []):
                    self.lookup[f"{nodedef.id}.{family.id}.{instance.id}"] = nodedef
        return self.lookup
    
    def __load_nodes__(self):
        """Load nodes from the specified path or URL."""
        nodes = self.__load_nodes_from_url__()
        return nodes

    def __build_editor__(self, edict) -> Editor:
        ranges = []
        for rng in edict.get("ranges", []):
            uom_id = rng["uom"]
            uom = get_uom_by_id(uom_id)
            if not uom:
                debug(f"UOM '{uom_id}' not found")
            # MinMaxRange or Subset
            if "min" in rng and "max" in rng:
                ranges.append(
                    EditorMinMaxRange(
                        uom=uom,
                        min=rng["min"],
                        max=rng["max"],
                        prec=rng.get("prec"),
                        step=rng.get("step"),
                        names=rng.get("names", {}),
                    )
                )
            elif "subset" in rng:
                ranges.append(
                    EditorSubsetRange(
                        uom=uom, subset=rng["subset"], names=rng.get("names", {})
                    )
                )
            else:
                debug(f"Range must have either min/max or subset: {rng}")
        
        return Editor(id=edict["id"], ranges=ranges)
    
    def format_nodes(self):
        """
        Format nodes for fine tuning or other purposes 
        :return: List of formatted nodes.
        """
        if not self.nodes:
            raise NuCoreError("No nodes loaded.")
        device_rag_formatter = DeviceRagFormatter(indent_str=" ", prefix="-")
        return device_rag_formatter.format(nodes=self.nodes) 
    
    def format_tools(self):
        """
        Format tools for fine tuning or other purposes.
        :return: List of formatted tools.
        """
        if not self.profile:
            raise NuCoreError("No profile loaded.")
        tools_rag_formatter = ToolsRAGFormatter(indent_str=" ", prefix="-")
        return tools_rag_formatter.format(tools_path=config.getToolsFile())
    
    def format_static_info(self, path:str):
        """
        Format static information for fine tuning or other purposes.
        :param path: Path to the static information directory.
        :return: List of formatted static information to be used for embeddings.
        """
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
        static_info = kwargs.get("static_info", False)
        dump = kwargs.get("dump", False)
        if tools: 
            tools_rag_docs = self.format_tools()
            if tools_rag_docs:
                all_docs += tools_rag_docs
        
        if static_info: 
            static_info_rag_docs = self.format_static_info()
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
        """
        
        include_rag_docs = kwargs.get("include_rag_docs", False)
        dump = kwargs.get("dump", False)
        
        rc = self.load_devices()
        if include_rag_docs:
            rc = self.load_rag_docs(dump=dump)
        return rc

    # To have the latest state, we need to load devices only
    def load_devices(self, include_profiles=True):
        if include_profiles:
            if not self.load_profile():
                return None
        
        root = self.__load_nodes__()
        if root == None:
            return None

        self.__build_nodedef_lookup__()

        self.nodes = {} 
        for node_elem in root.findall(".//node"):
            typeinfo_elems = node_elem.findall("./typeInfo/t")
            typeinfo = [
                TypeInfo(t.get("id"), t.get("val")) for t in typeinfo_elems
            ]

            property_elems = node_elem.findall("./property")
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

            # youtube hack
            node_def_id = node_elem.get("nodeDefId")
            family_elem = node_elem.find("./family")
            if family_elem is not None:
                try:
                    family_id = int(family_elem.text)
                except (ValueError, TypeError):
                    family_id = 0
                try:
                    instance_id = int(family_elem.get("instance"))
                except (ValueError, TypeError):
                    instance_id = 1

            node = Node(
                flag=int(node_elem.get("flag")),
                nodeDefId=node_def_id,
                address=node_elem.find("./address").text,
                name=node_elem.find("./name").text,
                family=family_id,
                instance=instance_id,
                hint=node_elem.find("./hint").text if node_elem.find("./hint") is not None else None,
                type=node_elem.find("./type").text if node_elem.find("./type") is not None else None,
                enabled=(node_elem.find("./enabled").text.lower() == "true"),
                deviceClass=int(node_elem.find("./deviceClass").text) if node_elem.find("./deviceClass") is not None else None,
                wattage=int(node_elem.find("./wattage").text) if node_elem.find("./wattage") is not None else None,
                dcPeriod=int(node_elem.find("./dcPeriod").text) if node_elem.find("./dcPeriod") is not None else None,
                startDelay=int(node_elem.find("./startDelay").text) if node_elem.find("./startDelay") is not None else None,
                endDelay=int(node_elem.find("./endDelay").text) if node_elem.find("./endDelay") is not None else None,
                pnode=node_elem.find("./pnode").text if node_elem.find("./pnode") is not None else None,
                rpnode=node_elem.find("./rpnode").text 
                if node_elem.find("./rpnode") is not None
                else None,
                sgid=int(node_elem.find("./sgid").text)
                if node_elem.find("./sgid") is not None
                else None,
                typeInfo=typeinfo,
                properties=properties,
                parent=node_elem.find("./parent").text
                if node_elem.find("./parent") is not None
                else None,
                custom=node_elem.find("./custom").attrib
                if node_elem.find("./custom") is not None
                else None,
                devtype=node_elem.find("./devtype").attrib
                if node_elem.find("./devtype") is not None
                else None,
            )
            if self.profile and node_def_id:
                node.node_def = self.lookup.get(f"{node_def_id}.{family_id}.{instance_id}")
                if not node.node_def:
                    debug(f"[WARN] No NodeDef found for: {node_def_id}")

            self.nodes[node.address] = node

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
        nucore_api = nucoreAPI(base_url=self.url, username=self.username, password=self.password)
        response = nucore_api.send_commands(commands)
        if response is None:
            raise NuCoreError("Failed to send commands.")
        return response
    
    async def create_automation_routines(self, routines:list):
        """
        Create automation routines using the nucore API.
        
        Args:
            routines (list): A list of routines to create.
        """
        if len (routines) == 0:
            raise NuCoreError ("No valid routines provided.")

        nucore_api = nucoreAPI(base_url=self.url, username=self.username, password=self.password)
        all_programs=nucorePrograms()
        return nucore_api.upload_programs(all_programs)

    
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
        nucore_api = nucoreAPI(base_url=self.url, username=self.username, password=self.password)
        properties = nucore_api.get_properties(device_id)
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
    



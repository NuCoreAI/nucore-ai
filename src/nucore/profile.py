from dataclasses import dataclass, field

from .editor import Editor
from .linkdef import LinkDef
from .nodedef import NodeDef, NodeProperty, NodeCommands, NodeLinks
from .linkdef import LinkParameter
from .cmd import Command, CommandParameter
from .nucore_error import NuCoreError
from .node_base import node_is_group
from .node import Node
from .group import Group
from .folder import Folder
from .editor import EditorMinMaxRange, EditorSubsetRange
from .uom import get_uom_by_id
import json
import logging

logger = logging.getLogger(__name__)

def debug(msg):
    logger.debug(f"[PROFILE FORMAT ERROR] {msg}")


@dataclass
class Instance:
    """
    An instance of a family, containing specific configurations
    for editors, link definitions, and node definitions.
    """

    id: str
    name: str
    editors: list[Editor] = field(default_factory=list)
    linkdefs: list[LinkDef] = field(default_factory=list)
    nodedefs: list[NodeDef] = field(default_factory=list)


@dataclass
class Family:
    """
    A family object that groups related instances.
    """

    id: str
    name: str
    instances: list[Instance]

@dataclass
class RuntimeProfile:
    """
    Holds runtime information about nodedefs used in the profile and link to thier nodes
    """
    nodedef: NodeDef
    nodes: set[Node] = field(default_factory=set) 


@dataclass
class Profile:
    """
    Defines the overall structure of a profile file, containing
    information about families and instances.
    """
    timestamp: str = "" 
    runtime_profiles: dict[str, RuntimeProfile] = field(default_factory=dict)
    families: list[Family] = field(default_factory=list)
    nodes:  dict = field(default_factory=dict)
    groups: dict = field(default_factory=dict)
    folders: dict = field(default_factory=dict)
    lookup: dict = field(default_factory=dict)
    

    def load_from_file(self, profile_path:str):
        if not profile_path: 
            raise NuCoreError("Profile path is mandatory.")

        with open(profile_path, "rt", encoding="utf8") as f:
            raw = json.load(f)

        return self.__parse_profile__(raw)
    
    def load_from_json(self, raw:dict):
        if not raw:
            raise NuCoreError("Profile data is mandatory.")
        """Load profile from the specified URL that returns json."""
        return self.__parse_profile__(raw)
    
    def build_lookup(self):
        """Build a lookup dictionary for quick access to families and instances."""
        for family in self.families:
            for instance in family.instances:
                for nodedef in getattr(instance, "nodedefs", []):
                    self.lookup[f"{nodedef.id}.{family.id}.{instance.id}"] = nodedef

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
    
    def __parse_profile__(self, raw):
        """Build Profile from dict, with type/checking and lookups"""
        for fidx, f in enumerate(raw.get("families", [])):
            # Validate keys / format
            if "id" not in f:
                debug(f"Family {fidx} missing 'id'")
            if isinstance(f, str):
                debug(f"Family {fidx} is a string, expected dict")
                continue
            instances = []
            #mpg names hack
            for _, i in enumerate(f.get("instances", [])):
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
            self.families.append(
                Family(id=f["id"], name=f.get("name", ""), instances=instances)
            )
            self.timestamp = raw.get("timestamp", "")
        return True

    def map_nodes(self, root):
        """Map nodes from XML root element into Profile's nodes dict."""
        if root == None:
            return None

        self.build_lookup()

        self.nodes = {} 
        self.groups = {} 
        tag_names = ['./node', './group']
        elements = []
        for tag in tag_names:
            elements.extend(root.findall(f'{tag}')) 

        for node_elem in elements:
            node_flag=int(node_elem.get("flag"))
            is_group=node_is_group(node_flag)

            node = None
            if is_group:
                node = Group(node_elem)
            else:
                node = Node(node_elem)

            if node.node_def_id:
                node.node_def = self.lookup.get(f"{node.node_def_id}.{node.family}.{node.instance}")
                if not node.node_def:
                    debug(f"[WARN] No NodeDef found for: {node.node_def_id}")
                else:
                    #register node in nodedef
                    if node.node_def.id not in self.runtime_profiles:
                        self.runtime_profiles[node.node_def.id] = RuntimeProfile(nodedef=node.node_def, nodes={node})
                    else:
                        try:
                            self.runtime_profiles[node.node_def.id].nodes.add(node)
                        except Exception as e:
                            pass #probably duplicate node
            if is_group:
                self.groups[node.address] = node
            else :
                self.nodes[node.address] = node 
        
        elements = root.findall(f'./folder')

        for node_elem in elements:
            try:
                folder = Folder(node_elem)
                self.folders[node.address] = folder
            except Exception as e:
                debug(f"Error parsing folder: {e}")
                continue
        
        return self.runtime_profiles, self.nodes, self.groups, self.folders

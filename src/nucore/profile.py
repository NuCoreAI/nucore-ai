from dataclasses import dataclass, field

from .editor import Editor
from .linkdef import LinkDef
from .nodedef import NodeDef, NodeProperty, NodeCommands, NodeLinks, Property
from .linkdef import LinkParameter
from .cmd import Command, CommandParameter
from .nucore_error import NuCoreError
from .node import Node, TypeInfo
from .editor import EditorMinMaxRange, EditorSubsetRange
from .uom import get_uom_by_id
import json
import logging
import xml.etree.ElementTree as ET

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
class Profile:
    """
    Defines the overall structure of a profile file, containing
    information about families and instances.
    """
    timestamp: str
    families: list[Family]
    nodes:  dict = field(default_factory=dict)
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
                    family_id = 1
                try:
                    instance_id = int(family_elem.get("instance"))
                except (ValueError, TypeError):
                    instance_id = 1
            else:
                family_id = 1
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
            if node_def_id:
                node.node_def = self.lookup.get(f"{node_def_id}.{family_id}.{instance_id}")
                if not node.node_def:
                    debug(f"[WARN] No NodeDef found for: {node_def_id}")

            self.nodes[node.address] = node

        return self.nodes

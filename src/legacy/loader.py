import json
import logging
import argparse
import xml.etree.ElementTree as ET
from .profile import Profile, Family, Instance
from .editor import Editor, EditorSubsetRange, EditorMinMaxRange
from .linkdef import LinkDef, LinkParameter
from .nodedef import NodeDef, NodeProperty, NodeCommands, NodeLinks
from .node import TypeInfo, Property, Node
from .cmd import Command, CommandParameter
from .uom import get_uom_by_id


logger = logging.getLogger(__name__)


def debug(msg):
    logger.debug(f"[PROFILE FORMAT ERROR] {msg}")


def load_profile(json_path):
    with open(json_path, "rt", encoding="utf8") as f:
        raw = json.load(f)
    return parse_profile(raw)


def parse_profile(raw):
    """Build Profile from dict, with type/checking and lookups"""
    families = []
    for fidx, f in enumerate(raw.get("families", [])):
        # Validate keys / format
        if "id" not in f:
            debug(f"Family {fidx} missing 'id'")
        instances = []
        for iidx, i in enumerate(f.get("instances", [])):
            # Build Editors for reference first
            editors_dict = {}
            for edict in i.get("editors", []):
                if "id" not in edict:
                    debug("Editor missing 'id'")
                    continue
                editors_dict[edict["id"]] = build_editor(edict)
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
                            id=pdict["id"],
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
                        id=ndict["id"],
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


def build_editor(edict) -> Editor:
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


def build_nodedef_lookup(profile):
    lookup = {}
    for family in profile.families:
        for instance in family.instances:
            for nodedef in getattr(instance, "nodedefs", []):
                lookup[nodedef.id] = nodedef
    return lookup


def load_nodes(xml_path, profile=None):
    nodedef_lookup = build_nodedef_lookup(profile) if profile else {}

    tree = ET.parse(xml_path)
    root = tree.getroot()

    nodes = []
    for node_elem in root.findall(".//node"):
        typeinfo_elems = node_elem.findall("./typeInfo/t")
        typeinfo = [
            TypeInfo(t.get("id"), t.get("val")) for t in typeinfo_elems
        ]

        property_elems = node_elem.findall("./property")
        properties = [
            Property(
                p.get("id"),
                p.get("value"),
                p.get("formatted"),
                p.get("uom"),
                p.get("prec"),
                p.get("name"),
            )
            for p in property_elems
        ]
        node_def_id = node_elem.get("nodeDefId")

        node = Node(
            flag=int(node_elem.get("flag")),
            nodeDefId=node_def_id,
            address=node_elem.find("./address").text,
            name=node_elem.find("./name").text,
            family=int(node_elem.find("./family").text),
            hint=node_elem.find("./hint").text,
            type=node_elem.find("./type").text,
            enabled=(node_elem.find("./enabled").text.lower() == "true"),
            deviceClass=int(node_elem.find("./deviceClass").text),
            wattage=int(node_elem.find("./wattage").text),
            dcPeriod=int(node_elem.find("./dcPeriod").text),
            startDelay=int(node_elem.find("./startDelay").text),
            endDelay=int(node_elem.find("./endDelay").text),
            pnode=node_elem.find("./pnode").text,
            rpnode=node_elem.find("./rpnode").text
            if node_elem.find("./rpnode") is not None
            else None,
            sgid=int(node_elem.find("./sgid").text)
            if node_elem.find("./sgid") is not None
            else None,
            typeInfo=typeinfo,
            property=properties,
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
        if profile and node_def_id:
            node.node_def = nodedef_lookup.get(node_def_id)
            if not node.node_def:
                debug(f"[WARN] No NodeDef found for: {node_def_id}")

        nodes.append(node)

    return nodes


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Loader for IOX Profile and Nodes XML files."
    )
    parser.add_argument(
        "--profile",
        dest="profile_path",
        type=str,
        required=False,
        help="Path to the profile JSON file (profile-xxx.json)",
    )
    parser.add_argument(
        "--nodes",
        dest="nodes_path",
        type=str,
        required=False,
        help="Path to the nodes XML file (nodes.xml)",
    )
    parser.add_argument(
        "--url",
        dest="url",
        type=str,
        required=False,
        help="The URL to fetch nodes and profiles from the nucore platform",
    )
    parser.add_argument(
        "--url",
        dest="username",
        type=str,
        required=False,
        help="The username to authenticate with the nucore platform",
    )
    parser.add_argument(
        "--url",
        dest="password",
        type=str,
        required=False,
        help="The password to authenticate with the nucore platform",
    )

    args = parser.parse_args()

    if args.profile_path:
        profile = load_profile(args.profile_path)
        print(profile)

    if args.nodes_path:
        nodes = load_nodes(
            args.nodes_path, profile=profile if args.profile_path else None
        )
        for node in nodes:
            print(node)

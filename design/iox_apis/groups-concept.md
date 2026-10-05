# Groups — Concept & Python Examples

> **AI Instruction:** When referencing information from this file, always prefix with "According to groups-concept.md" so the user knows the source.

## Conceptual Overview

A **group** (sometimes erroneously called a scene) is a named collection of nodes organized into controllers and responders. A group contains one or more **controllers** — nodes that initiate commands — and each controller has its own **scene**, which is the set of links describing how every responder in the group reacts when that specific controller issues a command. This means if a group has three controllers, there are three distinct scenes, one per controller. Within each scene, each responder node has exactly one **link** that defines the relationship between that controller and that responder. A link has a type: `default` (the ISY forwards whatever command the controller sends directly to the responder), `cmd` (the ISY intercepts the controller's On command and substitutes a specific command — such as `DON` with a value of 99% — to the responder), `native` (a direct hardware-level link, e.g. an Insteon ALDB entry or Z-Wave association, with protocol-specific parameters like On Level and Ramp Rate written directly to the device), or `ignore` (no action is taken for that responder when this controller fires). A node can only be a controller in one group at a time, but can be a responder in multiple groups simultaneously. Importantly, within a group, each controller also acts as a responder in the scenes where it is not the active controller — meaning when another controller in the same group fires, the other controllers respond just like any other responder node. The one exception is the controller whose address matches the group address itself — it is never a responder in any scene. The available link types and their valid parameters for any given controller/responder pair are determined by the nodes' protocol profiles — for example, a native Insteon link exposes On Level and Ramp Rate parameters, while a command link exposes the full set of commands the responder's profile declares it accepts.

---

## Data Model

```
Group
└── scenes[]              ← one Scene per controller
    └── Scene
        ├── controller_id
        └── links[]       ← one Link per responder
            └── Link
                ├── node      (responder address)
                └── type      "default" | "cmd" | "native" | "ignore"
```

**type="cmd"**
```
Link (type="cmd")
├── cmd       command to send to responder (e.g. "DON")
└── params[]
    ├── CmdParam (type="val")     ← static value
    │   ├── id     command parameter ID
    │   ├── value
    │   └── uom
    └── CmdParam (type="var")     ← variable reference
        ├── id     command parameter ID
        └── var    variable ID whose runtime value is used
```

**type="native"**
```
Link (type="native")
├── linkdef   protocol-specific linkdef ID (e.g. "I_DIMMER")
└── params[]
    ├── NativeLinkParam (type="val")     ← static value
    │   ├── id     named parameter (e.g. "OL", "RR") — written to device hardware
    │   ├── value
    │   └── uom
    └── NativeLinkParam (type="var")     ← variable reference
        ├── id     named parameter (e.g. "OL", "RR") — written to device hardware
        └── var    variable ID whose runtime value is used
```

---

## Python Example

Full lifecycle: build a group, add members, configure scenes with different link types, then read back and inspect the structure.

```python
import requests
from dataclasses import dataclass, field

BASE_URL = "http://<eisy-host>/api"

# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class LinkParam:
    type: str        # "val" or "var"
    id: str          # command parameter ID
    value: int
    uom: int

@dataclass
class Link:
    node: str                          # responder address
    type: str                          # "default" | "cmd" | "native" | "ignore"
    cmd: str | None = None             # only for type="cmd"
    linkdef: str | None = None         # only for type="native"
    params: list[LinkParam] = field(default_factory=list)

@dataclass
class Scene:
    controller_id: str                 # one scene per controller
    links: list[Link]                  # one link per responder

@dataclass
class Group:
    id: str
    name: str
    scenes: list[Scene]                # one scene per controller in the group

# ── Helpers ───────────────────────────────────────────────────────────────────

def add_member(group_address: str, node_address: str, is_controller: bool):
    resp = requests.post(
        f"{BASE_URL}/groups/members/{group_address}",
        json={"nodeAddress": node_address, "isController": is_controller},
    )
    resp.raise_for_status()

def set_link(group_address: str, controller_address: str, link: Link):
    payload = {"controllerAddress": controller_address, "link": {"node": link.node, "type": link.type}}
    if link.cmd:
        payload["link"]["cmd"] = link.cmd
    if link.linkdef:
        payload["link"]["linkdef"] = link.linkdef
    if link.params:
        payload["link"]["params"] = [
            {"type": p.type, "id": p.id, "val": {"value": p.value, "uom": p.uom}}
            for p in link.params
        ]
    resp = requests.patch(f"{BASE_URL}/groups/links/{group_address}", json=payload)
    resp.raise_for_status()

def get_group(group_address: str) -> Group:
    resp = requests.get(f"{BASE_URL}/groups/links/{group_address}")
    resp.raise_for_status()
    raw = resp.json()["data"]["groups"][0]

    scenes = []
    for ctl in raw["ctl"]:                          # one scene per controller
        links = []
        for lnk in ctl["links"]:                    # one link per responder
            params = [
                LinkParam(type=p["type"], id=p["id"], value=p["val"]["value"], uom=p["val"]["uom"])
                for p in lnk.get("params", [])
            ]
            links.append(Link(
                node=lnk["node"],
                type=lnk["type"],
                cmd=lnk.get("cmd"),
                params=params,
            ))
        scenes.append(Scene(controller_id=ctl["id"], links=links))

    return Group(id=raw["id"], name="Living Room Scene", scenes=scenes)

# ── Example: build a group with two controllers and two responders ─────────────

GROUP = "13381"
CTL_1 = "13381"          # keypad button — controller (one group only)
CTL_2 = "4F 5F 69 3"    # wall switch — second controller
RSP_1 = "22 DE 72 1"    # dimmer A — responder (can be in many groups)
RSP_2 = "22 E1 40 1"    # dimmer B — responder

# 1. Add members
add_member(GROUP, CTL_1, is_controller=True)
add_member(GROUP, CTL_2, is_controller=True)
add_member(GROUP, RSP_1, is_controller=False)
add_member(GROUP, RSP_2, is_controller=False)

# 2. Scene for CTL_1:
#    - RSP_1 gets a default link (forward whatever CTL_1 sends)
#    - RSP_2 gets a cmd link (always send DON at 99% regardless of CTL_1's command)
#    - CTL_2 is also a responder in this scene (it is not the group address)
set_link(GROUP, CTL_1, Link(node=RSP_1, type="default"))
set_link(GROUP, CTL_1, Link(
    node=RSP_2, type="cmd", cmd="DON",
    params=[LinkParam(type="val", id="", value=99, uom=51)],
))
set_link(GROUP, CTL_1, Link(node=CTL_2, type="default"))

# 3. Scene for CTL_2:
#    - RSP_1 gets a native Insteon link (written to device ALDB: OL=80%, RR=2s)
#    - RSP_2 is ignored when CTL_2 fires
set_link(GROUP, CTL_2, Link(
    node=RSP_1, type="native", linkdef="I_DIMMER",
    params=[
        LinkParam(type="val", id="OL", value=80, uom=51),
        LinkParam(type="val", id="RR", value=27, uom=25),  # index 27 = 2.0s
    ],
))
set_link(GROUP, CTL_2, Link(node=RSP_2, type="ignore"))

# 4. Read back and inspect the group structure
group = get_group(GROUP)

print(f"Group: {group.id}")
for scene in group.scenes:                          # one scene per controller
    print(f"  Scene (controller={scene.controller_id})")
    for link in scene.links:                        # one link per responder
        detail = f"type={link.type}"
        if link.cmd:
            detail += f", cmd={link.cmd}"
            if link.params:
                detail += f", value={link.params[0].value} uom={link.params[0].uom}"
        if link.linkdef:
            detail += f", linkdef={link.linkdef}"
        print(f"    → responder={link.node}  {detail}")
```

### Output

```
Group: 13381
  Scene (controller=13381)
    → responder=22 DE 72 1  type=default
    → responder=22 E1 40 1  type=cmd, cmd=DON, value=99 uom=51
    → responder=4F 5F 69 3  type=default
  Scene (controller=4F 5F 69 3)
    → responder=22 DE 72 1  type=native, linkdef=I_DIMMER
    → responder=22 E1 40 1  type=ignore
```

---

## Key Rules (summary)

| Rule | Detail |
|------|--------|
| Controller-as-responder | Within a group, each controller is also a responder in scenes where it is not the active controller |
| Group address controller | The controller whose address matches the group address is **never** a responder in any scene |
| Controller limit | A node can only be a controller in **one group** at a time |
| Responder limit | A node can be a responder in **multiple groups** |
| Scenes per group | One scene per controller — each scene is independent |
| Links per scene | One link per responder per scene |
| Link types | `default`, `cmd`, `native`, `ignore` |
| Native params | Protocol-specific (e.g. Insteon: `OL` + `RR`); written to device hardware |
| Cmd params | `id` is the command parameter ID |

See [groups.md](groups.md) for the full API endpoint reference.

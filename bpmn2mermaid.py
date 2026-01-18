#!/usr/bin/env python3
"""
bpmn2mermaid.py — BPMN 2.0 XML -> Mermaid flowchart translator.

This translates common BPMN 2.0 constructs into Mermaid flowchart syntax.

Supported (useful, common subset)
- Activities: task, userTask, serviceTask, scriptTask, manualTask, businessRuleTask,
  sendTask, receiveTask, callActivity, subProcess
- Events: startEvent, endEvent, intermediateCatchEvent, intermediateThrowEvent,
  boundaryEvent (with timer/message/error/signal/escalation/terminate icons)
- Gateways: XOR (exclusiveGateway), AND (parallelGateway), OR (inclusiveGateway),
  eventBased/complex shown generically
- Sequence flows with optional labels
- Collaboration:
  - Participants/pools + lanes rendered as Mermaid subgraphs
  - Message flows (bpmn:messageFlow) rendered as dashed edges between pools/lanes
- Artifacts:
  - Data objects (dataObjectReference) + data input/output associations (dotted edges)
  - Data stores (dataStoreReference) + associations (dotted edges)
  - Text annotations + associations (dotted edges)

Limitations / design choices
- Mermaid flowchart does not preserve BPMN layout.
- We do not try to fully expand subprocess internals (optional flag adds a hint only).
- Many BPMN types exist; this aims for a clean, extendable baseline.

Usage
  python3 bpmn2mermaid.py diagram.bpmn -o diagram.mmd
  python3 bpmn2mermaid.py diagram.bpmn -d TB

"""

from __future__ import annotations

import argparse
import re
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Set

BPMN_NS = "http://www.omg.org/spec/BPMN/20100524/MODEL"
NS = {"bpmn": BPMN_NS}

ACTIVITY_TAGS = {
    "task", "userTask", "serviceTask", "scriptTask", "manualTask", "businessRuleTask",
    "sendTask", "receiveTask", "callActivity", "subProcess",
}
EVENT_TAGS = {"startEvent", "endEvent", "intermediateThrowEvent", "intermediateCatchEvent", "boundaryEvent"}
GATEWAY_TAGS = {"exclusiveGateway", "parallelGateway", "inclusiveGateway", "eventBasedGateway", "complexGateway"}

ARTIFACT_TAGS = {"textAnnotation", "dataObjectReference", "dataStoreReference"}
EDGE_ASSOCIATION_TAGS = {"association"}

# --- helpers -----------------------------------------------------------------

def local(tag: str) -> str:
    return tag.split("}")[-1] if "}" in tag else tag

def sanitize_id(s: str) -> str:
    s = re.sub(r"[^0-9a-zA-Z_]", "_", s)
    if re.match(r"^[0-9]", s):
        s = "n_" + s
    return s

def esc(s: str) -> str:
    """
    Make labels safe for Mermaid flowchart syntax (Mermaid v10).
    Mermaid treats [], (), | as syntax in many contexts -> replace them.
    """
    t = (s or "").replace("\n", " ").replace('"', "'").strip()

    # Replace characters that break Mermaid parsing in labels
    t = t.replace("[", "⟦").replace("]", "⟧")   # or "(" and ")"
    t = t.replace("(", "⟮").replace(")", "⟯")   # avoids circle/doublecircle conflicts
    t = t.replace("|", "¦")                    # avoids edge-label delimiter
    t = t.replace("<", "‹").replace(">", "›")   # optional: avoids HTML surprises

    return t



def text_or_none(el: Optional[ET.Element]) -> Optional[str]:
    if el is None:
        return None
    t = (el.text or "").strip()
    return t or None

# --- model -------------------------------------------------------------------

@dataclass
class Node:
    id: str
    tag: str
    label: str
    process_id: str
    # For boundary events
    attached_to: Optional[str] = None
    event_kind: Optional[str] = None  # timer/message/error/signal/escalation/terminate/...
    is_interrupting: Optional[bool] = None

@dataclass
class DataObject:
    id: str
    name: str
    kind: str = "dataObject"  # dataObject or dataStore

@dataclass
class Edge:
    source: str
    target: str
    label: str = ""
    kind: str = "sequence"  # sequence, message, association

@dataclass
class DataEdge:
    source: str
    target: str
    kind: str  # input/output

# --- rendering ----------------------------------------------------------------

EVENT_ICON = {
    "timer": "⏰",
    "message": "✉️",
    "error": "❌",
    "signal": "📣",
    "escalation": "⚠️",
    "terminate": "🛑",
    "cancel": "⛔",
    "compensate": "↩️",
    "conditional": "🔎",
    "link": "🔗",
    "none": "",
}

def _event_prefix(node: Node) -> str:
    if not node.event_kind:
        return ""
    return (EVENT_ICON.get(node.event_kind, "") + " ").strip()

def mermaid_shape(node: Node, expand_subprocess: bool = False) -> str:
    label = esc(node.label) or node.tag

    # Add event icons (especially helpful for boundary/intermediate events)
    if node.tag in EVENT_TAGS:
        pref = _event_prefix(node)
        if pref:
            label = f"{pref} {label}".strip()

    # Activities
    if node.tag in ACTIVITY_TAGS:
        if node.tag == "callActivity":
            return f"[[{label}]]"          # subroutine/call
        if node.tag == "subProcess":
            # Mermaid doesn't have BPMN subprocess; use a stronger visual hint
            return f"[[{label}]]" if expand_subprocess else f"[{label}]"
        return f"[{label}]"

    # Events
    if node.tag in {"startEvent"}:
        return f"(({label}))"
    if node.tag in {"endEvent"}:
        return f"(({label}))"
    if node.tag in {"intermediateThrowEvent", "intermediateCatchEvent", "boundaryEvent"}:
        # Mermaid has no special small circle; keep consistent
        return f"(({label}))"

    # Gateways
    if node.tag == "exclusiveGateway":
        return f"{{{label}\\nXOR}}"
    if node.tag == "parallelGateway":
        return f"{{{label}\\nAND}}"
    if node.tag == "inclusiveGateway":
        return f"{{{label}\\nOR}}"
    if node.tag in GATEWAY_TAGS:
        return f"{{{label}}}"

    # Artifacts as nodes
    if node.tag == "textAnnotation":
        return f'["📝 {label}"]'

    return f"[{label}]"

def mermaid_data_shape(d: DataObject) -> str:
    name = esc(d.name) or ("Data Store" if d.kind == "dataStore" else "Data Object")
    if d.kind == "dataStore":
        # database-ish cylinder in Mermaid: [(...)]
        return f"[({name})]"
    return f"[/{name}/]"

# --- parsing ------------------------------------------------------------------

def detect_event_kind(ev: ET.Element) -> Optional[str]:
    """Detect event definition kind for (boundary/intermediate/start/end) events."""
    defs = [
        ("timer", "timerEventDefinition"),
        ("message", "messageEventDefinition"),
        ("error", "errorEventDefinition"),
        ("signal", "signalEventDefinition"),
        ("escalation", "escalationEventDefinition"),
        ("cancel", "cancelEventDefinition"),
        ("compensate", "compensateEventDefinition"),
        ("conditional", "conditionalEventDefinition"),
        ("link", "linkEventDefinition"),
        ("terminate", "terminateEventDefinition"),
    ]
    for kind, tag in defs:
        if ev.find(f"bpmn:{tag}", NS) is not None:
            return kind
    return None

def parse_bpmn(path: Path):
    tree = ET.parse(path)
    root = tree.getroot()

    participants: List[Tuple[str, str]] = []  # (processRef, pool name)
    message_edges: List[Edge] = []

    collab = root.find("bpmn:collaboration", NS)
    if collab is not None:
        for part in collab.findall("bpmn:participant", NS):
            pref = part.attrib.get("processRef")
            pname = (part.attrib.get("name") or "").strip()
            if pref:
                participants.append((pref, pname or pref))

        for mf in collab.findall("bpmn:messageFlow", NS):
            s = mf.attrib.get("sourceRef")
            t = mf.attrib.get("targetRef")
            if not s or not t:
                continue
            message_edges.append(
                Edge(s, t, (mf.attrib.get("name") or "").strip(), kind="message")
            )

    processes: Dict[str, ET.Element] = {p.attrib["id"]: p for p in root.findall("bpmn:process", NS)}

    lanes_by_process: Dict[str, List[Tuple[str, str, List[str]]]] = defaultdict(list)
    for pid, proc in processes.items():
        for ls in proc.findall("bpmn:laneSet", NS):
            for lane in ls.findall("bpmn:lane", NS):
                lid = lane.attrib.get("id", "")
                lname = (lane.attrib.get("name") or "").strip() or "Lane"
                refs = [r.text.strip() for r in lane.findall("bpmn:flowNodeRef", NS) if r.text]
                lanes_by_process[pid].append((lid, lname, refs))

    data_obj_defs: Dict[str, str] = {}
    for dobj in root.findall(".//bpmn:dataObject", NS):
        data_obj_defs[dobj.attrib.get("id", "")] = (dobj.attrib.get("name") or "").strip()

    data_store_defs: Dict[str, str] = {}
    for ds in root.findall(".//bpmn:dataStore", NS):
        data_store_defs[ds.attrib.get("id", "")] = (ds.attrib.get("name") or "").strip()

    data_objects: Dict[str, DataObject] = {}
    for dref in root.findall(".//bpmn:dataObjectReference", NS):
        rid = dref.attrib.get("id")
        if not rid:
            continue
        name = (dref.attrib.get("name") or "").strip()
        if not name:
            name = data_obj_defs.get(dref.attrib.get("dataObjectRef", ""), "").strip() or "Data Object"
        data_objects[rid] = DataObject(rid, name, kind="dataObject")

    for dsref in root.findall(".//bpmn:dataStoreReference", NS):
        rid = dsref.attrib.get("id")
        if not rid:
            continue
        name = (dsref.attrib.get("name") or "").strip()
        if not name:
            name = data_store_defs.get(dsref.attrib.get("dataStoreRef", ""), "").strip() or "Data Store"
        data_objects[rid] = DataObject(rid, name, kind="dataStore")

    nodes: Dict[str, Node] = {}

    for pid, proc in processes.items():
        for el in proc.iter():
            tag = local(el.tag)
            eid = el.attrib.get("id")
            if not eid:
                continue

            if tag in ACTIVITY_TAGS or tag in EVENT_TAGS or tag in GATEWAY_TAGS:
                label = (el.attrib.get("name") or "").strip() or tag
                n = Node(eid, tag, label, pid)

                if tag == "boundaryEvent":
                    n.attached_to = el.attrib.get("attachedToRef")
                    it = el.attrib.get("cancelActivity")
                    if it is not None:
                        n.is_interrupting = (it.lower() == "true")
                    n.event_kind = detect_event_kind(el) or "none"
                elif tag in EVENT_TAGS:
                    n.event_kind = detect_event_kind(el)

                nodes[eid] = n

            elif tag == "textAnnotation":
                txt = text_or_none(el.find("bpmn:text", NS)) or (el.attrib.get("text") or "").strip() or "Annotation"
                nodes[eid] = Node(eid, tag, txt, pid)

    edges: List[Edge] = []
    for proc in processes.values():
        for sf in proc.findall("bpmn:sequenceFlow", NS):
            s = sf.attrib.get("sourceRef")
            t = sf.attrib.get("targetRef")
            if not s or not t:
                continue
            edges.append(Edge(s, t, (sf.attrib.get("name") or "").strip(), kind="sequence"))

    assoc_edges: List[Edge] = []
    for assoc in root.findall(".//bpmn:association", NS):
        s = assoc.attrib.get("sourceRef")
        t = assoc.attrib.get("targetRef")
        if not s or not t:
            continue
        assoc_edges.append(Edge(s, t, (assoc.attrib.get("name") or "").strip(), kind="association"))

    data_edges: List[DataEdge] = []
    for act in root.iter():
        atag = local(act.tag)
        if atag not in ACTIVITY_TAGS:
            continue
        tid = act.attrib.get("id")
        if not tid:
            continue

        for doa in act.findall("bpmn:dataOutputAssociation", NS):
            tgt = doa.find("bpmn:targetRef", NS)
            if tgt is not None and (tgt.text or "").strip() in data_objects:
                data_edges.append(DataEdge(tid, tgt.text.strip(), "output"))

        for dia in act.findall("bpmn:dataInputAssociation", NS):
            src = dia.find("bpmn:sourceRef", NS)
            if src is not None and (src.text or "").strip() in data_objects:
                data_edges.append(DataEdge(src.text.strip(), tid, "input"))

    boundary_attach_edges: List[Edge] = []
    for n in nodes.values():
        if n.tag == "boundaryEvent" and n.attached_to:
            boundary_attach_edges.append(Edge(n.attached_to, n.id, "", kind="association"))

    all_edges = edges + message_edges + assoc_edges + boundary_attach_edges
    return processes, participants, lanes_by_process, nodes, data_objects, all_edges, data_edges

def generate_mermaid(
    processes,
    participants,
    lanes_by_process,
    nodes: Dict[str, Node],
    data_objects: Dict[str, DataObject],
    edges: List[Edge],
    data_edges: List[DataEdge],
    direction: str,
    expand_subprocess: bool = False,
):
    mid: Dict[str, str] = {}
    for bid in list(nodes.keys()) + list(data_objects.keys()):
        mid[bid] = sanitize_id(bid)

    proc_label: Dict[str, str] = {}
    for pid, proc in processes.items():
        proc_label[pid] = (proc.attrib.get("name") or pid).strip()
    for pid, pname in participants:
        proc_label[pid] = pname

    lines: List[str] = []

    def add(line: str, indent: int = 0):
        lines.append("  " * indent + line)

    add(f"flowchart {direction}")

    rendered: Set[str] = set()

    def render_node(bid: str, indent: int):
        if bid in rendered or bid not in nodes:
            return
        rendered.add(bid)
        add(f"{mid[bid]}{mermaid_shape(nodes[bid], expand_subprocess=expand_subprocess)}", indent)

    def render_data(did: str, indent: int):
        if did in rendered or did not in data_objects:
            return
        rendered.add(did)
        add(f"{mid[did]}{mermaid_data_shape(data_objects[did])}", indent)

    artifact_process: Dict[str, str] = {}
    for de in data_edges:
        if de.source in nodes and de.target in data_objects:
            artifact_process[de.target] = nodes[de.source].process_id
        if de.source in data_objects and de.target in nodes:
            artifact_process[de.source] = nodes[de.target].process_id
    for e in edges:
        if e.kind == "association":
            if e.source in nodes and e.target in data_objects:
                artifact_process[e.target] = nodes[e.source].process_id
            if e.source in data_objects and e.target in nodes:
                artifact_process[e.source] = nodes[e.target].process_id
            if e.source in nodes and e.target in nodes:
                artifact_process.setdefault(e.source, nodes[e.source].process_id)
                artifact_process.setdefault(e.target, nodes[e.target].process_id)

    if participants:
        for pid in processes.keys():
            add(f'subgraph {sanitize_id(pid)}["{esc(proc_label.get(pid, pid))}"]', 1)
            lane_defs = lanes_by_process.get(pid, [])

            if lane_defs:
                lane_refs_all: Set[str] = set()
                for lid, lname, refs in lane_defs:
                    add(f'subgraph {sanitize_id(lid)}["{esc(lname)}"]', 2)
                    for rid in refs:
                        lane_refs_all.add(rid)
                        render_node(rid, 3)

                    for did, dp in artifact_process.items():
                        if dp != pid:
                            continue
                        connected = any(
                            (de.source == did and de.target in refs) or (de.target == did and de.source in refs)
                            for de in data_edges
                        ) or any(
                            (e.source == did and e.target in refs) or (e.target == did and e.source in refs)
                            for e in edges if e.kind == "association"
                        )
                        if connected:
                            render_data(did, 3)

                    add("end", 2)

                for nid, n in nodes.items():
                    if n.process_id == pid and nid not in lane_refs_all:
                        render_node(nid, 2)

                for did, dp in artifact_process.items():
                    if dp == pid:
                        render_data(did, 2)
            else:
                for nid, n in nodes.items():
                    if n.process_id == pid:
                        render_node(nid, 2)
                for did, dp in artifact_process.items():
                    if dp == pid:
                        render_data(did, 2)

            add("end", 1)
    else:
        for nid in nodes:
            render_node(nid, 1)
        for did in data_objects:
            render_data(did, 1)

    def edge_line(e: Edge) -> Optional[str]:
        if e.source not in mid or e.target not in mid:
            return None

        s = mid[e.source]
        t = mid[e.target]
        label = esc(e.label)

        if e.kind == "sequence":
            if label:
                return f"{s} -->|{label}| {t}"
            return f"{s} --> {t}"

        if e.kind == "message":
            if label:
                return f"{s} -.->|{label}| {t}"
            return f"{s} -.-> {t}"


        if e.kind == "association":
            if label:
                return f"{s} -.->|{label}| {t}"
            return f"{s} -.-> {t}"

        return None

    for e in edges:
        line = edge_line(e)
        if line:
            add(line, 1)

    for de in data_edges:
        if de.source not in mid or de.target not in mid:
            continue
        lbl = "data" if de.kind == "output" else "input"
        add(f"{mid[de.source]} -.->|{lbl}| {mid[de.target]}", 1)

    return "\n".join(lines) + "\n"

def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Translate BPMN 2.0 XML to Mermaid flowchart.")
    ap.add_argument("bpmn_file", type=Path, help="Path to .bpmn (BPMN 2.0 XML) file")
    ap.add_argument(
        "-d", "--direction",
        default="LR",
        choices=["LR", "TB", "RL", "BT"],
        help="Mermaid flow direction (default: LR)",
    )
    ap.add_argument(
        "-o", "--out",
        type=Path,
        default=None,
        help="Write output to file (default: stdout)",
    )
    ap.add_argument(
        "--expand-subprocess",
        action="store_true",
        help="Render subProcess as [[...]] hint (still not full expansion).",
    )

    args = ap.parse_args(argv)

    processes, participants, lanes_by_process, nodes, data_objects, edges, data_edges = parse_bpmn(args.bpmn_file)
    mermaid = generate_mermaid(
        processes,
        participants,
        lanes_by_process,
        nodes,
        data_objects,
        edges,
        data_edges,
        args.direction,
        expand_subprocess=args.expand_subprocess,
    )

    if args.out:
        args.out.write_text(mermaid, encoding="utf-8")
    else:
        sys.stdout.write(mermaid)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())

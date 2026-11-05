"""Plant water-flow graph (source -> subsystem -> outlet / evaporation / product / loss)
built from an :class:`Analysis`; the GUI draws it as a Sankey-style diagram.

Edge colours and node status come from the water balance, so a leaking subsystem is visible
at a glance. Transfers between subsystems use ``plant.links``; any R'_out not covered by a
link is routed to a single "undefined transfer" node so that the picture never hides water.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

from .analysis import Analysis
from .model import Plant, Transfer, sync_transfers

LOSS_MIN_PCT = 0.5     # unaccounted loss below this share of inflow is not drawn as an edge


@dataclass
class FlowNode:
    id: str
    kind: str            # source | subsystem | outlet | evap | product | loss | pool
    name: str
    volume: float = 0.0
    column: int = 0
    status: str = ""     # balance status for subsystems


@dataclass
class FlowEdge:
    src: str
    dst: str
    volume: float
    kind: str            # intake | discharge | transfer | evap | product | loss
    sub: str = ""        # owning subsystem (for editing)
    ref: str = ""        # source / outlet / destination subsystem name; "" for evap, product, undefined transfer

    @property
    def editable(self) -> bool:
        return self.kind != "loss"


@dataclass
class FlowGraph:
    nodes: List[FlowNode] = field(default_factory=list)
    edges: List[FlowEdge] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    layout: Dict[str, float] = field(default_factory=dict)
    ref_total: float = 0.0            # if set, thickness scale is fixed to this total (comparable frames in time)

    def node(self, nid: str) -> FlowNode:
        for n in self.nodes:
            if n.id == nid:
                return n
        raise KeyError(nid)


def build_flow(a: Analysis, labels: Dict[str, str]) -> FlowGraph:
    """``labels``: translated names for the fixed nodes: evap, product, loss, pool."""
    plant = a.plant
    g = FlowGraph()
    status = {b.name: b for b in a.balance.subsystems}
    names = {s.name for s in plant.subsystems}

    def add(kind: str, name: str, col: int, st: str = "") -> str:
        nid = f"{kind}:{name}"
        if not any(n.id == nid for n in g.nodes):
            g.nodes.append(FlowNode(nid, kind, name, 0.0, col, st))
        return nid

    g.layout = dict(plant.layout)

    def edge(src: str, dst: str, vol: float, kind: str, sub: str = "", ref: str = "") -> None:
        if vol > 1e-9:
            g.edges.append(FlowEdge(src, dst, vol, kind, sub, ref))

    for s in plant.subsystems:
        b = status.get(s.name)
        add("subsystem", s.name, 1, b.status if b else "")
    for sub in plant.subsystems:
        sid = f"subsystem:{sub.name}"
        for src, v in sub.intake.items():
            edge(add("source", src, 0), sid, v, "intake", sub.name, src)
        for out, v in sub.discharge.items():
            edge(sid, add("outlet", out, 2), v, "discharge", sub.name, out)
        if sub.evaporation > 0:
            edge(sid, add("evap", labels["evap"], 2), sub.evaporation, "evap", sub.name)
        if sub.product > 0:
            edge(sid, add("product", labels["product"], 2), sub.product, "product", sub.name)
        b = status.get(sub.name)
        if b and b.unknown_loss > 0 and b.unknown_loss_pct_of_inflow > LOSS_MIN_PCT:
            edge(sid, add("loss", labels["loss"], 2), b.unknown_loss, "loss", sub.name)

    # transfers between subsystems
    linked: Dict[str, float] = {}
    for lk in plant.links:
        if lk.src in names and lk.dst in names and lk.src != lk.dst and lk.volume > 0:
            linked[lk.src] = linked.get(lk.src, 0.0) + lk.volume
        elif lk.volume > 0:
            g.warnings.append(f"{lk.src} -> {lk.dst}")
    for sub in plant.subsystems:
        total = sub.to_other
        done = linked.get(sub.name, 0.0)
        if total <= 0:
            continue
        scale = min(1.0, total / done) if done > 0 else 0.0
        for lk in plant.links:
            if lk.src == sub.name and lk.dst in names and lk.dst != lk.src and lk.volume > 0:
                edge(f"subsystem:{sub.name}", f"subsystem:{lk.dst}", lk.volume * scale, "transfer", sub.name, lk.dst)
        rest = total - done * scale
        if rest > 1e-6:
            edge(f"subsystem:{sub.name}", add("pool", labels["pool"], 2), rest, "transfer", sub.name)

    for n in g.nodes:
        ins = sum(e.volume for e in g.edges if e.dst == n.id)
        outs = sum(e.volume for e in g.edges if e.src == n.id)
        n.volume = max(ins, outs)
    return g


# ---------------------------------------------------------------- editing (used by the interactive diagram)
FLOW_KINDS = ("intake", "discharge", "transfer", "evap", "product")


def _sub(plant: Plant, name: str):
    for s in plant.subsystems:
        if s.name == name:
            return s
    raise KeyError(name)


def current_volume(plant: Plant, kind: str, sub: str, ref: str = "") -> float:
    s = _sub(plant, sub)
    if kind == "intake":
        return s.intake.get(ref, 0.0)
    if kind == "discharge":
        return s.discharge.get(ref, 0.0)
    if kind == "evap":
        return s.evaporation
    if kind == "product":
        return s.product
    if kind == "transfer":
        if ref:
            return sum(x.volume for x in plant.links if x.src == sub and x.dst == ref)
        return max(0.0, s.to_other - sum(x.volume for x in plant.links if x.src == sub))
    raise ValueError(kind)


def set_flow(plant: Plant, kind: str, sub: str, ref: str, volume: float) -> None:
    """Set one flow to ``volume`` (m3/year); 0 removes it. Raises KeyError for unknown names."""
    if volume < 0 or volume != volume:
        raise ValueError("volume must be >= 0")
    s = _sub(plant, sub)
    if kind == "intake":
        plant.source(ref)
        s.intake.pop(ref, None)
        if volume > 0:
            s.intake[ref] = volume
    elif kind == "discharge":
        plant.outlet(ref)
        s.discharge.pop(ref, None)
        if volume > 0:
            s.discharge[ref] = volume
    elif kind == "evap":
        s.evaporation = volume
    elif kind == "product":
        s.product = volume
    elif kind == "transfer":
        if ref:
            if ref == sub:
                raise ValueError("a subsystem cannot send water to itself")
            _sub(plant, ref)
            plant.links = [x for x in plant.links if not (x.src == sub and x.dst == ref)]
            if volume > 0:
                plant.links.append(Transfer(sub, ref, volume))
            sync_transfers(plant, touched={sub, ref})
        else:   # transfer with no destination: only the R'_out total changes
            linked = sum(x.volume for x in plant.links if x.src == sub)
            s.to_other = linked + volume
    else:
        raise ValueError(kind)


def add_flow(plant: Plant, kind: str, sub: str, ref: str, volume: float) -> None:
    """Add ``volume`` to an existing flow (or create it)."""
    set_flow(plant, kind, sub, ref, current_volume(plant, kind, sub, ref) + volume)


def delete_flow(plant: Plant, kind: str, sub: str, ref: str = "") -> None:
    set_flow(plant, kind, sub, ref, 0.0)

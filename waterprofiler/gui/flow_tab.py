"""Water-flow diagram (Sankey style) and the editor for transfers between subsystems."""

from __future__ import annotations

import copy
from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QImage, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtGui import QAction, QKeySequence, QShortcut
from PySide6.QtWidgets import QHBoxLayout, QFileDialog, QInputDialog, QLabel, QMenu, QToolTip, QWidget

from ..analysis import Analysis
from .. import flow as F
from ..flow import FlowEdge, FlowGraph, FlowNode, build_flow
from ..i18n import Translator
from ..model import Plant, sync_transfers
from .. import periodview as PV
from ..analysis import analyse
from .flow_time import FlowTimeBar
from .tabs import BalancePanel, _Scroll, _message_label
from .theme import COL
from .widgets import DataTable, button, card

LRM = "‎"
EDGE_COLOR = {"intake": "#0A74C8", "discharge": "#64748B", "transfer": "#14B8A6",
              "evap": "#E9892B", "product": "#2E9E4B", "loss": "#D64545"}
NODE_COLOR = {"source": "#0A74C8", "outlet": "#64748B", "evap": "#E9892B", "product": "#2E9E4B",
              "loss": "#D64545", "pool": "#14B8A6"}
STATUS_COLOR = {"balanced": "#0B3D78", "unaccounted loss": "#E9892B", "excess outflow": "#D64545"}
COL_ORDER = {"outlet": 0, "evap": 1, "product": 2, "loss": 3, "pool": 4}


def _num(v: float) -> str:
    return f"{LRM}{v:,.0f}{LRM}"


def labels_for(t: Translator) -> Dict[str, str]:
    return {"evap": t("flow_evap"), "product": t("flow_product"), "loss": t("flow_loss"), "pool": t("flow_pool")}


def paint_flow(p: QPainter, rect: QRectF, g: FlowGraph, rtl: bool, t: Translator,
               edge_hits: Optional[list] = None, hover_edge: int = -1, order_override: Optional[Dict[str, float]] = None,
               port_nodes: Optional[set] = None, targets: Optional[set] = None) -> List[Tuple[QRectF, FlowNode]]:
    """Draw the diagram into ``rect``; returns node hit boxes. ``edge_hits`` receives (path, edge index)."""
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    hits: List[Tuple[QRectF, FlowNode]] = []
    if not g.nodes:
        return hits
    pad_x, pad_y, gap = 150.0, 18.0, 14.0
    sub_w, thin_w = 132.0, 14.0
    left, right = rect.left() + pad_x, rect.right() - pad_x
    top, height = rect.top() + pad_y, rect.height() - 2 * pad_y
    cols: Dict[int, List[FlowNode]] = {0: [], 1: [], 2: []}
    for n in g.nodes:
        cols[n.column].append(n)
    cols[2].sort(key=lambda n: COL_ORDER.get(n.kind, 9))
    keys = dict(g.layout)
    keys.update(order_override or {})
    for c in cols.values():          # a stored / dragged position (if any) overrides the default order
        idx = {n.id: i for i, n in enumerate(c)}
        c.sort(key=lambda n: (keys.get(n.id, 1e9), idx[n.id]))

    def node_h(n: FlowNode, scale: float) -> float:
        return max(n.volume * scale, 44.0 if n.kind == "subsystem" else 16.0)

    total_vol = max(max(sum(n.volume for n in c) for c in cols.values() if c), g.ref_total) or 1.0
    n_max = max(len(c) for c in cols.values())
    scale = max(1e-6, (height - gap * (n_max - 1)) / total_vol)
    for _ in range(10):
        worst = max(sum(node_h(n, scale) for n in c) + gap * (len(c) - 1) for c in cols.values() if c)
        if worst <= height:
            break
        scale *= 0.88
    xs = {0: left, 1: (left + right) / 2 - sub_w / 2, 2: right - thin_w}
    wid = {0: thin_w, 1: sub_w, 2: thin_w}

    def X(x: float, w: float = 0.0) -> float:
        return (rect.left() + rect.right() - x - w) if rtl else x

    box: Dict[str, QRectF] = {}
    for c, nodes in cols.items():
        if not nodes:
            continue
        used = sum(node_h(n, scale) for n in nodes) + gap * (len(nodes) - 1)
        y = top + max(0.0, (height - used) / 2)
        for n in nodes:
            h = node_h(n, scale)
            box[n.id] = QRectF(X(xs[c], wid[c]), y, wid[c], h)
            y += h + gap

    out_used: Dict[str, float] = {}
    in_used: Dict[str, float] = {}
    out_tot: Dict[str, float] = {}
    in_tot: Dict[str, float] = {}
    for e in g.edges:
        out_tot[e.src] = out_tot.get(e.src, 0.0) + e.volume
        in_tot[e.dst] = in_tot.get(e.dst, 0.0) + e.volume

    def thick(n: FlowNode, vol: float, tot: float) -> float:
        return box[n.id].height() * vol / max(tot, n.volume, 1e-9)

    by_id = {n.id: n for n in g.nodes}
    order = {"transfer": 1}
    eidx = {id(e): i for i, e in enumerate(g.edges)}
    for e in sorted(g.edges, key=lambda e: (order.get(e.kind, 0), -e.volume)):
        s, d = by_id[e.src], by_id[e.dst]
        bs, bd = box[s.id], box[d.id]
        ts, td = thick(s, e.volume, out_tot[s.id]), thick(d, e.volume, in_tot[d.id])
        ys = bs.top() + out_used.get(s.id, 0.0)
        yd = bd.top() + in_used.get(d.id, 0.0)
        out_used[s.id] = out_used.get(s.id, 0.0) + ts
        in_used[d.id] = in_used.get(d.id, 0.0) + td
        path = QPainterPath()
        if e.kind == "transfer" and d.kind == "subsystem":
            sign = -1.0 if rtl else 1.0
            x1 = bs.right() if not rtl else bs.left()
            x2 = bd.right() if not rtl else bd.left()
            bulge = sign * (46.0 + 10.0 * (abs(ys - yd) / max(height, 1)) * 10)
            path.moveTo(x1, ys)
            path.cubicTo(x1 + bulge, ys, x2 + bulge, yd, x2, yd)
            path.lineTo(x2, yd + td)
            path.cubicTo(x2 + bulge, yd + td, x1 + bulge, ys + ts, x1, ys + ts)
            path.closeSubpath()
        else:
            if rtl:
                x1, x2 = bs.left(), bd.right()
            else:
                x1, x2 = bs.right(), bd.left()
            mx = (x1 + x2) / 2
            path.moveTo(x1, ys)
            path.cubicTo(mx, ys, mx, yd, x2, yd)
            path.lineTo(x2, yd + td)
            path.cubicTo(mx, yd + td, mx, ys + ts, x1, ys + ts)
            path.closeSubpath()
        col = QColor(EDGE_COLOR[e.kind])
        col.setAlpha(190 if eidx[id(e)] == hover_edge else 110)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(col)
        p.drawPath(path)
        if edge_hits is not None:
            edge_hits.append((path, eidx[id(e)]))

    f = QFont(p.font())
    f.setPixelSize(12)
    p.setFont(f)
    for n in g.nodes:
        b = box[n.id]
        if n.kind == "subsystem":
            c = QColor(STATUS_COLOR.get(n.status, "#0B3D78"))
            grad = QLinearGradient(b.topLeft(), b.bottomLeft())
            grad.setColorAt(0, c.lighter(115))
            grad.setColorAt(1, c)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(grad)
            p.drawRoundedRect(b, 9, 9)
            p.setPen(QColor("#FFFFFF"))
            fb = QFont(f)
            fb.setBold(True)
            p.setFont(fb)
            p.drawText(b.adjusted(4, 3, -4, -b.height() / 2 + 2), Qt.AlignmentFlag.AlignCenter, n.name)
            p.setFont(f)
            p.drawText(b.adjusted(4, b.height() / 2 - 4, -4, -3), Qt.AlignmentFlag.AlignCenter, _num(n.volume) + " m³")
        else:
            c = QColor(NODE_COLOR.get(n.kind, "#64748B"))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(c)
            p.drawRoundedRect(b, 4, 4)
            p.setPen(QColor(COL["text"]))
            label_w = pad_x - 14
            on_left = (n.column == 0) != rtl
            lr = QRectF(b.left() - 6 - label_w, b.center().y() - 18, label_w, 36) if on_left \
                else QRectF(b.right() + 6, b.center().y() - 18, label_w, 36)
            al = Qt.AlignmentFlag.AlignRight if on_left else Qt.AlignmentFlag.AlignLeft
            p.drawText(lr, al | Qt.AlignmentFlag.AlignVCenter | Qt.TextFlag.TextWordWrap, f"{n.name}\n{_num(n.volume)} m³")
        hits.append((b, n))
        if targets and n.id in targets:
            p.setPen(QPen(QColor("#0A74C8"), 2, Qt.PenStyle.DashLine))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(b.adjusted(-4, -4, 4, 4), 9, 9)
        if port_nodes and n.id in port_nodes:
            c = port_center(b, n, rtl)
            p.setPen(QPen(QColor("#FFFFFF"), 2))
            p.setBrush(QColor("#0A74C8"))
            p.drawEllipse(c, 7, 7)
    return hits


PORT_R = 9.0


def port_center(b: QRectF, n: FlowNode, rtl: bool) -> QPointF:
    """Connection handle on the outgoing side of a source / subsystem node."""
    x = b.left() if rtl else b.right()
    return QPointF(x, b.center().y())


def render_png(g: FlowGraph, path: str, rtl: bool, t: Translator, width: int = 1500, height: int = 820) -> bool:
    img = QImage(width, height, QImage.Format.Format_ARGB32)
    img.fill(QColor("#FFFFFF"))
    p = QPainter(img)
    paint_flow(p, QRectF(0, 0, width, height), g, rtl, t)
    p.end()
    return img.save(path)


def render_image(g: FlowGraph, rtl: bool, t: Translator, width: int = 1500, height: int = 820) -> QImage:
    img = QImage(width, height, QImage.Format.Format_ARGB32)
    img.fill(QColor("#FFFFFF"))
    p = QPainter(img)
    paint_flow(p, QRectF(0, 0, width, height), g, rtl, t)
    p.end()
    return img


class FlowDiagram(QWidget):
    """Interactive Sankey: hover, drag nodes to reorder, drag a handle to connect, right-click to edit."""

    node_clicked = Signal(str)
    connect_requested = Signal(str, str)          # source node id, target node id
    edit_requested = Signal(int)                  # edge index
    delete_requested = Signal(int)
    layout_changed = Signal(dict)
    add_flow_requested = Signal(str, str)         # node id, other node id (from the context menu)

    def __init__(self, t: Translator):
        super().__init__()
        self.t = t
        self.graph = FlowGraph()
        self.catalog: Dict[str, List[str]] = {"sources": [], "outlets": [], "subs": []}
        self._hits: List[Tuple[QRectF, FlowNode]] = []
        self._edge_hits: list = []
        self._hover_edge = -1
        self._hover_node: Optional[str] = None
        self._press: Optional[QPointF] = None
        self._press_node: Optional[FlowNode] = None
        self._mode = ""                           # "", "move", "connect"
        self._drag_y: Dict[str, float] = {}
        self._cursor = QPointF()
        self._connect_from: Optional[FlowNode] = None
        self.read_only = False                    # time view: nothing can be changed from the picture
        self.unit = "m³"
        self.setMinimumHeight(540)
        self.setMouseTracking(True)

    def set_graph(self, g: FlowGraph) -> None:
        self.graph = g
        self._hover_edge = -1
        self.update()

    # ------------------------------------------------------------------ painting
    def _targets(self) -> set:
        f = self._connect_from
        if f is None:
            return set()
        out = set()
        for n in self.graph.nodes:
            if n.id == f.id:
                continue
            if f.kind == "source" and n.kind == "subsystem":
                out.add(n.id)
            elif f.kind == "subsystem" and n.kind in ("subsystem", "outlet", "evap", "product"):
                out.add(n.id)
        return out

    def paintEvent(self, _ev) -> None:  # noqa: N802 (Qt API)
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#FFFFFF"))
        ports = set()
        if self._hover_node and self._mode == "":
            ports.add(self._hover_node)
        self._edge_hits = []
        override = dict(self._drag_y) if self._mode == "move" else None
        self._hits = paint_flow(p, QRectF(self.rect()), self.graph, self.t.rtl, self.t, self._edge_hits,
                                self._hover_edge, override, {n for n in ports if self._can_connect(n)}
                                | ({self._connect_from.id} if self._connect_from else set()), self._targets())
        if self._mode == "connect" and self._connect_from is not None:
            box = next((r for r, n in self._hits if n.id == self._connect_from.id), None)
            if box is not None:
                p.setPen(QPen(QColor("#0A74C8"), 3, Qt.PenStyle.DashLine))
                p.drawLine(port_center(box, self._connect_from, self.t.rtl), self._cursor)
        p.end()

    def _can_connect(self, nid: str) -> bool:
        n = next((x for x in self.graph.nodes if x.id == nid), None)
        return n is not None and n.kind in ("source", "subsystem")

    # ------------------------------------------------------------------ hit tests
    def _node_at(self, pos) -> Optional[FlowNode]:
        for r, n in self._hits:
            if r.adjusted(-3, -3, 3, 3).contains(QPointF(pos)):
                return n
        return None

    def _edge_at(self, pos) -> int:
        pt = QPointF(pos)
        for path, i in reversed(self._edge_hits):
            if path.contains(pt):
                return i
        return -1

    def _on_port(self, n: FlowNode, pos) -> bool:
        if n.kind not in ("source", "subsystem"):
            return False
        box = next(r for r, x in self._hits if x.id == n.id)
        c = port_center(box, n, self.t.rtl)
        return (QPointF(pos) - c).manhattanLength() <= PORT_R * 1.6

    def _edge_text(self, i: int) -> str:
        e = self.graph.edges[i]
        a, b = self.graph.node(e.src).name, self.graph.node(e.dst).name
        return f"{a} → {b}: {_num(e.volume)} {self.unit}"

    # ------------------------------------------------------------------ mouse
    def mouseMoveEvent(self, ev) -> None:  # noqa: N802 (Qt API)
        pos = ev.position()
        if self._mode == "connect":
            self._cursor = pos
            self.update()
            return
        if self._mode == "move" and self._press_node is not None:
            self._drag_y = self._new_layout(self._press_node, pos.y())
            self.update()
            return
        if (not self.read_only and self._press is not None and self._press_node is not None
                and (pos - self._press).manhattanLength() > 5):
            self._mode = "move"
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            return
        n = self._node_at(pos)
        hover_node = n.id if n is not None else None
        if n is not None and self._on_port(n, pos) and not self.read_only:
            self.setCursor(Qt.CursorShape.CrossCursor)
            QToolTip.showText(ev.globalPosition().toPoint(), self.t("flow_port_tip"), self)
            hover_edge = -1
        elif n is not None:
            self.setCursor(Qt.CursorShape.PointingHandCursor if n.kind == "subsystem" else Qt.CursorShape.OpenHandCursor)
            QToolTip.showText(ev.globalPosition().toPoint(), f"{n.name}: {_num(n.volume)} {self.unit}", self)
            hover_edge = -1
        else:
            hover_edge = self._edge_at(pos)
            if hover_edge >= 0:
                self.setCursor(Qt.CursorShape.PointingHandCursor)
                QToolTip.showText(ev.globalPosition().toPoint(), self._edge_text(hover_edge), self)
            else:
                self.unsetCursor()
                QToolTip.hideText()
        if hover_edge != self._hover_edge or hover_node != self._hover_node:
            self._hover_edge, self._hover_node = hover_edge, hover_node
            self.update()

    def mousePressEvent(self, ev) -> None:  # noqa: N802 (Qt API)
        if ev.button() != Qt.MouseButton.LeftButton:
            return
        pos = ev.position()
        n = self._node_at(pos)
        self._press, self._press_node = pos, n
        if n is not None and self._on_port(n, pos) and not self.read_only:
            self._mode, self._connect_from, self._cursor = "connect", n, pos
            self.setCursor(Qt.CursorShape.CrossCursor)
            self.update()

    def mouseReleaseEvent(self, ev) -> None:  # noqa: N802 (Qt API)
        pos = ev.position()
        mode, node = self._mode, self._press_node
        src = self._connect_from
        self._mode, self._press, self._press_node, self._connect_from, self._drag_y = "", None, None, None, {}
        self.unsetCursor()
        if mode == "connect" and src is not None:
            tgt = self._node_at(pos)
            self.update()
            if tgt is not None and tgt.id != src.id:
                self.connect_requested.emit(src.id, tgt.id)
        elif mode == "move" and node is not None:
            self.layout_changed.emit(self._new_layout(node, pos.y()))
        elif node is not None and node.kind == "subsystem" and ev.button() == Qt.MouseButton.LeftButton:
            self.node_clicked.emit(node.name)
        self.update()

    def _new_layout(self, node: FlowNode, y: float) -> Dict[str, float]:
        """Sort keys for the dragged node's column so that its order follows the cursor."""
        boxes = {n.id: r.center().y() for r, n in self._hits}
        col = [n for n in self.graph.nodes if n.column == node.column]
        lay = dict(self.graph.layout)
        for n in col:
            lay[n.id] = boxes.get(n.id, 0.0)
        lay[node.id] = y
        return lay

    # ------------------------------------------------------------------ context menu
    def contextMenuEvent(self, ev) -> None:  # noqa: N802 (Qt API)
        t = self.t
        pos = ev.pos()
        n = self._node_at(pos)
        menu = QMenu(self)
        if n is not None:
            if n.kind == "subsystem":
                menu.addAction(t("flow_open")).triggered.connect(lambda: self.node_clicked.emit(n.name))
                for title, kind, names in () if self.read_only else ((t("flow_add_transfer"), "subsystem", [x for x in self.catalog["subs"] if x != n.name]),
                                           (t("flow_add_intake"), "source", self.catalog["sources"]),
                                           (t("flow_add_discharge"), "outlet", self.catalog["outlets"])):
                    if names:
                        sub = menu.addMenu(title)
                        for name in names:
                            act = sub.addAction(name)
                            act.triggered.connect(lambda _=False, k=kind, nm=name: self.add_flow_requested.emit(n.id, f"{k}:{nm}"))
            elif n.kind in ("source", "outlet") and not self.read_only:
                names = [x for x in self.catalog["subs"]]
                if names:
                    sub = menu.addMenu(t("flow_add_to") if n.kind == "source" else t("flow_add_from"))
                    for name in names:
                        sub.addAction(name).triggered.connect(
                            lambda _=False, nm=name: self.add_flow_requested.emit(n.id, f"subsystem:{nm}"))
        else:
            i = -1 if self.read_only else self._edge_at(pos)
            if i >= 0:
                e = self.graph.edges[i]
                if e.editable:
                    menu.addAction(t("flow_edit_volume")).triggered.connect(lambda: self.edit_requested.emit(i))
                    menu.addAction(t("flow_delete")).triggered.connect(lambda: self.delete_requested.emit(i))
                else:
                    a = menu.addAction(t("flow_not_editable"))
                    a.setEnabled(False)
        if not menu.isEmpty():
            menu.exec(ev.globalPos())

    def mouseDoubleClickEvent(self, ev) -> None:  # noqa: N802 (Qt API)
        if not self.read_only and self._node_at(ev.position()) is None:
            i = self._edge_at(ev.position())
            if i >= 0 and self.graph.edges[i].editable:
                self.edit_requested.emit(i)


def _split(nid: str) -> Tuple[str, str]:
    k, _, name = nid.partition(":")
    return k, name


class FlowTab(_Scroll):
    subsystem_requested = Signal(str)
    links_changed = Signal()
    plant_edited = Signal()
    series_requested = Signal()

    def __init__(self, t: Translator):
        super().__init__()
        self.t = t
        self.plant: Optional[Plant] = None
        self._frames: Dict[tuple, tuple] = {}
        self._frame_key: Optional[tuple] = None
        self._analysis: Optional[Analysis] = None
        self._loading = False
        self.msg = _message_label()
        self.lay.addWidget(self.msg)

        self.diagram = FlowDiagram(t)
        self.diagram.node_clicked.connect(self.subsystem_requested)
        self.diagram.connect_requested.connect(self._add_flow)
        self.diagram.add_flow_requested.connect(self._add_flow)
        self.diagram.edit_requested.connect(self._edit_edge)
        self.diagram.delete_requested.connect(self._delete_edge)
        self.diagram.layout_changed.connect(self._layout_changed)
        self._undo: List[dict] = []
        self._owner = None
        legend = QLabel()
        legend.setTextFormat(Qt.TextFormat.RichText)
        items = [("intake", t("flow_l_intake")), ("transfer", t("flow_l_transfer")), ("discharge", t("flow_l_discharge")),
                 ("evap", t("flow_l_evap")), ("product", t("flow_l_product")), ("loss", t("flow_l_loss"))]
        legend.setText("&nbsp;&nbsp;&nbsp;".join(f"<span style='background-color:{EDGE_COLOR[k]}'>&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;</span> {txt}" for k, txt in items))
        legend.setObjectName("cardnote")
        hint = QLabel(t("flow_hint"))
        hint.setObjectName("cardnote")
        hint.setWordWrap(True)
        save = button(t("flow_save"), "", self._save_png)
        self.undo_btn = button(t("flow_undo"), "", self.undo)
        self.undo_btn.setEnabled(False)
        reset = button(t("flow_reset"), "", self._reset_layout)
        self._reset_btn = reset
        bar = QWidget()
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(0, 0, 0, 0)
        for b_ in (self.undo_btn, reset, save):
            bl.addWidget(b_)
        bl.addStretch(1)
        self.view_label = QLabel("")
        self.view_label.setStyleSheet("font-weight:700;")
        self.hint = hint
        self.bar = bar
        self.lay.addWidget(card(t("tab_flow"), self.view_label, self.diagram, legend, hint, bar))
        self.timebar = FlowTimeBar(t)
        self.timebar.changed.connect(self._redraw)
        self.timebar.goto_series.connect(self.series_requested)
        self.lay.addWidget(card(t("ft_title"), self.timebar, note=t("ft_note")))
        self.balance = BalancePanel(t)
        self.lay.addWidget(self.balance)
        sc = QShortcut(QKeySequence.StandardKey.Undo, self)
        sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        sc.activated.connect(self.undo)

        self.table = DataTable([t("flow_from"), t("flow_to"), t("flow_volume")], ["combo", "combo", "num"])
        self.table.setMinimumHeight(170)
        self.table.changed.connect(self._links_edited)
        add = button("+  " + t("add"), "primary", lambda: self.table.add_row([self._names()[0] if self._names() else "", "", 0]))
        rem = button(t("remove"), "danger", self.table.remove_selected)
        self.lay.addWidget(card(t("flow_links"), self.table, add, rem, note=t("flow_links_note")))
        self.lay.addStretch(1)

    # ------------------------------------------------------------------ data
    def _names(self) -> List[str]:
        return [s.name for s in (self.plant.subsystems if self.plant else [])]

    def refresh(self, plant: Plant, a: Optional[Analysis]) -> None:
        fresh = plant is not self._owner
        if fresh:
            self._undo.clear()
            self._owner = plant
            self.undo_btn.setEnabled(False)
        self.plant = plant
        self._analysis = a
        names = self._names()
        self.diagram.catalog = {"sources": [x.name for x in plant.sources], "outlets": [x.name for x in plant.outlets],
                                "subs": names}
        self.table.combo_items = {0: names, 1: names}
        self._loading = True
        self.table.set_rows([[x.src, x.dst, x.volume] for x in plant.links])
        self._loading = False
        self.timebar.set_plant(plant, fresh)
        self._redraw()

    def set_analysis(self, a: Optional[Analysis]) -> None:
        """Base-model analysis changed (an edit elsewhere): show it, or recompute the open period."""
        self._analysis = a
        if self.plant is not None:
            self._redraw()

    update_analysis = set_analysis

    def _redraw(self, *_a) -> None:
        period = self.timebar.period() if self.plant is not None else None
        editable = period is None
        self.diagram.read_only = not editable
        self.table.setEnabled(editable)
        self.bar.setEnabled(True)
        for w in (self.undo_btn, self._reset_btn):
            w.setEnabled(editable and (w is not self.undo_btn or bool(self._undo)))
        if self._analysis is None or not self.plant or not self.plant.subsystems:
            self.msg.setText(self.t("no_data"))
            self.diagram.set_graph(FlowGraph())
            self.view_label.setText("")
            return
        self.msg.setText("")
        if period is None:
            self.diagram.unit = "m³"
            self.view_label.setText(self.t("ft_view_base"))
            self.balance.set_unit("m³")
            self.diagram.set_graph(build_flow(self._analysis, labels_for(self.t)))
            self.balance.refresh(self._analysis)
            self.timebar.set_info(self.t("ft_info_base") if self.timebar.has_data() else self.t("ft_no_series"))
            self._strip()
            return
        frame = self._frame(self.timebar.index)
        self._strip()
        if frame is None:
            return
        g, a, info = frame
        annual = self.timebar.basis.currentData() == "annual"
        unit = self.t("ft_unit_annual") if annual else self.t("ft_unit_period")
        self.diagram.unit = unit
        self.view_label.setText(self.t("ft_view_period", label=period.label, start=str(period.start), end=str(period.end), days=info.days))
        self.balance.set_unit(unit)
        if info.empty or a is None:
            self.diagram.set_graph(FlowGraph())
            self.balance.show_error(self.t("ft_empty"))
            self.timebar.set_info(self.t("ft_empty"))
            return
        self.diagram.set_graph(g)
        self.balance.refresh(a)
        self.timebar.set_info(self.t("ft_info", applied=info.applied, total=info.total_vars, est=info.estimated, days=info.days))

    def _key(self) -> tuple:
        tb = self.timebar
        return (hash(repr(self.plant.to_dict())), tb.cal_combo.currentData(), tb.step.currentData(),
                str(tb.start.date()), str(tb.end.date()), tb.basis.currentData(), tb.fill.currentData(), self.t.lang)

    def _frame(self, i: int):
        """(graph, analysis, info) of slice ``i``; all slices are computed once so the picture scale is shared."""
        key = self._key()
        if key != self._frame_key:
            self._frames = {}
            self._frame_key = key
            tb = self.timebar
            labels = labels_for(self.t)
            for j, s in enumerate(tb.slices):
                pp, info = PV.period_plant(self.plant, s.start, s.end, tb.fill.currentData(), tb.basis.currentData())
                a = g = None
                if not info.empty:
                    try:
                        a = analyse(pp)
                        g = build_flow(a, labels)
                    except Exception:  # noqa: BLE001 - one bad period must not stop the animation
                        a = g = None
                        info.empty = True
                self._frames[j] = (g, a, info)
            ref = 0.0
            for g, _a2, _i in self._frames.values():
                if g is not None:
                    for col in {n.column for n in g.nodes}:
                        ref = max(ref, sum(n.volume for n in g.nodes if n.column == col))
            for g, _a2, _i in self._frames.values():
                if g is not None:
                    g.ref_total = ref
        return self._frames.get(i)

    def _strip(self) -> None:
        tb = self.timebar
        vals = []
        if tb.mode.currentData() == "period" and self.plant is not None:
            self._frame(tb.index)
            for j in range(len(tb.slices)):
                f = self._frames.get(j)
                vals.append(None if f is None or f[1] is None else f[1].balance.sum_intake)
        tb.set_strip(vals)

    def show_error(self, text: str) -> None:
        self.msg.setText(text)
        self.balance.show_error(text)

    def _links_edited(self) -> None:
        if self._loading or self.plant is None:
            return
        from ..model import Transfer
        old = {x.src for x in self.plant.links} | {x.dst for x in self.plant.links}
        links = []
        for src, dst, vol in self.table.rows():
            if src and dst and src != dst and vol and vol > 0:
                links.append(Transfer(src, dst, float(vol)))
        self.plant.links = links
        sync_transfers(self.plant, touched=old)
        self.links_changed.emit()

    def _save_png(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, self.t("flow_save"), "flow.png", "PNG (*.png)")
        if path:
            render_png(self.diagram.graph, path, self.t.rtl, self.t)

    # ------------------------------------------------------------------ interactive editing
    def _mutate(self, fn) -> bool:
        """Apply ``fn(plant)`` with an undo snapshot; on error restore and show the message."""
        if self.plant is None:
            return False
        snap = copy.deepcopy(self.plant.to_dict())
        try:
            fn(self.plant)
        except (KeyError, ValueError) as e:
            self._restore(snap)
            self.msg.setText(self.t("missing_ref", name=str(e).strip("'\"")) if isinstance(e, KeyError) else str(e))
            return False
        self.msg.setText("")
        self._undo.append(snap)
        del self._undo[:-40]
        self.undo_btn.setEnabled(True)
        self.plant_edited.emit()
        return True

    def _restore(self, snap: dict) -> None:
        new = Plant.from_dict(snap)
        self.plant.__dict__.update(new.__dict__)

    def undo(self) -> None:
        if not self._undo or self.plant is None:
            return
        self._restore(self._undo.pop())
        self.undo_btn.setEnabled(bool(self._undo))
        self.plant_edited.emit()

    def _ask(self, title: str, default: float) -> Optional[float]:
        v, ok = QInputDialog.getDouble(self, title, self.t("flow_volume_prompt"), float(default), 0.0, 1e12, 1)
        return v if ok else None

    def _edit_edge(self, i: int) -> None:
        g = self.diagram.graph
        if self.plant is None or not (0 <= i < len(g.edges)):
            return
        e = g.edges[i]
        v = self._ask(self.diagram._edge_text(i).split(":")[0], e.volume)
        if v is not None:
            self._mutate(lambda p: F.set_flow(p, e.kind, e.sub, e.ref, v))

    def _delete_edge(self, i: int) -> None:
        g = self.diagram.graph
        if self.plant is None or not (0 <= i < len(g.edges)):
            return
        e = g.edges[i]
        self._mutate(lambda p: F.delete_flow(p, e.kind, e.sub, e.ref))

    def _add_flow(self, a_id: str, b_id: str) -> None:
        """Create or increase a flow between two diagram nodes (dragged handle or context menu)."""
        if self.plant is None:
            return
        ka, na = _split(a_id)
        kb, nb = _split(b_id)
        if ka in ("outlet", "evap", "product") or (ka == "subsystem" and kb == "source"):
            ka, na, kb, nb = kb, nb, ka, na
        if ka == "source" and kb == "subsystem":
            kind, sub, ref = "intake", nb, na
        elif ka == "subsystem" and kb == "subsystem":
            kind, sub, ref = "transfer", na, nb
        elif ka == "subsystem" and kb == "outlet":
            kind, sub, ref = "discharge", na, nb
        elif ka == "subsystem" and kb in ("evap", "product"):
            kind, sub, ref = kb, na, ""
        else:
            self.msg.setText(self.t("flow_bad_link"))
            return
        default = F.current_volume(self.plant, kind, sub, ref) if kind in ("evap", "product") else 0.0
        v = self._ask(f"{sub}", default)
        if v is None or v <= 0:
            return
        if kind in ("evap", "product"):
            self._mutate(lambda p: F.set_flow(p, kind, sub, ref, v))
        else:
            self._mutate(lambda p: F.add_flow(p, kind, sub, ref, v))

    def _layout_changed(self, layout: dict) -> None:
        self._mutate(lambda p: setattr(p, "layout", dict(layout)))

    def _reset_layout(self) -> None:
        if self.plant is not None and self.plant.layout:
            self._mutate(lambda p: setattr(p, "layout", {}))

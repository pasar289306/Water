"""Analysis tabs: balance, true cost, savings, reconciliation."""

from __future__ import annotations

from typing import Callable, List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QFormLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel,
    QScrollArea, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from .. import savings as sv
from ..analysis import Analysis
from ..cost import COMPONENTS
from ..estimators import InputError
from ..i18n import Translator
from ..messages import localize, localize_all
from .. import measures as _ms
from ..model import Plant, Subsystem
from ..reconcile import ReconcileResult, reconcile
from .theme import COL
from .widgets import BarChart, StatCard, button, card, dspin

RIGHT = Qt.AlignmentFlag.AlignCenter
WARN_BG = QColor("#FFF1D6")
BAD_BG = QColor("#FBDADA")


def _fmt(v: Optional[float], d: int = 0) -> str:
    return "—" if v is None else f"{v:,.{d}f}"


def _pct(v: Optional[float]) -> str:
    return "—" if v is None else f"{100 * v:.1f}%"


def make_table(headers: List[str]) -> QTableWidget:
    tb = QTableWidget(0, len(headers))
    tb.setHorizontalHeaderLabels(headers)
    tb.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
    tb.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    tb.setAlternatingRowColors(True)
    tb.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    tb.setShowGrid(False)
    tb.verticalHeader().setVisible(False)
    tb.verticalHeader().setDefaultSectionSize(36)
    return tb


def stat_row(*cards: StatCard) -> QHBoxLayout:
    row = QHBoxLayout()
    row.setSpacing(14)
    for c in cards:
        row.addWidget(c, 1)
    return row


class _MessageLabel(QLabel):
    """Warning line that takes no space while it is empty."""

    def __init__(self):
        super().__init__()
        self.setObjectName("msg")
        self.setWordWrap(True)
        self.setVisible(False)

    def setText(self, text: str) -> None:  # noqa: N802 (Qt API)
        super().setText(text)
        self.setVisible(bool(text))


def _message_label() -> QLabel:
    return _MessageLabel()


def _warn_label() -> QLabel:
    lab = QLabel()
    lab.setObjectName("warnbox")
    lab.setWordWrap(True)
    lab.setVisible(False)
    return lab


def _set_visible_text(lab: QLabel, text: str) -> None:
    lab.setText(text)
    lab.setVisible(bool(text))

LRM = "\u200e"   # left-to-right mark. Qt's text engine ignores the newer isolates (U+2066..2069),
                 # which left signs on the wrong side of numbers ("950+"); a leading LRM makes the
                 # digits, the sign and the parentheses that follow resolve as one LTR run.


def ltr(text: str) -> str:
    return LRM + text + LRM


def put(tb: QTableWidget, r: int, c: int, text: str, number: bool = False, bg: Optional[QColor] = None, bold: bool = False) -> None:
    it = QTableWidgetItem(ltr(text) if number else text)
    if number:
        it.setTextAlignment(RIGHT)
    if bg is not None:
        it.setBackground(bg)
    if bold:
        f = it.font()
        f.setBold(True)
        it.setFont(f)
    tb.setItem(r, c, it)


class ResultLabel(QLabel):
    """Green result box that only shows while it has text."""

    def __init__(self):
        super().__init__()
        self.setObjectName("result")
        self.setWordWrap(True)
        self.setVisible(False)

    def setText(self, text: str) -> None:  # noqa: N802 (Qt API)
        super().setText(text)
        self.setVisible(bool(text))


class _Scroll(QScrollArea):
    def __init__(self):
        super().__init__()
        self.setWidgetResizable(True)
        self.setFrameShape(QScrollArea.Shape.NoFrame)
        self.body = QWidget()
        self.setWidget(self.body)
        self.lay = QVBoxLayout(self.body)
        self.lay.setContentsMargins(26, 22, 26, 26)
        self.lay.setSpacing(16)


class BalancePanel(QWidget):
    """Water balance cards, tables and chart. Lives on the flow page; ``set_unit`` switches m3/year <-> period."""

    def __init__(self, t: Translator):
        super().__init__()
        self.t = t
        self.unit = "m³"
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(14)
        self.msg = _message_label()
        self.lay.addWidget(self.msg)
        self.c_intake = StatCard(t("sum_intake") + " (m³)", COL["blue"])
        self.c_disch = StatCard(t("sum_discharge") + " (m³)", COL["navy"])
        self.c_cons = StatCard(t("series_consumptive") + " (m³)", COL["green"])
        self.c_loss = StatCard(t("unknown_loss") + " (m³)", COL["orange"])
        self.lay.addLayout(stat_row(self.c_intake, self.c_disch, self.c_cons, self.c_loss))
        self.table = make_table([t("subsystem"), t("inflow"), t("outflow"), t("unknown_loss"), t("loss_pct"), t("status")])
        self.table.setMinimumHeight(230)
        self.lay.addWidget(card(t("tab_balance") + " — " + t("subsystem"), self.table))
        self.totals_box = QGroupBox(t("plant_totals"))
        self.totals = QFormLayout(self.totals_box)
        self.totals.setHorizontalSpacing(30)
        self.totals.setVerticalSpacing(8)
        self.lay.addWidget(self.totals_box)
        self.chart = BarChart()
        self.chart.setMinimumHeight(300)
        self.lay.addWidget(card("", self.chart))
        self.warn = _warn_label()
        self.lay.addWidget(self.warn)

    def set_unit(self, unit: str) -> None:
        """Unit shown in card titles and in the totals list (e.g. "m³", "m³ in the period")."""
        t = self.t
        self.unit = unit
        for c, key in ((self.c_intake, "sum_intake"), (self.c_disch, "sum_discharge"),
                       (self.c_cons, "series_consumptive"), (self.c_loss, "unknown_loss")):
            c.title.setText(f"{t(key)} ({unit})")

    def show_error(self, text: str) -> None:
        self.msg.setText(text)
        self.table.setRowCount(0)
        self.chart.set_data("", [], [])
        for c in (self.c_intake, self.c_disch, self.c_cons, self.c_loss):
            c.set("—")
        while self.totals.rowCount():
            self.totals.removeRow(0)
        _set_visible_text(self.warn, "")

    def refresh(self, a: Analysis) -> None:
        t, b = self.t, a.balance
        self.msg.setText("" if a.plant.subsystems else t("no_data"))
        self.c_intake.set(_fmt(b.sum_intake))
        self.c_disch.set(_fmt(b.sum_discharge))
        self.c_cons.set(_fmt(b.sum_evaporation + b.sum_product))
        pct = (100.0 * b.unknown_loss / b.sum_intake) if b.sum_intake else 0.0
        bad = abs(pct) > 2.0
        self.c_loss.set(_fmt(b.unknown_loss), f"{pct:.1f}%", COL["red"] if bad else COL["green"])
        self.table.setRowCount(len(b.subsystems))
        for r, s in enumerate(b.subsystems):
            bg = None if s.status == "balanced" else (WARN_BG if s.status == "unaccounted loss" else BAD_BG)
            put(self.table, r, 0, s.name)
            put(self.table, r, 1, _fmt(s.inflow), True)
            put(self.table, r, 2, _fmt(s.outflow), True)
            put(self.table, r, 3, _fmt(s.unknown_loss), True, bg)
            put(self.table, r, 4, f"{s.unknown_loss_pct_of_inflow:.1f}", True, bg)
            put(self.table, r, 5, t("st_" + s.status), False, bg)

        while self.totals.rowCount():
            self.totals.removeRow(0)
        rows = [
            (t("sum_intake"), _fmt(b.sum_intake)), (t("sum_discharge"), _fmt(b.sum_discharge)),
            (t("sum_evap"), _fmt(b.sum_evaporation)), (t("sum_product"), _fmt(b.sum_product)),
            (t("unknown_loss"), _fmt(b.unknown_loss)),
            (t("metered_intake"), _fmt(b.metered_intake)), (t("metered_discharge"), _fmt(b.metered_discharge)),
            (t("src_gap"), _fmt(b.source_side_imbalance)), (t("out_gap"), _fmt(b.discharge_side_imbalance)),
            (t("closure"), "—" if b.closure_pct is None else f"{b.closure_pct:.1f}%"),
        ]
        for lab, val in rows:
            lbl = QLabel(ltr(val))
            lbl.setAlignment(RIGHT)
            lbl.setStyleSheet("font-weight:600;")
            self.totals.addRow(f"{lab} ({self.unit})" if "%" not in val else lab, lbl)

        names = [s.name for s in b.subsystems]
        sub = {s.name: s for s in a.plant.subsystems}
        self.chart.set_data(
            t("chart_title_balance"), names,
            [(t("series_intake"), [sub[n].total_intake for n in names]),
             (t("series_consumptive"), [sub[n].evaporation + sub[n].product for n in names]),
             (t("series_discharge"), [sub[n].total_discharge for n in names])])
        _set_visible_text(self.warn, "\n".join("⚠ " + w for w in localize_all(b.warnings, t.lang)))


BalanceTab = BalancePanel          # old name


class CostTab(_Scroll):
    def __init__(self, t: Translator):
        super().__init__()
        self.t = t
        self.msg = _message_label()
        self.lay.addWidget(self.msg)
        self.c_true = StatCard(t("tab_cost"), COL["navy"])
        self.c_direct = StatCard(t("direct_cost"), COL["blue"])
        self.c_ratio = StatCard(t("true_direct"), COL["orange"])
        self.c_m3 = StatCard(t("cost_per_m3"), COL["green"])
        self.lay.addLayout(stat_row(self.c_true, self.c_direct, self.c_ratio, self.c_m3))
        heads = [t("subsystem")] + [t("comp_" + c) for c in COMPONENTS] + [
            t("total"), t("cost_per_m3"), t("water_share"), t("cost_share"), t("priority")]
        self.table = make_table(heads)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.setStyleSheet("QHeaderView::section{padding:9px 4px; font-size:11px;} QTableWidget::item{padding:4px 3px;}")
        self.table.setMinimumHeight(250)
        self.lay.addWidget(card(t("tab_cost"), self.table))
        self.kpi = make_table([t("indicator"), t("value")])
        self.kpi.setMinimumHeight(380)
        self.lay.addWidget(card(t("kpi"), self.kpi))
        self.chart_share = BarChart()
        self.chart_share.setMinimumHeight(290)
        self.chart_comp = BarChart()
        self.chart_comp.setMinimumHeight(310)
        self.lay.addWidget(card("", self.chart_share))
        self.lay.addWidget(card("", self.chart_comp))
        self.sugg = make_table([t("ms_measure"), t("pl_target"), t("ms_use"), t("ms_est"), t("pl_val_col")])
        self.sugg.setMinimumHeight(330)
        self.lay.addWidget(card(t("cost_suggest"), self.sugg, note=t("cost_suggest_note")))
        self.lay.addStretch(1)

    def _fill_suggestions(self, plant: Plant) -> None:
        t = self.t
        est = _ms.recommend(plant, limit=8)
        self.sugg.setRowCount(len(est))
        for r, e in enumerate(est):
            m = e.measure
            put(self.sugg, r, 0, f"{m.id}. {m.name(t.lang)}")
            put(self.sugg, r, 1, e.target or t("ms_plant_wide"))
            put(self.sugg, r, 2, _ms.range_text(m.use, m, t.lang), number=True)
            put(self.sugg, r, 3, f"{_fmt(e.low)} – {_fmt(e.high)}", number=True)
            put(self.sugg, r, 4, f"{_fmt(e.value_low)} – {_fmt(e.value_high)}", number=True)

    def show_error(self, text: str) -> None:
        self.msg.setText(text)
        self.table.setRowCount(0)
        self.kpi.setRowCount(0)
        for c in (self.c_true, self.c_direct, self.c_ratio, self.c_m3):
            c.set("—")

    def refresh(self, a: Analysis) -> None:
        t, c, k = self.t, a.cost, a.kpi
        cur = a.plant.currency
        self.msg.setText("" if a.plant.subsystems else t("no_data"))
        self._fill_suggestions(a.plant)
        self.c_true.set(_fmt(c.total), cur)
        self.c_direct.set(_fmt(c.direct_billed_cost), cur)
        self.c_ratio.set("—" if c.true_to_direct_ratio is None else f"×{c.true_to_direct_ratio:.2f}",
                         t("true_direct_hint"))
        self.c_m3.set(_fmt(c.true_cost_per_m3_intake, 2), f"{cur} / m³")
        rank = {r.name: r for r in a.ranking}
        n = len(c.subsystems)
        self.table.setRowCount(n + 1)
        for r, sc in enumerate(c.subsystems):
            rk = rank[sc.name]
            put(self.table, r, 0, sc.name)
            for j, comp in enumerate(COMPONENTS, 1):
                put(self.table, r, j, _fmt(sc.components[comp]), True)
            m = len(COMPONENTS)
            put(self.table, r, m + 1, _fmt(sc.total), True, bold=True)
            put(self.table, r, m + 2, _fmt(sc.per_m3_intake, 2), True)
            put(self.table, r, m + 3, _pct(rk.intake_share), True)
            put(self.table, r, m + 4, _pct(rk.cost_share), True)
            bg = None if rk.priority == "low" else WARN_BG
            put(self.table, r, m + 5, t("pr_" + rk.priority), False, bg)
        put(self.table, n, 0, t("total"), bold=True)
        for j, comp in enumerate(COMPONENTS, 1):
            put(self.table, n, j, _fmt(c.by_component[comp]), True, bold=True)
        put(self.table, n, len(COMPONENTS) + 1, _fmt(c.total), True, bold=True)
        for j in range(len(COMPONENTS) + 2, len(COMPONENTS) + 6):
            put(self.table, n, j, "")

        rows = [
            (f"{t('direct_cost')} ({cur})", _fmt(c.direct_billed_cost)),
            (f"{t('tab_cost')} ({cur})", _fmt(c.total)),
            (t("true_direct"), "—" if c.true_to_direct_ratio is None else f"{c.true_to_direct_ratio:.2f}"),
            (f"{t('cost_per_m3')} ({cur})", _fmt(c.true_cost_per_m3_intake, 2)),
            (t("kpi_per_unit"), _fmt(k.intake_per_unit, 3)),
            (f"{t('kpi_cost_unit')} ({cur})", _fmt(k.true_cost_per_unit, 2)),
            (t("kpi_recirc"), _pct(k.recirculation_ratio)),
            (t("kpi_consumptive"), _pct(k.consumptive_fraction)),
            (t("kpi_discharge"), _pct(k.discharge_fraction)),
            (t("kpi_unacc"), _pct(k.unaccounted_fraction)),
        ]
        self.kpi.setRowCount(len(rows))
        for r, (lab, val) in enumerate(rows):
            put(self.kpi, r, 0, lab)
            put(self.kpi, r, 1, val, True)

        names = [sc.name for sc in c.subsystems]
        self.chart_share.set_data(
            t("chart_title_cost"), names,
            [(t("water_share"), [c.water_share[x] for x in names]), (t("cost_share"), [c.cost_share[x] for x in names])],
            percent=True)
        self.chart_comp.set_data(
            f"{t('chart_title_comp')} ({cur})", names,
            [(t("comp_" + comp), [sc.components[comp] for sc in c.subsystems]) for comp in COMPONENTS])


def _weighted_costs(plant: Plant, s: Subsystem):
    """Return (water $/m3, discharge $/m3, treatment $/m3 of intake) for a subsystem."""
    smap = {x.name: x for x in plant.sources}
    omap = {x.name: x for x in plant.outlets}
    tmap = {x.name: x for x in plant.treatments}
    vi = s.total_intake
    vd = s.total_discharge
    water = sum(smap[k].unit_cost * v for k, v in s.intake.items() if k in smap) / vi if vi > 0 else 0.0
    disc = sum(omap[k].unit_cost * v for k, v in s.discharge.items() if k in omap) / vd if vd > 0 else 0.0
    treat = sum(tmap[k].unit_cost * f for k, f in s.water_treatments.items() if k in tmap)
    return water, disc, treat


class SavingsTab(_Scroll):
    def __init__(self, t: Translator):
        super().__init__()
        self.t = t
        self.plant: Optional[Plant] = None

        # ---- cooling tower
        self.ct_box = QGroupBox(t("sv_ct"))
        f = QFormLayout(self.ct_box)
        self.ct_sub = QComboBox()
        self.ct_new = dspin(maximum=100, decimals=2, minimum=1.01, step=0.5)
        self.ct_new.setValue(6.0)
        self.ct_capex = dspin(decimals=2)
        self.ct_out = ResultLabel()
        b1 = button(t("calculate"), "primary")
        b1.clicked.connect(self._calc_ct)
        f.addRow(t("subsystem"), self.ct_sub)
        f.addRow(t("sv_new_coc"), self.ct_new)
        f.addRow(t("sv_capex"), self.ct_capex)
        f.addRow(b1)
        f.addRow(self.ct_out)
        self.lay.addWidget(self.ct_box)

        # ---- boiler
        self.bo_box = QGroupBox(t("sv_boiler"))
        f = QFormLayout(self.bo_box)
        self.bo_sub = QComboBox()
        self.bo_coc = dspin(maximum=200, decimals=2, minimum=1.01, step=1)
        self.bo_coc.setValue(20.0)
        self.bo_cond = dspin(maximum=100, decimals=1, step=5)
        self.bo_cond.setValue(70.0)
        self.bo_capex = dspin(decimals=2)
        self.bo_out = ResultLabel()
        b2 = button(t("calculate"), "primary")
        b2.clicked.connect(self._calc_boiler)
        f.addRow(t("subsystem"), self.bo_sub)
        f.addRow(t("sv_new_coc"), self.bo_coc)
        f.addRow(t("sv_cond"), self.bo_cond)
        f.addRow(t("sv_capex"), self.bo_capex)
        f.addRow(b2)
        f.addRow(self.bo_out)
        self.lay.addWidget(self.bo_box)

        # ---- leaks
        box = QGroupBox(t("sv_leak"))
        f = QFormLayout(box)
        self.lk_sub = QComboBox()
        self.lk_rate = dspin(decimals=3, step=0.1)
        self.lk_hours = dspin(maximum=8784, decimals=0)
        self.lk_hours.setValue(8000)
        self.lk_frac = dspin(maximum=1.0, decimals=2, step=0.1)
        self.lk_frac.setValue(1.0)
        self.lk_capex = dspin(decimals=2)
        self.lk_out = ResultLabel()
        b3 = button(t("calculate"), "primary")
        b3.clicked.connect(self._calc_leak)
        f.addRow(t("subsystem"), self.lk_sub)
        f.addRow(t("sv_leak_rate"), self.lk_rate)
        f.addRow(t("sv_leak_hours"), self.lk_hours)
        f.addRow(t("sv_fixed"), self.lk_frac)
        f.addRow(t("sv_capex"), self.lk_capex)
        f.addRow(b3)
        f.addRow(self.lk_out)
        self.lay.addWidget(box)
        self.lay.addStretch(1)

    def refresh(self, plant: Plant) -> None:
        self.plant = plant
        for combo, kind in ((self.ct_sub, "cooling_tower"), (self.bo_sub, "boiler"), (self.lk_sub, None)):
            cur = combo.currentText()
            combo.clear()
            for s in plant.subsystems:
                if kind is None or s.kind == kind:
                    combo.addItem(s.name)
            if cur:
                combo.setCurrentText(cur)

    def _sub(self, combo: QComboBox) -> Optional[Subsystem]:
        if self.plant is None:
            return None
        for s in self.plant.subsystems:
            if s.name == combo.currentText():
                return s
        return None

    def _report(self, label: QLabel, r: sv.SavingsResult, capex: float, cur: str) -> None:
        t = self.t
        lines = [f"{t('sv_water_saved')}: {r.makeup_savings:,.0f}"]
        if r.blowdown_savings is not None:
            lines.append(f"{t('sv_wastewater_saved')}: {r.blowdown_savings:,.0f}")
        if r.cost_savings is not None:
            lines.append(f"{t('sv_cost_saved')}: {r.cost_savings:,.0f} {cur}")
            if capex > 0:
                pb = sv.simple_payback_years(capex, r.cost_savings)
                lines.append(f"{t('sv_payback')}: " + ("—" if pb is None else f"{pb:.2f}"))
        label.setText("\n".join(lines))

    def _calc_ct(self) -> None:
        t, s = self.t, self._sub(self.ct_sub)
        if s is None:
            self.ct_out.setText(t("sv_none"))
            return
        if s.evaporation <= 0 or s.total_intake <= 0:
            self.ct_out.setText(t("sv_need_estimate"))
            return
        w, d, tr = _weighted_costs(self.plant, s)
        try:
            r = sv.cooling_tower_coc(s.evaporation, s.total_intake, self.ct_new.value(), w, d, tr)
        except InputError as e:
            self.ct_out.setText(localize(str(e), t.lang))
            return
        self._report(self.ct_out, r, self.ct_capex.value(), self.plant.currency)

    def _calc_boiler(self) -> None:
        t, s = self.t, self._sub(self.bo_sub)
        if s is None:
            self.bo_out.setText(t("sv_none"))
            return
        p = s.params
        if not all(k in p for k in ("hours", "load", "bhp")):
            self.bo_out.setText(t("sv_need_estimate"))
            return
        w, d, tr = _weighted_costs(self.plant, s)
        try:
            r = sv.boiler_coc_and_condensate(
                s.total_intake, p["hours"], p["load"], self.bo_coc.value(), self.bo_cond.value(),
                bhp=p["bhp"], baseline_blowdown_m3=s.total_discharge,
                water_unit_cost=w, discharge_unit_cost=d, treatment_unit_cost=tr)
        except InputError as e:
            self.bo_out.setText(localize(str(e), t.lang))
            return
        self._report(self.bo_out, r, self.bo_capex.value(), self.plant.currency)

    def _calc_leak(self) -> None:
        t, s = self.t, self._sub(self.lk_sub)
        if s is None:
            self.lk_out.setText(t("sv_none"))
            return
        w, d, _tr = _weighted_costs(self.plant, s)
        r = sv.leak_repair(self.lk_rate.value(), self.lk_hours.value(), self.lk_frac.value(), w, d)
        self._report(self.lk_out, r, self.lk_capex.value(), self.plant.currency)


class ReconcileTab(_Scroll):
    applied = Signal(object)    # emits the reconciled Plant

    def __init__(self, t: Translator):
        super().__init__()
        self.t = t
        self.plant: Optional[Plant] = None
        self.result: Optional[ReconcileResult] = None
        self.lay.addWidget(card(t("tab_reconcile"), note=t("rc_note")))
        form = QFormLayout()
        self.sigma = dspin(maximum=100, decimals=1, minimum=0.1, step=1)
        self.sigma.setValue(15.0)
        form.addRow(t("rc_sigma"), self.sigma)
        row = QHBoxLayout()
        self.run = button(t("rc_run"), "primary")
        self.apply = button(t("rc_apply"))
        self.apply.setEnabled(False)
        self.run.clicked.connect(self._run)
        self.apply.clicked.connect(self._apply)
        row.addWidget(self.run)
        row.addWidget(self.apply)
        row.addStretch(1)
        ctl = QWidget()
        cl = QVBoxLayout(ctl)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.addLayout(form)
        cl.addLayout(row)
        self.lay.addWidget(card("", ctl))
        self.info = QLabel()
        self.info.setObjectName("result")
        self.info.setWordWrap(True)
        self.info.setVisible(False)
        self.lay.addWidget(self.info)
        self.table = make_table([t("subsystem"), t("rc_flow"), t("rc_orig"), t("rc_adj"), t("rc_delta")])
        self.table.setMinimumHeight(450)
        self.lay.addWidget(card(t("rc_adj"), self.table))
        self.lay.addStretch(1)

    def _say(self, text: str) -> None:
        self.info.setText(text)
        self.info.setVisible(bool(text))

    def refresh(self, plant: Plant) -> None:
        self.plant = plant

    def _flow_label(self, key: str) -> str:
        t = self.t
        if ":" in key:
            head, name = key.split(":", 1)
            return f"{t('intake') if head == 'intake' else t('discharge')} — {name}"
        return {"from_other": t("from_other"), "to_other": t("to_other"),
                "product": t("product"), "evaporation": t("evaporation")}.get(key, key)

    def _run(self) -> None:
        t = self.t
        self.result = None
        self.apply.setEnabled(False)
        self.table.setRowCount(0)
        if self.plant is None or not self.plant.subsystems:
            self._say(t("no_data"))
            return
        try:
            res = reconcile(self.plant, default_rel_sigma=self.sigma.value() / 100.0)
        except Exception as e:  # noqa: BLE001 - surfaced to the user
            self._say(f"{t('analysis_error')}: {localize(str(e), t.lang)}")
            return
        self.result = res
        lines = [f"{t('rc_chi')}: {ltr(f'{res.chi_square:,.2f}')}",
                 f"{t('rc_resid')}: {ltr(f'{res.residual_before:,.1f}')}  /  {ltr(f'{res.residual_after:.3g}')}"]
        if not res.adjustments:
            lines.append(t("rc_none"))
        self._say("\n".join(lines))
        self.table.setRowCount(len(res.adjustments))
        for r, adj in enumerate(res.adjustments):
            pct = adj.delta_pct
            put(self.table, r, 0, adj.subsystem)
            put(self.table, r, 1, self._flow_label(adj.flow))
            put(self.table, r, 2, _fmt(adj.original), True)
            put(self.table, r, 3, _fmt(adj.adjusted), True)
            put(self.table, r, 4, f"{adj.delta:+,.0f}" + ("" if pct is None else f"  ({pct:+.1f}%)"), True)
        self.apply.setEnabled(bool(res.adjustments))

    def _apply(self) -> None:
        if self.result is not None:
            self.applied.emit(self.result.plant)
            self.result = None
            self.apply.setEnabled(False)
            self.table.setRowCount(0)
            self._say("")

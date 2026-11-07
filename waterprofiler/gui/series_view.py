"""Time series where the data lives: one shared time context, a table + chart panel for any group of variables,
and the time settings (calendar, slices, window, files).

* ``TimeContext``  - calendar, slice step, range and chart style shared by every panel of the application.
* ``ChartView``    - bars or a line (the user's choice, one choice for all charts).
* ``SeriesGrid``   - editable table (period x variables, paste from Excel, delete) with a footer and a chart.
* ``SeriesPanel``  - a collapsible ``SeriesGrid`` placed under the fields it belongs to.
* ``TimeSettings`` - the settings page section: calendar, step, range, reference window, data map, Excel / CSV.
"""

from __future__ import annotations

import csv
from datetime import date
from typing import Callable, Dict, List, Optional, Sequence

from PySide6.QtCore import QObject, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication, QKeySequence, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QFileDialog, QHBoxLayout, QHeaderView, QLabel, QMessageBox, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from .. import jcalendar as cal
from .. import timeseries as TS
from .. import ts_excel as XL
from ..i18n import Translator
from ..model import Plant
from ..numparse import parse_float
from .tabs import WARN_BG, _Scroll, make_table, put
from .theme import COL
from .timeseries_tab import CalDate
from .widgets import _short, button, card

MAX_SLICES = 600
CHART_MODES = ("bar", "line")


def fmt(v: Optional[float]) -> str:
    if v is None:
        return ""
    return f"{v:,.0f}" if abs(v) >= 1000 else f"{v:.6g}"


# ---------------------------------------------------------------------------------------------- context
class TimeContext(QObject):
    """The time axis every series panel uses. Kept in the project (``plant.ts_view``, ``plant.site.calendar``)."""

    changed = Signal()            # calendar, step or range changed: panels rebuild their rows
    data_changed = Signal()       # a value of the series was edited or imported
    chart_changed = Signal()      # bars <-> line

    def __init__(self, lang: str = "fa"):
        super().__init__()
        self.lang = lang
        self.plant: Optional[Plant] = None
        self.calendar = "jalali"
        self.step = "month"
        self.start = date.today().replace(month=1, day=1)
        self.end = date.today()
        self.chart_mode = "bar"
        self.slices: List[cal.Slice] = []
        self.too_long = False

    # -- loading
    def set_plant(self, plant: Plant, fresh: bool = True) -> None:
        self.plant = plant
        if not fresh and self.slices:
            return
        v = plant.ts_view
        self.calendar = plant.site.calendar if plant.site.calendar in cal.CALENDARS else "jalali"
        self.step = v.get("step") if v.get("step") in cal.STEPS else "month"
        self.chart_mode = v.get("chart") if v.get("chart") in CHART_MODES else "bar"
        a = b = None
        try:
            if v.get("start") and v.get("end"):
                a, b = date.fromisoformat(v["start"]), date.fromisoformat(v["end"])
        except ValueError:
            a = b = None
        if a is None or b is None or b < a:
            span = TS.data_span(plant)
            if span:
                a, b = span
            else:
                y = cal.parts(date.today(), self.calendar)[0]
                a, b = cal.make_date(self.calendar, y, 1, 1), cal.make_date(self.calendar, y, 12, 31)
        self.start, self.end = a, b
        self.recompute()
        self.changed.emit()

    def recompute(self) -> None:
        try:
            sl = cal.slices(self.start, self.end, self.step, self.calendar, self.lang)
        except ValueError:
            sl = []
        self.too_long = len(sl) > MAX_SLICES
        self.slices = sl[:MAX_SLICES]

    def _save(self) -> None:
        if self.plant is not None:
            self.plant.ts_view = {"step": self.step, "start": self.start.isoformat(), "end": self.end.isoformat(),
                                  "chart": self.chart_mode}
            self.plant.site.calendar = self.calendar

    # -- changes made by the user
    def set_view(self, calendar: Optional[str] = None, step: Optional[str] = None,
                 start: Optional[date] = None, end: Optional[date] = None) -> None:
        self.calendar = calendar or self.calendar
        self.step = step or self.step
        self.start = start or self.start
        self.end = end or self.end
        self.recompute()
        self._save()
        self.changed.emit()

    def set_chart_mode(self, mode: str) -> None:
        if mode in CHART_MODES and mode != self.chart_mode:
            self.chart_mode = mode
            self._save()
            self.chart_changed.emit()

    def notify_data(self) -> None:
        self.data_changed.emit()


# ---------------------------------------------------------------------------------------------- chart
class ChartView(QWidget):
    """One variable over the periods, as bars or as a line; empty periods are left blank."""

    def __init__(self, height: int = 230):
        super().__init__()
        self.setMinimumHeight(height)
        self.mode = "bar"
        self.title, self.labels, self.values, self.unit = "", [], [], ""

    def set_data(self, title: str, labels: List[str], values: List[Optional[float]], unit: str, mode: str = "bar") -> None:
        self.title, self.labels, self.values, self.unit, self.mode = title, labels, values, unit, mode
        self.update()

    def paintEvent(self, _ev) -> None:  # noqa: N802 (Qt API)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        p.setPen(QColor(COL["navy"]))
        f = p.font()
        f.setBold(True)
        p.setFont(f)
        p.drawText(QRectF(0, 2, w, 24), Qt.AlignmentFlag.AlignCenter, f"{self.title}  ({self.unit})" if self.unit else self.title)
        f.setBold(False)
        p.setFont(f)
        vals = [v for v in self.values if v is not None]
        if not vals:
            p.setPen(QColor(COL["sub"]))
            p.drawText(QRectF(0, 0, w, h), Qt.AlignmentFlag.AlignCenter, "—")
            p.end()
            return
        left, right, top, bottom = 64, 14, 34, 34
        pw, ph = max(1.0, w - left - right), max(1.0, h - top - bottom)
        lo, hi = min(0.0, min(vals)), max(0.0, max(vals))
        if hi == lo:
            hi = lo + 1.0
        span = hi - lo

        def Y(v: float) -> float:
            return top + ph * (1 - (v - lo) / span)

        for k in range(5):
            v = lo + span * k / 4
            p.setPen(QPen(QColor(COL["line"]), 1))
            p.drawLine(QPointF(left, Y(v)), QPointF(left + pw, Y(v)))
            p.setPen(QColor(COL["sub"]))
            p.drawText(QRectF(0, Y(v) - 9, left - 6, 18), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       "‎" + _short(v))
        n = len(self.values)
        gw = pw / n
        step = max(1, -(-n // max(1, int(pw // 78))))
        blue = QColor(COL["blue"])
        if self.mode == "line":
            pts = [(left + (i + 0.5) * gw, Y(v)) if v is not None else None for i, v in enumerate(self.values)]
            p.setPen(QPen(blue, 2))
            for a, b in zip(pts, pts[1:]):                       # a gap (no data) breaks the line
                if a is not None and b is not None:
                    p.drawLine(QPointF(*a), QPointF(*b))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(blue)
            for pt in pts:
                if pt is not None:
                    p.drawEllipse(QPointF(*pt), 3.5, 3.5)
        else:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(blue)
            for i, v in enumerate(self.values):
                if v is not None:
                    cx = left + (i + 0.5) * gw
                    y0, y1 = Y(max(v, 0)), Y(min(v, 0))
                    p.drawRoundedRect(QRectF(cx - gw * 0.34, y0, gw * 0.68, max(1.0, y1 - y0)), 2, 2)
        for i in range(n):
            if i % step == 0:
                cx = left + (i + 0.5) * gw
                p.setPen(QColor(COL["text"]))
                p.drawText(QRectF(cx - 34, top + ph + 6, 68, 18), Qt.AlignmentFlag.AlignCenter, self.labels[i])
        p.end()


def chart_labels(ctx: TimeContext) -> List[str]:
    out = []
    for s in ctx.slices:
        y, m, _d = cal.parts(s.start, ctx.calendar)
        out.append(f"{y}" if ctx.step == "year" else f"{y}/{m:02d}")
    return out


class ChartModeBox(QComboBox):
    """The bars / line choice; it follows and sets the shared context so that every chart looks the same."""

    def __init__(self, t: Translator, ctx: TimeContext):
        super().__init__()
        self.ctx = ctx
        self.addItem(t("ch_bar"), "bar")
        self.addItem(t("ch_line"), "line")
        self.setCurrentIndex(max(0, self.findData(ctx.chart_mode)))
        self.currentIndexChanged.connect(lambda _i: ctx.set_chart_mode(self.currentData()))
        ctx.chart_changed.connect(self._follow)
        ctx.changed.connect(self._follow)

    def _follow(self) -> None:
        i = self.findData(self.ctx.chart_mode)
        if i >= 0 and i != self.currentIndex():
            self.blockSignals(True)
            self.setCurrentIndex(i)
            self.blockSignals(False)


# ---------------------------------------------------------------------------------------------- table
class PasteTable(QTableWidget):
    """Table that accepts Ctrl+V of spreadsheet cells and Delete to clear."""

    pasted = Signal(int, int, list)
    cleared = Signal(list)

    def keyPressEvent(self, ev) -> None:  # noqa: N802 (Qt API)
        if ev.matches(QKeySequence.StandardKey.Paste):
            text = QGuiApplication.clipboard().text()
            rows = [line.split("\t") for line in text.replace("\r", "").split("\n") if line != ""]
            cur = self.currentIndex()
            if rows and cur.isValid():
                self.pasted.emit(cur.row(), cur.column(), rows)
            return
        if ev.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace) and self.state() != QAbstractItemView.State.EditingState:
            self.cleared.emit([(i.row(), i.column()) for i in self.selectedIndexes()])
            return
        super().keyPressEvent(ev)


class SeriesGrid(QWidget):
    """Periods x variables: the table where the values are typed or pasted, a footer with the total / mean, and a chart."""

    def __init__(self, t: Translator, ctx: TimeContext, keys: Callable[[], Sequence[str]]):
        super().__init__()
        self.t = t
        self.ctx = ctx
        self.keys = keys
        self.cols: List[TS.VarSpec] = []
        self._loading = False
        self._own_edit = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self.msg = QLabel("")
        self.msg.setObjectName("cardnote")
        self.msg.setWordWrap(True)
        self.tb = PasteTable(0, 2)
        self.tb.setAlternatingRowColors(True)
        self.tb.setShowGrid(False)
        self.tb.verticalHeader().setVisible(False)
        self.tb.verticalHeader().setDefaultSectionSize(32)
        self.tb.itemChanged.connect(self._cell_edited)
        self.tb.pasted.connect(self._paste)
        self.tb.cleared.connect(self._clear_cells)
        self.chart_var = QComboBox()
        self.chart_var.currentIndexChanged.connect(self._draw_chart)
        self.mode_box = ChartModeBox(t, ctx)
        bar = QHBoxLayout()
        bar.addWidget(QLabel(t("ch_variable")))
        bar.addWidget(self.chart_var, 1)
        bar.addWidget(self.mode_box)
        self.chart = ChartView(210)
        lay.addWidget(self.msg)
        lay.addWidget(self.tb)
        lay.addLayout(bar)
        lay.addWidget(self.chart)
        ctx.changed.connect(self.reload)
        ctx.chart_changed.connect(self._draw_chart)
        ctx.data_changed.connect(self._data_changed)

    def _data_changed(self) -> None:
        if not self._own_edit and self.isVisible():
            self.reload()

    # -- build
    def _specs(self) -> Dict[str, TS.VarSpec]:
        p = self.ctx.plant
        return {s.key: s for s in TS.catalog(p)} if p else {}

    def reload(self) -> None:
        p, t = self.ctx.plant, self.t
        if p is None:
            return
        slices = self.ctx.slices
        self.msg.setText(t("ts_too_long", n=MAX_SLICES) if self.ctx.too_long else ("" if slices else t("ts_bad_range")))
        specs = self._specs()
        self.cols = [specs[k] for k in self.keys() if k in specs]
        self._loading = True
        self.tb.clear()
        self.tb.setColumnCount(2 + len(self.cols))
        self.tb.setRowCount(len(slices) + 1 if slices else 0)
        self.tb.setHorizontalHeaderLabels([t("ts_col_period"), t("ts_col_days")] + [
            sp.label(t.lang) + "\n[" + TS.unit_text(sp.unit, t.lang, p) + "]" for sp in self.cols])
        hh = self.tb.horizontalHeader()
        hh.setFixedHeight(58)
        hh.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        for c in range(2, 2 + len(self.cols)):
            self.tb.setColumnWidth(c, 170)
        for r, s in enumerate(slices):
            lab = QTableWidgetItem(s.label + ("  *" if s.partial else ""))
            lab.setFlags(Qt.ItemFlag.ItemIsEnabled)
            if s.partial:
                lab.setToolTip(t("ts_partial_tip"))
            self.tb.setItem(r, 0, lab)
            d = QTableWidgetItem(str(s.days))
            d.setFlags(Qt.ItemFlag.ItemIsEnabled)
            d.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.tb.setItem(r, 1, d)
            for c in range(len(self.cols)):
                self._render_cell(r, 2 + c)
        if slices:
            self._render_footer()
        self.tb.setFixedHeight(min(380, 58 + 32 * (len(slices) + 1) + 6))
        self._loading = False
        cur = self.chart_var.currentData()
        self.chart_var.blockSignals(True)
        self.chart_var.clear()
        for sp in self.cols:
            self.chart_var.addItem(sp.label(t.lang), sp.key)
        i = self.chart_var.findData(cur)
        self.chart_var.setCurrentIndex(i if i >= 0 else 0)
        self.chart_var.blockSignals(False)
        self._draw_chart()

    def _cell_value(self, r: int, c: int):
        sp, s = self.cols[c - 2], self.ctx.slices[r]
        return TS.value_in(self.ctx.plant.series.get(sp.key, []), s.start, s.end, sp.agg)

    def _render_cell(self, r: int, c: int) -> None:
        v, cov = self._cell_value(r, c)
        it = QTableWidgetItem(fmt(v))
        it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        if v is not None and cov < 0.999:
            it.setBackground(WARN_BG)
            it.setToolTip(self.t("ts_cov_tip", pct=f"{100 * cov:.0f}"))
        self.tb.setItem(r, c, it)

    def _render_footer(self) -> None:
        r = len(self.ctx.slices)
        lab = QTableWidgetItem(self.t("ts_footer"))
        lab.setFlags(Qt.ItemFlag.ItemIsEnabled)
        f = lab.font()
        f.setBold(True)
        lab.setFont(f)
        self.tb.setItem(r, 0, lab)
        blank = QTableWidgetItem("")
        blank.setFlags(Qt.ItemFlag.ItemIsEnabled)
        self.tb.setItem(r, 1, blank)
        for c, sp in enumerate(self.cols, start=2):
            pts = self.ctx.plant.series.get(sp.key, [])
            got = [(TS.value_in(pts, x.start, x.end, sp.agg)[0], x.days) for x in self.ctx.slices]
            got = [(val, d) for val, d in got if val is not None]
            if not got:
                v = None
            elif sp.agg == "sum":
                v = sum(val for val, _ in got)
            else:
                v = sum(val * d for val, d in got) / sum(d for _, d in got)
            it = QTableWidgetItem(fmt(v))
            it.setFlags(Qt.ItemFlag.ItemIsEnabled)
            it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            fo = it.font()
            fo.setBold(True)
            it.setFont(fo)
            self.tb.setItem(r, c, it)

    def _draw_chart(self, *_a) -> None:
        key = self.chart_var.currentData()
        sp = next((s for s in self.cols if s.key == key), None)
        p = self.ctx.plant
        if sp is None or p is None:
            self.chart.set_data("", [], [], "", self.ctx.chart_mode)
            return
        c = 2 + self.cols.index(sp)
        vals = [self._cell_value(r, c)[0] for r in range(len(self.ctx.slices))]
        self.chart.set_data(sp.label(self.t.lang), chart_labels(self.ctx), vals,
                            TS.unit_text(sp.unit, self.t.lang, p), self.ctx.chart_mode)

    # -- editing
    def set_cell(self, r: int, c: int, text: str, ask: bool = True) -> bool:
        """Store the value typed into period r, variable column c. False if the text is not a number."""
        sp, s = self.cols[c - 2], self.ctx.slices[r]
        text = text.strip()
        v = parse_float(text)
        if text and v is None:
            return False
        if ask and v is not None and self._has_finer_data(sp.key, s):
            q = QMessageBox.question(self, self.t("app_title"), self.t("ts_finer_q"),
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if q != QMessageBox.StandardButton.Yes:
                return True
        TS.put(self.ctx.plant, sp.key, s.start, s.end, v)
        return True

    def _has_finer_data(self, key: str, s: cal.Slice) -> bool:
        for pt in self.ctx.plant.series.get(key, []):
            a, b = date.fromisoformat(pt.start), date.fromisoformat(pt.end)
            if b >= s.start and a <= s.end and (b - a).days + 1 < s.days and a >= s.start and b <= s.end:
                return True
        return False

    def _announce(self) -> None:
        self._own_edit = True
        self.ctx.notify_data()
        self._own_edit = False

    def _cell_edited(self, item: QTableWidgetItem) -> None:
        if self._loading or self.ctx.plant is None:
            return
        r, c = item.row(), item.column()
        if r >= len(self.ctx.slices) or c < 2:
            return
        ok = self.set_cell(r, c, item.text())
        self._loading = True
        self._render_cell(r, c)
        self._render_footer()
        self._loading = False
        self._draw_chart()
        self.msg.setText("" if ok else self.t("ts_not_number"))
        self._announce()

    def _paste(self, r0: int, c0: int, rows: List[List[str]]) -> None:
        if self.ctx.plant is None or c0 < 2:
            return
        bad = 0
        for i, line in enumerate(rows):
            for j, text in enumerate(line):
                r, c = r0 + i, c0 + j
                if r < len(self.ctx.slices) and c < 2 + len(self.cols):
                    if not self.set_cell(r, c, text, ask=False):
                        bad += 1
        self.reload()
        self.msg.setText(self.t("ts_not_number") if bad else "")
        self._announce()

    def _clear_cells(self, cells: List[tuple]) -> None:
        if self.ctx.plant is None:
            return
        for r, c in cells:
            if r < len(self.ctx.slices) and 2 <= c < 2 + len(self.cols):
                self.set_cell(r, c, "", ask=False)
        self.reload()
        self._announce()


class SeriesPanel(QWidget):
    """«Table and chart» under the fields it belongs to; collapsed until asked for, built on first use."""

    def __init__(self, t: Translator, ctx: TimeContext, keys: Callable[[], Sequence[str]]):
        super().__init__()
        self.t, self.ctx, self.keys = t, ctx, keys
        self.grid: Optional[SeriesGrid] = None
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(6)
        self.toggle = QPushButton(t("sp_show"))
        self.toggle.setCheckable(True)
        self.toggle.setProperty("kind", "ghost")
        self.toggle.toggled.connect(self._toggled)
        self.lay.addWidget(self.toggle, 0, Qt.AlignmentFlag.AlignLeft)

    def _toggled(self, on: bool) -> None:
        self.toggle.setText(self.t("sp_hide") if on else self.t("sp_show"))
        if on and self.grid is None:
            self.grid = SeriesGrid(self.t, self.ctx, self.keys)
            self.lay.addWidget(self.grid)
        if self.grid is not None:
            self.grid.setVisible(on)
            if on:
                self.grid.reload()

    def refresh(self) -> None:
        """The variables of the panel may have changed (a source was added or renamed)."""
        if self.grid is not None and self.grid.isVisible():
            self.grid.reload()


# ---------------------------------------------------------------------------------------------- settings
class TimeSettings(_Scroll):
    """Calendar, slices, range, the reference window of the base model, a map of the data, Excel / CSV files."""

    def __init__(self, t: Translator, ctx: TimeContext):
        super().__init__()
        self.t, self.ctx = t, ctx
        self._loading = False
        lang = t.lang

        self.cal_combo = QComboBox()
        self.cal_combo.addItem(t("cal_jalali"), "jalali")
        self.cal_combo.addItem(t("cal_gregorian"), "gregorian")
        self.step_combo = QComboBox()
        self.step_combo.addItem(t("ts_monthly"), "month")
        self.step_combo.addItem(t("ts_yearly"), "year")
        self.start = CalDate("jalali", lang)
        self.end = CalDate("jalali", lang)
        self.mode_box = ChartModeBox(t, ctx)
        row = QHBoxLayout()
        row.setSpacing(14)
        for text, w in ((t("ts_calendar"), self.cal_combo), (t("ts_slice"), self.step_combo),
                        (t("ts_start"), self.start), (t("ts_end"), self.end), (t("ch_style"), self.mode_box)):
            row.addLayout(self._field(text, w))
        row.addStretch(1)
        presets = QHBoxLayout()
        for key, fn in (("ts_p_year", self._preset_year), ("ts_p_last12", self._preset_last12), ("ts_p_data", self._preset_data)):
            presets.addWidget(button(t(key), "", fn))
        presets.addStretch(1)
        holder = QWidget()
        hl = QVBoxLayout(holder)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.addLayout(row)
        hl.addLayout(presets)
        self.lay.addWidget(card(t("ts_period"), holder, note=t("ts_set_note")))

        self.w_start = CalDate("jalali", lang)
        self.w_end = CalDate("jalali", lang)
        wrow = QHBoxLayout()
        wrow.setSpacing(14)
        for text, w in ((t("ts_start"), self.w_start), (t("ts_end"), self.w_end)):
            wrow.addLayout(self._field(text, w))
        wrow.addWidget(button(t("ts_w_from_table"), "", self._window_from_range), 0, Qt.AlignmentFlag.AlignBottom)
        wrow.addStretch(1)
        self.w_info = QLabel("")
        self.w_info.setObjectName("cardnote")
        self.w_info.setWordWrap(True)
        ww = QWidget()
        wl = QVBoxLayout(ww)
        wl.setContentsMargins(0, 0, 0, 0)
        wl.addLayout(wrow)
        wl.addWidget(self.w_info)
        self.lay.addWidget(card(t("ts_window"), ww, note=t("ts_window_note")))

        self.map = make_table([t("ts_map_var"), t("ts_map_unit"), t("ts_map_periods"), t("ts_map_span")])
        self.map_note = QLabel("")
        self.map_note.setObjectName("cardnote")
        self.map_note.setWordWrap(True)
        self.lay.addWidget(card(t("ts_map"), self.map, self.map_note, note=t("ts_map_note")))

        self.msg = QLabel("")
        self.msg.setObjectName("cardnote")
        self.msg.setWordWrap(True)
        io = QHBoxLayout()
        io.addWidget(button(t("ts_x_export"), "primary", self.export_xlsx_dialog))
        io.addWidget(button(t("ts_x_import"), "", self.import_xlsx_dialog))
        io.addWidget(button(t("ts_import"), "", self.import_csv_dialog))
        io.addWidget(button(t("ts_export"), "", self.export_csv_dialog))
        io.addStretch(1)
        iw = QWidget()
        il = QVBoxLayout(iw)
        il.setContentsMargins(0, 0, 0, 0)
        il.addLayout(io)
        il.addWidget(self.msg)
        self.lay.addWidget(card(t("ts_files"), iw, note=t("ts_files_note")))
        self.lay.addStretch(1)

        self.cal_combo.currentIndexChanged.connect(self._view_changed)
        self.step_combo.currentIndexChanged.connect(self._view_changed)
        self.start.changed.connect(self._view_changed)
        self.end.changed.connect(self._view_changed)
        self.w_start.changed.connect(self._window_edited)
        self.w_end.changed.connect(self._window_edited)
        ctx.changed.connect(self._from_ctx)
        ctx.data_changed.connect(self._fill_map)

    @staticmethod
    def _field(text: str, w: QWidget) -> QVBoxLayout:
        box = QVBoxLayout()
        lab = QLabel(text)
        lab.setObjectName("cardnote")
        box.addWidget(lab)
        box.addWidget(w)
        return box

    # -- context <-> widgets
    def refresh(self, plant: Plant) -> None:
        self.ctx.set_plant(plant, fresh=False)
        self._from_ctx(plant_changed=True)

    def _from_ctx(self, plant_changed: bool = False) -> None:
        c = self.ctx
        if c.plant is None:
            return
        self._loading = True
        self.cal_combo.setCurrentIndex(max(0, self.cal_combo.findData(c.calendar)))
        self.step_combo.setCurrentIndex(max(0, self.step_combo.findData(c.step)))
        for w in (self.start, self.end, self.w_start, self.w_end):
            if w.cal != c.calendar:
                w.set_calendar(c.calendar)
        self.start.set_date(c.start)
        self.end.set_date(c.end)
        win = TS.window_of(c.plant)
        if win:
            self.w_start.set_date(win[0])
            self.w_end.set_date(win[1])
        else:
            self.w_start.set_date(c.start)
            self.w_end.set_date(c.end)
        self._loading = False
        self._window_info()
        self._fill_map()

    def _view_changed(self, *_a) -> None:
        if self._loading or self.ctx.plant is None:
            return
        a, b = self.start.date(), self.end.date()
        calendar = self.cal_combo.currentData()
        if calendar != self.ctx.calendar:                       # dates are kept, only their display changes
            self._loading = True
            for w in (self.start, self.end, self.w_start, self.w_end):
                w.set_calendar(calendar)
            self._loading = False
        if b < a:
            self.msg.setText(self.t("ts_bad_range"))
            return
        self.msg.setText("")
        self.ctx.set_view(calendar, self.step_combo.currentData(), a, b)

    def _preset_year(self) -> None:
        y = cal.parts(date.today(), self.ctx.calendar)[0]
        self._set_range(cal.make_date(self.ctx.calendar, y, 1, 1), cal.make_date(self.ctx.calendar, y, 12, 31))

    def _preset_last12(self) -> None:
        end = date.today()
        y, m, _d = cal.parts(end, self.ctx.calendar)
        start = cal.make_date(self.ctx.calendar, y - 1, m, 1)
        self._set_range(start, end)

    def _preset_data(self) -> None:
        span = TS.data_span(self.ctx.plant) if self.ctx.plant else None
        if span:
            self._set_range(span[0], span[1])

    def _set_range(self, a: date, b: date) -> None:
        self._loading = True
        self.start.set_date(a)
        self.end.set_date(b)
        self._loading = False
        self._view_changed()

    def _window_from_range(self) -> None:
        self.w_start.set_date(self.start.date())
        self.w_end.set_date(self.end.date())

    def _window_edited(self) -> None:
        if self._loading or self.ctx.plant is None:
            return
        a, b = self.w_start.date(), self.w_end.date()
        if a <= b:
            self.ctx.plant.window = {"start": a.isoformat(), "end": b.isoformat()}
        self._window_info()

    def _window_info(self) -> None:
        if self.ctx.plant is None:
            return
        a, b = self.w_start.date(), self.w_end.date()
        if b < a:
            self.w_info.setText(self.t("ts_bad_range"))
            return
        n = len(TS.driven(self.ctx.plant))
        self.w_info.setText(self.t("ts_w_info2", days=(b - a).days + 1, n=n))

    # -- map of the data
    def _fill_map(self) -> None:
        p = self.ctx.plant
        if p is None:
            return
        specs = [s for s in TS.catalog(p) if p.series.get(s.key)]
        slices = self.ctx.slices
        self.map.clearContents()
        self.map.setRowCount(len(specs))
        for r, sp in enumerate(specs):
            have = sum(1 for s in slices if TS.value_in(p.series[sp.key], s.start, s.end, sp.agg)[0] is not None)
            pts = p.series[sp.key]
            span = f"{min(x.start for x in pts)}  →  {max(x.end for x in pts)}"
            put(self.map, r, 0, sp.label(self.t.lang), bold=True)
            put(self.map, r, 1, TS.unit_text(sp.unit, self.t.lang, p), True)
            put(self.map, r, 2, f"{have} / {len(slices)}", True)
            put(self.map, r, 3, span, True)
        self.map_note.setText("" if specs else self.t("ts_map_empty"))

    # -- files
    def _all_specs(self) -> List[TS.VarSpec]:
        return list(TS.catalog(self.ctx.plant)) if self.ctx.plant else []

    def export_csv(self, path: str) -> int:
        rows = TS.to_rows(self.ctx.plant)
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(TS.CSV_HEADER)
            w.writerows(rows)
        return len(rows)

    def import_csv(self, path: str) -> int:
        with open(path, newline="", encoding="utf-8-sig") as f:
            rows = list(csv.reader(f))
        if rows and rows[0] and rows[0][0].strip().lower() == "key":
            rows = rows[1:]
        ok, bad = TS.from_rows(self.ctx.plant, rows)
        self.ctx.notify_data()
        self.msg.setText(self.t("ts_imported", n=ok, bad=len(bad)))
        return ok

    def export_xlsx(self, path: str) -> int:
        return XL.export_template(self.ctx.plant, path, self.ctx.slices, self._all_specs(), self.t.lang)

    def import_xlsx(self, path: str) -> int:
        ok, bad = XL.import_template(self.ctx.plant, path)
        self.ctx.notify_data()
        self.msg.setText(self.t("ts_x_imported", n=ok, bad=len(bad)))
        return ok

    def export_xlsx_dialog(self) -> None:
        if self.ctx.plant is None or not self.ctx.slices:
            self.msg.setText(self.t("ts_x_nothing"))
            return
        path, _ = QFileDialog.getSaveFileName(self, self.t("ts_x_export"), "timeseries-template.xlsx", "Excel (*.xlsx)")
        if path:
            n = self.export_xlsx(path)
            self.msg.setText(self.t("ts_x_exported", n=n, m=len(self._all_specs())))

    def import_xlsx_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, self.t("ts_x_import"), "", "Excel (*.xlsx)")
        if path and self.ctx.plant is not None:
            self.import_xlsx(path)

    def export_csv_dialog(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, self.t("ts_export"), "timeseries.csv", "CSV (*.csv)")
        if path and self.ctx.plant is not None:
            self.export_csv(path)

    def import_csv_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, self.t("ts_import"), "", "CSV (*.csv)")
        if path and self.ctx.plant is not None:
            self.import_csv(path)

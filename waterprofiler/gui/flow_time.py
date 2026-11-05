"""Time controls of the flow / balance page: base model or one period, slider, previous / next, auto-play."""

from __future__ import annotations

from datetime import date
from typing import List, Optional

from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QHBoxLayout, QLabel, QPushButton, QSlider, QToolTip, QVBoxLayout, QWidget,
)

from .. import jcalendar as cal
from .. import timeseries as TS
from ..i18n import Translator
from ..model import Plant
from .theme import COL
from .timeseries_tab import CalDate
from .widgets import button

SPEEDS = (("ft_slow", 1800), ("ft_normal", 900), ("ft_fast", 400))
MAX_SLICES = 600


class TimeStrip(QWidget):
    """Total intake of every period as bars; the current period is highlighted; a click selects a period."""

    clicked = Signal(int)

    def __init__(self):
        super().__init__()
        self.labels: List[str] = []
        self.values: List[Optional[float]] = []
        self.current = -1
        self.setMinimumHeight(86)
        self.setMouseTracking(True)

    def set_data(self, labels: List[str], values: List[Optional[float]], current: int) -> None:
        self.labels, self.values, self.current = labels, values, current
        self.update()

    def _slot(self, x: float) -> int:
        n = len(self.values)
        if n == 0 or self.width() <= 0:
            return -1
        return min(n - 1, max(0, int(x / self.width() * n)))

    def mouseReleaseEvent(self, ev) -> None:  # noqa: N802 (Qt API)
        i = self._slot(ev.position().x())
        if i >= 0 and self.values[i] is not None:
            self.clicked.emit(i)

    def mouseMoveEvent(self, ev) -> None:  # noqa: N802 (Qt API)
        i = self._slot(ev.position().x())
        if i >= 0:
            v = self.values[i]
            QToolTip.showText(ev.globalPosition().toPoint(), f"{self.labels[i]}: {'—' if v is None else format(v, ',.0f')}", self)

    def paintEvent(self, _ev) -> None:  # noqa: N802 (Qt API)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        n = len(self.values)
        r = QRectF(0, 4, self.width(), self.height() - 8)
        if n == 0:
            return
        top = max([v for v in self.values if v is not None] or [1.0]) or 1.0
        w = r.width() / n
        for i, v in enumerate(self.values):
            x = r.left() + i * w
            if v is None:
                p.setPen(QColor("#C9D1DC"))
                p.drawLine(int(x + w / 2), int(r.bottom() - 2), int(x + w / 2), int(r.bottom()))
                continue
            h = max(2.0, (r.height() - 2) * v / top)
            col = QColor(COL["orange"] if i == self.current else COL["blue"])
            if i != self.current:
                col.setAlpha(150)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(col)
            p.drawRoundedRect(QRectF(x + w * 0.12, r.bottom() - h, w * 0.76, h), 2, 2)


class FlowTimeBar(QWidget):
    """Everything the user sets to see the water system at another time. ``period()`` is None in base mode."""

    changed = Signal()
    goto_series = Signal()

    def __init__(self, t: Translator):
        super().__init__()
        self.t = t
        self.plant: Optional[Plant] = None
        self.slices: List[cal.Slice] = []
        self.index = 0
        self._loading = False
        self._range_set = False
        lang = t.lang
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        def field(label: str, w: QWidget) -> QVBoxLayout:
            box = QVBoxLayout()
            lab = QLabel(label)
            lab.setObjectName("cardnote")
            box.addWidget(lab)
            box.addWidget(w)
            return box

        self.mode = QComboBox()
        self.mode.addItem(t("ft_mode_base"), "base")
        self.mode.addItem(t("ft_mode_period"), "period")
        self.basis = QComboBox()
        self.basis.addItem(t("ft_basis_period"), "period")
        self.basis.addItem(t("ft_basis_annual"), "annual")
        self.fill = QComboBox()
        self.fill.addItem(t("ft_fill_prorate"), "prorate")
        self.fill.addItem(t("ft_fill_base"), "base")
        self.cal_combo = QComboBox()
        self.cal_combo.addItem(t("cal_jalali"), "jalali")
        self.cal_combo.addItem(t("cal_gregorian"), "gregorian")
        self.step = QComboBox()
        self.step.addItem(t("ts_monthly"), "month")
        self.step.addItem(t("ts_yearly"), "year")
        self.start = CalDate("jalali", lang)
        self.end = CalDate("jalali", lang)
        self.span_btn = button(t("ts_p_data"), "", self._whole_span)

        row1 = QHBoxLayout()
        row1.setSpacing(14)
        for text, w in ((t("ft_mode"), self.mode), (t("ft_basis"), self.basis), (t("ft_fill"), self.fill)):
            row1.addLayout(field(text, w))
        row1.addStretch(1)
        row2 = QHBoxLayout()
        row2.setSpacing(14)
        for text, w in ((t("ts_calendar"), self.cal_combo), (t("ts_slice"), self.step),
                        (t("ts_start"), self.start), (t("ts_end"), self.end)):
            row2.addLayout(field(text, w))
        row2.addWidget(self.span_btn, 0, Qt.AlignmentFlag.AlignBottom)
        row2.addStretch(1)
        self.selectors = QWidget()
        sl = QVBoxLayout(self.selectors)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.addLayout(row2)

        self.prev_btn = button(t("ft_prev"), "", lambda: self.step_by(-1))
        self.next_btn = button(t("ft_next"), "", lambda: self.step_by(1))
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self.slider.setMinimum(0)
        self.slider.setMaximum(0)
        self.pos_label = QLabel("")
        self.pos_label.setStyleSheet("font-weight:700;")
        self.play_btn = button(t("ft_play"), "primary", self.toggle_play)
        self.speed = QComboBox()
        for key, ms in SPEEDS:
            self.speed.addItem(t(key), ms)
        self.speed.setCurrentIndex(1)
        self.loop = QCheckBox(t("ft_loop"))
        self.loop.setChecked(True)
        nav = QHBoxLayout()
        nav.setSpacing(10)
        nav.addWidget(self.play_btn)
        nav.addWidget(self.prev_btn)
        nav.addWidget(self.slider, 1)
        nav.addWidget(self.next_btn)
        nav.addWidget(self.pos_label)
        opts = QHBoxLayout()
        opts.setSpacing(14)
        opts.addLayout(field(t("ft_speed"), self.speed))
        opts.addWidget(self.loop, 0, Qt.AlignmentFlag.AlignBottom)
        opts.addStretch(1)
        self.player = QWidget()
        pl = QVBoxLayout(self.player)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.setSpacing(8)
        pl.addLayout(nav)
        pl.addLayout(opts)
        self.strip_title = QLabel(t("ft_strip"))
        self.strip_title.setObjectName("cardnote")
        self.strip = TimeStrip()
        pl.addWidget(self.strip_title)
        pl.addWidget(self.strip)

        self.info = QLabel("")
        self.info.setObjectName("cardnote")
        self.info.setWordWrap(True)
        self.goto_btn = button(t("ft_goto"), "", self.goto_series.emit)
        self.goto_btn.setVisible(False)

        root.addLayout(row1)
        root.addWidget(self.selectors)
        root.addWidget(self.player)
        root.addWidget(self.info)
        root.addWidget(self.goto_btn, 0, Qt.AlignmentFlag.AlignLeft)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.mode.currentIndexChanged.connect(self._mode_changed)
        self.basis.currentIndexChanged.connect(self._emit)
        self.fill.currentIndexChanged.connect(self._emit)
        self.cal_combo.currentIndexChanged.connect(self._calendar_changed)
        self.step.currentIndexChanged.connect(self._rebuild)
        self.start.changed.connect(self._rebuild)
        self.end.changed.connect(self._rebuild)
        self.slider.valueChanged.connect(self._slider_moved)
        self.strip.clicked.connect(self.slider.setValue)
        self.speed.currentIndexChanged.connect(self._speed_changed)
        self._apply_enabled()

    # ------------------------------------------------------------------ state
    def period(self) -> Optional[cal.Slice]:
        if self.mode.currentData() != "period" or not self.slices:
            return None
        return self.slices[min(self.index, len(self.slices) - 1)]

    def has_data(self) -> bool:
        return bool(self.plant and TS.data_span(self.plant))

    def is_playing(self) -> bool:
        return self.timer.isActive()

    def set_plant(self, plant: Plant, fresh: bool) -> None:
        """Called when the page is shown or the plant changed. ``fresh``: a different project was loaded."""
        self.plant = plant
        self._loading = True
        calendar = plant.site.calendar if plant.site.calendar in cal.CALENDARS else "jalali"
        if fresh or not self.slices:
            self.mode.setCurrentIndex(0)
            self.cal_combo.setCurrentIndex(0 if calendar == "jalali" else 1)
            for w in (self.start, self.end):
                w.set_calendar(calendar)
            self.index = 0
            self._range_set = False
        span = TS.data_span(plant)
        if span and not self._range_set:
            self.start.set_date(span[0])
            self.end.set_date(span[1])
            self._range_set = True
        has = self.has_data()
        if not has:
            self.pause()
            self.mode.setCurrentIndex(0)
        self._loading = False
        self._rebuild(emit=False)
        self._apply_enabled()

    def _apply_enabled(self) -> None:
        has = self.has_data()
        on = has and self.mode.currentData() == "period"
        self.mode.setEnabled(has)
        for w in (self.basis, self.fill, self.selectors, self.player):
            w.setEnabled(on)
        self.player.setVisible(has and bool(self.slices))
        self.selectors.setVisible(has)
        self.goto_btn.setVisible(not has)
        if not has:
            self.info.setText(self.t("ft_no_series"))

    def _whole_span(self) -> None:
        span = TS.data_span(self.plant) if self.plant else None
        if span:
            self._loading = True
            self.start.set_date(span[0])
            self.end.set_date(span[1])
            self._loading = False
            self._rebuild()

    def _calendar_changed(self, *_a) -> None:
        if self._loading or self.plant is None:
            return
        calendar = self.cal_combo.currentData()
        self._loading = True
        for w in (self.start, self.end):
            w.set_calendar(calendar)
        self._loading = False
        self._rebuild()

    def _mode_changed(self, *_a) -> None:
        if self._loading:
            return
        if self.mode.currentData() != "period":
            self.pause()
        self._apply_enabled()
        self._emit()

    def _rebuild(self, *_a, emit: bool = True) -> None:
        if self._loading or self.plant is None:
            return
        a, b = self.start.date(), self.end.date()
        old = self.slices[self.index].start if self.slices and self.index < len(self.slices) else None
        if b < a or not self.has_data():
            self.slices = []
        else:
            try:
                self.slices = cal.slices(a, b, self.step.currentData(), self.cal_combo.currentData(), self.t.lang)[:MAX_SLICES]
            except ValueError:
                self.slices = []
        idx = 0
        if old is not None:
            for i, s in enumerate(self.slices):
                if s.start <= old <= s.end:
                    idx = i
                    break
        self.index = idx
        self._loading = True
        self.slider.setMaximum(max(0, len(self.slices) - 1))
        self.slider.setValue(idx)
        self._loading = False
        self.player.setVisible(self.has_data() and bool(self.slices))
        if self.timer.isActive() and not self.slices:
            self.pause()
        self._update_position()
        if emit:
            self._emit()

    def set_strip(self, values: List[Optional[float]]) -> None:
        self.strip.set_data([s.label for s in self.slices], values, self.index)

    def set_info(self, text: str) -> None:
        self.info.setText(text)

    def _update_position(self) -> None:
        n = len(self.slices)
        if n:
            self.pos_label.setText(self.t("ft_pos", label=self.slices[self.index].label, i=self.index + 1, n=n))
        else:
            self.pos_label.setText("")
        self.strip.current = self.index
        self.strip.update()
        self.prev_btn.setEnabled(n > 1)
        self.next_btn.setEnabled(n > 1)

    def _slider_moved(self, v: int) -> None:
        if self._loading:
            return
        self.index = v
        self._update_position()
        self._emit()

    def step_by(self, d: int) -> None:
        n = len(self.slices)
        if n:
            self.slider.setValue((self.index + d) % n)

    def _emit(self, *_a) -> None:
        if not self._loading:
            self.changed.emit()

    # ------------------------------------------------------------------ auto-play
    def toggle_play(self) -> None:
        if self.timer.isActive():
            self.pause()
        else:
            self.play()

    def play(self) -> None:
        if len(self.slices) < 2 or self.mode.currentData() != "period":
            return
        if self.index >= len(self.slices) - 1:
            self.slider.setValue(0)
        self.timer.start(int(self.speed.currentData()))
        self.play_btn.setText(self.t("ft_pause"))

    def pause(self) -> None:
        self.timer.stop()
        self.play_btn.setText(self.t("ft_play"))

    def _speed_changed(self, *_a) -> None:
        if self.timer.isActive():
            self.timer.start(int(self.speed.currentData()))

    def _tick(self) -> None:
        n = len(self.slices)
        if n < 2:
            self.pause()
            return
        if self.index >= n - 1:
            if self.loop.isChecked():
                self.slider.setValue(0)
            else:
                self.pause()
            return
        self.slider.setValue(self.index + 1)
        if self.index >= n - 1 and not self.loop.isChecked():
            self.pause()

    def hideEvent(self, ev) -> None:  # noqa: N802 (Qt API)
        self.pause()
        super().hideEvent(ev)

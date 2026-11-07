"""The industrial complex page: plant definition and site structure on one page (two sections)."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QTabWidget, QVBoxLayout, QWidget

from ..i18n import Translator
from ..model import Plant
from .editor import PlantPanel
from .series_view import TimeContext, TimeSettings
from .site_tab import SiteTab


class IndustrialTab(QWidget):
    """«مجتمع صنعتی»: name, prices, sources, outlets, treatments (plant) + areas, products, meters (site)."""

    changed = Signal()

    def __init__(self, t: Translator, ctx: TimeContext | None = None):
        super().__init__()
        self.t = t
        self.ctx = ctx or TimeContext(t.lang)
        self.time_settings = TimeSettings(t, self.ctx)
        self.plant_panel = PlantPanel(t)
        self.site_tab = SiteTab(t)
        self.sections = QTabWidget()
        self.sections.setDocumentMode(True)
        self.sections.addTab(self.time_settings, t("ind_sec_time"))
        self.sections.addTab(self.plant_panel, t("ind_sec_plant"))
        self.sections.addTab(self.site_tab, t("ind_sec_site"))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.sections)
        self.plant_panel.changed.connect(self.changed)
        self.site_tab.changed.connect(self.changed)
        self.sections.currentChanged.connect(self._section_changed)
        self.plant: Plant | None = None

    def load(self, plant: Plant) -> None:
        self.plant = plant
        self.ctx.set_plant(plant, fresh=True)
        self.plant_panel.load(plant)
        self.site_tab.load(plant)

    def store(self, plant: Plant) -> None:
        self.plant_panel.store(plant)
        self.site_tab.store(plant)

    def refresh(self, plant: Plant) -> None:
        """The page is opened: pick up renamed subsystems, sources and outlets in the site section."""
        self.plant = plant
        self.plant_panel._lock_driven(plant)          # the time series may have changed what is entered elsewhere
        self.site_tab.refresh(plant)

    def _section_changed(self, _i: int) -> None:
        if self.plant is not None:
            if self.sections.currentWidget() is self.time_settings:
                self.time_settings.refresh(self.plant)
            self.plant_panel.store(self.plant)
            self.site_tab.refresh(self.plant)

    def show_plant(self) -> None:
        self.sections.setCurrentWidget(self.plant_panel)

    def show_time(self) -> None:
        self.sections.setCurrentWidget(self.time_settings)

    def show_site(self) -> None:
        self.sections.setCurrentWidget(self.site_tab)

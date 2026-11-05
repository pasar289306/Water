"""Main application window."""

from __future__ import annotations

import copy
import os
from typing import Optional

from PySide6.QtCore import QSettings, Qt, QUrl
from PySide6.QtGui import QAction, QImage, QKeySequence, QPageSize, QPdfWriter, QPixmap, QTextDocument, QTextOption
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QFileDialog, QFrame, QHBoxLayout, QLabel, QListWidget, QMainWindow,
    QMessageBox, QSplitter, QTabWidget, QVBoxLayout, QWidget,
)

from .. import __version__, branding
from ..analysis import Analysis, analyse
from ..estimate_apply import apply_estimate
from ..estimators import InputError
from ..export_xlsx import export_xlsx
from ..i18n import Translator
from ..importer import ImportProblem, export_input_workbook, import_workbook, write_template
from ..messages import localize
from ..model import Plant, Subsystem
from ..report_html import build_report_html
from ..sample import demo_plant, demo_with_history
from ..storage import EXTENSION, ProjectFileError, load_project, save_project
from .dialogs import AboutDialog, EstimateDialog
from .editor import PlantPanel, SubsystemEditor
from .measures_tabs import MeasuresTab, PlanTab
from .econ_tab import EconTab
from .indicators_tab import IndicatorsTab
from .site_tab import SiteTab
from .timeseries_tab import TimeTab
from .flow_tab import FlowTab, labels_for, render_image
from .tabs import CostTab, ReconcileTab, SavingsTab
from .theme import apply_theme, brand_icon
from .widgets import NavButton, button, card

ORG = "CEPWater"
APP = "CEPWater"


class PageStack(QTabWidget):
    """QTabWidget whose tab bar is hidden; the sidebar does the navigation."""

    def __init__(self):
        super().__init__()
        self.tabBar().hide()
        self.setDocumentMode(True)


def new_plant(t: Translator) -> Plant:
    return Plant(name=t("new_plant_name"), currency="IRR" if t.lang == "fa" else "USD")


class MainWindow(QMainWindow):
    def __init__(self, lang: str = "fa"):
        super().__init__()
        self.t = Translator(lang)
        self.plant = new_plant(self.t)
        self.path: Optional[str] = None
        self._snapshot = copy.deepcopy(self.plant.to_dict())
        self._cur = -1
        self.resize(1180, 760)
        self._build()
        self._load_all()

    # ============================================================ construction
    def _build(self) -> None:
        t = self.t
        app = QApplication.instance()
        app.setLayoutDirection(Qt.LayoutDirection.RightToLeft if t.rtl else Qt.LayoutDirection.LeftToRight)
        apply_theme(app)
        self.setWindowIcon(brand_icon())
        self.setWindowTitle(self._title())
        self._build_menu()

        self.tabs = PageStack()
        self.plant_panel = PlantPanel(t)
        self.sub_editor = SubsystemEditor(t)
        self.sub_editor.estimate_requested.connect(self._estimate)
        self.sub_list = QListWidget()
        self.flow_tab = FlowTab(t)
        self.flow_tab.subsystem_requested.connect(self._open_subsystem)
        self.flow_tab.links_changed.connect(self._links_changed)
        self.flow_tab.plant_edited.connect(self._flow_edited)
        self.flow_tab.series_requested.connect(lambda: self.tabs.setCurrentWidget(self.ts_tab))
        self.balance_tab = self.flow_tab.balance      # the balance lives on the flow page
        self.cost_tab = CostTab(t)
        self.savings_tab = SavingsTab(t)
        self.measures_tab = MeasuresTab(t)
        self.plan_tab = PlanTab(t)
        self.econ_tab = EconTab(t)
        self.ind_tab = IndicatorsTab(t)
        self.site_tab = SiteTab(t)
        self.ts_tab = TimeTab(t)
        self.ts_tab.model_changed.connect(self._series_applied)
        self.measures_tab.plan_requested.connect(self._to_plan)
        self.reconcile_tab = ReconcileTab(t)
        self.reconcile_tab.applied.connect(self._apply_reconciled)

        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(10)
        side = QWidget()
        sl = QVBoxLayout(side)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.addWidget(self.sub_list, 1)
        ll.addWidget(card(t("tab_subsystems"), side), 1)
        ll.addWidget(button("+  " + t("add"), "primary", self._add_subsystem))
        ll.addWidget(button(t("remove"), "danger", self._remove_subsystem))
        subs = QWidget()
        sh = QHBoxLayout(subs)
        sh.setContentsMargins(26, 22, 26, 18)
        split = QSplitter()
        split.setHandleWidth(10)
        split.addWidget(left)
        split.addWidget(self.sub_editor)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 4)
        split.setSizes([230, 900])
        sh.addWidget(split)

        self.subs_page = subs
        self._pages = [
            ("plant", "tab_plant", "desc_plant", self.plant_panel),
            ("site", "tab_site", "desc_site", self.site_tab),
            ("subsystems", "tab_subsystems", "desc_subsystems", subs),
            ("ts", "tab_ts", "desc_ts", self.ts_tab),
            ("flow", "tab_flow", "desc_flow", self.flow_tab),
            ("cost", "tab_cost", "desc_cost", self.cost_tab),
            ("savings", "tab_savings", "desc_savings", self.savings_tab),
            ("measures", "tab_measures", "desc_measures", self.measures_tab),
            ("plan", "tab_plan", "desc_plan", self.plan_tab),
            ("econ", "tab_econ", "desc_econ", self.econ_tab),
            ("ind", "tab_ind", "desc_ind", self.ind_tab),
            ("reconcile", "tab_reconcile", "desc_reconcile", self.reconcile_tab),
        ]
        for _icon, name_key, _desc, w in self._pages:
            self.tabs.addTab(w, t(name_key))

        root = QWidget()
        rl = QHBoxLayout(root)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(0)
        rl.addWidget(self._build_sidebar())
        content = QWidget()
        cl = QVBoxLayout(content)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(0)
        cl.addWidget(self._build_header())
        cl.addWidget(self.tabs, 1)
        rl.addWidget(content, 1)
        self.setCentralWidget(root)

        sb = self.statusBar()
        self._foot = QLabel(f"{branding.COMPANY[t.lang]}   ·   {branding.APP_NAME} {__version__}")
        self._foot.setContentsMargins(10, 0, 10, 0)
        sb.addPermanentWidget(self._foot)

        self.tabs.currentChanged.connect(self._tab_changed)
        self.sub_list.currentRowChanged.connect(self._row_changed)
        self._sync_nav(self.tabs.currentIndex())

    def _title(self) -> str:
        name = os.path.basename(self.path) if getattr(self, "path", None) else ""
        return f"{branding.APP_NAME}" + (f" — {name}" if name else "")

    def _build_sidebar(self) -> QFrame:
        t = self.t
        bar = QFrame()
        bar.setObjectName("sidebar")
        bar.setFixedWidth(250)
        lay = QVBoxLayout(bar)
        lay.setContentsMargins(16, 16, 16, 12)
        lay.setSpacing(4)
        logo = QLabel()
        pm = QPixmap(branding.logo_path("full"))
        if not pm.isNull():
            logo.setPixmap(pm.scaledToWidth(176, Qt.TransformationMode.SmoothTransformation))
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(logo)
        accent = QFrame()
        accent.setObjectName("accent")
        accent.setFixedHeight(3)
        lay.addSpacing(6)
        lay.addWidget(accent)
        lay.addSpacing(10)
        name = QLabel(branding.APP_NAME)
        name.setObjectName("appname")
        name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub = QLabel(branding.APP_SUBTITLE[t.lang])
        sub.setObjectName("appsub")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub.setWordWrap(True)
        lay.addWidget(name)
        lay.addWidget(sub)
        lay.addSpacing(16)

        self.nav = []
        group = QButtonGroup(bar)
        group.setExclusive(True)
        for i, (icon, name_key, _d, _w) in enumerate(self._pages):
            b = NavButton(icon, t(name_key))
            b.clicked.connect(lambda _=False, i=i: self.tabs.setCurrentIndex(i))
            group.addButton(b)
            lay.addWidget(b)
            self.nav.append(b)
        lay.addStretch(1)
        foot = QLabel(f"{branding.COMPANY[t.lang]}\n{branding.TAGLINE[t.lang]}")
        foot.setObjectName("sidefoot")
        foot.setAlignment(Qt.AlignmentFlag.AlignCenter)
        foot.setWordWrap(True)
        lay.addWidget(foot)
        return bar

    def _build_header(self) -> QFrame:
        t = self.t
        hd = QFrame()
        hd.setObjectName("header")
        hd.setFixedHeight(84)
        lay = QHBoxLayout(hd)
        lay.setContentsMargins(28, 10, 28, 10)
        lay.setSpacing(10)
        col = QVBoxLayout()
        col.setSpacing(2)
        self.page_title = QLabel()
        self.page_title.setObjectName("pagetitle")
        self.page_desc = QLabel()
        self.page_desc.setObjectName("pagedesc")
        col.addStretch(1)
        col.addWidget(self.page_title)
        col.addWidget(self.page_desc)
        col.addStretch(1)
        lay.addLayout(col, 1)
        lay.addWidget(button(t("save"), "primary", self.action_save))
        lay.addWidget(button("Excel", "", self.action_export_xlsx))
        lay.addWidget(button("PDF", "", self.action_export_pdf))
        return hd

    def _sync_nav(self, idx: int) -> None:
        if not (0 <= idx < len(self._pages)):
            return
        self.nav[idx].setChecked(True)
        _icon, name_key, desc_key, _w = self._pages[idx]
        self.page_title.setText(self.t(name_key))
        self.page_desc.setText(self.t(desc_key))

    def _build_menu(self) -> None:
        t = self.t
        mb = self.menuBar()
        mb.clear()
        m = mb.addMenu(t("file"))

        def act(menu, key, fn, shortcut=None):
            a = QAction(t(key), self)
            if shortcut:
                a.setShortcut(QKeySequence(shortcut))
            a.triggered.connect(lambda _=False: fn())
            menu.addAction(a)
            return a

        act(m, "new", self.action_new, QKeySequence.StandardKey.New)
        act(m, "open", self.action_open, QKeySequence.StandardKey.Open)
        act(m, "save", self.action_save, QKeySequence.StandardKey.Save)
        act(m, "save_as", self.action_save_as, QKeySequence.StandardKey.SaveAs)
        m.addSeparator()
        act(m, "load_demo", self.action_demo)
        m.addSeparator()
        act(m, "import_xlsx", self.action_import_xlsx)
        act(m, "save_template", self.action_save_template)
        act(m, "export_inputs", self.action_export_inputs)
        m.addSeparator()
        act(m, "export_xlsx", self.action_export_xlsx)
        act(m, "export_pdf", self.action_export_pdf)
        m.addSeparator()
        act(m, "exit", self.close)

        lm = mb.addMenu(t("language"))
        act(lm, "lang_fa", lambda: self.set_language("fa"))
        act(lm, "lang_en", lambda: self.set_language("en"))
        hm = mb.addMenu(t("help"))
        act(hm, "about", self.action_about)

    # ===================================================== model <-> UI sync
    def _catalog(self):
        return ([s.name for s in self.plant.sources], [o.name for o in self.plant.outlets],
                [x.name for x in self.plant.treatments])

    def collect(self) -> Plant:
        """Pull every editor's content into self.plant."""
        self.plant_panel.store(self.plant)
        self.site_tab.store(self.plant)
        if 0 <= self._cur < len(self.plant.subsystems):
            self.sub_editor.store(self.plant.subsystems[self._cur])
            item = self.sub_list.item(self._cur)
            if item is not None:
                item.setText(self.plant.subsystems[self._cur].name)
        return self.plant

    def _refresh_sub_list(self, select: int) -> None:
        self.sub_list.blockSignals(True)
        self.sub_list.clear()
        for s in self.plant.subsystems:
            self.sub_list.addItem(s.name)
        self.sub_list.blockSignals(False)
        self._cur = -1
        if self.plant.subsystems:
            select = max(0, min(select, len(self.plant.subsystems) - 1))
            self.sub_list.setCurrentRow(select)      # triggers _row_changed
            if self._cur != select:
                self._row_changed(select)
        self.sub_editor.setEnabled(bool(self.plant.subsystems))

    def _load_all(self, select: int = 0) -> None:
        self.plant_panel.load(self.plant)
        self.site_tab.load(self.plant)
        self.sub_editor.set_catalog(*self._catalog())
        self._refresh_sub_list(select)

    def _row_changed(self, row: int) -> None:
        if 0 <= self._cur < len(self.plant.subsystems):
            self.sub_editor.store(self.plant.subsystems[self._cur])
            item = self.sub_list.item(self._cur)
            if item is not None:
                item.setText(self.plant.subsystems[self._cur].name)
        self._cur = row
        if 0 <= row < len(self.plant.subsystems):
            self.sub_editor.load(self.plant.subsystems[row])

    def _tab_changed(self, idx: int) -> None:
        self._sync_nav(idx)
        self.collect()
        w = self.tabs.widget(idx)
        if w is self.cost_tab:
            self._run_analysis()
        elif w is self.flow_tab:
            a = self._run_analysis()
            self.flow_tab.refresh(self.plant, a)
        elif w is self.savings_tab:
            self.savings_tab.refresh(self.plant)
        elif w is self.measures_tab:
            self.measures_tab.refresh(self.plant)
        elif w is self.plan_tab:
            self.plan_tab.refresh(self.plant)
        elif w is self.econ_tab:
            self.econ_tab.refresh(self.plant)
        elif w is self.ind_tab:
            self.ind_tab.refresh(self.plant)
        elif w is self.site_tab:
            self.site_tab.refresh(self.plant)
        elif w is self.ts_tab:
            self.ts_tab.refresh(self.plant)
        elif w is self.reconcile_tab:
            self.reconcile_tab.refresh(self.plant)
        elif w is not self.plant_panel:
            # subsystems tab: pick up renamed sources / outlets / treatments
            self.sub_editor.set_catalog(*self._catalog())
            if 0 <= self._cur < len(self.plant.subsystems):
                self.sub_editor.load(self.plant.subsystems[self._cur])

    def _to_plan(self, measure_id: int, target: str) -> None:
        self.collect()
        self.plan_tab.refresh(self.plant)
        self.plan_tab.add(measure_id, target)
        self.tabs.setCurrentWidget(self.plan_tab)

    def _open_subsystem(self, name: str) -> None:
        self.collect()
        for i, s in enumerate(self.plant.subsystems):
            if s.name == name:
                self.tabs.setCurrentWidget(self.subs_page)
                self.sub_list.setCurrentRow(i)
                return

    def _series_applied(self) -> None:
        """A period of the time series was written into the annual model: reload the editors."""
        self._load_all(select=max(0, self._cur))
        self.reconcile_tab.refresh(self.plant)

    def _flow_edited(self) -> None:
        """The diagram changed the plant: reload the subsystem editor and recompute everything."""
        if 0 <= self._cur < len(self.plant.subsystems):
            self.sub_editor.load(self.plant.subsystems[self._cur])
        a = self._run_analysis()
        self.flow_tab.refresh(self.plant, a)

    def _links_changed(self) -> None:
        try:
            a = self._analyse()
        except Exception:  # noqa: BLE001 - the balance tab shows the details
            a = None
        self.flow_tab.update_analysis(a)

    def _analyse(self) -> Analysis:
        return analyse(self.plant)

    def _run_analysis(self) -> Optional[Analysis]:
        t = self.t
        try:
            a = self._analyse()
        except KeyError as e:
            msg = t("missing_ref", name=str(e).strip("'\""))
            self.cost_tab.show_error(msg)
            self.flow_tab.show_error(msg)
            return None
        except Exception as e:  # noqa: BLE001 - shown to the user, never crash
            msg = f"{t('analysis_error')}: {localize(str(e), t.lang)}"
            self.cost_tab.show_error(msg)
            self.flow_tab.show_error(msg)
            return None
        self.flow_tab.set_analysis(a)
        self.cost_tab.refresh(a)
        return a

    # ================================================================ actions
    def _is_dirty(self) -> bool:
        self.collect()
        return self.plant.to_dict() != self._snapshot

    def _mark_clean(self) -> None:
        self._snapshot = copy.deepcopy(self.plant.to_dict())

    def _confirm_discard(self) -> bool:
        if not self._is_dirty():
            return True
        r = QMessageBox.question(self, branding.APP_NAME, self.t("unsaved"),
                                 QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        return r == QMessageBox.StandardButton.Yes

    def _set_plant(self, plant: Plant, path: Optional[str]) -> None:
        self.plant = plant
        self.path = path
        self._load_all()
        self._tab_changed(self.tabs.currentIndex())     # the open page must show the new project, not the old one
        self._mark_clean()
        self._update_title()

    def _update_title(self) -> None:
        self.setWindowTitle(self._title())

    def action_new(self) -> None:
        if self._confirm_discard():
            self._set_plant(new_plant(self.t), None)

    def action_demo(self) -> None:
        if self._confirm_discard():
            self._set_plant(demo_with_history(self.t.lang), None)
            self.statusBar().showMessage(self.t("demo_loaded"), 4000)

    def action_open(self) -> None:
        if not self._confirm_discard():
            return
        path, _ = QFileDialog.getOpenFileName(self, self.t("open"), "", self.t("file_project"))
        if not path:
            return
        try:
            plant = load_project(path)
        except ProjectFileError as e:
            QMessageBox.critical(self, self.t("error"), str(e))
            return
        self._set_plant(plant, path)

    def action_save(self) -> bool:
        if not self.path:
            return self.action_save_as()
        return self._save_to(self.path)

    def action_save_as(self) -> bool:
        path, _ = QFileDialog.getSaveFileName(self, self.t("save_as"), self.plant.name + EXTENSION,
                                              self.t("file_project"))
        if not path:
            return False
        if not path.lower().endswith(EXTENSION):
            path += EXTENSION
        return self._save_to(path)

    def _save_to(self, path: str) -> bool:
        self.collect()
        try:
            save_project(self.plant, path)
        except OSError as e:
            QMessageBox.critical(self, self.t("error"), str(e))
            return False
        self.path = path
        self._mark_clean()
        self._update_title()
        self.statusBar().showMessage(self.t("saved"), 3000)
        return True

    def _need_analysis(self) -> Optional[Analysis]:
        self.collect()
        a = self._run_analysis()
        if a is None:
            QMessageBox.warning(self, self.t("error"), self.t("analysis_error"))
        return a

    def export_xlsx_to(self, path: str) -> None:
        a = self._analyse_or_raise()
        export_xlsx(a, path, self.t.lang)

    def export_pdf_to(self, path: str) -> None:
        a = self._analyse_or_raise()
        flow_img = None
        try:
            from ..flow import build_flow
            flow_img = render_image(build_flow(a, labels_for(self.t)), self.t.rtl, self.t, 1400, 760)
        except Exception:  # noqa: BLE001 - the report is still useful without the picture
            flow_img = None
        html = build_report_html(a, self.t.lang, logo="logo", flow="flow" if flow_img is not None else None)
        doc = QTextDocument()
        if flow_img is not None:
            doc.addResource(QTextDocument.ResourceType.ImageResource, QUrl("flow"), flow_img)
        img = QImage(branding.logo_path("full"))
        if not img.isNull():
            doc.addResource(QTextDocument.ResourceType.ImageResource, QUrl("logo"), img)
        if self.t.rtl:
            opt = QTextOption()
            opt.setTextDirection(Qt.LayoutDirection.RightToLeft)
            doc.setDefaultTextOption(opt)
        doc.setHtml(html)
        w = QPdfWriter(path)
        w.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
        w.setResolution(120)
        doc.print_(w)

    def _analyse_or_raise(self) -> Analysis:
        self.collect()
        return self._analyse()

    def action_export_xlsx(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, self.t("export_xlsx"), self.plant.name + ".xlsx",
                                              self.t("file_xlsx"))
        if not path:
            return
        if not path.lower().endswith(".xlsx"):
            path += ".xlsx"
        try:
            self.export_xlsx_to(path)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, self.t("error"), str(e))
            return
        self.statusBar().showMessage(self.t("export_done"), 4000)

    def action_export_pdf(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, self.t("export_pdf"), self.plant.name + ".pdf",
                                              self.t("file_pdf"))
        if not path:
            return
        if not path.lower().endswith(".pdf"):
            path += ".pdf"
        try:
            self.export_pdf_to(path)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, self.t("error"), str(e))
            return
        self.statusBar().showMessage(self.t("export_done"), 4000)

    def action_about(self) -> None:
        AboutDialog(self.t, self).exec()

    # ------------------------------------------------------ Excel import / export
    def import_from(self, path: str):
        """Load plant inputs from an Excel workbook; returns the list of notes."""
        plant, notes = import_workbook(path, self.t.lang)
        self._set_plant(plant, None)
        return notes

    def action_import_xlsx(self) -> None:
        if not self._confirm_discard():
            return
        path, _ = QFileDialog.getOpenFileName(self, self.t("import_xlsx"), "", self.t("file_xlsx"))
        if not path:
            return
        try:
            notes = self.import_from(path)
        except ImportProblem as e:
            QMessageBox.critical(self, self.t("error"), str(e))
            return
        self.statusBar().showMessage(self.t("imp_done"), 4000)
        if notes:
            more = len(notes) - 25
            text = "\n".join(notes[:25]) + (f"\n… (+{more})" if more > 0 else "")
            QMessageBox.warning(self, self.t("imp_warnings_title"), text)

    def _save_workbook(self, key: str, default: str, writer) -> None:
        path, _ = QFileDialog.getSaveFileName(self, self.t(key), default, self.t("file_xlsx"))
        if not path:
            return
        if not path.lower().endswith(".xlsx"):
            path += ".xlsx"
        try:
            writer(path)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, self.t("error"), str(e))
            return
        self.statusBar().showMessage(self.t("export_done"), 4000)

    def action_save_template(self) -> None:
        self._save_workbook("save_template", "CEPWater-template.xlsx", lambda p: write_template(p, self.t.lang))

    def action_export_inputs(self) -> None:
        self.collect()
        self._save_workbook("export_inputs", self.plant.name + "-inputs.xlsx",
                            lambda p: export_input_workbook(self.plant, p, self.t.lang))

    # ------------------------------------------------------------ subsystems
    def _add_subsystem(self) -> None:
        self.collect()
        base = self.t("new_subsystem")
        names = {s.name for s in self.plant.subsystems}
        name, i = base, 2
        while name in names:
            name, i = f"{base} {i}", i + 1
        self.plant.subsystems.append(Subsystem(name))
        self._load_all(select=len(self.plant.subsystems) - 1)

    def _remove_subsystem(self) -> None:
        if not (0 <= self._cur < len(self.plant.subsystems)):
            return
        self.collect()
        del self.plant.subsystems[self._cur]
        self._load_all(select=max(0, self._cur - 1))

    def _estimate(self) -> None:
        t = self.t
        self.collect()
        if not (0 <= self._cur < len(self.plant.subsystems)):
            return
        s = self.plant.subsystems[self._cur]
        if s.kind not in ("process", "cooling_tower", "boiler", "sanitary", "irrigation"):
            QMessageBox.information(self, t("estimate"), t("select_kind_first"))
            return
        srcs, outs, _tr = self._catalog()
        if not srcs:
            QMessageBox.information(self, t("estimate"), t("catalog_hint"))
            return
        dlg = EstimateDialog(t, s.kind, s.params, srcs, outs,
                             next(iter(s.intake), None), next(iter(s.discharge), None), self)
        if dlg.exec():
            try:
                apply_estimate(s, s.kind, dlg.values(), dlg.selected_source(), dlg.selected_outlet())
            except (InputError, KeyError) as e:
                QMessageBox.warning(self, t("error"), localize(str(e), t.lang))
                return
            self.sub_editor.load(s)

    def _apply_reconciled(self, plant: Plant) -> None:
        self.plant = plant
        sel = self._cur
        self._load_all(select=max(0, sel))
        self.reconcile_tab.refresh(self.plant)

    # -------------------------------------------------------------- language
    def set_language(self, lang: str) -> None:
        if lang == self.t.lang:
            return
        self.collect()
        QSettings(ORG, APP).setValue("lang", lang)
        sel, tab = self._cur, self.tabs.currentIndex()
        self.t = Translator(lang)
        self._build()
        self._load_all(select=max(0, sel))
        self.tabs.setCurrentIndex(tab)
        self._update_title()

    # ----------------------------------------------------------------- close
    def closeEvent(self, ev) -> None:  # noqa: N802 (Qt API)
        if self._confirm_discard():
            ev.accept()
        else:
            ev.ignore()

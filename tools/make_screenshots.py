"""Render documentation screenshots of the real application (headless Qt).

    python tools/make_screenshots.py docs/img
"""

from __future__ import annotations

import os
import sys
import traceback

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

from PySide6.QtCore import QPoint  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from scenarios import leaky_plant  # noqa: E402
from waterprofiler.app import _load_fonts  # noqa: E402


def main(out: str) -> int:
    os.makedirs(out, exist_ok=True)
    for name in ("question", "warning", "information", "critical"):
        setattr(QMessageBox, name, staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))

    app = QApplication([])
    _load_fonts(app)
    from waterprofiler.gui.dialogs import EstimateDialog
    from waterprofiler.gui.main_window import MainWindow
    from waterprofiler.sample import demo_plant

    win = MainWindow("fa")
    win.resize(1320, 880)
    win.show()
    app.processEvents()
    done, failed = [], []

    def go(widget):
        idx = win.tabs.indexOf(widget)
        win.tabs.setCurrentIndex(idx)
        win._tab_changed(idx)

    def settle():
        for _ in range(3):
            app.processEvents()

    def shot(widget, name):
        settle()
        path = os.path.join(out, name + ".png")
        ok = widget.grab().save(path)
        (done if ok else failed).append(name)

    def step(fn):
        try:
            fn()
        except Exception:  # noqa: BLE001 - keep going, report at the end
            failed.append(fn.__name__)
            traceback.print_exc()

    def blank():
        win.plant_panel.name.setFocus()
        shot(win, "00_start_blank")

    def plant_tab():
        win._set_plant(demo_plant("fa"), None)
        win.tabs.setCurrentIndex(0)
        shot(win, "01_plant")
        shot(win.plant_panel.widget(), "01_plant_panel")

    def file_menu():
        menu = win.menuBar().actions()[0].menu()
        menu.popup(win.mapToGlobal(QPoint(40, 30)))
        settle()
        shot(menu, "02_file_menu")
        menu.hide()

    def lang_menu():
        menu = win.menuBar().actions()[1].menu()
        menu.popup(win.mapToGlobal(QPoint(120, 30)))
        settle()
        shot(menu, "02_language_menu")
        menu.hide()

    def subsystems():
        go(win.subs_page)
        win.sub_list.setCurrentRow(1)
        shot(win, "03_subsystems")
        shot(win.sub_editor.widget(), "03_subsystem_editor_full")

    def estimate_dialog():
        s = win.plant.subsystems[1]
        srcs, outs, _t = win._catalog()
        dlg = EstimateDialog(win.t, s.kind, s.params, srcs, outs, next(iter(s.intake), None),
                             next(iter(s.discharge), None), win)
        dlg.show()
        shot(dlg, "04_estimate_dialog")
        dlg.close()

    def balance_ok():
        go(win.flow_tab)
        shot(win, "05_balance")
        shot(win.flow_tab.widget(), "05_balance_full")

    def cost():
        go(win.cost_tab)
        shot(win, "06_cost")
        shot(win.cost_tab.widget(), "06_cost_full")

    def savings():
        go(win.savings_tab)
        st = win.savings_tab
        st.ct_new.setValue(6.0)
        st.ct_capex.setValue(5000.0)
        st._calc_ct()
        st.bo_coc.setValue(20.0)
        st.bo_cond.setValue(70.0)
        st.bo_capex.setValue(12000.0)
        st._calc_boiler()
        st.lk_rate.setValue(2.0)
        st.lk_hours.setValue(8000)
        st.lk_frac.setValue(1.0)
        st.lk_capex.setValue(3000.0)
        st._calc_leak()
        shot(win, "07_savings")
        shot(st.widget(), "07_savings_full")

    def leak_balance():
        win._set_plant(leaky_plant("fa"), None)
        go(win.flow_tab)
        shot(win, "08_balance_leak")
        shot(win.flow_tab.widget(), "08_balance_leak_full")

    def flow_time():
        from waterprofiler.sample import demo_with_history
        win._set_plant(demo_with_history("fa"), None)
        go(win.flow_tab)
        tb = win.flow_tab.timebar
        tb.mode.setCurrentIndex(1)
        tb.slider.setValue(min(5, tb.slider.maximum()))
        shot(win, "19_flow_time")
        shot(win.flow_tab.widget(), "19_flow_time_full")

    def measures():
        go(win.measures_tab)
        win.measures_tab.select(11)
        shot(win, "12_measures")
        shot(win.measures_tab.widget(), "12_measures_full")

    def flow():
        from scenarios import flow_plant
        win._set_plant(flow_plant("fa"), None)
        go(win.flow_tab)
        shot(win, "14_flow")
        shot(win.flow_tab.widget(), "14_flow_full")

    def plan():
        go(win.plan_tab)
        shot(win, "13_plan")
        shot(win.plan_tab.widget(), "13_plan_full")

    def econ():
        go(win.econ_tab)
        shot(win, "15_econ")
        shot(win.econ_tab.widget(), "15_econ_full")

    def site_and_series():
        from waterprofiler.sample import demo_with_history
        win._set_plant(demo_with_history("fa"), None)
        go(win.site_tab)
        shot(win, "17_site")
        shot(win.site_tab.widget(), "17_site_full")
        go(win.ts_tab)
        shot(win, "18_timeseries")
        shot(win.ts_tab.widget(), "18_timeseries_full")

    def indicators():
        go(win.ind_tab)
        shot(win, "16_indicators")
        shot(win.ind_tab.widget(), "16_indicators_full")

    def reconcile():
        go(win.reconcile_tab)
        win.reconcile_tab._run()
        shot(win, "09_reconcile")
        shot(win.reconcile_tab.widget(), "09_reconcile_full")

    def about():
        from waterprofiler.gui.dialogs import AboutDialog
        dlg = AboutDialog(win.t, win)
        dlg.show()
        shot(dlg, "11_about")
        dlg.close()

    def english():
        win.set_language("en")
        win._set_plant(demo_plant("en"), None)
        go(win.cost_tab)
        shot(win, "10_english_cost")

    for fn in (blank, plant_tab, file_menu, lang_menu, subsystems, estimate_dialog, balance_ok, flow, flow_time, cost, measures, plan, econ, site_and_series, indicators,
               savings, leak_balance, reconcile, about, english):
        step(fn)

    print("saved:", ", ".join(done))
    if failed:
        print("FAILED:", ", ".join(failed))
    return 0 if not failed else 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "docs/img"))

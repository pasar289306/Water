from datetime import date

import pytest

from waterprofiler import jcalendar as cal
from waterprofiler import periodview as PV
from waterprofiler import timeseries as TS
from waterprofiler.analysis import analyse
from waterprofiler.sample import demo_plant, demo_with_history


def _months(p):
    span = TS.data_span(p)
    return cal.slices(span[0], span[1], "month", "jalali", "en")


def test_base_plant_untouched_and_empty_period():
    p = demo_with_history("en")
    before = p.to_dict()
    q, info = PV.period_plant(p, date(2000, 1, 1), date(2000, 1, 31))
    assert info.empty and p.to_dict() == before
    assert q is not p


def test_period_changes_volumes_and_scales_with_days():
    p = demo_with_history("en")
    sl = _months(p)[3]
    q, info = PV.period_plant(p, sl.start, sl.end, "prorate", "period")
    assert not info.empty and info.applied > 0 and info.days == (sl.end - sl.start).days + 1
    qa, ia = PV.period_plant(p, sl.start, sl.end, "prorate", "annual")
    a, aa = analyse(q).balance, analyse(qa).balance
    assert aa.sum_intake == pytest.approx(a.sum_intake * 365.0 / info.days, rel=1e-6)
    assert analyse(p).balance.sum_intake != pytest.approx(aa.sum_intake)


def test_prorate_keeps_balance_small_and_base_fill_differs():
    p = demo_with_history("en")
    sl = _months(p)[6]
    q, _ = PV.period_plant(p, sl.start, sl.end, "prorate", "annual")
    r, _ = PV.period_plant(p, sl.start, sl.end, "base", "annual")
    bq, br = analyse(q).balance, analyse(r).balance
    assert abs(bq.unknown_loss) <= abs(br.unknown_loss) + 1e-6
    assert bq.sum_intake == pytest.approx(br.sum_intake)


def test_plant_without_series_is_empty():
    p = demo_plant("en")
    _, info = PV.period_plant(p, date(2025, 1, 1), date(2025, 1, 31))
    assert info.empty


def test_all_months_analyse():
    p = demo_with_history("en")
    for s in _months(p):
        q, info = PV.period_plant(p, s.start, s.end)
        if not info.empty:
            assert analyse(q).balance.sum_intake > 0

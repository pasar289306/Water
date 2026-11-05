"""The plant as it was in one period: the base model with the time series of that period applied.

Used by the flow / balance page to follow the water system through time. The base (annual) model is never
changed; ``period_plant`` works on a copy.

Rules, in this order:

1. Every variable that has data in the period replaces its value in the copy (``timeseries.plan_changes`` /
   ``commit``: annualised, so months of different length compare).
2. A source or outlet that has data but no subsystem-level data is spread over the subsystems that use it, in
   proportion to their share in the base model (a meter reading says how much, the base model says who).
3. Subsystem flows (evaporation, product water, internal recirculation, discharge, transfers) without own data
   either follow the subsystem's intake of the period (``fill="prorate"``) or stay at the base value
   (``fill="base"``). The number of such estimated values is reported, never hidden.
4. With ``basis="period"`` all volumes are then expressed for the days of the period, not per year.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from datetime import date
from typing import List, Set, Tuple

from . import timeseries as TS
from .model import Plant, sync_transfers

FILLS = ("prorate", "base")
BASES = ("period", "annual")
_FLOW_FIELDS = ("intake", "discharge", "evaporation", "product", "recirc")


@dataclass
class PeriodInfo:
    start: date
    end: date
    days: int
    scale: float                  # factor applied to the annual-rate volumes (days / 365 for the "period" basis)
    applied: int = 0              # flow variables that had data in this period
    estimated: int = 0            # subsystem flow values filled by rule 3
    total_vars: int = 0           # flow variables of the model (sources, outlets, subsystem flows)
    empty: bool = False           # no flow data at all in the period
    notes: List[str] = field(default_factory=list)


def _is_flow_key(key: str) -> bool:
    return key.startswith(("source:", "outlet:")) or (key.startswith("sub:") and key.rsplit(":", 1)[-1] in _FLOW_FIELDS)


def scale_volumes(p: Plant, k: float) -> None:
    """Multiply every volume of the plant (m3 per year -> m3 per period) by ``k``."""
    for s in p.sources:
        if s.metered_m3 is not None:
            s.metered_m3 *= k
    for o in p.outlets:
        if o.metered_m3 is not None:
            o.metered_m3 *= k
    for sub in p.subsystems:
        for d in (sub.intake, sub.discharge):
            for n in d:
                d[n] *= k
        sub.recirculated *= k
        sub.from_other *= k
        sub.to_other *= k
        sub.product *= k
        sub.evaporation *= k
    for lk in p.links:
        lk.volume *= k


def period_plant(plant: Plant, start: date, end: date, fill: str = "prorate", basis: str = "period",
                 min_cov: float = 0.5) -> Tuple[Plant, PeriodInfo]:
    days = (end - start).days + 1
    info = PeriodInfo(start, end, days, days / 365.0 if basis == "period" else 1.0)
    base = plant
    p = copy.deepcopy(plant)
    changes = TS.plan_changes(p, start, end, min_cov)
    info.total_vars = len(base.sources) + len(base.outlets) + len(base.subsystems) * len(_FLOW_FIELDS)
    used: Set[str] = {c.key for c in changes if c.new is not None and _is_flow_key(c.key)}
    TS.commit(p, [c for c in changes if c.new is not None])
    info.applied = len(used)
    for c in changes:
        if c.new is None and _is_flow_key(c.key):
            info.notes.append(f"{c.key}:{c.note}")
    if not used:
        info.empty = True
        return p, info

    old_in = {s.name: s.total_intake for s in base.subsystems}
    touched_out: Set[Tuple[str, str]] = set()           # (subsystem, outlet) already set from data

    # rule 2: spread meter-level data over the subsystems of the base model
    sub_intake_data = any(k.startswith("sub:") and k.endswith(":intake") for k in used)
    sub_disch_data = any(k.startswith("sub:") and k.endswith(":discharge") for k in used)
    for s in p.sources:
        key = f"source:{s.name}"
        if key in used and not sub_intake_data and s.metered_m3 is not None:
            cur = sum(x.intake.get(s.name, 0.0) for x in p.subsystems)
            if cur > 0:
                k = s.metered_m3 / cur
                for x in p.subsystems:
                    if s.name in x.intake:
                        x.intake[s.name] *= k
            elif len([x for x in p.subsystems if s.name in x.intake]) == 0:
                info.notes.append(f"{key}:no_subsystem")
    for o in p.outlets:
        key = f"outlet:{o.name}"
        if key in used and not sub_disch_data and o.metered_m3 is not None:
            cur = sum(x.discharge.get(o.name, 0.0) for x in p.subsystems)
            if cur > 0:
                k = o.metered_m3 / cur
                for x in p.subsystems:
                    if o.name in x.discharge:
                        x.discharge[o.name] *= k
                        touched_out.add((x.name, o.name))
    if sub_disch_data:
        for x in p.subsystems:
            if f"sub:{x.name}:discharge" in used:
                touched_out.update((x.name, o) for o in x.discharge)

    # rule 3: what has no data of its own
    if fill == "prorate":
        ratio = {}
        for x in p.subsystems:
            o = old_in.get(x.name, 0.0)
            ratio[x.name] = x.total_intake / o if o > 0 else 1.0
        for x in p.subsystems:
            r = ratio[x.name]
            for attr, f in (("evaporation", "evaporation"), ("product", "product"), ("recirculated", "recirc")):
                if f"sub:{x.name}:{f}" not in used and getattr(x, attr) > 0 and abs(r - 1.0) > 1e-12:
                    setattr(x, attr, getattr(x, attr) * r)
                    info.estimated += 1
            for oname in list(x.discharge):
                if (x.name, oname) not in touched_out and abs(r - 1.0) > 1e-12 and x.discharge[oname] > 0:
                    x.discharge[oname] *= r
                    info.estimated += 1
        if p.links:
            for lk in p.links:
                lk.volume *= ratio.get(lk.src, 1.0)
            sync_transfers(p, touched=[x.name for x in p.subsystems])
            info.estimated += len(p.links)

    if basis == "period":
        scale_volumes(p, info.scale)
    return p, info


def intake_by_slice(plant: Plant, slices) -> List[float]:
    """Total intake of every slice from the series (None where nothing is known); for the time strip."""
    return TS.total_intake_slices(plant, slices)

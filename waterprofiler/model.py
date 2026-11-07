"""Data model for a plant: sources, outlets, treatments, subsystems, costs."""

from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict, fields
from typing import Dict, List, Optional

SQRT3 = math.sqrt(3.0)


@dataclass
class Pump:
    """A pump or motor serving a subsystem.

    Either give ``power_kw`` (or ``power_hp``) with ``efficiency``, or give
    electrical readings (volts, amps, power_factor, phases).
    """
    name: str = "pump"
    hours_per_year: float = 0.0
    power_kw: Optional[float] = None
    power_hp: Optional[float] = None
    efficiency: float = 0.9
    load_fraction: float = 1.0
    volts: Optional[float] = None
    amps: Optional[float] = None
    power_factor: float = 0.85
    phases: int = 3

    def kwh_per_year(self) -> float:
        h = self.hours_per_year
        if self.volts and self.amps:
            k = 1.0 if self.phases == 1 else SQRT3
            return self.volts * self.amps * k * self.power_factor / 1000.0 * h
        eta = self.efficiency if self.efficiency > 0 else 1.0
        if self.power_kw is not None:
            return self.power_kw * self.load_fraction * h / eta
        if self.power_hp is not None:
            return self.power_hp * 0.746 * self.load_fraction * h / eta
        return 0.0


@dataclass
class Source:
    name: str
    unit_cost: float = 0.0              # currency per m3
    metered_m3: Optional[float] = None  # plant-level meter / bill, m3/year
    alternative: bool = False           # non-conventional source: reclaimed, rain, seawater, ...


@dataclass
class Outlet:
    name: str
    unit_cost: float = 0.0              # currency per m3 discharged
    metered_m3: Optional[float] = None


@dataclass
class Treatment:
    name: str
    unit_cost: float = 0.0              # lump-sum currency per m3 treated
    wastewater: bool = False            # True: treats wastewater, False: treats water


@dataclass
class Subsystem:
    name: str
    kind: str = "process"               # process, cooling_tower, boiler, sanitary, irrigation, other
    # --- flows, m3/year
    area: str = ""                      # site area (department) this subsystem belongs to
    intake: Dict[str, float] = field(default_factory=dict)         # by source name
    recirculated: float = 0.0           # R_x, recirculated inside the subsystem
    from_other: float = 0.0             # R'_in
    to_other: float = 0.0               # R'_out
    product: float = 0.0                # P_x
    evaporation: float = 0.0            # E_x
    discharge: Dict[str, float] = field(default_factory=dict)      # by outlet name
    # --- true-cost inputs
    water_treatments: Dict[str, float] = field(default_factory=dict)       # treatment -> fraction of (intake+R_in) treated
    wastewater_treatments: Dict[str, float] = field(default_factory=dict)  # treatment -> fraction of W treated
    pumps: List[Pump] = field(default_factory=list)
    t_intake_c: Optional[float] = None
    t_wastewater_c: Optional[float] = None
    # --- optional estimation parameters (kept so the UI can re-derive flows)
    params: Dict[str, float] = field(default_factory=dict)

    @property
    def total_intake(self) -> float:
        return sum(self.intake.values())

    @property
    def total_discharge(self) -> float:
        return sum(self.discharge.values())


@dataclass
class UnitCosts:
    electricity_per_kwh: float = 0.0
    fuel_per_kwh_thermal: float = 0.0
    heating_efficiency: float = 0.8


@dataclass
class PlanItem:
    """One water-saving measure selected for the implementation plan."""
    measure_id: int
    target: str = ""                # subsystem name, or "" for plant-wide
    saving_pct: float = 0.0         # expected saving, % of the target's baseline intake
    capex: float = 0.0              # currency
    opex: float = 0.0               # currency per year
    life_years: float = 15.0
    rate_pct: float = 10.0          # discount rate, %
    note: str = ""
    # --- economic evaluation inputs (see economics.py); zeros keep the simple behaviour
    water_price: float = 0.0        # value of a saved m3, currency; 0 = derive from the plant's prices
    price_esc_pct: float = 0.0      # yearly rise of water value and other benefits, %
    opex_esc_pct: float = 0.0       # yearly rise of operating cost, %
    degradation_pct: float = 0.0    # yearly loss of the saving, %
    other_benefit: float = 0.0      # other annual benefit (energy, chemicals ...), currency
    salvage: float = 0.0            # residual value at the end of the life
    repl_year: float = 0.0          # year of a one-off replacement (0 = none)
    repl_cost: float = 0.0


@dataclass
class Transfer:
    """Water sent from one subsystem to another (m3/year); used by the flow diagram."""
    src: str
    dst: str
    volume: float = 0.0


@dataclass
class Profile:
    """Optional plant-level inputs for the indicators page. 0 means "not entered"."""
    employees: float = 0.0
    working_days: float = 0.0
    building_area_m2: float = 0.0
    green_area_m2: float = 0.0
    revenue: float = 0.0               # currency per year
    value_added: float = 0.0
    production_cost: float = 0.0
    steam_t: float = 0.0               # steam produced, t/year
    permit_m3: float = 0.0             # licensed abstraction, m3/year
    storage_m3: float = 0.0
    outage_hours: float = 0.0          # hours per year without supply
    network_km: float = 0.0            # water mains length
    connections: float = 0.0
    pressure_m: float = 0.0            # average operating pressure, m of water
    ef_el: float = 0.0                 # kg CO2 per kWh electricity
    ef_th: float = 0.0                 # kg CO2 per kWh thermal
    cod_raw: float = 0.0               # mg/L, wastewater before treatment
    cod_out: float = 0.0               # mg/L, discharged
    tds_intake: float = 0.0            # mg/L
    tds_discharge: float = 0.0
    target_intake_per_unit: float = 0.0
    target_recirc_pct: float = 0.0
    target_loss_pct: float = 0.0
    sub_production: Dict[str, float] = field(default_factory=dict)   # subsystem -> product units per year


@dataclass
class Area:
    """A department of the site (production hall, utilities, administration, landscape ...)."""
    name: str
    kind: str = "production"            # production, utility, services, landscape, other
    note: str = ""


@dataclass
class Product:
    name: str
    unit: str = "t"
    note: str = ""


@dataclass
class Meter:
    name: str
    tag: str = ""                       # time-series key the meter feeds, e.g. "source:Well"
    kind: str = "flow"                  # flow, energy, quality, level, other
    unit: str = "m3"
    accuracy_pct: float = 0.0
    installed: str = ""                 # ISO date
    calibrated: str = ""                # ISO date of the last calibration
    note: str = ""


@dataclass
class Site:
    """Descriptive data of the industrial site."""
    name: str = ""
    owner: str = ""
    sector: str = ""
    province: str = ""
    city: str = ""
    address: str = ""
    latitude: float = 0.0
    longitude: float = 0.0
    land_area_m2: float = 0.0
    commissioning_year: int = 0
    shifts_per_day: int = 0
    operating_days: int = 0
    calendar: str = "jalali"            # jalali | gregorian: how periods are labelled and sliced
    note: str = ""
    areas: List[Area] = field(default_factory=list)
    products: List[Product] = field(default_factory=list)
    meters: List[Meter] = field(default_factory=list)


@dataclass
class Point:
    """One observation of a time series: a value valid for the whole interval start..end (ISO dates, inclusive)."""
    start: str
    end: str
    value: float


@dataclass
class Plant:
    name: str = "Plant"
    period: str = "year"
    currency: str = "IRR"
    production_per_year: float = 0.0
    production_unit: str = "t"
    sources: List[Source] = field(default_factory=list)
    outlets: List[Outlet] = field(default_factory=list)
    treatments: List[Treatment] = field(default_factory=list)
    subsystems: List[Subsystem] = field(default_factory=list)
    costs: UnitCosts = field(default_factory=UnitCosts)
    plan: List[PlanItem] = field(default_factory=list)
    links: List[Transfer] = field(default_factory=list)
    layout: Dict[str, float] = field(default_factory=dict)   # flow-diagram node id -> vertical sort key
    profile: Profile = field(default_factory=Profile)
    site: Site = field(default_factory=Site)
    series: Dict[str, List[Point]] = field(default_factory=dict)    # time series by variable key
    window: Dict[str, str] = field(default_factory=dict)            # reference window: {"start": iso, "end": iso}
    ts_view: Dict[str, str] = field(default_factory=dict)           # time-series view: step, start, end, chart (bar | line)

    def source(self, name: str) -> Source:
        for s in self.sources:
            if s.name == name:
                return s
        raise KeyError(f"unknown source: {name}")

    def outlet(self, name: str) -> Outlet:
        for o in self.outlets:
            if o.name == name:
                return o
        raise KeyError(f"unknown outlet: {name}")

    def treatment(self, name: str) -> Treatment:
        for t in self.treatments:
            if t.name == name:
                return t
        raise KeyError(f"unknown treatment: {name}")

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "Plant":
        subs = []
        for sd in d.get("subsystems", []):
            sd = dict(sd)
            sd["pumps"] = [Pump(**p) for p in sd.get("pumps", [])]
            subs.append(Subsystem(**sd))
        plant = Plant(
            name=d.get("name", "Plant"),
            period=d.get("period", "year"),
            currency=d.get("currency", "IRR"),
            production_per_year=d.get("production_per_year", 0.0),
            production_unit=d.get("production_unit", "t"),
            sources=[Source(**s) for s in d.get("sources", [])],
            outlets=[Outlet(**o) for o in d.get("outlets", [])],
            treatments=[Treatment(**t) for t in d.get("treatments", [])],
            subsystems=subs,
            costs=UnitCosts(**d.get("costs", {})),
            plan=[PlanItem(**p) for p in d.get("plan", [])],
            links=[Transfer(**x) for x in d.get("links", [])],
            layout={str(k): float(v) for k, v in d.get("layout", {}).items()},
            profile=_profile_from(d.get("profile", {})),
            site=_site_from(d.get("site", {})),
            series={str(k): [Point(**p) for p in v] for k, v in d.get("series", {}).items()},
            window={str(k): str(v) for k, v in d.get("window", {}).items()},
            ts_view={str(k): str(v) for k, v in d.get("ts_view", {}).items()},
        )
        # older files kept the working days on the site page; there is now one field (the profile's)
        if plant.site.operating_days and not plant.profile.working_days:
            plant.profile.working_days = float(plant.site.operating_days)
        plant.site.operating_days = 0
        return plant


def _profile_from(d: dict) -> Profile:
    names = {f.name for f in fields(Profile)}
    kw = {k: v for k, v in (d or {}).items() if k in names and k != "sub_production"}
    kw["sub_production"] = {str(k): float(v) for k, v in (d or {}).get("sub_production", {}).items()}
    return Profile(**kw)


def _site_from(d: dict) -> Site:
    d = dict(d or {})
    names = {f.name for f in fields(Site)}
    kw = {k: v for k, v in d.items() if k in names and k not in ("areas", "products", "meters")}
    return Site(**kw, areas=[Area(**a) for a in d.get("areas", [])], products=[Product(**p) for p in d.get("products", [])],
                meters=[Meter(**m) for m in d.get("meters", [])])


def sync_transfers(plant: Plant, touched=()) -> None:
    """Set R'_out / R'_in of every subsystem named in ``plant.links`` (or in ``touched``) to the link sums."""
    names = {x.src for x in plant.links} | {x.dst for x in plant.links} | set(touched)
    for s in plant.subsystems:
        if s.name in names:
            s.to_other = sum(x.volume for x in plant.links if x.src == s.name)
            s.from_other = sum(x.volume for x in plant.links if x.dst == s.name)

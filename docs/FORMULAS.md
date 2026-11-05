# Mathematical formulation

Methodology: Malhotra et al., *Plant Water Profiler*, ORNL/TM-2021/1918. All
volumes are stored in **m³/year**; the report's US units are converted at the
boundary (1 US gal = 0.003785411784 m³, 1 lb = 0.45359237 kg, 1 refrigeration
ton = 3.516852842 kW). Every formula below is covered by a hand-calculation test
in `tests/test_engine.py`.

Notation per subsystem *x*: **I** intake (by source), **W** discharge (by outlet),
**E** evaporation / consumptive loss, **P** water leaving in product,
**R** internal recirculation, **R′in / R′out** water received from / sent to another
subsystem, **G** gross use, **M** makeup, **B** blowdown, **C** cycles of concentration.

## 1. Bottom-up estimators (`estimators.py`)

| Subsystem | Equations |
|---|---|
| Process | G = N·g, R = G·r, M = G − R, P = N·p |
| Cooling tower | G = 3 gpm/ton · tons · L · H · 60;  E = G · (e/100) · ΔT[°F]/10 (e = 0.85 %/10 °F);  C = B꜀/M꜀;  M = E / (1 − 1/C);  B = M − E;  R = G − M |
| Once-through cooling | I = G = W = F·H |
| Boiler | C = B꜀/F꜀;  S = BHP · 34.5 lb/h;  G = S / (1 − 1/C) · L · H;  M = (F꜀/M꜀) · G;  B = G / C;  E = M − B;  R = G − M |
| Sanitary | G = employees · days · litres per person-day |
| Landscaping | G = E = area · depth |

Conductivity ratios must give C > 1; boiler feedwater cannot be dirtier than
makeup. Violations raise `InputError` rather than returning a number.

## 2. Water balance (`balance.py`)

Subsystem unaccounted loss:

    L_x = (I_x + R′in) − (R′out + P_x + E_x + W_x)

Plant:

    L = ΣI − (ΣP + ΣE + ΣW)

Calibration compares ΣI with the metered / billed source water and ΣW with the
metered discharge. Plant closure error = metered intake − metered discharge − ΣE − ΣP.
A subsystem is flagged *unaccounted loss* (L > 2 % of inflow, typically leaks),
*excess outflow* (L < −2 %, inconsistent data) or *balanced*.
Inter-subsystem transfers must cancel across the plant; otherwise a warning is raised.

## 3. True cost of water (`cost.py`)

    $_x = Σ_s $_s·I_x,s + Σ_o $_o·W_x,o + Σ_t $_t·T_x,t + Σ_t′ $_t′·T′_x,t′ + $_elec·Elec_x + $_fuel·Fuel_x

* T_x,t = f_x,t · (I_x + R′in): water treated before use, f = fraction treated.
* T′_x,t′ = f_x,t′ · W_x: wastewater treated before discharge.
* Pump energy: kW · load · H / η, or V·A·pf·H/1000 (single phase) and ×√3 (three phase).
* Heat lost in discharge: Fuel = ρ·W·c_p·(T_ww − T_in) / 3600 / η_heating, zero when
  the wastewater is not warmer than the intake.

Reported: cost by component and subsystem, true-to-direct cost ratio
(direct = source water + discharge fees), true cost per m³ of intake, and shares of
water vs cost per subsystem. A subsystem is prioritised for *volume* (≥ 20 % of
intake), *cost* (cost share ≥ 1.25 × water share and ≥ 5 %), or *both*.

## 4. Savings (`savings.py`)

* Cooling tower: M′ = E / (1 − 1/C′); ΔM = M − M′; ΔB = B − B′ (B′ = M′ − E).
* Boiler: G′ = S/(1 − 1/C′)·L·H; M′ = (1 − r′)·G′; B′ = G′/C′.
* Leaks: saved = rate · hours · fraction fixed.
* Cost saving = ΔM·($_water + $_treatment) + ΔB·$_discharge; payback = capex / annual saving.

## 5. Data reconciliation (`reconcile.py`, an addition to the report)

Minimise Σ((x − x₀)/σ)² subject to A·x = b, where the constraints are: every
subsystem closes, inter-subsystem transfers cancel, and plant meters per source /
outlet are matched exactly. Closed form x = x₀ − S·Aᵀ(A·S·Aᵀ)⁺(A·x₀ − b) with
S = diag(σ²). Negative results are pinned at zero and the problem re-solved.
Flows with larger relative uncertainty move more.

## 6. Corrections to the printed report

| Report | This implementation |
|---|---|
| Signs in the L_x equation inconsistent | L_x = inflows − outflows, R cancels |
| Landscaping formula contains an extra D | G = 0.623·d·A (US) / area·depth (SI) |
| Heat-in-wastewater sign reversed | positive when wastewater is warmer than intake |
| Motor efficiency multiplied | divided (input power = shaft power / η) |

## 7. Not included

Industry-average comparison tables (EIO-LCA, STATCAN) are not in the report and
are not bundled. Drift loss in cooling towers is ignored, as in the report.

## Water-saving measures database and plan (v0.3)

The database (`waterprofiler/measures_data.py`) holds 80 measures in 9 groups taken from the supplied study
"comprehensive list of industrial water-saving measures". Percentages are engineering screening ranges, not
guarantees; investment is a qualitative level (low / medium / high / very high), never an invented price.

Plan economics, with `Qs` the combined saving in m3/day, `N` the life in years and `i` the discount rate:

    I1  = CAPEX / Qs                         [currency per (m3/day)]
    I2  = CAPEX / (Qs * 365 * N)             [currency per m3 over the life]
    CRF = i (1+i)^N / ((1+i)^N - 1)
    LCC = (CRF * CAPEX + OPEX) / (Qs * 365)  [currency per m3]
    payback = CAPEX / (annual value - OPEX)

Measures acting on the same target combine as `1 - prod(1 - p_k)` (never added); plant-wide measures act on
the intake left after subsystem-level savings. The annual value of saved water uses the source price plus water
treatment of the target (discharge price excluded). Worked example of the study: 500 000 currency, 200 m3/day,
15 years -> I1 = 2500, I2 = 0.457.

## Economic evaluation of a measure (`waterprofiler/economics.py`)

Year-end cash flows; `Qs` first-year saving (m3), `p` value of a saved m3 (purchase + treatment + wastewater,
automatic unless entered), `g` price escalation, `e` cost escalation, `d` yearly loss of the saving, `r` discount rate.

    CF_0 = -CAPEX
    CF_t = (Qs (1-d)^(t-1) p + other) (1+g)^(t-1) - OPEX (1+e)^(t-1) - [t = T_r] REPL      t = 1..N   (+ SALVAGE at t = N)

    NPV  = sum_t CF_t / (1+r)^t                       IRR: rate with NPV = 0 (None without a sign change)
    SPB  = first t with sum CF >= 0 (linear in the year)       DPB = same on discounted flows
    BCR  = PV(benefits + salvage) / PV(CAPEX + OPEX + REPL)     PI = NPV / CAPEX
    LCOW = PV(CAPEX + OPEX + REPL - SALVAGE) / PV(saved volume)   (cost of one saved m3)

Check values used in the tests: CAPEX 1000, 300 per year for 5 years at 10 % gives NPV 137.24, IRR 15.24 %,
simple payback 3.33 y, discounted payback 4.26 y, BCR 1.137. The study's example (500 000 for 200 m3/day,
15 years, 10 %) gives an undiscounted cost of 0.4566 per m3 (I2) and a levelised cost of 0.9005 per m3.
Tax, loans and depreciation are not modelled.

## Supply and consumption indicators

`waterprofiler/indicators.py` derives about 80 indicators from the balance, the true-cost model, the pumps and the
saving plan, plus optional plant-level inputs (`Plant.profile`; 0 = not entered). An indicator whose inputs are
missing is shown as "—" together with the inputs it needs; nothing is estimated. Notation: I intake, R internal
recirculation, R'in water received from another subsystem, W discharge, E evaporation, P water in product, L
unaccounted loss, Q production.

| Group | Main indicators and formulas |
|---|---|
| Supply | largest source share `max(I_s)/ΣI`; concentration `Σ(I_s/ΣI)²` (HHI); alternative-source share; average price `Σ$_s·I_s/ΣI`; metering coverage; permit use `ΣI/permit`; storage days `storage/(ΣI/365)`; availability `1 − outage/8760`; pumping kWh and kWh/m³; ILI `L/UARL` with `UARL=(18·Lm+0.8·Nc)·P·365/1000` (approximate: all unaccounted loss is taken as leakage) |
| Use and reuse | gross use `ΣI+ΣR+ΣR'in`; reuse factor `gross/ΣI`; recirculation, internal and transfer shares; consumption `ΣI−ΣW` (GRI 303-5); consumptive, evaporation and product shares; loss; intensity `ΣI/Q`; per employee, area, revenue, value added; water per steam |
| Wastewater | `ΣW/ΣI`; treated share; outlet share and price; COD load `COD·W/1000`, COD removal `1−COD_out/COD_raw`; TDS load `(TDS_out·W−TDS_in·I)/1000`; concentration factor |
| Cost and energy | true cost, per m³ and per unit; component shares; heat lost; CO₂ `kWh_el·EF_el+kWh_th·EF_th` |
| Quality | closure, source- and discharge-side gaps, metering coverage, share of balanced subsystems, input completeness |
| Targets | gap of intensity, recirculation and loss to targets entered by the user (no industry averages) |
| Plan | combined saving, intake and intensity after the plan, value, capital per m³/day |

Per subsystem: shares of intake and cost, cost per m³, loss, consumptive / discharge / transfer / recirculation
shares, intensity (with production entered), cycles of concentration `bc/mc`, blowdown / make-up, litres per person
per day. Monthly indicators (peaking factors, trends, seasonality) need monthly data and are not included yet.

## Flow and balance in time (`waterprofiler/periodview.py`, v0.8)

The flow page shows the base (annual) model or one period of the time series. The base model is never changed:
the period view works on a copy.

1. Every flow variable with data in the period (source, outlet, subsystem intake / discharge / evaporation / product /
   recirculation) replaces its value; series values are annualised (sums × 365 / days) as in the time-series plan.
2. A source or outlet with data but without subsystem-level data is spread over the subsystems that use it, in
   proportion to their share in the base model.
3. Flows without data of their own follow the subsystem's intake of the period: x_period = x_base · (intake_period / intake_base)
   (`fill = prorate`), or keep the base value (`fill = base`). The number of such estimated values is shown.
4. Volume scale: "in the period" multiplies all volumes by days / 365; "annualised rate" keeps m3/year.

The balance table, totals and chart of the page are the ordinary water balance (section 2) of that adjusted copy.
During auto-play the thickness scale of the diagram is fixed to the largest column total of all periods, so frames
are visually comparable.

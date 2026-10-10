"""Expected daily demand per (date, store, product) and the noisy realized demand.

units = base_daily_demand
        x store_scale[size] x store_product_affinity
        x zone_multiplier[zone, category/tag]          (70% primary zone, 30% secondary zone)
        x weekday_multiplier[zone, dow] x category_weekday[category, dow]
        x weather_multiplier[weather_label, weather_sensitivity] x temperature_slope
        x event_multiplier[event, category/tag, zone]  (holidays, seasons, sports, vacations, paydays)
        x annual_trend
realized = Poisson(units x Gamma(k=8, theta=1/8))       (negative-binomial over-dispersion)
"""
from dataclasses import dataclass

import numpy as np
import pandas as pd

from build_calendar_weather import COLLEGE_VACATIONS, SEP_VACATIONS, build_events, in_windows
from catalog_data import PRODUCTS, SEASON_WINDOWS, WEEKDAY_MULT
from common import DATES, rng

SIZE_SCALE = {"small": 0.8, "medium": 1.0, "large": 1.25}
ANNUAL_GROWTH = 0.035
GAMMA_SHAPE = 8.0

# Zone x (category key or tag) multipliers; anything not listed is 1.0.
ZONE_MULT = {
    "residential": {"gro": 1.4, "cln": 1.5, "hyg": 1.3, "dai": 1.4, "alc": 1.2, "ice": 1.2, "bby": 1.4, "pet": 1.5,
                    "bak": 1.2, "dsp": 1.2, "prep": 0.85, "cof": 0.85, "soft_drink_large": 1.3},
    "business": {"cof": 1.9, "prep": 1.8, "bak": 1.3, "snk": 1.1, "bev": 1.1, "alc": 0.55, "gro": 0.6, "cln": 0.4,
                 "bby": 0.3, "pet": 0.3, "dai": 0.9, "ice": 0.6, "cig": 1.1},
    "school": {"cdy": 2.1, "snk": 1.7, "bev": 1.2, "bak": 1.3, "frz": 1.4, "dai": 1.1, "alc": 0.15, "cig": 0.2,
               "phm": 0.6, "cof": 0.6, "bby": 0.6, "cln": 0.6, "energy": 0.3, "juice": 1.5, "chocolate_milk": 1.6},
    "college": {"alc": 1.9, "cof": 1.6, "prep": 1.5, "snk": 1.4, "cig": 1.4, "bev": 1.1, "dsp": 1.4, "phm": 1.1,
                "ice": 1.4, "bby": 0.2, "pet": 0.3, "cln": 0.5, "gro": 0.9, "energy": 1.6, "hangover": 1.4},
    "highway": {"snk": 1.8, "bev": 1.5, "cof": 1.8, "ice": 1.3, "prep": 1.2, "cig": 1.3, "alc": 0.6, "gro": 0.5,
                "cln": 0.3, "hyg": 0.7, "bby": 0.7, "pet": 0.4, "dai": 0.7, "water": 1.5, "energy": 1.6,
                "soft_drink": 1.2, "soft_drink_large": 0.7},
    "industrial": {"prep": 1.9, "cof": 1.5, "cig": 1.6, "bev": 1.3, "snk": 1.3, "alc": 0.9, "bak": 1.2, "gro": 0.7,
                   "cln": 0.4, "bby": 0.3, "pet": 0.3, "dsp": 0.6, "energy": 1.5},
    "shopping_center": {"cdy": 1.3, "frz": 1.5, "bev": 1.2, "snk": 1.2, "flw": 1.6, "alc": 0.8, "gro": 0.7,
                        "cln": 0.6, "cig": 0.8, "gift_chocolate": 1.5},
    "hospital": {"cof": 1.7, "phm": 2.5, "flw": 2.6, "bby": 1.7, "hyg": 1.6, "prep": 1.4, "bev": 1.1, "alc": 0.25,
                 "cig": 0.7, "dsp": 0.7, "ice": 0.6, "water": 1.3},
}

# Category x weekday (Mon..Sun) multipliers on top of the zone weekday profile.
CATEGORY_WEEKDAY = {
    "alc": [0.80, 0.80, 0.85, 1.00, 1.35, 1.55, 1.05],
    "cof": [1.08, 1.08, 1.08, 1.08, 1.05, 0.85, 0.80],
    "prep": [1.05, 1.05, 1.05, 1.05, 1.05, 0.90, 0.85],
    "ice": [0.85, 0.85, 0.90, 0.95, 1.15, 1.35, 1.15],
}
COLLEGE_ALCOHOL_WEEKDAY = [0.85, 0.85, 1.00, 1.35, 1.40, 1.20, 0.80]

WEATHER_MULT = {
    "hot": {"hot": 1.30, "mild": 1.00, "cold": 0.72, "rainy": 0.85},
    "cold": {"hot": 0.75, "mild": 1.00, "cold": 1.40, "rainy": 1.15},
    "neutral": {"hot": 1.00, "mild": 1.00, "cold": 0.97, "rainy": 0.93},
}

# Zone intensity for party-type events: effective factor = 1 + (factor - 1) x boost.
PARTY = {"residential": 1.3, "college": 1.3, "highway": 0.7, "business": 0.6, "industrial": 0.8, "school": 0.4,
         "hospital": 0.4, "shopping_center": 0.8}
SOCCER = {**PARTY, "business": 0.8}
# Closed-office effect for business-like zones on official holidays.
WORKPLACE_ZONES = ("business", "industrial", "school", "college")

# event key -> list of (targets, factor, zone_boost or None, zones restriction or None)
# targets: "all" or a tuple of category keys / tags.
EVENT_RULES = {
    "independence_eve": [(("beer",), 2.4, PARTY, None), (("spirits",), 2.2, PARTY, None),
                         (("snk",), 1.7, PARTY, None), (("dip",), 1.9, PARTY, None), (("ice",), 2.3, PARTY, None),
                         (("soft_drink", "soft_drink_large"), 1.6, PARTY, None), (("dsp",), 2.0, PARTY, None)],
    "independence": [(("beer",), 2.4, PARTY, None), (("spirits",), 1.6, PARTY, None), (("snk",), 1.5, PARTY, None),
                     (("ice",), 1.8, PARTY, None), (("dip",), 1.5, PARTY, None),
                     (("soft_drink", "soft_drink_large"), 1.4, PARTY, None), (("hangover",), 1.6, None, None)],
    "super_bowl": [(("beer",), 1.9, PARTY, None), (("snk",), 1.7, PARTY, None), (("dip",), 2.0, PARTY, None),
                   (("ice",), 1.7, PARTY, None), (("soft_drink", "soft_drink_large"), 1.4, PARTY, None),
                   (("dsp",), 1.5, PARTY, None)],
    "mexico_match": [(("beer",), 2.2, SOCCER, None), (("snk",), 1.8, SOCCER, None), (("dip",), 2.0, SOCCER, None),
                     (("ice",), 1.9, SOCCER, None), (("soft_drink", "soft_drink_large"), 1.5, SOCCER, None),
                     (("dsp",), 1.4, SOCCER, None)],
    "world_cup": [(("beer",), 1.12, None, None), (("snk",), 1.08, None, None)],
    "liga_mx_playoffs": [(("beer",), 1.2, PARTY, None), (("snk",), 1.15, PARTY, None), (("dip",), 1.15, PARTY, None)],
    "christmas_eve": [(("alc",), 2.4, PARTY, None), (("soft_drink_large",), 2.2, PARTY, None),
                      (("ice",), 2.6, PARTY, None), (("dsp",), 2.6, PARTY, None), (("snk",), 1.5, PARTY, None),
                      (("dip",), 1.6, PARTY, None), ("all", 0.8, None, WORKPLACE_ZONES)],
    "new_years_eve": [(("alc",), 2.6, PARTY, None), (("soft_drink_large",), 2.2, PARTY, None),
                      (("ice",), 2.8, PARTY, None), (("dsp",), 2.6, PARTY, None), (("snk",), 1.5, PARTY, None),
                      (("dip",), 1.6, PARTY, None), ("all", 0.8, None, WORKPLACE_ZONES)],
    "christmas": [("all", 0.8, None, None), (("hangover",), 1.5, None, None)],
    "new_year": [("all", 0.8, None, None), (("hangover",), 2.0, None, None), (("water",), 1.4, None, None),
                 (("soup",), 1.5, None, None), (("ice",), 1.2, None, None)],
    "guadalupe_reyes": [(("alc",), 1.15, None, None), (("snk",), 1.08, None, None),
                        (("sweet_bread",), 1.1, None, None), (("dsp",), 1.15, None, None)],
    "three_kings_eve": [(("bak",), 1.2, None, None), (("hot_chocolate",), 1.3, None, None)],
    "three_kings": [(("bak",), 1.4, None, None), (("hot_chocolate",), 1.6, None, None), (("milk",), 1.4, None, None)],
    "candlemas": [(("tamales",), 3.5, None, None), (("hot_chocolate",), 1.5, None, None)],
    "valentines_eve": [(("chocolate",), 1.5, None, None), (("flowers",), 2.0, None, None)],
    "valentines": [(("chocolate",), 2.6, None, None), (("flowers",), 5.0, None, None), (("cdy",), 1.3, None, None)],
    "mothers_eve": [(("flowers",), 2.5, None, None), (("chocolate",), 1.3, None, None)],
    "mothers_day": [(("flowers",), 6.0, None, None), (("chocolate",), 2.0, None, None)],
    "fathers_day": [(("beer",), 1.4, PARTY, None), (("spirits",), 1.5, PARTY, None), (("ice",), 1.3, PARTY, None)],
    "children_day": [(("cdy",), 1.8, None, None), (("snk",), 1.3, None, None), (("frz",), 1.4, None, None),
                     (("juice",), 1.3, None, None)],
    "halloween": [(("cdy",), 2.4, None, None)],
    "day_of_dead": [(("flowers",), 2.5, None, None), (("cdy",), 1.5, None, None), (("sweet_bread",), 1.6, None, None)],
    "lent": [(("tuna",), 1.5, None, None), (("prep",), 0.95, None, None)],
    "holy_week": [("all", 1.35, None, ("highway",)), (("alc",), 1.2, None, ("residential",))],
    "buen_fin": [("all", 1.35, None, ("shopping_center",)), ("all", 1.04, None,
                 ("residential", "business", "highway", "industrial", "hospital", "school", "college"))],
    "dry_law": [(("alc",), 0.05, None, None)],
    "saltillo_anniversary": [("all", 1.05, None, None), (("beer",), 1.2, None, None)],
    "santo_cristo": [("all", 1.06, None, ("residential", "business")), (("snk",), 1.1, None, None)],
    "saltillo_fair": [(("beer",), 1.15, None, None), (("snk",), 1.1, None, None), ("all", 1.03, None, None)],
    "back_to_school": [(("snk", "juice"), 1.2, None, ("school",))],
    "school_council": [("all", 0.55, None, ("school",))],
}
# Official long-weekend holidays (beyond their own rules above).
for _k in ("constitution", "juarez", "labor_day", "revolution", "inauguration", "election_day"):
    EVENT_RULES.setdefault(_k, []).extend([
        ("all", 0.5, None, WORKPLACE_ZONES), ("all", 1.25, None, ("highway",)),
        (("alc",), 1.25, None, ("residential",)), (("ice",), 1.15, None, ("residential",))])
for _k in ("independence", "christmas", "new_year"):
    EVENT_RULES[_k].append(("all", 0.6, None, WORKPLACE_ZONES))


@dataclass
class DemandModel:
    expected: np.ndarray       # float64 [days, stores, products]
    realized: np.ndarray       # int32  [days, stores, products]
    calendar_hint: np.ndarray  # float64 [days], weighted mean calendar multiplier


def _product_masks():
    keys = [p[1] for p in PRODUCTS]
    tags = [p[8] for p in PRODUCTS]

    def mask(targets):
        if targets == "all":
            return np.ones(len(PRODUCTS), bool)
        return np.array([k in targets or bool(t & set(targets)) for k, t in zip(keys, tags)])
    return mask


def _zone_matrix(zone: str, mask) -> np.ndarray:
    out = np.ones(len(PRODUCTS))
    for target, f in ZONE_MULT[zone].items():
        out[mask((target,))] *= f
    return out


def build(stores: pd.DataFrame, weather: pd.DataFrame) -> DemandModel:
    n_d, n_s, n_p = len(DATES), len(stores), len(PRODUCTS)
    mask = _product_masks()
    zones = stores.zone_type.tolist()
    zones2 = [z if isinstance(z, str) else None for z in stores.secondary_zone_type]
    dow = DATES.dayofweek.to_numpy()
    base = np.array([p[7] for p in PRODUCTS])

    r = rng(5)
    scale = np.array([SIZE_SCALE[s] for s in stores.size_category]) * r.uniform(0.9, 1.1, n_s)
    affinity = r.lognormal(0.0, 0.15, (n_s, n_p))
    zone_mult = np.empty((n_s, n_p))
    for s, (z1, z2) in enumerate(zip(zones, zones2)):
        zone_mult[s] = _zone_matrix(z1, mask) if z2 is None else 0.7 * _zone_matrix(z1, mask) + 0.3 * _zone_matrix(z2, mask)
    static = base[None, :] * scale[:, None] * affinity * zone_mult  # [S, P]

    # Weekday: zone profile (blended for secondary zone) x category profile.
    wk_zone = np.array([np.array(WEEKDAY_MULT[z1]) if z2 is None
                        else 0.7 * np.array(WEEKDAY_MULT[z1]) + 0.3 * np.array(WEEKDAY_MULT[z2])
                        for z1, z2 in zip(zones, zones2)])  # [S, 7]
    wk_cat = np.ones((7, n_p))
    for target, prof in CATEGORY_WEEKDAY.items():
        wk_cat[:, mask((target,))] = np.array(prof)[:, None]
    weekday = wk_zone[:, dow].T[:, :, None] * wk_cat[dow][:, None, :]  # [D, S, P]
    college = np.array([z == "college" for z in zones])
    alc = mask(("alc",))
    weekday[np.ix_(np.arange(n_d), college, alc)] *= (np.array(COLLEGE_ALCOHOL_WEEKDAY)[dow] /
                                                      np.array(CATEGORY_WEEKDAY["alc"])[dow])[:, None, None]

    # Weather: label table x temperature slope.
    sens = [p[6] for p in PRODUCTS]
    tmax = weather.temp_max_c.to_numpy()
    wthr = np.ones((n_d, n_p))
    for j, sv in enumerate(sens):
        wthr[:, j] = [WEATHER_MULT[sv][lab] for lab in weather.weather_label]
        if sv == "hot":
            wthr[:, j] *= np.exp(np.clip(0.02 * (tmax - 26), -0.3, 0.3))
        elif sv == "cold":
            wthr[:, j] *= np.exp(np.clip(-0.015 * (tmax - 24), -0.3, 0.3))

    # Calendar events, vacations and paydays.
    events = np.ones((n_d, n_s, n_p))
    day_index = {d: i for i, d in enumerate(DATES.date)}

    def apply(day_idx, targets, factor, boost=None, only_zones=None):
        pm = mask(targets)
        for s in range(n_s):
            if only_zones is not None and zones[s] not in only_zones:
                continue
            f = factor if boost is None else 1 + (factor - 1) * boost.get(zones[s], 1.0)
            events[np.ix_(day_idx, [s], pm)] *= f

    by_key: dict[str, list[int]] = {}
    for d, k in build_events():
        by_key.setdefault(k, []).append(day_index[d])
    for k, idx in by_key.items():
        for targets, factor, boost, only in EVENT_RULES.get(k, []):
            apply(np.array(idx), targets, factor, boost, only)

    sep_vac = np.array([d in in_windows(SEP_VACATIONS) for d in DATES.date])
    col_vac = np.array([d in in_windows(COLLEGE_VACATIONS) for d in DATES.date])
    summer = sep_vac & np.isin(DATES.month, [7, 8])
    apply(np.where(sep_vac)[0], "all", 0.42, only_zones=("school",))
    apply(np.where(sep_vac)[0], ("cdy", "snk", "juice", "chocolate_milk"), 0.75, only_zones=("school",))
    apply(np.where(sep_vac)[0], ("cdy", "frz"), 1.10, only_zones=("residential",))
    apply(np.where(summer)[0], "all", 1.12, only_zones=("highway",))
    apply(np.where(col_vac)[0], "all", 0.50, only_zones=("college",))
    apply(np.where(col_vac)[0], ("alc", "energy"), 0.60, only_zones=("college",))

    payday = np.array([(d.day == 15) or (d + pd.Timedelta(days=1)).day == 1 for d in DATES])
    after = np.r_[False, payday[:-1]]
    apply(np.where(payday | after)[0], "all", 1.06)
    apply(np.where(payday | after)[0], ("alc",), 1.12)

    static_w = static / static.sum()
    hint = (events * static_w[None]).sum(axis=(1, 2))

    # Seasonal products only sell inside their window.
    for j, p in enumerate(PRODUCTS):
        for tag, ((m1, d1), (m2, d2)) in SEASON_WINDOWS.items():
            if tag in p[8]:
                md = DATES.month * 100 + DATES.day
                inside = (md >= m1 * 100 + d1) | (md <= m2 * 100 + d2)
                peak = np.isin(md, [104, 105, 106])
                events[~inside, :, j] = 0.0
                events[inside, :, j] *= np.where(peak[inside], 25.0, 8.0)[:, None]

    years = (DATES - DATES[0]).days.to_numpy() / 365.25
    trend = (1 + ANNUAL_GROWTH) ** years

    expected = static[None, :, :] * weekday * wthr[:, None, :] * events * trend[:, None, None]
    noise = rng(6).gamma(GAMMA_SHAPE, 1 / GAMMA_SHAPE, expected.shape)
    realized = rng(7).poisson(expected * noise).astype(np.int32)
    return DemandModel(expected=expected, realized=realized, calendar_hint=hint)

"""Build calendar.csv (holidays, events, paydays, vacations) and weather.csv (synthetic Saltillo climate).

All event dates are generated from rules or chosen synthetically; nothing is looked up online.
The 2026 World Cup knockout results for Mexico are fictional.
"""
from datetime import date, timedelta

import numpy as np
import pandas as pd

from common import DATES, END, START, rng, write_csv

# event key -> (scope, display name)
EVENT_INFO = {
    "new_year": ("national", "New Year's Day"),
    "three_kings_eve": ("commercial", "Three Kings' Eve"),
    "three_kings": ("commercial", "Three Kings' Day"),
    "candlemas": ("religious", "Candlemas"),
    "constitution": ("national", "Constitution Day"),
    "super_bowl": ("sports", "Super Bowl"),
    "valentines_eve": ("commercial", "Valentine's Eve"),
    "valentines": ("commercial", "Valentine's Day"),
    "lent": ("religious", "Lent"),
    "holy_week": ("religious", "Holy Week"),
    "juarez": ("national", "Benito Juarez's Birthday"),
    "children_day": ("commercial", "Children's Day"),
    "labor_day": ("national", "Labor Day"),
    "mothers_eve": ("commercial", "Mother's Day Eve"),
    "mothers_day": ("commercial", "Mother's Day"),
    "election_day": ("national", "Federal Election Day"),
    "dry_law": ("national", "Dry Law (alcohol sales ban)"),
    "fathers_day": ("commercial", "Father's Day"),
    "liga_mx_playoffs": ("sports", "Liga MX Playoffs Match Day"),
    "world_cup": ("sports", "FIFA World Cup 2026"),
    "mexico_match": ("sports", "Mexico Match Day"),
    "saltillo_anniversary": ("local", "Saltillo City Anniversary"),
    "saltillo_fair": ("local", "Saltillo Fair"),
    "santo_cristo": ("local", "Santo Cristo de la Capilla Festivities"),
    "back_to_school": ("school", "Back to School"),
    "independence_eve": ("national", "Independence Eve (El Grito)"),
    "independence": ("national", "Independence Day"),
    "inauguration": ("national", "Presidential Inauguration Day"),
    "halloween": ("commercial", "Halloween"),
    "day_of_dead": ("religious", "Day of the Dead"),
    "buen_fin": ("commercial", "Buen Fin"),
    "revolution": ("national", "Revolution Day"),
    "guadalupe": ("religious", "Our Lady of Guadalupe Day"),
    "guadalupe_reyes": ("commercial", "Guadalupe-Reyes Season"),
    "christmas_eve": ("commercial", "Christmas Eve"),
    "christmas": ("national", "Christmas Day"),
    "new_years_eve": ("commercial", "New Year's Eve"),
    "school_council": ("school", "School Technical Council (no classes)"),
}
OFFICIAL_HOLIDAYS = {"new_year", "constitution", "juarez", "labor_day", "election_day", "independence",
                     "inauguration", "revolution", "christmas"}

# SEP (K-12) vacation windows, synthetic approximations of the official calendar.
SEP_VACATIONS = [
    ("2024-01-01", "2024-01-07"), ("2024-03-25", "2024-04-05"), ("2024-07-16", "2024-08-25"),
    ("2024-12-21", "2025-01-07"), ("2025-04-14", "2025-04-25"), ("2025-07-16", "2025-08-31"),
    ("2025-12-20", "2026-01-11"), ("2026-03-30", "2026-04-10"), ("2026-07-16", "2026-08-30"),
]
# University (UAdeC / Tec style semesters) vacation windows, synthetic.
COLLEGE_VACATIONS = [
    ("2024-01-01", "2024-01-21"), ("2024-03-23", "2024-04-07"), ("2024-06-01", "2024-08-11"),
    ("2024-12-14", "2025-01-19"), ("2025-04-12", "2025-04-27"), ("2025-06-01", "2025-08-10"),
    ("2025-12-13", "2026-01-18"), ("2026-03-28", "2026-04-12"), ("2026-05-30", "2026-08-09"),
]
# Mexico's 2026 World Cup matches: group stage plus a fictional Round of 32 win and Round of 16 exit.
MEXICO_MATCHES = ["2026-06-11", "2026-06-18", "2026-06-24", "2026-06-30", "2026-07-05"]


def nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    d = date(year, month, 1)
    d += timedelta(days=(weekday - d.weekday()) % 7)
    return d + timedelta(weeks=n - 1)


def last_weekday(year: int, month: int, weekday: int) -> date:
    d = date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def easter(year: int) -> date:
    """Anonymous Gregorian algorithm."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = (h + l - 7 * m + 114) % 31 + 1
    return date(year, month, day)


def date_span(a, b):
    a, b = pd.Timestamp(a).date(), pd.Timestamp(b).date()
    return [a + timedelta(days=i) for i in range((b - a).days + 1)]


def in_windows(windows) -> set:
    return {d for a, b in windows for d in date_span(a, b)}


def build_events() -> list[tuple[date, str]]:
    """Return (date, event_key) pairs within the dataset range."""
    ev = []
    add = lambda d, k: ev.append((d, k))
    for y in range(START.year - 1, END.year + 1):
        add(date(y, 1, 1), "new_year")
        add(date(y, 1, 5), "three_kings_eve")
        add(date(y, 1, 6), "three_kings")
        add(date(y, 2, 2), "candlemas")
        add(nth_weekday(y, 2, 0, 1), "constitution")
        add(nth_weekday(y, 2, 6, 2), "super_bowl")
        add(date(y, 2, 13), "valentines_eve")
        add(date(y, 2, 14), "valentines")
        e = easter(y)
        for d in date_span(e - timedelta(days=46), e - timedelta(days=8)):
            add(d, "lent")
        for d in date_span(e - timedelta(days=7), e):
            add(d, "holy_week")
        add(nth_weekday(y, 3, 0, 3), "juarez")
        add(date(y, 4, 30), "children_day")
        add(date(y, 5, 1), "labor_day")
        add(date(y, 5, 9), "mothers_eve")
        add(date(y, 5, 10), "mothers_day")
        add(nth_weekday(y, 6, 6, 3), "fathers_day")
        # Liga MX playoffs (synthetic windows): Clausura in May, Apertura late Nov - mid Dec;
        # match days are Thursdays, Saturdays and Sundays inside each window.
        for a, b in [(nth_weekday(y, 5, 3, 2), nth_weekday(y, 5, 6, 4)),
                     (last_weekday(y, 11, 3), nth_weekday(y, 12, 6, 2))]:
            for d in date_span(a, b):
                if d.weekday() in (3, 5, 6):
                    add(d, "liga_mx_playoffs")
        add(date(y, 7, 25), "saltillo_anniversary")
        fair_start = last_weekday(y, 7, 4)
        for d in date_span(fair_start, fair_start + timedelta(days=16)):
            add(d, "saltillo_fair")
        for d in date_span(date(y, 8, 1), date(y, 8, 6)):
            add(d, "santo_cristo")
        add(date(y, 9, 15), "independence_eve")
        add(date(y, 9, 16), "independence")
        add(date(y, 10, 31), "halloween")
        add(date(y, 11, 1), "day_of_dead")
        add(date(y, 11, 2), "day_of_dead")
        rev = nth_weekday(y, 11, 0, 3)
        add(rev, "revolution")
        for d in date_span(rev - timedelta(days=3), rev):
            add(d, "buen_fin")
        add(date(y, 12, 12), "guadalupe")
        for d in date_span(date(y, 12, 12), date(y + 1, 1, 6)):
            add(d, "guadalupe_reyes")
        add(date(y, 12, 24), "christmas_eve")
        add(date(y, 12, 25), "christmas")
        add(date(y, 12, 31), "new_years_eve")
    add(date(2024, 6, 2), "election_day")
    add(date(2024, 6, 1), "dry_law")
    add(date(2024, 6, 2), "dry_law")
    add(date(2024, 10, 1), "inauguration")
    for d in date_span("2026-06-11", "2026-07-19"):
        add(d, "world_cup")
    for d in MEXICO_MATCHES:
        add(pd.Timestamp(d).date(), "mexico_match")
    # Back to school: the week before classes restart after summer vacation.
    for a, b in SEP_VACATIONS:
        if pd.Timestamp(b).month == 8:
            for d in date_span(pd.Timestamp(b) - pd.Timedelta(days=6), b):
                add(d, "back_to_school")
    # SEP Technical Council: last Friday of each school month (Sep-Jun), no classes.
    sep_vac = in_windows(SEP_VACATIONS)
    for y in range(START.year, END.year + 1):
        for m in [1, 2, 3, 4, 5, 6, 9, 10, 11, 12]:
            d = last_weekday(y, m, 4)
            if d not in sep_vac:
                add(d, "school_council")
    lo, hi = START.date(), END.date()
    return sorted({(d, k) for d, k in ev if lo <= d <= hi})


def season_of(d: date) -> str:
    md = (d.month, d.day)
    if md >= (12, 21) or md < (3, 20):
        return "winter"
    if md < (6, 21):
        return "spring"
    if md < (9, 23):
        return "summer"
    return "autumn"


def build_calendar(events) -> pd.DataFrame:
    by_day: dict[date, list[str]] = {}
    for d, k in events:
        by_day.setdefault(d, []).append(k)
    sep_vac, col_vac = in_windows(SEP_VACATIONS), in_windows(COLLEGE_VACATIONS)
    rows = []
    for ts in DATES:
        d = ts.date()
        keys = by_day.get(d, [])
        hol = [EVENT_INFO[k][1] for k in keys if k in OFFICIAL_HOLIDAYS]
        other = [k for k in keys if k not in OFFICIAL_HOLIDAYS]
        month_end = (ts + pd.Timedelta(days=1)).day == 1
        rows.append({
            "date": ts.strftime("%Y-%m-%d"),
            "day_of_week": ts.day_name(),
            "is_weekend": int(ts.dayofweek >= 5),
            "is_holiday": int(bool(hol)),
            "holiday_name": "|".join(hol) or None,
            "season_name": season_of(d),
            "event_name": "|".join(EVENT_INFO[k][1] for k in other) or None,
            "event_scope": "|".join(EVENT_INFO[k][0] for k in other) or None,
            "is_payday": int(d.day == 15 or month_end),
            "is_school_vacation": int(d in sep_vac),
            "is_college_vacation": int(d in col_vac),
            "demand_multiplier_hint": 1.0,  # filled in by simulate_operations.py
        })
    return pd.DataFrame(rows)


# Monthly climatology for Saltillo (calibrated on the original dataset's Saltillo site).
TMAX_CLIM = [19.0, 21.0, 25.0, 28.0, 31.0, 32.0, 31.5, 31.0, 28.5, 25.5, 22.0, 19.5]
TMIN_CLIM = [3.0, 5.0, 8.5, 11.5, 15.0, 16.5, 16.5, 16.5, 15.0, 11.5, 7.0, 4.0]
RAIN_PROB = [0.05, 0.05, 0.05, 0.08, 0.12, 0.25, 0.28, 0.30, 0.30, 0.12, 0.06, 0.05]
RAIN_MEAN_MM = [4, 4, 4, 5, 6, 8, 7, 8, 8, 6, 4, 4]


def weather_label(tmax: float, tmin: float, precip: float) -> str:
    if precip >= 5.0:
        return "rainy"
    if tmax >= 30.0:
        return "hot"
    if tmax < 17.0 or tmin <= 1.0:
        return "cold"
    return "mild"


def build_weather() -> pd.DataFrame:
    r = rng(4)
    n = len(DATES)
    doy = DATES.dayofyear.to_numpy()
    month = DATES.month.to_numpy() - 1
    # Smooth climatology: interpolate monthly values by day of year.
    mid = np.array([15, 45, 74, 105, 135, 166, 196, 227, 258, 288, 319, 349])
    interp = lambda clim: np.interp(doy, np.r_[mid - 365, mid, mid + 365], np.tile(clim, 3))
    tmax_c, tmin_c = interp(TMAX_CLIM), interp(TMIN_CLIM)

    anom = np.zeros(n)
    for t in range(1, n):  # AR(1) daily anomaly
        anom[t] = 0.7 * anom[t - 1] + r.normal(0, 1.3)

    front = np.zeros(n)  # cold fronts Nov-Mar: 2-4 days, strong drop
    t = 0
    while t < n:
        if month[t] in (10, 11, 0, 1, 2) and r.random() < 0.09:
            length, depth = int(r.integers(2, 5)), r.uniform(4, 9)
            front[t:t + length] = np.maximum(front[t:t + length], depth * np.linspace(1, 0.5, length)[:n - t])
            t += length + 2
        else:
            t += 1

    heat = np.zeros(n)  # occasional heat waves May-Aug
    t = 0
    while t < n:
        if month[t] in (4, 5, 6, 7) and r.random() < 0.03:
            length = int(r.integers(3, 7))
            heat[t:t + length] = r.uniform(1.5, 3.0)
            t += length
        else:
            t += 1

    rain = (r.random(n) < np.array(RAIN_PROB)[month]) * r.gamma(1.2, np.array(RAIN_MEAN_MM)[month] / 1.2)
    rain = np.round(rain, 1)
    tmax = tmax_c + anom - front + heat - np.where(rain >= 5, r.uniform(2, 4, n), 0)
    tmin = tmin_c + 0.6 * anom - 0.6 * front + 0.4 * heat
    tmin = np.minimum(tmin, tmax - 6)
    tmax, tmin = np.round(tmax, 1), np.round(tmin, 1)
    return pd.DataFrame({
        "date": DATES.strftime("%Y-%m-%d"), "temp_max_c": tmax, "temp_min_c": tmin, "precipitation_mm": rain,
        "weather_label": [weather_label(a, b, c) for a, b, c in zip(tmax, tmin, rain)],
    })


def main() -> None:
    print("Building calendar and weather...")
    write_csv(build_calendar(build_events()), "calendar")
    write_csv(build_weather(), "weather", float_format="%.1f")


if __name__ == "__main__":
    main()

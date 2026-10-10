"""Baseline demand forecaster (stand-in for the SageMaker CatBoost/LightGBM endpoint).

Multiplicative, fully deterministic model fitted per store x product:

    expected(t) = level x weekday_factor[dow(t)] x calendar_hint(t) x weather_factor[label(t)]

* `calendar_hint` is `calendar.demand_multiplier_hint` (events, vacations, paydays).
* `weekday_factor` comes from the last 12 weeks, `weather_factor` from the last year
  (both shrunk toward 1.0 when there is little evidence).
* `level` is the last 28 days of demand with those effects removed.

Quantiles use the dataset's demand noise (Poisson x Gamma(shape=8)), i.e. variance mu + mu^2/8,
with a normal approximation. Swap `forecast_product` for a SageMaker adapter returning the same
`ForecastResult` and nothing else changes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np

from ..data.repository import CalendarDay, DataRepository, SalesMatrix, WeatherDay
from .catalog import Catalog

MODEL_NAME = "seasonal-baseline-v1"
GAMMA_SHAPE = 8.0
Z80 = 0.8416
Z90 = 1.2816
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


@dataclass
class Driver:
    kind: str  # weekday | weather | event | payday | trend | season
    name: str
    impact_pct: float
    detail: str


@dataclass
class ForecastResult:
    store_id: str
    product_id: str
    as_of: date
    dates: list[date]
    p50: np.ndarray
    p80: np.ndarray
    p90: np.ndarray
    drivers: list[Driver] = field(default_factory=list)
    model: str = MODEL_NAME
    backtest_wape: float | None = None


@dataclass
class _Fit:
    level: float
    weekday: np.ndarray
    weather: dict[str, float]
    weather_counts: dict[str, int]


def _hint(cal: dict[date, CalendarDay], day: date) -> float:
    entry = cal.get(day)
    return entry.demand_multiplier_hint if entry else 1.0


def _label(wx: dict[date, WeatherDay], day: date) -> str | None:
    entry = wx.get(day)
    return entry.weather_label if entry else None


def _fit(y: np.ndarray, days: list[date], cal: dict[date, CalendarDay], wx: dict[date, WeatherDay]) -> _Fit:
    hints = np.array([_hint(cal, d) for d in days])
    dows = np.array([d.weekday() for d in days])
    labels = [_label(wx, d) for d in days]
    base = y / np.maximum(hints, 0.05)

    recent = slice(max(0, len(y) - 84), len(y))
    overall = base[recent].mean() if base[recent].size else 0.0
    weekday = np.ones(7)
    if overall > 0:
        for k in range(7):
            mask = dows[recent] == k
            count = int(mask.sum())
            mean = base[recent][mask].mean() if count else overall
            weekday[k] = (mean * count + overall * 2) / (count + 2) / overall

    z = base / weekday[dows]
    mean_z = z.mean() if z.size else 0.0
    weather: dict[str, float] = {}
    counts: dict[str, int] = {}
    if mean_z > 0:
        for label in {lab for lab in labels if lab}:
            mask = np.array([lab == label for lab in labels])
            count = int(mask.sum())
            counts[label] = count
            ratio = z[mask].mean() / mean_z
            weather[label] = float(np.clip((ratio * count + 10) / (count + 10), 0.5, 2.5))

    last = slice(max(0, len(y) - 28), len(y))
    wf = np.array([weather.get(lab or "", 1.0) for lab in labels])
    deseasonalized = y[last] / np.maximum(hints[last] * weekday[dows[last]] * wf[last], 0.05)
    level = float(deseasonalized.mean()) if deseasonalized.size else 0.0
    return _Fit(level=level, weekday=weekday, weather=weather, weather_counts=counts)


def _predict(fit: _Fit, days: list[date], cal: dict[date, CalendarDay], wx: dict[date, WeatherDay]) -> np.ndarray:
    values = [
        fit.level * fit.weekday[d.weekday()] * _hint(cal, d) * fit.weather.get(_label(wx, d) or "", 1.0) for d in days
    ]
    return np.maximum(np.array(values, dtype=float), 0.0)


def _quantile(mu: np.ndarray, z: float) -> np.ndarray:
    return np.maximum(mu + z * np.sqrt(mu + mu**2 / GAMMA_SHAPE), mu)


def _span(days: list[date]) -> str:
    if not days:
        return ""
    if len(days) == 1:
        return days[0].strftime("%b %d")
    return f"{days[0].strftime('%b %d')}-{days[-1].strftime('%b %d')}"


def forecast_product(
    repo: DataRepository,
    catalog: Catalog,
    store_id: str,
    product_id: str,
    as_of: date,
    horizon: int,
    backtest: bool = True,
    history: SalesMatrix | None = None,
) -> ForecastResult:
    """Forecast `horizon` days after `as_of`. Pass `history` (last 365 days of the store) to reuse it."""
    first_day, _ = repo.data_bounds()
    hist_start = max(first_day, as_of - timedelta(days=364))
    hist_days = [hist_start + timedelta(days=k) for k in range((as_of - hist_start).days + 1)]
    future = [as_of + timedelta(days=k) for k in range(1, horizon + 1)]
    cal = repo.calendar(hist_start, as_of + timedelta(days=horizon))
    wx = repo.weather(hist_start, as_of + timedelta(days=horizon))
    if history is not None and history.dates and history.dates[0] == hist_start and history.dates[-1] == as_of:
        y = history.row(product_id)
    else:
        y = repo.sales_matrix(store_id, hist_start, as_of).row(product_id)

    fit = _fit(y, hist_days, cal, wx)
    mu = _predict(fit, future, cal, wx)
    result = ForecastResult(
        store_id=store_id,
        product_id=product_id,
        as_of=as_of,
        dates=future,
        p50=mu,
        p80=_quantile(mu, Z80),
        p90=_quantile(mu, Z90),
    )

    if backtest and len(y) > 120:
        cut = len(y) - 14
        back_fit = _fit(y[:cut], hist_days[:cut], cal, wx)
        predicted = _predict(back_fit, hist_days[cut:], cal, wx)
        actual = y[cut:]
        if actual.sum() > 0:
            result.backtest_wape = float(np.abs(predicted - actual).sum() / actual.sum())

    result.drivers = _drivers(catalog, product_id, fit, y, future, cal, wx)
    return result


def _drivers(
    catalog: Catalog,
    product_id: str,
    fit: _Fit,
    y: np.ndarray,
    future: list[date],
    cal: dict[date, CalendarDay],
    wx: dict[date, WeatherDay],
) -> list[Driver]:
    drivers: list[Driver] = []
    product = catalog.products[product_id]

    peak = int(np.argmax(fit.weekday))
    low = int(np.argmin(fit.weekday))
    if fit.weekday[peak] - fit.weekday[low] > 0.08:
        drivers.append(
            Driver(
                kind="weekday",
                name=f"{WEEKDAYS[peak]} peak",
                impact_pct=float(round((fit.weekday[peak] - 1) * 100, 1)),
                detail=f"{WEEKDAYS[peak]} sells {round((fit.weekday[peak] - 1) * 100)}% above an average day; "
                f"{WEEKDAYS[low]} is the slowest ({round((fit.weekday[low] - 1) * 100)}%).",
            )
        )

    upcoming: dict[str, list[date]] = {}
    for d in future:
        label = _label(wx, d)
        if label:
            upcoming.setdefault(label, []).append(d)
    for label, days in sorted(upcoming.items(), key=lambda kv: -len(kv[1])):
        factor = fit.weather.get(label, 1.0)
        if abs(factor - 1) >= 0.05 and fit.weather_counts.get(label, 0) >= 5:
            drivers.append(
                Driver(
                    kind="weather",
                    name=f"{label.capitalize()} weather",
                    impact_pct=float(round((factor - 1) * 100, 1)),
                    detail=f"{len(days)} {label} day(s) expected ({_span(days)}); this product historically sells "
                    f"{round((factor - 1) * 100):+d}% on {label} days (weather sensitivity: "
                    f"{product.weather_sensitivity}).",
                )
            )

    events: dict[str, list[date]] = {}
    for d in future:
        entry = cal.get(d)
        if entry and abs(entry.demand_multiplier_hint - 1) >= 0.04:
            for name in entry.event_names or ((entry.holiday_name,) if entry.holiday_name else ()):
                events.setdefault(name, []).append(d)
    for name, days in list(events.items())[:3]:
        hint = float(np.mean([cal[d].demand_multiplier_hint for d in days]))
        drivers.append(
            Driver(
                kind="event",
                name=name,
                impact_pct=float(round((hint - 1) * 100, 1)),
                detail=f"{name} ({_span(days)}): network-wide calendar multiplier x{hint:.2f}.",
            )
        )

    paydays = [d for d in future if cal.get(d) and cal[d].is_payday]
    if paydays:
        drivers.append(
            Driver(
                kind="payday",
                name="Payday",
                impact_pct=6.0,
                detail=f"Payday on {', '.join(d.strftime('%b %d') for d in paydays)} adds store traffic.",
            )
        )

    if len(y) >= 56 and y[-56:-28].mean() > 0:
        change = y[-28:].mean() / y[-56:-28].mean() - 1
        if abs(change) >= 0.05:
            drivers.append(
                Driver(
                    kind="trend",
                    name="Recent momentum",
                    impact_pct=float(round(change * 100, 1)),
                    detail=f"Last 28 days sold {round(change * 100):+d}% vs the previous 28 days.",
                )
            )

    seasons = {cal[d].season_name for d in future if d in cal}
    if product.is_seasonal and seasons:
        drivers.append(
            Driver(
                kind="season",
                name="Seasonal product",
                impact_pct=0.0,
                detail=f"Seasonal item; upcoming season: {', '.join(sorted(seasons))}.",
            )
        )
    drivers.sort(key=lambda d: abs(d.impact_pct), reverse=True)
    return drivers

"""In-memory adapter over the v2 CSV folder.

`sales.csv` is a full date x store x product grid (3,012,000 rows), so it is packed into a dense
`int32` cube `[store, product, day]` (~12 MB) plus its cumulative sum. Every query is then an
array slice, fast enough to answer tool calls interactively.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from .repository import (
    Affinity,
    CalendarDay,
    Campaign,
    Category,
    Lot,
    OpenOrder,
    Product,
    SalesMatrix,
    Store,
    Supplier,
    SupplierOffer,
    WeatherDay,
)

log = logging.getLogger(__name__)

REQUIRED_FILES = (
    "stores.csv",
    "categories.csv",
    "products.csv",
    "suppliers.csv",
    "supplier_products.csv",
    "calendar.csv",
    "weather.csv",
    "sales.csv",
    "inventory_lots.csv",
    "purchase_orders.csv",
)
# Optional (older v2 folders work without them): promotion_campaigns.csv, promotion_store_days.csv
# and product_affinity.csv (built from ticket_lines.csv by `stockcopilot.data.build_affinity`).


def _none_if_blank(value: object) -> str | None:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return None
    text = str(value).strip()
    return text or None


def _split(value: object) -> tuple[str, ...]:
    text = _none_if_blank(value)
    return tuple(part for part in text.split("|") if part) if text else ()


class CsvRepository:
    def __init__(self, dataset_dir: str | Path):
        self.dataset_dir = Path(dataset_dir).expanduser().resolve()
        missing = [name for name in REQUIRED_FILES if not (self.dataset_dir / name).exists()]
        if missing:
            raise FileNotFoundError(
                f"Dataset folder {self.dataset_dir} is missing {', '.join(missing)}. "
                "Set DATASET_DIR to the BackendRoad2Tech v2 folder."
            )
        self._lock = threading.Lock()
        self._loaded = False

    # ------------------------------------------------------------------ loading
    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        with self._lock:
            if self._loaded:
                return
            started = time.perf_counter()
            self._load()
            self._loaded = True
            log.info("Loaded v2 dataset from %s in %.1fs", self.dataset_dir, time.perf_counter() - started)

    def _read(self, name: str, **kwargs: object) -> pd.DataFrame:
        return pd.read_csv(self.dataset_dir / name, **kwargs)  # type: ignore[arg-type]

    def _load(self) -> None:
        stores = self._read("stores.csv", dtype=str)
        self._stores = [
            Store(
                store_id=r.store_id,
                store_name=r.store_name,
                colonia=r.colonia,
                latitude=float(r.latitude),
                longitude=float(r.longitude),
                zone_type=r.zone_type,
                secondary_zone_type=_none_if_blank(r.secondary_zone_type),
                size_category=r.size_category,
                is_24h=str(r.is_24h) == "1",
            )
            for r in stores.itertuples(index=False)
        ]
        self._store_index = {s.store_id: i for i, s in enumerate(self._stores)}

        self._categories = [
            Category(
                category_id=r.category_id,
                category_name=r.category_name,
                is_perishable=bool(r.is_perishable),
                typical_shelf_life_days=int(r.typical_shelf_life_days),
                requires_refrigeration=bool(r.requires_refrigeration),
            )
            for r in self._read("categories.csv").itertuples(index=False)
        ]

        self._products = [
            Product(
                product_id=r.product_id,
                product_name=r.product_name,
                category_id=r.category_id,
                brand=r.brand,
                unit=r.unit,
                shelf_life_days=int(r.shelf_life_days),
                retail_price=float(r.retail_price),
                is_seasonal=bool(r.is_seasonal),
                weather_sensitivity=r.weather_sensitivity,
                base_daily_demand=float(r.base_daily_demand),
            )
            for r in self._read("products.csv").itertuples(index=False)
        ]
        self._product_ids = [p.product_id for p in self._products]
        self._product_index = {pid: i for i, pid in enumerate(self._product_ids)}

        self._suppliers = [
            Supplier(
                supplier_id=r.supplier_id,
                supplier_name=r.supplier_name,
                email=r.email,
                categories_served=_split(r.categories_served),
                city=r.city,
                reliability_score=float(r.reliability_score),
            )
            for r in self._read("suppliers.csv").itertuples(index=False)
        ]
        self._offers = [
            SupplierOffer(
                supplier_id=r.supplier_id,
                product_id=r.product_id,
                unit_cost=float(r.unit_cost),
                min_order_qty=int(r.min_order_qty),
                lead_time_days=int(r.lead_time_days),
                delivery_days=str(r.delivery_days),
            )
            for r in self._read("supplier_products.csv").itertuples(index=False)
        ]

        calendar = self._read("calendar.csv", keep_default_na=False)
        self._calendar = {}
        for r in calendar.itertuples(index=False):
            day = date.fromisoformat(r.date)
            self._calendar[day] = CalendarDay(
                date=day,
                day_of_week=r.day_of_week,
                is_weekend=str(r.is_weekend) == "1",
                is_holiday=str(r.is_holiday) == "1",
                holiday_name=_none_if_blank(r.holiday_name),
                season_name=r.season_name,
                event_names=_split(r.event_name),
                is_payday=str(r.is_payday) == "1",
                demand_multiplier_hint=float(r.demand_multiplier_hint),
            )
        self._weather = {
            date.fromisoformat(r.date): WeatherDay(
                date=date.fromisoformat(r.date),
                temp_max_c=float(r.temp_max_c),
                temp_min_c=float(r.temp_min_c),
                precipitation_mm=float(r.precipitation_mm),
                weather_label=r.weather_label,
            )
            for r in self._read("weather.csv").itertuples(index=False)
        }

        sales = self._read(
            "sales.csv",
            usecols=["date", "store_id", "product_id", "units_sold"],
            dtype={"date": "category", "store_id": "category", "product_id": "category", "units_sold": "int32"},
        )
        day_values = [date.fromisoformat(d) for d in sales["date"].cat.categories]
        self._first_day = min(day_values)
        self._last_day = max(day_values)
        n_days = (self._last_day - self._first_day).days + 1
        day_of_category = np.array([(d - self._first_day).days for d in day_values], dtype=np.int32)
        store_of_category = np.array([self._store_index[s] for s in sales["store_id"].cat.categories])
        product_of_category = np.array([self._product_index[p] for p in sales["product_id"].cat.categories])

        cube = np.zeros((len(self._stores), len(self._products), n_days), dtype=np.int32)
        np.add.at(
            cube,
            (
                store_of_category[sales["store_id"].cat.codes.to_numpy()],
                product_of_category[sales["product_id"].cat.codes.to_numpy()],
                day_of_category[sales["date"].cat.codes.to_numpy()],
            ),
            sales["units_sold"].to_numpy(),
        )
        self._cube = cube
        self._cum = np.cumsum(cube, axis=2, dtype=np.int64)
        del sales

        lots = self._read("inventory_lots.csv", dtype={"quantity_received": "int64", "quantity_remaining": "int64"})
        lots["received_date"] = pd.to_datetime(lots["received_date"]).dt.date
        lots["expiration_date"] = pd.to_datetime(lots["expiration_date"]).dt.date
        self._lots = {store_id: frame for store_id, frame in lots.groupby("store_id", sort=False)}

        orders = self._read("purchase_orders.csv", keep_default_na=True)
        orders["order_date"] = pd.to_datetime(orders["order_date"]).dt.date
        orders["expected_delivery_date"] = pd.to_datetime(orders["expected_delivery_date"]).dt.date
        orders["actual_delivery_date"] = pd.to_datetime(orders["actual_delivery_date"]).dt.date
        self._orders = {store_id: frame for store_id, frame in orders.groupby("store_id", sort=False)}
        self._load_optional()

    def _load_optional(self) -> None:
        self._campaigns: list[Campaign] = []
        self._assignments: dict[str, dict[str, bool]] = {}
        self._affinities: list[Affinity] = []
        if (self.dataset_dir / "promotion_campaigns.csv").exists():
            self._campaigns = [
                Campaign(
                    campaign_id=r.campaign_id,
                    strategy_type=r.strategy_type,
                    objective=r.objective,
                    anchor_product_id=r.anchor_product_id,
                    promoted_product_id=r.promoted_product_id,
                    discount_pct=float(r.discount_pct),
                    start_date=date.fromisoformat(r.start_date),
                    end_date=date.fromisoformat(r.end_date),
                    trigger_reason=str(r.trigger_reason),
                )
                for r in self._read("promotion_campaigns.csv", keep_default_na=False).itertuples(index=False)
            ]
        if (self.dataset_dir / "promotion_store_days.csv").exists():
            days = self._read("promotion_store_days.csv")
            eligible = days[days["is_eligible"] == 1]
            treated = eligible.groupby(["campaign_id", "store_id"])["is_treatment"].max()
            for (campaign_id, store_id), flag in treated.items():
                self._assignments.setdefault(campaign_id, {})[store_id] = bool(flag)
        if (self.dataset_dir / "product_affinity.csv").exists():
            self._affinities = [
                Affinity(
                    product_id=r.product_id,
                    other_product_id=r.other_product_id,
                    co_tickets=int(r.co_tickets),
                    confidence=float(r.confidence),
                    lift=float(r.lift),
                )
                for r in self._read("product_affinity.csv").itertuples(index=False)
            ]
        elif (self.dataset_dir / "ticket_lines.csv").exists():
            log.warning(
                "ticket_lines.csv found but product_affinity.csv is missing; run "
                "`python -m stockcopilot.data.build_affinity` to use observed basket affinity."
            )

    # ------------------------------------------------------------------ helpers
    def _day_slice(self, start: date, end: date) -> tuple[slice, list[date]]:
        start = max(start, self._first_day)
        end = min(end, self._last_day)
        if end < start:
            return slice(0, 0), []
        i0 = (start - self._first_day).days
        i1 = (end - self._first_day).days + 1
        return slice(i0, i1), [start + timedelta(days=k) for k in range(i1 - i0)]

    def _store_idx(self, store_id: str) -> int:
        try:
            return self._store_index[store_id]
        except KeyError as exc:
            raise KeyError(f"Unknown store_id {store_id}") from exc

    # ------------------------------------------------------------------ protocol
    def data_bounds(self) -> tuple[date, date]:
        self._ensure_loaded()
        return self._first_day, self._last_day

    def stores(self) -> list[Store]:
        self._ensure_loaded()
        return list(self._stores)

    def categories(self) -> list[Category]:
        self._ensure_loaded()
        return list(self._categories)

    def products(self) -> list[Product]:
        self._ensure_loaded()
        return list(self._products)

    def suppliers(self) -> list[Supplier]:
        self._ensure_loaded()
        return list(self._suppliers)

    def offers(self) -> list[SupplierOffer]:
        self._ensure_loaded()
        return list(self._offers)

    def calendar(self, start: date, end: date) -> dict[date, CalendarDay]:
        self._ensure_loaded()
        return {d: c for d, c in self._calendar.items() if start <= d <= end}

    def weather(self, start: date, end: date) -> dict[date, WeatherDay]:
        self._ensure_loaded()
        return {d: w for d, w in self._weather.items() if start <= d <= end}

    def sales_matrix(self, store_id: str, start: date, end: date) -> SalesMatrix:
        self._ensure_loaded()
        days, dates = self._day_slice(start, end)
        units = self._cube[self._store_idx(store_id), :, days].astype(np.float64)
        return SalesMatrix(store_id=store_id, product_ids=list(self._product_ids), dates=dates, units=units)

    def cumulative_units(self, store_id: str, up_to: date) -> dict[str, int]:
        self._ensure_loaded()
        if up_to < self._first_day:
            return {pid: 0 for pid in self._product_ids}
        day = min((up_to - self._first_day).days, self._cum.shape[2] - 1)
        totals = self._cum[self._store_idx(store_id), :, day]
        return {pid: int(totals[i]) for i, pid in enumerate(self._product_ids)}

    def sales_by_store(self, product_id: str, start: date, end: date) -> dict[str, np.ndarray]:
        self._ensure_loaded()
        days, _ = self._day_slice(start, end)
        p = self._product_index[product_id]
        return {s.store_id: self._cube[i, p, days].astype(np.float64) for i, s in enumerate(self._stores)}

    def lots(self, store_id: str, up_to: date) -> list[Lot]:
        self._ensure_loaded()
        self._store_idx(store_id)
        frame = self._lots.get(store_id)
        if frame is None:
            return []
        frame = frame[frame["received_date"] <= up_to]
        return [
            Lot(
                lot_id=r.lot_id,
                product_id=r.product_id,
                supplier_id=r.supplier_id,
                received_date=r.received_date,
                quantity_received=int(r.quantity_received),
                expiration_date=r.expiration_date,
                quantity_remaining=int(r.quantity_remaining),
            )
            for r in frame.itertuples(index=False)
        ]

    def campaigns(self) -> list[Campaign]:
        self._ensure_loaded()
        return list(self._campaigns)

    def campaign_assignments(self) -> dict[str, dict[str, bool]]:
        self._ensure_loaded()
        return {k: dict(v) for k, v in self._assignments.items()}

    def affinities(self) -> list[Affinity]:
        self._ensure_loaded()
        return list(self._affinities)

    def open_orders(self, store_id: str, as_of: date) -> list[OpenOrder]:
        self._ensure_loaded()
        self._store_idx(store_id)
        frame = self._orders.get(store_id)
        if frame is None:
            return []
        delivered = frame["actual_delivery_date"]
        pending = frame[(frame["order_date"] <= as_of) & (delivered.isna() | (delivered > as_of))]
        return [
            OpenOrder(
                order_id=r.order_id,
                product_id=r.product_id,
                supplier_id=r.supplier_id,
                order_date=r.order_date,
                quantity=int(r.quantity),
                unit_cost=float(r.unit_cost),
                expected_delivery_date=r.expected_delivery_date,
            )
            for r in pending.itertuples(index=False)
        ]

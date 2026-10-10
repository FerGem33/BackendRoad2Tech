"""Read-only data access contract over the Road2Tech v2 dataset.

Two adapters implement it: `CsvRepository` (reads the v2 CSV folder into memory) and
`PostgresRepository` (same tables loaded into PostgreSQL). Domain code only depends on this
protocol, so a future Athena / S3 Curated adapter can replace either one.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol

import numpy as np


@dataclass(frozen=True)
class Store:
    store_id: str
    store_name: str
    colonia: str
    latitude: float
    longitude: float
    zone_type: str
    secondary_zone_type: str | None
    size_category: str
    is_24h: bool


@dataclass(frozen=True)
class Category:
    category_id: str
    category_name: str
    is_perishable: bool
    typical_shelf_life_days: int
    requires_refrigeration: bool


@dataclass(frozen=True)
class Product:
    product_id: str
    product_name: str
    category_id: str
    brand: str
    unit: str
    shelf_life_days: int
    retail_price: float
    is_seasonal: bool
    weather_sensitivity: str
    base_daily_demand: float


@dataclass(frozen=True)
class Supplier:
    supplier_id: str
    supplier_name: str
    email: str
    categories_served: tuple[str, ...]
    city: str
    reliability_score: float


@dataclass(frozen=True)
class SupplierOffer:
    supplier_id: str
    product_id: str
    unit_cost: float
    min_order_qty: int
    lead_time_days: int
    delivery_days: str


@dataclass(frozen=True)
class CalendarDay:
    date: date
    day_of_week: str
    is_weekend: bool
    is_holiday: bool
    holiday_name: str | None
    season_name: str
    event_names: tuple[str, ...]
    is_payday: bool
    demand_multiplier_hint: float


@dataclass(frozen=True)
class WeatherDay:
    date: date
    temp_max_c: float
    temp_min_c: float
    precipitation_mm: float
    weather_label: str


@dataclass(frozen=True)
class Lot:
    lot_id: str
    product_id: str
    supplier_id: str
    received_date: date
    quantity_received: int
    expiration_date: date
    quantity_remaining: int


@dataclass(frozen=True)
class OpenOrder:
    order_id: str
    product_id: str
    supplier_id: str
    order_date: date
    quantity: int
    unit_cost: float
    expected_delivery_date: date


@dataclass(frozen=True)
class Campaign:
    """A historical promotion experiment (`promotion_campaigns.csv`)."""

    campaign_id: str
    strategy_type: str  # affinity_combo | rescue_bundle | contextual_offer
    objective: str
    anchor_product_id: str
    promoted_product_id: str
    discount_pct: float  # fraction, e.g. 0.10
    start_date: date
    end_date: date
    trigger_reason: str


@dataclass(frozen=True)
class Affinity:
    """Observed co-purchase of two products in the same ticket (`product_affinity.csv`)."""

    product_id: str
    other_product_id: str
    co_tickets: int
    confidence: float  # share of `product_id` tickets that also contain `other_product_id`
    lift: float


@dataclass(frozen=True)
class SalesMatrix:
    """Daily units sold for one store: `units[i, j]` is product `product_ids[i]` on `dates[j]`."""

    store_id: str
    product_ids: list[str]
    dates: list[date]
    units: np.ndarray

    def row(self, product_id: str) -> np.ndarray:
        return self.units[self.product_ids.index(product_id)]


class DataRepository(Protocol):
    def data_bounds(self) -> tuple[date, date]: ...

    def stores(self) -> list[Store]: ...

    def categories(self) -> list[Category]: ...

    def products(self) -> list[Product]: ...

    def suppliers(self) -> list[Supplier]: ...

    def offers(self) -> list[SupplierOffer]: ...

    def calendar(self, start: date, end: date) -> dict[date, CalendarDay]: ...

    def weather(self, start: date, end: date) -> dict[date, WeatherDay]: ...

    def sales_matrix(self, store_id: str, start: date, end: date) -> SalesMatrix:
        """Units sold per product and day for a store, inclusive range, zero-filled."""
        ...

    def cumulative_units(self, store_id: str, up_to: date) -> dict[str, int]:
        """Total units sold per product from the first day of data through `up_to` (inclusive)."""
        ...

    def sales_by_store(self, product_id: str, start: date, end: date) -> dict[str, np.ndarray]:
        """Daily units of one product for every store, inclusive range, zero-filled."""
        ...

    def lots(self, store_id: str, up_to: date) -> list[Lot]:
        """Every lot received at the store on or before `up_to`."""
        ...

    def open_orders(self, store_id: str, as_of: date) -> list[OpenOrder]:
        """Orders placed on or before `as_of` that had not been delivered by the end of `as_of`."""
        ...

    def campaigns(self) -> list[Campaign]:
        """Historical promotion campaigns (empty if the dataset has none)."""
        ...

    def campaign_assignments(self) -> dict[str, dict[str, bool]]:
        """campaign_id -> {store_id: is_treatment} for eligible stores (treatment vs control)."""
        ...

    def affinities(self) -> list[Affinity]:
        """Observed product pairs from tickets (empty until `build_affinity` has run)."""
        ...

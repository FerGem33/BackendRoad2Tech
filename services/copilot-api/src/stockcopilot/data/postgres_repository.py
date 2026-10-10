"""PostgreSQL adapter (tables from `schema.sql`, loaded by `load_postgres`).

Small dimension tables (stores, products, calendar, weather, ...) are read once and cached;
fact tables (sales, inventory_lots, purchase_orders) are queried per store or product.
"""

from __future__ import annotations

from datetime import date, timedelta
from functools import cached_property

import numpy as np
from psycopg_pool import ConnectionPool

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


def _split(value: str | None) -> tuple[str, ...]:
    return tuple(part for part in (value or "").split("|") if part)


class PostgresRepository:
    def __init__(self, pool: ConnectionPool):
        self.pool = pool

    def _all(self, sql: str, params: tuple[object, ...] = ()) -> list[tuple]:
        with self.pool.connection() as conn:
            return conn.execute(sql, params).fetchall()

    # ------------------------------------------------------------ dimensions
    @cached_property
    def _bounds(self) -> tuple[date, date]:
        row = self._all("SELECT min(date), max(date) FROM calendar")[0]
        return row[0], row[1]

    @cached_property
    def _stores(self) -> list[Store]:
        rows = self._all(
            "SELECT store_id, store_name, colonia, latitude, longitude, zone_type, secondary_zone_type,"
            " size_category, is_24h FROM stores ORDER BY store_id"
        )
        return [Store(r[0], r[1], r[2], float(r[3]), float(r[4]), r[5], r[6], r[7], r[8] == 1) for r in rows]

    @cached_property
    def _categories(self) -> list[Category]:
        rows = self._all(
            "SELECT category_id, category_name, is_perishable, typical_shelf_life_days, requires_refrigeration"
            " FROM categories ORDER BY category_id"
        )
        return [Category(r[0], r[1], r[2] == 1, int(r[3]), r[4] == 1) for r in rows]

    @cached_property
    def _products(self) -> list[Product]:
        rows = self._all(
            "SELECT product_id, product_name, category_id, brand, unit, shelf_life_days, retail_price,"
            " is_seasonal, weather_sensitivity, base_daily_demand FROM products ORDER BY product_id"
        )
        return [
            Product(r[0], r[1], r[2], r[3], r[4], int(r[5]), float(r[6]), r[7] == 1, r[8], float(r[9])) for r in rows
        ]

    @cached_property
    def _product_ids(self) -> list[str]:
        return [p.product_id for p in self._products]

    @cached_property
    def _suppliers(self) -> list[Supplier]:
        rows = self._all(
            "SELECT supplier_id, supplier_name, email, categories_served, city, reliability_score"
            " FROM suppliers ORDER BY supplier_id"
        )
        return [Supplier(r[0], r[1], r[2], _split(r[3]), r[4], float(r[5])) for r in rows]

    @cached_property
    def _offers(self) -> list[SupplierOffer]:
        rows = self._all(
            "SELECT supplier_id, product_id, unit_cost, min_order_qty, lead_time_days, delivery_days"
            " FROM supplier_products ORDER BY product_id, supplier_id"
        )
        return [SupplierOffer(r[0], r[1], float(r[2]), int(r[3]), int(r[4]), r[5] or "") for r in rows]

    @cached_property
    def _calendar(self) -> dict[date, CalendarDay]:
        rows = self._all(
            "SELECT date, day_of_week, is_weekend, is_holiday, holiday_name, season_name, event_name,"
            " is_payday, demand_multiplier_hint FROM calendar"
        )
        return {
            r[0]: CalendarDay(r[0], r[1], r[2] == 1, r[3] == 1, r[4], r[5], _split(r[6]), r[7] == 1, float(r[8]))
            for r in rows
        }

    @cached_property
    def _weather(self) -> dict[date, WeatherDay]:
        rows = self._all("SELECT date, temp_max_c, temp_min_c, precipitation_mm, weather_label FROM weather")
        return {r[0]: WeatherDay(r[0], float(r[1]), float(r[2]), float(r[3]), r[4]) for r in rows}

    def _table_exists(self, name: str) -> bool:
        return bool(self._all("SELECT to_regclass(%s) IS NOT NULL", (name,))[0][0])

    @cached_property
    def _campaigns(self) -> list[Campaign]:
        if not self._table_exists("promotion_campaigns"):
            return []
        rows = self._all(
            "SELECT campaign_id, strategy_type, objective, anchor_product_id, promoted_product_id, discount_pct,"
            " start_date, end_date, coalesce(trigger_reason, '') FROM promotion_campaigns ORDER BY campaign_id"
        )
        return [Campaign(r[0], r[1], r[2], r[3], r[4], float(r[5]), r[6], r[7], r[8]) for r in rows]

    @cached_property
    def _assignments(self) -> dict[str, dict[str, bool]]:
        if not self._table_exists("promotion_store_days"):
            return {}
        rows = self._all(
            "SELECT campaign_id, store_id, max(is_treatment) FROM promotion_store_days"
            " WHERE is_eligible = 1 GROUP BY campaign_id, store_id"
        )
        out: dict[str, dict[str, bool]] = {}
        for campaign_id, store_id, flag in rows:
            out.setdefault(campaign_id, {})[store_id] = flag == 1
        return out

    @cached_property
    def _affinities(self) -> list[Affinity]:
        if not self._table_exists("product_affinity"):
            return []
        rows = self._all("SELECT product_id, other_product_id, co_tickets, confidence, lift FROM product_affinity")
        return [Affinity(r[0], r[1], int(r[2]), float(r[3]), float(r[4])) for r in rows]

    def campaigns(self) -> list[Campaign]:
        return list(self._campaigns)

    def campaign_assignments(self) -> dict[str, dict[str, bool]]:
        return {k: dict(v) for k, v in self._assignments.items()}

    def affinities(self) -> list[Affinity]:
        return list(self._affinities)

    def data_bounds(self) -> tuple[date, date]:
        return self._bounds

    def stores(self) -> list[Store]:
        return list(self._stores)

    def categories(self) -> list[Category]:
        return list(self._categories)

    def products(self) -> list[Product]:
        return list(self._products)

    def suppliers(self) -> list[Supplier]:
        return list(self._suppliers)

    def offers(self) -> list[SupplierOffer]:
        return list(self._offers)

    def calendar(self, start: date, end: date) -> dict[date, CalendarDay]:
        return {d: c for d, c in self._calendar.items() if start <= d <= end}

    def weather(self, start: date, end: date) -> dict[date, WeatherDay]:
        return {d: w for d, w in self._weather.items() if start <= d <= end}

    # ------------------------------------------------------------------ facts
    def _dates(self, start: date, end: date) -> list[date]:
        first, last = self._bounds
        start, end = max(start, first), min(end, last)
        return [start + timedelta(days=k) for k in range((end - start).days + 1)] if end >= start else []

    def sales_matrix(self, store_id: str, start: date, end: date) -> SalesMatrix:
        dates = self._dates(start, end)
        units = np.zeros((len(self._product_ids), len(dates)))
        if dates:
            index = {pid: i for i, pid in enumerate(self._product_ids)}
            rows = self._all(
                "SELECT product_id, date, units_sold FROM sales WHERE store_id = %s AND date BETWEEN %s AND %s",
                (store_id, dates[0], dates[-1]),
            )
            for product_id, day, value in rows:
                units[index[product_id], (day - dates[0]).days] = value
        return SalesMatrix(store_id=store_id, product_ids=list(self._product_ids), dates=dates, units=units)

    def cumulative_units(self, store_id: str, up_to: date) -> dict[str, int]:
        totals = {pid: 0 for pid in self._product_ids}
        rows = self._all(
            "SELECT product_id, sum(units_sold) FROM sales WHERE store_id = %s AND date <= %s GROUP BY product_id",
            (store_id, up_to),
        )
        for product_id, total in rows:
            totals[product_id] = int(total)
        return totals

    def sales_by_store(self, product_id: str, start: date, end: date) -> dict[str, np.ndarray]:
        dates = self._dates(start, end)
        series = {s.store_id: np.zeros(len(dates)) for s in self._stores}
        if dates:
            rows = self._all(
                "SELECT store_id, date, units_sold FROM sales WHERE product_id = %s AND date BETWEEN %s AND %s",
                (product_id, dates[0], dates[-1]),
            )
            for store_id, day, value in rows:
                series[store_id][(day - dates[0]).days] = value
        return series

    def lots(self, store_id: str, up_to: date) -> list[Lot]:
        rows = self._all(
            "SELECT lot_id, product_id, supplier_id, received_date, quantity_received, expiration_date,"
            " quantity_remaining FROM inventory_lots WHERE store_id = %s AND received_date <= %s",
            (store_id, up_to),
        )
        return [Lot(r[0], r[1], r[2], r[3], int(r[4]), r[5], int(r[6])) for r in rows]

    def open_orders(self, store_id: str, as_of: date) -> list[OpenOrder]:
        rows = self._all(
            "SELECT order_id, product_id, supplier_id, order_date, quantity, unit_cost, expected_delivery_date"
            " FROM purchase_orders WHERE store_id = %s AND order_date <= %s"
            " AND (actual_delivery_date IS NULL OR actual_delivery_date > %s)",
            (store_id, as_of, as_of),
        )
        return [OpenOrder(r[0], r[1], r[2], r[3], int(r[4]), float(r[5]), r[6]) for r in rows]

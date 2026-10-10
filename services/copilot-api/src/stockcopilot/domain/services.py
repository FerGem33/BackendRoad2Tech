"""Shared context for every tool: repository, catalog, demo date and small caches."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import date, timedelta

from ..data.repository import Affinity, DataRepository, Product, SalesMatrix, Store
from .catalog import Catalog
from .forecast import ForecastResult, forecast_product
from .forecast_provider import ForecastProvider
from .inventory import StorePosition, store_position
from .models import ProductRef, StoreRef, StrategyType, SupplierRef
from .uplift import UpliftEstimate, measure_uplift


@dataclass
class Services:
    repo: DataRepository
    catalog: Catalog
    as_of: date
    forecast_provider: ForecastProvider | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _positions: dict[tuple[str, date], StorePosition] = field(default_factory=dict, repr=False)
    _forecasts: dict[tuple[str, str, date, int], ForecastResult] = field(default_factory=dict, repr=False)
    _history: dict[tuple[str, date], SalesMatrix] = field(default_factory=dict, repr=False)
    _uplift: dict[StrategyType, UpliftEstimate] | None = field(default=None, repr=False)

    @classmethod
    def create(
        cls, repo: DataRepository, as_of: date | None = None, forecast_provider: ForecastProvider | None = None
    ) -> Services:
        first, last = repo.data_bounds()
        resolved = as_of or last
        if not first <= resolved <= last:
            raise ValueError(f"DEMO_AS_OF {resolved} is outside the dataset range {first}..{last}")
        return cls(repo=repo, catalog=Catalog(repo), as_of=resolved, forecast_provider=forecast_provider)

    # --------------------------------------------------------------- caches
    def position(self, store_id: str) -> StorePosition:
        key = (store_id, self.as_of)
        with self._lock:
            if key not in self._positions:
                self._positions[key] = store_position(self.repo, store_id, self.as_of)
            return self._positions[key]

    def history(self, store_id: str) -> SalesMatrix:
        """Last 365 days of sales for a store (shared by every forecast of that store)."""
        key = (store_id, self.as_of)
        with self._lock:
            if key not in self._history:
                first, _ = self.repo.data_bounds()
                start = max(first, self.as_of - timedelta(days=364))
                self._history[key] = self.repo.sales_matrix(store_id, start, self.as_of)
            return self._history[key]

    def forecast(self, store_id: str, product_id: str, horizon: int) -> ForecastResult:
        key = (store_id, product_id, self.as_of, horizon)
        cached = self._forecasts.get(key)
        if cached is not None:
            return cached
        if self.forecast_provider is None:
            result = forecast_product(
                self.repo, self.catalog, store_id, product_id, self.as_of, horizon, history=self.history(store_id)
            )
        else:
            result = self.forecast_provider.forecast(store_id, product_id, self.as_of, horizon)
        with self._lock:
            if len(self._forecasts) > 5000:
                self._forecasts.clear()
            self._forecasts[key] = result
        return result

    def uplift(self) -> dict[StrategyType, UpliftEstimate]:
        """Campaign-measured uplift per strategy type (computed once, campaigns up to `as_of` only)."""
        if self._uplift is None:
            measured = measure_uplift(self.repo, self.as_of)
            with self._lock:
                self._uplift = measured
        return self._uplift

    def affinity_partners(self, product_id: str) -> list[Affinity]:
        """Products observed in the same tickets as `product_id`, strongest lift first."""
        pairs = [a for a in self.repo.affinities() if a.product_id == product_id and a.lift > 1]
        return sorted(pairs, key=lambda a: a.lift, reverse=True)

    # ----------------------------------------------------------------- refs
    def store_ref(self, store: Store) -> StoreRef:
        return StoreRef(
            store_id=store.store_id,
            store_name=store.store_name,
            colonia=store.colonia,
            zone_type=store.zone_type,
            secondary_zone_type=store.secondary_zone_type,
            size_category=store.size_category,
            latitude=store.latitude,
            longitude=store.longitude,
        )

    def product_ref(self, product: Product) -> ProductRef:
        category = self.catalog.categories[product.category_id]
        return ProductRef(
            product_id=product.product_id,
            product_name=product.product_name,
            category_id=product.category_id,
            category_name=category.category_name,
            brand=product.brand,
            unit=product.unit,
            retail_price=product.retail_price,
            shelf_life_days=product.shelf_life_days,
            is_perishable=category.is_perishable,
            weather_sensitivity=product.weather_sensitivity,
        )

    def supplier_ref(self, supplier_id: str) -> SupplierRef:
        s = self.catalog.suppliers[supplier_id]
        return SupplierRef(
            supplier_id=s.supplier_id,
            supplier_name=s.supplier_name,
            email=s.email,
            city=s.city,
            reliability_score=s.reliability_score,
        )

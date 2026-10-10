"""Load and validate the local synthetic data required by baseline demand forecasting."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from .config import DEFAULT_DATA_DIR


@dataclass(frozen=True)
class ForecastData:
    """Observed grid, immutable store/product attributes, and known date context."""

    observed: pd.DataFrame
    stores: pd.DataFrame
    products: pd.DataFrame
    context: pd.DataFrame


def _read_csv(data_dir: Path, name: str, **kwargs) -> pd.DataFrame:
    path = data_dir / f"{name}.csv"
    if not path.exists():
        raise FileNotFoundError(f"Required forecast input is missing: {path}")
    return pd.read_csv(path, **kwargs)


def _validate_unique(frame: pd.DataFrame, keys: list[str], name: str) -> None:
    if frame.duplicated(keys).any():
        raise ValueError(f"{name} must be unique by {keys}")


def load_forecast_data(data_dir: Path = DEFAULT_DATA_DIR) -> ForecastData:
    """Load only columns that are available at training or forecast time."""
    data_dir = Path(data_dir)
    sales = _read_csv(
        data_dir,
        "sales",
        usecols=["date", "store_id", "product_id", "units_sold", "campaign_id"],
        dtype={"store_id": "string", "product_id": "string", "campaign_id": "string"},
        parse_dates=["date"],
    )
    inventory = _read_csv(
        data_dir,
        "inventory_daily",
        usecols=["date", "store_id", "product_id", "units_sold", "lost_demand"],
        dtype={"store_id": "string", "product_id": "string"},
        parse_dates=["date"],
    ).rename(columns={"units_sold": "inventory_units_sold"})
    calendar = _read_csv(data_dir, "calendar", parse_dates=["date"])
    weather = _read_csv(data_dir, "weather", parse_dates=["date"])
    stores = _read_csv(data_dir, "stores", dtype={"store_id": "string"})
    products = _read_csv(data_dir, "products", dtype={"product_id": "string"})

    keys = ["date", "store_id", "product_id"]
    _validate_unique(sales, keys, "sales")
    _validate_unique(inventory, keys, "inventory_daily")
    _validate_unique(calendar, ["date"], "calendar")
    _validate_unique(weather, ["date"], "weather")

    observed = sales.merge(inventory, on=keys, how="inner", validate="one_to_one")
    if len(observed) != len(sales):
        raise ValueError("Every sales row must have a matching inventory row")
    if not np.array_equal(observed.units_sold.to_numpy(), observed.inventory_units_sold.to_numpy()):
        raise ValueError("sales.units_sold and inventory_daily.units_sold must reconcile")

    context = calendar.merge(weather, on="date", how="inner", validate="one_to_one")
    observed = observed.merge(context, on="date", how="left", validate="many_to_one")
    observed = observed.merge(products, on="product_id", how="left", validate="many_to_one")
    observed = observed.merge(stores, on="store_id", how="left", validate="many_to_one")
    if observed[["category_id", "zone_type"]].isna().any().any():
        raise ValueError("Every observed row must resolve product and store dimensions")

    observed["campaign_id"] = observed["campaign_id"].fillna("").astype("string")
    observed["is_treated_campaign"] = observed["campaign_id"].str.strip().ne("")
    observed["demand_target"] = observed["units_sold"].astype(float) + observed["lost_demand"].astype(float)
    if (observed["demand_target"] < 0).any():
        raise ValueError("demand_target cannot be negative")
    return ForecastData(observed=observed, stores=stores, products=products, context=context)


def load_future_context(data_dir: Path = DEFAULT_DATA_DIR) -> pd.DataFrame:
    """Load the feature-only, known-in-advance context used for the demo horizon."""
    context = _read_csv(data_dir, "forecast_context", parse_dates=["date"])
    _validate_unique(context, ["date"], "forecast_context")
    return context.sort_values("date").reset_index(drop=True)

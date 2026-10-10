"""Build the small test fixture (tests/fixtures/v2) from a generated Road2Tech v2 dataset.

    python scripts/make_fixture.py ../../public/softtek/dataset/dataset-hackathon-road2tech/v2

Keeps 3 stores and the products of the strategy tests and of every historical campaign, over
the full date range, so stock reconstruction and campaign measurement stay exact.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

STORES = ["S001", "S016", "S018"]
PRODUCTS = ["P001", "P010", "P016", "P023", "P026", "P034", "P059", "P067", "P069", "P085", "P097", "P117"]
OUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "v2"


def _read(source: Path, name: str) -> pd.DataFrame:
    return pd.read_csv(source / f"{name}.csv", dtype=str, keep_default_na=False)


def main(source: Path) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name in ("zones", "categories", "calendar", "weather", "suppliers", "promotion_campaigns"):
        _read(source, name).to_csv(OUT / f"{name}.csv", index=False)
    stores = _read(source, "stores")
    stores[stores.store_id.isin(STORES)].to_csv(OUT / "stores.csv", index=False)
    for name in ("products", "supplier_products"):
        frame = _read(source, name)
        frame[frame.product_id.isin(PRODUCTS)].to_csv(OUT / f"{name}.csv", index=False)
    for name in ("sales", "inventory_lots", "purchase_orders"):
        frame = _read(source, name)
        frame[frame.store_id.isin(STORES) & frame.product_id.isin(PRODUCTS)].to_csv(OUT / f"{name}.csv", index=False)
    days = _read(source, "promotion_store_days")
    days[days.store_id.isin(STORES)].to_csv(OUT / "promotion_store_days.csv", index=False)
    affinity = _read(source, "product_affinity")
    keep = affinity.product_id.isin(PRODUCTS) & affinity.other_product_id.isin(PRODUCTS)
    affinity[keep].to_csv(OUT / "product_affinity.csv", index=False)
    print(f"Fixture written to {OUT}")


if __name__ == "__main__":
    main(Path(sys.argv[1]))

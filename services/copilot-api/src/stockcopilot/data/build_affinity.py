"""Build `product_affinity.csv` from `ticket_lines.csv` (observed co-purchases).

    python -m stockcopilot.data.build_affinity --dataset-dir <v2 folder>

Reading the ~7M ticket lines takes ~30 s, so this runs once after generating the dataset
instead of at API startup. Campaign-driven lines (`line_type == campaign_bundle`) are excluded
so promotions do not inflate the affinity they are later used to justify.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

from ..config import get_settings

MIN_CO_TICKETS = 20
COLUMNS = ["product_id", "other_product_id", "co_tickets", "confidence", "lift"]


def compute_affinity(lines: pd.DataFrame, min_co_tickets: int = MIN_CO_TICKETS) -> pd.DataFrame:
    """Pairs bought in the same ticket: count, confidence P(other | product) and lift."""
    lines = lines[lines["line_type"] != "campaign_bundle"][["ticket_id", "product_id"]].drop_duplicates()
    total_tickets = lines["ticket_id"].nunique()
    if total_tickets == 0:
        return pd.DataFrame(columns=COLUMNS)
    per_product = lines.groupby("product_id", observed=True).size()
    sizes = lines.groupby("ticket_id").size()
    baskets = lines[lines["ticket_id"].isin(sizes[sizes > 1].index)]
    pairs = baskets.merge(baskets, on="ticket_id", suffixes=("", "_other"))
    pairs = pairs[pairs["product_id"] != pairs["product_id_other"]]
    co = pairs.groupby(["product_id", "product_id_other"], observed=True).size().rename("co_tickets").reset_index()
    co = co[co["co_tickets"] >= min_co_tickets].rename(columns={"product_id_other": "other_product_id"})
    base = co["product_id"].map(per_product).astype(float)
    other = co["other_product_id"].map(per_product).astype(float)
    co["confidence"] = (co["co_tickets"] / base).round(5)
    co["lift"] = (co["co_tickets"] * total_tickets / (base * other)).round(3)
    return co[COLUMNS].sort_values(["product_id", "lift"], ascending=[True, False]).reset_index(drop=True)


def build(dataset_dir: Path) -> Path:
    lines = pd.read_csv(
        dataset_dir / "ticket_lines.csv",
        usecols=["ticket_id", "product_id", "line_type"],
        dtype={"ticket_id": "string", "product_id": "string", "line_type": "string"},
    )
    out = dataset_dir / "product_affinity.csv"
    compute_affinity(lines).to_csv(out, index=False)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", type=Path, default=get_settings().dataset_dir)
    args = parser.parse_args(argv)
    out = build(args.dataset_dir.expanduser().resolve())
    print(f"wrote {out} ({len(pd.read_csv(out))} pairs)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

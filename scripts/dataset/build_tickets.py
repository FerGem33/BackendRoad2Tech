"""Expand daily sales into reproducible ticket lines while preserving every sales aggregate.

`ticket_lines.csv` is intentionally generated after inventory-constrained sales.  Its
quantities and money therefore reconcile with `sales.csv`; it never creates demand or
revenue that the operations simulation did not fulfil.
"""
import csv

import numpy as np
import pandas as pd

from common import OUT_DIR, rng

# Product pairs that create ordinary historical basket affinity, apart from campaigns.
AFFINITY_RULES = (
    ("P069", "P085", 0.16),  # coffee + conchas
    ("P026", "P034", 0.12),  # Corona + chips
    ("P015", "P047", 0.10),  # energy drink + chocolate
    ("P059", "P001", 0.08),  # sandwich + cold drink
)


def _timestamp(date_text: str, r: np.random.Generator) -> str:
    # Convenience-store traffic: a mix of morning, lunch, afternoon and evening purchases.
    hour = int(r.choice([7, 8, 9, 13, 14, 18, 19, 20, 21], p=[.08, .10, .08, .12, .14, .16, .14, .10, .08]))
    minute, second = int(r.integers(0, 60)), int(r.integers(0, 60))
    return f"{date_text}T{hour:02d}:{minute:02d}:{second:02d}"


def _line(ticket_id, timestamp, date_text, store_id, product_id, quantity, price, discount_pct,
          campaign_id, line_type):
    gross = round(quantity * price, 2)
    discount = round(gross * discount_pct, 2)
    return [ticket_id, timestamp, date_text, store_id, product_id, quantity, gross, discount,
            round(gross - discount, 2), campaign_id or "", line_type]


def _campaign_pairs(group: pd.DataFrame) -> list[tuple[str, str, str]]:
    """Return campaign id and its two SKU ids when both are sold in this store-day."""
    pairs = []
    for campaign_id, rows in group.dropna(subset=["campaign_id"]).groupby("campaign_id", sort=False):
        ids = rows.product_id.tolist()
        if len(ids) == 2:
            pairs.append((campaign_id, ids[0], ids[1]))
    return pairs


def main() -> None:
    print("Building full-period ticket lines...")
    sales = pd.read_csv(OUT_DIR / "sales.csv", keep_default_na=True)
    products = pd.read_csv(OUT_DIR / "products.csv").set_index("product_id")
    prices = products.retail_price.to_dict()
    out = OUT_DIR / "ticket_lines.csv"
    fields = ["ticket_id", "timestamp", "date", "store_id", "product_id", "quantity", "gross_amount",
              "discount_amount", "net_amount", "campaign_id", "line_type"]
    ticket_number = 0
    line_count = 0
    with out.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(fields)
        for group_number, ((date_text, store_id), group) in enumerate(sales.groupby(["date", "store_id"], sort=False)):
            r = rng(80, group_number)
            rows = {row.product_id: row for row in group.itertuples(index=False) if row.units_sold > 0}
            remaining = {pid: int(row.units_sold) for pid, row in rows.items()}

            def add_ticket(lines):
                nonlocal ticket_number, line_count
                ticket_number += 1
                ticket_id = f"T{ticket_number:010d}"
                timestamp = _timestamp(date_text, r)
                for pid, qty, line_type in lines:
                    row = rows[pid]
                    discount_pct = 0.0 if not pd.notna(row.campaign_id) else float(row.discount_amount / row.gross_revenue)
                    writer.writerow(_line(ticket_id, timestamp, date_text, store_id, pid, qty, prices[pid], discount_pct,
                                          row.campaign_id if pd.notna(row.campaign_id) else None, line_type))
                    line_count += 1

            # First, preserve a visible share of treatment baskets as multi-product campaign tickets.
            for campaign_id, first, second in _campaign_pairs(group):
                qty = min(remaining.get(first, 0), remaining.get(second, 0))
                for _ in range(min(3, int(qty * 0.25))):
                    add_ticket([(first, 1, "campaign_bundle"), (second, 1, "campaign_bundle")])
                    remaining[first] -= 1
                    remaining[second] -= 1

            # Then add normal co-purchase history for association rules / recommenders.
            for first, second, rate in AFFINITY_RULES:
                qty = min(remaining.get(first, 0), remaining.get(second, 0))
                for _ in range(min(4, int(qty * rate))):
                    add_ticket([(first, 1, "affinity_bundle"), (second, 1, "affinity_bundle")])
                    remaining[first] -= 1
                    remaining[second] -= 1

            # Residual units retain exact daily totals. Small quantities make individual baskets plausible
            # without turning this educational dataset into one row per physical unit.
            for pid in group.product_id:
                left = remaining.get(pid, 0)
                while left:
                    qty = min(left, int(r.integers(1, 7)))
                    add_ticket([(pid, qty, "regular")])
                    left -= qty
    print(f"  wrote {out.relative_to(OUT_DIR.parents[3])}  tickets={ticket_number:,} lines={line_count:,} "
          f"size={out.stat().st_size / 1e6:.2f} MB")


if __name__ == "__main__":
    main()

"""Load the Road2Tech v2 CSV folder into PostgreSQL (idempotent: tables are truncated first).

    python -m stockcopilot.data.load_postgres --dataset-dir <v2 folder> --database-url postgresql://...

Uses COPY, so the 3M-row sales table loads in well under a minute.
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from importlib import resources
from pathlib import Path

import psycopg

from ..config import get_settings

# Load order respects foreign keys. Optional tables are skipped when their CSV is missing.
# inventory_daily, ticket_lines and forecast_context are not loaded: the API rebuilds stock from
# lots and only needs the affinity summary of the tickets.
OPTIONAL = {"promotion_campaigns", "promotion_store_days", "product_affinity"}
TABLES = (
    "zones",
    "stores",
    "categories",
    "products",
    "suppliers",
    "supplier_products",
    "calendar",
    "weather",
    "sales",
    "inventory_lots",
    "purchase_orders",
    "promotion_campaigns",
    "promotion_store_days",
    "product_affinity",
)


def apply_schema(conn: psycopg.Connection) -> None:
    sql = resources.files("stockcopilot.data").joinpath("schema.sql").read_text(encoding="utf-8")
    conn.execute(sql)


def load(dataset_dir: Path, database_url: str) -> None:
    if not (dataset_dir / "product_affinity.csv").exists() and (dataset_dir / "ticket_lines.csv").exists():
        from .build_affinity import build

        print(f"building {build(dataset_dir)}")
    with psycopg.connect(database_url, autocommit=False) as conn:
        apply_schema(conn)
        conn.execute(f"TRUNCATE {', '.join(reversed(TABLES))}")
        for table in TABLES:
            path = dataset_dir / f"{table}.csv"
            if table in OPTIONAL and not path.exists():
                print(f"{table:<20} skipped (no {path.name})")
                continue
            started = time.perf_counter()
            with path.open(encoding="utf-8", newline="") as handle:
                header = next(csv.reader(handle))
                handle.seek(0)
                columns = ", ".join(header)
                with conn.cursor().copy(
                    f"COPY {table} ({columns}) FROM STDIN WITH (FORMAT csv, HEADER true, NULL '')"
                ) as copy:
                    while chunk := handle.read(1 << 20):
                        copy.write(chunk)
            count = conn.execute(f"SELECT count(*) FROM {table}").fetchone()
            print(f"{table:<18} {count[0] if count else 0:>10,} rows  {time.perf_counter() - started:5.1f}s")
        conn.execute("ANALYZE")
        conn.commit()


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", type=Path, default=settings.dataset_dir)
    parser.add_argument("--database-url", default=settings.database_url)
    args = parser.parse_args(argv)
    if not args.database_url:
        parser.error("--database-url (or DATABASE_URL) is required")
    load(args.dataset_dir.expanduser().resolve(), args.database_url)
    return 0


if __name__ == "__main__":
    sys.exit(main())

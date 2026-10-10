"""Shared constants, paths and I/O helpers for the v2 dataset generators."""
from pathlib import Path

import numpy as np
import pandas as pd

SEED = 42

REPO_ROOT = Path(__file__).resolve().parents[2]
INPUT_DIR = REPO_ROOT / "public" / "softtek" / "dataset" / "dataset-hackathon-road2tech"
OUT_DIR = INPUT_DIR / "v2"

START = pd.Timestamp("2024-01-01")
END = pd.Timestamp("2026-09-30")
DATES = pd.date_range(START, END, freq="D")
N_DAYS = len(DATES)

# Replenishment policy (see supplier_selection.py and README).
CYCLE_DAYS = 7
SAFETY_DAYS_PERISHABLE = 1
SAFETY_DAYS_STABLE = 3
PERISHABLE_MAX_SHELF_LIFE = 30
MIN_PREDICTED_DEMAND_TO_ORDER = 0.05

# Saltillo urban-area bounding box used for store placement and validation.
LAT_RANGE = (25.36, 25.49)
LON_RANGE = (-101.06, -100.92)


def rng(*keys: int) -> np.random.Generator:
    """Independent, reproducible random stream derived from SEED and integer keys."""
    return np.random.default_rng([SEED, *keys])


def out_path(name: str) -> Path:
    return OUT_DIR / f"{name}.csv"


def write_csv(df: pd.DataFrame, name: str, float_format: str | None = None) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = out_path(name)
    df.to_csv(path, index=False, encoding="utf-8", float_format=float_format)
    print(f"  wrote {path.relative_to(REPO_ROOT)}  rows={len(df):,}  size={path.stat().st_size / 1e6:.2f} MB")
    return path


def read_csv(name: str, **kwargs) -> pd.DataFrame:
    return pd.read_csv(out_path(name), **kwargs)

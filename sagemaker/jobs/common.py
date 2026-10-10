"""Shared helpers for SageMaker training and processing containers."""
from __future__ import annotations

import json
import os
import tarfile
from pathlib import Path
from typing import Any


def environment_path(name: str, default: str) -> Path:
    """Resolve a SageMaker environment directory without depending on a local layout."""
    return Path(os.environ.get(name, default)).resolve()


def environment_int(name: str, default: int) -> int:
    """Read a positive integer hyperparameter supplied by SageMaker."""
    value = int(os.environ.get(name, default))
    if value < 1:
        raise ValueError(f"{name} must be positive")
    return value


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def extract_model_archive(archive: Path, destination: Path) -> None:
    """Extract a SageMaker model archive while rejecting path-traversal members."""
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar.getmembers():
            target = (destination / member.name).resolve()
            if target != root and root not in target.parents:
                raise ValueError(f"Unsafe model archive member: {member.name}")
        tar.extractall(destination, filter="data")

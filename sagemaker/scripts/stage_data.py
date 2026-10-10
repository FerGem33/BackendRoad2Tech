"""Upload only validated forecast inputs and publish an immutable data manifest."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from botocore.exceptions import ClientError

from scripts.forecast.config import DEFAULT_DATA_DIR

from .aws_clients import client, session


FORECAST_INPUTS = (
    "sales.csv",
    "inventory_daily.csv",
    "stores.csv",
    "products.csv",
    "calendar.csv",
    "weather.csv",
    "forecast_context.csv",
)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def latest_sales_date(path: Path) -> str:
    with path.open(newline="", encoding="utf-8") as stream:
        dates = [row["date"] for row in csv.DictReader(stream)]
    if not dates:
        raise ValueError("sales.csv is empty")
    return max(dates)


def stage_data(bucket: str, data_dir: Path, prefix: str, profile: str | None, region: str) -> dict:
    missing = [name for name in FORECAST_INPUTS if not (data_dir / name).is_file()]
    if missing:
        raise FileNotFoundError(f"Missing forecast input files: {', '.join(missing)}")
    aws = session(profile, region)
    s3 = client(aws, "s3")
    data_as_of = latest_sales_date(data_dir / "sales.csv")
    manifest = {
        "schema_version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "data_as_of": data_as_of,
        "data_prefix": prefix.rstrip("/"),
        "files": [],
    }
    for name in FORECAST_INPUTS:
        path = data_dir / name
        key = f"{manifest['data_prefix']}/{name}"
        s3.upload_file(str(path), bucket, key, ExtraArgs={"ServerSideEncryption": "AES256", "ContentType": "text/csv"})
        manifest["files"].append({"name": name, "bytes": path.stat().st_size, "sha256": file_sha256(path)})
    body = json.dumps(manifest, indent=2, sort_keys=True).encode() + b"\n"
    s3.put_object(
        Bucket=bucket,
        Key=f"{manifest['data_prefix']}/manifest.json",
        Body=body,
        ContentType="application/json",
        ServerSideEncryption="AES256",
    )
    s3.put_object(
        Bucket=bucket,
        Key="curated/forecast/latest.json",
        Body=body,
        ContentType="application/json",
        ServerSideEncryption="AES256",
    )
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--prefix", default=None)
    parser.add_argument("--profile", default=None)
    parser.add_argument("--region", default=os.environ.get("AWS_REGION", "us-east-1"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    prefix = args.prefix or f"curated/forecast/{datetime.now(UTC):%Y%m%dT%H%M%SZ}"
    try:
        manifest = stage_data(args.bucket, args.data_dir.resolve(), prefix, args.profile, args.region)
    except (ClientError, OSError, ValueError) as error:
        print(f"Data staging failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

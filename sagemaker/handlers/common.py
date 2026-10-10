"""Shared AWS helpers for the lightweight forecast Lambdas."""
from __future__ import annotations

import json
import os
from decimal import Decimal
from typing import Any

import boto3
from botocore.config import Config


AWS_CONFIG = Config(retries={"total_max_attempts": 3, "mode": "standard"}, connect_timeout=5, read_timeout=60)
S3 = boto3.client("s3", config=AWS_CONFIG)
SAGEMAKER = boto3.client("sagemaker", config=AWS_CONFIG)
DYNAMODB = boto3.resource("dynamodb", config=AWS_CONFIG)


def setting(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def table():
    return DYNAMODB.Table(setting("FORECAST_TABLE_NAME"))


def load_json(bucket: str, key: str) -> dict[str, Any]:
    with S3.get_object(Bucket=bucket, Key=key)["Body"] as body:
        return json.loads(body.read())


def parse_s3_url(url: str) -> tuple[str, str]:
    if not url.startswith("s3://"):
        raise ValueError(f"Expected an S3 URL, received {url}")
    bucket_and_key = url.removeprefix("s3://")
    bucket, separator, key = bucket_and_key.partition("/")
    if not bucket or not separator or not key:
        raise ValueError(f"Expected an S3 object URL, received {url}")
    return bucket, key


def native_json(line: str) -> dict[str, Any]:
    """Decode numerical values as Decimal for DynamoDB's exact number representation."""
    return json.loads(line, parse_float=Decimal)

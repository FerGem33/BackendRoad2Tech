"""Invoke the deployed forecast-runner Lambda once without waiting for the schedule."""
from __future__ import annotations

import argparse
import json
import sys

from botocore.exceptions import ClientError

from .aws_clients import client, session


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--function-name", required=True)
    parser.add_argument("--profile", default=None)
    parser.add_argument("--region", default="us-east-1")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        response = client(session(args.profile, args.region), "lambda").invoke(
            FunctionName=args.function_name,
            InvocationType="RequestResponse",
            Payload=json.dumps({"source": "manual"}).encode(),
        )
        with response["Payload"] as payload:
            print(payload.read().decode())
    except ClientError as error:
        print(f"Forecast run failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

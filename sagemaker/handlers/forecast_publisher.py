"""Validate a completed processing output and atomically switch the active forecast pointer."""
from __future__ import annotations

from datetime import UTC, datetime

from common import S3, SAGEMAKER, native_json, parse_s3_url, table


EXPECTED_STORE_SKUS = 25 * 120
EXPECTED_HORIZON = 14


def _validate(records: list[dict]) -> None:
    if len(records) != EXPECTED_STORE_SKUS:
        raise ValueError(f"Expected {EXPECTED_STORE_SKUS} store-SKU records, received {len(records)}")
    for record in records:
        points = record.get("forecast", [])
        if len(points) != EXPECTED_HORIZON:
            raise ValueError(f"{record.get('store_id')}/{record.get('product_id')} has {len(points)} forecast points")
        for point in points:
            if min(point["p50"], point["p80"], point["p90"]) < 0:
                raise ValueError("Forecast quantiles cannot be negative")
            if not point["p50"] <= point["p80"] <= point["p90"]:
                raise ValueError("Forecast quantiles must be monotonic")


def _output_url(job: dict) -> str:
    outputs = job.get("ProcessingOutputConfig", {}).get("Outputs", [])
    if len(outputs) != 1:
        raise ValueError("Expected exactly one forecast processing output")
    return outputs[0]["S3Output"]["S3Uri"].rstrip("/") + "/forecasts.jsonl"


def lambda_handler(event, _context):
    detail = event.get("detail", {})
    job_name = detail.get("ProcessingJobName")
    if not job_name:
        raise ValueError("SageMaker event does not contain ProcessingJobName")
    job = SAGEMAKER.describe_processing_job(ProcessingJobName=job_name)
    if job.get("ProcessingJobStatus") != "Completed":
        return {"status": "ignored", "reason": f"Processing job status is {job.get('ProcessingJobStatus')}"}
    if job.get("Environment", {}).get("STOCKCOPILOT_RUN_TYPE") != "forecast":
        return {"status": "ignored", "reason": "Processing job is not a StockCopilot forecast run"}

    active = table().get_item(Key={"pk": "CONTROL", "sk": "ACTIVE_MODEL"}).get("Item")
    expected_package = job["Environment"]["MODEL_PACKAGE_ARN"]
    if not active or active.get("model_package_arn") != expected_package:
        return {"status": "ignored", "reason": "Processing output belongs to a stale model"}

    bucket, key = parse_s3_url(_output_url(job))
    with S3.get_object(Bucket=bucket, Key=key)["Body"] as body:
        records = [native_json(line.decode()) for line in body.iter_lines() if line.strip()]
    _validate(records)

    forecasts = table()
    with forecasts.batch_writer() as writer:
        for record in records:
            writer.put_item(
                Item={
                    "pk": f"FORECAST#{record['store_id']}#{record['product_id']}",
                    "sk": f"AS_OF#{record['data_as_of']}",
                    **record,
                    "published_at": datetime.now(UTC).isoformat(),
                    "processing_job_name": job_name,
                }
            )
    forecasts.put_item(
        Item={
            "pk": "CONTROL",
            "sk": "ACTIVE_FORECAST",
            "data_as_of": records[0]["data_as_of"],
            "model_package_arn": expected_package,
            "model_version": records[0]["model_version"],
            "processing_job_name": job_name,
            "published_at": datetime.now(UTC).isoformat(),
        }
    )
    return {"status": "published", "processing_job_name": job_name, "records": len(records)}

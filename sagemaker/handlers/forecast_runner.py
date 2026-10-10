"""Start one SageMaker Processing Job from the active model and latest curated dataset."""
from __future__ import annotations

from datetime import UTC, datetime

from common import SAGEMAKER, load_json, setting, table


def _job_name() -> str:
    return f"stockcopilot-forecast-{datetime.now(UTC):%Y%m%d%H%M%S}"


def lambda_handler(event, _context):
    bucket = setting("FORECAST_BUCKET_NAME")
    active = table().get_item(Key={"pk": "CONTROL", "sk": "ACTIVE_MODEL"}).get("Item")
    if not active:
        return {"status": "skipped", "reason": "No approved active model"}
    manifest = load_json(bucket, "curated/forecast/latest.json")
    job_name = _job_name()
    SAGEMAKER.create_processing_job(
        ProcessingJobName=job_name,
        RoleArn=setting("SAGEMAKER_EXECUTION_ROLE_ARN"),
        AppSpecification={
            "ImageUri": active["image_uri"],
            "ContainerEntrypoint": ["python", "-m", "sagemaker.jobs.batch_predict"],
        },
        ProcessingInputs=[
            {
                "InputName": "forecast-data",
                "S3Input": {
                    "S3Uri": f"s3://{bucket}/{manifest['data_prefix']}/",
                    "LocalPath": "/opt/ml/processing/input/data",
                    "S3DataType": "S3Prefix",
                    "S3InputMode": "File",
                    "S3DataDistributionType": "FullyReplicated",
                },
            },
            {
                "InputName": "model-artifact",
                "S3Input": {
                    "S3Uri": active["model_data_url"],
                    "LocalPath": "/opt/ml/processing/input/model",
                    "S3DataType": "S3Prefix",
                    "S3InputMode": "File",
                    "S3DataDistributionType": "FullyReplicated",
                },
            },
        ],
        ProcessingOutputConfig={
            "Outputs": [
                {
                    "OutputName": "forecasts",
                    "S3Output": {
                        "S3Uri": f"s3://{bucket}/forecast-runs/{job_name}",
                        "LocalPath": "/opt/ml/processing/output",
                        "S3UploadMode": "EndOfJob",
                    },
                }
            ]
        },
        ProcessingResources={"ClusterConfig": {"InstanceCount": 1, "InstanceType": "ml.m5.2xlarge", "VolumeSizeInGB": 30}},
        StoppingCondition={"MaxRuntimeInSeconds": 3600},
        Environment={
            "MODEL_PACKAGE_ARN": active["model_package_arn"],
            "MODEL_VERSION": active["model_version"],
            "STOCKCOPILOT_RUN_TYPE": "forecast",
        },
        Tags=[
            {"Key": "Project", "Value": "StockCopilot"},
            {"Key": "Component", "Value": "ForecastBatch"},
            {"Key": "ModelVersion", "Value": active["model_version"]},
        ],
    )
    return {"status": "started", "processing_job_name": job_name, "source": event.get("source", "unknown")}

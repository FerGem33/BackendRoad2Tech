"""Start, quality-gate, register, and activate one SageMaker CatBoost model version."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import UTC, datetime

from botocore.exceptions import ClientError

from .aws_clients import client, resource, session


def _read_json_object(s3, bucket: str, key: str) -> dict:
    with s3.get_object(Bucket=bucket, Key=key)["Body"] as body:
        return json.loads(body.read())


def _job_name() -> str:
    return f"stockcopilot-train-{datetime.now(UTC):%Y%m%d%H%M%S}"


def start_training(args: argparse.Namespace) -> str:
    aws = session(args.profile, args.region)
    s3 = client(aws, "s3")
    sm = client(aws, "sagemaker")
    manifest = _read_json_object(s3, args.bucket, "curated/forecast/latest.json")
    data_as_of = args.data_as_of or manifest["data_as_of"]
    job_name = _job_name()
    sm.create_training_job(
        TrainingJobName=job_name,
        RoleArn=args.execution_role_arn,
        AlgorithmSpecification={"TrainingImage": args.image_uri, "TrainingInputMode": "File"},
        InputDataConfig=[
            {
                "ChannelName": "training",
                "ContentType": "text/csv",
                "InputMode": "File",
                "DataSource": {
                    "S3DataSource": {
                        "S3DataType": "S3Prefix",
                        "S3Uri": f"s3://{args.bucket}/{manifest['data_prefix']}/",
                        "S3DataDistributionType": "FullyReplicated",
                    }
                },
            }
        ],
        OutputDataConfig={"S3OutputPath": f"s3://{args.bucket}/model-runs"},
        ResourceConfig={"InstanceType": args.instance_type, "InstanceCount": 1, "VolumeSizeInGB": 30},
        StoppingCondition={"MaxRuntimeInSeconds": args.max_runtime_seconds},
        HyperParameters={"data_as_of": data_as_of, "model_iterations": str(args.iterations)},
        Tags=[
            {"Key": "Project", "Value": "StockCopilot"},
            {"Key": "Component", "Value": "ForecastTraining"},
            {"Key": "DatasetPrefix", "Value": manifest["data_prefix"]},
        ],
    )
    return job_name


def wait_for_training(sm, job_name: str, poll_seconds: int) -> dict:
    while True:
        job = sm.describe_training_job(TrainingJobName=job_name)
        status = job["TrainingJobStatus"]
        print(f"{job_name}: {status}")
        if status == "Completed":
            return job
        if status in {"Failed", "Stopped"}:
            raise RuntimeError(job.get("FailureReason", f"Training job ended with {status}"))
        time.sleep(poll_seconds)


def register_and_activate(args: argparse.Namespace, training_job: dict) -> str:
    artifact_url = training_job["ModelArtifacts"]["S3ModelArtifacts"]
    job_name = training_job["TrainingJobName"]
    aws = session(args.profile, args.region)
    sm = client(aws, "sagemaker")
    dynamodb = resource(aws, "dynamodb")
    package = sm.create_model_package(
        ModelPackageGroupName=args.model_package_group,
        ModelApprovalStatus="Approved",
        InferenceSpecification={
            "Containers": [{"Image": args.image_uri, "ModelDataUrl": artifact_url}],
            "SupportedContentTypes": ["application/json"],
            "SupportedResponseMIMETypes": ["application/json"],
        },
        CustomerMetadataProperties={
            "training_job": job_name,
            "quality_gate": "catboost_mean_wape_p50_lt_seasonal_naive",
        },
    )
    package_arn = package["ModelPackageArn"]
    model_version = package_arn.rsplit("/", 1)[-1]
    manifest = _read_json_object(client(aws, "s3"), args.bucket, "curated/forecast/latest.json")
    data_as_of = args.data_as_of or manifest["data_as_of"]
    table = dynamodb.Table(args.forecast_table)
    table.put_item(
        Item={
            "pk": "CONTROL",
            "sk": "ACTIVE_MODEL",
            "model_package_arn": package_arn,
            "model_version": model_version,
            "model_data_url": artifact_url,
            "image_uri": args.image_uri,
            "data_as_of": data_as_of,
            "training_job_name": job_name,
            "activated_at": datetime.now(UTC).isoformat(),
        }
    )
    return package_arn


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--image-uri", required=True)
    parser.add_argument("--execution-role-arn", required=True)
    parser.add_argument("--model-package-group", required=True)
    parser.add_argument("--forecast-table", required=True)
    parser.add_argument("--profile", default=None)
    parser.add_argument("--region", default=os.environ.get("AWS_REGION", "us-east-1"))
    parser.add_argument("--instance-type", default="ml.m5.2xlarge")
    parser.add_argument("--iterations", type=int, default=750)
    parser.add_argument("--data-as-of", default=None, help="Forecast cutoff date; defaults to the staged sales maximum.")
    parser.add_argument("--max-runtime-seconds", type=int, default=7200)
    parser.add_argument("--wait", action="store_true")
    parser.add_argument("--poll-seconds", type=int, default=30)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        job_name = start_training(args)
        print(f"Started SageMaker training job: {job_name}")
        if not args.wait:
            return 0
        sm = client(session(args.profile, args.region), "sagemaker")
        training_job = wait_for_training(sm, job_name, args.poll_seconds)
        package_arn = register_and_activate(args, training_job)
    except (ClientError, OSError, RuntimeError, ValueError) as error:
        print(f"Training workflow failed: {error}", file=sys.stderr)
        return 1
    print(f"Registered and activated model package: {package_arn}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

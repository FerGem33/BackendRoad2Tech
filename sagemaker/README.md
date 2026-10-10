# StockCopilot SageMaker forecast

This directory deploys the CatBoost baseline-demand model without a permanently running inference endpoint.

```text
validated v2 data -> S3 Curated -> SageMaker Training -> Model Registry
                                                |
EventBridge Scheduler -> SageMaker Processing -> S3 forecast output -> DynamoDB -> Copilot API
```

The model predicts `units_sold + lost_demand`, excludes treated campaign rows, and emits 14-day P50/P80/P90 forecasts. The scheduler starts processing at 02:00 in `America/Monterrey`; it does not retrain automatically.

## Prerequisites

- AWS CLI profile `road2tech` authenticated in `us-east-1`.
- Docker, AWS SAM CLI, Python 3.12, and a generated v2 dataset.
- IAM permission for the deployer to create the stack, push ECR images, start SageMaker jobs, register model packages, and pass only the stack's SageMaker execution role.

No key or secret is stored in this repository, container image, or Lambda environment.

Before deploying to a shared account, generate a reviewable baseline for the runtime IAM permissions from the Python SDK calls:

```bash
uvx iam-policy-autopilot@latest generate-policies \
  "$(pwd)/sagemaker/scripts/stage_data.py" \
  "$(pwd)/sagemaker/scripts/submit_training.py" \
  "$(pwd)/sagemaker/scripts/run_forecast.py" \
  "$(pwd)/sagemaker/handlers/forecast_runner.py" \
  "$(pwd)/sagemaker/handlers/forecast_publisher.py" \
  --region us-east-1 \
  --service-hints s3 dynamodb sagemaker lambda \
  --pretty
```

Review the generated policy against the SAM roles before attaching it to a human deployment identity. Do not use `--upload-policies` for this project.

## Deploy the infrastructure

From the repository root:

```bash
export AWS_PROFILE=road2tech
export AWS_REGION=us-east-1

sam build --template-file sagemaker/template.yaml
sam deploy --guided --template-file sagemaker/template.yaml --stack-name stockcopilot-forecast
```

Keep the stack outputs in shell variables:

```bash
STACK=stockcopilot-forecast
BUCKET=$(aws cloudformation describe-stacks --stack-name "$STACK" --query "Stacks[0].Outputs[?OutputKey=='ForecastBucketName'].OutputValue" --output text)
ROLE_ARN=$(aws cloudformation describe-stacks --stack-name "$STACK" --query "Stacks[0].Outputs[?OutputKey=='SageMakerExecutionRoleArn'].OutputValue" --output text)
TABLE=$(aws cloudformation describe-stacks --stack-name "$STACK" --query "Stacks[0].Outputs[?OutputKey=='ForecastTableName'].OutputValue" --output text)
PACKAGE_GROUP=$(aws cloudformation describe-stacks --stack-name "$STACK" --query "Stacks[0].Outputs[?OutputKey=='ModelPackageGroupName'].OutputValue" --output text)
RUNNER=$(aws cloudformation describe-stacks --stack-name "$STACK" --query "Stacks[0].Outputs[?OutputKey=='ForecastRunnerFunctionName'].OutputValue" --output text)
```

## Stage, train, and publish

Install the local operator dependencies and upload only the forecast inputs:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-ml.txt
python -m sagemaker.scripts.stage_data --bucket "$BUCKET" --profile road2tech
```

Build an immutable image tag and push it to the stack-created ECR repository:

```bash
IMAGE_URI=$(bash sagemaker/scripts/build_and_push.sh "$STACK")
```

Submit training. For the current interactive demo, use `2026-09-16` so the 14 forecast dates align with the Copilot API's `DEMO_AS_OF`. Omit `--data-as-of` in a live deployment to train through the newest staged sales date.

```bash
python -m sagemaker.scripts.submit_training \
  --bucket "$BUCKET" \
  --image-uri "$IMAGE_URI" \
  --execution-role-arn "$ROLE_ARN" \
  --model-package-group "$PACKAGE_GROUP" \
  --forecast-table "$TABLE" \
  --data-as-of 2026-09-16 \
  --profile road2tech \
  --wait
```

The command registers and activates a model only when its mean P50 WAPE is lower than the seasonal-naive baseline. Start the first batch manually; later runs are scheduled automatically:

```bash
python -m sagemaker.scripts.run_forecast --function-name "$RUNNER" --profile road2tech
```

The publisher Lambda validates all 3,000 `store × SKU` records and their 14 ordered quantiles before moving the active forecast pointer. A failed or stale job cannot replace the dashboard's previous forecasts.

## Connect the Copilot API

Deploy the Copilot REST stack with the forecast table output:

```bash
cd services/copilot-api
sam build
sam deploy --guided \
  --parameter-overrides \
    DatabaseUrl='postgresql://...' \
    DemoAsOf=2026-09-16 \
    ForecastProvider=dynamodb \
    ForecastTableName="$TABLE"
```

`FORECAST_PROVIDER=local` remains the default for local tests. `dynamodb` is the cloud mode; it preserves the existing REST response contract but supports only the precomputed 14-day horizon.

## Operations and cleanup

- Training and Processing Jobs terminate after completion; no SageMaker endpoint is created or billed while idle.
- S3 keeps versioned inputs and model artifacts. Forecast-run outputs expire after 30 days; empty the versioned bucket before deleting the stack when the hackathon ends.
- CloudWatch Logs contains training/processing diagnostics and Lambda traces. Use the processing-job name and request ID for troubleshooting.
- The stack uses no VPC or NAT because it accesses only regional AWS services. Add a VPC only when a private data source is introduced.

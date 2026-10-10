#!/usr/bin/env bash
set -euo pipefail

PROFILE="${AWS_PROFILE:-road2tech}"
REGION="${AWS_REGION:-us-east-1}"
STACK_NAME="${1:-stockcopilot-forecast}"
IMAGE_TAG="forecast-$(git rev-parse --short HEAD)"

repo_uri=$(aws cloudformation describe-stacks \
  --profile "$PROFILE" --region "$REGION" --stack-name "$STACK_NAME" \
  --query "Stacks[0].Outputs[?OutputKey=='ForecastRepositoryUri'].OutputValue" --output text)
registry="${repo_uri%%/*}"

aws ecr get-login-password --profile "$PROFILE" --region "$REGION" | \
  docker login --username AWS --password-stdin "$registry"
docker build --platform linux/amd64 --file sagemaker/Dockerfile --tag "$repo_uri:$IMAGE_TAG" .
docker push "$repo_uri:$IMAGE_TAG"
printf '%s\n' "$repo_uri:$IMAGE_TAG"

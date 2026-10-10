#!/usr/bin/env bash
# Redeploys the code of every lambda (the infrastructure already exists, see README.md).
# Usage: ./scripts/deploy.sh [function-name ...]   (default: all folders in functions/)
set -euo pipefail
cd "$(dirname "$0")/.."
BUCKET=${BUCKET:-road2tech-artifacts-510936382741}
REGION=${AWS_REGION:-us-east-1}
./scripts/build.sh
names=("$@")
[ ${#names[@]} -eq 0 ] && names=($(ls functions))
for n in "${names[@]}"; do
  aws s3 cp "dist/lambdas/$n.zip" "s3://$BUCKET/lambdas/$n.zip" --region "$REGION" --only-show-errors
  aws lambda update-function-code --function-name "road2tech-$n" --s3-bucket "$BUCKET" --s3-key "lambdas/$n.zip" \
    --region "$REGION" --query 'LastUpdateStatus' --output text
done

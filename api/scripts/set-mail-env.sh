#!/usr/bin/env bash
# Copies the Resend settings from ../.env.local (RESEND_PASS, RESEND_EMAIL) into RESEND_API_KEY and
# EMAIL_FROM_ADDRESS of the Lambdas that send email, keeping their other variables. Never prints the key.
# Usage: ./scripts/set-mail-env.sh [function-name ...]   (default: the lambdas that send email)
set -euo pipefail
cd "$(dirname "$0")/.."
REGION=${AWS_REGION:-us-east-1}
ENV_FILE=${ENV_FILE:-../.env.local}
key=$(grep '^RESEND_PASS=' "$ENV_FILE" | cut -d= -f2-)
from=$(grep '^RESEND_EMAIL=' "$ENV_FILE" | cut -d= -f2-)
[ -n "$key" ] && [ -n "$from" ] || { echo "faltan RESEND_PASS o RESEND_EMAIL en $ENV_FILE" >&2; exit 1; }
names=("$@")
[ ${#names[@]} -eq 0 ] && names=(restock-orders supplier-test-email send-email)
for n in "${names[@]}"; do
  fn="road2tech-$n"
  aws lambda wait function-updated-v2 --function-name "$fn" --region "$REGION"
  vars=$(aws lambda get-function-configuration --function-name "$fn" --region "$REGION" --query 'Environment.Variables' --output json)
  payload=$(KEY="$key" FROM="$from" python3 -c 'import json,os,sys; v=json.load(sys.stdin) or {}; v.update(RESEND_API_KEY=os.environ["KEY"], EMAIL_FROM_ADDRESS=os.environ["FROM"]); print(json.dumps({"Variables": v}))' <<<"$vars")
  aws lambda update-function-configuration --function-name "$fn" --region "$REGION" \
    --environment "$payload" --query 'LastUpdateStatus' --output text >/dev/null
  echo "$fn: RESEND_API_KEY y EMAIL_FROM_ADDRESS=$from actualizados"
done

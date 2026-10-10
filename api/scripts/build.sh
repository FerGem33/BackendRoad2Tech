#!/usr/bin/env bash
# Builds every lambda in functions/ for provided.al2023 (arm64) into dist/<name>.zip (binary named bootstrap).
set -euo pipefail
cd "$(dirname "$0")/.."
rm -rf dist/lambdas && mkdir -p dist/lambdas
for dir in functions/*/; do
  name=$(basename "$dir")
  out=$(mktemp -d)
  GOOS=linux GOARCH=arm64 CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -tags lambda.norpc -o "$out/bootstrap" "./$dir"
  (cd "$out" && zip -q -X "$OLDPWD/dist/lambdas/$name.zip" bootstrap)
  rm -rf "$out"
  echo "built dist/lambdas/$name.zip"
done

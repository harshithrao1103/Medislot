#!/usr/bin/env bash
set -euo pipefail

rm -rf build
mkdir -p build

for lambda_dir in lambdas/*/; do
  [ -d "$lambda_dir" ] || continue
  lambda_name="$(basename "$lambda_dir")"
  echo "Packaging ${lambda_name}..."
  target="build/${lambda_name}"
  mkdir -p "$target"
  cp -a "${lambda_dir}/." "$target/"
  if [ -f "${lambda_dir}/requirements.txt" ]; then
    python -m pip install --disable-pip-version-check \
      -r "${lambda_dir}/requirements.txt" --target "$target"
  fi
  if [ "$lambda_name" = "db_init" ]; then
    cp database/schema.sql "$target/schema.sql"
  fi
  (cd "$target" && zip -qr "../${lambda_name}.zip" .)
done

echo "Lambda packages ready in build/"

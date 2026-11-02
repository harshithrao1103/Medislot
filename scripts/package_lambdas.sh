#!/usr/bin/env bash
set -euo pipefail

rm -rf build
mkdir -p build

for lambda_dir in lambdas/*/; do
  [ -d "$lambda_dir" ] || continue
  [ "$(basename "$lambda_dir")" != "_shared" ] || continue
  lambda_name="$(basename "$lambda_dir")"
  echo "Packaging ${lambda_name}..."
  target="build/${lambda_name}"
  mkdir -p "$target"
  find "$lambda_dir" -maxdepth 1 -type f \( -name "*.py" -o -name "requirements.txt" \) -exec cp {} "$target/" \;
  if [ "$lambda_name" != "db_init" ]; then
    cp lambdas/_shared/auth_common.py "$target/auth_common.py"
    if [ "$lambda_name" = "doctor_api" ]; then
      cp lambdas/_shared/doctor_common.py "$target/doctor_common.py"
    fi
  fi
  if [ -f "${lambda_dir}/requirements.txt" ]; then
    python -m pip install --disable-pip-version-check \
      -r "${lambda_dir}/requirements.txt" --target "$target"
  fi
  if [ "$lambda_name" = "db_init" ]; then
    cp database/schema.sql "$target/schema.sql"
  fi
  find "$target" -type d -name "__pycache__" -prune -exec rm -rf {} +
  (cd "$target" && zip -qr "../${lambda_name}.zip" .)
done

echo "Lambda packages ready in build/"

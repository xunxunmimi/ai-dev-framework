#!/usr/bin/env bash

set -Eeuo pipefail

suite_dir="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
version="$(tr -d '[:space:]' < "$suite_dir/VERSION")"
mkdir -p "$suite_dir/releases"
output="$suite_dir/releases/ai-development-frameworks-v${version}.zip"
stage="$(mktemp -d)"
trap 'find "$stage" -depth -delete 2>/dev/null || true' EXIT
mkdir -p "$stage/AI开发框架"
rsync -a \
  --exclude '.DS_Store' \
  --exclude '._*' \
  --exclude '__pycache__' \
  --exclude '*.pyc' \
  --exclude 'releases/' \
  --exclude 'archive/' \
  --exclude 'evals/artifacts/' \
  "$suite_dir/" "$stage/AI开发框架/"
find "$stage/AI开发框架" -type f -name '._*' -delete
candidate="$stage/ai-development-frameworks-v${version}.zip"
(
  cd "$stage"
  /usr/bin/zip -qry -X "$candidate" "AI开发框架"
)
if [[ -f "$output" ]]; then
  stamp="$(date +%Y%m%d-%H%M%S)"
  mkdir -p "$suite_dir/archive/release-history"
  mv "$output" "$suite_dir/archive/release-history/ai-development-frameworks-v${version}.previous-${stamp}.zip"
fi
mv "$candidate" "$output"
printf 'Created %s\n' "$output"

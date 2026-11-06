#!/usr/bin/env bash

set -Eeuo pipefail

suite_dir="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
[[ $# -ge 1 ]] || { printf 'Usage: %s /absolute/project [--dry-run] [--offline-writers-stopped source] [--legacy-artifact-review /absolute/review.json --legacy-artifact-review-sha256 SHA256]\n' "$0" >&2; exit 2; }
project_path="$1"
shift
args=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) args+=("$1"); shift ;;
    --offline-writers-stopped|--legacy-artifact-review|--legacy-artifact-review-sha256)
      [[ $# -ge 2 && -n "$2" ]] || { printf '%s\n' 'This option needs an explicit nonempty value.' >&2; exit 2; }
      args+=("$1" "$2"); shift 2 ;;
    *) printf 'Unsupported update argument: %s\n' "$1" >&2; exit 2 ;;
  esac
done
exec python3 "$suite_dir/installers/project_framework.py" update "$project_path" --suite "$suite_dir" "${args[@]}"

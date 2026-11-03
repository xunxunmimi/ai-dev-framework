#!/usr/bin/env bash

set -Eeuo pipefail

suite_dir="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
[[ $# -ge 1 && $# -le 2 ]] || { printf 'Usage: %s /absolute/project [--dry-run]\n' "$0" >&2; exit 2; }
if [[ $# -eq 2 && "$2" != "--dry-run" ]]; then
  printf 'Only --dry-run is accepted as the second argument.\n' >&2
  exit 2
fi
exec python3 "$suite_dir/installers/project_framework.py" update "$1" --suite "$suite_dir" ${2:+"$2"}

#!/usr/bin/env bash

set -Eeuo pipefail

suite_dir="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
[[ $# -eq 1 ]] || { printf 'Usage: %s /absolute/project\n' "$0" >&2; exit 2; }
exec python3 "$suite_dir/installers/project_framework.py" adopt "$1" --suite "$suite_dir"

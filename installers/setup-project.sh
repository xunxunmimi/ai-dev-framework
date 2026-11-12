#!/usr/bin/env bash

set -Eeuo pipefail

suite_dir="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
mode="${1:-}"

case "$mode" in
  ios-daily)
    [[ $# -eq 2 || ( $# -eq 4 && "$3" == "--read-policy" ) ]] || { printf 'Usage: %s ios-daily /absolute/project\n' "$0" >&2; exit 2; }
    exec "$suite_dir/frameworks/ios-daily-development-rules/setup-project.sh" "$2" ios-maintenance "${4:-}"
    ;;
  harmony-daily)
    [[ $# -eq 2 || ( $# -eq 4 && "$3" == "--read-policy" ) ]] || { printf 'Usage: %s harmony-daily /absolute/project\n' "$0" >&2; exit 2; }
    exec "$suite_dir/frameworks/harmonyos-daily-development-rules/setup-project.sh" "$2" harmony-maintenance "${4:-}"
    ;;
  harmony-transition)
    [[ $# -eq 2 || ( $# -eq 4 && "$3" == "--read-policy" ) ]] || { printf 'Usage: %s harmony-transition /absolute/project\n' "$0" >&2; exit 2; }
    exec "$suite_dir/frameworks/harmonyos-daily-development-rules/setup-project.sh" "$2" migration-transition "${4:-}"
    ;;
  migration)
    [[ $# -ge 3 ]] || { printf 'Usage: %s migration /absolute/ios-source /absolute/harmony-target\n' "$0" >&2; exit 2; }
    exec python3 "$suite_dir/frameworks/ios-to-harmony-migrator-v2/scripts/start_migration.py" "${@:2}"
    ;;
  *)
    printf 'Usage: %s {ios-daily|harmony-daily|harmony-transition|migration} ...\n' "$0" >&2
    exit 2
    ;;
esac

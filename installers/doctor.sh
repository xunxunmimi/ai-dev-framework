#!/usr/bin/env bash

set -Eeuo pipefail

suite_dir="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
skill_home="${CODEX_HOME:-$HOME/.codex}/skills"
status=0

check_link() {
  local name="$1"
  local expected="$suite_dir/frameworks/$name"
  local actual=""
  if [[ -L "$skill_home/$name" ]]; then
    actual="$(readlink "$skill_home/$name")"
  fi
  local pin_root="${actual%/frameworks/$name}"
  if [[ "$actual" == "$expected" ]]; then
    printf 'INFO mutable legacy link: %s (explicit install required to pin a release)\n' "$name"
    printf 'OK skill: %s\n' "$name"
  elif [[ -f "$pin_root/.framework-pin.json" ]] && python3 "$suite_dir/installers/pin_release.py" --verify "$pin_root" >/dev/null; then
    printf 'OK pinned skill: %s\n' "$name"
    if [[ "$(cat "$pin_root/VERSION")" != "$(cat "$suite_dir/VERSION")" ]]; then
      printf 'INFO newer/different active suite; pinned registration was not automatically upgraded: %s\n' "$name"
    fi
  else
    printf 'FAIL skill: %s (run install-this-mac.sh)\n' "$name" >&2
    status=1
  fi
}

check_link ios-to-harmony-migrator-v2
check_link ios-daily-development-rules
check_link harmonyos-daily-development-rules

python3 "$suite_dir/shared/task-routing/self_test.py" || status=1
PYTHONDONTWRITEBYTECODE=1 python3 "$suite_dir/shared/task-routing/runtime_self_test.py" || status=1
PYTHONDONTWRITEBYTECODE=1 python3 "$suite_dir/shared/task-routing/read_boundary_self_test.py" || status=1
PYTHONDONTWRITEBYTECODE=1 python3 "$suite_dir/shared/task-routing/task_scope_self_test.py" || status=1
PYTHONDONTWRITEBYTECODE=1 python3 "$suite_dir/shared/task-routing/task_only_self_test.py" || status=1
PYTHONDONTWRITEBYTECODE=1 python3 "$suite_dir/shared/task-routing/lifecycle_protocol_self_test.py" || status=1
PYTHONDONTWRITEBYTECODE=1 python3 "$suite_dir/shared/task-routing/task_delivery_self_test.py" || status=1
python3 "$suite_dir/frameworks/ios-to-harmony-migrator-v2/scripts/self_test.py" || status=1
python3 "$suite_dir/frameworks/ios-daily-development-rules/scripts/self_test.py" || status=1
python3 "$suite_dir/frameworks/harmonyos-daily-development-rules/scripts/self_test.py" || status=1
python3 "$suite_dir/evals/scripts/self_test.py" || status=1
PYTHONDONTWRITEBYTECODE=1 python3 "$suite_dir/evals/scripts/safety_regression.py" || status=1
PYTHONDONTWRITEBYTECODE=1 python3 "$suite_dir/installers/upgrade_self_test.py" || status=1
PYTHONDONTWRITEBYTECODE=1 python3 "$suite_dir/installers/controlled_task_write_self_test.py" || status=1
PYTHONDONTWRITEBYTECODE=1 python3 "$suite_dir/installers/optional_ui_self_test.py" || status=1
PYTHONDONTWRITEBYTECODE=1 python3 "$suite_dir/installers/harmonyos_reimplementation_self_test.py" || status=1
PYTHONDONTWRITEBYTECODE=1 python3 "$suite_dir/evals/scripts/safety_regression.py" || status=1
PYTHONDONTWRITEBYTECODE=1 python3 "$suite_dir/evals/scripts/routing_self_test.py" || status=1

if [[ -f "${CODEX_HOME:-$HOME/.codex}/tools/lanhu-mcp/.env" ]]; then
  printf '%s\n' 'OK optional Lanhu MCP credential file exists (content not read).'
else
  printf '%s\n' 'INFO optional Lanhu MCP is not configured on this computer.'
fi

exit "$status"

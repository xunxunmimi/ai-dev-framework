#!/usr/bin/env bash

set -Eeuo pipefail

suite_dir="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
skill_home="${CODEX_HOME:-$HOME/.codex}/skills"
if [[ "${1:-}" == "--dry-run" ]]; then
  python3 "$suite_dir/installers/pin_release.py" --source "$suite_dir" --destination "${CODEX_HOME:-$HOME/.codex}/framework-versions"
  printf 'Would register three Skills under %s; no files written.\n' "$skill_home"
  exit 0
fi
[[ $# -eq 0 ]] || { printf '%s\n' 'Usage: install-this-mac.sh [--dry-run]' >&2; exit 2; }
# 先冻结并校验内容，链接不再指向会随源码修改的活动目录。
pinned_suite="$(python3 "$suite_dir/installers/pin_release.py" --source "$suite_dir" --destination "${CODEX_HOME:-$HOME/.codex}/framework-versions" --apply --path-only)"
mkdir -p "$skill_home"

install_link() {
  local name="$1"
  local source_path="$pinned_suite/frameworks/$name"
  local target_path="$skill_home/$name"
  if [[ -e "$target_path" && ! -L "$target_path" ]]; then
    printf 'Refusing to replace non-symlink skill: %s\n' "$target_path" >&2
    exit 3
  fi
  ln -sfn "$source_path" "$target_path"
  printf 'Installed %s -> %s\n' "$target_path" "$source_path"
}

install_link ios-to-harmony-migrator-v2
install_link ios-daily-development-rules
install_link harmonyos-daily-development-rules

printf '%s\n' 'Installed three framework Skills. Existing Lanhu credentials and third-party Skills were not changed.'

#!/usr/bin/env bash

set -Eeuo pipefail

kit_dir="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
target_path="${1:-}"
lifecycle="${2:-ios-maintenance}"

if [[ -z "$target_path" || "$target_path" != /* ]]; then
  printf '%s\n' "Usage: $0 /absolute/path/to/ios-project"
  exit 2
fi

if [[ ! -d "$target_path" ]]; then
  printf 'Project directory does not exist: %s\n' "$target_path" >&2
  exit 2
fi

target_path="$(CDPATH= cd -- "$target_path" && pwd -P)"
if [[ "$target_path" == "/" ]]; then
  printf '%s\n' 'Refusing to install into the filesystem root.' >&2
  exit 2
fi

if [[ "$lifecycle" != "ios-maintenance" ]]; then
  printf 'Unsupported iOS lifecycle: %s\n' "$lifecycle" >&2
  exit 2
fi





rules_dir="$target_path/.codex-rules/ios"
shared_rules="$target_path/.codex-rules/_shared"
ai_project="$target_path/.ai-project"
suite_shared="$(CDPATH= cd -- "$kit_dir/../.." && pwd)/shared"
suite_dir="$(CDPATH= cd -- "$kit_dir/../.." && pwd)"
suite_version="$(tr -d '[:space:]' < "$suite_dir/VERSION")"
# 任何项目读取之前确认可读配置范围；无策略不自动扩大为全项目扫描。
if [[ -n "${3:-}" ]]; then
  read_mode="$(python3 "$suite_dir/installers/bootstrap_read_policy.py" "$target_path" --platform ios --policy "$3")"
else
  read_mode="$(python3 "$suite_dir/installers/bootstrap_read_policy.py" "$target_path" --platform ios)"
fi
if [[ "$read_mode" == "complete" ]]; then
if [[ -d "$target_path/.migration-v2" || -d "$target_path/.migration-agents" ]]; then
  printf '%s\n' 'Migration state detected. Use ios-to-harmony-migrator-v2 instead of ordinary iOS daily rules.' >&2
  exit 3
fi
fi

# 首装由 Python 持 update→tasks 双锁覆盖全部复制；不能把 setup 当作升级入口。
if [[ -z "${AI_FRAMEWORK_INSTALL_LOCK_FDS:-}" ]]; then
  exec python3 "$suite_dir/installers/project_framework.py" setup-new "$target_path" --suite "$suite_dir" \
    --setup-argv "$kit_dir/setup-project.sh" "$target_path" "$lifecycle" "${3:-}"
fi
python3 "$suite_dir/installers/project_framework.py" setup-lock-check "$target_path" --suite "$suite_dir"
mkdir -p "$rules_dir/references" "$shared_rules/references" "$shared_rules/schemas" "$ai_project/checkpoints"

copy_managed() {
  local source_file="$1"
  local destination_file="$2"
  local marker="$3"
  if [[ ! -e "$destination_file" ]] || grep -Fq "$marker" "$destination_file"; then
    cp "$source_file" "$destination_file"
    printf 'Installed %s\n' "$destination_file"
  else
    printf 'Kept user-managed %s\n' "$destination_file"
  fi
}

copy_managed "$kit_dir/templates/WORKFLOW.md" "$rules_dir/WORKFLOW.md" '<!-- ios-daily-rules:managed-workflow -->'
copy_managed "$kit_dir/references/verification.md" "$rules_dir/references/verification.md" '<!-- ios-daily-rules:managed-verification -->'
copy_managed "$kit_dir/references/code-quality-ios.md" "$rules_dir/references/code-quality-ios.md" '<!-- ios-daily-rules:managed-code-quality -->'
copy_managed "$suite_shared/task-routing/task_router.py" "$shared_rules/task_router.py" 'Shared, evidence-based task router'
copy_managed "$suite_shared/task-routing/task_runtime.py" "$shared_rules/task_runtime.py" '任务控制工具'
copy_managed "$suite_shared/task-routing/read_boundary.py" "$shared_rules/read_boundary.py" '统一项目读取边界'
copy_managed "$suite_shared/schemas/temporary-external-grant.schema.json" "$shared_rules/schemas/temporary-external-grant.schema.json" 'ai-frameworks:managed-external-grant'
copy_managed "$suite_shared/schemas/metadata-ack.schema.json" "$shared_rules/schemas/metadata-ack.schema.json" 'ai-frameworks:managed-metadata-ack'
copy_managed "$suite_shared/context-engineering/task-control.md" "$shared_rules/references/task-control.md" '任务控制与验证约定'
copy_managed "$suite_shared/context-engineering/read-boundary.md" "$shared_rules/references/read-boundary.md" '项目读取边界'
copy_managed "$suite_shared/task-routing/framework_router.py" "$shared_rules/framework_router.py" 'Select exactly one primary framework'
copy_managed "$suite_shared/context-engineering/context-policy.md" "$shared_rules/references/context-policy.md" '四层上下文'
copy_managed "$suite_shared/lifecycle/lifecycle.md" "$shared_rules/references/lifecycle.md" '项目生命周期与框架切换'
copy_managed "$suite_shared/verification-budget/verification-budget.md" "$shared_rules/references/verification-budget.md" '验证预算'
copy_managed "$suite_shared/code-quality/code-quality-contract.md" "$shared_rules/references/code-quality-contract.md" '<!-- ai-frameworks:managed-code-quality-contract -->'
if [[ ! -e "$rules_dir/PROJECT.md" ]]; then
  cp "$kit_dir/templates/PROJECT.md" "$rules_dir/PROJECT.md"
  printf 'Created %s\n' "$rules_dir/PROJECT.md"
else
  printf 'Kept project-specific %s\n' "$rules_dir/PROJECT.md"
fi

printf '%s\n' \
  '{' \
  '  "schemaVersion": "1.0",' \
  "  \"frameworkVersion\": \"$suite_version\"," \
  '  "state": "ios-maintenance",' \
  '  "defaultFramework": "ios-daily-development-rules",' \
  '  "migrationFramework": "off",' \
  '  "routing": "one-primary-framework-per-task"' \
  '}' > "$ai_project/lifecycle.json"
if [[ ! -e "$ai_project/evidence-index.md" ]]; then
  printf '# Evidence index\n\nOnly durable, verified evidence pointers belong here.\n' > "$ai_project/evidence-index.md"
fi
python3 "$suite_dir/shared/project-detection/detect_project_capabilities.py" \
  "$target_path" --platform ios --output "$ai_project/detected-capabilities.json" >/dev/null

external_tools_marker='<!-- ios-daily-rules:external-tools -->'
if ! grep -Fq "$external_tools_marker" "$rules_dir/PROJECT.md"; then
  printf '\n%s\n%s\n\n%s\n%s\n%s\n%s\n%s\n' \
    "$external_tools_marker" \
    '## 外部能力' \
    '- 蓝湖设计源：未配置' \
    '- 设计事实优先级：当前 App（可改为“蓝湖”或“产品确认”）' \
    '- 蓝湖项目地址：未配置；这里只记录地址，不记录 Cookie' \
    '- 蓝湖 MCP 权限：只读' \
    '- Ponytail Review：条件触发，每个任务最多一次' >> "$rules_dir/PROJECT.md"
  printf 'Added external-tool routing fields to %s\n' "$rules_dir/PROJECT.md"
fi

agents_file="$target_path/AGENTS.md"
agents_marker='<!-- ios-daily-development-rules -->'
agents_pointer='For iOS maintenance, read `.ai-project/read-policy.json` before any project reads, then `.ai-project/lifecycle.json`, then `.codex-rules/ios/WORKFLOW.md` and project facts. Use the D0/D1 fast lane for clear local changes. For ambiguous or D2+ work, use `.codex-rules/_shared/task_router.py`; never let unrelated dirty files raise the task level.'

if [[ ! -e "$agents_file" ]]; then
  printf '# Project instructions\n\n%s\n%s\n' "$agents_marker" "$agents_pointer" > "$agents_file"
  printf 'Created %s\n' "$agents_file"
elif ! grep -Fq "$agents_marker" "$agents_file"; then
  printf '\n%s\n%s\n' "$agents_marker" "$agents_pointer" >> "$agents_file"
  printf 'Added iOS daily-rules pointer to %s\n' "$agents_file"
else
  printf 'iOS daily-rules pointer already exists in %s\n' "$agents_file"
fi

python3 "$suite_dir/installers/project_framework.py" record \
  --project "$target_path" --suite "$suite_dir" \
  --framework ios-daily-development-rules --lifecycle ios-maintenance \
  --managed '.codex-rules/ios/WORKFLOW.md=frameworks/ios-daily-development-rules/templates/WORKFLOW.md' \
  --managed '.codex-rules/ios/references/verification.md=frameworks/ios-daily-development-rules/references/verification.md' \
  --managed '.codex-rules/ios/references/code-quality-ios.md=frameworks/ios-daily-development-rules/references/code-quality-ios.md' \
  --managed '.codex-rules/_shared/task_router.py=shared/task-routing/task_router.py' \
  --managed '.codex-rules/_shared/task_runtime.py=shared/task-routing/task_runtime.py' \
  --managed '.codex-rules/_shared/read_boundary.py=shared/task-routing/read_boundary.py' \
  --managed '.codex-rules/_shared/schemas/temporary-external-grant.schema.json=shared/schemas/temporary-external-grant.schema.json' \
  --managed '.codex-rules/_shared/schemas/metadata-ack.schema.json=shared/schemas/metadata-ack.schema.json' \
  --managed '.codex-rules/_shared/references/task-control.md=shared/context-engineering/task-control.md' \
  --managed '.codex-rules/_shared/references/read-boundary.md=shared/context-engineering/read-boundary.md' \
  --managed '.codex-rules/_shared/framework_router.py=shared/task-routing/framework_router.py' \
  --managed '.codex-rules/_shared/references/context-policy.md=shared/context-engineering/context-policy.md' \
  --managed '.codex-rules/_shared/references/lifecycle.md=shared/lifecycle/lifecycle.md' \
  --managed '.codex-rules/_shared/references/verification-budget.md=shared/verification-budget/verification-budget.md' \
  --managed '.codex-rules/_shared/references/code-quality-contract.md=shared/code-quality/code-quality-contract.md' >/dev/null

printf '%s\n' 'Done. Review .codex-rules/ios/PROJECT.md and fill in only facts that differ from the defaults.'

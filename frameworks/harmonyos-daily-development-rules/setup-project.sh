#!/usr/bin/env bash

set -Eeuo pipefail

kit_dir="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
target_path="${1:-}"
lifecycle="${2:-harmony-maintenance}"

if [[ -z "$target_path" || "$target_path" != /* ]]; then
  printf '%s\n' "Usage: $0 /absolute/path/to/harmonyos-project"
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

if [[ "$lifecycle" != "harmony-maintenance" && "$lifecycle" != "migration-transition" ]]; then
  printf 'Unsupported HarmonyOS lifecycle: %s\n' "$lifecycle" >&2
  exit 2
fi





rules_dir="$target_path/.codex-rules/harmonyos"
shared_rules="$target_path/.codex-rules/_shared"
ai_project="$target_path/.ai-project"
suite_shared="$(CDPATH= cd -- "$kit_dir/../.." && pwd)/shared"
suite_dir="$(CDPATH= cd -- "$kit_dir/../.." && pwd)"
suite_version="$(tr -d '[:space:]' < "$suite_dir/VERSION")"
# 任何项目读取之前确认可读配置范围；无策略不自动扩大为全项目扫描。
if [[ -n "${3:-}" ]]; then
  read_mode="$(python3 "$suite_dir/installers/bootstrap_read_policy.py" "$target_path" --platform harmonyos --policy "$3")"
else
  read_mode="$(python3 "$suite_dir/installers/bootstrap_read_policy.py" "$target_path" --platform harmonyos)"
fi
if [[ "$read_mode" == "complete" ]]; then
if [[ ( -d "$target_path/.migration-v2" || -d "$target_path/.migration-agents" ) && $# -lt 2 ]]; then
  printf '%s\n' 'Migration state detected. Re-run with migration-transition or harmony-maintenance to make the lifecycle switch explicit.' >&2
  exit 3
fi
fi

# 已接入项目必须先审计；不能靠管理标记覆盖用户改动。
if [[ -f "$ai_project/framework-installation.json" ]]; then
  python3 "$suite_dir/installers/project_framework.py" audit "$target_path" --suite "$suite_dir" >/dev/null || exit 3
elif [[ -d "$target_path/.codex-rules" ]]; then
  printf '%s\n' 'Existing rules require adopt/audit before setup; refusing overwrite.' >&2
  exit 3
fi
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

copy_managed "$kit_dir/templates/WORKFLOW.md" "$rules_dir/WORKFLOW.md" '<!-- harmonyos-daily-rules:managed-workflow -->'
copy_managed "$kit_dir/references/verification.md" "$rules_dir/references/verification.md" '<!-- harmonyos-daily-rules:managed-verification -->'
copy_managed "$kit_dir/references/code-quality-harmonyos.md" "$rules_dir/references/code-quality-harmonyos.md" '<!-- harmonyos-daily-rules:managed-code-quality -->'
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
if [[ "$read_mode" == "complete" && -f "$target_path/.migration-v2/references/code-quality-contract.md" ]]; then
  copy_managed "$suite_shared/code-quality/code-quality-contract.md" "$target_path/.migration-v2/references/code-quality-contract.md" '<!-- ai-frameworks:managed-code-quality-contract -->'
fi
if [[ ! -e "$rules_dir/PROJECT.md" ]]; then
  cp "$kit_dir/templates/PROJECT.md" "$rules_dir/PROJECT.md"
  printf 'Created %s\n' "$rules_dir/PROJECT.md"
else
  printf 'Kept project-specific %s\n' "$rules_dir/PROJECT.md"
fi

if [[ "$lifecycle" == "migration-transition" ]]; then
  migration_mode='on-demand-parity-only'
else
  migration_mode='archived-read-only'
fi
printf '%s\n' \
  '{' \
  '  "schemaVersion": "1.0",' \
  "  \"frameworkVersion\": \"$suite_version\"," \
  "  \"state\": \"$lifecycle\"," \
  '  "defaultFramework": "harmonyos-daily-development-rules",' \
  "  \"migrationFramework\": \"$migration_mode\"," \
  '  "routing": "one-primary-framework-per-task"' \
  '}' > "$ai_project/lifecycle.json"
if [[ ! -e "$ai_project/evidence-index.md" ]]; then
  printf '# Evidence index\n\nOnly durable, verified evidence pointers belong here.\n' > "$ai_project/evidence-index.md"
fi
python3 "$suite_dir/shared/project-detection/detect_project_capabilities.py" \
  "$target_path" --platform harmonyos --output "$ai_project/detected-capabilities.json" >/dev/null

external_tools_marker='<!-- harmonyos-daily-rules:external-tools -->'
if ! grep -Fq "$external_tools_marker" "$rules_dir/PROJECT.md"; then
  printf '\n%s\n%s\n\n%s\n%s\n%s\n%s\n%s\n' \
    "$external_tools_marker" \
    '## 外部能力' \
    '- 蓝湖设计源：未配置' \
    '- 设计事实优先级：当前应用（可改为“蓝湖”或“产品确认”）' \
    '- 蓝湖项目地址：未配置；这里只记录地址，不记录 Cookie' \
    '- 蓝湖 MCP 权限：只读' \
    '- Ponytail Review：条件触发，每个任务最多一次' >> "$rules_dir/PROJECT.md"
  printf 'Added external-tool routing fields to %s\n' "$rules_dir/PROJECT.md"
fi

agents_file="$target_path/AGENTS.md"
agents_marker='<!-- harmonyos-daily-development-rules -->'
agents_pointer='Read `.ai-project/read-policy.json` before any project reads, then `.ai-project/lifecycle.json`, then `.codex-rules/harmonyos/WORKFLOW.md` and project facts. In migration-transition, HarmonyOS daily rules own ordinary bugs/features/release work; migration V2 is used only for parity, missing iOS behavior, or migration evidence. Choose one primary framework per task.'

if [[ ! -e "$agents_file" ]]; then
  printf '# Project instructions\n\n%s\n%s\n' "$agents_marker" "$agents_pointer" > "$agents_file"
  printf 'Created %s\n' "$agents_file"
elif ! grep -Fq "$agents_marker" "$agents_file"; then
  printf '\n%s\n%s\n' "$agents_marker" "$agents_pointer" >> "$agents_file"
  printf 'Added HarmonyOS daily-rules pointer to %s\n' "$agents_file"
else
  printf 'HarmonyOS daily-rules pointer already exists in %s\n' "$agents_file"
fi

record_args=(
  python3 "$suite_dir/installers/project_framework.py" record
  --project "$target_path" --suite "$suite_dir"
  --framework harmonyos-daily-development-rules --lifecycle "$lifecycle"
  --managed '.codex-rules/harmonyos/WORKFLOW.md=frameworks/harmonyos-daily-development-rules/templates/WORKFLOW.md'
  --managed '.codex-rules/harmonyos/references/verification.md=frameworks/harmonyos-daily-development-rules/references/verification.md'
  --managed '.codex-rules/harmonyos/references/code-quality-harmonyos.md=frameworks/harmonyos-daily-development-rules/references/code-quality-harmonyos.md'
  --managed '.codex-rules/_shared/task_router.py=shared/task-routing/task_router.py'
  --managed '.codex-rules/_shared/task_runtime.py=shared/task-routing/task_runtime.py'
  --managed '.codex-rules/_shared/read_boundary.py=shared/task-routing/read_boundary.py'
  --managed '.codex-rules/_shared/schemas/temporary-external-grant.schema.json=shared/schemas/temporary-external-grant.schema.json'
  --managed '.codex-rules/_shared/schemas/metadata-ack.schema.json=shared/schemas/metadata-ack.schema.json'
  --managed '.codex-rules/_shared/references/task-control.md=shared/context-engineering/task-control.md'
  --managed '.codex-rules/_shared/references/read-boundary.md=shared/context-engineering/read-boundary.md'
  --managed '.codex-rules/_shared/framework_router.py=shared/task-routing/framework_router.py'
  --managed '.codex-rules/_shared/references/context-policy.md=shared/context-engineering/context-policy.md'
  --managed '.codex-rules/_shared/references/lifecycle.md=shared/lifecycle/lifecycle.md'
  --managed '.codex-rules/_shared/references/verification-budget.md=shared/verification-budget/verification-budget.md'
  --managed '.codex-rules/_shared/references/code-quality-contract.md=shared/code-quality/code-quality-contract.md'
)
if [[ "$read_mode" == "complete" && -f "$target_path/.migration-v2/references/code-quality-contract.md" ]]; then
  record_args+=(--managed '.migration-v2/references/code-quality-contract.md=shared/code-quality/code-quality-contract.md')
fi
"${record_args[@]}" >/dev/null

printf '%s\n' 'Done. Review .codex-rules/harmonyos/PROJECT.md and fill in only facts that differ from the defaults.'

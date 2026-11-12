#!/usr/bin/env python3
"""Initialize or resume a compact, risk-routed migration control center."""

import argparse
import json
import sys
from pathlib import Path

from analyze_source import prepare_discovery
from classify_task import route_tools
from detect_gates import detect as detect_gates
from migration_state import ensure_state
from read_boundary import ReadBoundary, require_complete


def nested(a: Path, b: Path) -> bool:
    return a == b or a in b.parents or b in a.parents


def render(source: Path, destination: Path, values: dict[str, str]) -> None:
    text = source.read_text(encoding="utf-8")
    for key, value in values.items():
        text = text.replace("{{" + key + "}}", value)
    destination.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("target", type=Path, nargs="?")
    parser.add_argument("--name")
    parser.add_argument("--init-missing-read-policies", action="store_true", help="记录已获用户明确授权的缺失策略；不覆盖已有策略")
    parser.add_argument("--authorization-source", help="实际用户授权来源/原意，不含凭据；路径本身不是授权")
    args = parser.parse_args()
    framework = Path(__file__).resolve().parent.parent
    suite = framework.parents[1]
    sys.path.insert(0, str(suite / "installers"))
    from migration_read_policy import prepare
    resolved_source = args.source.expanduser().resolve()
    target = args.target if args.target else resolved_source.parent / f"{resolved_source.name}_harmonyos"
    try:
        source, target = prepare(args.source, target, args.init_missing_read_policies, args.authorization_source)
    except (OSError, ValueError) as error:
        print(json.dumps({"status": "blocked", "error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 2
    from project_framework import installation_locks, installation_states, audit_project, manifest_protocol, read_manifest, require_fresh_installation
    with installation_locks(target):
        installed = (target / ".ai-project/framework-installation.json").is_file()
        if installed:
            manifest = read_manifest(target)
            manifest_protocol(manifest)
            # 日常框架的完整清单不代表迁移规则完整；禁止借 resume 隐式切换框架或补写迁移控制。
            if manifest.get("framework") != "ios-to-harmony-migrator-v2":
                raise SystemExit("existing installation is not ios-to-harmony-migrator-v2; migration resume cannot switch frameworks")
            # 严格解析所有已知/旧记录后，仅无遗留执行的合法终态释放占用；不改写原状态和证据。
            states = installation_states(target, manifest.get("legacyArtifactReview"))
            if any(state["status"] not in {"closed", "cancelled"} for state in states):
                raise SystemExit("existing tasks must be explicitly ended before migration initialization/resume writes")
            if any(state.get("pendingExecutions") for state in states):
                raise SystemExit("pending task executions must be explicitly released before migration initialization/resume writes")
            audit = audit_project(target, suite)
            if audit["installedTaskProtocolVersion"] != "2" or not audit["safeToUpdate"] or audit["updateAvailable"]:
                raise SystemExit("existing migration rules require audit and explicit offline update; resume cannot install rules")
        else:
            require_fresh_installation(target)
        return initialize(source, target, framework, suite, args, installed)


def initialize(source: Path, target: Path, framework: Path, suite: Path, args, installed: bool) -> int:
    """在安装双锁内初始化控制数据；既有规则只读，首装按完整受管清单复制。"""
    control = target / ".migration-v2"
    workspace = target / ".migration-workspace-v2"
    ai_project = target / ".ai-project"
    project_file = control / "project.json"
    # 先核既有控制中心归属，拒绝其他源/目标时不能先改写发现索引与基线。
    boundary = ReadBoundary(target)
    existing = boundary.read_json(".migration-v2/project.json") if boundary.exists(".migration-v2/project.json") else None
    if installed and existing is None:
        raise SystemExit("existing migration control center is missing project.json; audit/recovery required before resume")
    if existing is not None:
        if Path(existing["sourceRoot"]).resolve() != source or Path(existing["targetRoot"]).resolve() != target:
            raise SystemExit("existing V2 control center belongs to different source or target")
    control.mkdir(exist_ok=True)
    ai_project.mkdir(exist_ok=True)
    for name in ("ir", "reports", "evidence", "cache", "state"):
        (workspace / name).mkdir(parents=True, exist_ok=True)
    discovery = prepare_discovery(source, workspace)
    quality_contract = control / "references/code-quality-contract.md"
    from project_framework import MANAGED_MAPPINGS, record_manifest
    mappings = MANAGED_MAPPINGS["ios-to-harmony-migrator-v2"]
    if not installed:
        for relative, source_relative in mappings:
            destination = target / relative
            if destination.exists():
                raise SystemExit("existing unmanaged migration rule needs adopt/audit before setup: " + relative)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text((suite / source_relative).read_text(encoding="utf-8"), encoding="utf-8")
    values = {
        "PROJECT_NAME": args.name or source.name,
        "SOURCE_ROOT": str(source),
        "TARGET_ROOT": str(target),
        "FRAMEWORK_ROOT": str(framework),
        "WORKSPACE_ROOT": str(workspace),
        "FRAMEWORK_VERSION": (suite / "VERSION").read_text(encoding="utf-8").strip(),
    }
    if existing is not None:
        existing["frameworkRoot"] = str(framework)
        existing["frameworkVersion"] = values["FRAMEWORK_VERSION"]
        existing["stateFile"] = str(workspace / "state/migration-state.json")
        project_file.write_text(json.dumps(existing, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        mode = "resume"
    else:
        render(framework / "assets/project.json", project_file, values)
        render(framework / "assets/project-agents.md", control / "AGENTS.md", values)
        render(framework / "assets/gate-config.json", control / "gate-config.json", values)
        render(framework / "assets/current.md", control / "CURRENT.md", values)
        mode = "new"
    gate_config_path = control / "gate-config.json"
    gate_config = json.loads(gate_config_path.read_text(encoding="utf-8")) if gate_config_path.is_file() else {}
    if not gate_config.get("gates"):
        gate_config_path.write_text(json.dumps(detect_gates(target, workspace), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    project_config = json.loads(project_file.read_text(encoding="utf-8"))
    lifecycle_file = ai_project / "lifecycle.json"
    lifecycle = json.loads(lifecycle_file.read_text(encoding="utf-8")) if lifecycle_file.exists() else {
        "schemaVersion": "1.0",
        "state": "migration-active",
        "defaultFramework": "ios-to-harmony-migrator-v2",
        "migrationFramework": "active",
        "routing": "one-primary-framework-per-task",
    }
    lifecycle["frameworkVersion"] = values["FRAMEWORK_VERSION"]
    lifecycle_file.write_text(json.dumps(lifecycle, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    ensure_state(workspace)
    context = {
        "schemaVersion": "2.1",
        "mode": mode,
        "stage": "pre",
        "intent": "migration",
        "impactLevel": "T3",
        "taskLevel": "T3",
        "reason": "Phase 0 source discovery and migration planning",
        "riskFlags": [],
        "confidence": "high",
        "modelClass": "strong",
        "mustRead": [
            str(framework / "references/core-contract.md"),
            str(framework / "references/migration-workflow.md"),
            str(framework / "references/source-analysis.md"),
            str(framework / "references/discovery-gate.md"),
            discovery["discoveryBaseline"],
            str(quality_contract),
            str(framework / "references/code-quality-migration.md"),
            str(control / "project.json"),
            str(control / "CURRENT.md"),
        ],
        "read": [
            str(framework / "references/core-contract.md"),
            str(framework / "references/migration-workflow.md"),
            str(framework / "references/source-analysis.md"),
            str(framework / "references/discovery-gate.md"),
            discovery["discoveryBaseline"],
            str(quality_contract),
            str(framework / "references/code-quality-migration.md"),
            str(control / "project.json"),
            str(control / "CURRENT.md"),
        ],
        "readIfNeeded": [discovery["sourceIndex"]],
        "doNotReadByDefault": ["historical reports", "raw logs", "unaffected modules"],
        "scope": {"taskFiles": [], "fileCount": 0, "moduleCount": 0},
        "contextBudget": {"maxPrimaryFiles": 28, "history": "summary-only", "rawLogs": "pointer-only"},
        "verificationPlan": "cross-module-contracts",
        "gateProfile": "T3",
        "toolRouting": route_tools("继续迁移页面", "T3", project_config),
    }
    (control / "task-context.json").write_text(
        json.dumps(context, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    pointer = target / "AGENTS.md"
    if not pointer.exists():
        pointer.write_text((control / "AGENTS.md").read_text(encoding="utf-8"), encoding="utf-8")
    if not installed:
        record_manifest(target, suite, "ios-to-harmony-migrator-v2", lifecycle.get("state", "migration-active"), mappings)
    print(json.dumps({"status": mode, "control": str(control), "workspace": str(workspace)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

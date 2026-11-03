#!/usr/bin/env python3
"""Build a V2 migration task context through the shared 2.1 router."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def load_shared() -> None:
    resolved = Path(__file__).resolve()
    for parent in resolved.parents:
        candidate = parent / "shared" / "task-routing"
        if (candidate / "task_router.py").is_file():
            sys.path.insert(0, str(candidate))
            return
    raise SystemExit("shared task router not found; install the complete AI development framework suite")


load_shared()
from read_boundary import require_complete
from task_runtime import atomic_json, task_dir, confirmed_identity
from task_router import build_context, snapshot, task_files  # noqa: E402


UI_WORDS = ("页面", "交互", "按钮", "布局", "颜色", "字体", "弹窗", "ui", "arkui", "view", "点击")
LANHU_EXPLICIT = ("lanhuapp.com", "蓝湖")
VISUAL_REFERENCE = ("设计稿", "视觉", "还原度", "切图", "标注", "颜色", "字体", "字号", "间距", "圆角", "图标", "布局", "样式")
PONYTAIL_EXPLICIT = ("ponytail", "过度设计", "复杂度审查", "简化审查", "删除多余", "删掉多余")


def route_tools(request: str, level: str, config: dict) -> dict:
    value = request.lower()
    external = config.get("externalTools", {})
    lanhu_config = external.get("lanhu", {})
    ponytail_config = external.get("ponytailReview", {})
    explicit_lanhu = any(word in value for word in LANHU_EXPLICIT)
    configured_lanhu = bool(lanhu_config.get("projectUrl"))
    visual_gap = any(word in value for word in VISUAL_REFERENCE)
    migration_page = "迁移" in value and any(word in value for word in ("页面", "组件", "功能", "page", "component", "feature"))

    if lanhu_config.get("mode", "conditional") == "disabled":
        lanhu_mode, lanhu_reason = "off", "project policy disables Lanhu"
    elif (explicit_lanhu or configured_lanhu) and (explicit_lanhu or visual_gap or migration_page):
        lanhu_mode = {"T0": "scoped", "T1": "single-region", "T2": "feature", "T3": "slice", "T4": "release-gap"}[level]
        lanhu_reason = "configured or explicit design source is relevant"
    else:
        lanhu_mode, lanhu_reason = "off", "no relevant configured or explicit Lanhu design source"

    explicit_ponytail = any(word in value for word in PONYTAIL_EXPLICIT)
    if ponytail_config.get("mode", "conditional") == "disabled" or level == "T0":
        ponytail_mode, ponytail_reason = "off", "disabled or no source-code diff expected"
    elif explicit_ponytail:
        ponytail_mode, ponytail_reason = "post-diff-once", "user explicitly requested complexity review"
    elif level in {"T2", "T3"}:
        ponytail_mode, ponytail_reason = "post-diff-conditional", "run once only when final Diff has complexity signals"
    else:
        ponytail_mode, ponytail_reason = "off", "small or high-risk task does not auto-run complexity review"

    return {
        "lanhu": {"mode": lanhu_mode, "reason": lanhu_reason, "readOnly": True, "authority": lanhu_config.get("authority", "supplemental")},
        "ponytailReview": {"mode": ponytail_mode, "reason": ponytail_reason, "maxRuns": int(ponytail_config.get("maxRunsPerTask", 1)), "afterVerification": True},
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--request", required=True)
    parser.add_argument("--stage", choices=("pre", "post", "final"), default="pre")
    parser.add_argument("--file", action="append", default=[])
    parser.add_argument("--task-id")
    parser.add_argument("--write-context", action="store_true")
    parser.add_argument("--write-baseline", action="store_true")
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    project = args.project.expanduser().resolve()
    boundary = require_complete(project)
    control = project / ".migration-v2"
    config = boundary.read_json(".migration-v2/project.json")
    framework = Path(config["frameworkRoot"])
    if args.write_context and not args.task_id:
        raise ValueError("写任务上下文必须指定 --task-id 并先 task_runtime init")
    directory = task_dir(project, args.task_id) if args.task_id else None
    if directory:
        confirmed_identity(project)
    baseline_path = directory / "baseline.json" if directory else control / "task-baseline.json"
    baseline = boundary.read_json(baseline_path.relative_to(project).as_posix()) if boundary.exists(baseline_path.relative_to(project).as_posix()) else None
    if directory and baseline:
        baseline = {"projectRoot": str(project), "paths": baseline["paths"]}
    files, excluded = task_files(project, baseline if args.stage != "pre" else None, args.file)
    context = build_context(args.request, project, "migration", args.stage, files, excluded)
    level = context["impactLevel"]

    refs = [framework / "references/core-contract.md"]
    if level in {"T2", "T3", "T4"}:
        quality_contract = control / "references/code-quality-contract.md"
        if not quality_contract.is_file():
            quality_contract = framework.parents[1] / "shared/code-quality/code-quality-contract.md"
        refs.extend([quality_contract, framework / "references/code-quality-migration.md"])
    if level in {"T3", "T4"}:
        refs.extend([
            framework / "references/migration-workflow.md",
            framework / "references/source-analysis.md",
            framework / "references/discovery-gate.md",
        ])
        baseline = Path(config["workspaceRoot"]) / "ir/discovery-baseline.json"
        if baseline.is_file():
            refs.append(baseline)
    if context.get("taskShape") == "parity-batch":
        refs.append(framework / "references/parity-residuals.md")
    if level in {"T2", "T3", "T4"} or any(word in args.request.lower() for word in ("arkts", "编译", "类型", "装饰器")):
        refs.append(framework / "references/arkts-implementation.md")
    if level in {"T2", "T3", "T4"} and any(word in args.request.lower() for word in UI_WORDS):
        refs.append(framework / "references/ui-quality.md")
    if level == "T4":
        refs.extend([framework / "references/verification.md", framework / "references/high-risk-and-release.md"])

    model_class = {"T0": "cost_efficient", "T1": "balanced", "T2": "balanced", "T3": "strong", "T4": "highest"}[level]
    required = [str(path) for path in refs] + [str(control / "project.json"), str(control / "CURRENT.md")]
    context.update({
        "taskLevel": level,
        "modelClass": model_class,
        "recommendedModel": config.get("modelPolicy", {}).get(model_class) if config.get("modelPolicy", {}).get(model_class) in config.get('availableModels', []) else None,
        "modelSelectionApplied": False,
        "modelAvailabilitySource": 'project-declared host capabilities; not auto-detected or applied',
        "changedFiles": files,
        "mustRead": required + context["mustRead"],
        "read": required + context["mustRead"],
        "verificationMode": {"T0": "diff-only", "T1": "user-runtime-acceptance", "T2": "risk-based-targeted", "T3": "cross-module", "T4": "full-risk"}[level],
        "runGatesByDefault": level in {"T2", "T3", "T4"},
        "gateProfile": level if level in {"T2", "T3", "T4"} else None,
        "toolRouting": route_tools(args.request, level, config),
    })
    context["taskId"] = args.task_id
    if args.write_baseline:
        raise ValueError("基线由 task_runtime init 创建，恢复不能重写")
    if args.write_context and not args.dry_run:
        atomic_json(directory / "context.json", context)
    print(json.dumps(context, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

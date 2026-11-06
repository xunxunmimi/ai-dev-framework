#!/usr/bin/env python3
"""Advance migration state only when deterministic preconditions are satisfied."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

# 完整套件共用身份与证据契约；不创建全局模块或修改系统配置。
import sys
for parent in Path(__file__).resolve().parents:
    shared = parent / "shared/task-routing"
    if (shared / "task_runtime.py").is_file():
        sys.path.insert(0, str(shared))
        break
from read_boundary import require_complete
from task_runtime import atomic_json, binding, digest as content_digest, validate_report, lock, protocol_gate, project_lock


from check_discovery_gate import validate_workspace
from plan_approval import approval_status, digest, discovery_binding


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def framework_version() -> str:
    return (Path(__file__).resolve().parents[3] / "VERSION").read_text(encoding="utf-8").strip()


def state_path(workspace: Path) -> Path:
    return workspace / "state/migration-state.json"


def initial_state() -> dict:
    return {
        "schemaVersion": "1.0",
        "frameworkVersion": framework_version(),
        "phase": "discovery",
        "status": "active",
        "activeSlice": None,
        "completedSlices": [],
        "blockers": [],
        "evidence": {},
        "updatedAt": timestamp(),
    }


def ensure_state(workspace: Path) -> dict:
    require_complete(workspace.parent)
    path = state_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        data = json.loads(path.read_text(encoding="utf-8"))
        data["frameworkVersion"] = framework_version()
        return data
    data = initial_state()
    write_state(workspace, data)
    append_event(workspace, "initialized", None, data, "framework", "")
    return data


def write_state(workspace: Path, state: dict) -> None:
    state["updatedAt"] = timestamp()
    path = state_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_json(path, state)


def append_event(workspace: Path, event: str, before: dict | None, after: dict, actor: str, reason: str) -> None:
    path = workspace / "state/events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "schemaVersion": "1.0",
        "at": timestamp(),
        "event": event,
        "actor": actor,
        "reason": reason,
        "before": None if before is None else {"phase": before["phase"], "status": before["status"], "activeSlice": before.get("activeSlice")},
        "after": {"phase": after["phase"], "status": after["status"], "activeSlice": after.get("activeSlice")},
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def load_plan(workspace: Path) -> dict:
    _, plan = digest(workspace)
    return plan


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def passed_report(workspace: Path, path: Path | None, label: str, profile: str, slice_id: str | None = None, task_id: str | None = None) -> dict:
    """核对当前任务、切片、实际代码、完整 Profile、配置与计划；只看 passed 字符串不够。"""
    boundary = require_complete(workspace.parent)
    require(path is not None and boundary.exists(path.relative_to(workspace.parent).as_posix()), f"{label} evidence JSON is required")
    data = boundary.read_json(path.relative_to(workspace.parent).as_posix())
    require(bool(task_id), "缺少当前任务 ID，不能接受 passed")
    validate_report(workspace.parent, data, task_id, profile, slice_id)
    config_path = workspace.parent / ".migration-v2/gate-config.json"
    require(data.get("configPath") == str(config_path.resolve()), "报告来自不同 Gate 配置")
    require(data.get("configSha256") == content_digest(boundary.read_json(".migration-v2/gate-config.json")), "Gate 配置已变化")
    require(data.get("planSha256") == digest(workspace)[0], "迁移计划已变化，报告失效")
    require(data.get("coverageComplete") is True, "定向 Gate 子集不能替代完整切片/发布 Profile")
    return data


def advance(workspace: Path, event: str, slice_id: str | None, evidence: Path | None, reason: str, task_id: str | None = None, dry_run: bool = False) -> dict:
    """在前置证据成立后推进状态；失败不写状态，调用方持有迁移工作区锁。"""
    require_complete(workspace.parent)
    boundary = require_complete(workspace.parent)
    protocol_gate(workspace.parent)
    require(boundary.exists(state_path(workspace).relative_to(workspace.parent).as_posix()), 'migration state missing; initialize explicitly first')
    current = boundary.read_json(state_path(workspace).relative_to(workspace.parent).as_posix())
    before = json.loads(json.dumps(current))
    phase, status = current["phase"], current["status"]
    if event == "discovery_ready":
        require((phase, status) == ("discovery", "active"), "discovery_ready requires discovery/active")
        report = validate_workspace(workspace)
        require(report.get("status") == "passed", "Phase 0 discovery gate is not passed")
        current.update(phase="planning", status="active")
        current["evidence"]["discoveryGate"] = {key: report.get(key) for key in ("baselineSha256", "sourceIndexSha256")}
    elif event == "plan_ready":
        require((phase, status) == ("planning", "active"), "plan_ready requires planning/active")
        plan_sha, plan = digest(workspace)
        discovery = validate_workspace(workspace)
        require(discovery.get("status") == "passed" and not discovery_binding(plan, discovery), "plan is not bound to current discovery")
        current.update(status="waiting-human")
        current["evidence"]["planSha256"] = plan_sha
    elif event == "plan_approved":
        require((phase, status) == ("planning", "waiting-human"), "plan_approved requires planning/waiting-human")
        valid, details = approval_status(workspace)
        require(valid, details)
        current.update(phase="implementation", status="active")
    elif event == 'slice_reopened':
        valid, details = approval_status(workspace)
        require(valid, details)
        require(phase in {'implementation', 'transition', 'release'} and status in {'active', 'complete'}, 'only a verified flow in an active/completed migration can reopen')
        require(current.get('activeSlice') is None, 'another slice is already active')
        require(slice_id in current['completedSlices'], 'only a completed slice can reopen')
        require(bool(task_id) and bool(reason.strip()), 'reopen requires task ID and contradictory feedback/reason')
        binding(workspace.parent, task_id, 'T3', slice_id)
        current.setdefault('reopenedSlices', []).append({'sliceId': slice_id, 'taskId': task_id, 'reason': reason, 'at': timestamp(), 'previousEvidence': current['evidence'].get('sliceReports', {}).get(slice_id), 'previousPhase': phase})
        current['completedSlices'].remove(slice_id)
        stale = current['evidence'].setdefault('invalidated', [])
        for key in ('releaseCandidateReport', 'releaseReport'):
            if key in current['evidence']:
                stale.append({'kind': key, 'path': current['evidence'].pop(key), 'reason': 'affected slice reopened'})
        previous = current['evidence'].get('sliceReports', {}).pop(slice_id, None)
        if previous:
            stale.append({'kind': 'sliceReport', 'path': previous, 'reason': reason})
        current.update(phase='implementation', status='active', activeSlice=slice_id, activeTaskId=task_id)
    elif event == "slice_started":
        valid, details = approval_status(workspace)
        require(valid, details)
        require(bool(task_id), "slice_started 必须提供 --task-id")
        binding(workspace.parent, task_id, "T3", slice_id)
        current["activeTaskId"] = task_id
        require((phase, status) == ("implementation", "active"), "slice_started requires implementation/active")
        require(current.get("activeSlice") is None, "another slice is already active")
        require(bool(slice_id), "--slice is required")
        planned = {str(item.get("id")) for item in load_plan(workspace).get("slices", [])}
        require(slice_id in planned, "slice is not present in the approved migration plan")
        require(slice_id not in current["completedSlices"], "slice is already completed")
        current["activeSlice"] = slice_id
    elif event == "slice_verified":
        require((phase, status) == ("implementation", "active"), "slice_verified requires implementation/active")
        require(bool(slice_id) and current.get("activeSlice") == slice_id, "--slice must match activeSlice")
        valid, details = approval_status(workspace)
        require(valid, details)
        profile = next(item.get("gateProfile", "T3") for item in load_plan(workspace).get("slices", []) if item.get("id") == slice_id)
        report = passed_report(workspace, evidence, "slice gate", profile, slice_id, current.get("activeTaskId"))
        current["completedSlices"] = sorted(set(current["completedSlices"] + [slice_id]))
        current["evidence"].setdefault("sliceReports", {})[slice_id] = str(evidence.resolve())
        current["activeSlice"] = None
        current["blockers"] = [item for item in current["blockers"] if item.get("slice") != slice_id]
    elif event == "slice_blocked":
        require((phase, status) == ("implementation", "active"), "slice_blocked requires implementation/active")
        require(current.get("activeSlice") is not None, "no active slice to block")
        require(bool(reason.strip()), "--reason is required")
        current["status"] = "blocked"
        current["blockers"].append({"slice": current["activeSlice"], "reason": reason, "at": timestamp()})
    elif event == "slice_resumed":
        require((phase, status) == ("implementation", "blocked"), "slice_resumed requires implementation/blocked")
        require(bool(reason.strip()), "--reason must describe what changed")
        current["status"] = "active"
    elif event == "migration_transition":
        require((phase, status) == ("implementation", "active"), "migration_transition requires implementation/active")
        require(current.get("activeSlice") is None, "active slice must be closed")
        planned = {str(item.get("id")) for item in load_plan(workspace).get("slices", [])}
        require(planned <= set(current["completedSlices"]), "not every planned slice is verified")
        current.update(phase="transition", status="active")
    elif event == "release_candidate":
        require((phase, status) == ("transition", "active"), "release_candidate requires transition/active")
        report = passed_report(workspace, evidence, "T4 release gate", "T4", task_id=task_id)
        require(report.get('verificationPurpose') == 'release' and report.get('releaseContractPassed') is True, 'release requires final Release/artifact/device contract, not a targeted T4 or Debug pass')
        current.update(phase="release", status="active")
        current["evidence"]["releaseCandidateReport"] = str(evidence.resolve())
    elif event == "released":
        require((phase, status) == ("release", "active"), "released requires release/active")
        release_report = passed_report(workspace, evidence, "release", "T4", task_id=task_id)
        require(release_report.get('verificationPurpose') == 'release' and release_report.get('releaseContractPassed') is True, 'release evidence contract missing')
        require(any(item.get("id") == "release-manual-evidence" and item.get("status") == "passed" for item in release_report.get("gates", [])), "released 还需要绑定的人工发布证据，编译通过不能代表已发布")
        current["status"] = "complete"
        current["evidence"]["releaseReport"] = str(evidence.resolve())
    else:
        raise ValueError(f"unsupported event: {event}")
    current["frameworkVersion"] = framework_version()
    if dry_run:
        return {'status': 'dry-run', 'event': event, 'before': before, 'after': current, 'writes': []}
    write_state(workspace, current)
    append_event(workspace, event, before, current, "user-or-orchestrator", reason)
    sync_compatibility_files(workspace, current)
    return current


def sync_compatibility_files(workspace: Path, state: dict) -> None:
    target = workspace.parent
    project_path = target / ".migration-v2/project.json"
    if project_path.is_file():
        project = json.loads(project_path.read_text(encoding="utf-8"))
        project["phase"] = state["phase"]
        project["frameworkVersion"] = framework_version()
        project["stateFile"] = str(state_path(workspace))
        atomic_json(project_path, project)
    lifecycle_path = target / ".ai-project/lifecycle.json"
    if lifecycle_path.is_file() and (state["phase"] in {"transition", "release"} or state.get('reopenedSlices')):
        lifecycle = json.loads(lifecycle_path.read_text(encoding="utf-8"))
        lifecycle["frameworkVersion"] = framework_version()
        lifecycle["state"] = "harmony-maintenance" if state["status"] == "complete" else "migration-transition"
        lifecycle["defaultFramework"] = "harmonyos-daily-development-rules"
        lifecycle["migrationFramework"] = "archived-read-only" if state["status"] == "complete" else "on-demand-parity-only"
        atomic_json(lifecycle_path, lifecycle)
    current_path = target / ".migration-v2/CURRENT.md"
    current_path.write_text(
        "# 当前状态\n\n"
        f"- phase: {state['phase']}\n"
        f"- status: {state['status']}\n"
        f"- activeSlice: {state.get('activeSlice') or 'none'}\n"
        f"- completedSlices: {len(state['completedSlices'])}\n"
        f"- blockers: {len(state['blockers'])}\n"
        f"- updatedAt: {state['updatedAt']}\n\n"
        "详细事件见 `.migration-workspace-v2/state/events.jsonl`。\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    show = sub.add_parser("show")
    show.add_argument("workspace", type=Path)
    transition = sub.add_parser("transition")
    transition.add_argument("workspace", type=Path)
    transition.add_argument("event", choices=("discovery_ready", "plan_ready", "plan_approved", "slice_started", "slice_reopened", "slice_verified", "slice_blocked", "slice_resumed", "migration_transition", "release_candidate", "released"))
    transition.add_argument("--slice")
    transition.add_argument("--task-id")
    transition.add_argument("--evidence", type=Path)
    transition.add_argument("--reason", default="")
    transition.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    workspace = args.workspace.expanduser().resolve()
    try:
        boundary = require_complete(workspace.parent)
        if args.command == "show":
            print(json.dumps(boundary.read_json(state_path(workspace).relative_to(workspace.parent).as_posix()) if boundary.exists(state_path(workspace).relative_to(workspace.parent).as_posix()) else initial_state(), ensure_ascii=False, indent=2))
            return 0
        from contextlib import nullcontext
        with project_lock(workspace.parent, '.ai-project/tasks.lock') if not args.dry_run else nullcontext(), nullcontext() if args.dry_run else lock(workspace / "state/transition.lock"):
            state = advance(
            workspace,
            args.event,
            args.slice,
            args.evidence.expanduser().resolve() if args.evidence else None,
            args.reason,
            args.task_id,
            args.dry_run,
        )
    except (ValueError, OSError, json.JSONDecodeError, SystemExit) as error:
        print(json.dumps({"status": "blocked", "error": str(error)}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(state, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

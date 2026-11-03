#!/usr/bin/env python3
"""Behavior tests for task isolation and false-positive prevention."""

import json
import subprocess
import tempfile
from pathlib import Path

from task_router import build_context, classify, snapshot, task_files


def git(project: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(project), *args], check=True, capture_output=True, text=True)


def main() -> int:
    assert classify("修复支付页面发布按钮颜色", [], "daily")["impactLevel"] == "D1"
    assert classify("准备上线并验证支付回调与验签", [], "daily")["impactLevel"] == "D4"
    assert classify("迁移设置页面", [], "migration")["impactLevel"] == "T3"
    parity_batch = classify("修复音频、PDF和图片流程共七项 iOS 一致性问题", [], "migration")
    assert parity_batch["impactLevel"] == "T3"
    assert parity_batch["taskShape"] == "parity-batch"
    assert parity_batch["executionStrategy"] == "joint-diagnosis-then-slices"
    assert classify("修复七处鸿蒙文案和间距，不涉及 iOS 一致性", [], "daily")["impactLevel"] == "D1"
    assert classify("新增设置页面", [], "daily")["impactLevel"] == "D2"
    control = classify("更新接入开发规则", ["AGENTS.md", ".codex-rules/_shared/task_router.py", ".migration-v2/project.json"], "daily", "final")
    assert control["impactLevel"] == "D3"
    assert control["scope"]["productFileCount"] == 0

    with tempfile.TemporaryDirectory() as folder:
        project = Path(folder)
        git(project, "init", "-q")
        git(project, "config", "user.email", "self-test@example.invalid")
        git(project, "config", "user.name", "Self Test")
        (project / "old.txt").write_text("base\n", encoding="utf-8")
        (project / "task.txt").write_text("base\n", encoding="utf-8")
        (project / ".ai-project").mkdir(exist_ok=True)
        (project / ".ai-project/read-policy.json").write_text(json.dumps({"schemaVersion":"1.0","confirmed":True,"projectRoot":str(project.resolve()),"mode":"complete","allow":["**"],"deny":[],"scopeDescription":"合成测试目录明确授权；不涉及真实业务"}))
        git(project, "add", ".")
        git(project, "commit", "-qm", "baseline")
        (project / "old.txt").write_text("pre-existing\n", encoding="utf-8")
        baseline = snapshot(project)
        (project / "task.txt").write_text("current task\n", encoding="utf-8")
        selected, excluded = task_files(project, baseline, [])
        assert selected == ["task.txt"], selected
        assert excluded == ["old.txt"], excluded
        context = build_context("修复问题", project, "daily", "final", selected, [f"old-{index}.txt" for index in range(25)])
        assert context["excludedPreexistingChangeCount"] == 25
        assert len(context["excludedPreexistingChanges"]) == 21

    print("Shared task router self-test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

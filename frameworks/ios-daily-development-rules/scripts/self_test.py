#!/usr/bin/env python3
from pathlib import Path
import json
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
required = [
    ("README.md", "iOS 日常开发规则"),
    ("setup-project.sh", "ios-daily-development-rules"),
    ("setup-project.sh", "lifecycle.json"),
    ("SKILL.md", "ios-maintenance"),
    ("agents/openai.yaml", "$ios-daily-development-rules"),
    ("templates/WORKFLOW.md", "D0/D1 快速通道"),
    ("templates/WORKFLOW.md", "代码质量与注释预算"),
    ("templates/PROJECT.md", "ios-daily-rules:external-tools"),
    ("references/code-quality-ios.md", "ios-daily-rules:managed-code-quality"),
    ("references/verification.md", "D0/D1 不创建独立验收报告"),
    ("external-tools.lock.json", "ponytail-review"),
    ("setup-project.sh", "code-quality-contract.md"),
    ("setup-project.sh", "project_framework.py"),
    ("SKILL.md", "detected-capabilities.json"),
]
errors = []
for relative, marker in required:
    path = root / relative
    if not path.is_file():
        errors.append(f"missing: {relative}")
        continue
    if marker not in path.read_text(encoding="utf-8"):
        errors.append(f"missing marker in {relative}: {marker}")

if errors:
    print("\n".join(errors), file=sys.stderr)
    raise SystemExit(1)

with tempfile.TemporaryDirectory() as folder:
    project = Path(folder) / "Demo"
    project.mkdir()
    (project / ".ai-project").mkdir(exist_ok=True)
    (project / ".ai-project/read-policy.json").write_text(json.dumps({"schemaVersion":"1.0","confirmed":True,"projectRoot":str(project.resolve()),"mode":"complete","allow":["**"],"deny":[],"scopeDescription":"合成测试目录明确授权；不涉及真实业务"}))
    (project / "Demo.xcodeproj").mkdir()
    result = subprocess.run(
        [str(root / "setup-project.sh"), str(project), "ios-maintenance"],
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        print(result.stdout + result.stderr, file=sys.stderr)
        raise SystemExit(result.returncode)
    assert (project / ".codex-rules/_shared/references/code-quality-contract.md").is_file()
    assert (project / ".codex-rules/ios/references/code-quality-ios.md").is_file()
    assert (project / ".codex-rules/_shared/framework_router.py").is_file()
    assert (project / ".ai-project/detected-capabilities.json").is_file()
    assert (project / ".ai-project/framework-installation.json").is_file()
    suite = root.parents[1]
    audit = subprocess.run(
        [sys.executable, str(suite / "installers/project_framework.py"), "audit", str(project), "--suite", str(suite)],
        text=True,
        capture_output=True,
    )
    assert audit.returncode == 0, audit.stderr
    assert json.loads(audit.stdout)["safeToUpdate"] is True
    workflow = project / ".codex-rules/ios/WORKFLOW.md"
    workflow.write_text(workflow.read_text(encoding="utf-8") + "\nlocal edit\n", encoding="utf-8")
    drift = subprocess.run(
        [sys.executable, str(suite / "installers/project_framework.py"), "audit", str(project), "--suite", str(suite)],
        text=True,
        capture_output=True,
    )
    assert drift.returncode == 2
    assert json.loads(drift.stdout)["safeToUpdate"] is False
print(f"OK: iOS daily rules ({len(required)} checks passed)")

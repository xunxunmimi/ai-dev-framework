import json
#!/usr/bin/env python3
from pathlib import Path
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
required = [
    ("README.md", "HarmonyOS 日常开发规则"),
    ("setup-project.sh", "harmonyos-daily-development-rules"),
    ("setup-project.sh", "migration-transition"),
    ("SKILL.md", "migration-transition"),
    ("agents/openai.yaml", "$harmonyos-daily-development-rules"),
    ("templates/WORKFLOW.md", "D0/D1 快速通道"),
    ("templates/WORKFLOW.md", "代码质量与注释预算"),
    ("templates/PROJECT.md", "harmonyos-daily-rules:external-tools"),
    ("references/code-quality-harmonyos.md", "harmonyos-daily-rules:managed-code-quality"),
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
    (project / "entry").mkdir()
    (project / "entry/hvigorfile.ts").touch()
    result = subprocess.run(
        [str(root / "setup-project.sh"), str(project), "harmony-maintenance"],
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        print(result.stdout + result.stderr, file=sys.stderr)
        raise SystemExit(result.returncode)
    assert (project / ".codex-rules/_shared/references/code-quality-contract.md").is_file()
    assert (project / ".codex-rules/harmonyos/references/code-quality-harmonyos.md").is_file()
    assert (project / ".codex-rules/_shared/framework_router.py").is_file()
    assert (project / ".ai-project/detected-capabilities.json").is_file()
    assert (project / ".ai-project/framework-installation.json").is_file()
print(f"OK: HarmonyOS daily rules ({len(required)} checks passed)")

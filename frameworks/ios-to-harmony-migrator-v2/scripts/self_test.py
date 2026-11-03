#!/usr/bin/env python3
"""Exercise initialization, task routing, gate selection/cache, and UI scoring."""

import hashlib
import base64
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from check_discovery_gate import AREA_DETAIL_REQUIREMENTS

# 回归只操作临时元数据夹具；用真实绑定报告替代旧版裸 passed，避免测试绕过门禁。
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "shared/task-routing"))
from task_runtime import atomic_json, observe_identity, begin, checkpoint
from ui_evidence import load_context, digest as ui_digest



def run(*args: str, expected: int = 0) -> subprocess.CompletedProcess:
    result = subprocess.run(args, text=True, capture_output=True)
    if result.returncode != expected:
        raise AssertionError(f"{args} -> {result.returncode}\n{result.stdout}\n{result.stderr}")
    return result


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def complete_discovery(workspace: Path) -> tuple[str, str]:
    baseline_path = workspace / "ir/discovery-baseline.json"
    index_path = workspace / "ir/source-index.json"
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    index_digest = sha256(index_path)
    baseline["status"] = "ready-for-plan"
    baseline["source"]["analysedSourceIndexSha256"] = index_digest
    baseline["analysisCoverage"]["sourceFilesAccountedFor"] = baseline["analysisCoverage"]["sourceFilesIndexed"]
    for area_name, area in baseline["areas"].items():
        area.update({
            "status": "complete",
            "summary": f"Test fixture analysis for {area_name}",
            "evidenceRefs": ["ViewController.m"],
            "coverage": {"identified": 1, "mapped": 1, "excluded": 0, "unknown": 0},
            "items": [{
                "id": f"{area_name}-1",
                "name": "fixture",
                "disposition": "mapped",
                "sourceRefs": ["ViewController.m"],
                "targetStrategy": "preserve fixture behavior",
                "details": {field: "fixture" for field in AREA_DETAIL_REQUIREMENTS[area_name]},
            }],
        })
    baseline_path.write_text(json.dumps(baseline, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return sha256(baseline_path), index_digest


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    with tempfile.TemporaryDirectory() as folder:
        base = Path(folder)
        source, target = base / "ios", base / "harmony"
        source.mkdir()
        (source / ".ai-project").mkdir()
        (source / ".ai-project/read-policy.json").write_text(json.dumps({"schemaVersion":"1.0","confirmed":True,"projectRoot":str(source.resolve()),"mode":"complete","allow":["**"],"deny":[],"scopeDescription":"合成迁移源目录完整读取授权"}))
        target.mkdir()
        (target / ".ai-project").mkdir(exist_ok=True)
        (target / ".ai-project/read-policy.json").write_text(json.dumps({"schemaVersion":"1.0","confirmed":True,"projectRoot":str(target.resolve()),"mode":"complete","allow":["**"],"deny":[],"scopeDescription":"合成测试目录明确授权；不涉及真实业务"}))
        (source / "ViewController.m").write_text("@implementation ViewController\n@end\n", encoding="utf-8")
        run(sys.executable, str(root / "scripts/start_migration.py"), str(source), str(target))
        project = json.loads((target / ".migration-v2/project.json").read_text(encoding="utf-8"))
        assert Path(project["sourceRoot"]).resolve() == source.resolve()
        initial_context = json.loads((target / ".migration-v2/task-context.json").read_text(encoding="utf-8"))
        assert initial_context["toolRouting"]["lanhu"]["mode"] == "off"
        assert initial_context["toolRouting"]["ponytailReview"]["mode"] == "post-diff-conditional"
        assert initial_context["schemaVersion"] == "2.1"
        workspace = target / ".migration-workspace-v2"
        assert (target / ".migration-v2/references/code-quality-contract.md").is_file()
        assert (workspace / "ir/source-index.json").is_file()
        assert (workspace / "ir/discovery-baseline.json").is_file()
        assert any(path.endswith("discovery-gate.md") for path in initial_context["mustRead"])
        assert any(path.endswith("discovery-baseline.json") for path in initial_context["mustRead"])
        assert any(path.endswith("source-index.json") for path in initial_context["readIfNeeded"])
        assert any(path.endswith("code-quality-contract.md") for path in initial_context["mustRead"])
        assert any(path.endswith("code-quality-migration.md") for path in initial_context["mustRead"])
        assert (target / ".ai-project/lifecycle.json").is_file()
        assert (target / ".ai-project/framework-installation.json").is_file()
        assert (workspace / "state/migration-state.json").is_file()
        detected_gates = json.loads((target / ".migration-v2/gate-config.json").read_text(encoding="utf-8"))
        assert detected_gates["gates"][0]["id"] == "release-manual-evidence"
        assert detected_gates["gates"][0]["confirmed"] is False
        plan = workspace / "ir/migration-plan.json"
        incomplete_baseline_digest = sha256(workspace / "ir/discovery-baseline.json")
        index_digest = sha256(workspace / "ir/source-index.json")
        plan_data = {
            "schemaVersion": "2.0",
            "source": {"root": str(source), "revision": "fixture"},
            "target": {"root": str(target), "minimumApi": "fixture"},
            "discovery": {
                "baseline": "ir/discovery-baseline.json",
                "baselineSha256": incomplete_baseline_digest,
                "sourceIndexSha256": index_digest,
            },
            "scope": ["fixture"],
            "slices": [{
                "id": "fixture",
                "sourceRefs": ["ViewController.m"],
                "targetOutcome": "fixture",
                "dependencies": [],
                "taskLevel": "T3",
                "acceptance": ["fixture"],
            }],
            "risks": [],
            "acceptance": ["fixture"],
        }
        ui_expected = {'taskId':'fixture-task','screens':[{'sliceId':'fixture','screenId':'home','states':[{'id':'default'}]}]}
        plan_data['uiCoverageSha256'] = ui_digest(ui_expected)
        plan.write_text(json.dumps(plan_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        run(sys.executable, str(root / "scripts/check_discovery_gate.py"), str(workspace), expected=1)
        run(sys.executable, str(root / "scripts/plan_approval.py"), "approve", str(workspace), "--approved-by", "test-user", expected=1)
        baseline_digest, index_digest = complete_discovery(workspace)
        run(sys.executable, str(root / "scripts/check_discovery_gate.py"), str(workspace))
        plan_data["discovery"]["baselineSha256"] = baseline_digest
        plan_data["discovery"]["sourceIndexSha256"] = index_digest
        plan.write_text(json.dumps(plan_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        run(sys.executable, str(root / "scripts/plan_approval.py"), "approve", str(workspace), "--approved-by", "test-user")
        run(sys.executable, str(root / "scripts/plan_approval.py"), "check", str(workspace))
        state_script = str(root / "scripts/migration_state.py")
        run(sys.executable, state_script, "transition", str(workspace), "discovery_ready")
        run(sys.executable, state_script, "transition", str(workspace), "plan_ready")
        run(sys.executable, state_script, "transition", str(workspace), "plan_approved")
        (target / "AppScope").mkdir(exist_ok=True)
        (target / "AppScope/app.json5").write_text('{"bundleName":"com.example.fixture"}')
        (target / "build-profile.json5").write_text('{"products":[{"name":"default"}]}')
        (target / "entry/src/main/ets/pages").mkdir(parents=True, exist_ok=True)
        config_path = target / ".migration-v2/gate-config.json"
        config_path.write_text(json.dumps({"targetRoot":str(target), "evidenceRoot":str(workspace / "evidence/gates"), "profiles":{"T3":["fixture"]}, "gates":[{"id":"fixture","confirmed":True,"argv":[sys.executable,"-c","print('fixture checked')"],"cwd":str(target),"cacheable":False}]}))
        atomic_json(target / ".ai-project/identity.json", observe_identity(target, "harmonyos", {"module":"entry", "buildMode":"debug"}))
        begin(target, "fixture-task", ["entry/src/main/ets/pages/Index.ets"], "迁移夹具切片")
        run(sys.executable, state_script, "transition", str(workspace), "slice_started", "--slice", "fixture", "--task-id", "fixture-task")
        expected_path = workspace / 'ir/ui-expected.json'
        atomic_json(expected_path, ui_expected)
        actual_target = target.resolve()
        ui_binding, _ = load_context(actual_target, 'fixture-task', 'T3', 'fixture', actual_target / '.migration-workspace-v2/ir/ui-expected.json')
        png = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a7r0AAAAASUVORK5CYII=')
        png_path = workspace / 'evidence/synthetic.png';png_path.write_bytes(png)
        ui_record = {'path':'.migration-workspace-v2/evidence/synthetic.png','sha256':sha256(png_path),'mediaType':'image/png','binding':ui_binding,'capturedAt':'2026-10-23T12:00:00+00:00','origin':'runtime-capture','captureCommand':['synthetic-contract-test-only'],'artifactSha256':'synthetic','device':'fixture-only','stateId':'default'}
        strict_ui = workspace / 'ir/strict-ui.json'
        atomic_json(strict_ui, {'schemaVersion':'2.0','binding':ui_binding,'coverageSha256':ui_digest(ui_expected),'screens':[{'sliceId':'fixture','screenId':'home','states':[{'id':'default','status':'verified','sourceEvidence':[{**ui_record,'side':'source'}],'targetEvidence':[{**ui_record,'side':'target'}]}]}],'review':{'reviewedBy':'synthetic fixture; not actual visual review','reviewedAt':'2026-10-23T12:01:00+00:00','decision':'passed'}})
        run(sys.executable, str(root/'scripts/ui_state_manifest.py'), 'check', str(strict_ui), '--project', str(target), '--task-id', 'fixture-task', '--slice', 'fixture', '--expected', str(expected_path))
        slice_report = workspace / "reports/slice-report.json"
        slice_report.parent.mkdir(parents=True, exist_ok=True)
        slice_report.write_text(json.dumps({"status": "passed"}), encoding="utf-8")
        run(sys.executable, state_script, "transition", str(workspace), "slice_verified", "--slice", "fixture", "--evidence", str(slice_report), expected=1)
        run(sys.executable, str(root / "scripts/run_gates.py"), str(config_path), "--profile", "T3", "--task-id", "fixture-task", "--slice-id", "fixture", "--report", str(slice_report))
        run(sys.executable, state_script, "transition", str(workspace), "slice_verified", "--slice", "fixture", "--evidence", str(slice_report))
        run(sys.executable, state_script, "transition", str(workspace), "migration_transition")
        state = json.loads((workspace / "state/migration-state.json").read_text(encoding="utf-8"))
        assert state["phase"] == "transition"
        assert state["completedSlices"] == ["fixture"]
        checkpoint(target, "fixture-task", ["切片通过"], [], [], "后续缓存独立回归", "closed")
        assert len((workspace / "state/events.jsonl").read_text(encoding="utf-8").splitlines()) == 7
        begin(target, 'reopen-task', ['entry/src/main/ets/pages/Index.ets'], 'new contradictory feedback in fixture')
        before_reopen = (workspace / 'state/migration-state.json').read_bytes()
        run(sys.executable, state_script, 'transition', str(workspace), 'slice_reopened', '--slice', 'fixture', '--task-id', 'reopen-task', '--reason', 'fixture feedback contradicts verification', '--dry-run')
        assert (workspace / 'state/migration-state.json').read_bytes() == before_reopen
        run(sys.executable, state_script, 'transition', str(workspace), 'slice_reopened', '--slice', 'fixture', '--task-id', 'reopen-task', '--reason', 'fixture feedback contradicts verification')
        reopened = json.loads((workspace / 'state/migration-state.json').read_text())
        assert reopened['completedSlices'] == [] and reopened['activeTaskId'] == 'reopen-task'
        assert reopened['evidence']['invalidated']
        run(sys.executable, state_script, 'transition', str(workspace), 'slice_verified', '--slice', 'fixture', '--evidence', str(slice_report), expected=1)
        run(sys.executable, str(root / 'scripts/run_gates.py'), str(config_path), '--profile', 'T3', '--task-id', 'reopen-task', '--slice-id', 'fixture', '--report', str(slice_report))
        run(sys.executable, state_script, 'transition', str(workspace), 'slice_verified', '--slice', 'fixture', '--evidence', str(slice_report))
        run(sys.executable, state_script, 'transition', str(workspace), 'migration_transition')
        checkpoint(target, 'reopen-task', ['reopened slice reverified'], [], [], 'closed', 'closed')
        (source / "ViewController.m").write_text("@implementation ViewController\n// changed\n@end\n", encoding="utf-8")
        run(sys.executable, str(root / "scripts/plan_approval.py"), "check", str(workspace), expected=1)
        (source / "ViewController.m").write_text("@implementation ViewController\n@end\n", encoding="utf-8")
        run(sys.executable, str(root / "scripts/plan_approval.py"), "check", str(workspace))
        plan_data["changed"] = True
        plan.write_text(json.dumps(plan_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        run(sys.executable, str(root / "scripts/plan_approval.py"), "check", str(workspace), expected=1)
        routed = run(
            sys.executable,
            str(root / "scripts/classify_task.py"),
            "--project",
            str(target),
            "--request",
            "修复按钮点击没有反应",
        )
        routed_data = json.loads(routed.stdout)
        assert routed_data["taskLevel"] == "T1"
        assert routed_data["verificationMode"] == "user-runtime-acceptance"
        assert routed_data["runGatesByDefault"] is False
        assert routed_data["independentReport"] is False
        assert routed_data["gateProfile"] is None
        assert routed_data["toolRouting"]["lanhu"]["mode"] == "off"
        assert routed_data["toolRouting"]["ponytailReview"]["mode"] == "off"
        assert not any("code-quality" in path for path in routed_data["mustRead"])
        false_positive = run(
            sys.executable,
            str(root / "scripts/classify_task.py"),
            "--project",
            str(target),
            "--request",
            "修复支付页面发布按钮颜色不对",
        )
        false_positive_data = json.loads(false_positive.stdout)
        assert false_positive_data["taskLevel"] == "T1"
        assert false_positive_data["riskFlags"] == ["ui-local"]
        feature = run(
            sys.executable,
            str(root / "scripts/classify_task.py"),
            "--project",
            str(target),
            "--request",
            "新增页面并实现导航和状态恢复",
        )
        feature_data = json.loads(feature.stdout)
        assert feature_data["taskLevel"] == "T2"
        assert feature_data["toolRouting"]["lanhu"]["mode"] == "off"
        assert feature_data["toolRouting"]["ponytailReview"]["mode"] == "post-diff-conditional"
        assert any(path.endswith("code-quality-contract.md") for path in feature_data["mustRead"])
        assert any(path.endswith("code-quality-migration.md") for path in feature_data["mustRead"])

        project["externalTools"]["lanhu"]["projectUrl"] = "https://lanhuapp.com/example"
        (target / ".migration-v2/project.json").write_text(
            json.dumps(project, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        designed_feature = run(
            sys.executable,
            str(root / "scripts/classify_task.py"),
            "--project",
            str(target),
            "--request",
            "新增会员页面并按设计稿还原布局",
        )
        designed_data = json.loads(designed_feature.stdout)
        assert designed_data["taskLevel"] == "T2"
        assert designed_data["toolRouting"]["lanhu"]["mode"] == "feature"
        migration_slice = run(
            sys.executable,
            str(root / "scripts/classify_task.py"),
            "--project",
            str(target),
            "--request",
            "迁移设置页面",
        )
        migration_data = json.loads(migration_slice.stdout)
        assert migration_data["taskLevel"] == "T3"
        assert migration_data["toolRouting"]["lanhu"]["mode"] == "slice"
        assert migration_data["toolRouting"]["ponytailReview"]["mode"] == "post-diff-conditional"
        parity_batch = run(
            sys.executable,
            str(root / "scripts/classify_task.py"),
            "--project",
            str(target),
            "--request",
            "修复音频、PDF、图片和结果页共七项 iOS 一致性问题",
        )
        parity_batch_data = json.loads(parity_batch.stdout)
        assert parity_batch_data["taskLevel"] == "T3"
        assert parity_batch_data["taskShape"] == "parity-batch"
        assert parity_batch_data["executionStrategy"] == "joint-diagnosis-then-slices"
        assert any(path.endswith("parity-residuals.md") for path in parity_batch_data["mustRead"])
        proof = base / "proof.txt"
        proof.write_text("ok", encoding="utf-8")
        gate_config = {
            "schemaVersion": "2.0",
            "targetRoot": str(target),
            "evidenceRoot": str(workspace / "evidence/gates"),
            "profiles": {"T0": [], "T1": [], "T2": ["smoke"], "T3": [], "T4": []},
            "gates": [{
                "id": "smoke",
                "argv": [sys.executable, "-c", "print('ok')"],
                "cwd": str(target),
                "inputs": ["**/*.ets"],
                "cacheable": True,
                "confirmed": True,
                "fingerprintValues": {"toolchain":"metadata-fixture"},
                "cacheScope": "project-only-deterministic",
            }],
        }
        config = target / ".migration-v2/gate-config.json"
        config.write_text(json.dumps(gate_config), encoding="utf-8")
        begin(target, "cache-task", ["entry/src/main/ets/pages/Index.ets"], "缓存独立回归")
        report = workspace / "reports/gate-report.json"
        run(sys.executable, str(root / "scripts/run_gates.py"), str(config), "--task-id", "cache-task", "--profile", "T2", "--report", str(report))
        run(sys.executable, str(root / "scripts/run_gates.py"), str(config), "--task-id", "cache-task", "--profile", "T2", "--reuse-valid", "--report", str(report))
        assert json.loads(report.read_text(encoding="utf-8"))["gates"][0]["reused"] is True
        ui_manifest = workspace / "ir/ui-state-manifest.json"
        run(
            sys.executable,
            str(root / "scripts/ui_state_manifest.py"),
            "add",
            str(ui_manifest),
            "--slice",
            "fixture",
            "--screen",
            "home",
            "--source-ref",
            "ViewController.m",
            "--target-ref",
            "entry/pages/Index.ets",
            "--states",
            "default",
        )
        run(sys.executable, str(root / "scripts/ui_state_manifest.py"), "check", str(ui_manifest), expected=1)
        ui_data = json.loads(ui_manifest.read_text(encoding="utf-8"))
        ui_data["screens"][0]["states"][0].update({
            "status": "verified",
            "sourceEvidence": [str(proof)],
            "targetEvidence": [str(proof)],
        })
        ui_manifest.write_text(json.dumps(ui_data), encoding="utf-8")
        run(sys.executable, str(root / "scripts/ui_state_manifest.py"), "check", str(ui_manifest), expected=1)
        categories = {
            name: {"score": 92, "evidence": [str(proof)]}
            for name in (
                "structure",
                "typography_assets",
                "interaction_states",
                "motion_lifecycle",
                "navigation_restore",
                "geometry_detail",
            )
        }
        scores = base / "scores.json"
        scores.write_text(json.dumps({"categories": categories}), encoding="utf-8")
        run(
            sys.executable,
            str(root / "scripts/score_ui_quality.py"),
            str(scores),
            "--report",
            str(base / "score-report.json"),
            "--state-manifest",
            str(ui_manifest),
            expected=1,
        )
    print("V2 self-test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

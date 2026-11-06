#!/usr/bin/env python3
"""升级预览、恢复与漂移保护回归；只生成临时规则文本，不创建业务工程。"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from project_framework import MANAGED_MAPPINGS, audit_project, record_manifest, upgrade, restore, review_artifacts, artifact_inventory, installation_states, load_artifact_review
from task_runtime import atomic_json, lock


class UpgradeScenarios(unittest.TestCase):
    """验证升级真正改变受管规则且可恢复，而非只检查标记存在。"""

    def setUp(self):
        """建立临时规则套件与安装清单，不执行其文件内容。"""
        self.temporary = tempfile.TemporaryDirectory(prefix="framework-upgrade-")
        self.base = Path(self.temporary.name).resolve()
        self.project = self.base / "project-metadata"
        self.suite = self.base / "suite-metadata"
        self.project.mkdir()
        (self.project / ".ai-project").mkdir(exist_ok=True)
        (self.project / ".ai-project/read-policy.json").write_text(json.dumps({"schemaVersion":"1.0","confirmed":True,"projectRoot":str(self.project.resolve()),"mode":"complete","allow":["**"],"deny":[],"scopeDescription":"合成测试目录明确授权；不涉及真实业务"}))
        self.suite.mkdir()
        (self.suite / "VERSION").write_text("2.5.0\n")
        self.mappings = MANAGED_MAPPINGS["ios-daily-development-rules"]
        for destination, source in self.mappings:
            for path in (self.project / destination, self.suite / source):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("TASK_PROTOCOL = \"2\"\n# 旧规则夹具\n" if source == "shared/task-routing/task_runtime.py" else "# 旧规则夹具\n")
        atomic_json(self.project / ".ai-project/lifecycle.json", {"state": "ios-maintenance", "frameworkVersion": "2.5.0", "userFact": "保留"})
        record_manifest(self.project, self.suite, "ios-daily-development-rules", "ios-maintenance", self.mappings)
        (self.suite / self.mappings[0][1]).write_text("# 新规则夹具\n")
        (self.suite / "VERSION").write_text("2.5.1\n")

    def tearDown(self):
        """移除本测试临时状态；不触及真实框架和项目。"""
        self.temporary.cleanup()

    def test_preview_upgrade_restore(self):
        """差异预览无写入；升级保留项目事实，恢复还原旧文件和版本。"""
        preview = audit_project(self.project, self.suite)
        self.assertTrue(preview["safeToUpdate"])
        self.assertTrue(any(item["diff"] for item in preview["managedFiles"]))
        target = self.project / self.mappings[0][0]
        self.assertEqual(target.read_text(), "# 旧规则夹具\n")
        result = upgrade(self.project, self.suite, "synthetic: all writers stopped")
        self.assertEqual(target.read_text(), "# 新规则夹具\n")
        self.assertEqual(json.loads((self.project / ".ai-project/lifecycle.json").read_text())["userFact"], "保留")
        restore(self.project, Path(result["transaction"]), "synthetic: all writers stopped")
        self.assertEqual(target.read_text(), "# 旧规则夹具\n")
        self.assertEqual(json.loads((self.project / ".ai-project/lifecycle.json").read_text())["frameworkVersion"], "2.5.0")

    def test_drift_rejects_update_and_restore(self):
        """升级前或升级后的本地漂移都不能被静默覆盖。"""
        target = self.project / self.mappings[0][0]
        target.write_text("# 用户修改\n")
        with self.assertRaises(ValueError):
            upgrade(self.project, self.suite, "synthetic: all writers stopped")
        target.write_text("# 旧规则夹具\n")
        result = upgrade(self.project, self.suite, "synthetic: all writers stopped")
        target.write_text("# 升级后用户修改\n")
        with self.assertRaises(ValueError):
            restore(self.project, Path(result["transaction"]), "synthetic: all writers stopped")
        self.assertEqual(target.read_text(), "# 升级后用户修改\n")

    def test_interrupted_restore_does_not_recreate_user_deleted_file(self):
        """没有 after 的中断事务也不得把用户删除误判为计划内变化。"""
        result = upgrade(self.project, self.suite, "synthetic: all writers stopped")
        transaction = Path(result["transaction"])
        record = json.loads((transaction / "transaction.json").read_text())
        record.pop("after")
        record["status"] = "applying"
        atomic_json(transaction / "transaction.json", record)
        target = self.project / self.mappings[0][0]
        target.unlink()
        with self.assertRaises(ValueError):
            restore(self.project, transaction, "synthetic: all writers stopped")
        self.assertFalse(target.exists())


    def test_adopt_and_record_cannot_reset_existing_drift(self):
        """已有安装清单不能重复 adopt，内部 record 也不能重置任意用户漂移。"""
        target = self.project / self.mappings[0][0]
        target.write_text("# 用户必须保留的漂移")
        with self.assertRaises(ValueError):
            record_manifest(self.project, self.suite, "ios-daily-development-rules", "ios-maintenance", self.mappings)
        script = Path(__file__).with_name("project_framework.py")
        result = subprocess.run([sys.executable, str(script), "adopt", str(self.project), "--suite", str(self.suite)], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("已有安装清单", result.stderr)
        self.assertFalse(audit_project(self.project, self.suite)["safeToUpdate"])


    def test_interrupted_transaction_and_lock(self):
        """缺少最终完成记录的计划内写入仍可恢复；并发升级立即拒绝。"""
        result = upgrade(self.project, self.suite, "synthetic: all writers stopped")
        transaction = Path(result["transaction"])
        record = json.loads((transaction / "transaction.json").read_text())
        record.pop("after")
        record["status"] = "applying"
        atomic_json(transaction / "transaction.json", record)
        restore(self.project, transaction, "synthetic: all writers stopped")
        with lock(self.project / ".ai-project/framework-update.lock"):
            with self.assertRaises(ValueError):
                upgrade(self.project, self.suite, "synthetic: all writers stopped")


    def task(self, task_id="sample", protocol="2", status="active"):
        """创建明确归属于合成根的最小任务记录，用于安装门禁而非伪造验收。"""
        from task_runtime import observe_identity, stable_identity, DIMENSIONS
        from read_boundary import ReadBoundary
        (self.project / "Package.swift").write_text("// synthetic metadata-only fixture\n")
        identity = observe_identity(self.project, "ios")
        state = {"schemaVersion": "1.0", "taskId": task_id, "status": status, "identity": identity, "baseline": {}, "checkpoint": {}, "allowedFiles": ["sample.swift"], "readCoverage": ReadBoundary(self.project).coverage(), "baseRevision": "synthetic", "protectedGlobs": [], "owner": "synthetic-review", "request": "installer metadata fixture", "completed": [], "todo": [], "blockers": [], "nextAction": "synthetic", "updatedAt": "2026-11-06T00:00:00+00:00", "completion": {name: "not-run" for name in DIMENSIONS}}
        if protocol == "2":
            state.update(schemaVersion="2.0", taskProtocolVersion="2", generation=1, stableIdentity=stable_identity(identity, self.project), acceptedContext=identity, acceptedRevision="synthetic")
        elif protocol != "legacy":
            state["schemaVersion"] = protocol
        path = self.project / ".ai-project/tasks" / task_id / "state.json"
        atomic_json(path, state)
        return path

    def test_offline_ack_is_required_and_both_locks_are_respected(self):
        """未声明停写绝不修改；任务锁与更新锁一样阻止安装。"""
        target = self.project / self.mappings[0][0]
        with self.assertRaisesRegex(ValueError, "offline-writers-stopped"):
            upgrade(self.project, self.suite)
        with lock(self.project / ".ai-project/tasks.lock"):
            with self.assertRaises(ValueError):
                upgrade(self.project, self.suite, "synthetic: stopped")
        self.assertEqual(target.read_text(), "# 旧规则夹具\n")

    def test_new_active_and_unknown_tasks_block_but_legacy_not_migrated(self):
        """新活动任务与未知格式阻断；旧任务离线升级后逐字保持，继续占有原范围。"""
        path = self.task()
        with self.assertRaisesRegex(ValueError, "活动任务"):
            upgrade(self.project, self.suite, "synthetic: stopped")
        path.unlink()
        path = self.task(protocol="unknown")
        with self.assertRaisesRegex(ValueError, "未知或部分"):
            upgrade(self.project, self.suite, "synthetic: stopped")
        path.unlink()
        path = self.task(protocol="legacy")
        before = path.read_bytes()
        result = upgrade(self.project, self.suite, "synthetic: stopped")
        self.assertEqual(path.read_bytes(), before)
        transaction = json.loads((Path(result["transaction"]) / "transaction.json").read_text())
        self.assertEqual(transaction["taskDataMigration"], "not-performed")
        self.assertEqual(transaction["offlineWritersStoppedSource"], "synthetic: stopped")

    def test_legacy_restore_rejects_any_new_protocol_data(self):
        """取消也不能把新协议数据交给旧运行器；安装恢复不是任务数据回滚。"""
        manifest_path = self.project / ".ai-project/framework-installation.json"
        manifest = json.loads(manifest_path.read_text()); manifest.pop("taskProtocolVersion")
        atomic_json(manifest_path, manifest)
        result = upgrade(self.project, self.suite, "synthetic: stopped")
        self.task(status="cancelled")
        with self.assertRaisesRegex(ValueError, "不能降级"):
            restore(self.project, Path(result["transaction"]), "synthetic: stopped")

    def test_partial_manifest_and_old_suite_are_rejected(self):
        """协议标记不能掩盖缺运行器、重复路径或旧源码。"""
        path = self.project / ".ai-project/framework-installation.json"
        manifest = json.loads(path.read_text()); manifest["managedFiles"] = manifest["managedFiles"][:-1]
        atomic_json(path, manifest)
        with self.assertRaisesRegex(ValueError, "不完整"):
            audit_project(self.project, self.suite)
        (self.suite / "shared/task-routing/task_runtime.py").write_text("# old runner without protocol guard\n")
        with self.assertRaisesRegex(ValueError, "旧或未知协议"):
            upgrade(self.project, self.suite, "synthetic: stopped")

    def test_record_cannot_bypass_task_or_existing_installation(self):
        """record 不得复位清单或借首装接管有任务的目录。"""
        with self.assertRaisesRegex(ValueError, "已有安装清单"):
            record_manifest(self.project, self.suite, "ios-daily-development-rules", "ios-maintenance", self.mappings)
        (self.project / ".ai-project/framework-installation.json").unlink()
        self.task(protocol="legacy", status="closed")
        with self.assertRaisesRegex(ValueError, "已有任务"):
            record_manifest(self.project, self.suite, "ios-daily-development-rules", "ios-maintenance", self.mappings)

    def test_forged_or_unavailable_inherited_lock_fds_fail_closed(self):
        """字符串声明不能替代可继承、属于当前两锁 inode 的有效描述符。"""
        import os
        from unittest import mock
        from project_framework import installation_locks, INSTALL_LOCK_ENV
        with mock.patch.dict(os.environ, {INSTALL_LOCK_ENV: json.dumps({"projectRoot": str(self.project), "fds": [99998, 99999]})}):
            with self.assertRaises((OSError, ValueError)):
                with installation_locks(self.project):
                    self.fail("invalid descriptors acquired")


    def test_every_upgrade_file_write_can_be_recovered_without_data_migration(self):
        """逐写点在替换后注入中断；可从初始事务前滚识别并恢复全部受管原文。"""
        from unittest import mock
        import project_framework as installer
        original_text, original_json = installer.atomic_text, installer.atomic_json
        expected = {relative: (self.project / relative).read_bytes() for relative, _ in self.mappings}
        expected[".ai-project/framework-installation.json"] = (self.project / ".ai-project/framework-installation.json").read_bytes()
        expected[".ai-project/lifecycle.json"] = (self.project / ".ai-project/lifecycle.json").read_bytes()
        for fail_at in range(1, len(expected) + 1):
            counter = [0]
            def interrupted(path, text):
                original_text(path, text)
                counter[0] += 1
                if counter[0] == fail_at:
                    raise OSError("synthetic crash after planned file replace")
            with mock.patch.object(installer, "atomic_text", side_effect=interrupted):
                with self.assertRaises(OSError):
                    upgrade(self.project, self.suite, "synthetic fault injection: stopped")
            transactions = [path for path in (self.project / ".ai-project/upgrades").glob("*/transaction.json") if json.loads(path.read_text())["status"] == "applying"]
            self.assertEqual(len(transactions), 1)
            restore(self.project, transactions[0].parent, "synthetic recovery: stopped")
            self.assertEqual({relative: (self.project / relative).read_bytes() for relative in expected}, expected)
        def final_record_interrupted(path, value):
            if path.name == "transaction.json" and value.get("status") == "complete":
                raise OSError("synthetic crash before final transaction record")
            return original_json(path, value)
        with mock.patch.object(installer, "atomic_json", side_effect=final_record_interrupted):
            with self.assertRaises(OSError):
                upgrade(self.project, self.suite, "synthetic final record injection: stopped")
        transaction = next(path for path in (self.project / ".ai-project/upgrades").glob("*/transaction.json") if json.loads(path.read_text())["status"] == "applying")
        restore(self.project, transaction.parent, "synthetic recovery: stopped")
        self.assertEqual({relative: (self.project / relative).read_bytes() for relative in expected}, expected)

    def test_incomplete_task_directory_and_initialization_block_installation(self):
        """没有权威状态的普通目录或未发布暂存目录都不能被当作没有任务。"""
        import shutil
        for name in ("orphan", ".init-synthetic"):
            path = self.project / ".ai-project/tasks" / name
            path.mkdir(parents=True)
            (path / "baseline.json").write_text("{}")
            with self.assertRaisesRegex(ValueError, "state.json|初始化"):
                upgrade(self.project, self.suite, "synthetic: stopped")
            shutil.rmtree(path)


    def test_actual_setup_holds_task_lock_and_cannot_overwrite_existing_rules(self):
        """真实首装入口受任务锁保护；成功后再次setup或缺清单旧规则都不能覆盖。"""
        suite = Path(__file__).resolve().parents[1]
        project = self.base / "fresh-ios"
        (project / ".ai-project").mkdir(parents=True)
        (project / "Package.swift").write_text("// synthetic fixture\n")
        atomic_json(project / ".ai-project/read-policy.json", {"schemaVersion": "1.0", "confirmed": True, "projectRoot": str(project), "mode": "complete", "allow": ["**"], "deny": [], "scopeDescription": "synthetic fresh installation"})
        command = [str(suite / "frameworks/ios-daily-development-rules/setup-project.sh"), str(project)]
        with lock(project / ".ai-project/tasks.lock"):
            result = subprocess.run(command, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((project / ".codex-rules").exists())
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        manifest = json.loads((project / ".ai-project/framework-installation.json").read_text())
        self.assertEqual(manifest["taskProtocolVersion"], "2")
        target = project / ".codex-rules/ios/WORKFLOW.md"
        before = target.read_bytes()
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(target.read_bytes(), before)
        (project / ".ai-project/framework-installation.json").unlink()
        target.write_text("synthetic user change")
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(target.read_text(), "synthetic user change")

    def test_migration_first_install_records_actual_shared_runtime_chain(self):
        """迁移入口真实复制并登记运行链；再次resume不重新登记或复制规则。"""
        suite = Path(__file__).resolve().parents[1]
        source, target = self.base / "migration-ios", self.base / "migration-harmony"
        for project in (source, target):
            (project / ".ai-project").mkdir(parents=True)
            atomic_json(project / ".ai-project/read-policy.json", {"schemaVersion": "1.0", "confirmed": True, "projectRoot": str(project), "mode": "complete", "allow": ["**"], "deny": [], "scopeDescription": "synthetic migration installation"})
        (source / "ViewController.m").write_text("@implementation ViewController\n@end\n")
        command = [sys.executable, str(suite / "frameworks/ios-to-harmony-migrator-v2/scripts/start_migration.py"), str(source), str(target)]
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        path = target / ".ai-project/framework-installation.json"
        manifest = json.loads(path.read_text())
        self.assertEqual(manifest["taskProtocolVersion"], "2")
        expected = dict(MANAGED_MAPPINGS["ios-to-harmony-migrator-v2"])
        self.assertEqual({item["path"]: item["source"] for item in manifest["managedFiles"]}, expected)
        for relative, source_relative in expected.items():
            self.assertEqual((target / relative).read_bytes(), (suite / source_relative).read_bytes())
        before = path.read_bytes()
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(path.read_bytes(), before)


    def test_terminal_pending_execution_blocks_upgrade_and_restore_without_state_change(self):
        """终结任务的执行租约继续阻断安装；停写文字不能解除执行，也不更改任务记录。"""
        result = upgrade(self.project, self.suite, "synthetic: stopped before task creation")
        transaction = Path(result["transaction"])
        for protocol, status in (("2", "cancelled"), ("2", "closed"), ("legacy", "cancelled")):
            with self.subTest(protocol=protocol, status=status):
                path = self.task(protocol=protocol, status=status)
                state = json.loads(path.read_text())
                state["pendingExecutions"] = {"synthetic-verification": {"startedAt": "2026-11-06T00:00:00+00:00", "generation": 1, "runnerPid": 1}}
                atomic_json(path, state)
                before = path.read_bytes()
                preview = audit_project(self.project, self.suite)
                self.assertFalse(preview["safeToUpdate"])
                self.assertEqual(preview["pendingExecutionTasks"], ["sample"])
                self.assertEqual(preview["activeProtocol2Tasks"], [])
                managed_before = {item["path"]: (self.project / item["path"]).read_bytes() for item in preview["managedFiles"]}
                with self.assertRaisesRegex(ValueError, "release-execution"):
                    upgrade(self.project, self.suite, "synthetic: all writers claimed stopped")
                with self.assertRaisesRegex(ValueError, "release-execution"):
                    restore(self.project, transaction, "synthetic: all writers claimed stopped")
                self.assertEqual(path.read_bytes(), before)
                self.assertEqual({relative: (self.project / relative).read_bytes() for relative in managed_before}, managed_before)


    def artifact(self, name="archive", router_context=False):
        """建立没有所有权状态的历史材料；context由框架真实router函数生成而非任务state。"""
        directory = self.project / ".ai-project/tasks" / name
        directory.mkdir(parents=True, exist_ok=True)
        if router_context:
            from task_router import build_context
            context = build_context("调整单处说明", self.project, "daily", "pre", [], [])
            context["taskId"] = None
            self.assertEqual(context["schemaVersion"], "2.1")
            atomic_json(directory / "task-context.json", context)
        else:
            (directory / "report.txt").write_bytes(b"synthetic historical report")
        (directory / "handoff.md").write_text("synthetic handwritten handoff without a task baseline")
        staged = directory / "staged"
        staged.mkdir(exist_ok=True)
        (staged / "payload.bin").write_bytes(b"synthetic descendant, not inspected by installer")
        return directory

    def confirmed_artifacts(self, names):
        """仅测试中模拟逐目录明确确认；真实CLI只生成pending草案。"""
        review = review_artifacts(self.project, names)
        self.assertIs(review["confirmed"], False)
        self.assertEqual(review["reviewedBy"], "")
        self.assertEqual(review["authorizationSource"], "")
        review.update(confirmed=True, reviewedBy="synthetic independent reviewer", authorizationSource="synthetic explicit directory-by-directory authorization")
        return review

    def task_material_bytes(self):
        """测试侧保存原材料与状态字节，用于证明安装器没有改写历史原件。"""
        return {path.relative_to(self.project).as_posix(): path.read_bytes() for path in (self.project / ".ai-project/tasks").rglob("*") if path.is_file()}

    def test_router_context_and_materials_require_explicit_confirmed_review(self):
        """schema2.1空taskId路由材料不自动算任务或豁免；pending草案不可用于安装。"""
        self.artifact(router_context=True)
        with self.assertRaisesRegex(ValueError, "state.json"):
            audit_project(self.project, self.suite)
        draft = review_artifacts(self.project, ["archive"])
        with self.assertRaisesRegex(ValueError, "明确确认"):
            upgrade(self.project, self.suite, "synthetic: stopped", draft)
        before = self.task_material_bytes()
        review = self.confirmed_artifacts(["archive"])
        result = upgrade(self.project, self.suite, "synthetic: stopped", review)
        self.assertEqual(self.task_material_bytes(), before)
        manifest = json.loads((self.project / ".ai-project/framework-installation.json").read_text())
        self.assertEqual(manifest["legacyArtifactReview"], review)
        self.assertTrue(audit_project(self.project, self.suite)["safeToUpdate"])
        record = json.loads((Path(result["transaction"]) / "transaction.json").read_text())
        self.assertEqual(record["legacyArtifactReview"], review)
        self.assertEqual(len(record["planned"]), len(self.mappings) + 2)

    def test_artifact_review_preserves_mixed_legacy_states_and_materials(self):
        """合成17份严格旧state加28个材料目录，升级不迁移状态、不改写或删除材料。"""
        for index in range(17):
            self.task(task_id=f"legacy-{index}", protocol="legacy", status="closed" if index < 10 else "active")
        names = [f"evidence-{index}" for index in range(28)]
        for index, name in enumerate(names):
            self.artifact(name, router_context=index < 7)
        before = self.task_material_bytes()
        review = self.confirmed_artifacts(names)
        self.assertEqual(len(installation_states(self.project, review)), 17)
        upgrade(self.project, self.suite, "synthetic: stopped", review)
        self.assertEqual(self.task_material_bytes(), before)
        self.assertEqual(len(installation_states(self.project, review)), 17)

    def test_artifact_review_never_hides_new_unknown_or_partial_state(self):
        """审查后新state须严格解析；合法state也使原材料审查失效，不能忽略其范围。"""
        self.artifact()
        review = self.confirmed_artifacts(["archive"])
        path = self.task(task_id="archive", protocol="legacy", status="active")
        before = path.read_bytes()
        with self.assertRaisesRegex(ValueError, "state|标记"):
            upgrade(self.project, self.suite, "synthetic: stopped", review)
        self.assertEqual(path.read_bytes(), before)
        for value in ({"schemaVersion": "2.1", "taskId": None}, {"schemaVersion": "2.0", "taskId": "archive"}):
            atomic_json(path, value)
            before = path.read_bytes()
            with self.assertRaises(ValueError):
                audit_project(self.project, self.suite, review)
            self.assertEqual(path.read_bytes(), before)

    def test_persistent_markers_and_initialization_cannot_be_reviewed_as_artifacts(self):
        """已知持久任务标记即使没有state也阻断；普通手写handoff须显式审查才可保留。"""
        from project_framework import TASK_STATE_MARKERS
        import shutil
        directory = self.artifact()
        for name in sorted(TASK_STATE_MARKERS - {"state.json"}):
            with self.subTest(marker=name):
                marker = directory / name
                marker.write_text("synthetic ownership marker")
                with self.assertRaisesRegex(ValueError, "持久任务标记"):
                    review_artifacts(self.project, ["archive"])
                marker.unlink()
        shutil.rmtree(directory)
        self.artifact(".init-synthetic")
        with self.assertRaises(ValueError):
            review_artifacts(self.project, [".init-synthetic"])
        with self.assertRaisesRegex(ValueError, "初始化"):
            audit_project(self.project, self.suite)

    def test_review_root_inventory_and_metadata_drift_reject_without_writes(self):
        """复制根、同大小附件变化、目录换名、新条目及审查记录摘要篡改都阻断。"""
        import copy, os
        directory = self.artifact()
        review = self.confirmed_artifacts(["archive"])
        for key in ("projectRoot", "projectDevice", "projectInode"):
            altered = copy.deepcopy(review)
            altered[key] = str(self.base / "different-project") if key == "projectRoot" else altered[key] + 1
            with self.assertRaisesRegex(ValueError, "其他项目根"):
                audit_project(self.project, self.suite, altered)
        altered = copy.deepcopy(review)
        altered["directories"]["archive"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "漂移"):
            audit_project(self.project, self.suite, altered)
        altered = copy.deepcopy(review)
        # Python把1与1.0视为相等，但审查内清单本身也必须有同一稳定JSON摘要。
        entry = altered["directInventories"]["archive"]["entries"][0]
        entry["size"] = float(entry["size"])
        with self.assertRaisesRegex(ValueError, "漂移"):
            audit_project(self.project, self.suite, altered)
        path = directory / "report.txt"
        info = path.stat(); path.write_bytes(b"X" * info.st_size)
        os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns))
        with self.assertRaisesRegex(ValueError, "漂移"):
            audit_project(self.project, self.suite, review)
        review = self.confirmed_artifacts(["archive"])
        (directory / "new.txt").write_text("synthetic new evidence")
        before = self.task_material_bytes()
        with self.assertRaisesRegex(ValueError, "漂移"):
            upgrade(self.project, self.suite, "synthetic: stopped", review)
        self.assertEqual(self.task_material_bytes(), before)

    def test_artifact_inventory_never_reads_attachment_bytes_or_descendants(self):
        """审查只读目录元数据；报告与子目录内容的open/stat均禁止仍可生成草案。"""
        import os
        from unittest import mock
        self.artifact(router_context=True)
        original_open, original_stat = os.open, os.stat
        def guarded_open(path, *args, **kwargs):
            if str(path) in {"task-context.json", "handoff.md", "payload.bin"}:
                raise AssertionError("attachment bytes opened")
            return original_open(path, *args, **kwargs)
        def guarded_stat(path, *args, **kwargs):
            if str(path) == "payload.bin":
                raise AssertionError("descendant metadata inspected")
            return original_stat(path, *args, **kwargs)
        with mock.patch("os.open", side_effect=guarded_open), mock.patch("os.stat", side_effect=guarded_stat):
            draft = review_artifacts(self.project, ["archive"])
        self.assertIs(draft["confirmed"], False)
        self.assertEqual({item["name"] for item in draft["directInventories"]["archive"]["entries"]}, {"task-context.json", "handoff.md", "staged"})

    def test_unknown_denied_and_unsafe_entries_block_before_private_stat(self):
        """未知/拒绝直接项先检查名字授权，链接、硬链接和特殊文件不可接受。"""
        import os
        from unittest import mock
        directory = self.artifact()
        private = directory / ".env"
        private.write_text("synthetic excluded material")
        original_stat = os.stat
        def guard(path, *args, **kwargs):
            if str(path) == ".env":
                raise AssertionError("private entry stat")
            return original_stat(path, *args, **kwargs)
        with mock.patch("os.stat", side_effect=guard):
            with self.assertRaises(ValueError):
                review_artifacts(self.project, ["archive"])
        private.unlink()
        source = directory / "report.txt"
        unsafe = directory / "unsafe"
        for kind in ("symlink", "hardlink", "fifo"):
            with self.subTest(kind=kind):
                if kind == "symlink":
                    unsafe.symlink_to(source)
                elif kind == "hardlink":
                    os.link(source, unsafe)
                else:
                    os.mkfifo(unsafe)
                with self.assertRaises(ValueError):
                    review_artifacts(self.project, ["archive"])
                unsafe.unlink()

    def test_partial_policy_and_task_only_do_not_gain_artifact_read_scope(self):
        """显式审查不能扩充已有白名单；受限条目在stat之前拒绝，task-only入口保持关闭。"""
        import os
        from unittest import mock
        from read_boundary import task_only_scope
        directory = self.artifact()
        denied = directory / "denied.txt"; denied.write_text("synthetic denied")
        policy_path = self.project / ".ai-project/read-policy.json"
        policy = json.loads(policy_path.read_text())
        policy.update(mode="partial", allow=[".ai-project/**", ".codex-rules/**"], deny=[".ai-project/tasks/archive/denied.txt"])
        atomic_json(policy_path, policy)
        original_stat = os.stat
        def guard(path, *args, **kwargs):
            if str(path) == "denied.txt":
                raise AssertionError("denied entry stat")
            return original_stat(path, *args, **kwargs)
        with mock.patch("os.stat", side_effect=guard):
            with self.assertRaises(ValueError):
                review_artifacts(self.project, ["archive"])
        with task_only_scope(self.project, "sample"):
            with self.assertRaisesRegex(ValueError, "任务专用"):
                review_artifacts(self.project, ["archive"])
        self.assertEqual(json.loads(policy_path.read_text()), policy)

    def test_half_install_restore_uses_record_review_before_manifest_is_updated(self):
        """首个规则替换后中断，新manifest尚未出现时仍按原事务审查恢复，不碰历史材料。"""
        from unittest import mock
        import project_framework as installer
        self.artifact(router_context=True)
        review = self.confirmed_artifacts(["archive"])
        before = self.task_material_bytes()
        old_manifest = (self.project / ".ai-project/framework-installation.json").read_bytes()
        original = installer.atomic_text
        def interrupted(path, text):
            original(path, text)
            raise OSError("synthetic crash after first managed-file replacement")
        with mock.patch.object(installer, "atomic_text", side_effect=interrupted):
            with self.assertRaises(OSError):
                upgrade(self.project, self.suite, "synthetic: stopped", review)
        self.assertEqual((self.project / ".ai-project/framework-installation.json").read_bytes(), old_manifest)
        transaction = next((self.project / ".ai-project/upgrades").glob("*/transaction.json"))
        record = json.loads(transaction.read_text())
        self.assertEqual(record["legacyArtifactReview"], review)
        self.assertNotIn("legacyArtifactReview", json.loads(old_manifest))
        restore(self.project, transaction.parent, "synthetic: stopped")
        self.assertEqual(self.task_material_bytes(), before)
        self.assertEqual((self.project / ".ai-project/framework-installation.json").read_bytes(), old_manifest)
        self.assertTrue(audit_project(self.project, self.suite, review)["safeToUpdate"])

    def test_review_cli_requires_exact_paired_file_hash_and_outputs_pending_only(self):
        """CLI只输出待审草案；文件SHA或参数缺失/预览后换文件都在安装写前阻断。"""
        import hashlib
        self.artifact()
        script = Path(__file__).with_name("project_framework.py")
        command = [sys.executable, str(script), "review-artifacts", str(self.project), "--directory", "archive"]
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIs(json.loads(result.stdout)["confirmed"], False)
        review = self.confirmed_artifacts(["archive"])
        path = self.base / "explicit-review.json"; path.write_text(json.dumps(review))
        expected = hashlib.sha256(path.read_bytes()).hexdigest()
        self.assertEqual(load_artifact_review(self.project, path, expected), review)
        for value in (None, "0" * 64):
            with self.assertRaisesRegex(ValueError, "SHA256|配对"):
                load_artifact_review(self.project, path, value)
        audit = [sys.executable, str(script), "audit", str(self.project), "--suite", str(self.suite), "--legacy-artifact-review", str(path), "--legacy-artifact-review-sha256", expected]
        result = subprocess.run(audit, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        review["reviewedBy"] = "changed after preview"; path.write_text(json.dumps(review))
        apply = [sys.executable, str(script), "update", str(self.project), "--suite", str(self.suite), "--offline-writers-stopped", "synthetic: stopped", "--legacy-artifact-review", str(path), "--legacy-artifact-review-sha256", expected]
        result = subprocess.run(apply, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.project / ".ai-project/upgrades").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)

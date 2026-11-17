#!/usr/bin/env python3
"""隔离回归：显式摘要批准的规则升级保留任务原文，不执行任何业务工程。"""

import copy
import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import project_framework as installer
from pin_release import pin, verify
from task_runtime import (
    acknowledge_metadata,
    atomic_json,
    begin,
    check_scope,
    lock,
    observe_identity,
    resume,
    rules_identity,
    validate_state,
)


class SimulatedPowerLoss(BaseException):
    """模拟无法由普通异常处理捕获的中断，留下供显式恢复的事务。"""


class PreserveUpgradeScenarios(unittest.TestCase):
    """全部读写限定在 TemporaryDirectory；实际安装与运行时 API 不打成功桩。"""

    def setUp(self):
        """建立两个独立固定套件和完整安装，旧源码在整个升级期间保持可校验。"""
        temporary = tempfile.TemporaryDirectory(prefix="framework-preserve-")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.project = self.base / "project"
        self.source = self.base / "candidate-source"
        self.project.mkdir()
        self.source.mkdir()
        self.mappings = installer.MANAGED_MAPPINGS["ios-daily-development-rules"]
        checkout = Path(__file__).resolve().parents[1]
        for _, source in self.mappings:
            target = self.source / source
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(checkout / source, target)
        (self.source / "README.md").write_text("# Synthetic pinned fixture\n")
        (self.source / "VERSION").write_text("2.6.11-fixture\n")
        self.rule = ".codex-rules/_shared/references/read-boundary.md"
        self.rule_source = dict(self.mappings)[self.rule]
        (self.source / self.rule_source).write_text("# fixture-old-rule\n")
        self.old_suite = Path(pin(self.source, self.base / "pins", True)["root"])

        atomic_json(self.project / ".ai-project/read-policy.json", {
            "schemaVersion": "1.0", "confirmed": True,
            "projectRoot": str(self.project), "mode": "complete",
            "allow": ["**"], "deny": [],
            "scopeDescription": "synthetic regression fixture only",
        })
        (self.project / "Package.swift").write_text("// synthetic identity marker\n")
        # 提前建立所有独立编辑范围，后建任务不会使旧任务看到新增业务文件。
        self.code_names = [f"sample-{index:02d}.swift" for index in range(71)]
        for name in self.code_names:
            (self.project / name).write_text(f"// synthetic source {name}\n")
        for destination, source in self.mappings:
            target = self.project / destination
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(self.old_suite / source, target)
        atomic_json(self.project / ".ai-project/lifecycle.json", {
            "state": "ios-maintenance", "frameworkVersion": "2.6.11-fixture",
            "userFact": "必须逐字保留的项目事实",
        })
        installer.record_manifest(
            self.project, self.old_suite, "ios-daily-development-rules",
            "ios-maintenance", self.mappings,
        )
        atomic_json(self.project / ".ai-project/identity.json",
                    observe_identity(self.project, "ios"))
        (self.source / "VERSION").write_text("2.6.12-fixture\n")
        (self.source / self.rule_source).write_text("# fixture-new-rule\n")
        self.suite = Path(pin(self.source, self.base / "pins", True)["root"])
        self.assertNotEqual(self.suite, self.old_suite)
        verify(self.old_suite)
        verify(self.suite)
        rules_identity(self.project)

    def tasks(self, count=1):
        """由真实 begin 的完整记录派生独立任务，保留非规范 JSON 原始字节。"""
        directory = self.project / ".ai-project/tasks/task-00"
        state = (json.loads((directory / "state.json").read_text())
                 if (directory / "state.json").exists()
                 else begin(self.project, "task-00", [self.code_names[0]], "fixture task"))
        baseline = json.loads((directory / "baseline.json").read_text())
        statuses = ("active", "blocked", "waiting-permission")
        for index in range(count):
            task_id = f"task-{index:02d}"
            current = copy.deepcopy(state)
            current.update(taskId=task_id, allowedFiles=[self.code_names[index]],
                           status=statuses[index % len(statuses)],
                           nextAction=f"fixture next action {index}")
            validate_state(current)
            frozen = {**baseline, "taskId": task_id}
            target = self.project / ".ai-project/tasks" / task_id
            target.mkdir(exist_ok=True)
            # 不使用安装器的序列化，以发现偷偷重排字段或重新保存同值 JSON。
            (target / "state.json").write_bytes(
                (json.dumps(current, ensure_ascii=False, indent=3) + "\n\n").encode())
            (target / "baseline.json").write_bytes(
                (json.dumps(frozen, ensure_ascii=False, indent=1) + "\n\n").encode())
            (target / "handoff.md").write_text(f"# 原交接 {index}\n\n不可重建\n")
        return directory / "state.json"

    def snapshot(self):
        """记录业务、规则和任务全部原文，排除事务日志与合法锁文件。"""
        return {
            path.relative_to(self.project).as_posix(): path.read_bytes()
            for path in self.project.rglob("*")
            if path.is_file()
            and ".ai-project/upgrades/" not in path.relative_to(self.project).as_posix()
            and path.name not in {"framework-update.lock", "tasks.lock", "verification.lock"}
        }

    def task_bytes(self):
        """任务状态、冻结基线和交接原文均须不变，不只比较 JSON 语义。"""
        return {name: data for name, data in self.snapshot().items()
                if name.startswith(".ai-project/tasks/")}

    def plan(self):
        """通过公开只读 API 获得本轮精确计划摘要，不构造或伪造批准。"""
        report = installer.audit_project(self.project, self.suite)
        digest = report.get("preserveTaskStatePlanSha256")
        self.assertIsInstance(digest, str, {
            key: report.get(key) for key in (
                "preserveTaskStateBlockedReason", "activeProtocol2Tasks",
                "pendingExecutionTasks", "unsupportedTaskSchemaTasks", "localDrift",
            )
        })
        self.assertRegex(digest, r"^[0-9a-f]{64}$")
        return digest

    def apply(self, digest):
        """只在临时目录显式选择 preserve 模式，保留原有停写声明门禁。"""
        return installer.upgrade(
            self.project, self.suite, "fixture-stop",
            preserve_task_state_plan_sha256=digest,
        )

    def transactions(self):
        """返回本夹具的升级日志，供恢复与自动回滚断言使用。"""
        return sorted((self.project / ".ai-project/upgrades").glob("*/transaction.json"))

    def assert_rejected_unchanged(self, digest):
        """被拒操作不得改写业务、安装原文或任务，即使执行锁文件已存在。"""
        before = self.snapshot()
        with self.assertRaises(ValueError):
            self.apply(digest)
        self.assertEqual(self.snapshot(), before)

    def test_no_opt_in_keeps_active_task_guard_and_audit_is_read_only(self):
        """出现 preserve 计划不等自动启用；原无参数升级仍须拒绝活动任务。"""
        self.tasks()
        before = self.snapshot()
        report = installer.audit_project(self.project, self.suite)
        self.assertFalse(report["safeToUpdate"])
        self.plan()
        self.assertEqual(self.snapshot(), before)
        with self.assertRaisesRegex(ValueError, "活动任务"):
            installer.upgrade(self.project, self.suite, "fixture-stop")
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.transactions(), [])

    def test_71_tasks_keep_exact_state_baseline_and_handoff_through_restore(self):
        """71 个活动／阻塞／待权限任务升级和恢复均保留原文与状态。"""
        self.tasks(71)
        before, task_before = self.snapshot(), self.task_bytes()
        self.assertEqual(len([name for name in task_before if name.endswith("state.json")]), 71)
        self.assertEqual({json.loads(data)["status"] for name, data in task_before.items()
                          if name.endswith("state.json")},
                         {"active", "blocked", "waiting-permission"})
        digest = self.plan()
        self.assertEqual(self.plan(), digest)
        result = self.apply(digest)
        self.assertEqual(result["status"], "updated")
        self.assertEqual((self.project / self.rule).read_text(), "# fixture-new-rule\n")
        self.assertEqual(self.task_bytes(), task_before)
        verify(self.old_suite)
        rules_identity(self.project)
        transaction = Path(result["transaction"])
        record = json.loads((transaction / "transaction.json").read_text())
        self.assertEqual(record["taskDataMigration"], "not-performed")
        self.assertEqual(record["offlineWritersStoppedSource"], "fixture-stop")
        installer.restore(self.project, transaction, "fixture-stop")
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.task_bytes(), task_before)
        rules_identity(self.project)

    def test_resume_accepts_exact_managed_overlay_but_not_unowned_changes(self):
        """升级后原任务可只读恢复，带短横线的规则路径不再误判为越界。"""
        path = self.tasks()
        task_before = self.task_bytes()
        state = json.loads(path.read_text())
        self.assertIn(self.rule, state["baseline"])
        self.apply(self.plan())
        self.assertEqual(resume(self.project, "task-00")["status"], "active")
        self.assertEqual(check_scope(self.project, state)["changedFiles"], [])
        self.assertEqual(self.task_bytes(), task_before)
        (self.project / "unowned.swift").write_text("// unrelated later edit\n")
        with self.assertRaises(ValueError):
            resume(self.project, "task-00")
        self.assertEqual(self.task_bytes(), task_before)

    def test_wrong_digest_and_missing_offline_ack_do_not_write(self):
        """摘要和停写声明是独立门禁，不能用其中一个代替另一个。"""
        self.tasks()
        digest = self.plan()
        self.assert_rejected_unchanged("0" * 64)
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "offline-writers-stopped"):
            installer.upgrade(self.project, self.suite,
                              preserve_task_state_plan_sha256=digest)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.transactions(), [])

    def test_state_and_baseline_bytes_are_bound_to_preview(self):
        """即使只改变合法 JSON 的空白，旧摘要也不能批准新状态或基线。"""
        path = self.tasks()
        for target in (path, path.with_name("baseline.json")):
            with self.subTest(file=target.name):
                digest, original = self.plan(), target.read_bytes()
                target.write_bytes(original + b" \n")
                self.assert_rejected_unchanged(digest)
                target.write_bytes(original)

    def test_new_task_after_preview_is_not_silently_preserved(self):
        """计划绑定完整任务集合，预览后新增合法任务必须重新审查。"""
        self.tasks()
        digest = self.plan()
        self.tasks(2)
        self.assert_rejected_unchanged(digest)

    def test_managed_and_candidate_drift_invalidate_preserve_plan(self):
        """保留任务不提供覆盖本地规则或漂移候选套件的旁路。"""
        self.tasks()
        for target in (self.project / self.rule, self.suite / self.rule_source):
            with self.subTest(file=str(target)):
                digest, original = self.plan(), target.read_bytes()
                target.write_bytes(original + b"# unreviewed drift\n")
                self.assert_rejected_unchanged(digest)
                target.write_bytes(original)

    def test_inconsistent_baseline_never_gets_an_eligible_digest(self):
        """冻结基线必须与 state 的任务 ID、原身份、路径摘要一致。"""
        path = self.tasks().with_name("baseline.json")
        original = path.read_bytes()
        for key, value in (("taskId", "wrong-task"), ("identity", {}), ("paths", {})):
            with self.subTest(field=key):
                invalid = json.loads(original)
                invalid[key] = value
                atomic_json(path, invalid)
                before = self.snapshot()
                try:
                    report = installer.audit_project(self.project, self.suite)
                except ValueError:
                    pass
                else:
                    self.assertFalse(report.get("preserveTaskStatePlanSha256"))
                self.assert_rejected_unchanged("0" * 64)
                self.assertEqual(self.snapshot(), before)
                path.write_bytes(original)

    def test_unavailable_or_changed_previous_pin_blocks_preserve(self):
        """旧套件无法独立验证时不可签发 preserve 计划。"""
        self.tasks()
        digest = self.plan()
        path = self.old_suite / self.rule_source
        original = path.read_bytes()
        path.write_bytes(original + b"# stale old pin\n")
        self.assert_rejected_unchanged(digest)
        path.write_bytes(original)
        moved = self.old_suite.with_name("temporarily-unavailable-pin")
        self.old_suite.rename(moved)
        try:
            self.assert_rejected_unchanged(digest)
        finally:
            moved.rename(self.old_suite)

    def test_pending_execution_blocks_upgrade_and_restore(self):
        """未释放执行登记阻断升级及恢复，不因停写文字或终态而忽略。"""
        path = self.tasks()
        digest, original = self.plan(), path.read_bytes()
        state = json.loads(original)
        state["pendingExecutions"] = {"fixture-runner": {"startedAt": "2026-11-17T00:00:00+00:00"}}
        atomic_json(path, state)
        report = installer.audit_project(self.project, self.suite)
        self.assertFalse(report.get("preserveTaskStatePlanSha256"))
        self.assert_rejected_unchanged(digest)
        path.write_bytes(original)
        result = self.apply(self.plan())
        state["status"] = "cancelled"
        atomic_json(path, state)
        before = self.snapshot()
        with self.assertRaises(ValueError):
            installer.restore(self.project, Path(result["transaction"]), "fixture-stop")
        self.assertEqual(self.snapshot(), before)

    def test_real_verification_lease_blocks_upgrade_and_restore(self):
        """真实 flock 即使没有 pending 登记也阻断，不以模拟字段代替租约。"""
        path = self.tasks()
        digest = self.plan()
        with lock(path.with_name("verification.lock")):
            self.assertFalse(installer.audit_project(self.project, self.suite)
                             .get("preserveTaskStatePlanSha256"))
            self.assert_rejected_unchanged(digest)
        result = self.apply(self.plan())
        before = self.snapshot()
        with lock(path.with_name("verification.lock")):
            with self.assertRaises(ValueError):
                installer.restore(self.project, Path(result["transaction"]), "fixture-stop")
        self.assertEqual(self.snapshot(), before)

    def metadata_task(self):
        """通过真实元数据确认生成完整 2.1 历史，不手造宽松的 schema 标记。"""
        name = "Fixture.xcworkspace/xcuserdata/fixture.xcuserdatad/UserInterfaceState.xcuserstate"
        path = self.project / name
        path.parent.mkdir(parents=True)
        path.write_bytes(b"fixture-old-ui")
        state_path = self.tasks()
        path.write_bytes(b"fixture-new-ui")
        old, new = (hashlib.sha256(value).hexdigest()
                    for value in (b"fixture-old-ui", b"fixture-new-ui"))
        authorization = {"confirmed_by": "fixture reviewer",
                         "authorization_source": "synthetic explicit decision"}
        preview = acknowledge_metadata(self.project, "task-00", name, old, new,
                                       "fixture UI update", **authorization)
        acknowledge_metadata(self.project, "task-00", name, old, new,
                             "fixture UI update", True, preview["previewSha256"],
                             **authorization)
        self.assertEqual(json.loads(state_path.read_text())["schemaVersion"], "2.1")
        return state_path

    def test_supported_schema21_history_survives_upgrade_resume_and_restore(self):
        """合法 2.1 元数据链保持原文，规则交接与既有 UI 确认各自精确生效。"""
        self.metadata_task()
        before, tasks = self.snapshot(), self.task_bytes()
        result = self.apply(self.plan())
        self.assertEqual(self.task_bytes(), tasks)
        self.assertEqual(resume(self.project, "task-00")["schemaVersion"], "2.1")
        installer.restore(self.project, Path(result["transaction"]), "fixture-stop")
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(resume(self.project, "task-00")["schemaVersion"], "2.1")

    def test_unsupported_schema_never_receives_a_preserve_plan(self):
        """候选不支持 2.1 时，显式 preserve 也不得降级元数据语义。"""
        self.metadata_task()
        runtime = self.source / "shared/task-routing/task_runtime.py"
        original = runtime.read_text()
        declaration = 'SUPPORTED_STATE_SCHEMAS = ("2.0", "2.1")'
        self.assertEqual(original.count(declaration), 1)
        runtime.write_text(original.replace(declaration, 'SUPPORTED_STATE_SCHEMAS = ("2.0",)'))
        self.suite = Path(pin(self.source, self.base / "pins", True)["root"])
        report = installer.audit_project(self.project, self.suite)
        self.assertEqual(report["unsupportedTaskSchemaTasks"], ["task-00"])
        self.assertFalse(report.get("preserveTaskStatePlanSha256"))
        self.assert_rejected_unchanged("0" * 64)

    def test_same_suite_or_legacy_installation_cannot_opt_in(self):
        """必须是两个独立套件及原协议 2 清单，不能借 preserve 原地接管旧安装。"""
        self.tasks()
        report = installer.audit_project(self.project, self.old_suite)
        self.assertFalse(report.get("preserveTaskStatePlanSha256"))
        manifest_path = self.project / installer.MANIFEST_RELATIVE
        manifest = json.loads(manifest_path.read_text())
        manifest.pop("taskProtocolVersion")
        atomic_json(manifest_path, manifest)
        report = installer.audit_project(self.project, self.suite)
        self.assertFalse(report.get("preserveTaskStatePlanSha256"))
        self.assert_rejected_unchanged("0" * 64)

    def test_receipt_tampering_and_rehashed_scope_widening_are_rejected(self):
        """摘要检测意外篡改；重算摘要仍不能扩大精确受管路径或改写原基线。"""
        state_path = self.tasks()
        state, task_before = json.loads(state_path.read_text()), self.task_bytes()
        self.apply(self.plan())
        manifest_path = self.project / installer.MANIFEST_RELATIVE
        original_bytes = manifest_path.read_bytes()
        original = json.loads(original_bytes)

        def receipt_digest(receipt):
            """独立计算公开规范的规范化 JSON 摘要，不调用被测验证函数。"""
            payload = {key: receipt[key] for key in ("protocol", "planSha256", "tasks")}
            return hashlib.sha256(json.dumps(
                payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode()).hexdigest()

        receipt = original["preservedTaskRules"]
        self.assertEqual(receipt["receiptSha256"], receipt_digest(receipt))
        cases = []
        altered = copy.deepcopy(original)
        altered["preservedTaskRules"]["planSha256"] = "0" * 64
        cases.append(("changed plan without new receipt digest", altered, False))
        altered = copy.deepcopy(original)
        altered["preservedTaskRules"]["tasks"]["task-00"]["baselineSha256"] = "0" * 64
        cases.append(("changed frozen baseline anchor", altered, True))
        for key in ("baselineSha256", "installedSha256"):
            altered = copy.deepcopy(original)
            altered["preservedTaskRules"]["tasks"]["task-00"]["paths"][self.rule][key] = "0" * 64
            cases.append((f"changed transition {key}", altered, True))
        altered = copy.deepcopy(original)
        unrelated = self.code_names[1]
        altered["preservedTaskRules"]["tasks"]["task-00"]["paths"][unrelated] = {
            "baselineSha256": state["baseline"][unrelated],
            "installedSha256": state["baseline"][unrelated],
        }
        cases.append(("non-managed business path", altered, True))
        altered = copy.deepcopy(original)
        altered["preservedTaskRules"]["tasks"]["task-00"]["paths"][self.rule]["wildcard"] = "**"
        cases.append(("extra transition field", altered, True))
        for name, manifest, rehash in cases:
            with self.subTest(tampering=name):
                if rehash:
                    manifest["preservedTaskRules"]["receiptSha256"] = receipt_digest(manifest["preservedTaskRules"])
                atomic_json(manifest_path, manifest)
                try:
                    with self.assertRaises(ValueError):
                        check_scope(self.project, state)
                    with self.assertRaises(ValueError):
                        resume(self.project, "task-00")
                    self.assertEqual(self.task_bytes(), task_before)
                finally:
                    manifest_path.write_bytes(original_bytes)
        self.assertEqual(resume(self.project, "task-00")["status"], "active")

    def test_chained_upgrades_preserve_original_baseline_and_restore_each_step(self):
        """A→B→C 沿用原始任务基线；先恢复 B 再恢复 A，交接回执亦逐字恢复。"""
        state_path = self.tasks(3)
        state = json.loads(state_path.read_text())
        original, task_before = self.snapshot(), self.task_bytes()
        first = self.apply(self.plan())
        intermediate = self.snapshot()
        self.assertEqual(self.task_bytes(), task_before)
        # 第二轮既改变已交接路径，也新增另一条准确受管路径。
        (self.source / "VERSION").write_text("2.6.13-fixture\n")
        (self.source / self.rule_source).write_text("# fixture-third-rule\n")
        another = ".codex-rules/ios/references/verification.md"
        source = self.source / dict(self.mappings)[another]
        source.write_text(source.read_text() + "\n# fixture-third-verification\n")
        self.suite = Path(pin(self.source, self.base / "pins", True)["root"])
        second = self.apply(self.plan())
        self.assertEqual(self.task_bytes(), task_before)
        manifest = json.loads((self.project / installer.MANIFEST_RELATIVE).read_text())
        paths = manifest["preservedTaskRules"]["tasks"]["task-00"]["paths"]
        for name in (self.rule, another):
            self.assertEqual(paths[name]["baselineSha256"], state["baseline"][name])
            self.assertEqual(paths[name]["installedSha256"],
                             hashlib.sha256((self.project / name).read_bytes()).hexdigest())
        for index, status in enumerate(("active", "blocked", "waiting-permission")):
            self.assertEqual(resume(self.project, f"task-{index:02d}")["status"], status)
        installer.restore(self.project, Path(second["transaction"]), "fixture-stop")
        self.assertEqual(self.snapshot(), intermediate)
        self.assertEqual(resume(self.project, "task-00")["status"], "active")
        installer.restore(self.project, Path(first["transaction"]), "fixture-stop")
        self.assertEqual(self.snapshot(), original)
        self.assertEqual(self.task_bytes(), task_before)
        self.assertEqual(resume(self.project, "task-00")["status"], "active")

    def test_caught_atomic_text_failure_rolls_back_without_task_migration(self):
        """每个计划写点替换后抛一次 OSError，自动回滚且任务原文不动。"""
        self.tasks(3)
        before = self.snapshot()
        original_write = installer.atomic_text
        writes = []

        def observe_write(path, text):
            """记录真实升级写序列，不假定所有内容未变的受管文件都会被替换。"""
            writes.append(Path(path).relative_to(self.project).as_posix())
            original_write(path, text)

        with mock.patch.object(installer, "atomic_text", side_effect=observe_write):
            result = self.apply(self.plan())
        self.assertIn(self.rule, writes)
        self.assertIn(installer.MANIFEST_RELATIVE.as_posix(), writes)
        installer.restore(self.project, Path(result["transaction"]), "fixture-stop")
        self.assertEqual(self.snapshot(), before)
        for fail_at in range(1, len(writes) + 1):
            with self.subTest(write=fail_at):
                digest, count = self.plan(), 0

                def interrupted(path, text):
                    """仅故障一次，允许回滚使用同一真实原子写函数。"""
                    nonlocal count
                    original_write(path, text)
                    count += 1
                    if count == fail_at:
                        raise OSError("fixture caught failure after replace")

                with mock.patch.object(installer, "atomic_text", side_effect=interrupted):
                    with self.assertRaises(OSError):
                        self.apply(digest)
                self.assertGreaterEqual(count, fail_at)
                self.assertEqual(self.snapshot(), before)
                self.assertTrue(self.transactions())
                self.assertTrue(all(json.loads(path.read_text())["status"] == "restored"
                                    for path in self.transactions()))

    def test_uncaught_power_loss_leaves_explicitly_restorable_journal(self):
        """非普通异常保留计划日志，原 restore API 可恢复且不重写活动任务。"""
        self.tasks(3)
        before, tasks = self.snapshot(), self.task_bytes()
        original_write = installer.atomic_text
        changed = False

        def interrupted(path, text):
            """等实际变更的规则落盘后中断，避免只测替换相同内容。"""
            nonlocal changed
            original_write(path, text)
            if Path(path) == self.project / self.rule:
                changed = True
                raise SimulatedPowerLoss("fixture abrupt interruption")

        with mock.patch.object(installer, "atomic_text", side_effect=interrupted):
            with self.assertRaises(SimulatedPowerLoss):
                self.apply(self.plan())
        self.assertTrue(changed)
        self.assertEqual(self.task_bytes(), tasks)
        self.assertEqual((self.project / self.rule).read_text(), "# fixture-new-rule\n")
        journals = self.transactions()
        self.assertEqual(len(journals), 1)
        self.assertEqual(json.loads(journals[0].read_text())["status"], "applying")
        installer.restore(self.project, journals[0].parent, "fixture-stop")
        self.assertEqual(self.snapshot(), before)

    def test_restore_refuses_later_managed_drift_without_overwriting(self):
        """preserve 恢复仍不能覆盖升级后的用户规则修改。"""
        self.tasks()
        result = self.apply(self.plan())
        (self.project / self.rule).write_text("# user edit after upgrade\n")
        before = self.snapshot()
        with self.assertRaises(ValueError):
            installer.restore(self.project, Path(result["transaction"]), "fixture-stop")
        self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    unittest.main(verbosity=2)

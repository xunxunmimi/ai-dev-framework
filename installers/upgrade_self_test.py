#!/usr/bin/env python3
"""升级预览、恢复与漂移保护回归；只生成临时规则文本，不创建业务工程。"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from project_framework import MANAGED_MAPPINGS, audit_project, record_manifest, upgrade, restore
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
                path.write_text("# 旧规则夹具\n")
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
        result = upgrade(self.project, self.suite)
        self.assertEqual(target.read_text(), "# 新规则夹具\n")
        self.assertEqual(json.loads((self.project / ".ai-project/lifecycle.json").read_text())["userFact"], "保留")
        restore(self.project, Path(result["transaction"]))
        self.assertEqual(target.read_text(), "# 旧规则夹具\n")
        self.assertEqual(json.loads((self.project / ".ai-project/lifecycle.json").read_text())["frameworkVersion"], "2.5.0")

    def test_drift_rejects_update_and_restore(self):
        """升级前或升级后的本地漂移都不能被静默覆盖。"""
        target = self.project / self.mappings[0][0]
        target.write_text("# 用户修改\n")
        with self.assertRaises(ValueError):
            upgrade(self.project, self.suite)
        target.write_text("# 旧规则夹具\n")
        result = upgrade(self.project, self.suite)
        target.write_text("# 升级后用户修改\n")
        with self.assertRaises(ValueError):
            restore(self.project, Path(result["transaction"]))
        self.assertEqual(target.read_text(), "# 升级后用户修改\n")

    def test_interrupted_restore_does_not_recreate_user_deleted_file(self):
        """没有 after 的中断事务也不得把用户删除误判为计划内变化。"""
        result = upgrade(self.project, self.suite)
        transaction = Path(result["transaction"])
        record = json.loads((transaction / "transaction.json").read_text())
        record.pop("after")
        record["status"] = "applying"
        atomic_json(transaction / "transaction.json", record)
        target = self.project / self.mappings[0][0]
        target.unlink()
        with self.assertRaises(ValueError):
            restore(self.project, transaction)
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
        result = upgrade(self.project, self.suite)
        transaction = Path(result["transaction"])
        record = json.loads((transaction / "transaction.json").read_text())
        record.pop("after")
        record["status"] = "applying"
        atomic_json(transaction / "transaction.json", record)
        restore(self.project, transaction)
        with lock(self.project / ".ai-project/framework-update.lock"):
            with self.assertRaises(ValueError):
                upgrade(self.project, self.suite)


if __name__ == "__main__":
    unittest.main(verbosity=2)

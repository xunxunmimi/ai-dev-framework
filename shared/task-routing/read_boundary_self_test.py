#!/usr/bin/env python3
"""读取边界的正反例回归；仅访问合成元数据目录，不使用真实打印机源码。"""
import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from read_boundary import ReadBoundary, BoundaryError, POLICY_PATH, canonical_relative
from task_runtime import atomic_json, observe_identity, begin, binding, capture_snapshot, confirmed_identity, verify_configuration, validate_report, run_verification, task_dir, resume


class ReadBoundaryScenarios(unittest.TestCase):
    """验证真正的读取行为、状态失效和部分报告，不用 marker 数量代替边界证明。"""

    def setUp(self):
        """创建两个允许配置及不可访问的文本样本；策略只授予必要控制文件。"""
        self.temporary = tempfile.TemporaryDirectory(prefix='read-boundary-fixture-')
        self.root = Path(self.temporary.name).resolve()
        self.config = 'Config/Demo.xcodeproj/project.pbxproj'
        self.scheme = 'Config/Demo.xcodeproj/xcshareddata/xcschemes/Demo.xcscheme'
        for name, text in [(self.config, 'PRODUCT_BUNDLE_IDENTIFIER = com.example.boundary;'), (self.scheme, '<Scheme/>'), ('Restricted/private.swift', '// 合成禁止内容'), ('AGENTS.md', '# 合成规则')]:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        self.policy = {'schemaVersion': '1.0', 'confirmed': True, 'projectRoot': str(self.root), 'mode': 'partial', 'scopeDescription': '只检查合成配置；不读取 Restricted 或未知子树', 'allow': [self.config, self.scheme, 'AGENTS.md', '.ai-project/**', '.codex-rules/**'], 'deny': ['Restricted/**']}
        self.write_policy()

    def tearDown(self):
        """仅清理本测试临时目录；不删除或更改真实项目。"""
        self.temporary.cleanup()

    def write_policy(self):
        """更新合成目录策略；只在测试中模拟授权配置变化。"""
        atomic_json(self.root / POLICY_PATH, self.policy)

    def start(self):
        """用真实共享实现建立部分范围身份和独立任务，返回任务状态。"""
        atomic_json(self.root / '.ai-project/identity.json', observe_identity(self.root, 'ios', {'bundleId': 'com.example.boundary', 'scheme': 'Demo'}))
        return begin(self.root, 'scope', ['AGENTS.md'], '合成只读配置演练')

    def test_missing_invalid_or_cross_project_policy_blocks(self):
        """无策略、未确认或跨项目根不能获得隐式全量读取权限。"""
        (self.root / POLICY_PATH).unlink()
        with self.assertRaises(BoundaryError): ReadBoundary(self.root)
        for update in ({'confirmed': False}, {'projectRoot': '/other-project'}, {'mode': 'complete'}):
            invalid = {**self.policy, **update}
            with self.assertRaises(BoundaryError): ReadBoundary(self.root, invalid)

    def test_denied_and_unknown_are_rejected_before_open(self):
        """未知与 deny 路径在任何打开前拒绝，不先验证内容是否有用。"""
        reader = ReadBoundary(self.root)
        with mock.patch('os.open', side_effect=AssertionError('不应打开任何文件')):
            for name in ('Restricted/private.swift', 'Unknown/new.swift', '.env', '.ssh/id_rsa'):
                with self.assertRaises(BoundaryError): reader.read_bytes(name)

    def test_partial_snapshot_never_enters_restricted_or_root_scan(self):
        """精确白名单只打开允许祖先，不枚举根或进入受限目录。"""
        original_open, original_scandir = os.open, os.scandir
        root_inode = self.root.stat().st_ino
        def guarded_open(path, *args, **kwargs):
            """禁止进入受限目录；若测试触发真实违规会直接失败。"""
            if str(path) == 'Restricted': raise AssertionError('进入受限目录')
            return original_open(path, *args, **kwargs)
        def guarded_scandir(path):
            """根枚举会暴露未知子树，部分精确白名单不应执行。"""
            if isinstance(path, int) and os.fstat(path).st_ino == root_inode: raise AssertionError('枚举项目根')
            return original_scandir(path)
        with mock.patch('os.open', side_effect=guarded_open), mock.patch('os.scandir', side_effect=guarded_scandir):
            captured = capture_snapshot(self.root)
        self.assertEqual(set(captured['paths']), {self.config, self.scheme, 'AGENTS.md'})
        self.assertEqual(captured['coverage']['status'], 'partial')
        self.assertIn('outside-allowlist', captured['coverage']['uninspected'])

    def test_path_normalization_and_globs_fail_closed(self):
        """拒绝父级跳转、绝对路径、别名分隔和含糊 glob；目录边界不误匹配。"""
        for name in ('../file', '/file', 'Config/../file', 'Config//file', './file', 'Config\\file'):
            with self.assertRaises(BoundaryError): canonical_relative(name)
        for entry in ('Config/*.swift', '../**', 'Config//**'):
            with self.assertRaises(BoundaryError): ReadBoundary(self.root, {**self.policy, 'allow': [entry]})
        reader = ReadBoundary(self.root, {**self.policy, 'allow': ['Config/**'], 'deny': ['Config/Secret/**']})
        self.assertFalse(reader.allowed('Config/Secret/file'))
        self.assertTrue(reader.allowed('Config/SecretSafe/file'))

    def test_denied_child_is_not_statted_or_entered(self):
        """允许目录内的 deny 子树在 stat 前剪枝，不能只在打开文件后过滤。"""
        (self.root / 'Allowed/Restricted').mkdir(parents=True)
        (self.root / 'Allowed/Restricted/private.swift').write_text('// 不可读样本')
        (self.root / 'Allowed/public.txt').write_text('public')
        reader = ReadBoundary(self.root, {**self.policy, 'allow': ['Allowed/**'], 'deny': ['Allowed/Restricted/**']})
        original = os.stat
        def guarded(path, *args, **kwargs):
            """一旦对 deny 子项查询类型就失败，验证权限判断的先后顺序。"""
            if path == 'Restricted': raise AssertionError('stat 受限子树')
            return original(path, *args, **kwargs)
        with mock.patch('os.stat', side_effect=guarded):
            self.assertEqual(reader.files(), ['Allowed/public.txt'])

    def test_capability_probe_uses_identical_boundary(self):
        """能力索引只摘要同一白名单配置，不能另走无边界递归探测。"""
        import importlib.util
        path = Path(__file__).resolve().parents[1] / 'project-detection/detect_project_capabilities.py'
        spec = importlib.util.spec_from_file_location('capability_probe', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        result = module.detect(self.root, 'ios')
        self.assertEqual(result['readCoverage']['status'], 'partial')
        self.assertNotIn('Restricted/private.swift', result['projectFiles'])
        self.assertIn(self.config, result['projectFiles'])

    def test_leaf_symlink_never_reads_denied_target(self):
        """允许文件名被换成指向受限内容的链接时立即阻塞，包括内部目标。"""
        path = self.root / self.config
        path.unlink()
        path.symlink_to(self.root / 'Restricted/private.swift')
        with self.assertRaises(BoundaryError): ReadBoundary(self.root).read_bytes(self.config)
        with self.assertRaises(BoundaryError): capture_snapshot(self.root)

    def test_parent_symlink_cannot_escape_or_alias(self):
        """祖先目录链接到项目内外均拒绝；不通过 resolve 消去链接后再授权。"""
        target = self.root / 'Alias'
        target.symlink_to(self.root / 'Config', target_is_directory=True)
        reader = ReadBoundary(self.root, {**self.policy, 'allow': ['Alias/**']})
        with self.assertRaises(BoundaryError): reader.read_bytes('Alias/Demo.xcodeproj/project.pbxproj')
        with self.assertRaises(BoundaryError): reader.files()

    def test_hardlink_and_fifo_are_not_read_as_regular_owned_files(self):
        """硬链接归属不明确和 FIFO 特殊类型均阻塞，不等待或读取流。"""
        path = self.root / self.config
        path.unlink()
        os.link(self.root / 'Restricted/private.swift', path)
        with self.assertRaises(BoundaryError): ReadBoundary(self.root).read_bytes(self.config)
        path.unlink()
        os.mkfifo(path)
        with self.assertRaises(BoundaryError): ReadBoundary(self.root).read_bytes(self.config)

    def test_link_swap_during_open_is_rejected(self):
        """模拟检查后替换为链接；O_NOFOLLOW 确保不会读取替换后的目标。"""
        reader = ReadBoundary(self.root)
        original = os.open
        swapped = False
        def swap(path, *args, **kwargs):
            """在叶子 open 前替换，验证读取本身的防护而非事前 exists。"""
            nonlocal swapped
            if path == 'project.pbxproj' and not swapped:
                swapped = True
                leaf = self.root / self.config
                leaf.unlink()
                leaf.symlink_to(self.root / 'Restricted/private.swift')
            return original(path, *args, **kwargs)
        with mock.patch('os.open', side_effect=swap):
            with self.assertRaises(BoundaryError): reader.read_bytes(self.config)

    def test_partial_report_cannot_be_forged_into_full_pass(self):
        """部分范围即使每项检查通过仍为 partial，默认完整证据入口拒绝。"""
        self.start()
        report = verify_configuration(self.root, 'scope')
        self.assertEqual(report['status'], 'partial')
        self.assertEqual(report['executionStatus'], 'passed')
        with self.assertRaises(ValueError): validate_report(self.root, report, 'scope', 'D0')
        validate_report(self.root, report, 'scope', 'D0', allow_partial=True)
        forged = copy.deepcopy(report)
        forged['status'] = 'passed'
        with self.assertRaises(ValueError): validate_report(self.root, forged, 'scope', 'D0', allow_partial=True)
        self.assertEqual(resume(self.root, 'scope')['completion']['automatedTests'], 'not-run')

    def test_policy_change_invalidates_identity_and_evidence(self):
        """改变允许/拒绝范围后必须重新核对，旧身份和旧报告都不能继续使用。"""
        self.start()
        report = verify_configuration(self.root, 'scope')
        self.policy['deny'].append('FutureRestricted/**')
        self.write_policy()
        with self.assertRaises(ValueError): confirmed_identity(self.root)
        with self.assertRaises(ValueError): validate_report(self.root, report, 'scope', 'D0', allow_partial=True)

    def test_report_coverage_and_check_omissions_are_rejected(self):
        """报告不能隐藏未检查范围，也不能删除检查项后仍被接受。"""
        self.start()
        report = verify_configuration(self.root, 'scope')
        for key, value in [('coverage', {'status': 'complete'}), ('checks', [])]:
            forged = copy.deepcopy(report)
            forged[key] = value
            with self.assertRaises(ValueError): validate_report(self.root, forged, 'scope', 'D0', allow_partial=True)
        with self.assertRaises(ValueError): verify_configuration(self.root, 'scope', 'D4')

    def test_blocked_cli_reports_missing_required_configuration(self):
        """必要身份文件不可读时输出 blocked 及范围说明，不把缺项当不适用。"""
        self.policy['deny'].append(self.config)
        self.write_policy()
        runtime = Path(__file__).with_name('task_runtime.py')
        result = subprocess.run([sys.executable, '-B', str(runtime), 'identity', '--project', str(self.root), '--platform', 'ios'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)['status'], 'blocked')
        self.assertEqual(json.loads(result.stdout)['readCoverage']['status'], 'blocked')

    def test_external_command_is_blocked_before_subprocess(self):
        """外部编译/测试不能由本白名单约束；部分模式在启动前拒绝。"""
        self.start()
        argv = [sys.executable, '-c', 'raise AssertionError("不得执行")']
        atomic_json(self.root / '.ai-project/verification-commands.json', {'commands': [{'confirmed': True, 'kind': 'automatedTests', 'profiles': ['D0'], 'argv': argv}]})
        with mock.patch('task_runtime.subprocess.run', side_effect=AssertionError('执行了外部命令')):
            with self.assertRaises(BoundaryError): run_verification(self.root, 'scope', 'D0', 'automatedTests', argv, task_dir(self.root, 'scope') / 'test.json')

    def test_control_state_symlink_is_also_rejected(self):
        """控制文件不能借链接绕过读取边界，身份 JSON 同样受保护。"""
        self.start()
        identity = self.root / '.ai-project/identity.json'
        identity.unlink()
        identity.symlink_to(self.root / 'Restricted/private.swift')
        with self.assertRaises(BoundaryError): confirmed_identity(self.root)

    def test_complete_scope_has_explicit_full_report(self):
        """明确授予合成目录完整范围时可产生 passed，同时记录标准排除项。"""
        self.policy.update(mode='complete', allow=['**'], deny=[], scopeDescription='合成全范围，含 Restricted 文本样本')
        self.write_policy()
        self.start()
        report = verify_configuration(self.root, 'scope')
        self.assertEqual(report['status'], 'passed')
        validate_report(self.root, report, 'scope', 'D0')
        self.assertIn('credential-name-exclusions', report['coverage']['uninspected'])

    def test_complete_snapshot_skips_credential_names_before_stat_open_or_hash(self):
        """合成凭据不被 stat、打开或加入摘要；正常业务文件仍被读取。"""
        self.policy.update(mode='complete', allow=['**'], deny=[], scopeDescription='合成完整范围')
        self.write_policy()
        names = ['.test/credentials.local.json', '.test/CREDENTIALS.LOCAL.JSON', '.test/client_secret.json', '.test/api-key.dev.json', '.test/.env.production', '.test/signing.p8', '.test/secrets/password.txt']
        for name in names:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('synthetic secret')
        (self.root / '.test/public.json').write_text('ordinary data')
        original_stat, original_open = os.stat, os.open
        forbidden = {'credentials.local.json', 'CREDENTIALS.LOCAL.JSON', 'client_secret.json', 'api-key.dev.json', '.env.production', 'signing.p8', 'secrets'}
        def check_stat(path, *args, **kwargs):
            if str(path) in forbidden: raise AssertionError('stat credential')
            return original_stat(path, *args, **kwargs)
        def check_open(path, *args, **kwargs):
            if str(path) in forbidden: raise AssertionError('open credential')
            return original_open(path, *args, **kwargs)
        with mock.patch('os.stat', side_effect=check_stat), mock.patch('os.open', side_effect=check_open):
            result = capture_snapshot(self.root)
        self.assertIn('.test/public.json', result['paths'])
        self.assertFalse(set(names) & set(result['paths']))
        self.assertEqual(result['coverage']['status'], 'complete')
        self.assertIn('not inspected', result['coverage']['credentialExclusionPolicy'])
        with self.assertRaises(BoundaryError): ReadBoundary(self.root).read_bytes('.test/credentials.local.json')

    def test_local_configuration_skipped_before_stat_open_hash_in_complete_scope(self):
        """同名合成配置及本机配置目录先按名字剪枝，完整范围如实标注。"""
        self.policy.update(mode='complete', allow=['**'], deny=[], scopeDescription='合成完整范围')
        self.write_policy()
        names = ['entry/src/main/resources/rawfile/sample_app_account.local.json', 'entry/src/main/resources/rawfile/sample_app_account.dev.local.json', 'entry/src/main/resources/rawfile/SAMPLE_APP_ACCOUNT.DEV.LOCAL.JSON', 'entry/src/main/resources/rawfile/profile.local/nested.json']
        for name in names:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('synthetic-sensitive-config')
        normal = self.root / 'entry/src/main/resources/rawfile/public.json'
        normal.write_text('ordinary')
        forbidden = {'sample_app_account.local.json', 'sample_app_account.dev.local.json', 'SAMPLE_APP_ACCOUNT.DEV.LOCAL.JSON', 'profile.local'}
        stat0, open0 = os.stat, os.open
        def stat_guard(path, *args, **kwargs):
            if str(path) in forbidden: raise AssertionError('stat local config')
            return stat0(path, *args, **kwargs)
        def open_guard(path, *args, **kwargs):
            if str(path) in forbidden: raise AssertionError('open local config')
            return open0(path, *args, **kwargs)
        with mock.patch('os.stat', side_effect=stat_guard), mock.patch('os.open', side_effect=open_guard):
            snap = capture_snapshot(self.root)
        self.assertFalse(set(names) & set(snap['paths']))
        self.assertIn(normal.relative_to(self.root).as_posix(), snap['paths'])
        self.assertEqual(snap['coverage']['status'], 'complete')
        self.assertIn('credential-name-policy-v3', snap['coverage']['standardExclusions'])
        self.assertIn('credential-name-exclusions', snap['coverage']['uninspected'])
        reader = ReadBoundary(self.root)
        for name in names:
            with self.assertRaises(BoundaryError): reader.read_bytes(name)

    def test_local_configuration_links_cannot_be_aliased(self):
        self.policy.update(mode='complete', allow=['**'], deny=[], scopeDescription='合成完整范围')
        self.write_policy()
        target = self.root / 'sample_app_account.local.json'
        target.write_text('synthetic-sensitive-config')
        alias = self.root / 'ordinary.json'
        alias.symlink_to(target)
        with self.assertRaises(BoundaryError): capture_snapshot(self.root)
        alias.unlink()
        os.link(target, alias)
        with self.assertRaises(BoundaryError): capture_snapshot(self.root)
        alias.unlink()
        target.unlink()
        target.symlink_to(self.root / 'Restricted/private.swift')
        self.assertNotIn(target.name, capture_snapshot(self.root)['paths'])

    def test_policy_v2_report_cannot_be_reused_for_v3(self):
        self.start()
        report = verify_configuration(self.root, 'scope')
        report['binding']['readCoverage']['credentialExclusionPolicy'] = 'name-policy-v2; excluded paths are not inspected; unnamed secrets are not detectable'
        with self.assertRaises(ValueError): validate_report(self.root, report, 'scope', 'D0', allow_partial=True)

    def test_link_escape_and_hardlink_are_blocked_even_in_complete_mode(self):
        self.policy.update(mode='complete', allow=['**'], deny=[], scopeDescription='合成完整范围')
        self.write_policy()
        target = self.root / 'Restricted/private.swift'
        alias = self.root / 'ordinary.json'
        alias.symlink_to(target)
        with self.assertRaises(BoundaryError): capture_snapshot(self.root)
        alias.unlink()
        os.link(target, alias)
        with self.assertRaises(BoundaryError): capture_snapshot(self.root)

    def test_old_coverage_cannot_validate_after_exclusion_policy_change(self):
        self.start()
        report = verify_configuration(self.root, 'scope')
        report['coverage'].pop('credentialExclusionPolicy')
        with self.assertRaises(ValueError): validate_report(self.root, report, 'scope', 'D0', allow_partial=True)

    def test_partial_installer_only_adds_allowed_control_files(self):
        """真实安装流程仅写必要配置；业务样本摘要保持不变，索引明确部分范围。"""
        suite = Path(__file__).resolve().parents[2]
        before = (self.root / 'Restricted/private.swift').read_bytes()
        result = subprocess.run(['bash', str(suite / 'installers/setup-project.sh'), 'ios-daily', str(self.root)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((self.root / 'Restricted/private.swift').read_bytes(), before)
        self.assertEqual(json.loads((self.root / '.ai-project/detected-capabilities.json').read_text())['readCoverage']['status'], 'partial')
        self.start()
        report = verify_configuration(self.root, 'scope')
        self.assertEqual(report['status'], 'partial')


if __name__ == '__main__':
    unittest.main(verbosity=2)

#!/usr/bin/env python3
"""项目/任务范围隔离回归；只读写合成文本，不触及真实业务或平台拒绝操作。"""
import copy
import json
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

from read_boundary import ReadBoundary, BoundaryError, task_read_scope
from task_runtime import atomic_json, observe_identity, begin, checkpoint, resume, verify_configuration, validate_report, run_verification, task_dir


class TaskScopeScenarios(unittest.TestCase):
    """临时控制范围不成为项目禁读；项目真实约束与平台操作暂停不被任务解除。"""

    def setUp(self):
        """建立有明确长期授权的合成项目，身份只读取 Package 标记。"""
        self.temporary = tempfile.TemporaryDirectory(prefix='task-scope-')
        self.root = Path(self.temporary.name).resolve()
        for name in ('Package.swift', 'one.txt', 'two.txt'):
            (self.root / name).write_text('// 合成样本 ' + name)
        self.project = {'schemaVersion': '1.0', 'confirmed': True, 'scopeKind': 'project', 'projectRoot': str(self.root), 'mode': 'complete', 'allow': ['**'], 'deny': [], 'scopeDescription': '用户明确授权的合成长期项目范围', 'identityFiles': ['Package.swift'], 'identityGitMode': 'unverified'}
        self.write_project()

    def tearDown(self):
        """删除仅本用例拥有的临时目录。"""
        self.temporary.cleanup()

    def write_project(self):
        """写合成项目策略和身份，用于明确模拟策略授权变化。"""
        atomic_json(self.root / '.ai-project/read-policy.json', self.project)
        atomic_json(self.root / '.ai-project/identity.json', observe_identity(self.root, 'ios'))

    def policy(self, task, file):
        """返回只允许当前样本及控制状态的临时任务范围。"""
        return {'schemaVersion': '1.0', 'confirmed': True, 'scopeKind': 'task', 'taskId': task, 'projectRoot': str(self.root), 'mode': 'partial', 'allow': ['Package.swift', file, '.ai-project/**'], 'deny': [], 'scopeDescription': '仅当前合成任务，结束不改项目授权'}

    def start(self, task='one', file='one.txt'):
        """以独立范围开始任务，不将其策略写回项目文件。"""
        return begin(self.root, task, [file], '合成配置检查', read_policy=self.policy(task, file))

    def test_temporary_range_does_not_change_shared_project(self):
        """初始化和关闭均不改共享策略，新对话可按项目授权读取其他文件。"""
        before = (self.root / '.ai-project/read-policy.json').read_bytes()
        self.start()
        checkpoint(self.root, 'one', [], [], [], '完成', 'closed')
        self.assertEqual((self.root / '.ai-project/read-policy.json').read_bytes(), before)
        self.assertTrue(ReadBoundary(self.root).allowed('two.txt'))
        self.assertEqual(ReadBoundary(self.root).coverage()['scopeKind'], 'project')

    def test_parallel_tasks_have_distinct_readers_and_baselines(self):
        """两个并行任务读取范围独立，线程上下文不会相互串扰。"""
        first = self.start('one', 'one.txt')
        second = self.start('two', 'two.txt')
        self.assertNotIn('two.txt', first['baseline'])
        self.assertNotIn('one.txt', second['baseline'])
        def read(task, own, other):
            """在任务上下文读本任务文件，并确认别的文件未获本任务授权。"""
            with task_read_scope(self.root, task):
                reader = ReadBoundary(self.root)
                reader.read_text(own)
                with self.assertRaises(BoundaryError): reader.read_text(other)
                return reader.coverage()['taskId']
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda args: read(*args), [('one', 'one.txt', 'two.txt'), ('two', 'two.txt', 'one.txt')]))
        self.assertEqual(results, ['one', 'two'])

    def test_resume_reloads_only_explicit_task_scope(self):
        """显式恢复旧任务使用其范围，普通入口不猜测最近任务。"""
        self.start()
        self.assertEqual(resume(self.root, 'one')['readCoverage']['scopeKind'], 'task')
        self.assertEqual(ReadBoundary(self.root).policy['mode'], 'complete')
        with task_read_scope(self.root, 'one'):
            self.assertFalse(ReadBoundary(self.root).allowed('two.txt'))

    def test_closed_task_does_not_block_new_task_same_file(self):
        """关闭后释放编辑范围，新任务不继承前一任务的临时白名单。"""
        self.start()
        checkpoint(self.root, 'one', [], [], [], '关闭', 'closed')
        state = begin(self.root, 'next', ['one.txt'], '新的普通合成工作')
        self.assertEqual(state['readCoverage']['scopeKind'], 'project')
        self.assertIn('two.txt', state['baseline'])

    def test_project_real_denial_survives_task_allow_all(self):
        """项目明确的真实禁止项仍有效，任务完整范围不能放宽项目边界。"""
        (self.root / 'forbidden').mkdir()
        (self.root / 'forbidden/secret.txt').write_text('合成拒绝样本')
        self.project.update(mode='partial', deny=['forbidden/**'])
        self.write_project()
        policy = self.policy('one', 'one.txt')
        policy.update(mode='complete', allow=['**'])
        begin(self.root, 'one', ['one.txt'], '继承项目真实限制', read_policy=policy)
        with task_read_scope(self.root, 'one'):
            with self.assertRaises(BoundaryError): ReadBoundary(self.root).read_text('forbidden/secret.txt')
        checkpoint(self.root, 'one', [], [], [], '关闭', 'closed')
        self.assertFalse(ReadBoundary(self.root).allowed('forbidden/secret.txt'))

    def test_missing_task_policy_never_falls_back_to_project(self):
        """任务策略丢失阻塞恢复，不自动退回较宽项目授权。"""
        self.start()
        (self.root / '.ai-project/tasks/one/read-policy.json').unlink()
        with self.assertRaises(BoundaryError): resume(self.root, 'one')

    def test_changed_task_policy_invalidates_old_evidence(self):
        """修改任务范围后，旧基线和报告不能继续算通过。"""
        self.start()
        report = verify_configuration(self.root, 'one')
        changed = self.policy('one', 'one.txt')
        changed['allow'].append('two.txt')
        atomic_json(self.root / '.ai-project/tasks/one/read-policy.json', changed)
        with self.assertRaises(ValueError): resume(self.root, 'one')
        with self.assertRaises(ValueError): validate_report(self.root, report, 'one', 'D0', allow_partial=True)

    def test_scope_restores_after_exception(self):
        """异常离开也恢复上下文，后续对话不会遗留临时范围。"""
        try:
            with task_read_scope(self.root, 'temporary', self.policy('temporary', 'one.txt')):
                self.assertFalse(ReadBoundary(self.root).allowed('two.txt'))
                raise RuntimeError('合成异常')
        except RuntimeError:
            pass
        self.assertTrue(ReadBoundary(self.root).allowed('two.txt'))

    def test_partial_evidence_stays_partial_after_close(self):
        """关闭任务不会把部分验证升级成完整通过，历史覆盖如实保留。"""
        self.start()
        report = verify_configuration(self.root, 'one')
        self.assertEqual(report['status'], 'partial')
        with self.assertRaises(ValueError): validate_report(self.root, report, 'one', 'D0')
        checkpoint(self.root, 'one', [], [], [], '关闭', 'closed')
        self.assertEqual(resume(self.root, 'one')['completion']['configuration']['status'], 'partial')

    def test_legacy_project_policy_is_not_silently_widened(self):
        """兼容既有无 scopeKind 项目策略，不静默删除其明确 deny。"""
        legacy = {**self.project, 'mode': 'partial', 'deny': ['forbidden/**']}
        legacy.pop('scopeKind')
        atomic_json(self.root / '.ai-project/read-policy.json', legacy)
        reader = ReadBoundary(self.root)
        self.assertEqual(reader.coverage()['scopeKind'], 'legacy-project')
        self.assertFalse(reader.allowed('forbidden/file'))

    def test_task_policy_cannot_be_installed_as_project_policy(self):
        """不能再次把临时任务策略装进共享项目入口。"""
        with self.assertRaises(BoundaryError): ReadBoundary(self.root, self.policy('one', 'one.txt'))

    def test_supplied_policy_cannot_replace_published_task_scope(self):
        """内存中的较宽策略不能替换已发布任务文件，防止恢复时绕过范围。"""
        self.start()
        changed = self.policy('one', 'one.txt')
        changed['allow'].append('two.txt')
        with task_read_scope(self.root, 'one', changed):
            with self.assertRaises(BoundaryError): ReadBoundary(self.root)

    def test_missing_policy_cannot_be_replaced_from_memory(self):
        """删除旧任务文件后也不能用新参数绕过恢复，必须保留阻塞。"""
        self.start()
        (self.root / '.ai-project/tasks/one/read-policy.json').unlink()
        with task_read_scope(self.root, 'one', self.policy('one', 'one.txt')):
            with self.assertRaises(BoundaryError): ReadBoundary(self.root)

    def test_installer_rejects_task_policy_and_preserves_project(self):
        """真正的安装入口拒绝临时策略，不能再次持久化成项目约束。"""
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'installers'))
        from bootstrap_read_policy import bootstrap
        path = self.root / '.ai-project/task-input.json'
        atomic_json(path, self.policy('one', 'one.txt'))
        before = (self.root / '.ai-project/read-policy.json').read_bytes()
        with self.assertRaises(BoundaryError): bootstrap(self.root, 'ios', path)
        self.assertEqual((self.root / '.ai-project/read-policy.json').read_bytes(), before)

    def test_operation_hold_does_not_ban_source_path(self):
        """操作暂停独立于路径，普通文本读取可用，已暂停操作不能执行。"""
        atomic_json(self.root / '.ai-project/operation-holds.json', {'operations': [{'operationId': 'historical-validation', 'status': 'hold', 'pathRestrictions': [], 'exactCommandKnown': False}]})
        self.assertTrue(ReadBoundary(self.root).allowed('one.txt'))
        begin(self.root, 'ordinary', ['one.txt'], '普通合成任务')
        argv = [sys.executable, '-c', 'pass']
        def contract(operation):
            """精确命令确认仍与用途及历史暂停分别核对。"""
            return {'confirmed': True, 'kind': 'compiled', 'profiles': ['D2'], 'argv': argv, 'operationId': operation}
        for operation in [None, 'historical-validation']:
            atomic_json(self.root / '.ai-project/verification-commands.json', {'commands': [contract(operation)]})
            with mock.patch('task_runtime.subprocess.run', side_effect=AssertionError('不应执行历史拒绝操作')):
                with self.assertRaises(ValueError): run_verification(self.root, 'ordinary', 'D2', 'compiled', argv, task_dir(self.root, 'ordinary') / 'check.json')
        atomic_json(self.root / '.ai-project/verification-commands.json', {'commands': [contract('ordinary-fixture-check')]})
        report = run_verification(self.root, 'ordinary', 'D2', 'compiled', argv, task_dir(self.root, 'ordinary') / 'check.json')
        self.assertEqual(report['status'], 'passed')


if __name__ == '__main__':
    unittest.main(verbosity=2)

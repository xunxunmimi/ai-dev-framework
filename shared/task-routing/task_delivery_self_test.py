#!/usr/bin/env python3
"""代码交付与精确组合确认回归；全部使用临时文本工程，不接触真实项目。"""
import copy
import json
import multiprocessing
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import task_runtime as runtime
from lifecycle_protocol_self_test import fixture, git, state

PBX = 'App.xcodeproj/project.pbxproj'
UI = 'App.xcworkspace/xcuserdata/synthetic.xcuserdatad/UserInterfaceState.xcuserstate'


def inherit_worker(root, task_id, event, queue):
    """两个真实进程竞争已交付的同一文件，最多一个可取得新所有权。"""
    event.wait(5)
    try:
        runtime.begin(Path(root), task_id, ['one.swift'], 'synthetic successor', from_delivery='task', confirmed_by='reviewer', authorization_source='synthetic explicit handoff')
        queue.put(True)
    except (ValueError, OSError):
        queue.put(False)


class DeliveryScenarios(unittest.TestCase):
    """验证所有权、历史证据及真实磁盘不变量，不用接口返回值代替状态检查。"""

    def setUp(self):
        """完整授权只适用于临时 Git 工程；UI 状态初始随合成文件一起冻结。"""
        self.tmp = tempfile.TemporaryDirectory(prefix='task-delivery-')
        self.addCleanup(self.tmp.cleanup)
        self.root = fixture(Path(self.tmp.name).resolve() / 'project', with_git=True)
        path = self.root / UI; path.parent.mkdir(parents=True); path.write_bytes(b'original-ui')
        git(self.root, 'add', '.'); git(self.root, 'commit', '-qm', 'synthetic UI')

    def start(self, files=None):
        """使用真实 init 建立不可变基线；不在测试中伪造交付记录。"""
        return runtime.begin(self.root, 'task', files or ['one.swift'], 'synthetic code task')

    def move(self, action='complete-code', apply=False, token=None, **kwargs):
        """显式授权的预览/应用参数保持一致，允许对组合确认传递精确路径。"""
        return runtime.lifecycle_transition(self.root, 'task', action, 'operation-1', 'synthetic explicit decision', 'reviewer', 'synthetic authorization', apply=apply, preview_hash=token, **kwargs)

    def deliver(self):
        """只有保存过当前检查点的代码才可声明交付，验收项保持待用户处理。"""
        (self.root / 'one.swift').write_text('// synthetic delivered code\n')
        runtime.checkpoint(self.root, 'task', ['code prepared'], ['real-device acceptance'], [], 'wait for acceptance')
        preview = self.move()
        return self.move(apply=True, token=preview['previewSha256'])

    def inherit(self, task='next', **kwargs):
        """继续开发必须新任务和明确的交付关联，不由 closed 自动继承用户修改。"""
        return runtime.begin(self.root, task, ['one.swift'], 'synthetic successor', from_delivery='task', confirmed_by='reviewer', authorization_source='synthetic explicit handoff', **kwargs)

    def test_delivery_releases_code_preserves_acceptance_and_original_baseline(self):
        self.start(); original = (self.root / '.ai-project/tasks/task/baseline.json').read_bytes()
        self.deliver(); delivered = state(self.root)
        self.assertEqual(delivered['status'], 'closed')
        self.assertEqual(delivered['completion']['realDevice'], 'waiting-user')
        self.assertEqual(delivered['todo'], ['real-device acceptance'])
        self.assertEqual(delivered['codeDelivery']['snapshot']['one.swift'], runtime.file_hash(self.root / 'one.swift'))
        self.assertEqual((self.root / '.ai-project/tasks/task/baseline.json').read_bytes(), original)
        self.assertTrue(runtime.resume(self.root, 'task')['historical'])
        with self.assertRaises(ValueError): runtime.checkpoint(self.root, 'task', [], [], [], 'reopen')
        with self.assertRaises(ValueError): runtime.binding(self.root, 'task', 'D2')
        old_state = (self.root / '.ai-project/tasks/task/state.json').read_bytes()
        self.assertTrue(self.move(apply=True, token=self.move()['previewSha256'])['replayed'])
        self.assertEqual((self.root / '.ai-project/tasks/task/state.json').read_bytes(), old_state)

    def test_default_init_still_rejects_dirty_delivery_but_explicit_handoff_succeeds(self):
        self.start(); self.deliver(); before = copy.deepcopy(state(self.root))
        with self.assertRaises(ValueError): runtime.begin(self.root, 'unreviewed', ['one.swift'], 'not an explicit handoff')
        successor = self.inherit()
        self.assertEqual(successor['status'], 'active')
        self.assertEqual(successor['completion']['compiled'], 'not-run')
        self.assertEqual(successor['completion']['realDevice'], 'waiting-user')
        self.assertEqual(successor['predecessorDelivery']['taskId'], 'task')
        self.assertEqual(state(self.root), before)
        with self.assertRaises(ValueError): self.inherit('competing')

    def test_delivery_requires_active_checkpoint_and_no_execution_lease(self):
        self.start(); original = state(self.root)
        for status in ('blocked', 'waiting-permission'):
            modified = copy.deepcopy(original); modified['status'] = status
            runtime.atomic_json(self.root / '.ai-project/tasks/task/state.json', modified)
            with self.assertRaises(ValueError): self.move()
            self.assertEqual(state(self.root), modified)
        runtime.atomic_json(self.root / '.ai-project/tasks/task/state.json', original)
        (self.root / 'one.swift').write_text('unreviewed source')
        with self.assertRaises(ValueError): self.move()
        runtime.checkpoint(self.root, 'task', [], [], [], 'checkpoint')
        modified = state(self.root); modified['pendingExecutions'] = {'synthetic': {'runnerPid': 1}}
        runtime.atomic_json(self.root / '.ai-project/tasks/task/state.json', modified)
        with self.assertRaises(ValueError): self.move()
        self.assertEqual(state(self.root), modified)

    def test_held_verification_lock_blocks_delivery(self):
        self.start()
        with runtime.project_lock(self.root, '.ai-project/tasks/task/verification.lock'):
            with self.assertRaises((ValueError, OSError)): self.move()
        self.assertEqual(state(self.root)['status'], 'active')

    def test_preview_drift_rejects_release_without_state_write(self):
        self.start(); preview = self.move(); before = state(self.root)
        (self.root / 'one.swift').write_text('changed after preview')
        with self.assertRaises(ValueError): self.move(apply=True, token=preview['previewSha256'])
        self.assertEqual(state(self.root), before)

    def test_successor_rejects_drift_and_plain_closed_record(self):
        self.start(); self.deliver()
        (self.root / 'one.swift').write_text('changed after delivery')
        with self.assertRaises(ValueError): self.inherit()
        current = state(self.root); current.pop('codeDelivery')
        runtime.atomic_json(self.root / '.ai-project/tasks/task/state.json', current)
        with self.assertRaises(ValueError): self.inherit()

    def test_successor_cannot_expand_scope_or_omit_authorization(self):
        self.start(); self.deliver()
        with self.assertRaises(ValueError): runtime.begin(self.root, 'no-auth', ['one.swift'], 'synthetic', from_delivery='task')
        with self.assertRaises(ValueError): runtime.begin(self.root, 'larger', ['one.swift', 'two.swift'], 'synthetic', from_delivery='task', confirmed_by='reviewer', authorization_source='synthetic')
        self.assertFalse((self.root / '.ai-project/tasks/larger').exists())

    def test_partial_delivery_receipt_cannot_authorize_successor(self):
        self.start(); self.deliver(); original = state(self.root)
        for key in ('operationId', 'confirmedBy', 'authorizationSource', 'reason', 'at', 'rulesIdentity', 'snapshot'):
            with self.subTest(missing=key):
                partial = copy.deepcopy(original); partial['codeDelivery'].pop(key)
                runtime.atomic_json(self.root / '.ai-project/tasks/task/state.json', partial)
                with self.assertRaises(ValueError): self.inherit('incomplete-' + key)
        runtime.atomic_json(self.root / '.ai-project/tasks/task/state.json', original)
        self.inherit()

    def test_successor_checks_revision_and_root(self):
        self.start(); self.deliver()
        git(self.root, 'add', 'one.swift'); git(self.root, 'commit', '-qm', 'synthetic revision change')
        with self.assertRaises(ValueError): self.inherit()

    def test_concurrent_successors_get_only_one_owner(self):
        self.start(); self.deliver()
        context = multiprocessing.get_context('spawn'); event = context.Event(); queue = context.Queue()
        workers = [context.Process(target=inherit_worker, args=(str(self.root), name, event, queue)) for name in ('a', 'b')]
        for worker in workers: worker.start()
        event.set(); results = [queue.get(timeout=15) for _ in workers]
        for worker in workers: worker.join(15); self.assertFalse(worker.is_alive())
        self.assertEqual(sorted(results), [False, True])

    def composite(self):
        """PBX 与外层 UI 先同时变化，使用同一次显式预览而不是不存在的顺序。"""
        self.start(['one.swift', PBX]); before = state(self.root)
        (self.root / PBX).write_text((self.root / PBX).read_text() + '// same target PBX change\n')
        (self.root / UI).write_bytes(b'explicit-new-ui')
        ack = {'path': UI, 'oldSha256': before['baseline'][UI], 'newSha256': runtime.file_hash(self.root / UI)}
        return ack

    def test_simultaneous_pbx_and_outer_ui_confirmed_in_one_transaction(self):
        ack = self.composite(); before = state(self.root); baseline = (self.root / '.ai-project/tasks/task/baseline.json').read_bytes()
        with self.assertRaises(ValueError): self.move('continue', context_files=[PBX])
        preview = self.move('continue', context_files=[PBX], metadata_ack=ack)
        self.assertEqual(state(self.root), before)
        self.move('continue', apply=True, token=preview['previewSha256'], context_files=[PBX], metadata_ack=ack)
        current = state(self.root)
        self.assertEqual(current['baseline'], before['baseline'])
        self.assertEqual((self.root / '.ai-project/tasks/task/baseline.json').read_bytes(), baseline)
        self.assertIn(PBX, current['acknowledgedContext']); self.assertIn(UI, current['acknowledgedMetadata'])
        self.assertEqual(current['generation'], before['generation'] + 1)
        runtime.check_scope(self.root, current)

    def test_composite_rejects_unacknowledged_source_and_changed_preview(self):
        ack = self.composite(); preview = self.move('continue', context_files=[PBX], metadata_ack=ack); before = state(self.root)
        (self.root / 'two.swift').write_text('unowned source drift')
        with self.assertRaises(ValueError): self.move('continue', apply=True, token=preview['previewSha256'], context_files=[PBX], metadata_ack=ack)
        self.assertEqual(state(self.root), before)
        git(self.root, 'restore', '--', 'two.swift')
        (self.root / UI).write_bytes(b'third-ui-drift')
        with self.assertRaises(ValueError): self.move('continue', apply=True, token=preview['previewSha256'], context_files=[PBX], metadata_ack=ack)
        self.assertEqual(state(self.root), before)

    def test_composite_rejects_real_target_change(self):
        ack = self.composite(); before = state(self.root)
        (self.root / PBX).write_text('PRODUCT_BUNDLE_IDENTIFIER = synthetic.other;\n')
        with self.assertRaises(ValueError): self.move('continue', context_files=[PBX], metadata_ack=ack)
        self.assertEqual(state(self.root), before)

    def test_cli_preview_apply_and_terminal_guard(self):
        self.start()
        command = [sys.executable, '-B', str(Path(runtime.__file__)), 'complete-code', '--project', str(self.root), '--task-id', 'task', '--operation-id', 'cli-delivery', '--reason', 'synthetic code decision', '--confirmed-by', 'reviewer', '--authorization-source', 'synthetic approval']
        preview = subprocess.run(command, capture_output=True, text=True, check=True)
        token = json.loads(preview.stdout)['previewSha256']
        self.assertEqual(state(self.root)['status'], 'active')
        applied = subprocess.run(command + ['--apply', '--preview-sha256', token], capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(applied.stdout)['status'], 'code-delivered')
        self.assertEqual(state(self.root)['completion']['realDevice'], 'waiting-user')
        with self.assertRaises(ValueError): runtime.checkpoint(self.root, 'task', [], [], [], 'reopen')


if __name__ == '__main__':
    unittest.main(verbosity=2)

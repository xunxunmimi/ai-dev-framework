#!/usr/bin/env python3
"""任务控制场景回归；只使用临时文本/Git 元数据夹具，不创建或运行业务候选工程。"""
import importlib.util
import json
import subprocess
import sys
import tempfile
import os
import hashlib
import uuid
from datetime import datetime, timedelta, timezone
import unittest
from unittest import mock
from pathlib import Path

from task_runtime import (atomic_json, begin, binding, checkpoint, confirmed_identity, lock, observe_identity, read_json, resume, run_verification, task_dir, validate_report, check_scope, acknowledge_metadata)
from task_router import classify, status_paths
from framework_router import route
from read_boundary import BoundaryError, issue_external_permit, read_external_once

# 本文件可从源码或待应用差异目录运行；测试日志注明实际脚本路径。
ROUTER = Path(__file__).with_name("task_router.py")


def git(root, *args):
    """在隔离夹具执行 Git 命令；身份只用命令局部配置，不改用户全局设置。"""
    return subprocess.run(["git", "-C", str(root), "-c", "user.name=Framework Test", "-c", "user.email=test@example.invalid", *args], capture_output=True, text=True, check=True)


def fixture(root):
    """建立最小元数据与文本样本；Package.swift 只是身份探测标记，不是可构建 App。"""
    root.mkdir()
    (root / "Package.swift").write_text("// 仅测试身份探测的标记\n")
    (root / "one.swift").write_text("// 自有函数样本 A\n")
    (root / "two.swift").write_text("// 自有函数样本 B\n")
    (root / ".gitignore").write_text(".ai-project/\nPods/\n")
    (root / "Pods").mkdir()
    (root / "Pods/shared.swift").write_text("// 只读公共库样本\n")
    git(root, "init", "-q", "-b", "main")
    git(root, "add", ".")
    git(root, "commit", "-qm", "metadata fixture")
    (root / ".ai-project").mkdir(exist_ok=True)
    (root / ".ai-project/read-policy.json").write_text(json.dumps({"schemaVersion":"1.0","confirmed":True,"projectRoot":str(root.resolve()),"mode":"complete","allow":["**"],"deny":[],"scopeDescription":"合成测试目录明确授权；不涉及真实业务"}))
    atomic_json(root / ".ai-project/identity.json", observe_identity(root, "ios"))
    return root


class RuntimeScenarios(unittest.TestCase):
    """覆盖状态中断、任务串扰、禁改范围与证据失效的行为，不以文本 marker 代替测试。"""

    def setUp(self):
        """为每个测试提供隔离临时目录，避免跨用例状态泄漏。"""
        self.temporary = tempfile.TemporaryDirectory(prefix="framework-control-")
        self.base = Path(self.temporary.name)
        self.root = fixture(self.base / "metadata")

    def tearDown(self):
        """删除本测试临时夹具；不接触真实业务目录。"""
        self.temporary.cleanup()

    def test_exact_metadata_acknowledgement(self):
        import hashlib
        name = 'Demo.xcodeproj/project.xcworkspace/xcuserdata/person.xcuserdatad/UserInterfaceState.xcuserstate'
        file = self.root / name
        file.parent.mkdir(parents=True)
        file.write_bytes(b'old metadata')
        old = hashlib.sha256(file.read_bytes()).hexdigest()
        state = begin(self.root, 'metadata', ['one.swift'], 'fixture')
        begin(self.root, 'other-task', ['two.swift'], 'independent fixture')
        file.write_bytes(b'new metadata')
        new = hashlib.sha256(file.read_bytes()).hexdigest()
        auth = {'confirmed_by':'fixture reviewer', 'authorization_source':'synthetic user approval'}
        with self.assertRaises(ValueError): acknowledge_metadata(self.root, 'metadata', name, old, new, 'fixture')
        with self.assertRaises(ValueError): acknowledge_metadata(self.root, 'metadata', name, '0'*64, new, 'fixture', **auth)
        with self.assertRaises(ValueError): acknowledge_metadata(self.root, 'metadata', name, old, '0'*64, 'fixture', **auth)
        with self.assertRaises(ValueError): acknowledge_metadata(self.root, 'metadata', 'wrong.txt', old, new, 'fixture', **auth)
        (self.root / 'third.swift').write_text('unowned outside change')
        with self.assertRaises(ValueError): acknowledge_metadata(self.root, 'metadata', name, old, new, 'fixture', **auth)
        (self.root / 'third.swift').unlink()
        preview = acknowledge_metadata(self.root, 'metadata', name, old, new, 'fixture', **auth)
        self.assertEqual(preview['status'], 'dry-run')
        with self.assertRaises(ValueError): acknowledge_metadata(self.root, 'other-task', name, old, new, 'fixture', True, preview['previewSha256'], **auth)
        with self.assertRaises(ValueError): acknowledge_metadata(self.root, 'metadata', name, old, new, 'fixture', True, '0'*64, **auth)
        acknowledge_metadata(self.root, 'metadata', name, old, new, 'fixture', True, preview['previewSha256'], **auth)
        saved = read_json(task_dir(self.root, 'metadata') / 'state.json')
        self.assertEqual(saved['baseline'], state['baseline'])
        check_scope(self.root, saved)
        file.write_bytes(b'later change')
        with self.assertRaises(ValueError): check_scope(self.root, saved)

    def test_metadata_interrupt_lock_and_old_report(self):
        import task_runtime
        name = 'Demo.xcodeproj/project.xcworkspace/xcuserdata/person.xcuserdatad/UserInterfaceState.xcuserstate'
        file = self.root / name
        file.parent.mkdir(parents=True); file.write_bytes(b'old')
        begin(self.root, 'metadata', ['one.swift'], 'fixture')
        before = binding(self.root, 'metadata', 'D2')
        report = {'status':'passed','binding':before}
        file.write_bytes(b'new')
        old, new = (hashlib.sha256(x).hexdigest() for x in (b'old', b'new'))
        auth = {'confirmed_by':'fixture reviewer', 'authorization_source':'synthetic user approval'}
        preview = acknowledge_metadata(self.root, 'metadata', name, old, new, 'fixture', **auth)['previewSha256']
        with task_runtime.project_lock(self.root, '.ai-project/tasks.lock'):
            with self.assertRaises(ValueError): acknowledge_metadata(self.root, 'metadata', name, old, new, 'fixture', True, preview, **auth)
        writer = task_runtime.atomic_json
        def interrupted(path, value):
            if Path(path).name == 'state.json': raise OSError('synthetic interruption')
            return writer(path, value)
        with mock.patch.object(task_runtime, 'atomic_json', interrupted):
            with self.assertRaises(OSError): acknowledge_metadata(self.root, 'metadata', name, old, new, 'fixture', True, preview, **auth)
        self.assertNotIn('acknowledgedMetadata', read_json(task_dir(self.root, 'metadata')/'state.json'))
        self.assertTrue(list((task_dir(self.root, 'metadata')/'metadata-confirmations').glob('*.json')))
        acknowledge_metadata(self.root, 'metadata', name, old, new, 'fixture', True, preview, **auth)
        with self.assertRaises(ValueError): validate_report(self.root, report, 'metadata', 'D2')

    def metadata_fixture(self, name, content=b'old metadata'):
        """创建精确UI夹具，保持元数据不属于源码允许编辑列表。"""
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def metadata_apply(self, task_id, name, old, new, reason='synthetic exact UI authorization'):
        """测试使用真实dry-run摘要再apply，不模拟成功或跳过锁内复核。"""
        auth = {'confirmed_by': 'synthetic reviewer', 'authorization_source': 'synthetic explicit metadata authorization'}
        preview = acknowledge_metadata(self.root, task_id, name, old, new, reason, **auth)
        return acknowledge_metadata(self.root, task_id, name, old, new, reason, True, preview['previewSha256'], **auth)

    def test_outer_workspace_metadata_and_absent_new_ui_keep_immutable_baseline(self):
        """外层workspace可确认既有/精确新建UI；原baseline与历史identity逐字保留。"""
        existing = 'Workspace.xcworkspace/xcuserdata/person.xcuserdatad/UserInterfaceState.xcuserstate'
        new_name = 'Second.xcworkspace/xcuserdata/person.xcuserdatad/UserInterfaceState.xcuserstate'
        path = self.metadata_fixture(existing, b'old')
        state = begin(self.root, 'metadata', ['one.swift'], 'outer workspace UI')
        original = (task_dir(self.root, 'metadata') / 'baseline.json').read_bytes()
        path.write_bytes(b'new')
        old, new = (hashlib.sha256(value).hexdigest() for value in (b'old', b'new'))
        self.metadata_apply('metadata', existing, old, new)
        added = self.metadata_fixture(new_name, b'newly created')
        new_hash = hashlib.sha256(added.read_bytes()).hexdigest()
        self.metadata_apply('metadata', new_name, 'absent', new_hash)
        saved = read_json(task_dir(self.root, 'metadata') / 'state.json')
        self.assertEqual((task_dir(self.root, 'metadata') / 'baseline.json').read_bytes(), original)
        self.assertEqual(saved['identity'], state['identity'])
        self.assertEqual(saved['schemaVersion'], '2.1')
        self.assertEqual(saved['metadataAckProtocol'], '2')
        self.assertNotIn(new_name, saved['baseline'])
        self.assertEqual(saved['acknowledgedMetadata'][new_name]['oldSha256'], 'absent')
        self.assertEqual(saved['generation'], state['generation'] + 2)
        check_scope(self.root, saved)
        self.assertEqual(len(list((task_dir(self.root, 'metadata') / 'metadata-confirmations').glob('*.json'))), 2)

    def test_metadata_multiple_acknowledgements_and_legal_parallel_owner(self):
        """已有精确ack与合法并发owner按check_scope授权核对，不能被二次扫描错误拒绝。"""
        names = ['Demo.xcodeproj/project.xcworkspace/xcuserdata/person.xcuserdatad/UserInterfaceState.xcuserstate',
                 'Outer.xcworkspace/xcuserdata/person.xcuserdatad/UserInterfaceState.xcuserstate']
        paths = [self.metadata_fixture(name, b'old') for name in names]
        begin(self.root, 'metadata', ['one.swift'], 'metadata changes')
        begin(self.root, 'parallel', ['two.swift'], 'independent source owner')
        (self.root / 'two.swift').write_text('// changed by independent scoped owner')
        old, new = (hashlib.sha256(value).hexdigest() for value in (b'old', b'new'))
        for name, path in zip(names, paths):
            path.write_bytes(b'new')
            self.metadata_apply('metadata', name, old, new)
        state = read_json(task_dir(self.root, 'metadata') / 'state.json')
        check_scope(self.root, state)
        self.assertEqual(set(state['acknowledgedMetadata']), set(names))
        (self.root / 'unowned.swift').write_text('// unowned untracked source must not be ignored')
        extra = 'Third.xcworkspace/xcuserdata/person.xcuserdatad/UserInterfaceState.xcuserstate'
        path = self.metadata_fixture(extra, b'new')
        with self.assertRaises(ValueError):
            self.metadata_apply('metadata', extra, 'absent', hashlib.sha256(path.read_bytes()).hexdigest())

    def test_metadata_after_explicit_same_target_pbx_context_confirmation(self):
        """先明确承接同目标PBX再确认UI；standalone不会顺便接受第二次PBX漂移。"""
        import task_runtime
        name = 'Outer.xcworkspace/xcuserdata/person.xcuserdatad/UserInterfaceState.xcuserstate'
        path = self.metadata_fixture(name, b'old')
        pbx = self.root / 'Demo.xcodeproj/project.pbxproj'
        pbx.parent.mkdir(exist_ok=True)
        pbx.write_text('PRODUCT_BUNDLE_IDENTIFIER = com.example.synthetic; CURRENT_PROJECT_VERSION = 1;')
        scheme = self.root / 'Demo.xcodeproj/xcshareddata/xcschemes/Demo.xcscheme'
        scheme.parent.mkdir(parents=True); scheme.write_text('<Scheme/>')
        git(self.root, 'add', '.'); git(self.root, 'commit', '-qm', 'synthetic Xcode target fixture')
        atomic_json(self.root / '.ai-project/identity.json', observe_identity(self.root, 'ios', {'bundleId': 'com.example.synthetic', 'scheme': 'Demo'}))
        state = begin(self.root, 'metadata', ['one.swift'], 'same-target context then UI')
        baseline = (task_dir(self.root, 'metadata') / 'baseline.json').read_bytes()
        pbx.write_text('PRODUCT_BUNDLE_IDENTIFIER = com.example.synthetic; CURRENT_PROJECT_VERSION = 2;')
        kwargs = {'context_files': ['Demo.xcodeproj/project.pbxproj'], 'confirmed_by': 'synthetic reviewer', 'authorization_source': 'synthetic exact context approval'}
        preview = task_runtime.lifecycle_transition(self.root, 'metadata', 'continue', 'context-generation-2', 'same target config', **kwargs)
        task_runtime.lifecycle_transition(self.root, 'metadata', 'continue', 'context-generation-2', 'same target config', apply=True, preview_hash=preview['previewSha256'], **kwargs)
        path.write_bytes(b'new')
        old, new = (hashlib.sha256(value).hexdigest() for value in (b'old', b'new'))
        self.metadata_apply('metadata', name, old, new)
        saved = read_json(task_dir(self.root, 'metadata') / 'state.json')
        self.assertIn('Demo.xcodeproj/project.pbxproj', saved['acknowledgedContext'])
        self.assertEqual(saved['identity'], state['identity'])
        self.assertEqual((task_dir(self.root, 'metadata') / 'baseline.json').read_bytes(), baseline)
        check_scope(self.root, saved)
        pbx.write_text('PRODUCT_BUNDLE_IDENTIFIER = com.example.synthetic; CURRENT_PROJECT_VERSION = 3;')
        extra = 'Other.xcworkspace/xcuserdata/person.xcuserdatad/UserInterfaceState.xcuserstate'
        new_path = self.metadata_fixture(extra, b'UI created during unconfirmed PBX change')
        with self.assertRaises(ValueError):
            self.metadata_apply('metadata', extra, 'absent', hashlib.sha256(new_path.read_bytes()).hexdigest())

    def test_metadata_absent_and_digest_rejections_do_not_accept_deletion(self):
        """absent不能覆盖已有基线、代替未知旧值或确认删除；新值始终需完整真实摘要。"""
        name = 'Outer.xcworkspace/xcuserdata/person.xcuserdatad/UserInterfaceState.xcuserstate'
        path = self.metadata_fixture(name, b'old')
        state = begin(self.root, 'metadata', ['one.swift'], 'strict UI hashes')
        path.write_bytes(b'new')
        old, new = (hashlib.sha256(value).hexdigest() for value in (b'old', b'new'))
        auth = {'confirmed_by': 'synthetic reviewer', 'authorization_source': 'synthetic approval'}
        for previous, current in [('absent', new), ('0' * 64, new), (old, '0' * 64), (None, new), (old, 'absent'), (old[:8], new)]:
            with self.subTest(previous=previous, current=current):
                with self.assertRaises(ValueError):
                    acknowledge_metadata(self.root, 'metadata', name, previous, current, 'strict hashes', **auth)
        missing = 'Missing.xcworkspace/xcuserdata/person.xcuserdatad/UserInterfaceState.xcuserstate'
        with self.assertRaises((ValueError, OSError)):
            acknowledge_metadata(self.root, 'metadata', missing, 'absent', new, 'not a deletion authorization', **auth)
        self.assertEqual(read_json(task_dir(self.root, 'metadata') / 'state.json'), state)

    def test_prepare_metadata_ack_does_not_accept_identity_or_scope_and_rejects_aliases(self):
        """helper仅准备单项元数据；身份/范围由组合调用者核对，非规范路径不可绕过边界。"""
        import task_runtime
        name = 'Outer.xcworkspace/xcuserdata/person.xcuserdatad/UserInterfaceState.xcuserstate'
        path = self.metadata_fixture(name, b'old')
        state = begin(self.root, 'metadata', ['one.swift'], 'prepare helper contract')
        path.write_bytes(b'new')
        old, new = (hashlib.sha256(value).hexdigest() for value in (b'old', b'new'))
        with mock.patch.object(task_runtime, 'task_identity', side_effect=AssertionError('helper called task_identity')), mock.patch.object(task_runtime, 'check_scope', side_effect=AssertionError('helper called check_scope')):
            proposed, entry = task_runtime.prepare_metadata_ack(self.root, state, name, old, new, 'metadata only', 'synthetic reviewer', 'synthetic approval')
        self.assertNotIn('acknowledgedMetadata', state)
        self.assertEqual(proposed['acknowledgedMetadata'][name], entry)
        self.assertEqual(read_json(task_dir(self.root, 'metadata') / 'state.json'), state)
        for invalid in ('../' + name, str(path), name.replace('xcuserdata/', './xcuserdata/'), name.replace('xcuserdata/', 'xcuserdata//'), name.replace('/', chr(92)), name + '.backup'):
            with self.subTest(path=invalid):
                with mock.patch('os.open', side_effect=AssertionError('invalid UI path touched filesystem')):
                    with self.assertRaises(ValueError):
                        task_runtime.prepare_metadata_ack(self.root, state, invalid, old, new, 'invalid path', 'reviewer', 'approval')

    def test_metadata_links_hardlinks_and_protected_paths_remain_blocked(self):
        """精确UI文件同样拒绝叶子/祖先链接、多硬链接和受保护元数据。"""
        import task_runtime
        name = 'Outer.xcworkspace/xcuserdata/person.xcuserdatad/UserInterfaceState.xcuserstate'
        path = self.metadata_fixture(name, b'old')
        state = begin(self.root, 'metadata', ['one.swift'], 'strict UI boundary', protected=['Protected.xcworkspace/**'])
        old = hashlib.sha256(b'old').hexdigest()
        outside = self.base / 'outside-ui'; outside.write_bytes(b'new')
        new = hashlib.sha256(b'new').hexdigest()
        path.unlink(); path.symlink_to(outside)
        with self.assertRaises((ValueError, OSError)):
            task_runtime.prepare_metadata_ack(self.root, state, name, old, new, 'symlink denied', 'reviewer', 'approval')
        path.unlink(); os.link(outside, path)
        with self.assertRaises((ValueError, OSError)):
            task_runtime.prepare_metadata_ack(self.root, state, name, old, new, 'hardlink denied', 'reviewer', 'approval')
        path.unlink(); path.write_bytes(b'new')
        linked = self.root / 'Alias.xcworkspace'; linked.symlink_to(self.root / 'Outer.xcworkspace', target_is_directory=True)
        alias = name.replace('Outer.', 'Alias.')
        with self.assertRaises((ValueError, OSError)):
            task_runtime.prepare_metadata_ack(self.root, state, alias, 'absent', new, 'ancestor symlink denied', 'reviewer', 'approval')
        protected = name.replace('Outer.', 'Protected.')
        with mock.patch('os.open', side_effect=AssertionError('protected UI path opened')):
            with self.assertRaises(ValueError):
                task_runtime.prepare_metadata_ack(self.root, state, protected, 'absent', new, 'protected UI denied', 'reviewer', 'approval')

    def test_metadata_preview_and_backup_races_keep_concurrent_changes(self):
        """预览后允许源码/任务变化都失效；备份期间旧writer改state不能被确认覆盖。"""
        import task_runtime
        name = 'Outer.xcworkspace/xcuserdata/person.xcuserdatad/UserInterfaceState.xcuserstate'
        path = self.metadata_fixture(name, b'old')
        state = begin(self.root, 'metadata', ['one.swift'], 'preview/state race')
        path.write_bytes(b'new')
        old, new = (hashlib.sha256(value).hexdigest() for value in (b'old', b'new'))
        auth = {'confirmed_by': 'synthetic reviewer', 'authorization_source': 'synthetic approval'}
        preview = acknowledge_metadata(self.root, 'metadata', name, old, new, 'preview race', **auth)
        (self.root / 'one.swift').write_text('// scoped source changed after preview')
        with self.assertRaisesRegex(ValueError, '预览摘要'):
            acknowledge_metadata(self.root, 'metadata', name, old, new, 'preview race', True, preview['previewSha256'], **auth)
        preview = acknowledge_metadata(self.root, 'metadata', name, old, new, 'state race', **auth)
        state_path = task_dir(self.root, 'metadata') / 'state.json'
        changed = {**state, 'generation': state['generation'] + 1, 'nextAction': 'concurrent explicit writer update'}
        original_fsync = task_runtime.os.fsync
        injected = []
        def raced_fsync(fd):
            original_fsync(fd)
            if not injected:
                injected.append(True)
                state_path.write_text(json.dumps(changed))
        with mock.patch.object(task_runtime.os, 'fsync', side_effect=raced_fsync):
            with self.assertRaisesRegex(ValueError, '任务状态变化'):
                acknowledge_metadata(self.root, 'metadata', name, old, new, 'state race', True, preview['previewSha256'], **auth)
        self.assertEqual(read_json(state_path), changed)
        self.assertNotIn('acknowledgedMetadata', changed)

    def test_metadata_backup_file_drift_and_pending_execution_do_not_acknowledge(self):
        """备份期间UI再次变化不能承认旧摘要；未解除执行租约时也不能承认或清租约。"""
        import task_runtime
        name = 'Outer.xcworkspace/xcuserdata/person.xcuserdatad/UserInterfaceState.xcuserstate'
        path = self.metadata_fixture(name, b'old')
        state = begin(self.root, 'metadata', ['one.swift'], 'metadata during execution')
        path.write_bytes(b'new')
        old, new = (hashlib.sha256(value).hexdigest() for value in (b'old', b'new'))
        auth = {'confirmed_by': 'synthetic reviewer', 'authorization_source': 'synthetic approval'}
        preview = acknowledge_metadata(self.root, 'metadata', name, old, new, 'UI race', **auth)
        original_fsync = task_runtime.os.fsync
        injected = []
        def raced_fsync(fd):
            original_fsync(fd)
            if not injected:
                injected.append(True)
                path.write_bytes(b'changed while backing up')
        with mock.patch.object(task_runtime.os, 'fsync', side_effect=raced_fsync):
            with self.assertRaises(ValueError):
                acknowledge_metadata(self.root, 'metadata', name, old, new, 'UI race', True, preview['previewSha256'], **auth)
        state_path = task_dir(self.root, 'metadata') / 'state.json'
        self.assertEqual(read_json(state_path), state)
        path.write_bytes(b'new')
        leased = {**state, 'pendingExecutions': {'synthetic-execution': {'runnerPid': 1}}}
        atomic_json(state_path, leased)
        with self.assertRaisesRegex(ValueError, '验证执行'):
            acknowledge_metadata(self.root, 'metadata', name, old, new, 'runner pending', **auth)
        self.assertEqual(read_json(state_path), leased)

    def test_metadata_same_path_reconfirmation_preserves_full_chain_and_blocks_next_drift(self):
        """同一路径逐轮确认只接受上一新摘要；每轮历史与原baseline保留，未确认下一轮仍阻塞。"""
        name = 'Outer.xcworkspace/xcuserdata/person.xcuserdatad/UserInterfaceState.xcuserstate'
        path = self.metadata_fixture(name, b'original')
        state = begin(self.root, 'metadata', ['one.swift'], 'sequential UI confirmation')
        baseline = (task_dir(self.root, 'metadata') / 'baseline.json').read_bytes()
        original, first, second = (hashlib.sha256(value).hexdigest() for value in (b'original', b'first', b'second'))
        path.write_bytes(b'first')
        self.metadata_apply('metadata', name, original, first)
        first_state = read_json(task_dir(self.root, 'metadata') / 'state.json')
        first_entry = first_state['acknowledgedMetadata'][name]
        self.assertEqual(first_entry['baselineSha256'], original)
        path.write_bytes(b'second')
        before = (task_dir(self.root, 'metadata') / 'state.json').read_bytes()
        with self.assertRaisesRegex(ValueError, '续轮旧摘要'):
            self.metadata_apply('metadata', name, original, second)
        self.assertEqual((task_dir(self.root, 'metadata') / 'state.json').read_bytes(), before)
        self.metadata_apply('metadata', name, first, second)
        saved = read_json(task_dir(self.root, 'metadata') / 'state.json')
        self.assertEqual(saved['metadataAcknowledgements'][0], first_entry)
        self.assertEqual(len(saved['metadataAcknowledgements']), 2)
        self.assertEqual(saved['acknowledgedMetadata'][name]['oldSha256'], first)
        self.assertEqual(saved['acknowledgedMetadata'][name]['baselineSha256'], original)
        self.assertEqual(saved['generation'], state['generation'] + 2)
        self.assertEqual((task_dir(self.root, 'metadata') / 'baseline.json').read_bytes(), baseline)
        check_scope(self.root, saved)
        with self.assertRaises(ValueError):
            self.metadata_apply('metadata', name, second, second)
        path.write_bytes(b'third, not yet confirmed')
        with self.assertRaises(ValueError):
            check_scope(self.root, saved)
        self.assertEqual(read_json(task_dir(self.root, 'metadata') / 'state.json'), saved)
        path.write_bytes(b'original')
        with self.assertRaises(ValueError):
            check_scope(self.root, saved)

    def test_metadata_absent_creation_then_reconfirmation_keeps_absent_baseline(self):
        """新建UI的后续轮记录真实上一摘要；baselineSha256始终为absent，不能再次冒充首次新建。"""
        name = 'Added.xcworkspace/xcuserdata/person.xcuserdatad/UserInterfaceState.xcuserstate'
        state = begin(self.root, 'metadata', ['one.swift'], 'new UI then later UI state')
        path = self.metadata_fixture(name, b'created')
        first, second = (hashlib.sha256(value).hexdigest() for value in (b'created', b'later'))
        self.metadata_apply('metadata', name, 'absent', first)
        path.write_bytes(b'later')
        with self.assertRaises(ValueError):
            self.metadata_apply('metadata', name, 'absent', second)
        self.metadata_apply('metadata', name, first, second)
        saved = read_json(task_dir(self.root, 'metadata') / 'state.json')
        self.assertNotIn(name, state['baseline'])
        self.assertNotIn(name, saved['baseline'])
        self.assertEqual([item['oldSha256'] for item in saved['metadataAcknowledgements']], ['absent', first])
        self.assertTrue(all(item['baselineSha256'] == 'absent' for item in saved['metadataAcknowledgements']))
        check_scope(self.root, saved)
        path.unlink()
        with self.assertRaises(ValueError):
            check_scope(self.root, saved)

    def test_metadata_legacy_map_is_preserved_before_first_sequential_history_entry(self):
        """旧map无history时原记录逐字回填；历史与latest不一致时拒绝承接，绝不删旧证据。"""
        import task_runtime
        name = 'Outer.xcworkspace/xcuserdata/person.xcuserdatad/UserInterfaceState.xcuserstate'
        path = self.metadata_fixture(name, b'original')
        state = begin(self.root, 'metadata', ['one.swift'], 'legacy UI acknowledgement continuation')
        original, first, second = (hashlib.sha256(value).hexdigest() for value in (b'original', b'first', b'second'))
        path.write_bytes(b'first')
        legacy_entry = {'oldSha256': original, 'newSha256': first, 'reason': 'legacy explicit UI approval', 'confirmedBy': 'synthetic reviewer', 'authorizationSource': 'synthetic legacy approval', 'taskId': 'metadata', 'path': name, 'at': task_runtime.now()}
        state['acknowledgedMetadata'] = {name: legacy_entry}
        state_path = task_dir(self.root, 'metadata') / 'state.json'
        atomic_json(state_path, state)
        path.write_bytes(b'second')
        self.metadata_apply('metadata', name, first, second)
        saved = read_json(state_path)
        self.assertEqual(saved['metadataAcknowledgements'][0], legacy_entry)
        self.assertEqual(len(saved['metadataAcknowledgements']), 2)
        self.assertEqual(saved['metadataAcknowledgements'][1]['baselineSha256'], original)
        check_scope(self.root, saved)
        path.write_bytes(b'third')
        for key, value in [('reason', None), ('oldSha256', '0' * 64), ('newSha256', '0' * 64), ('taskId', 'other-task'), ('at', '2026-11-10T00:00:00')]:
            with self.subTest(historical_field=key):
                tampered = json.loads(json.dumps(saved))
                if value is None:
                    tampered['metadataAcknowledgements'][0].pop(key)
                else:
                    tampered['metadataAcknowledgements'][0][key] = value
                with self.assertRaisesRegex(ValueError, '确认历史'):
                    task_runtime.prepare_metadata_ack(self.root, tampered, name, second, hashlib.sha256(b'third').hexdigest(), 'must not skip a broken old round', 'reviewer', 'approval')
        tampered = json.loads(json.dumps(saved))
        tampered['metadataAcknowledgements'][-1]['reason'] = 'history no longer equals latest'
        with self.assertRaisesRegex(ValueError, '确认历史'):
            task_runtime.prepare_metadata_ack(self.root, tampered, name, second, hashlib.sha256(b'third').hexdigest(), 'must not rewrite history', 'reviewer', 'approval')
        self.assertEqual(read_json(state_path), saved)

    def test_metadata_exact_path_owner_or_terminal_pending_execution_cannot_be_acknowledged(self):
        """精确UI仍归其他任务或遗留执行时不能用ack接管；终结且无租约才可明确确认。"""
        name = 'Outer.xcworkspace/xcuserdata/person.xcuserdatad/UserInterfaceState.xcuserstate'
        path = self.metadata_fixture(name, b'old')
        git(self.root, 'add', name); git(self.root, 'commit', '-qm', 'synthetic UI ownership fixture')
        begin(self.root, 'metadata', ['one.swift'], 'metadata acknowledgement must not steal ownership')
        other = begin(self.root, 'ui-owner', [name], 'separate exact UI owner')
        path.write_bytes(b'new')
        old, new = (hashlib.sha256(value).hexdigest() for value in (b'old', b'new'))
        own_state = (task_dir(self.root, 'metadata') / 'state.json').read_bytes()
        for status, pending in [('active', False), ('waiting-permission', False), ('blocked', False), ('closed', True), ('cancelled', True)]:
            with self.subTest(status=status, pending=pending):
                holder = {**other, 'status': status}
                if pending:
                    holder['pendingExecutions'] = {'synthetic-execution': {'runnerPid': 1}}
                atomic_json(task_dir(self.root, 'ui-owner') / 'state.json', holder)
                with self.assertRaisesRegex(ValueError, '另一任务或遗留执行占用'):
                    self.metadata_apply('metadata', name, old, new)
                self.assertEqual((task_dir(self.root, 'metadata') / 'state.json').read_bytes(), own_state)
        atomic_json(task_dir(self.root, 'ui-owner') / 'state.json', {**other, 'status': 'closed'})
        self.metadata_apply('metadata', name, old, new)
        check_scope(self.root, read_json(task_dir(self.root, 'metadata') / 'state.json'))

    def test_metadata_existing_verification_lock_blocks_even_without_pending_lease(self):
        """无lease的旧runner仍持验证锁时拒绝；普通预览不创建原本缺失的锁文件。"""
        import task_runtime
        name = 'Outer.xcworkspace/xcuserdata/person.xcuserdatad/UserInterfaceState.xcuserstate'
        path = self.metadata_fixture(name, b'old')
        state = begin(self.root, 'metadata', ['one.swift'], 'existing runner lock')
        path.write_bytes(b'new')
        old, new = (hashlib.sha256(value).hexdigest() for value in (b'old', b'new'))
        auth = {'confirmed_by': 'synthetic reviewer', 'authorization_source': 'synthetic approval'}
        directory = task_dir(self.root, 'metadata')
        verification = directory / 'verification.lock'
        self.assertFalse(verification.exists())
        preview = acknowledge_metadata(self.root, 'metadata', name, old, new, 'existing lock probe', **auth)
        self.assertFalse(verification.exists())
        with task_runtime.project_lock(self.root, '.ai-project/tasks/metadata/verification.lock'):
            with self.assertRaisesRegex(ValueError, '验证锁'):
                acknowledge_metadata(self.root, 'metadata', name, old, new, 'existing lock probe', **auth)
            with self.assertRaisesRegex(ValueError, '验证锁'):
                acknowledge_metadata(self.root, 'metadata', name, old, new, 'existing lock probe', True, preview['previewSha256'], **auth)
        self.assertEqual(read_json(directory / 'state.json'), state)
        result = acknowledge_metadata(self.root, 'metadata', name, old, new, 'existing lock probe', True, preview['previewSha256'], **auth)
        self.assertEqual(result['status'], 'acknowledged')

    def test_metadata_new_path_seeds_all_legacy_map_entries_without_reading_other_ui(self):
        """首次升级2.1补齐其他路径旧map历史；只核已有字段/基线，不额外打开其UI。"""
        import task_runtime
        from read_boundary import ReadBoundary
        old_name = 'First.xcworkspace/xcuserdata/person.xcuserdatad/UserInterfaceState.xcuserstate'
        new_name = 'Second.xcworkspace/xcuserdata/person.xcuserdatad/UserInterfaceState.xcuserstate'
        old_path = self.metadata_fixture(old_name, b'old')
        new_path = self.metadata_fixture(new_name, b'old')
        state = begin(self.root, 'metadata', ['one.swift'], 'upgrade legacy metadata maps on a new path')
        old, new = (hashlib.sha256(value).hexdigest() for value in (b'old', b'new'))
        old_path.write_bytes(b'new')
        legacy_entry = {'oldSha256': old, 'newSha256': new, 'reason': 'old path explicit approval', 'confirmedBy': 'synthetic reviewer', 'authorizationSource': 'synthetic legacy approval', 'taskId': 'metadata', 'path': old_name, 'at': task_runtime.now()}
        state['acknowledgedMetadata'] = {old_name: legacy_entry}
        state_path = task_dir(self.root, 'metadata') / 'state.json'
        atomic_json(state_path, state)
        new_path.write_bytes(b'new')
        original_read = ReadBoundary.read_bytes
        def guarded_read(boundary, relative):
            if relative == old_name:
                raise AssertionError('legacy history seeding opened another UI file')
            return original_read(boundary, relative)
        with mock.patch.object(ReadBoundary, 'read_bytes', guarded_read):
            proposed, entry = task_runtime.prepare_metadata_ack(self.root, state, new_name, old, new, 'new path exact approval', 'reviewer', 'approval')
        self.assertEqual(proposed['schemaVersion'], '2.1')
        self.assertEqual(proposed['metadataAcknowledgements'], [legacy_entry, entry])
        task_runtime.validate_state(proposed)
        self.assertEqual(read_json(state_path), state)
        broken = json.loads(json.dumps(state))
        broken['acknowledgedMetadata'][old_name].pop('authorizationSource')
        with self.assertRaisesRegex(ValueError, '确认历史'):
            task_runtime.prepare_metadata_ack(self.root, broken, new_name, old, new, 'cannot hide incomplete old path', 'reviewer', 'approval')
        self.metadata_apply('metadata', new_name, old, new)
        saved = read_json(state_path)
        self.assertEqual(saved['metadataAcknowledgements'][0], legacy_entry)
        self.assertEqual(set(saved['acknowledgedMetadata']), {old_name, new_name})
        check_scope(self.root, saved)

    def test_external_one_time_bound_and_denials(self):
        import read_boundary
        begin(self.root, 'external', ['one.swift'], 'fixture')
        external = (self.base / 'outside').resolve(); external.mkdir()
        item = external / 'sample.txt'; item.write_text('synthetic only')
        meta = external.stat()
        details = item.stat()
        grant = {'projectRoot':str(self.root.resolve()), 'taskId':'external', 'scopeKind':'external-once', 'confirmed':True, 'authorizationSource':'synthetic test approval', 'externalRoot':str(external), 'rootDevice':meta.st_dev, 'rootInode':meta.st_ino, 'file':'sample.txt', 'fileDevice':details.st_dev, 'fileInode':details.st_ino, 'fileSize':details.st_size, 'fileMtimeNs':details.st_mtime_ns, 'fileCtimeNs':details.st_ctime_ns, 'expiresAt':(datetime.now(timezone.utc)+timedelta(minutes=2)).isoformat()}
        def issue(value=grant):
            info = item.stat()
            return issue_external_permit(self.root, 'external', {**value, 'fileDevice':info.st_dev, 'fileInode':info.st_ino, 'fileSize':info.st_size, 'fileMtimeNs':info.st_mtime_ns, 'fileCtimeNs':info.st_ctime_ns, 'authorizationId':uuid.uuid4().hex})
        token = issue(); self.assertEqual(read_external_once(self.root, 'external', token, 'sample.txt'), b'synthetic only')
        with self.assertRaises(BoundaryError): read_external_once(self.root, 'external', token, 'sample.txt')
        fixed = {**grant, 'authorizationId':'single_approval_0001'}
        issued = issue_external_permit(self.root, 'external', fixed)
        self.assertEqual(read_external_once(self.root, 'external', issued, 'sample.txt'), b'synthetic only')
        with self.assertRaises(FileExistsError): issue_external_permit(self.root, 'external', fixed)
        token = issue()
        with self.assertRaises(BoundaryError): read_external_once(self.root, 'other', token, 'sample.txt')
        token = issue()
        with self.assertRaises(BoundaryError): read_external_once(self.root, 'external', token, 'another.txt')
        for changed in ({'confirmed':False}, {'expiresAt':'2000-01-01T00:00:00+00:00'}, {'file':'.env.production'}, {'file':'.ENV.production'}, {'file':'.AWS/config'}, {'file':'.Git/config'}, {'file':'.Env'}, {'file':'credentials.json'}, {'file':'GoogleService-Info.plist'}, {'file':'private.p8'}, {'file':'private.pfx'}, {'file':'private.jks'}, {'file':'private.keystore'}, {'taskId':'other'}):
            with self.assertRaises(BoundaryError): issue({**grant, **changed})
        with self.assertRaises(BoundaryError): issue({**grant, 'externalRoot':str(self.root.resolve())})
        with self.assertRaises(BoundaryError): issue({**grant, 'externalRoot':str(external/'.codex')})
        token = issue({**grant, 'rootInode':meta.st_ino + 1})
        with self.assertRaises(BoundaryError): read_external_once(self.root, 'external', token, 'sample.txt')
        item.unlink(); item.symlink_to(self.root/'one.swift')
        token = issue()
        with self.assertRaises(OSError): read_external_once(self.root, 'external', token, 'sample.txt')
        item.unlink(); other_external = external/'second.txt'; other_external.write_text('synthetic'); os.link(other_external, item)
        token = issue()
        with self.assertRaises(BoundaryError): read_external_once(self.root, 'external', token, 'sample.txt')
        item.unlink(); item.write_text('synthetic only')
        token = issue(); package = self.root/'Package.swift'; original = package.read_text(); package.write_text(original+'// changed identity\n')
        with self.assertRaises(ValueError): read_external_once(self.root, 'external', token, 'sample.txt')
        package.write_text(original)
        token = issue(); info = item.stat(); item.write_text('different data'); os.utime(item, ns=(info.st_atime_ns, info.st_mtime_ns))
        with self.assertRaises(BoundaryError): read_external_once(self.root, 'external', token, 'sample.txt')
        item.write_text('synthetic only')
        token = issue()
        atomic_json(self.root/'.ai-project/operation-holds.json', {'operations':[{'operationId':'held', 'status':'hold'}]})
        with self.assertRaises(ValueError): read_external_once(self.root, 'external', token, 'sample.txt')
        with self.assertRaises(BoundaryError): issue({**grant, 'operationId':'different-id'})
        (self.root/'.ai-project/operation-holds.json').unlink()
        token = issue(); actual_secure = read_boundary.secure_fd
        def denied(root, relative, directory=False):
            if root == Path('/'): raise PermissionError('simulated platform denial')
            return actual_secure(root, relative, directory)
        with mock.patch.object(read_boundary, 'secure_fd', denied):
            with self.assertRaises(PermissionError): read_external_once(self.root, 'external', token, 'sample.txt')
        with self.assertRaises(BoundaryError): read_external_once(self.root, 'external', token, 'sample.txt')
        token = issue()
        checkpoint(self.root, 'external', [], [], [], 'close', 'closed')
        with self.assertRaises(BoundaryError): read_external_once(self.root, 'external', token, 'sample.txt')

    def test_continuous_and_parallel_tasks(self):
        """连续任务不覆盖基线，不同文件并行可用，相同文件并行拒绝。"""
        first = begin(self.root, "first", ["one.swift"], "局部修复")
        second = begin(self.root, "second", ["two.swift"], "独立修复")
        self.assertNotEqual(first["taskId"], second["taskId"])
        with self.assertRaises(ValueError):
            begin(self.root, "overlap", ["one.swift"], "冲突")
        with self.assertRaises(ValueError):
            begin(self.root, "first", ["one.swift"], "不应覆盖")
        (self.root / "one.swift").write_text("// 任务 A 修改\n")
        checkpoint(self.root, "first", ["修改完"], ["验收"], [], "用户验收", "closed")
        self.assertTrue(task_dir(self.root, "first").joinpath("handoff.md").is_file())
        self.assertEqual(resume(self.root, "second")["taskId"], "second")

    def test_switch_project_and_branch(self):
        """复制状态到另项目或切换分支不能静默恢复。"""
        begin(self.root, "same-id", ["one.swift"], "修复")
        other = fixture(self.base / "other")
        atomic_json(other / ".ai-project/identity.json", read_json(self.root / ".ai-project/identity.json"))
        with self.assertRaises(ValueError):
            confirmed_identity(other)
        git(self.root, "checkout", "-qb", "different")
        with self.assertRaises(ValueError):
            resume(self.root, "same-id")

    def test_disconnect_and_permission_wait(self):
        """检查点可恢复，断线后文件变化拒绝自动继续，权限等待仍占用范围。"""
        begin(self.root, "resume", ["one.swift"], "修复")
        checkpoint(self.root, "resume", [], ["需权限"], ["用户尚未授权"], "等待用户", "waiting-permission")
        self.assertEqual(resume(self.root, "resume")["status"], "waiting-permission")
        with self.assertRaises(ValueError):
            begin(self.root, "other", ["one.swift"], "不能抢占")
        (self.root / "one.swift").write_text("// 断线后的未知变化\n")
        with self.assertRaises(ValueError):
            resume(self.root, "resume")
        checkpoint(self.root, "resume", ["已人工核对变化"], [], [], "继续")
        self.assertEqual(resume(self.root, "resume")["status"], "active")

    def test_protected_and_preexisting_changes(self):
        """依赖与用户预先修改受保护，Git 忽略的 Pods 变化仍会被发现。"""
        with self.assertRaises(ValueError):
            begin(self.root, "pods", ["Pods/shared.swift"], "不能改库")
        (self.root / "one.swift").write_text("// 用户原有修改\n")
        with self.assertRaises(ValueError):
            begin(self.root, "dirty", ["one.swift"], "不能认领用户代码")
        begin(self.root, "safe", ["two.swift"], "另一文件")
        (self.root / "Pods/shared.swift").write_text("// 非法改动\n")
        with self.assertRaises(ValueError):
            check_scope(self.root, read_json(task_dir(self.root, "safe") / "state.json"))

    def test_report_binding_and_completion_dimensions(self):
        """真实执行轻量命令；错任务/等级/过期代码及命令契约变化均使 passed 无效。"""
        begin(self.root, "verify", ["one.swift"], "修复")
        command = [sys.executable, "-c", "pass"]
        atomic_json(self.root / ".ai-project/verification-commands.json", {"commands": [{"confirmed": True, "argv": command, "kind": "compiled", "profiles": ["D2"]}]})
        path = task_dir(self.root, "verify") / "compile.json"
        report = run_verification(self.root, "verify", "D2", "compiled", command, path)
        validate_report(self.root, report, "verify", "D2")
        incomplete = dict(report)
        incomplete.pop("limitations")
        with self.assertRaises(ValueError):
            validate_report(self.root, incomplete, "verify", "D2")
        incomplete = dict(report)
        incomplete["coverage"] = {**report["coverage"], "uninspected": []}
        with self.assertRaises(ValueError):
            validate_report(self.root, incomplete, "verify", "D2")
        with self.assertRaises((ValueError, FileNotFoundError)):
            validate_report(self.root, report, "wrong", "D2")
        with self.assertRaises(ValueError):
            validate_report(self.root, report, "verify", "T4")
        state = read_json(task_dir(self.root, "verify") / "state.json")
        self.assertEqual(state["completion"]["realDevice"], "waiting-user")
        self.assertEqual(state["completion"]["pushed"], "not-run")
        self.assertEqual(state["completion"]["automatedTests"], "not-run")
        (self.root / "one.swift").write_text("// 编译后的变化\n")
        with self.assertRaises(ValueError):
            validate_report(self.root, report, "verify", "D2")
        state = checkpoint(self.root, "verify", ["改动"], [], [], "重验")
        self.assertEqual(state["completion"]["compiled"]["status"], "stale")

    def test_unconfirmed_command_and_code_mutating_command(self):
        """未经确认的命令拒绝；命令改变源码时不能返回 passed。"""
        begin(self.root, "run", ["one.swift"], "验证")
        command = [sys.executable, "-c", "from pathlib import Path;Path('one.swift').write_text('changed')"]
        atomic_json(self.root / ".ai-project/verification-commands.json", {"commands": []})
        path = task_dir(self.root, "run") / "report.json"
        with self.assertRaises(ValueError):
            run_verification(self.root, "run", "D2", "compiled", command, path)
        atomic_json(self.root / ".ai-project/verification-commands.json", {"commands": [{"confirmed": True, "argv": command, "kind": "compiled", "profiles": ["D2"]}]})
        self.assertEqual(run_verification(self.root, "run", "D2", "compiled", command, path)["status"], "failed")

    def test_lock_and_atomic_file(self):
        """同一锁的竞争立即失败；原子替换后磁盘总能解析完整 JSON。"""
        path = self.root / ".ai-project/test.lock"
        with lock(path):
            with self.assertRaises(ValueError):
                with lock(path):
                    pass
        target = self.root / ".ai-project/test.json"
        atomic_json(target, {"large": "中" * 10000})
        self.assertEqual(len(read_json(target)["large"]), 10000)

    def test_router_baseline_and_rename(self):
        """旧基线不会进入新任务 pre；重命名与所有未跟踪文件都能识别。"""
        baseline = self.root / ".ai-project/legacy-baseline.json"
        atomic_json(baseline, {"projectRoot": str(self.root), "paths": {}})
        (self.root / "one.swift").write_text("// 上一任务遗留\n")
        output = subprocess.run([sys.executable, str(ROUTER), "--project", str(self.root), "--request", "修复问题", "--baseline", str(baseline), "--write-baseline", "--stage", "pre"], capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(output.stdout)["scope"]["taskFiles"], [])
        git(self.root, "mv", "two.swift", "renamed.swift")
        (self.root / "new").mkdir()
        (self.root / "new/a.swift").write_text("a")
        (self.root / "new/b.swift").write_text("b")
        paths = status_paths(self.root)
        self.assertIn("two.swift", paths)
        self.assertIn("renamed.swift", paths)
        self.assertIn("new/a.swift", paths)
        self.assertIn("new/b.swift", paths)

    def test_deletion_and_platform_routes(self):
        """数据破坏提高等级，视觉与明确否定不误触发；iOS/鸿蒙生命周期互斥。"""
        self.assertEqual(classify("修复永久删除照片逻辑", [], "daily")["impactLevel"], "D4")
        self.assertEqual(classify("删除按钮颜色", [], "daily")["impactLevel"], "D1")
        self.assertNotEqual(classify("修复文案，不涉及数据删除", [], "daily")["impactLevel"], "D4")
        self.assertEqual(route("ios-maintenance", "修复问题")["framework"], "ios-daily-development-rules")
        self.assertEqual(route("harmony-maintenance", "普通缺陷")["framework"], "harmonyos-daily-development-rules")
        self.assertEqual(route("migration-transition", "批量 iOS 一致性问题")["framework"], "ios-to-harmony-migrator-v2")

    def test_gate_reports_reject_bare_stale_wrong_profile_and_subset(self):
        """实际执行轻量 Gate；裸 passed、错切片/等级、过期代码和不完整子集不能关闭任务。"""
        suite = Path(__file__).resolve().parents[2]
        scripts = suite / "frameworks/ios-to-harmony-migrator-v2/scripts"
        sys.path.insert(0, str(scripts))
        import migration_state
        workspace = self.root / ".migration-workspace-v2"
        (workspace / "ir").mkdir(parents=True)
        (workspace / "ir/migration-plan.json").write_text('{"slices":[]}')
        suite = Path(__file__).resolve().parents[2]
        sys.path.insert(0, str(suite / 'installers'))
        from project_framework import MANAGED_MAPPINGS, record_manifest
        import shutil
        for destination, source in MANAGED_MAPPINGS['ios-daily-development-rules']:
            path = self.root / destination
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(suite / source, path)
        record_manifest(self.root, suite, 'ios-daily-development-rules', 'ios-maintenance', MANAGED_MAPPINGS['ios-daily-development-rules'])
        atomic_json(self.root / '.ai-project/identity.json', observe_identity(self.root, 'ios'))
        config = self.root / ".migration-v2/gate-config.json"
        atomic_json(config, {"targetRoot":str(self.root), "evidenceRoot":str(workspace / "evidence/gates"), "profiles":{"T3":["one","two"]}, "gates":[{"id":name,"confirmed":True,"argv":[sys.executable,"-c","pass"]} for name in ("one","two")]})
        begin(self.root, "gates", ["one.swift"], "门禁报告验证")
        report = workspace / "reports/gates.json"
        atomic_json(report, {"status":"passed"})
        with self.assertRaises(ValueError):
            migration_state.passed_report(workspace, report, "fixture", "T3", "slice", "gates")
        command = [sys.executable, str(scripts / "run_gates.py"), str(config), "--task-id", "gates", "--slice-id", "slice", "--profile", "T3", "--report", str(report)]
        subprocess.run(command, check=True, capture_output=True, text=True)
        migration_state.passed_report(workspace, report, "fixture", "T3", "slice", "gates")
        with self.assertRaises(ValueError):
            migration_state.passed_report(workspace, report, "fixture", "T4", "slice", "gates")
        with self.assertRaises(ValueError):
            migration_state.passed_report(workspace, report, "fixture", "T3", "other-slice", "gates")
        subprocess.run(command + ["--gate","one"], check=True, capture_output=True, text=True)
        with self.assertRaises(ValueError):
            migration_state.passed_report(workspace, report, "fixture", "T3", "slice", "gates")
        subprocess.run(command, check=True, capture_output=True, text=True)
        (self.root / "one.swift").write_text("// 验证后的新代码")
        with self.assertRaises(ValueError):
            migration_state.passed_report(workspace, report, "fixture", "T3", "slice", "gates")


    def test_terminal_task_and_initialization_interruption(self):
        """终结任务不能重开抢占；初始化在第二次写入失败时可安全重试且忽略未发布残留。"""
        begin(self.root, "old", ["one.swift"], "旧任务")
        checkpoint(self.root, "old", [], [], [], "关闭", "closed")
        begin(self.root, "new", ["one.swift"], "接管")
        with self.assertRaises(ValueError):
            checkpoint(self.root, "old", [], [], [], "不能重新抢占")
        import task_runtime
        writer = task_runtime.atomic_json
        calls = []
        def interrupted(path, value):
            """在 state 尚未写入时模拟断线异常，保留首次写入行为用于测试事务。"""
            calls.append(path)
            if len(calls) == 2:
                raise OSError("模拟初始化断线")
            return writer(path, value)
        with mock.patch.object(task_runtime, "atomic_json", side_effect=interrupted):
            with self.assertRaises(OSError):
                begin(self.root, "interrupted", ["two.swift"], "中断")
        self.assertFalse(task_dir(self.root, "interrupted").exists())
        atomic_json(self.root / ".ai-project/tasks/.init-orphan/state.json", {"incomplete":True})
        begin(self.root, "interrupted", ["two.swift"], "恢复初始化")
        self.assertEqual(resume(self.root, "interrupted")["status"], "active")

    def test_report_cannot_write_business_and_waiting_cannot_execute(self):
        """报告输出不能覆盖依赖或业务；权限等待状态不能执行已确认命令。"""
        begin(self.root, "guard", ["one.swift"], "验证边界")
        command = [sys.executable, "-c", "pass"]
        atomic_json(self.root / ".ai-project/verification-commands.json", {"commands":[{"confirmed":True,"argv":command,"kind":"compiled","profiles":["D2"]}]})
        before = (self.root / "Pods/shared.swift").read_text()
        with self.assertRaises(ValueError):
            run_verification(self.root, "guard", "D2", "compiled", command, self.root / "Pods/shared.swift")
        self.assertEqual((self.root / "Pods/shared.swift").read_text(), before)
        checkpoint(self.root, "guard", [], [], ["缺授权"], "等待", "waiting-permission")
        with self.assertRaises(ValueError):
            run_verification(self.root, "guard", "D2", "compiled", command, task_dir(self.root, "guard") / "report.json")


    def test_project_inside_repository_preserves_user_edits(self):
        """仓库子项目正确转换 Git 路径，不认领用户脏文件，也不把邻项目变化计入本任务。"""
        child = self.root / "ios"
        child.mkdir()
        for name in ("Package.swift", "one.swift", "two.swift"):
            (child / name).write_text("// 子项目元数据样本")
        git(self.root, "add", ".")
        git(self.root, "commit", "-qm", "nested metadata fixture")
        (child / ".ai-project").mkdir(exist_ok=True)
        (child / ".ai-project/read-policy.json").write_text(json.dumps({"schemaVersion":"1.0","confirmed":True,"projectRoot":str(child.resolve()),"mode":"complete","allow":["**"],"deny":[],"scopeDescription":"合成测试目录明确授权；不涉及真实业务"}))
        atomic_json(child / ".ai-project/identity.json", observe_identity(child, "ios"))
        (child / "one.swift").write_text("// 用户已有修改")
        (self.root / "two.swift").write_text("// 同仓库其他项目修改")
        self.assertEqual(status_paths(child), ["one.swift"])
        with self.assertRaises(ValueError):
            begin(child, "dirty", ["one.swift"], "不能认领")
        state = begin(child, "clean", ["two.swift"], "独立文件")
        self.assertEqual(state["baselineDirtyStatus"], ["one.swift"])


    def test_failed_attempt_does_not_retain_previous_pass(self):
        """不可执行的新尝试持久记录 blocked，不能沿用之前的编译通过状态。"""
        begin(self.root, "attempt", ["one.swift"], "失败记录")
        command = ["/nonexistent/framework-test-command"]
        atomic_json(self.root / ".ai-project/verification-commands.json", {"commands":[{"confirmed":True,"argv":command,"kind":"compiled","profiles":["D2"]}]})
        report = run_verification(self.root, "attempt", "D2", "compiled", command, task_dir(self.root,"attempt") / "report.json")
        self.assertEqual(report["status"], "blocked")
        state = read_json(task_dir(self.root,"attempt") / "state.json")
        self.assertEqual(state["completion"]["compiled"]["status"], "blocked")
        self.assertEqual(state["status"], "blocked")
        self.assertTrue(state["blockers"])
        self.assertTrue(list((task_dir(self.root,"attempt") / "verification-history").glob("*.json")))


    def test_identity_app_fields_and_conflict(self):
        """iOS 应用与鸿蒙字段来源于实际配置；不适用明确标记、选错目标抛错。"""
        app = self.base / "app-metadata"
        (app / "Example.xcodeproj/xcshareddata/xcschemes").mkdir(parents=True)
        (app / "Example.xcodeproj/project.pbxproj").write_text('PRODUCT_BUNDLE_IDENTIFIER = com.example.app;')
        (app / "Example.xcodeproj/xcshareddata/xcschemes/Example.xcscheme").write_text('<Scheme/>')
        (app / "Package.swift").write_text("// App 同根 Package 标记")
        (app / ".ai-project").mkdir(exist_ok=True)
        (app / ".ai-project/read-policy.json").write_text(json.dumps({"schemaVersion":"1.0","confirmed":True,"projectRoot":str(app.resolve()),"mode":"complete","allow":["**"],"deny":[],"scopeDescription":"合成测试目录明确授权；不涉及真实业务"}))
        identity = observe_identity(app, "ios")
        self.assertEqual(identity["fields"]["bundleId"]["value"], "com.example.app")
        self.assertEqual(identity["fields"]["product"]["status"], "not-applicable")
        with self.assertRaises(ValueError):
            observe_identity(app, "ios", {"bundleId": "wrong"})
        harmony = self.base / "harmony-metadata"
        (harmony / "AppScope").mkdir(parents=True)
        (harmony / "entry/src/main").mkdir(parents=True)
        (harmony / "build-profile.json5").write_text('{"products":[{"name":"default"}]}')
        (harmony / "AppScope/app.json5").write_text('{"bundleName":"com.example.harmony"}')
        (harmony / "entry/src/main/module.json5").write_text('{}')
        (harmony / ".ai-project").mkdir(exist_ok=True)
        (harmony / ".ai-project/read-policy.json").write_text(json.dumps({"schemaVersion":"1.0","confirmed":True,"projectRoot":str(harmony.resolve()),"mode":"complete","allow":["**"],"deny":[],"scopeDescription":"合成测试目录明确授权；不涉及真实业务"}))
        identity = observe_identity(harmony, "harmonyos", {"buildMode": "debug"})
        self.assertEqual(identity["fields"]["module"]["value"], "entry")
        self.assertEqual(identity["fields"]["scheme"]["status"], "not-applicable")
        with self.assertRaises(ValueError):
            observe_identity(harmony, "ios")


if __name__ == "__main__":
    unittest.main(verbosity=2)

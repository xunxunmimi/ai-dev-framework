#!/usr/bin/env python3
"""独立协议审查：仅在全新文本/Git 夹具中检验候选；绝不安装真实项目。"""
from __future__ import annotations

import argparse
import contextlib
import copy
from datetime import datetime, timedelta, timezone
import importlib.util
import json
import multiprocessing
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from unittest import mock

SOURCE = Path(__file__).resolve().parents[2] if Path(__file__).with_name('task_runtime.py').is_file() else Path(__file__).resolve().parents[1] / 'framework-candidate'
sys.path.insert(0, str(SOURCE / 'shared/task-routing'))
import task_runtime as runtime
from read_boundary import ReadBoundary, BoundaryError, task_only_scope, issue_external_permit, read_external_once

# 必须与候选文档的停写确认完全一致；收到实现者的协议约定后固定该值。
OFFLINE_ACK = 'all-writers-stopped'


def fixture(root: Path, with_git: bool = False) -> Path:
    """只创建 PBX、Scheme、自有文本；完整授权只作用于该临时夹具。"""
    root.mkdir()
    for name, data in {
        'App.xcodeproj/project.pbxproj': 'PRODUCT_BUNDLE_IDENTIFIER = synthetic.example;\n',
        'App.xcodeproj/xcshareddata/xcschemes/App.xcscheme': '<Scheme/>\n',
        'one.swift': '// synthetic one\n', 'two.swift': '// synthetic two\n',
        '.gitignore': '.ai-project/\n',
    }.items():
        path = root / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text(data)
    if with_git:
        for args in (['init', '-q', '-b', 'main'], ['add', '.'], ['commit', '-qm', 'synthetic fixture']):
            git(root, *args)
    runtime.atomic_json(root / '.ai-project/read-policy.json', {
        'schemaVersion': '1.0', 'confirmed': True, 'projectRoot': str(root.resolve()), 'mode': 'complete',
        'allow': ['**'], 'deny': [], 'scopeDescription': 'independent isolated protocol review',
    })
    runtime.atomic_json(root / '.ai-project/identity.json', runtime.observe_identity(root, 'ios', {'bundleId': 'synthetic.example', 'scheme': 'App'}))
    return root


def git(root: Path, *args: str) -> str:
    """Git 始终指定隔离工作树和命令局部身份，不访问任何远端。"""
    result = subprocess.run(['git', '-C', str(root), '-c', 'user.name=Synthetic Review', '-c', 'user.email=review@example.invalid', *args], capture_output=True, text=True, check=True)
    return result.stdout.strip()


def state(root: Path, task_id: str = 'task') -> dict:
    """以机器状态为准，不以派生交接或返回值推断提交。"""
    return runtime.read_json(root / f'.ai-project/tasks/{task_id}/state.json')


def move(root: Path, action: str, task_id: str = 'task', operation: str = 'review-operation-0001', apply: bool = False, preview: str | None = None, context_files: list[str] | None = None, reason: str = 'isolated explicit review') -> dict:
    """使用公开 API 与明确授权记录，预览和应用参数保持一致。"""
    return runtime.lifecycle_transition(root, task_id, action, operation, reason, 'independent reviewer', 'synthetic explicit approval', apply=apply, preview_hash=preview, context_files=context_files, offline_ack=OFFLINE_ACK)


def begin_worker(root: str, task_id: str, filename: str, start, queue) -> None:
    """两个真实进程同时申请同一精确范围，结果通过管道带回。"""
    start.wait(5)
    try:
        runtime.begin(Path(root), task_id, [filename], 'synthetic concurrent begin')
        queue.put({'task': task_id, 'success': True})
    except (ValueError, OSError) as error:
        queue.put({'task': task_id, 'success': False, 'error': str(error)})


def verification_worker(root: str, argv: list[str], queue) -> None:
    """真实外部命令留在单独验证进程，不从主测试线程伪造锁状态。"""
    try:
        report = runtime.run_verification(Path(root), 'task', 'D2', 'compiled', argv, Path(root) / '.ai-project/tasks/task/report.json')
        queue.put({'ok': True, 'report': report})
    except Exception as error:
        queue.put({'ok': False, 'error': f'{type(error).__name__}: {error}'})


class IndependentProtocolReview(unittest.TestCase):
    """从操作结果与磁盘不变量验证；不复制候选内部判定逻辑。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='framework-independent-review-')
        self.base = Path(self.tmp.name).resolve()
        self.root = fixture(self.base / 'project')

    def tearDown(self):
        self.tmp.cleanup()

    def test_cancel_drift_preserves_origin_releases_only_selected_task(self):
        initial = runtime.begin(self.root, 'task', ['one.swift'], 'synthetic')
        runtime.begin(self.root, 'other', ['two.swift'], 'synthetic independent')
        baseline_path = self.root / '.ai-project/tasks/task/baseline.json'
        baseline = baseline_path.read_bytes()
        config = self.root / 'App.xcodeproj/project.pbxproj'
        config.write_text(config.read_text() + '// legitimate file-reference evolution\n')
        original_other = state(self.root, 'other')
        preview = move(self.root, 'cancel')
        self.assertEqual(preview['status'], 'dry-run')
        self.assertEqual(state(self.root)['status'], 'active')
        move(self.root, 'cancel', apply=True, preview=preview['previewSha256'])
        final = state(self.root)
        self.assertEqual(final['status'], 'cancelled')
        for field in ('identity', 'baseline', 'checkpoint', 'completion'):
            self.assertEqual(final[field], initial[field], field)
        self.assertEqual(baseline_path.read_bytes(), baseline)
        self.assertEqual(state(self.root, 'other'), original_other)
        self.assertGreater(final['generation'], initial['generation'])
        self.assertEqual(runtime.resume(self.root, 'task')['status'], 'cancelled')
        with self.assertRaises((ValueError, BoundaryError)):
            runtime.checkpoint(self.root, 'task', [], [], [], 'reopen')
        with self.assertRaises((ValueError, BoundaryError)):
            runtime.begin(self.root, 'overlap-other', ['two.swift'], 'must remain occupied')

    def test_cancel_replay_is_idempotent_and_parameter_bound(self):
        runtime.begin(self.root, 'task', ['one.swift'], 'synthetic')
        preview = move(self.root, 'cancel')
        move(self.root, 'cancel', apply=True, preview=preview['previewSha256'])
        path = self.root / '.ai-project/tasks/task/state.json'
        snapshot = path.read_bytes(); mtime = path.stat().st_mtime_ns
        move(self.root, 'cancel', apply=True, preview=preview['previewSha256'])
        self.assertEqual(path.read_bytes(), snapshot)
        self.assertEqual(path.stat().st_mtime_ns, mtime)
        with self.assertRaises((ValueError, BoundaryError)):
            move(self.root, 'cancel', apply=True, preview=preview['previewSha256'], reason='different approval scope')
        self.assertEqual(path.read_bytes(), snapshot)

    def test_cancel_preview_rejects_state_drift_and_no_implicit_wait_release(self):
        runtime.begin(self.root, 'task', ['one.swift'], 'synthetic')
        preview = move(self.root, 'cancel')
        runtime.checkpoint(self.root, 'task', [], [], ['approval'], 'wait', 'waiting-permission')
        before = state(self.root)
        with self.assertRaises((ValueError, BoundaryError)):
            move(self.root, 'cancel', apply=True, preview=preview['previewSha256'])
        self.assertEqual(state(self.root), before)

        with self.assertRaises((ValueError, BoundaryError)):
            runtime.begin(self.root, 'overlap', ['one.swift'], 'must remain occupied')

    def test_partial_multitask_archive_rolls_forward_individual_commits(self):
        runtime.begin(self.root, 'task', ['one.swift'], 'synthetic first')
        runtime.begin(self.root, 'other', ['two.swift'], 'synthetic second')
        first = move(self.root, 'cancel')
        second = move(self.root, 'cancel', task_id='other', operation='other-operation-0001')
        move(self.root, 'cancel', apply=True, preview=first['previewSha256'])
        runtime.checkpoint(self.root, 'other', [], [], ['changed after preview'], 'wait', 'blocked')
        with self.assertRaises((ValueError, BoundaryError)):
            move(self.root, 'cancel', task_id='other', operation='other-operation-0001', apply=True, preview=second['previewSha256'])
        self.assertEqual(state(self.root)['status'], 'cancelled')
        self.assertEqual(state(self.root, 'other')['status'], 'blocked')
        first_committed = state(self.root)
        move(self.root, 'cancel', apply=True, preview=first['previewSha256'])
        self.assertEqual(state(self.root), first_committed)
        fresh = move(self.root, 'cancel', task_id='other', operation='other-operation-0001')
        move(self.root, 'cancel', task_id='other', operation='other-operation-0001', apply=True, preview=fresh['previewSha256'])
        self.assertEqual(state(self.root, 'other')['status'], 'cancelled')

    def test_continue_requires_exact_config_drift_and_stales_previous_report(self):
        original = runtime.begin(self.root, 'task', ['one.swift'], 'synthetic')
        report = runtime.verify_configuration(self.root, 'task')
        old_binding = copy.deepcopy(report['binding'])
        name = 'App.xcodeproj/project.pbxproj'
        config = self.root / name; config.write_text(config.read_text() + '// same target, new source reference\n')
        with self.assertRaises((ValueError, BoundaryError)):
            runtime.resume(self.root, 'task')
        with self.assertRaises((ValueError, BoundaryError)):
            move(self.root, 'continue', context_files=[])
        preview = move(self.root, 'continue', context_files=[name])
        move(self.root, 'continue', apply=True, preview=preview['previewSha256'], context_files=[name])
        accepted = state(self.root)
        self.assertEqual(accepted['identity'], original['identity'])
        self.assertEqual(accepted['baseline'], original['baseline'])
        self.assertEqual(runtime.resume(self.root, 'task')['status'], 'active')
        with self.assertRaises((ValueError, BoundaryError)):
            runtime.validate_report(self.root, report, 'task', 'D0')
        self.assertNotEqual(runtime.binding(self.root, 'task', 'D0'), old_binding)
        config.write_text(config.read_text() + '// second unapproved change\n')
        with self.assertRaises((ValueError, BoundaryError)):
            runtime.resume(self.root, 'task')

    def test_continue_rejects_target_change_and_unrelated_source_drift(self):
        runtime.begin(self.root, 'task', ['one.swift'], 'synthetic')
        name = 'App.xcodeproj/project.pbxproj'
        config = self.root / name
        original = config.read_text()
        config.write_text(original.replace('synthetic.example', 'different.example'))
        with self.assertRaises((ValueError, BoundaryError)):
            move(self.root, 'continue', context_files=[name])
        config.write_text(original + '// benign context drift\n')
        (self.root / 'two.swift').write_text('// unrelated unowned source change\n')
        with self.assertRaises((ValueError, BoundaryError)):
            move(self.root, 'continue', context_files=[name])

    def test_observed_original_target_survives_new_sibling_bundle_after_explicit_continue(self):
        runtime.atomic_json(self.root / '.ai-project/identity.json', runtime.observe_identity(self.root, 'ios', {'scheme': 'App'}))
        initial = runtime.begin(self.root, 'task', ['one.swift'], 'synthetic initially unique target')
        self.assertEqual(initial['identity']['fields']['bundleId']['status'], 'observed')
        self.assertEqual(runtime.resume(self.root, 'task')['status'], 'active')
        name = 'App.xcodeproj/project.pbxproj'
        config = self.root / name; config.write_text(config.read_text() + 'PRODUCT_BUNDLE_IDENTIFIER = synthetic.extension;\n')
        preview = move(self.root, 'continue', context_files=[name])
        move(self.root, 'continue', apply=True, preview=preview['previewSha256'], context_files=[name])
        resumed = runtime.resume(self.root, 'task')
        self.assertEqual(resumed['stableIdentity']['target']['bundleId'], 'synthetic.example')
        self.assertEqual(resumed['identity'], initial['identity'])
        self.assertEqual(resumed['status'], 'active')

    def test_continue_rejects_read_policy_drift(self):
        runtime.begin(self.root, 'task', ['one.swift'], 'synthetic')
        path = self.root / '.ai-project/read-policy.json'
        changed = json.loads(path.read_text()); changed['scopeDescription'] += ' changed authorization'
        runtime.atomic_json(path, changed)
        with self.assertRaises((ValueError, BoundaryError)):
            move(self.root, 'continue', context_files=[])

    def test_init_rechecks_identity_after_tasks_lock_acquired(self):
        actual = runtime.project_lock
        changed = False
        @contextlib.contextmanager
        def mutate_after_lock(root, relative):
            nonlocal changed
            with actual(root, relative):
                if relative == '.ai-project/tasks.lock' and not changed:
                    changed = True
                    config = self.root / 'App.xcodeproj/project.pbxproj'
                    config.write_text(config.read_text() + '// configuration changed at lock boundary\n')
                yield
        with mock.patch.object(runtime, 'project_lock', mutate_after_lock):
            with self.assertRaises((ValueError, BoundaryError)):
                runtime.begin(self.root, 'task', ['one.swift'], 'synthetic race')
        self.assertFalse((self.root / '.ai-project/tasks/task/state.json').exists())

    def test_verify_finish_cannot_resurrect_cancelled_task(self):
        runtime.begin(self.root, 'task', ['one.swift'], 'synthetic')
        runtime.atomic_json(self.root / '.ai-project/verification-commands.json', {'commands': [{'confirmed': True, 'kind': 'compiled', 'profiles': ['D2'], 'argv': ['synthetic-command']}]})
        actual = runtime.subprocess.run
        def during_command(*args, **kwargs):
            if args[0] == ['synthetic-command']:
                preview = move(self.root, 'cancel')
                move(self.root, 'cancel', apply=True, preview=preview['previewSha256'])
                raise OSError('synthetic command environment failure')
            return actual(*args, **kwargs)
        with mock.patch.object(runtime.subprocess, 'run', during_command):
            runtime.run_verification(self.root, 'task', 'D2', 'compiled', ['synthetic-command'], self.root / '.ai-project/tasks/task/report.json')
        final = state(self.root)
        self.assertEqual(final['status'], 'cancelled')
        self.assertEqual(final['completion']['compiled'], 'not-run')
        self.assertTrue(list((self.root / '.ai-project/tasks/task/verification-history').glob('*.json')))

    def test_timeout_after_inflight_cancel_retains_execution_lease(self):
        runtime.begin(self.root, 'task', ['one.swift'], 'synthetic')
        argv = ['synthetic-timeout-command']
        runtime.atomic_json(self.root / '.ai-project/verification-commands.json', {'commands': [{'confirmed': True, 'kind': 'compiled', 'profiles': ['D2'], 'argv': argv}]})
        actual = runtime.subprocess.run
        def cancel_then_timeout(*args, **kwargs):
            if args[0] == argv:
                preview = move(self.root, 'cancel'); move(self.root, 'cancel', apply=True, preview=preview['previewSha256'])
                raise subprocess.TimeoutExpired(argv, .1)
            return actual(*args, **kwargs)
        with mock.patch.object(runtime.subprocess, 'run', cancel_then_timeout):
            report = runtime.run_verification(self.root, 'task', 'D2', 'compiled', argv, self.root / '.ai-project/tasks/task/report.json')
        final = state(self.root)
        self.assertEqual(report['status'], 'blocked')
        self.assertEqual(final['status'], 'cancelled')
        self.assertTrue(final['pendingExecutions'], 'terminal after-binding reason must not erase timeout uncertainty')
        self.assertEqual(final['completion']['compiled'], 'not-run')
        with self.assertRaises((ValueError, BoundaryError)):
            runtime.begin(self.root, 'after-cancel-timeout', ['one.swift'], 'unconfirmed descendants still occupy the file')
        preview = move(self.root, 'release-execution', operation='release-cancel-timeout-0001')
        move(self.root, 'release-execution', operation='release-cancel-timeout-0001', apply=True, preview=preview['previewSha256'])
        runtime.begin(self.root, 'after-cancel-timeout-release', ['one.swift'], 'synthetic no subprocess was launched; explicit stop recorded')

    def test_same_file_process_init_has_only_one_owner(self):
        context = multiprocessing.get_context('fork')
        ready = context.Event(); results = context.Queue()
        workers = [context.Process(target=begin_worker, args=(str(self.root), task_id, 'one.swift', ready, results)) for task_id in ('one', 'two')]
        for worker in workers: worker.start()
        ready.set()
        outcomes = [results.get(timeout=10) for _ in workers]
        for worker in workers: worker.join(timeout=10); self.assertEqual(worker.exitcode, 0)
        self.assertEqual(sum(item['success'] for item in outcomes), 1, outcomes)
        runtime.begin(self.root, 'independent', ['two.swift'], 'nonoverlapping remains available')

    def test_real_inflight_verification_cancel_defers_conflicting_reownership(self):
        runtime.begin(self.root, 'task', ['one.swift'], 'synthetic')
        ready, release = self.base / 'command-ready', self.base / 'command-release'
        program = "from pathlib import Path; import sys,time; ready,release,target=map(Path,sys.argv[1:]); ready.write_text('ready'); deadline=time.monotonic()+10\nwhile not release.exists() and time.monotonic()<deadline: time.sleep(.01)\nif not release.exists(): raise RuntimeError('synthetic barrier timed out')\ntarget.write_text('// synthetic late old-writer output\\n')"
        argv = [sys.executable, '-c', program, str(ready), str(release), str(self.root / 'one.swift')]
        runtime.atomic_json(self.root / '.ai-project/verification-commands.json', {'commands': [{'confirmed': True, 'kind': 'compiled', 'profiles': ['D2'], 'argv': argv}]})
        context = multiprocessing.get_context('fork'); queue = context.Queue()
        worker = context.Process(target=verification_worker, args=(str(self.root), argv, queue)); worker.start()
        try:
            deadline = time.monotonic() + 8
            while not ready.exists() and time.monotonic() < deadline: time.sleep(.01)
            self.assertTrue(ready.exists(), 'external command did not start')
            preview = move(self.root, 'cancel')
            move(self.root, 'cancel', apply=True, preview=preview['previewSha256'])
            self.assertEqual(state(self.root)['status'], 'cancelled')
            with self.assertRaises((ValueError, BoundaryError)):
                move(self.root, 'release-execution', operation='cannot-release-live-verifier')
            with self.assertRaises((ValueError, BoundaryError)):
                runtime.begin(self.root, 'overlap', ['one.swift'], 'must wait for old subprocess exit')
            runtime.begin(self.root, 'independent', ['two.swift'], 'independent file can proceed')
        finally:
            release.write_text('release')
            worker.join(timeout=12)
            if worker.is_alive(): worker.terminate(); worker.join()
        self.assertEqual(worker.exitcode, 0)
        result = queue.get(timeout=2); self.assertTrue(result['ok'], result)
        self.assertEqual(state(self.root)['status'], 'cancelled')
        self.assertEqual(state(self.root)['completion']['compiled'], 'not-run')
        runtime.begin(self.root, 'after-exit', ['one.swift'], 'old external writer exited')

    def test_verifier_parent_crash_does_not_release_orphan_subprocess_ownership(self):
        runtime.begin(self.root, 'task', ['one.swift'], 'synthetic')
        ready, release, done = (self.base / name for name in ('orphan-ready', 'orphan-release', 'orphan-done'))
        program = "from pathlib import Path; import sys,time,os; ready,release,target,done=map(Path,sys.argv[1:]); ready.write_text(str(os.getpid())); deadline=time.monotonic()+10\nwhile not release.exists() and time.monotonic()<deadline: time.sleep(.01)\nif not release.exists(): raise RuntimeError('synthetic orphan barrier timed out')\ntarget.write_text('// synthetic orphan late output\\n'); done.write_text('complete')"
        argv = [sys.executable, '-c', program, str(ready), str(release), str(self.root / 'one.swift'), str(done)]
        runtime.atomic_json(self.root / '.ai-project/verification-commands.json', {'commands': [{'confirmed': True, 'kind': 'compiled', 'profiles': ['D2'], 'argv': argv}]})
        context = multiprocessing.get_context('fork'); queue = context.Queue()
        worker = context.Process(target=verification_worker, args=(str(self.root), argv, queue)); worker.start()
        try:
            deadline = time.monotonic() + 8
            while not ready.exists() and time.monotonic() < deadline: time.sleep(.01)
            self.assertTrue(ready.exists(), 'external command did not start')
            worker.terminate(); worker.join(timeout=5)
            self.assertFalse(worker.is_alive(), 'synthetic verifier did not terminate')
            preview = move(self.root, 'cancel')
            move(self.root, 'cancel', apply=True, preview=preview['previewSha256'])
            with self.assertRaises((ValueError, BoundaryError)):
                runtime.begin(self.root, 'overlap-orphan', ['one.swift'], 'old subprocess still writing after parent crash')
        finally:
            release.write_text('release')
            deadline = time.monotonic() + 5
            while not done.exists() and time.monotonic() < deadline: time.sleep(.01)
            if worker.is_alive(): worker.terminate(); worker.join()
            if ready.exists() and not done.exists():
                try: os.kill(int(ready.read_text()), 15)
                except ProcessLookupError: pass
            self.assertTrue(done.exists(), 'synthetic orphan did not finish its bounded cleanup')
        # 子进程已结束也不能仅凭父进程/锁消失自动清租约，必须显式记录停写确认。
        with self.assertRaises((ValueError, BoundaryError)):
            runtime.begin(self.root, 'before-explicit-release', ['one.swift'], 'pending execution is not auto-released')
        preview = move(self.root, 'release-execution', operation='release-orphan-0001')
        move(self.root, 'release-execution', operation='release-orphan-0001', apply=True, preview=preview['previewSha256'])
        runtime.begin(self.root, 'after-explicit-release', ['one.swift'], 'synthetic old writer stopped and lease reviewed')

    def test_directory_fsync_failure_after_state_replace_replays_committed_operation(self):
        runtime.begin(self.root, 'task', ['one.swift'], 'synthetic')
        preview = move(self.root, 'cancel')
        directory = self.root / '.ai-project/tasks/task'
        real = runtime.fsync_directory
        def uncertain(path):
            if Path(path) == directory: raise OSError('synthetic directory fsync failure after state replace')
            return real(path)
        with mock.patch.object(runtime, 'fsync_directory', uncertain):
            with self.assertRaises(OSError): move(self.root, 'cancel', apply=True, preview=preview['previewSha256'])
        self.assertEqual(state(self.root)['status'], 'cancelled')
        before = state(self.root)
        move(self.root, 'cancel', apply=True, preview=preview['previewSha256'])
        self.assertEqual(state(self.root), before)

    def test_timeout_does_not_release_unconfirmed_descendant_execution(self):
        runtime.begin(self.root, 'task', ['one.swift'], 'synthetic')
        ready, release, done = (self.base / name for name in ('timeout-ready', 'timeout-release', 'timeout-done'))
        descendant = "from pathlib import Path; import sys,time,os; ready,release,target,done=map(Path,sys.argv[1:]); ready.write_text(str(os.getpid())); deadline=time.monotonic()+10\nwhile not release.exists() and time.monotonic()<deadline: time.sleep(.01)\nif not release.exists(): raise RuntimeError('synthetic timeout barrier timed out')\ntarget.write_text('// synthetic timed-out descendant output\\n'); done.write_text('complete')"
        parent = "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c',sys.argv[1],*sys.argv[2:]],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); time.sleep(10)"
        argv = [sys.executable, '-c', parent, descendant, str(ready), str(release), str(self.root / 'one.swift'), str(done)]
        runtime.atomic_json(self.root / '.ai-project/verification-commands.json', {'commands': [{'confirmed': True, 'kind': 'compiled', 'profiles': ['D2'], 'argv': argv}]})
        try:
            report = runtime.run_verification(self.root, 'task', 'D2', 'compiled', argv, self.root / '.ai-project/tasks/task/report.json', timeout=.8)
            self.assertEqual(report['status'], 'blocked')
            self.assertTrue(ready.exists(), 'synthetic descendant did not reach barrier')
            preview = move(self.root, 'cancel'); move(self.root, 'cancel', apply=True, preview=preview['previewSha256'])
            with self.assertRaises((ValueError, BoundaryError)):
                runtime.begin(self.root, 'overlap-timeout', ['one.swift'], 'timed-out command descendant still writes')
        finally:
            release.write_text('release')
            deadline = time.monotonic() + 5
            while not done.exists() and time.monotonic() < deadline: time.sleep(.01)
            if ready.exists() and not done.exists():
                try: os.kill(int(ready.read_text()), 15)
                except ProcessLookupError: pass
            self.assertTrue(done.exists(), 'synthetic timed-out descendant did not finish cleanup')
        preview = move(self.root, 'release-execution', operation='release-timeout-0001')
        move(self.root, 'release-execution', operation='release-timeout-0001', apply=True, preview=preview['previewSha256'])
        runtime.begin(self.root, 'after-timeout-release', ['one.swift'], 'explicit confirmed stop after timeout')

    def test_unknown_and_partial_protocol_rejected(self):
        original = runtime.begin(self.root, 'task', ['one.swift'], 'synthetic')
        path = self.root / '.ai-project/tasks/task/state.json'
        changes = [{'schemaVersion': '999.unknown'}, {'taskProtocolVersion': 999}, {'generation': -1}, {'generation': '0'}]
        for change in changes:
            with self.subTest(change=change):
                runtime.atomic_json(path, {**original, **change})
                before = path.read_bytes()
                with self.assertRaises((ValueError, BoundaryError)):
                    runtime.checkpoint(self.root, 'task', [], [], [], 'continue')
                self.assertEqual(path.read_bytes(), before)
        missing = dict(original); missing.pop('acceptedContext')
        runtime.atomic_json(path, missing)
        with self.assertRaises((ValueError, BoundaryError)):
            runtime.resume(self.root, 'task')

    def test_valid_but_wrong_task_or_root_state_cannot_be_written_or_bound(self):
        original = runtime.begin(self.root, 'task', ['one.swift'], 'synthetic')
        path = self.root / '.ai-project/tasks/task/state.json'
        records = [{**original, 'taskId': 'alien'}, {**original, 'identity': {**original['identity'], 'projectRoot': str(self.base / 'other-root')}}]
        for record in records:
            with self.subTest(task=record['taskId'], root=record['identity']['projectRoot']):
                runtime.atomic_json(path, record); before = path.read_bytes()
                with self.assertRaises((ValueError, BoundaryError)):
                    runtime.checkpoint(self.root, 'task', [], [], [], 'incorrect association must block')
                with self.assertRaises((ValueError, BoundaryError)):
                    runtime.binding(self.root, 'task', 'D2')
                self.assertEqual(path.read_bytes(), before)

    def test_cross_root_copy_cannot_cancel_and_symlink_alias_is_same_root(self):
        runtime.begin(self.root, 'task', ['one.swift'], 'synthetic')
        alias = self.base / 'project-alias'; alias.symlink_to(self.root, target_is_directory=True)
        self.assertEqual(runtime.resume(alias, 'task')['status'], 'active')
        clone = self.base / 'same-name-copy'; shutil.copytree(self.root, clone)
        policy = json.loads((clone / '.ai-project/read-policy.json').read_text()); policy['projectRoot'] = str(clone)
        runtime.atomic_json(clone / '.ai-project/read-policy.json', policy)
        with self.assertRaises((ValueError, BoundaryError)):
            move(clone, 'cancel')

    def test_same_path_recreated_root_cannot_inherit_inode_bound_tasks(self):
        runtime.begin(self.root, 'task', ['one.swift'], 'synthetic')
        old = self.base / 'previous-root'
        self.root.rename(old); shutil.copytree(old, self.root)
        with self.assertRaises((ValueError, BoundaryError)):
            move(self.root, 'cancel')
        with self.assertRaises((ValueError, BoundaryError)):
            runtime.resume(self.root, 'task')

    def test_same_repo_worktree_is_a_distinct_stable_project(self):
        root = fixture(self.base / 'repository', with_git=True)
        first = runtime.begin(root, 'task', ['one.swift'], 'synthetic')
        worktree = self.base / 'worktree'
        git(root, 'worktree', 'add', '-q', '-b', 'review-worktree', str(worktree))
        runtime.atomic_json(worktree / '.ai-project/read-policy.json', {'schemaVersion': '1.0', 'confirmed': True, 'projectRoot': str(worktree), 'mode': 'complete', 'allow': ['**'], 'deny': [], 'scopeDescription': 'isolated worktree review'})
        runtime.atomic_json(worktree / '.ai-project/identity.json', runtime.observe_identity(worktree, 'ios', {'bundleId': 'synthetic.example', 'scheme': 'App'}))
        second = runtime.begin(worktree, 'task', ['one.swift'], 'synthetic independent worktree')
        self.assertNotEqual(first['stableIdentity'], second['stableIdentity'])
        self.assertEqual(first['stableIdentity']['repositoryId'], second['stableIdentity']['repositoryId'])
        runtime.atomic_json(worktree / '.ai-project/tasks/task/state.json', first)
        with self.assertRaises((ValueError, BoundaryError)):
            move(worktree, 'cancel')

    def test_configuration_continue_revokes_old_external_token_before_external_access(self):
        import read_boundary
        runtime.begin(self.root, 'task', ['one.swift'], 'synthetic')
        external = self.base / 'external'; external.mkdir(); file = external / 'sample.txt'; file.write_text('synthetic outside data')
        directory, details = external.stat(), file.stat()
        grant = {'projectRoot': str(self.root), 'taskId': 'task', 'scopeKind': 'external-once', 'confirmed': True, 'authorizationSource': 'synthetic explicit approval',
                 'authorizationId': uuid.uuid4().hex, 'externalRoot': str(external), 'rootDevice': directory.st_dev, 'rootInode': directory.st_ino,
                 'file': 'sample.txt', 'fileDevice': details.st_dev, 'fileInode': details.st_ino, 'fileSize': details.st_size,
                 'fileMtimeNs': details.st_mtime_ns, 'fileCtimeNs': details.st_ctime_ns, 'expiresAt': (datetime.now(timezone.utc) + timedelta(minutes=2)).isoformat()}
        token = issue_external_permit(self.root, 'task', grant)
        name = 'App.xcodeproj/project.pbxproj'; config = self.root / name; config.write_text(config.read_text() + '// confirmed evolution\n')
        preview = move(self.root, 'continue', context_files=[name]); move(self.root, 'continue', apply=True, preview=preview['previewSha256'], context_files=[name])
        original = read_boundary.secure_fd
        def guard(root, relative, directory=False):
            if root == Path('/'): raise AssertionError('revoked token reached external root')
            return original(root, relative, directory)
        with mock.patch.object(read_boundary, 'secure_fd', guard):
            with self.assertRaises((ValueError, BoundaryError)):
                read_external_once(self.root, 'task', token, 'sample.txt')
        renewed = issue_external_permit(self.root, 'task', {**grant, 'authorizationId': uuid.uuid4().hex})
        self.assertEqual(read_external_once(self.root, 'task', renewed, 'sample.txt'), b'synthetic outside data')

    def test_task_state_commit_interruption_replay_does_not_duplicate(self):
        runtime.begin(self.root, 'task', ['one.swift'], 'synthetic')
        preview = move(self.root, 'cancel')
        actual = runtime.atomic_json
        def fail_state(path, value):
            if Path(path).name == 'state.json': raise OSError('synthetic precommit failure')
            return actual(path, value)
        with mock.patch.object(runtime, 'atomic_json', fail_state):
            with self.assertRaises(OSError):
                move(self.root, 'cancel', apply=True, preview=preview['previewSha256'])
        self.assertEqual(state(self.root)['status'], 'active')
        move(self.root, 'cancel', apply=True, preview=preview['previewSha256'])
        self.assertEqual(state(self.root)['status'], 'cancelled')

    def test_legacy_requires_explicit_migration_and_retains_original_history(self):
        original = runtime.begin(self.root, 'task', ['one.swift'], 'synthetic')
        legacy = {key: value for key, value in original.items() if key not in {'taskProtocolVersion', 'generation', 'stableIdentity', 'acceptedContext', 'acceptedRevision', 'history'}}
        legacy['schemaVersion'] = '1.0'
        path = self.root / '.ai-project/tasks/task/state.json'; runtime.atomic_json(path, legacy)
        before = path.read_bytes()
        with self.assertRaises((ValueError, BoundaryError)):
            runtime.checkpoint(self.root, 'task', [], [], [], 'legacy ordinary writer must block')
        self.assertEqual(path.read_bytes(), before)
        with self.assertRaises((ValueError, BoundaryError)):
            runtime.lifecycle_transition(self.root, 'task', 'continue', 'legacy-review-0001', 'explicit migration', 'reviewer', 'approval', context_files=[], offline_ack='')
        preview = move(self.root, 'continue', context_files=[])
        move(self.root, 'continue', apply=True, preview=preview['previewSha256'], context_files=[])
        migrated = state(self.root)
        self.assertEqual(migrated['identity'], original['identity'])
        self.assertEqual(migrated['baseline'], original['baseline'])
        self.assertEqual(migrated['schemaVersion'], '2.0')

    def test_legacy_closed_record_never_reactivates(self):
        initial = runtime.begin(self.root, 'task', ['one.swift'], 'synthetic')
        legacy = {key: value for key, value in initial.items() if key not in {'taskProtocolVersion', 'generation', 'stableIdentity', 'acceptedContext', 'acceptedRevision', 'history'}}
        legacy.update(schemaVersion='1.0', status='closed', nextAction='B1 synthetic terminal archive')
        path = self.root / '.ai-project/tasks/task/state.json'; runtime.atomic_json(path, legacy)
        before = path.read_bytes()
        with self.assertRaises((ValueError, BoundaryError)):
            move(self.root, 'continue', context_files=[])
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(json.loads(before)['status'], 'closed')

    def test_task_only_cancel_and_replay_do_not_read_global_identity_or_source(self):
        (self.root / '.ai-project/read-policy.json').unlink()
        (self.root / '.ai-project/identity.json').unlink()
        names = ['App.xcodeproj/project.pbxproj', 'App.xcodeproj/xcshareddata/xcschemes/App.xcscheme', 'one.swift']
        spec = {'schemaVersion': '1.0', 'scopeKind': 'task-only', 'confirmed': True, 'taskId': 'task', 'projectRoot': str(self.root),
                'authorizationSource': 'synthetic approval', 'scopeDescription': 'independent task-only fixture', 'allowFiles': names,
                'identityFiles': names[:2], 'editableFiles': ['one.swift'], 'denyDirectories': [], 'platform': 'ios',
                'identitySelection': {'bundleId': 'synthetic.example', 'scheme': 'App'}}
        with task_only_scope(self.root, 'task', spec):
            runtime.begin(self.root, 'task', ['one.swift'], 'synthetic')
        actual_open = os.open
        forbidden = {'identity.json', 'framework-protocol.json', 'one.swift', 'project.pbxproj', 'App.xcscheme'}
        def guarded(path, *args, **kwargs):
            if Path(path).name in forbidden:
                raise AssertionError(f'termination unexpectedly read source/global state: {path}')
            return actual_open(path, *args, **kwargs)
        with mock.patch('os.open', side_effect=guarded), mock.patch.object(runtime.subprocess, 'run', side_effect=AssertionError('task-only external command')), task_only_scope(self.root, 'task'):
            preview = move(self.root, 'cancel')
            move(self.root, 'cancel', apply=True, preview=preview['previewSha256'])
            move(self.root, 'cancel', apply=True, preview=preview['previewSha256'])
            self.assertEqual(runtime.resume(self.root, 'task')['status'], 'cancelled')
        self.assertFalse((self.root / '.ai-project/read-policy.json').exists())
        self.assertFalse((self.root / '.ai-project/identity.json').exists())
        result = subprocess.run([sys.executable, '-B', str(SOURCE / 'shared/task-routing/task_runtime.py'), 'resume', '--project', str(self.root), '--task-id', 'task', '--task-only'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(json.loads(result.stdout)['historical'])
        self.assertEqual(json.loads(result.stdout)['status'], 'cancelled')

    def test_task_only_unknown_or_partial_state_fails_before_business_read(self):
        (self.root / '.ai-project/read-policy.json').unlink(); (self.root / '.ai-project/identity.json').unlink()
        names = ['App.xcodeproj/project.pbxproj', 'App.xcodeproj/xcshareddata/xcschemes/App.xcscheme', 'one.swift']
        spec = {'schemaVersion': '1.0', 'scopeKind': 'task-only', 'confirmed': True, 'taskId': 'task', 'projectRoot': str(self.root),
                'authorizationSource': 'synthetic approval', 'scopeDescription': 'independent task-only unknown-state fixture', 'allowFiles': names,
                'identityFiles': names[:2], 'editableFiles': ['one.swift'], 'denyDirectories': [], 'platform': 'ios',
                'identitySelection': {'bundleId': 'synthetic.example', 'scheme': 'App'}}
        with task_only_scope(self.root, 'task', spec): original = runtime.begin(self.root, 'task', ['one.swift'], 'synthetic')
        path = self.root / '.ai-project/tasks/task/state.json'
        malformed = [{**original, 'schemaVersion': '999.unknown'}, {**original, 'generation': '1'}, {**original, 'allowedFiles': 'one.swift'}, {**original, 'acceptedContext': {}}, {**original, 'completion': {}}]
        actual_open = os.open
        def guard(path, *args, **kwargs):
            if Path(path).name in {'identity.json', 'one.swift', 'project.pbxproj', 'App.xcscheme'}:
                raise AssertionError(f'partial state authorized a business/global read: {path}')
            return actual_open(path, *args, **kwargs)
        for changed in malformed:
            with self.subTest(schema=changed['schemaVersion'], generation=changed['generation'], allowed=changed['allowedFiles']):
                runtime.atomic_json(path, changed); before = path.read_bytes()
                with task_only_scope(self.root, 'task'), mock.patch('os.open', side_effect=guard), mock.patch.object(runtime.subprocess, 'run', side_effect=AssertionError('task-only external command')):
                    with self.assertRaises((ValueError, BoundaryError)): runtime.resume(self.root, 'task')
                self.assertEqual(path.read_bytes(), before)

    def test_revision_evolution_requires_explicit_continue_not_rebased_baseline(self):
        root = fixture(self.base / 'git-project', with_git=True)
        initial = runtime.begin(root, 'task', ['one.swift'], 'synthetic')
        git(root, 'commit', '--allow-empty', '-qm', 'synthetic revision evolution')
        with self.assertRaises((ValueError, BoundaryError)):
            runtime.resume(root, 'task')
        preview = move(root, 'continue', context_files=[])
        move(root, 'continue', apply=True, preview=preview['previewSha256'], context_files=[])
        accepted = state(root)
        self.assertEqual(accepted['baseRevision'], initial['baseRevision'])
        self.assertNotEqual(accepted['acceptedRevision'], initial['baseRevision'])
        self.assertEqual(runtime.resume(root, 'task')['status'], 'active')

    def test_handoff_failure_after_commit_remains_terminal_replay_safe(self):
        runtime.begin(self.root, 'task', ['one.swift'], 'synthetic')
        preview = move(self.root, 'cancel')
        with mock.patch.object(runtime, 'write_handoff', side_effect=OSError('synthetic derived-view failure')):
            try:
                move(self.root, 'cancel', apply=True, preview=preview['previewSha256'])
            except OSError:
                pass  # 即使实现显式报告视图失败，机器状态也必须已提交且可重放。
        self.assertEqual(state(self.root)['status'], 'cancelled')
        before = state(self.root)
        move(self.root, 'cancel', apply=True, preview=preview['previewSha256'])
        self.assertEqual(state(self.root), before)


class IndependentInstallerReview(unittest.TestCase):
    """使用独立复制的最小受管套件；安装行为只针对临时合成目录。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='framework-independent-install-review-')
        self.base = Path(self.tmp.name).resolve()
        self.root = fixture(self.base / 'project')
        self.suite = self.base / 'suite'; self.suite.mkdir(); (self.suite / 'VERSION').write_text('synthetic-old\n')
        spec = importlib.util.spec_from_file_location('independent_installer', SOURCE / 'installers/project_framework.py')
        self.installer = importlib.util.module_from_spec(spec); spec.loader.exec_module(self.installer)
        self.mappings = self.installer.MANAGED_MAPPINGS['ios-daily-development-rules']
        for destination, source in self.mappings:
            for target in (self.suite / source, self.root / destination):
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(SOURCE / source, target)
        self.installer.record_manifest(self.root, self.suite, 'ios-daily-development-rules', 'ios-maintenance', self.mappings)
        runtime.atomic_json(self.root / '.ai-project/lifecycle.json', {'state': 'ios-maintenance', 'frameworkVersion': 'synthetic-old', 'privateFact': 'synthetic-preserved'})

    def tearDown(self):
        self.tmp.cleanup()

    def update_available(self):
        """仅改合成套件中的规则文本与版本，不修改候选源码。"""
        path = self.suite / self.mappings[0][1]; path.write_text(path.read_text() + '\nSynthetic next rule.\n')
        (self.suite / 'VERSION').write_text('synthetic-new\n')

    def test_upgrade_respects_task_lock_and_offline_ack(self):
        self.update_available()
        before = (self.root / self.mappings[0][0]).read_bytes()
        with self.assertRaises(ValueError): self.installer.upgrade(self.root, self.suite)
        with runtime.project_lock(self.root, '.ai-project/tasks.lock'):
            with self.assertRaises((ValueError, OSError)): self.installer.upgrade(self.root, self.suite, OFFLINE_ACK)
        self.assertEqual((self.root / self.mappings[0][0]).read_bytes(), before)

    def test_cancelled_but_partial_schema_cannot_be_treated_as_safe(self):
        original = runtime.begin(self.root, 'task', ['one.swift'], 'synthetic')
        bad_states = [
            {**original, 'status': 'cancelled', 'allowedFiles': 'one.swift'},
            {**original, 'status': 'cancelled', 'baseline': []},
            {**original, 'status': 'cancelled', 'identity': {}},
            {**original, 'status': 'cancelled', 'stableIdentity': {}},
        ]
        missing = {**original, 'status': 'cancelled'}; missing.pop('completion'); bad_states.append(missing)
        self.update_available()
        path = self.root / '.ai-project/tasks/task/state.json'
        for record in bad_states:
            with self.subTest(fields={key: record.get(key) for key in ('allowedFiles', 'baseline', 'identity', 'stableIdentity')}):
                runtime.atomic_json(path, record)
                with self.assertRaises((ValueError, KeyError, TypeError)):
                    self.installer.upgrade(self.root, self.suite, OFFLINE_ACK)

    def test_record_and_setup_do_not_bypass_existing_installation(self):
        before = (self.root / '.ai-project/framework-installation.json').read_bytes()
        with self.assertRaises(ValueError):
            self.installer.record_manifest(self.root, self.suite, 'ios-daily-development-rules', 'ios-maintenance', self.mappings)
        script = SOURCE / 'frameworks/ios-daily-development-rules/setup-project.sh'
        # 使用真实setup入口；其必须在复制规则前拒绝现有安装。
        result = subprocess.run([str(script), str(self.root), 'ios-maintenance'], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((self.root / '.ai-project/framework-installation.json').read_bytes(), before)

    def test_partial_installation_failure_blocks_runtime_then_restore_keeps_tasks(self):
        original = runtime.begin(self.root, 'task', ['one.swift'], 'synthetic')
        legacy = {key: value for key, value in original.items() if key not in {'taskProtocolVersion', 'generation', 'stableIdentity', 'acceptedContext', 'acceptedRevision', 'history'}}
        legacy.update(schemaVersion='1.0', status='closed')
        task_path = self.root / '.ai-project/tasks/task/state.json'; runtime.atomic_json(task_path, legacy)
        manifest_path = self.root / '.ai-project/framework-installation.json'
        old_manifest = json.loads(manifest_path.read_text()); old_manifest.pop('taskProtocolVersion')
        runtime.atomic_json(manifest_path, old_manifest)
        task_before = task_path.read_bytes()
        self.update_available()
        real = self.installer.atomic_text
        def interrupted(path, value):
            if Path(path) == self.root / self.mappings[1][0]: raise OSError('synthetic second-managed-file interruption')
            return real(path, value)
        with mock.patch.object(self.installer, 'atomic_text', interrupted):
            with self.assertRaises(OSError): self.installer.upgrade(self.root, self.suite, OFFLINE_ACK)
        self.assertEqual(task_path.read_bytes(), task_before)
        with self.assertRaises((ValueError, BoundaryError)): runtime.begin(self.root, 'new', ['two.swift'], 'must block partial install')
        transaction = next((self.root / '.ai-project/upgrades').iterdir())
        self.installer.restore(self.root, transaction, OFFLINE_ACK)
        self.assertEqual(task_path.read_bytes(), task_before)
        self.assertEqual(json.loads(manifest_path.read_text()).get('taskProtocolVersion'), None)
        self.assertEqual(json.loads((self.root / '.ai-project/lifecycle.json').read_text())['privateFact'], 'synthetic-preserved')

    def test_restore_legacy_binary_cannot_downgrade_new_terminal_task(self):
        manifest_path = self.root / '.ai-project/framework-installation.json'
        old_manifest = json.loads(manifest_path.read_text()); old_manifest.pop('taskProtocolVersion')
        runtime.atomic_json(manifest_path, old_manifest)
        self.update_available()
        result = self.installer.upgrade(self.root, self.suite, OFFLINE_ACK)
        runtime.begin(self.root, 'task', ['one.swift'], 'synthetic')
        preview = move(self.root, 'cancel'); move(self.root, 'cancel', apply=True, preview=preview['previewSha256'])
        task_path = self.root / '.ai-project/tasks/task/state.json'; before = task_path.read_bytes()
        with self.assertRaises(ValueError): self.installer.restore(self.root, Path(result['transaction']), OFFLINE_ACK)
        self.assertEqual(task_path.read_bytes(), before)

    def test_new_manifest_with_missing_or_duplicate_required_mapping_blocks_runtime(self):
        path = self.root / '.ai-project/framework-installation.json'; original = json.loads(path.read_text())
        for mappings in (original['managedFiles'][1:], original['managedFiles'] + original['managedFiles'][:1]):
            with self.subTest(count=len(mappings)):
                runtime.atomic_json(path, {**original, 'managedFiles': mappings})
                with self.assertRaises((ValueError, BoundaryError)): runtime.begin(self.root, 'new', ['one.swift'], 'must block incomplete installation')

    def test_installer_source_protocol_cannot_be_downgraded_by_version_label(self):
        self.update_available()
        (self.suite / 'shared/task-routing/task_runtime.py').write_text('# old runner with no protocol guard\n')
        with self.assertRaises(ValueError): self.installer.upgrade(self.root, self.suite, OFFLINE_ACK)

    def test_pending_execution_blocks_upgrade_and_restore_despite_terminal_and_offline_ack(self):
        self.update_available()
        upgrade = self.installer.upgrade(self.root, self.suite, OFFLINE_ACK)
        original = runtime.begin(self.root, 'task', ['one.swift'], 'synthetic')
        path = self.root / '.ai-project/tasks/task/state.json'
        for status in ('cancelled', 'closed'):
            with self.subTest(status=status):
                pending = {**original, 'status': status, 'pendingExecutions': {'synthetic-unconfirmed-execution': {'startedAt': datetime.now(timezone.utc).isoformat(), 'generation': original['generation'], 'runnerPid': 999999}}}
                runtime.atomic_json(path, pending)
                before = path.read_bytes()
                rule_before = (self.root / self.mappings[0][0]).read_bytes()
                audit = self.installer.audit_project(self.root, self.suite)
                self.assertFalse(audit['safeToUpdate'])
                self.assertIn('task', audit['pendingExecutionTasks'])
                with self.assertRaises(ValueError): self.installer.upgrade(self.root, self.suite, OFFLINE_ACK)
                with self.assertRaises(ValueError): self.installer.restore(self.root, Path(upgrade['transaction']), OFFLINE_ACK)
                self.assertEqual(path.read_bytes(), before)
                self.assertEqual((self.root / self.mappings[0][0]).read_bytes(), rule_before)


if __name__ == '__main__':
    unittest.main(verbosity=2)

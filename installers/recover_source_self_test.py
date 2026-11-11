#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Source-location recovery regressions using only temporary synthetic projects."""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from pin_release import pin
from project_framework import MANAGED_MAPPINGS, MANIFEST_RELATIVE, record_manifest
import recover_source
from recover_source import recover
from task_runtime import atomic_json, DIMENSIONS, lock, observe_identity, stable_identity
from read_boundary import ReadBoundary


class SourceRecoveryScenarios(unittest.TestCase):
    """只创建临时框架/任务记录，验证恢复既不升级代码也不变更任务所有权。"""

    def setUp(self):
        """建立原安装与独立固定快照；可变源码位置故意保留为原 suiteRoot。"""
        self.temporary = tempfile.TemporaryDirectory(prefix='framework-source-recovery-')
        self.root = Path(self.temporary.name).resolve()
        self.project = self.root / 'project'
        self.source = self.root / 'mutable-source'
        self.project.mkdir()
        self.source.mkdir()
        (self.project / '.ai-project').mkdir()
        self.policy = {'schemaVersion': '1.0', 'confirmed': True, 'projectRoot': str(self.project), 'mode': 'complete', 'allow': ['**'], 'deny': [], 'scopeDescription': 'Synthetic fixture only'}
        atomic_json(self.project / '.ai-project/read-policy.json', self.policy)
        (self.project / 'Package.swift').write_text('// synthetic marker\n')
        (self.source / 'VERSION').write_text('2.6.7-task-handoff-review\n')
        (self.source / 'README.md').write_text('# synthetic snapshot\n')
        self.mappings = MANAGED_MAPPINGS['ios-to-harmony-migrator-v2']
        for target, origin in self.mappings:
            raw = '# synthetic managed source\n'
            if origin.endswith('/task_runtime.py'):
                # 校验只能解析这些常量，绝不能执行下面的恶意顶层语句。
                raw = 'TASK_PROTOCOL = "2"\nSUPPORTED_STATE_SCHEMAS = ("2.0", "2.1")\nraise RuntimeError("candidate code must never execute")\n'
            for path in (self.source / origin, self.project / target):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(raw)
        self.candidate = Path(pin(self.source, self.root / 'pins', True)['root'])
        self.manifest = record_manifest(self.project, self.source, 'ios-to-harmony-migrator-v2', 'migration-active', self.mappings, persist=False)
        atomic_json(self.project / MANIFEST_RELATIVE, self.manifest)
        self.auth = {'offline_writers_stopped': 'synthetic writers stopped', 'reason': 'restore exact source path', 'confirmed_by': 'synthetic operator', 'authorization_source': 'synthetic approval'}

    def tearDown(self):
        """仅清除此用例拥有的临时夹具。"""
        self.temporary.cleanup()

    def task(self, task_id='task-1', status='active'):
        """生成真实校验器接受的协议2状态，不伪造实际项目任务或完成证据。"""
        identity = observe_identity(self.project, 'ios')
        state = {'schemaVersion': '2.0', 'taskProtocolVersion': '2', 'generation': 1, 'stableIdentity': stable_identity(identity, self.project), 'acceptedContext': identity, 'acceptedRevision': 'synthetic', 'taskId': task_id, 'status': status, 'identity': identity, 'baseline': {}, 'checkpoint': {}, 'allowedFiles': ['sample.swift'], 'readCoverage': ReadBoundary(self.project).coverage(), 'baseRevision': 'synthetic', 'protectedGlobs': [], 'owner': 'synthetic', 'request': 'synthetic recovery fixture', 'completed': [], 'todo': [], 'blockers': [], 'nextAction': 'keep original task', 'updatedAt': '2026-11-11T00:00:00+00:00', 'completion': {name: 'not-run' for name in DIMENSIONS}}
        path = self.project / '.ai-project/tasks' / task_id / 'state.json'
        atomic_json(path, state)
        return path

    def preview(self):
        """预览默认无业务/控制数据写入；返回精确摘要供同一授权重放。"""
        return recover(self.project, self.candidate, **self.auth)

    def apply(self, preview):
        """用预览绑定的请求应用，仅允许此次恢复身份。"""
        return recover(self.project, self.candidate, **self.auth, apply=True, expected_preview_sha256=preview['previewSha256'])

    def manifest_bytes(self):
        """读取夹具清单原始字节，验证失败路径不重登记基线。"""
        return (self.project / MANIFEST_RELATIVE).read_bytes()

    def test_47_active_tasks_and_materials_preserved(self):
        """47个未完成任务仍可精确重绑来源，所有state/旧材料逐字保留。"""
        paths = [self.task('task-' + str(index)) for index in range(47)]
        material = self.project / '.ai-project/tasks/old-notes/handoff.md'
        material.parent.mkdir(parents=True)
        material.write_text('historical notes, not a task\n')
        before = {path: path.read_bytes() for path in paths + [material]}
        old_manifest = self.manifest_bytes()
        preview = self.preview()
        self.assertEqual(preview['status'], 'dry-run')
        self.assertEqual(self.manifest_bytes(), old_manifest)
        self.assertFalse((self.project / '.ai-project/framework-update.lock').exists())
        self.assertFalse((self.project / '.ai-project/source-recoveries').exists())
        self.assertEqual(len(preview['preview']['tasks']['states']), 47)
        self.assertEqual(set(preview['preview']['tasks']['materials']), {'old-notes'})
        result = self.apply(preview)
        self.assertEqual(result['status'], 'recovered')
        after = json.loads(self.manifest_bytes())
        self.assertEqual({key: value for key, value in after.items() if key != 'suiteRoot'}, {key: value for key, value in self.manifest.items() if key != 'suiteRoot'})
        self.assertEqual(after['suiteRoot'], str(self.candidate))
        self.assertEqual(before, {path: path.read_bytes() for path in before})
        self.assertEqual(json.loads((self.project / result['audit']).read_text())['status'], 'applied')

    def test_version_mismatch_fails_even_with_self_consistent_pin(self):
        """候选自己的pin可自洽，但不同版本不能冒充原安装版本。"""
        (self.source / 'VERSION').write_text('different-version\n')
        other = Path(pin(self.source, self.root / 'other-pins', True)['root'])
        before = self.manifest_bytes()
        with self.assertRaisesRegex(ValueError, 'exact originally'):
            recover(self.project, other, **self.auth)
        self.assertEqual(before, self.manifest_bytes())

    def test_source_digest_mismatch_fails_even_with_self_consistent_pin(self):
        """同版本不同源码也必须拒绝，不能仅相信pin或版本字符串。"""
        (self.source / self.mappings[0][1]).write_text('# changed source\n')
        other = Path(pin(self.source, self.root / 'other-pins', True)['root'])
        with self.assertRaisesRegex(ValueError, 'exact originally'):
            recover(self.project, other, **self.auth)

    def test_missing_source_or_drifted_pin_rejected(self):
        """完整源码和pin都是必须项，不允许用部分受管文件重建身份。"""
        path = self.candidate / self.mappings[0][1]
        original = path.read_bytes()
        path.unlink()
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            self.preview()
        path.write_bytes(original + b'# drift\n')
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            self.preview()

    def test_project_rule_drift_rejected(self):
        """项目受管文件仍必须与原始安装一致，不能用恢复接受用户漂移。"""
        (self.project / self.mappings[0][0]).write_text('# user change\n')
        with self.assertRaisesRegex(ValueError, 'rule drift'):
            self.preview()

    def test_partial_protocol_manifest_rejected(self):
        """缺少必需映射时拒绝；恢复不是adopt或reset-manifest。"""
        manifest = copy.deepcopy(self.manifest)
        manifest['managedFiles'].pop()
        atomic_json(self.project / MANIFEST_RELATIVE, manifest)
        with self.assertRaises(ValueError):
            self.preview()

    def test_unknown_protocol_rejected(self):
        """未知安装协议不能因版本/源码吻合而被接管。"""
        manifest = copy.deepcopy(self.manifest)
        manifest['taskProtocolVersion'] = 'unknown'
        atomic_json(self.project / MANIFEST_RELATIVE, manifest)
        with self.assertRaises(ValueError):
            self.preview()

    def test_pending_execution_rejected(self):
        """终态或活动任务中未释放的执行租约都阻止恢复。"""
        path = self.task(status='closed')
        state = json.loads(path.read_text())
        state['pendingExecutions'] = {'runner': {'runnerPid': 42}}
        atomic_json(path, state)
        with self.assertRaisesRegex(ValueError, 'pending executions'):
            self.preview()

    def test_real_installation_and_verification_locks_rejected(self):
        """真实持有的安装/任务/验证文件锁都阻止干预。"""
        self.task()
        preview = self.preview()
        for relative in ['.ai-project/framework-update.lock', '.ai-project/tasks.lock', '.ai-project/tasks/task-1/verification.lock']:
            with self.subTest(relative=relative), lock(self.project / relative):
                with self.assertRaises(ValueError):
                    self.preview()
                with self.assertRaises(ValueError):
                    self.apply(preview)

    def test_preview_expired_when_task_changes(self):
        """任务新增/变化会使预览过期，即便新状态仍合法也不能写。"""
        path = self.task()
        preview = self.preview()
        state = json.loads(path.read_text())
        state['nextAction'] = 'changed after preview'
        atomic_json(path, state)
        before = self.manifest_bytes()
        with self.assertRaisesRegex(ValueError, 'preview expired'):
            self.apply(preview)
        self.assertEqual(before, self.manifest_bytes())

    def test_preview_expired_when_manifest_changes(self):
        """不相关清单事实也参与预览摘要，不能被旧前像覆盖。"""
        preview = self.preview()
        manifest = copy.deepcopy(self.manifest)
        manifest['userNote'] = 'new'
        atomic_json(self.project / MANIFEST_RELATIVE, manifest)
        with self.assertRaisesRegex(ValueError, 'preview expired'):
            self.apply(preview)

    def test_repeat_is_idempotent(self):
        """同一精确操作重放不再次改清单/任务或新增审计。"""
        self.task()
        preview = self.preview()
        result = self.apply(preview)
        manifest = self.manifest_bytes()
        audit = (self.project / result['audit']).read_bytes()
        again = self.apply(preview)
        self.assertEqual(again['status'], 'already-applied')
        self.assertEqual(manifest, self.manifest_bytes())
        self.assertEqual(audit, (self.project / result['audit']).read_bytes())

    def test_interrupted_audit_finalization_can_resume(self):
        """清单已原子写入而审计收尾失败时，同操作可核实并收尾而非重置。"""
        preview = self.preview()
        original = recover_source.atomic_control_json
        def fail_final(project, relative, value):
            """只模拟审计最终状态写失败，不拦截prepared或清单提交。"""
            if relative.startswith(recover_source.AUDIT_DIRECTORY) and value.get('status') == 'applied':
                raise OSError('synthetic audit finalization failure')
            return original(project, relative, value)
        with mock.patch.object(recover_source, 'atomic_control_json', side_effect=fail_final):
            with self.assertRaises(OSError):
                self.apply(preview)
        manifest = self.manifest_bytes()
        result = self.apply(preview)
        self.assertEqual(result['status'], 'already-applied')
        self.assertEqual(manifest, self.manifest_bytes())
        self.assertEqual(json.loads((self.project / result['audit']).read_text())['status'], 'applied')

    def test_interrupted_before_manifest_can_resume(self):
        """prepared审计存在但清单未写时，原预览可安全重放。"""
        preview = self.preview()
        before = self.manifest_bytes()
        original = recover_source.atomic_control_json
        def fail_manifest(project, relative, value):
            """模拟清单原子提交前失败，保留prepared审计。"""
            if relative == MANIFEST_RELATIVE.as_posix():
                raise OSError('synthetic manifest write failure')
            return original(project, relative, value)
        with mock.patch.object(recover_source, 'atomic_control_json', side_effect=fail_manifest):
            with self.assertRaises(OSError):
                self.apply(preview)
        self.assertEqual(before, self.manifest_bytes())
        self.assertEqual(self.apply(preview)['status'], 'recovered')

    def test_last_moment_state_change_preserves_manifest(self):
        """准备审计后出现非协作writer改动时，最终重验拒绝提交清单。"""
        path = self.task()
        preview = self.preview()
        before = self.manifest_bytes()
        original = recover_source.atomic_control_json
        def change_after_audit(project, relative, value):
            """只模拟一次外部任务状态变化，不伪造它持有正确安装锁。"""
            original(project, relative, value)
            if relative.startswith(recover_source.AUDIT_DIRECTORY):
                state = json.loads(path.read_text())
                state['nextAction'] = 'noncooperative concurrent writer'
                atomic_json(path, state)
        with mock.patch.object(recover_source, 'atomic_control_json', side_effect=change_after_audit):
            with self.assertRaisesRegex(ValueError, 'inputs changed before commit'):
                self.apply(preview)
        self.assertEqual(before, self.manifest_bytes())

    def test_audit_permission_required_before_manifest_write(self):
        """审计目录未获读取/写入范围时，不以必要维护为由绕过边界。"""
        policy = copy.deepcopy(self.policy)
        policy['mode'] = 'partial'
        policy['deny'] = [recover_source.AUDIT_DIRECTORY + '/**']
        atomic_json(self.project / '.ai-project/read-policy.json', policy)
        preview = self.preview()
        before = self.manifest_bytes()
        with self.assertRaises(ValueError):
            self.apply(preview)
        self.assertEqual(before, self.manifest_bytes())

    def test_legacy_material_with_ownership_marker_rejected(self):
        """无state但有baseline等任务标记不能当普通旧材料忽略。"""
        path = self.project / '.ai-project/tasks/broken-task/baseline.json'
        path.parent.mkdir(parents=True)
        path.write_text('{}')
        with self.assertRaisesRegex(ValueError, '持久任务标记'):
            self.preview()

    def test_candidate_links_and_extra_file_rejected(self):
        """候选根/祖先/源文件链接与pin之外的文件均拒绝。"""
        alias = self.root / 'candidate-link'
        alias.symlink_to(self.candidate, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'not links'):
            recover(self.project, alias, **self.auth)
        file = self.candidate / self.mappings[0][1]
        raw = file.read_bytes()
        outside = self.root / 'outside.py'
        outside.write_bytes(raw)
        file.unlink()
        file.symlink_to(outside)
        with self.assertRaisesRegex(ValueError, 'linked or special'):
            self.preview()
        file.unlink()
        file.write_bytes(raw)
        (self.candidate / 'unexpected.txt').write_text('extra')
        with self.assertRaisesRegex(ValueError, 'unlisted'):
            self.preview()

    def test_read_policy_and_project_symlinks_not_bypassed(self):
        """已拒绝的受管路径不能读；项目清单链接不能被替换。"""
        policy = copy.deepcopy(self.policy)
        policy['mode'] = 'partial'
        policy['deny'] = [self.mappings[0][0]]
        atomic_json(self.project / '.ai-project/read-policy.json', policy)
        with self.assertRaises(ValueError):
            self.preview()
        atomic_json(self.project / '.ai-project/read-policy.json', self.policy)
        manifest = self.project / MANIFEST_RELATIVE
        outside = self.root / 'outside-manifest.json'
        outside.write_bytes(manifest.read_bytes())
        manifest.unlink()
        manifest.symlink_to(outside)
        with self.assertRaises(ValueError):
            self.preview()

    def test_authorization_and_exact_preview_required(self):
        """缺少停写/原因/批准来源或摘要时只能拒绝，不推导用户意图。"""
        for key in self.auth:
            missing = dict(self.auth)
            missing[key] = ''
            with self.subTest(key=key), self.assertRaises(ValueError):
                recover(self.project, self.candidate, **missing)
        with self.assertRaisesRegex(ValueError, 'expected-preview'):
            recover(self.project, self.candidate, **self.auth, apply=True)
        preview = self.preview()
        changed = dict(self.auth)
        changed['reason'] = 'different reason'
        with self.assertRaisesRegex(ValueError, 'preview expired'):
            recover(self.project, self.candidate, **changed, apply=True, expected_preview_sha256=preview['previewSha256'])


if __name__ == '__main__':
    unittest.main(verbosity=2)

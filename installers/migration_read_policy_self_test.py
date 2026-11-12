#!/usr/bin/env python3
"""Synthetic migration authorization/bootstrap regression; no real project inputs."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from unittest.mock import patch
import migration_read_policy as m

SUITE = Path(__file__).resolve().parents[1]
AUTH = '合成测试用户明确授权源和目标项目完整迁移读取及初始化策略'


def invoke(roots):
    return m.prepare(*map(Path, roots), True, AUTH)


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name).resolve()
        self.source = self.base / 'iOS 中文 空格'
        self.target = self.base / '鸿蒙 目标'
        self.source.mkdir(); self.target.mkdir()
        self.roots = self.source, self.target

    def tearDown(self):
        self.temp.cleanup()

    def install(self, root, **changes):
        policy = m.policy_for(root, AUTH); policy.update(changes)
        path = root / m.POLICY_PATH
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(policy, ensure_ascii=False))
        return path.read_bytes()

    def test_both_missing_chinese_spaces(self):
        m.prepare(*self.roots, True, AUTH)
        for root in self.roots:
            self.assertEqual(m.require_complete(root).policy['authorizationSource'], AUTH)

    def test_single_missing_existing_exact_bytes(self):
        original = self.install(self.source, userNote='retain')
        m.prepare(*self.roots, True, AUTH)
        self.assertEqual((self.source / m.POLICY_PATH).read_bytes(), original)

    def test_existing_legacy_default_unchanged(self):
        for root in self.roots:
            self.install(root)
            path = root / m.POLICY_PATH
            p = json.loads(path.read_text()); p.pop('scopeKind');path.write_text(json.dumps(p))
        before = [(p / m.POLICY_PATH).read_bytes() for p in self.roots]
        m.prepare(*self.roots)
        self.assertEqual(before, [(p / m.POLICY_PATH).read_bytes() for p in self.roots])
        self.assertFalse((self.source / '.ai-project/.read-policy-bootstrap.lock').exists())

    def test_existing_policy_legacy_root_alias(self):
        for root in self.roots: self.install(root)
        alias = self.base / 'legacy-alias'; alias.symlink_to(self.source, target_is_directory=True)
        self.assertEqual(m.prepare(alias, self.target), self.roots)
        with self.assertRaises(ValueError): m.prepare(alias, self.target, True, AUTH)

    def test_direct_cli_default_target_resolves_source_alias(self):
        target = self.source.parent / (self.source.name + '_harmonyos'); target.mkdir()
        self.install(self.source); self.install(target)
        alias = self.base / 'source-alias'; alias.symlink_to(self.source, target_is_directory=True)
        command = [sys.executable, str(SUITE/'frameworks/ios-to-harmony-migrator-v2/scripts/start_migration.py'), str(alias)]
        result = subprocess.run(command,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertTrue((target/'.migration-v2/project.json').exists())
        self.assertFalse((self.base/'source-alias_harmonyos').exists())

    def test_no_implicit_consent(self):
        with self.assertRaisesRegex(ValueError, '缺少迁移读取策略'): m.prepare(*self.roots)
        self.assertFalse((self.source / '.ai-project').exists())
        for flag, auth in ((True, None), (True, '  '), (False, AUTH)):
            with self.assertRaises(ValueError): m.prepare(*self.roots, flag, auth)

    def test_partial_deny_not_replaced_or_other_created(self):
        original = self.install(self.target, mode='partial', allow=['src/**'], deny=['src/private/**'])
        with self.assertRaises(ValueError): m.prepare(*self.roots, True, AUTH)
        self.assertEqual((self.target / m.POLICY_PATH).read_bytes(), original)
        self.assertFalse((self.source / '.ai-project').exists())

    def test_invalid_variants_preserved(self):
        for changes in ({'confirmed':False}, {'projectRoot':str(self.source)}, {'mode':'complete','deny':['src/**']}, {'scopeKind':'task'}):
            original = self.install(self.target, **changes)
            with self.assertRaises(ValueError): m.prepare(*self.roots, True, AUTH)
            self.assertEqual((self.target / m.POLICY_PATH).read_bytes(), original)
            self.assertFalse((self.source / '.ai-project').exists())

    def test_corrupt_and_empty(self):
        path = self.target / m.POLICY_PATH; path.parent.mkdir()
        for raw in ('', '{bad', '[]', 'null'):
            path.write_text(raw)
            with self.assertRaises(ValueError): m.prepare(*self.roots, True, AUTH)
            self.assertEqual(path.read_text(), raw)
            self.assertFalse((self.source / '.ai-project').exists())

    def test_old_state_no_reconstruction(self):
        (self.target / '.ai-project/tasks').mkdir(parents=True)
        with self.assertRaisesRegex(ValueError, '恢复原策略'): m.prepare(*self.roots, True, AUTH)
        self.assertFalse((self.source / '.ai-project').exists())

    def test_legacy_metadata_not_reconstructed(self):
        for name in ('.ai-project/lifecycle.json', '.migration-workspace/state', '.migration-workspace-v2/state', '.codex-rules/harmonyos'):
            path = self.target / name
            path.parent.mkdir(parents=True,exist_ok=True);path.write_text('legacy')
            with self.assertRaisesRegex(ValueError, '恢复原策略'): m.prepare(*self.roots, True, AUTH)
            self.assertFalse((self.source / '.ai-project').exists())
            path.unlink()
            # Use a fresh target for each distinct legacy marker.
            import shutil
            shutil.rmtree(self.target); self.target.mkdir()

    def test_preflight_permission_denial_no_write(self):
        original = m.bootstrap_bytes
        def deny(root, name):
            if root == self.target: raise PermissionError('synthetic platform denial')
            return original(root, name)
        with patch.object(m, 'bootstrap_bytes', deny):
            with self.assertRaises(ValueError): m.prepare(*self.roots, True, AUTH)
        self.assertFalse((self.source / '.ai-project').exists())

    def test_same_nested_and_missing_roots(self):
        for dest in (self.source, self.source / 'sub', self.base / 'absent'):
            if dest.name == 'sub': dest.mkdir()
            with self.assertRaises((OSError, ValueError)): m.prepare(self.source, dest, True, AUTH)
        self.assertFalse((self.source / '.ai-project').exists())

    def test_symlink_root_and_metadata_and_file(self):
        alias = self.base / 'alias'; alias.symlink_to(self.target, target_is_directory=True)
        with self.assertRaises(ValueError): m.prepare(self.source, alias, True, AUTH)
        (self.target / '.ai-project').symlink_to(self.source, target_is_directory=True)
        with self.assertRaises(ValueError): m.prepare(*self.roots, True, AUTH)
        (self.target / '.ai-project').unlink(); (self.target / '.ai-project').mkdir()
        (self.target / m.POLICY_PATH).symlink_to(self.source / 'absent')
        with self.assertRaises(ValueError): m.prepare(*self.roots, True, AUTH)
        self.assertFalse((self.source / '.ai-project').exists())

    def test_concurrent_initializers_idempotent(self):
        with ProcessPoolExecutor(2) as pool:
            list(pool.map(invoke, [tuple(map(str,self.roots))] * 4))
        before = [(r / m.POLICY_PATH).read_bytes() for r in self.roots]
        m.prepare(*self.roots, True, AUTH)
        self.assertEqual(before, [(r / m.POLICY_PATH).read_bytes() for r in self.roots])

    def test_permission_second_write_and_retry(self):
        actual = m.publish
        count = 0
        def fail(directory, policy):
            nonlocal count
            count += 1
            if count == 2: raise PermissionError('synthetic denied')
            return actual(directory, policy)
        with patch.object(m, 'publish', fail):
            with self.assertRaisesRegex(ValueError, '本次已创建策略'): m.prepare(*self.roots, True, AUTH)
        self.assertTrue((self.source / m.POLICY_PATH).exists())
        self.assertFalse((self.target / m.POLICY_PATH).exists())
        m.prepare(*self.roots, True, AUTH)
        m.require_complete(self.target)

    def test_publication_does_not_clobber_concurrent_writer(self):
        actual = m.publish
        def conflict(directory, policy):
            if policy['projectRoot'] == str(self.source):
                self.install(self.source, mode='partial',allow=['src/**'])
            return actual(directory, policy)
        with patch.object(m, 'publish', conflict):
            with self.assertRaises(ValueError): m.prepare(*self.roots, True, AUTH)
        self.assertEqual(json.loads((self.source / m.POLICY_PATH).read_text())['mode'], 'partial')
        self.assertFalse((self.target / m.POLICY_PATH).exists())

    def test_crash_after_link_recovers(self):
        self.install(self.source)
        stage = self.source / '.ai-project' / (m.STAGE_PREFIX + 'a'*32)
        os.link(self.source / m.POLICY_PATH, stage)
        m.prepare(*self.roots, True, AUTH)
        self.assertFalse(stage.exists()); m.require_complete(self.source)

    def test_unknown_hardlink_rejected(self):
        self.install(self.source)
        os.link(self.source / m.POLICY_PATH, self.source / '.ai-project/unknown')
        with self.assertRaises(ValueError): m.prepare(*self.roots, True, AUTH)
        self.assertFalse((self.target / '.ai-project').exists())

    def test_secrets_still_excluded(self):
        m.prepare(*self.roots, True, AUTH)
        boundary = m.require_complete(self.source)
        for name in ('.env', '.ssh/key', 'test.p12', 'credentials/account.json'):
            with self.assertRaises(ValueError): boundary.require(name)

    def test_cli_missing_actionable_no_control_files(self):
        p = subprocess.run(['bash',str(SUITE/'installers/setup-project.sh'),'migration',*map(str,self.roots)], capture_output=True,text=True)
        self.assertEqual(p.returncode,2); self.assertIn('--init-missing-read-policies',p.stderr)
        self.assertFalse((self.target/'.migration-v2').exists())

    def test_full_cli_discovery_and_active_guards(self):
        (self.source/'App.swift').write_text('import SwiftUI\nstruct App {}\n')
        cmd = ['bash',str(SUITE/'installers/setup-project.sh'),'migration',*map(str,self.roots),'--init-missing-read-policies','--authorization-source',AUTH]
        p = subprocess.run(cmd,capture_output=True,text=True)
        self.assertEqual(p.returncode,0,p.stderr)
        self.assertTrue((self.target/'.migration-v2/project.json').exists())
        self.assertTrue((self.target/'.migration-workspace-v2').exists())
        p = subprocess.run(cmd,capture_output=True,text=True)
        self.assertEqual(p.returncode,0,p.stderr)
        # Existing project task guards are exercised by upgrade_self_test; initializer preserves them.
        tasks = self.target / '.ai-project/tasks/active'
        tasks.mkdir(parents=True); (tasks/'state.json').write_text('{bad')
        before = (self.target/'.migration-v2/project.json').read_bytes()
        p = subprocess.run(cmd,capture_output=True,text=True)
        self.assertNotEqual(p.returncode,0)
        self.assertEqual((self.target/'.migration-v2/project.json').read_bytes(),before)


if __name__ == '__main__': unittest.main()

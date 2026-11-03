#!/usr/bin/env python3
"""Known counterexamples and recovery checks, using owned temporary fixtures."""
from __future__ import annotations
import base64
import copy
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'shared/task-routing'))
sys.path.insert(0, str(ROOT / 'frameworks/ios-to-harmony-migrator-v2/scripts'))
sys.path.insert(0, str(ROOT / 'installers'))
from task_router import classify
from read_boundary import BoundaryError, ReadBoundary
from analyze_source import build_source_index
from ui_evidence import validate_manifest, digest
from release_evidence import validate_release
from task_runtime import atomic_json, observe_identity, begin, binding
import task_runtime
from detect_gates import detect
import migration_state
import transactional_files as tx
import pin_release


class SafetyRegressions(unittest.TestCase):
    """合成夹具不冒充业务/真机/视觉正确性证据。"""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='framework-safety-')
        self.root = Path(self.temp.name).resolve()
        atomic_json(self.root / '.ai-project/read-policy.json', {'schemaVersion':'1.0', 'confirmed':True, 'scopeKind':'project', 'projectRoot':str(self.root), 'mode':'complete', 'allow':['**'], 'deny':[], 'scopeDescription':'test-owned synthetic fixture only'})
        (self.root / 'one.swift').write_text('// synthetic')

    def tearDown(self):
        self.temp.cleanup()

    def test_fixed_routes_and_no_heavy_simple_lane(self):
        cases = json.loads((ROOT / 'evals/cases/policy-routing.json').read_text())
        for case in cases:
            with self.subTest(case=case['id']):
                result = classify(case['request'], case['files'], stage='final')
                self.assertEqual(result['impactLevel'], case['expected'])
                self.assertTrue(set(case.get('requiredChecks', [])) <= set(result['verificationRequirements']['required']))
                if 'releaseRequired' in case:
                    self.assertEqual(result['verificationRequirements']['releaseBuildRequired'], case['releaseRequired'])
                if case['expected'] in {'D0', 'D1'}:
                    self.assertFalse(result['runRuntimeByDefault'])
                    self.assertFalse(result['independentReport'])
                    self.assertEqual(result['executionPolicy']['defaultAgents'], 1)

    def test_source_analyzer_excludes_private_before_read(self):
        (self.root / '.env.production').write_text('synthetic sentinel; not a credential')
        (self.root / '.aws').mkdir()
        (self.root / '.aws/config').write_text('synthetic sentinel')
        reads = []
        original = ReadBoundary.read_bytes
        def capture(reader, relative):
            reads.append(relative)
            return original(reader, relative)
        with mock.patch.object(ReadBoundary, 'read_bytes', capture):
            result = build_source_index(self.root)
        self.assertEqual([x['path'] for x in result['files']], ['one.swift'])
        self.assertFalse(any('.env' in x or '.aws' in x for x in reads))
        policy = self.root / '.ai-project/read-policy.json'
        data = json.loads(policy.read_text());data.update(mode='partial', allow=['one.swift'])
        atomic_json(policy, data)
        with self.assertRaises(BoundaryError):
            build_source_index(self.root)

    def ui_fixture(self):
        """真实解析的微型 PNG 仅用于契约测试，不宣称来自设备。"""
        raw = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a7r0AAAAASUVORK5CYII=')
        (self.root / 'proof.png').write_bytes(raw)
        expected = {'taskId':'ui', 'screens':[{'sliceId':'s', 'screenId':'home', 'states':[{'id':'default'}]}]}
        current = {'projectRoot':str(self.root), 'taskId':'ui', 'codeSha256':'synthetic', 'uiCoverageSha256':digest(expected)}
        record = {'path':'proof.png', 'sha256':hashlib.sha256(raw).hexdigest(), 'binding':current, 'mediaType':'image/png', 'capturedAt':'2026-10-23T12:00:00+00:00', 'origin':'runtime-capture', 'captureCommand':['synthetic-contract-test'], 'artifactSha256':'synthetic', 'device':'fixture-only', 'stateId':'default'}
        manifest = {'binding':current, 'coverageSha256':digest(expected), 'screens':[{'sliceId':'s', 'screenId':'home', 'states':[{'id':'default','status':'verified', 'sourceEvidence':[{**record, 'side':'source'}], 'targetEvidence':[{**record, 'side':'target'}]}]}], 'review':{'reviewedBy':'synthetic test fixture; no real visual claim', 'reviewedAt':'2026-10-23T12:01:00+00:00', 'decision':'passed'}}
        return manifest, current, expected

    def test_ui_contract_and_known_counterexamples(self):
        manifest, current, expected = self.ui_fixture()
        check = lambda data, exp=expected: validate_manifest(data, self.root, 's', current, exp)
        self.assertEqual(check(manifest)['status'], 'passed')
        for mutate in ('missing-page','readme','stale','duplicate','exemption','interaction','changed-contract'):
            bad, exp = copy.deepcopy(manifest), copy.deepcopy(expected)
            if mutate == 'missing-page':bad['screens'] = []
            if mutate == 'readme':bad['screens'][0]['states'][0]['targetEvidence'] = ['one.swift']
            if mutate == 'stale':bad['binding']['codeSha256'] = 'changed'
            if mutate == 'duplicate':bad['screens'].append(copy.deepcopy(bad['screens'][0]))
            if mutate == 'exemption':bad['screens'][0]['states'][0].update(status='not_applicable',notes='skip')
            if mutate == 'interaction':exp['screens'][0]['states'][0]['interactionRequired'] = True
            if mutate == 'changed-contract':exp['screens'][0]['states'].append({'id':'error'})
            with self.subTest(counterexample=mutate):self.assertEqual(check(bad, exp)['status'], 'blocked')
        (self.root / 'proof.png').write_bytes(b'changed')
        self.assertEqual(check(manifest)['status'], 'blocked')

    def test_release_debug_cannot_be_release(self):
        (self.root / 'hvigorw').write_text('# synthetic wrapper; not executed')
        detected = detect(self.root, self.root / '.migration-workspace-v2')
        release_gate = next(x for x in detected['gates'] if x['id'] == 'harmony-build-release')
        self.assertIn('buildMode=release', release_gate['argv'])
        self.assertFalse(release_gate['confirmed'])
        errors = validate_release(detected, ['harmony-build-debug'], [{'id':'harmony-build-debug','status':'passed'}], ReadBoundary(self.root), {'taskId':'release'})
        self.assertTrue(any('Release build missing' in x for x in errors))

    def test_binding_rehashes_each_operation_and_reads_rules_once(self):
        (self.root / 'Package.swift').write_text('// identity marker only; no build')
        atomic_json(self.root / '.ai-project/identity.json', observe_identity(self.root, 'ios', {}))
        begin(self.root, 'bind', ['one.swift'], 'synthetic binding')
        with mock.patch.object(task_runtime, 'rules_identity', wraps=task_runtime.rules_identity) as checked:
            before = binding(self.root, 'bind', 'D2')
            self.assertEqual(checked.call_count, 1)
        (self.root / 'one.swift').write_text('// changed')
        after = binding(self.root, 'bind', 'D2')
        self.assertNotEqual(before['codeSha256'], after['codeSha256'])

    def test_router_dry_run_has_no_writes(self):
        before = sorted(str(x.relative_to(self.root)) for x in self.root.rglob('*'))
        result = subprocess.run([sys.executable, '-B', str(ROOT/'shared/task-routing/task_router.py'), '--project',str(self.root),'--request','改标题颜色','--file','one.swift','--dry-run','--write-context',str(self.root/'.ai-project/context.json')],text=True,capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(before, sorted(str(x.relative_to(self.root)) for x in self.root.rglob('*')))

    def test_transaction_restore_interruption_and_user_drift(self):
        candidate = self.root / 'candidate';candidate.mkdir()
        (candidate / 'one.swift').write_text('// candidate')
        (candidate / 'new.md').write_text('new')
        plan = {'root':str(self.root), 'candidate':str(candidate), 'files':[{'path':name,'beforeSha256':tx.sha(tx.content(self.root,name)),'afterSha256':tx.sha(tx.content(candidate,name))} for name in ('one.swift','new.md')]}
        before = (self.root/'one.swift').read_bytes()
        tx.preview(plan)
        self.assertFalse((self.root/'new.md').exists())

        transaction = self.root/'transaction'
        tx.apply(plan, transaction)
        (self.root/'one.swift').write_text('// user edit after upgrade')
        with self.assertRaises(ValueError):tx.restore(transaction)
        self.assertEqual((self.root/'one.swift').read_text(),'// user edit after upgrade')
        (self.root/'one.swift').write_text('// candidate')
        record=json.loads((transaction/'transaction.json').read_text());record['status']='applying';atomic_json(transaction/'transaction.json',record)
        tx.restore(transaction)
        self.assertEqual((self.root/'one.swift').read_bytes(), before)
        self.assertFalse((self.root/'new.md').exists())

    def test_pinned_release_reuses_only_unchanged_content(self):
        destination=self.root/'pins'
        self.assertEqual(pin_release.pin(ROOT,destination)['status'],'dry-run')
        self.assertFalse(destination.exists())
        created=pin_release.pin(ROOT,destination,True)
        pinned=Path(created['root'])
        self.assertEqual(pin_release.pin(ROOT,destination)['status'],'verified')
        (pinned/'shared/task-routing/task_router.py').write_text('# drift')
        with self.assertRaises(ValueError):pin_release.verify(pinned)


if __name__ == '__main__':
    unittest.main(verbosity=2)

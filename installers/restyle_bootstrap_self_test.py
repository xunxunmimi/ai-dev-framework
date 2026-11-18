"""生产入口的隔离安装回归；不运行应用。"""
import json
import unittest
from unittest import mock
from harmonyos_reimplementation_self_test import ReimplementationTests
import bootstrap_harmonyos_restyle as entry
from enable_harmonyos_reimplementation import TARGET

class BootstrapTests(ReimplementationTests):
    def install(self):
        p=entry.bootstrap(self.target,self.source)
        return entry.bootstrap(self.target,self.source,True,p['plan']['planSha256'],'synthetic writers stopped')
    def test_standalone_install(self):
        before=self.inventory(self.source);result=self.install()
        self.assertTrue((self.target/TARGET/'SKILL.md').is_file())
        self.assertFalse((self.target/'.codex-rules').exists());self.assertFalse((self.target/'.ai-project/lifecycle.json').exists())
        self.assertEqual(before,self.inventory(self.source));self.assertFalse(result['businessExecutionPerformed'])
        s=json.loads((self.target/TARGET/'selection.json').read_text());self.assertEqual(s['workflowPhase'],'rebrand-production-before-acceptance')
    def test_daily_blocks_without_writes(self):
        (self.target/'.ai-project/lifecycle.json').write_text('{}');before=self.inventory(self.target)
        with self.assertRaises(ValueError): self.install()
        self.assertEqual(before,self.inventory(self.target))
    def test_plan_drift_blocks(self):
        p=entry.bootstrap(self.target,self.source);policy=self.target/'.ai-project/read-policy.json'
        d=json.loads(policy.read_text());d['scopeDescription']='changed';policy.write_text(json.dumps(d))
        with self.assertRaises(ValueError):entry.bootstrap(self.target,self.source,True,p['plan']['planSha256'],'synthetic stop')
        self.assertFalse((self.target/TARGET).exists())
    def test_conflict_before_publish_blocks(self):
        real=entry.enable
        def race(*args,**kwargs):
            (self.target/'.ai-project/lifecycle.json').write_text('{}')
            return real(*args,**kwargs)
        with mock.patch.object(entry,'enable',side_effect=race):
            with self.assertRaises(ValueError):self.install()
        self.assertFalse((self.target/TARGET).exists())
    def test_missing_stop_blocks(self):
        p=entry.bootstrap(self.target,self.source)
        with self.assertRaises(ValueError):entry.bootstrap(self.target,self.source,True,p['plan']['planSha256'])
        self.assertFalse((self.target/TARGET).exists())
    def partial(self,deny=None,omit=None):
        p=self.target/'.ai-project/read-policy.json';d=json.loads(p.read_text())
        allow=entry.required_read_scope()['targetReadAllowCandidates']
        if omit:allow=[n for n in allow if n!=omit]
        d.update(mode='partial',allow=allow,deny=deny or []);p.write_text(json.dumps(d))
    def test_absent_parents_created_under_exact_partial_grant(self):
        self.partial();self.assertFalse((self.target/'.agents').exists())
        p=entry.bootstrap(self.target,self.source);self.assertFalse((self.target/'.agents').exists())
        result=entry.bootstrap(self.target,self.source,True,p['plan']['planSha256'],'synthetic stop')
        self.assertEqual(result['status'],'enabled');self.assertTrue((self.target/TARGET/'SKILL.md').is_file())
    def test_missing_parent_grant_actionable_without_creation(self):
        self.partial(omit='.agents')
        with self.assertRaisesRegex(ValueError,'安装前置路径未授权.*.agents'):entry.bootstrap(self.target,self.source)
        self.assertFalse((self.target/'.agents').exists())
    def test_explicit_parent_deny_not_overridden(self):
        self.partial(deny=['.agents']);before=self.inventory(self.target)
        with self.assertRaisesRegex(ValueError,'安装前置路径未授权'):entry.bootstrap(self.target,self.source)
        self.assertEqual(before,self.inventory(self.target))
    def test_scope_proposal_has_no_broad_agents_or_target_access(self):
        with mock.patch.object(entry,'ReadBoundary',side_effect=AssertionError('no target access')):
            result=entry.required_read_scope()
        self.assertFalse(result['authorizationGranted']);self.assertEqual(result['writes'],[])
        self.assertFalse(any('*' in n for n in result['targetReadAllowCandidates']))
        self.assertIn('.agents',result['targetReadAllowCandidates']);self.assertIn('.agents/skills',result['targetReadAllowCandidates'])
if __name__=='__main__':unittest.main(verbosity=2)

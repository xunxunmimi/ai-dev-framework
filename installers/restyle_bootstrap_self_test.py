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
if __name__=='__main__':unittest.main(verbosity=2)

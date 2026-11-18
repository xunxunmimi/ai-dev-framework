"""独立入口只读预检回归，真实临时源/目标，不安装框架。"""
import json
from pathlib import Path
import tempfile
import unittest
from plan_harmonyos_restyle import plan

class RestylePlanTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.base=Path(self.tmp.name).resolve()
        self.source=self.base/'source'; self.target=self.base/'target'
        for root in [self.source,self.target]:
            (root/'.ai-project').mkdir(parents=True)
            (root/'.ai-project/read-policy.json').write_text(json.dumps({'schemaVersion':'1.0','confirmed':True,'projectRoot':str(root),'mode':'complete','allow':['**'],'deny':[],'scopeDescription':'synthetic fixture only'}))
    def tearDown(self): self.tmp.cleanup()
    def inventory(self): return {str(p):p.read_bytes() for p in self.base.rglob('*') if p.is_file()}
    def test_standalone_no_daily_no_write(self):
        before=self.inventory(); result=plan(self.target,self.source)
        self.assertEqual(result['status'],'preflight-only'); self.assertFalse(result['executionSupported'])
        self.assertFalse(result['dailyFrameworkBeforeAcceptance']); self.assertEqual(before,self.inventory())
    def test_existing_daily_fails_closed(self):
        for name in ['framework-installation.json','lifecycle.json']:
            (self.target/'.ai-project'/name).write_text('{}')
        before=self.inventory(); result=plan(self.target,self.source)
        self.assertEqual(result['status'],'blocked-existing-context'); self.assertEqual(len(result['conflicts']),2)
        self.assertEqual(before,self.inventory())
    def test_existing_records_block(self):
        folder=self.target/'.ai-project/tasks/unknown'; folder.mkdir(parents=True)
        self.assertIn('.ai-project/tasks',plan(self.target,self.source)['conflicts'])
    def test_denied_inspection_does_not_mean_absent(self):
        p=self.target/'.ai-project/read-policy.json'; data=json.loads(p.read_text()); data.update(mode='partial',deny=['.ai-project/tasks']); p.write_text(json.dumps(data))
        with self.assertRaises(ValueError): plan(self.target,self.source)
    def test_nested_rejected(self):
        with self.assertRaises(ValueError): plan(self.target,self.base)
    def test_unsafe_parent_rejected(self):
        (self.target/'.agents').symlink_to(self.source,target_is_directory=True)
        with self.assertRaises(ValueError): plan(self.target,self.source)
    def test_unknown_agents_instructions_block(self):
        (self.target/'AGENTS.md').write_text('unreviewed rules')
        self.assertIn('AGENTS.md',plan(self.target,self.source)['conflicts'])

    def test_real_state_sentinel_paths_block(self):
        for name in ['identity.json','operation-holds.json','legacy-baseline.json','task-input.json']:
            p=self.target/'.ai-project'/name;p.write_text('{}')
            with self.subTest(name=name):
                self.assertIn('.ai-project/'+name,plan(self.target,self.source)['conflicts'])
            p.unlink()
        folder=self.target/'.ai-project/checkpoints';folder.mkdir();(folder/'old.json').write_text('{}')
        self.assertIn('.ai-project/checkpoints',plan(self.target,self.source)['conflicts'])
if __name__=='__main__': unittest.main(verbosity=2)

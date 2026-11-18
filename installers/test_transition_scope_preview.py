import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from read_boundary import ReadBoundary
from transition_scope_preview import preview,known_slots
class Fixture(unittest.TestCase):
    def setUp(self):
        self.t=tempfile.TemporaryDirectory();self.p=Path(self.t.name).resolve()
        (self.p/'.ai-project').mkdir()
        self.policy=dict(schemaVersion="1.0",confirmed=True,projectRoot=str(self.p),mode="partial",
                         scopeDescription="synthetic",allow=[".ai-project/**",".codex-rules/**","AGENTS.md",
                         ".agents/skills/harmonyos-app-reimplementation/**","wanted.ets"],deny=["secret.ets"])
        self.file=self.p/'.ai-project/read-policy.json';self.nonce='1'*32
    def tearDown(self):self.t.cleanup()
    def setup_policy(self):
        old=json.dumps(self.policy).encode()
        self.file.write_bytes(old)
        return hashlib.sha256(old).hexdigest()
    def test_permission_delta_does_not_grant_arbitrary_paths(self):
        result=preview(self.p,self.nonce,self.setup_policy())
        bound=ReadBoundary(self.p,policy=json.loads(result['afterPolicyText']))
        self.assertTrue(bound.allowed(result['quarantine']+'/SKILL.md'))
        self.assertFalse(bound.allowed(result['quarantine']+'/unknown.txt'))
        self.assertFalse(bound.allowed('.agents/skills/other/SKILL.md'))
        self.assertFalse(bound.allowed('secret.ets'))
        self.assertTrue(bound.allowed('wanted.ets'))
        self.assertTrue(all('*' not in x for x in result['exactReadAdditions']))
    def test_nonce_not_influenced_by_policy_hash(self):
        a=preview(self.p,self.nonce,self.setup_policy())
        self.policy['allow'].append('.agents')
        self.policy["scopeDescription"] += " reviewed"
        b=preview(self.p,self.nonce,self.setup_policy())
        self.assertNotEqual(a['policyAfterSha256'],b['policyAfterSha256'])
        self.assertEqual((a['stage'],a['quarantine']),(b['stage'],b['quarantine']))
    def test_old_deny_stays_absolute(self):
        self.policy['deny'].append('.agents/**')
        with self.assertRaises(ValueError):
            preview(self.p,self.nonce,self.setup_policy())
if __name__=='__main__':unittest.main()

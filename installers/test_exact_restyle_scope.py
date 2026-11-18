import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from read_boundary import ReadBoundary
from exact_restyle_scope import preview
class ReviewTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name).resolve()
        self.p=self.root/'.ai-project/read-policy.json';self.p.parent.mkdir()
        self.policy={'schemaVersion':'1.0','confirmed':True,'projectRoot':str(self.root),'scopeKind':'project','scopeDescription':'fixture','mode':'partial','allow':['.ai-project/**','app.ets','.agents/skills/harmonyos-app-reimplementation/**'],'deny':['.agents/user-private/**']}
    def tearDown(self): self.tmp.cleanup()
    def write(self):
        data=json.dumps(self.policy).encode();self.p.write_bytes(data)
        return hashlib.sha256(data).hexdigest(),data
    def test_only_literal(self):
        checksum,data=self.write()
        out=preview(self.root,checksum,'synthetic confirmation')
        self.assertEqual(self.p.read_bytes(),data)
        self.assertFalse((self.root/'.agents').exists())
        b=ReadBoundary(self.root,policy=json.loads(out['afterPolicyText']))
        self.assertTrue(b.allowed('.agents'))
        self.assertFalse(b.allowed('.agents/skills'))
        self.assertFalse(b.allowed('.agents/user-private/files'))
        self.assertFalse(b.allowed('secret.ets'))
    def test_false_digest(self):
        self.write()
        with self.assertRaises(ValueError):preview(self.root,'0'*64,'fixture')
    def test_no_broadening_past_deny(self):
        self.policy['deny'].append('.agents/**')
        checksum,_=self.write()
        with self.assertRaises(ValueError):preview(self.root,checksum,'fixture')
    def test_no_unneeded_change(self):
        self.policy['allow'].append('.agents')
        checksum,_=self.write()
        with self.assertRaises(ValueError):preview(self.root,checksum,'fixture')
    def test_duplicate_json_rejected(self):
        _, data=self.write(); bad=data[:-1]+b', "mode":"partial"}';self.p.write_bytes(bad)
        with self.assertRaises(ValueError):preview(self.root,hashlib.sha256(bad).hexdigest(),'fixture')
if __name__=='__main__':unittest.main()

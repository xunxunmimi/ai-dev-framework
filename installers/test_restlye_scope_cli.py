from pathlib import Path
import json
import subprocess
import sys
import unittest
from test_register_exact_parent import PolicyRegistrationFixture
from register_exact_parent import sha
CLI=Path(__file__).with_name('restyle_scope_cli.py')
class CliTest(PolicyRegistrationFixture):
    def test_cli_review_independent_from_registration(self):
        nonce='2'*32
        cmd=[sys.executable,str(CLI),'--project',str(self.root),'--nonce',nonce,
             '--policy-before-sha256',self.a]
        preview=subprocess.run(cmd+['preview'],check=True,capture_output=True,text=True)
        p=json.loads(preview.stdout)
        self.assertNotIn('afterPolicyText',p)
        self.assertEqual(self.p.read_bytes(),self.before)
        opts=['register','--policy-after-sha256',p['policyAfterSha256'],
              '--installation-manifest-sha256',sha(self.manifest),
              '--owner-approval-reference','synthetic independent grant',
              '--writer-stop-evidence','synthetic stop']
        denial=subprocess.run(cmd+opts+['--approved-exact-path','.agents'],capture_output=True,text=True)
        self.assertEqual(denial.returncode,2)
        self.assertEqual(self.p.read_bytes(),self.before)
        for path in p['exactReadAdditions']:opts+=['--approved-exact-path',path]
        output=subprocess.run(cmd+opts,check=True,capture_output=True,text=True)
        self.assertEqual(json.loads(output.stdout)['status'],'registered-exact-parent')
        self.assertEqual(sha(self.p.read_bytes()),p['policyAfterSha256'])
if __name__=='__main__':unittest.main(verbosity=2)

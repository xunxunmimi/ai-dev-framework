from pathlib import Path
import fcntl
import json
import tempfile
import unittest
from exact_restyle_scope import preview
from transition_scope_preview import preview as transition_preview
from register_exact_parent import register,sha,LOCKS,MANIFEST
class PolicyRegistrationFixture(unittest.TestCase):
 def setUp(self):
  self.t=tempfile.TemporaryDirectory();self.root=Path(self.t.name).resolve()
  self.p=self.root/'.ai-project/read-policy.json'; self.p.parent.mkdir()
  self.before=json.dumps({'schemaVersion':'1.0','projectRoot':str(self.root),'confirmed':True,'mode':'partial','allow':['.ai-project/**','camera.ets'],'deny':['credentials.ets'],'scopeDescription':'explicit synthetic'}).encode()
  self.p.write_bytes(self.before);self.p.chmod(0o640)
  for path in LOCKS:(self.root/path).touch()
  self.manifest=b'{"schemaVersion":"1.0","taskProtocolVersion":"2","framework":"harmonyos-daily-development-rules","lifecycle":"harmony-maintenance"}';(self.root/MANIFEST).write_bytes(self.manifest)
  self.a=sha(self.before)
  self.b=preview(self.root,self.a,'owner exact confirmation fixture')['policyAfterSha256']
 def tearDown(self):self.t.cleanup()
 def run_register(self,after=None,fault=None):
  return register(self.root,self.a,after or self.b,sha(self.manifest),'owner exact confirmation fixture','synthetic independent stop evidence',fault=fault)
 def test_exact_and_preserve_mode_archive(self):
  self.assertFalse(self.run_register()['dailyTransitionApplied'])
  self.assertEqual(sha(self.p.read_bytes()),self.b)
  self.assertEqual(self.p.stat().st_mode&0o777,0o640)
  self.assertEqual((self.p.parent/f'read-policy-exact-preimage-{self.a}.json').read_bytes(),self.before)
 def test_wrong_digest_and_active_task(self):
  with self.assertRaises(ValueError):self.run_register('a'*64)
  (self.root/'.ai-project/tasks/alpha').mkdir(parents=True)
  with self.assertRaises(ValueError):self.run_register()
  self.assertEqual(self.p.read_bytes(),self.before)
 def test_no_attempt_while_lock_held(self):
  with (self.root/LOCKS[-1]).open('rb') as held:
   fcntl.flock(held, fcntl.LOCK_EX|fcntl.LOCK_NB)
   with self.assertRaises(BlockingIOError):self.run_register()
  self.assertEqual(self.p.read_bytes(),self.before)
 def test_backed_up_interruption_replay(self):
  def crash(point):
   if point=='archive-durable':raise RuntimeError('synthetic stop')
  with self.assertRaises(RuntimeError):self.run_register(fault=crash)
  self.assertEqual(self.p.read_bytes(),self.before);self.run_register()
  self.assertEqual(sha(self.p.read_bytes()),self.b)
 def test_no_override_after_competing_edit(self):
  def edit(point):
   if point=='staged':self.p.write_bytes(self.before+b' ')
  with self.assertRaises(ValueError):self.run_register(fault=edit)
  self.assertEqual(self.p.read_bytes(),self.before+b' ')
 def test_exact_once_for_formal_transition(self):
  nonce='f'*32
  expected=transition_preview(self.root,nonce,self.a)
  args=(self.root,self.a,expected['policyAfterSha256'],sha(self.manifest),'approved exact fixture','synthetic stop')
  with self.assertRaises(ValueError):register(*args,nonce=nonce,approved_exact_delta=['.agents'])
  result=register(*args,nonce=nonce,approved_exact_delta=expected['exactReadAdditions'])
  self.assertEqual(result['newExactReadPaths'],expected['exactReadAdditions'])
  self.assertEqual(sha(self.p.read_bytes()),expected['policyAfterSha256'])
  self.assertFalse((self.root/'.agents').exists())
if __name__=='__main__':unittest.main(verbosity=2)

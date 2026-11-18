import os
"""Only temporary 2.6.14/2.6.15 fixtures; no native input accepted."""
import json,subprocess,sys,tempfile,unittest,hashlib,os
from pathlib import Path
from task_runtime import suite_digest
from governance_journal import bytes_for
OLD=Path(os.environ['RESTYLE_TEST_OLD_PIN']) if os.environ.get('RESTYLE_TEST_OLD_PIN') else None;NEW=Path(os.environ['RESTYLE_TEST_NEW_PIN']) if os.environ.get('RESTYLE_TEST_NEW_PIN') else None
HERE=Path(__file__).parent
@unittest.skipUnless(OLD is not None and NEW is not None, 'set RESTYLE_TEST_OLD_PIN and RESTYLE_TEST_NEW_PIN to independently verified 2.6.14/2.6.15 fixtures')
class Smoke(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.base=Path(self.tmp.name).resolve();self.root=self.base/'target';self.source=self.base/'source';self.nonce='a'*32
  for root in (self.root,self.source):
   (root/'.ai-project').mkdir(parents=True);(root/'.ai-project/read-policy.json').write_text(json.dumps({'schemaVersion':'1.0','confirmed':True,'projectRoot':str(root),'mode':'complete','allow':['**'],'deny':[],'scopeDescription':'synthetic independent owner grant'}))
  subprocess.run(['bash',str(OLD/'installers/setup-project.sh'),'harmony-daily',str(self.root)],check=True,capture_output=True)
  (self.root/'.ai-project/reimplementation-install.lock').touch()
  p=self.root/'.ai-project/read-policy.json';d=json.loads(p.read_text());d.update(mode='partial',allow=['.ai-project','.ai-project/**','.codex-rules','.codex-rules/**','AGENTS.md']);p.write_text(json.dumps(d))
  self.manifest=(self.root/'.ai-project/framework-installation.json').read_bytes();self.msha=self.sha(self.manifest)
  self.oldsha=suite_digest(OLD);self.newsha=suite_digest(NEW)
 def tearDown(self):self.tmp.cleanup()
 def sha(self,b):return hashlib.sha256(b).hexdigest()
 def runcli(self,script,args,ok=True):
  env=dict(os.environ);env.pop('PYTHONPATH',None)
  p=subprocess.run([sys.executable,'-B',str(HERE/script),*map(str,args)],capture_output=True,text=True,env=env)
  self.assertEqual(p.returncode,0 if ok else 2,p.stdout+'\n'+p.stderr)
  return json.loads(p.stdout)
 def common(self,action):return ['--project',self.root,action,'--source',self.source,'--new-pin',NEW,'--new-pin-sha256',self.newsha,'--nonce',self.nonce,'--manifest-sha256',self.msha]
 def register(self):
  before=self.sha((self.root/'.ai-project/read-policy.json').read_bytes());args=['--project',self.root,'--nonce',self.nonce,'--policy-before-sha256',before]
  p=self.runcli('restyle_scope_cli.py',args+['preview'])
  extra=[]
  for n in p['exactReadAdditions']:extra+=['--approved-exact-path',n]
  self.runcli('restyle_scope_cli.py',args+['register','--policy-after-sha256',p['policyAfterSha256'],'--installation-manifest-sha256',self.msha,'--owner-approval-reference','synthetic exact scope approval','--writer-stop-evidence','synthetic no writers']+extra)
  archive=self.root/'.ai-project'/('read-policy-exact-preimage-'+before+'.json')
  self.assertTrue(archive.is_file());self.assertEqual(self.sha(archive.read_bytes()),before)
 def test_full_cli_partial_registration_stage_plan_apply_rollback(self):
  self.register()
  p=self.runcli('restyle_transition_cli.py',self.common('stage-preview'))
  self.assertFalse((self.root/'.agents').exists())
  self.runcli('restyle_transition_cli.py',self.common('stage')+['--approved-plan-sha256',p['planSha256'],'--owner-write-approval-reference','synthetic stage only approval','--writer-stop-evidence','synthetic no writers'])
  plan=self.runcli('restyle_transition_cli.py',self.common('plan')+['--old-pin',OLD,'--old-pin-sha256',self.oldsha,'--writer-stop-evidence','synthetic no writers'])
  record=self.base/'reviewed-record.json';record.write_bytes(bytes_for(plan['record']))
  extra=[]
  for n in plan['requiredWritePaths']:extra+=['--approved-write-path',n]
  for action in ('apply','rollback'):
   r=self.runcli('restyle_transition_cli.py',['--project',self.root,action,'--record',record,'--approved-plan-sha256',plan['planSha256'],'--owner-write-approval-reference','synthetic independently authenticated governance approval','--writer-stop-evidence','synthetic still stopped']+extra)
   self.assertEqual(r['status'],'complete' if action=='apply' else 'restored')
  self.assertEqual((self.root/'.ai-project/framework-installation.json').read_bytes(),self.manifest)
  self.assertTrue((self.root/'.agents/skills'/('.restyle-quarantine-'+self.nonce)).is_dir())
 def test_missing_scope_fails_no_stage(self):
  self.runcli('restyle_transition_cli.py',self.common('stage-preview'),False)
  self.assertFalse((self.root/'.agents').exists())
 def test_wrong_stage_digest_fails_no_write(self):
  self.register()
  self.runcli('restyle_transition_cli.py',self.common('stage')+['--approved-plan-sha256','0'*64,'--owner-write-approval-reference','synthetic approval','--writer-stop-evidence','synthetic stop'],False)
  self.assertFalse((self.root/'.agents').exists())
 def test_existing_task_blocks_staging(self):
  self.register();p=self.root/'.ai-project/tasks/active';p.mkdir(parents=True);(p/'task.json').write_text('{}')
  self.runcli('restyle_transition_cli.py',self.common('stage-preview'),False)
  self.assertFalse((self.root/'.agents').exists())
 def test_staging_no_overwrite(self):
  self.register();p=self.runcli('restyle_transition_cli.py',self.common('stage-preview'))
  args=self.common('stage')+['--approved-plan-sha256',p['planSha256'],'--owner-write-approval-reference','synthetic stage approval','--writer-stop-evidence','synthetic stop']
  self.runcli('restyle_transition_cli.py',args)
  first=(self.root/'.agents/skills'/('.restyle-stage-'+self.nonce)/'SKILL.md').read_bytes()
  self.runcli('restyle_transition_cli.py',args,False)
  self.assertEqual(first,(self.root/'.agents/skills'/('.restyle-stage-'+self.nonce)/'SKILL.md').read_bytes())
if __name__=='__main__':unittest.main(verbosity=2)

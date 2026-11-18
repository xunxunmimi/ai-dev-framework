import os
"""仅临时真实版本夹具，绝不接受外部项目参数。"""
import json,hashlib,sys,tempfile,subprocess,unittest
from pathlib import Path
import daily_restyle_transition as api
from task_runtime import suite_digest
from governance_journal import LOCK,JOURNAL,bytes_for
from read_boundary import ReadBoundary
OLD=Path(os.environ['RESTYLE_TEST_OLD_PIN']) if os.environ.get('RESTYLE_TEST_OLD_PIN') else None
NEW=Path(os.environ['RESTYLE_TEST_NEW_PIN']) if os.environ.get('RESTYLE_TEST_NEW_PIN') else None
def approval(root,r,digest):
 return {'planSha256':digest,'targetRoot':str(root),'scope':api.write_scope(r),'evidence':'SYNTHETIC independent governance-write authorization; not native permission','appWritesAuthorized':False}
def apply(root,r,digest,fault=None):return api.apply(root,r,digest,fault,write_approval=approval(root,r,digest))
def rollback(root,r,digest,fault=None):return api.rollback(root,r,digest,fault,write_approval=approval(root,r,digest))
@unittest.skipUnless(OLD is not None and NEW is not None, 'set RESTYLE_TEST_OLD_PIN and RESTYLE_TEST_NEW_PIN to independently verified 2.6.14/2.6.15 fixtures')
class EndToEnd(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(prefix='synthetic-integrated-');self.base=Path(self.tmp.name).resolve();self.root=self.base/'target';self.source=self.base/'source'
  for root in (self.root,self.source):
   (root/'.ai-project').mkdir(parents=True);(root/'.ai-project/read-policy.json').write_text(json.dumps({'schemaVersion':'1.0','scopeKind':'project','confirmed':True,'projectRoot':str(root),'mode':'complete','allow':['**'],'deny':[],'scopeDescription':'synthetic owner fixture authorization'}))
  subprocess.run(['bash',str(OLD/'installers/setup-project.sh'),'harmony-daily',str(self.root)],check=True,capture_output=True)
  (self.root/LOCK).touch();(self.root/JOURNAL).mkdir();self.stage='.restyle-stage-'+'b'*32
  content=api.inventory(NEW)
  b=ReadBoundary(self.root);sb=ReadBoundary(self.source)
  content['selection.json']=bytes_for({'schemaVersion':'1.0','enabled':True,'projectRoot':str(self.root),'sourceRoot':str(self.source),'intent':'implementation-preserving-restyle','sourceReadOnly':True,'sourcePolicySha256':sb.sha256,'targetPolicySha256':b.sha256,'workflowPhase':'rebrand-production-before-acceptance','dailyHandoff':'explicit-after-acceptance'})
  for name,data in content.items():
   p=self.root/api.PARENT/self.stage/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data)
  self.original=(self.root/api.MANIFEST).read_bytes();self.oldsha=suite_digest(OLD);self.newsha=suite_digest(NEW)
  self.record=self.prepare();self.approved=api.sha(bytes_for(self.record))
 def prepare(self):return api.prepare(self.root,self.source,OLD,self.oldsha,api.sha(self.original),NEW,self.newsha,self.stage,'synthetic explicit all-writer stop attestation')
 def tearDown(self):self.tmp.cleanup()
 def legacy(self,expected):
  p=subprocess.run([sys.executable,'-B','-c','import sys;sys.path.insert(0,sys.argv[1]);from task_runtime import protocol_gate;from pathlib import Path;protocol_gate(Path(sys.argv[2]))',str(OLD/'shared/task-routing'),str(self.root)],capture_output=True)
  self.assertEqual(p.returncode==0,expected)
 def test_complete_and_rollback(self):
  self.assertEqual(apply(self.root,self.record,self.approved)['status'],'complete');self.legacy(False)
  self.assertEqual(rollback(self.root,self.record,self.approved)['status'],'restored');self.legacy(True)
  self.assertFalse((self.root/api.TARGET).exists())
 def test_fault_at_every_apply_phase(self):
  for phase in ['journal','park',api.LIFECYCLE,api.ROUTING,'skill','complete']:
   case=EndToEnd();case.setUp()
   try:
    def fail(p):
     if p==phase:raise RuntimeError('synthetic crash')
    with self.subTest(phase=phase),self.assertRaises(RuntimeError):apply(case.root,case.record,case.approved,fail)
    rollback(case.root,case.record,case.approved);case.legacy(True)
   finally:case.tearDown()
 def test_skill_drift_blocks_rollback(self):
  apply(self.root,self.record,self.approved);(self.root/api.SKILL).write_bytes(b'owner edit')
  current=(self.root/api.MANIFEST).read_bytes()
  with self.assertRaises(ValueError):rollback(self.root,self.record,self.approved)
  self.assertEqual(current,(self.root/api.MANIFEST).read_bytes());self.legacy(False)
 def test_quarantine_crash_replay(self):
  apply(self.root,self.record,self.approved)
  def crash(phase):raise RuntimeError('crash after atomic quarantine')
  with self.assertRaises(RuntimeError):rollback(self.root,self.record,self.approved,crash)
  self.assertFalse((self.root/api.TARGET).exists());self.legacy(False)
  q=self.root/api.PARENT/('.restyle-quarantine-'+self.record['transactionId'])
  self.assertTrue(q.is_dir())
  rollback(self.root,self.record,self.approved);self.legacy(True)
  self.assertTrue(q.is_dir())
 def test_postpublish_inventory_drift_blocks_complete(self):
  def mutate(phase):
   if phase=='skill':(self.root/api.TARGET/'extra.txt').write_text('unexpected')
  with self.assertRaises(ValueError):apply(self.root,self.record,self.approved,mutate)
  self.assertEqual((self.root/api.MANIFEST).read_bytes(),api.unpack(self.record['prepared']))
 def test_owner_postcheck_blocks_complete(self):
  def mutate(phase):
   if phase=='skill':(self.root/next(iter(self.record['ownerFactsSha256']))).write_text('owner edit')
  with self.assertRaises(ValueError):apply(self.root,self.record,self.approved,mutate)
  self.legacy(False)
 def test_strict_record_and_duplicates(self):
  with self.assertRaises(ValueError):api.decode_record(b'{"x":1,"x":2}')
  r=dict(self.record);r['unexpected']=True
  with self.assertRaises(ValueError):apply(self.root,r,api.sha(bytes_for(r)))
  self.assertEqual((self.root/api.MANIFEST).read_bytes(),self.original)
 def test_original_modes_preserved(self):
  import os,stat
  for n in (api.MANIFEST,api.LIFECYCLE,api.ROUTING):os.chmod(self.root/n,0o640)
  apply(self.root,self.record,self.approved)
  for n in (api.MANIFEST,api.LIFECYCLE,api.ROUTING):self.assertEqual(stat.S_IMODE((self.root/n).stat().st_mode),0o640)
  rollback(self.root,self.record,self.approved)
  for n in (api.MANIFEST,api.LIFECYCLE,api.ROUTING):self.assertEqual(stat.S_IMODE((self.root/n).stat().st_mode),0o640)
 def test_historical_nested_pin_roundtrip(self):
  m=json.loads(self.original);historical=str(self.root/'.framework-pins'/(m['suiteVersion']+'-'+self.oldsha[:16]));m['suiteRoot']=historical
  self.original=bytes_for(m);(self.root/api.MANIFEST).write_bytes(self.original)
  r=api.prepare(self.root,self.source,OLD,self.oldsha,api.sha(self.original),NEW,self.newsha,self.stage,'synthetic writer stop',historical,'synthetic independent native manifest receipt')
  approved=api.sha(bytes_for(r));apply(self.root,r,approved);rollback(self.root,r,approved)
  self.assertEqual((self.root/api.MANIFEST).read_bytes(),self.original)
  self.assertFalse((self.root/'.framework-pins').exists())
 def test_quarantine_drift_blocks_replay(self):
  apply(self.root,self.record,self.approved)
  def crash(phase):raise RuntimeError('interrupted')
  with self.assertRaises(RuntimeError):rollback(self.root,self.record,self.approved,crash)
  q=self.root/api.PARENT/('.restyle-quarantine-'+self.record['transactionId'])
  (q/'SKILL.md').write_text('external change')
  before=(self.root/api.MANIFEST).read_bytes()
  with self.assertRaises(ValueError):rollback(self.root,self.record,self.approved)
  self.assertEqual((self.root/api.MANIFEST).read_bytes(),before)
 def test_partial_exact_agents_policy_reissuance(self):
  # Exact parent and descendants, no `.agents/**` or directory subtree shorthand.
  fixed=self.stage[len('.restyle-stage-'):];q=api.PARENT+'/.restyle-quarantine-'+fixed
  allowed={'.ai-project','.codex-rules','.agents',api.PARENT,api.ROUTING,api.INSTALL_LOCK,q,api.TARGET,api.PARENT+'/'+self.stage}
  for base in (self.root/'.ai-project',self.root/'.codex-rules'):
   allowed.update(x.relative_to(self.root).as_posix() for x in base.rglob('*'))
  allowed.update(['.ai-project/tasks','.ai-project/upgrades','.ai-project/operation-holds','.ai-project/checkpoints','.ai-project/identity.json','.ai-project/operation-holds.json','.ai-project/legacy-baseline.json','.ai-project/task-input.json',JOURNAL+'/journal.json'])
  for name in self.record['skillSha256']:
   for prefix in (q,api.TARGET,api.PARENT+'/'+self.stage):
    path=Path(prefix)/name;allowed.add(str(path))
    allowed.update(str(x) for x in path.parents if str(x) not in ('.',))
  policy=json.loads((self.root/'.ai-project/read-policy.json').read_bytes());policy.update(mode='partial',allow=sorted(allowed))
  paths=[];digests=[]
  for revision in ('owner reviewed paths v1','owner reissued same paths v2'):
   policy['scopeDescription']=revision;(self.root/'.ai-project/read-policy.json').write_text(json.dumps(policy))
   selection=self.root/api.PARENT/self.stage/'selection.json';obj=json.loads(selection.read_bytes());obj['targetPolicySha256']=ReadBoundary(self.root).sha256;selection.write_bytes(bytes_for(obj))
   r=self.prepare();paths.append(api.PARENT+'/.restyle-quarantine-'+r['transactionId']);digests.append(r['coreSha256'])
  self.assertEqual(paths,[q,q]);self.assertNotEqual(*digests)
  self.assertFalse(any('*' in x for x in allowed))
  digest=api.sha(bytes_for(r));apply(self.root,r,digest);rollback(self.root,r,digest)
  self.assertTrue((self.root/q).is_dir())
 def test_read_grant_not_write_approval(self):
  with self.assertRaises(ValueError):api.apply(self.root,self.record,self.approved)
  self.assertEqual((self.root/api.MANIFEST).read_bytes(),self.original)
if __name__=='__main__':unittest.main(verbosity=2)

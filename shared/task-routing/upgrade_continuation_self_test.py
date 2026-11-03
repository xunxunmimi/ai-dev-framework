#!/usr/bin/env python3
"""活动任务跨官方升级的隔离回归；不接触真实业务工程。"""
import json, hashlib, sys, tempfile, unittest
from pathlib import Path
from unittest import mock
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'installers'))
import task_runtime as rt
import project_framework as pf
from runtime_self_test import fixture
from project_framework import MANAGED_MAPPINGS, record_manifest, upgrade, restore

class ContinuationTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(prefix='upgrade-chain-test-'); self.base=Path(self.tmp.name).resolve()
  self.root=fixture(self.base/'project'); self.suite=self.base/'suite'; self.suite.mkdir(); (self.suite/'VERSION').write_text('2.5.1\n')
  self.maps=MANAGED_MAPPINGS['ios-daily-development-rules']
  for dest, source in self.maps:
   p=self.suite/source;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('old '+dest+'\n')
   if dest in rt.IOS_252_MAPPING:
    p=self.root/dest;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('old '+dest+'\n')
  oldmaps=[pair for pair in self.maps if pair[0] in rt.IOS_252_MAPPING]
  rt.atomic_json(self.root/'.ai-project/lifecycle.json',{'state':'ios-maintenance','frameworkVersion':'2.5.1','routing':'stable'})
  record_manifest(self.root,self.suite,'ios-daily-development-rules','ios-maintenance',oldmaps)
  (self.suite/'VERSION').write_text('2.5.2\n')
  with mock.patch.dict(pf.MANAGED_MAPPINGS,{'ios-daily-development-rules':oldmaps}):
   anchor=Path(upgrade(self.root,self.suite)['transaction'])
  self.anchor=anchor.name; self.anchor_sha=hashlib.sha256((anchor/'transaction.json').read_bytes()).hexdigest()
  self.name='Demo.xcodeproj/project.xcworkspace/xcuserdata/test.xcuserdatad/UserInterfaceState.xcuserstate'; self.ui=self.root/self.name;self.ui.parent.mkdir(parents=True);self.ui.write_bytes(b'old')
  self.old=hashlib.sha256(b'old').hexdigest();self.new=hashlib.sha256(b'new').hexdigest()
  self.state=rt.begin(self.root,'task',['one.swift'],'fixture');self.oldbaseline=self.state['baseline'].copy()
  statepath=rt.task_dir(self.root,'task')/'state.json'; state=rt.read_json(statepath)
  state['completion']['compiled']={'status':'passed','binding':rt.binding(self.root,'task','D3')};rt.atomic_json(statepath,state)
  self.ui.write_bytes(b'new')
  self.transactions=[]
  for version in ('2.6.1','2.6.2-ruler-task-handoff'):
   (self.suite/'VERSION').write_text(version+'\n'); (self.suite/self.maps[0][1]).write_text('new '+version+'\n')
   self.transactions.append(Path(upgrade(self.root,self.suite)['transaction']).name)
  tx=[json.loads((self.root/'.ai-project/upgrades'/x/'transaction.json').read_text()) for x in self.transactions];key='.ai-project/framework-installation.json'
  manifests=[json.loads(tx[0]['before'][key]),json.loads(tx[0]['after'][key]),json.loads(tx[1]['after'][key])]
  self.pins=[{'version':m['suiteVersion'],'suiteRoot':m['suiteRoot'],'suiteSha256':m['suiteSha256']} for m in manifests]
  self.auth={'confirmed_by':'fixture user','authorization_source':'synthetic authorization'}
 def tearDown(self): self.tmp.cleanup()
 def accept(self, ids=None, path=None, old=None, new=None, apply=False, preview=None, task='task', auth=None):
  return rt.accept_framework_upgrade(self.root,task,self.transactions if ids is None else ids,self.name if path is None else path,self.old if old is None else old,self.new if new is None else new,apply=apply,preview_hash=preview,anchor_id=self.anchor,anchor_sha256=self.anchor_sha,trusted_suites=self.pins,**(self.auth if auth is None else auth))
 def preview(self): return self.accept()['previewSha256']
 def test_success_scope_and_metadata(self):
  with self.assertRaises(ValueError): rt.check_scope(self.root,rt.read_json(rt.task_dir(self.root,'task')/'state.json'))
  preview=self.preview(); self.assertEqual(self.accept(apply=True,preview=preview)['status'],'accepted')
  saved=rt.read_json(rt.task_dir(self.root,'task')/'state.json'); self.assertEqual(saved['baseline'],self.oldbaseline)
  with self.assertRaises(ValueError): rt.check_scope(self.root,saved)
  p=rt.acknowledge_metadata(self.root,'task',self.name,self.old,self.new,'fixture',**self.auth)
  rt.acknowledge_metadata(self.root,'task',self.name,self.old,self.new,'fixture',True,p['previewSha256'],**self.auth)
  rt.check_scope(self.root,rt.read_json(rt.task_dir(self.root,'task')/'state.json'))
 def test_reject_bad_inputs_and_extra_business(self):
  for kw in ({'task':'wrong'}, {'path':'../outside'}, {'old':'0'*64}, {'new':'0'*64}, {'ids':['x']}, {'ids':self.transactions[1:]}, {'auth':{'confirmed_by':'','authorization_source':'x'}}):
   with self.subTest(kw=kw), self.assertRaises((ValueError,FileNotFoundError)):self.accept(**kw)
  (self.root/'two.swift').write_text('outside')
  with self.assertRaises(ValueError):self.accept()
 def test_bad_transactions_and_drift(self):
  p=self.root/'.ai-project/upgrades'/self.transactions[0]/'transaction.json'; original=p.read_bytes(); tx=json.loads(original)
  for edit in ('incomplete','project','after','path'):
   t=json.loads(original)
   if edit=='incomplete':t['status']='applying'
   elif edit=='project':t['projectRoot']='/other'
   elif edit=='after':t['after'][self.maps[0][0]]='tampered'
   else:t['before']['other.swift']='bad';t['after']['other.swift']='bad';t['planned']['other.swift']='bad'
   rt.atomic_json(p,t)
   with self.subTest(edit=edit), self.assertRaises(ValueError):self.accept()
  p.write_bytes(original)
  (self.root/self.maps[0][0]).write_text('drift')
  with self.assertRaises(ValueError):self.accept()
 def test_lock_and_interruption_and_idempotency(self):
  preview=self.preview()
  with rt.project_lock(self.root,'.ai-project/tasks.lock'):
   with self.assertRaises(ValueError):self.accept(apply=True,preview=preview)
  real=rt.atomic_json
  def crash(path,value):
   if Path(path).name=='state.json':raise OSError('interrupted')
   return real(path,value)
  with mock.patch.object(rt,'atomic_json',crash):
   with self.assertRaises(OSError):self.accept(apply=True,preview=preview)
  self.assertNotIn('acceptedFrameworkUpgrade',rt.read_json(rt.task_dir(self.root,'task')/'state.json'))
  self.assertEqual(self.accept(apply=True,preview=preview)['status'],'accepted')
  self.assertEqual(self.accept(apply=True,preview=preview)['status'],'already-accepted')
 def test_backup_publication_failure_can_retry(self):
  preview=self.preview()
  with mock.patch.object(rt.os,'link',side_effect=OSError('publish failed')):
   with self.assertRaises(OSError):self.accept(apply=True,preview=preview)
  folder=rt.task_dir(self.root,'task')/'upgrade-confirmations'
  self.assertEqual(list(folder.glob('*.json')),[])
  self.assertEqual(self.accept(apply=True,preview=preview)['status'],'accepted')
 def test_stale_evidence_and_rollback_source(self):
  p=rt.task_dir(self.root,'task')/'state.json'; report=p.parent/'historical.json'; report.write_text('{"status":"passed"}\n'); original=report.read_bytes()
  v=self.preview();self.accept(apply=True,preview=v)
  self.assertEqual(rt.read_json(p)['completion']['compiled']['status'],'stale')
  self.assertEqual(report.read_bytes(),original)
  record=rt.read_json(self.root/'.ai-project/upgrades'/self.transactions[-1]/'transaction.json');self.assertEqual(record['before'][self.maps[0][0]],'new 2.6.1\n')
 def test_accepted_record_revalidates_transaction_and_pending_ui(self):
  self.accept(apply=True,preview=self.preview())
  self.ui.write_bytes(b'other')
  with self.assertRaises(ValueError):rt.acknowledge_metadata(self.root,'task',self.name,self.old,self.new,'fixture',**self.auth)
  self.ui.write_bytes(b'new')
  tx=self.root/'.ai-project/upgrades'/self.transactions[0]/'transaction.json'; data=tx.read_bytes();tx.write_bytes(data+b' ')
  with self.assertRaises(ValueError):rt.acknowledge_metadata(self.root,'task',self.name,self.old,self.new,'fixture',**self.auth)
 def test_first_manifest_rule_identity_cannot_be_replaced(self):
  p=rt.task_dir(self.root,'task')/'state.json';state=rt.read_json(p)
  state['completion']['compiled']['binding']['rulesIdentity']['version']='wrong';rt.atomic_json(p,state)
  with self.assertRaises(ValueError):self.accept()
 def test_forged_extra_mapping_with_self_consistent_transaction(self):
  name='.codex-rules/extra-unmanaged.txt';source='shared/task-routing/extra-unmanaged.txt';value='injected\n';h=hashlib.sha256(value.encode()).hexdigest()
  (self.suite/source).write_text(value);(self.root/name).write_text(value)
  paths=[self.root/'.ai-project/upgrades'/x/'transaction.json' for x in self.transactions];a,b=[json.loads(p.read_text()) for p in paths]; key='.ai-project/framework-installation.json'
  entry={'path':name,'source':source,'installedSha256':h,'sourceSha256':h}
  mid=json.loads(a['after'][key]);mid['managedFiles'].append(entry);mt=json.dumps(mid,ensure_ascii=False,indent=2)+'\n'
  final=json.loads(b['after'][key]);final['managedFiles'].append(entry);final['suiteSha256']=rt.suite_digest(self.suite);ft=json.dumps(final,ensure_ascii=False,indent=2)+'\n'
  a['before'][name]=None;a['planned'][name]=a['after'][name]=value;a['planned'][key]=a['after'][key]=mt
  b['before'][name]=b['planned'][name]=b['after'][name]=value;b['before'][key]=mt;b['planned'][key]=b['after'][key]=ft
  for p,tx in zip(paths,(a,b)):rt.atomic_json(p,tx)
  (self.root/key).write_text(ft);rt.rules_identity(self.root)
  with self.assertRaisesRegex(ValueError,'映射'):self.accept()
 def test_lifecycle_modify_remove_add_rejected(self):
  p=self.root/'.ai-project/upgrades'/self.transactions[-1]/'transaction.json';original=p.read_bytes();key='.ai-project/lifecycle.json';life_path=self.root/key; life_original=life_path.read_bytes()
  for mutation in ('modify','remove','add'):
   t=json.loads(original);life=json.loads(t['after'][key])
   if mutation=='modify':life['routing']='changed'
   elif mutation=='remove':del life['routing']
   else:life['extra']='added'
   content=json.dumps(life,ensure_ascii=False,indent=2)+'\n';t['planned'][key]=t['after'][key]=content;rt.atomic_json(p,t);life_path.write_text(content)
   with self.subTest(mutation=mutation),self.assertRaisesRegex(ValueError,'生命周期'):self.accept()
  p.write_bytes(original);life_path.write_bytes(life_original)
 def test_trusted_mapping_rejects_alias_unknown_version_and_extra_fields(self):
  p=self.root/'.ai-project/upgrades'/self.transactions[-1]/'transaction.json';original=p.read_bytes();key='.ai-project/framework-installation.json';manifest_path=self.root/key; manifest_original=manifest_path.read_bytes()
  for mutation in ('alias','version','manifest_extra','entry_extra'):
   t=json.loads(original);m=json.loads(t['after'][key])
   if mutation=='alias':
    alias=self.suite/'shared/task-routing/alias.py';alias.write_bytes((self.suite/m['managedFiles'][0]['source']).read_bytes());m['managedFiles'][0]['source']='shared/task-routing/alias.py';m['suiteSha256']=rt.suite_digest(self.suite)
   elif mutation=='version':m['suiteVersion']='unknown'
   elif mutation=='manifest_extra':m['unexpected']='value'
   else:m['managedFiles'][0]['extra']='value'
   content=json.dumps(m,ensure_ascii=False,indent=2)+'\n';t['planned'][key]=t['after'][key]=content;rt.atomic_json(p,t);manifest_path.write_text(content)
   with self.subTest(mutation=mutation),self.assertRaises((ValueError,FileNotFoundError)):self.accept()
   if mutation=='alias':alias.unlink()
  p.write_bytes(original);manifest_path.write_bytes(manifest_original)
 def test_anchor_identity_tamper_rejected(self):
  anchor=self.root/'.ai-project/upgrades'/self.anchor/'transaction.json';anchor.write_bytes(anchor.read_bytes()+b' ')
  with self.assertRaisesRegex(ValueError,'起点事务摘要'):self.accept()
 def test_pinned_source_and_anchor_hash_rejected_when_changed(self):
  before=self.anchor_sha;self.anchor_sha='0'*64
  with self.assertRaisesRegex(ValueError,'起点事务摘要'):self.accept()
  self.anchor_sha=before
  for key,value in [('suiteRoot','/other'),('suiteSha256','0'*64)]:
   previous=self.pins[1][key];self.pins[1][key]=value
   with self.subTest(key=key),self.assertRaisesRegex(ValueError,'套件来源'):self.accept()
   self.pins[1][key]=previous
if __name__=='__main__':unittest.main(verbosity=2)

#!/usr/bin/env python3
"""Synthetic task-only bootstrap; no shared identity/policy, no Git or private reads."""
import json,os,sys,tempfile,unittest,subprocess
from pathlib import Path
from unittest import mock
from read_boundary import ReadBoundary,BoundaryError,task_only_scope,external_control_bytes
from task_runtime import begin,resume,checkpoint,verify_configuration,validate_report,run_verification,task_dir,project_lock
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'installers'))
import controlled_task_write as controlled
from transactional_files import sha

class TaskOnlyScenarios(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(prefix='task-only-fixture-');self.base=Path(self.tmp.name).resolve();self.root=self.base/'project';self.root.mkdir();self.iso=self.base/'isolated';self.iso.mkdir()
  (self.root/'Package.swift').write_text('// synthetic');(self.root/'.test').mkdir();(self.root/'.test/private.ini').write_text('synthetic secret')
  self.names=[f'Payment/file{i}.swift' for i in range(9)]
  for name in self.names:
   for root,data in [(self.root,'old'),(self.iso,'new')]:
    path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(data)
  self.spec={'schemaVersion':'1.0','scopeKind':'task-only','confirmed':True,'taskId':'pay','projectRoot':str(self.root),'authorizationSource':'synthetic user','scopeDescription':'synthetic task only','allowFiles':['Package.swift']+self.names,'identityFiles':['Package.swift'],'editableFiles':self.names,'denyDirectories':['.test'],'platform':'ios','identitySelection':{}}
 def tearDown(self):self.tmp.cleanup()
 def start(self):
  with task_only_scope(self.root,'pay',self.spec):return begin(self.root,'pay',self.names,'synthetic')
 def test_init_resume_partial_zero_private_and_no_external(self):
  op,st=os.open,os.stat
  def guarded_open(p,*a,**k):
   if str(p) in {'.test','private.ini'}:raise AssertionError('private open')
   return op(p,*a,**k)
  def guarded_stat(p,*a,**k):
   if str(p) in {'.test','private.ini'}:raise AssertionError('private stat')
   return st(p,*a,**k)
  with mock.patch('os.open',side_effect=guarded_open),mock.patch('os.stat',side_effect=guarded_stat),mock.patch('task_runtime.subprocess.run',side_effect=AssertionError('Git/build')):
   state=self.start()
   with task_only_scope(self.root,'pay'):
    self.assertEqual(resume(self.root,'pay')['status'],'active')
    report=verify_configuration(self.root,'pay');validate_report(self.root,report,'pay','D0',allow_partial=True)
    self.assertEqual(report['status'],'partial');self.assertEqual(state['baseRevision'],'unverified-read-scope')
    with self.assertRaises(BoundaryError):ReadBoundary(self.root).read_bytes('.test/private.ini')
  self.assertFalse((self.root/'.ai-project/read-policy.json').exists());self.assertFalse((self.root/'.ai-project/identity.json').exists())
  with self.assertRaises(BoundaryError):ReadBoundary(self.root)
 def test_spec_changed_or_wrong_task_or_global_policy_blocks(self):
  self.start();path=self.root/'.ai-project/tasks/pay/task-only.json';spec=json.loads(path.read_text());spec['allowFiles'].append('other.txt');path.write_text(json.dumps(spec))
  with task_only_scope(self.root,'pay'):
   with self.assertRaises((ValueError,BoundaryError)):resume(self.root,'pay')
  with task_only_scope(self.root,'other'):
   with self.assertRaises(BoundaryError):ReadBoundary(self.root)
  (self.root/'.ai-project/read-policy.json').write_text('{}')
  with task_only_scope(self.root,'pay'):
   with self.assertRaises(BoundaryError):ReadBoundary(self.root)
 def test_changed_spec_rejected_before_new_identity_file_open(self):
  self.start();extra=self.root/'unexpected.txt';extra.write_text('synthetic private')
  path=self.root/'.ai-project/tasks/pay/task-only.json';spec=json.loads(path.read_text());spec['allowFiles'].append('unexpected.txt');spec['identityFiles'].append('unexpected.txt');path.write_text(json.dumps(spec))
  original=os.open
  def guard(p,*a,**k):
   if str(p)=='unexpected.txt':raise AssertionError('new identity file opened before policy verification')
   return original(p,*a,**k)
  with mock.patch('os.open',side_effect=guard),task_only_scope(self.root,'pay'):
   with self.assertRaises(BoundaryError):resume(self.root,'pay')
 def test_deleted_spec_and_cross_task_cannot_rebind(self):
  self.start();(self.root/'.ai-project/tasks/pay/task-only.json').unlink()
  with task_only_scope(self.root,'pay',self.spec):
   with self.assertRaises(BoundaryError):ReadBoundary(self.root)
  with task_only_scope(self.root,'pay',self.spec):
   with self.assertRaises(BoundaryError):resume(self.root,'other')
 def test_other_task_private_metadata_not_authorized(self):
  self.start();other=self.root/'.ai-project/tasks/other';other.mkdir();(other/'attachment.txt').write_text('synthetic restricted')
  with task_only_scope(self.root,'pay'):
   reader=ReadBoundary(self.root)
   with mock.patch('os.open',side_effect=AssertionError('not authorized before open')):
    with self.assertRaises(BoundaryError):reader.read_bytes('.ai-project/tasks/other/attachment.txt')
 def test_partial_external_verification_blocked(self):
  self.start()
  with task_only_scope(self.root,'pay'),mock.patch('task_runtime.subprocess.run',side_effect=AssertionError('external command')):
   with self.assertRaises(BoundaryError):run_verification(self.root,'pay','D2','compiled',[sys.executable,'-c','pass'],self.root/'.ai-project/tasks/pay/report.json')
 def test_init_interruption_can_retry(self):
  import task_runtime
  real=task_runtime.atomic_json
  def broken(path,value):
   if path.name=='baseline.json':raise OSError('synthetic interruption')
   return real(path,value)
  with mock.patch('task_runtime.atomic_json',side_effect=broken):
   with self.assertRaises(OSError):self.start()
  self.assertFalse((self.root/'.ai-project/tasks/pay').exists())
  self.assertEqual(self.start()['status'],'active')

 def test_explicit_denied_directory_cannot_be_authorized(self):
  spec={**self.spec,'allowFiles':self.spec['allowFiles']+['.test/private.ini']}
  with task_only_scope(self.root,'pay',spec):
   original=os.open
   def guard(path,*a,**k):
    if str(path) in {'.test','private.ini'}:raise AssertionError('denied file opened')
    return original(path,*a,**k)
   with mock.patch('os.open',side_effect=guard):
    with self.assertRaises(BoundaryError):ReadBoundary(self.root)
 def test_denied_directory_change_invalidates_before_access(self):
  self.start();p=self.root/'.ai-project/tasks/pay/task-only.json';spec=json.loads(p.read_text());spec['denyDirectories']=[];p.write_text(json.dumps(spec))
  with task_only_scope(self.root,'pay'):
   with self.assertRaises(BoundaryError):resume(self.root,'pay')

 def test_unapproved_and_links_block(self):
  spec={**self.spec,'allowFiles':self.spec['allowFiles']+['.test/**']}
  with task_only_scope(self.root,'pay',spec):
   with self.assertRaises(BoundaryError):ReadBoundary(self.root)
  f=self.root/self.names[0];f.unlink();f.symlink_to(self.root/'.test/private.ini')
  with self.assertRaises((OSError,ValueError)):self.start()
  f.unlink();os.link(self.root/'.test/private.ini',f)
  with self.assertRaises((OSError,ValueError)):self.start()
 def test_conflicting_task_and_controlled_write(self):
  self.start();other={**self.spec,'taskId':'other'}
  with task_only_scope(self.root,'other',other):
   with self.assertRaises(ValueError):begin(self.root,'other',self.names,'overlap')
  directory=self.root/'.ai-project/tasks/pay/controlled-writes';directory.mkdir();tx=directory/'change'
  plan={'root':str(self.root),'candidate':str(self.iso),'reviewedBy':'synthetic','reviewNote':'synthetic review','files':[{'path':n,'beforeSha256':sha((self.root/n).read_bytes()),'afterSha256':sha((self.iso/n).read_bytes()),'mode':420} for n in self.names]}
  preview=controlled.controlled_write(self.root,'pay',plan,tx,task_only=True)
  with task_only_scope(self.root,'pay'):
   with project_lock(self.root,'.ai-project/tasks.lock'):
    with self.assertRaises(ValueError):controlled.controlled_write(self.root,'pay',plan,tx,True,preview['previewSha256'],True)
  result=controlled.controlled_write(self.root,'pay',plan,tx,True,preview['previewSha256'],True)
  self.assertEqual(result['status'],'applied-needs-review-and-checkpoint')
  with task_only_scope(self.root,'pay'):
   with self.assertRaises(ValueError):resume(self.root,'pay')
   checkpoint(self.root,'pay',['synthetic diff reviewed'],[],[],'continue')
   self.assertEqual(resume(self.root,'pay')['status'],'active')
  self.assertFalse((self.root/'.ai-project/read-policy.json').exists());self.assertFalse((self.root/'.ai-project/identity.json').exists())
 def test_symlink_candidate_and_report_path_never_probe_private_target(self):
  self.start();directory=self.root/'.ai-project/tasks/pay/controlled-writes';directory.mkdir();tx=directory/'change'
  alias=self.base/'isolation-alias';alias.symlink_to(self.root/'.test',target_is_directory=True)
  plan={'root':str(self.root),'candidate':str(alias),'reviewedBy':'synthetic','reviewNote':'synthetic review','files':[{'path':self.names[0],'beforeSha256':sha((self.root/self.names[0]).read_bytes()),'afterSha256':sha((self.iso/self.names[0]).read_bytes()),'mode':420}]}
  original=os.lstat
  def guarded(path,*a,**k):
   if str(path).startswith(str(self.root/'.test')):raise AssertionError('private target stat')
   return original(path,*a,**k)
  with mock.patch('os.lstat',side_effect=guarded):
   with self.assertRaises(ValueError):controlled.controlled_write(self.root,'pay',plan,tx,task_only=True)
   with task_only_scope(self.root,'pay'):
    with self.assertRaises(BoundaryError):run_verification(self.root,'pay','D2','compiled',[sys.executable,'-c','pass'],self.root/'.test/private.ini')

 def test_closed_task_scope_is_not_reusable(self):
  self.start()
  with task_only_scope(self.root,'pay'):
   checkpoint(self.root,'pay',[],[],[],'done','closed')
  with task_only_scope(self.root,'pay'):
   with self.assertRaises(BoundaryError):ReadBoundary(self.root)

 def test_control_spec_rejects_project_private_path_and_alias(self):
  alias=self.base/'private-alias';alias.symlink_to(self.root/'.test',target_is_directory=True)
  original=os.lstat
  def guarded(path,*a,**k):
   if str(path).startswith(str(self.root/'.test')):raise AssertionError('private target stat')
   return original(path,*a,**k)
  with mock.patch('os.lstat',side_effect=guarded):
   for path in (self.root/'.test/private.ini',alias/'private.ini'):
    with self.assertRaises(BoundaryError):external_control_bytes(path,self.root)
  hard=self.base/'hardlink.json';os.link(self.root/'.test/private.ini',hard)
  with self.assertRaises(BoundaryError):external_control_bytes(hard,self.root)

 def test_task_only_interrupted_write_restores(self):
  self.start();directory=self.root/'.ai-project/tasks/pay/controlled-writes';directory.mkdir();tx=directory/'change'
  plan={'root':str(self.root),'candidate':str(self.iso),'reviewedBy':'synthetic','reviewNote':'synthetic review','files':[{'path':n,'beforeSha256':sha((self.root/n).read_bytes()),'afterSha256':sha((self.iso/n).read_bytes()),'mode':420} for n in self.names]}
  preview=controlled.controlled_write(self.root,'pay',plan,tx,task_only=True);real=controlled.apply_files
  def interrupted(plan,tx):real(plan,tx);raise RuntimeError('synthetic interruption')
  with mock.patch.object(controlled,'apply_files',side_effect=interrupted):
   with self.assertRaises(RuntimeError):controlled.controlled_write(self.root,'pay',plan,tx,True,preview['previewSha256'],True)
  self.assertTrue(all((self.root/n).read_text()=='old' for n in self.names))
  self.assertEqual(json.loads((tx/'transaction.json').read_text())['status'],'restored')

 def test_cli_controlled_preview_apply_checkpoint(self):
  self.start();directory=self.root/'.ai-project/tasks/pay/controlled-writes';directory.mkdir();tx=directory/'change'
  plan={'root':str(self.root),'candidate':str(self.iso),'reviewedBy':'synthetic','reviewNote':'synthetic review','files':[{'path':n,'beforeSha256':sha((self.root/n).read_bytes()),'afterSha256':sha((self.iso/n).read_bytes()),'mode':420} for n in self.names]}
  planpath=self.base/'plan.json';planpath.write_text(json.dumps(plan));script=Path(controlled.__file__).resolve()
  cmd=[sys.executable,str(script),'--project',str(self.root),'--task-id','pay','--task-only','--plan',str(planpath),'--transaction',str(tx)]
  r=subprocess.run(cmd,capture_output=True,text=True);self.assertEqual(r.returncode,0,r.stdout+r.stderr);preview=json.loads(r.stdout);self.assertEqual(preview['status'],'dry-run')
  r=subprocess.run(cmd+['--apply','--preview-sha256',preview['previewSha256']],capture_output=True,text=True);self.assertEqual(r.returncode,0,r.stdout+r.stderr);self.assertEqual(json.loads(r.stdout)['status'],'applied-needs-review-and-checkpoint')
  script=Path(__file__).with_name('task_runtime.py');r=subprocess.run([sys.executable,str(script),'checkpoint','--project',str(self.root),'--task-id','pay','--task-only','--completed','synthetic reviewed'],capture_output=True,text=True);self.assertEqual(r.returncode,0,r.stdout+r.stderr)
  self.assertFalse((self.root/'.ai-project/read-policy.json').exists());self.assertFalse((self.root/'.ai-project/identity.json').exists())

 def test_cli_init_and_resume(self):
  specpath=self.base/'spec.json';specpath.write_text(json.dumps(self.spec))
  script=Path(__file__).with_name('task_runtime.py');cmd=[sys.executable,str(script),'init','--project',str(self.root),'--task-id','pay','--task-only','--task-only-spec',str(specpath),'--request','synthetic']
  for n in self.names:cmd+=['--file',n]
  r=subprocess.run(cmd,capture_output=True,text=True);self.assertEqual(r.returncode,0,r.stdout+r.stderr)
  r=subprocess.run([sys.executable,str(script),'resume','--project',str(self.root),'--task-id','pay','--task-only'],capture_output=True,text=True);self.assertEqual(r.returncode,0,r.stdout+r.stderr)

if __name__=='__main__':unittest.main(verbosity=2)

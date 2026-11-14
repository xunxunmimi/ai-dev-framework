#!/usr/bin/env python3
"""Runs Gate CLI only against synthetic temp project; no real application."""
import json,os,subprocess,sys,tempfile,unittest,hashlib,importlib.util,contextlib
from unittest import mock
from pathlib import Path
from runtime_self_test import fixture
from task_runtime import begin,task_dir,read_json,suite_digest,RUNTIME_MANAGED_MAPPINGS

class GatePreflightTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(prefix='gate-preflight-');self.base=Path(self.tmp.name).resolve();self.root=fixture(self.base/'project');self.marker=self.base/'gate-ran'
  migration=self.root/'.migration-v2';migration.mkdir();self.config=migration/'gate-config.json'
  self.config.write_text(json.dumps({'targetRoot':str(self.root),'evidenceRoot':str(self.root/'.migration-workspace-v2/evidence/gates'),'gates':[{'id':'synthetic-gate','confirmed':True,'argv':[sys.executable,'-c',f'from pathlib import Path;Path({str(self.marker)!r}).write_text("ran")'],'cwd':str(self.root)}],'profiles':{'T2':['synthetic-gate']}}))
  suite=self.base/'synthetic-suite';suite.mkdir();(suite/'VERSION').write_text('synthetic');managed=[]
  for destination,source in RUNTIME_MANAGED_MAPPINGS['ios-to-harmony-migrator-v2']:
   s=suite/source;d=self.root/destination;s.parent.mkdir(parents=True,exist_ok=True);d.parent.mkdir(parents=True,exist_ok=True);raw=('synthetic '+source).encode();s.write_bytes(raw);d.write_bytes(raw);h=hashlib.sha256(raw).hexdigest();managed.append({'path':destination,'source':source,'installedSha256':h,'sourceSha256':h})
  manifest={'schemaVersion':'1.0','suiteVersion':'synthetic','suiteRoot':str(suite),'suiteSha256':suite_digest(suite),'framework':'ios-to-harmony-migrator-v2','taskProtocolVersion':'2','managedFiles':managed};(self.root/'.ai-project/framework-installation.json').write_text(json.dumps(manifest))
  begin(self.root,'case',['one.swift'],'synthetic');self.directory=task_dir(self.root,'case');self.before=(self.directory/'state.json').read_bytes()
  self.script=Path(__file__).resolve().parents[2]/'frameworks/ios-to-harmony-migrator-v2/scripts/run_gates.py'
 def tearDown(self):self.tmp.cleanup()
 def cmd(self,report):return [sys.executable,str(self.script),str(self.config),'--profile','T2','--task-id','case','--purpose','targeted','--report',str(report)]
 def test_invalid_report_rejected_before_lease_or_gate(self):
  r=subprocess.run(self.cmd(self.base/'outside/report.json'),capture_output=True,text=True)
  self.assertNotEqual(r.returncode,0);self.assertIn('Gate 报告只能写到',r.stderr);self.assertEqual((self.directory/'state.json').read_bytes(),self.before);self.assertFalse((self.directory/'verification.lock').exists());self.assertFalse(self.marker.exists())
 def test_valid_report_executes_synthetic_gate_and_clears_lease(self):
  report=self.root/'.migration-workspace-v2/reports/result.json';r=subprocess.run(self.cmd(report),capture_output=True,text=True)
  self.assertEqual(r.returncode,0,r.stdout+r.stderr);self.assertTrue(self.marker.exists());self.assertTrue(report.is_file());self.assertEqual(json.loads(report.read_text())['status'],'passed');self.assertFalse(read_json(self.directory/'state.json').get('pendingExecutions'))
 def test_valid_nested_report_parent_is_created(self):
  report=self.root/'.migration-workspace-v2/reports/nested/new/result.json';r=subprocess.run(self.cmd(report),capture_output=True,text=True)
  self.assertEqual(r.returncode,0,r.stdout+r.stderr);self.assertTrue(report.is_file());self.assertTrue(self.marker.exists());self.assertFalse(read_json(self.directory/'state.json').get('pendingExecutions'))
 def test_existing_lease_never_released_by_bad_path(self):
  from task_runtime import start_execution,project_lock
  with project_lock(self.root,'.ai-project/tasks.lock'):
   state=read_json(self.directory/'state.json');execution=start_execution(self.root,'case',state)
  before=(self.directory/'state.json').read_bytes();r=subprocess.run(self.cmd(self.base/'outside/report.json'),capture_output=True,text=True)
  self.assertNotEqual(r.returncode,0);self.assertEqual((self.directory/'state.json').read_bytes(),before);self.assertIn(execution,read_json(self.directory/'state.json')['pendingExecutions']);self.assertFalse(self.marker.exists())
 def test_bad_evidence_root_rejected_without_lease(self):
  cfg=json.loads(self.config.read_text());cfg['evidenceRoot']=str(self.base/'outside-evidence');self.config.write_text(json.dumps(cfg))
  r=subprocess.run(self.cmd(self.root/'.migration-workspace-v2/reports/result.json'),capture_output=True,text=True)
  self.assertNotEqual(r.returncode,0);self.assertIn('Gate 日志只能写到',r.stderr);self.assertEqual((self.directory/'state.json').read_bytes(),self.before);self.assertFalse(self.marker.exists())
 def test_existing_parent_file_and_evidence_file_rejected_before_registration(self):
  workspace=self.root/'.migration-workspace-v2';workspace.mkdir();reports=workspace/'reports';reports.write_text('synthetic')
  r=subprocess.run(self.cmd(reports/'nested/result.json'),capture_output=True,text=True)
  self.assertNotEqual(r.returncode,0);self.assertEqual((self.directory/'state.json').read_bytes(),self.before);self.assertFalse(self.marker.exists());self.assertFalse((self.directory/'verification.lock').exists())
  reports.unlink();evidence=workspace/'evidence';evidence.write_text('synthetic')
  r=subprocess.run(self.cmd(reports/'result.json'),capture_output=True,text=True)
  self.assertNotEqual(r.returncode,0);self.assertEqual((self.directory/'state.json').read_bytes(),self.before);self.assertFalse(self.marker.exists());self.assertFalse((self.directory/'verification.lock').exists())
 def test_symlink_evidence_ancestor_rejected_before_registration(self):
  workspace=self.root/'.migration-workspace-v2';evidence=workspace/'evidence';evidence.mkdir(parents=True);actual=evidence/'real';actual.mkdir();(evidence/'gates').symlink_to(actual,target_is_directory=True)
  r=subprocess.run(self.cmd(workspace/'reports/result.json'),capture_output=True,text=True)
  self.assertNotEqual(r.returncode,0);self.assertEqual((self.directory/'state.json').read_bytes(),self.before);self.assertFalse(self.marker.exists());self.assertFalse((self.directory/'verification.lock').exists())
 def test_existing_directory_symlink_and_hardlink_rejected(self):
  reports=self.root/'.migration-workspace-v2/reports';reports.mkdir(parents=True);target=reports/'bad';target.mkdir()
  r=subprocess.run(self.cmd(target),capture_output=True,text=True);self.assertNotEqual(r.returncode,0);self.assertEqual((self.directory/'state.json').read_bytes(),self.before);target.rmdir()
  target.symlink_to(self.root/'one.swift');r=subprocess.run(self.cmd(target),capture_output=True,text=True);self.assertNotEqual(r.returncode,0);self.assertEqual((self.directory/'state.json').read_bytes(),self.before);target.unlink()
  os.link(self.root/'one.swift',target);r=subprocess.run(self.cmd(target),capture_output=True,text=True);self.assertNotEqual(r.returncode,0);self.assertEqual((self.directory/'state.json').read_bytes(),self.before);self.assertFalse(self.marker.exists())
 def test_path_race_after_precheck_records_precommand_completion(self):
  spec=importlib.util.spec_from_file_location('synthetic_gate_module',self.script);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
  reports=self.root/'.migration-workspace-v2/reports';reports.mkdir(parents=True);outside=self.base/'outside';outside.mkdir();alias=reports/'later';report=alias/'r.json';original=module.verification_session
  @contextlib.contextmanager
  def race(*args,**kwargs):
   alias.symlink_to(outside,target_is_directory=True)
   with original(*args,**kwargs) as value:yield value
  with mock.patch.object(module,'verification_session',race),mock.patch.object(sys,'argv',self.cmd(report)[1:]):
   with self.assertRaises(ValueError):module.main()
  state=read_json(self.directory/'state.json');self.assertFalse(state['pendingExecutions']);self.assertEqual(state['executionHistory'][-1]['outcome'],'gate-path-rejected-before-gate');self.assertEqual(state['executionHistory'][-1]['proofKind'],'framework-pre-yield-path-check');self.assertEqual(state['executionHistory'][-1]['executionRecord']['runnerPid'],os.getpid());self.assertFalse(self.marker.exists())
  from task_runtime import resume
  self.assertEqual(resume(self.root,'case')['status'],'active')
 def test_precommand_audit_failure_retains_lease(self):
  import task_runtime
  original=task_runtime.save_state
  def broken(directory,state):
   if state.get('executionHistory') and state['executionHistory'][-1].get('proofKind')=='framework-pre-yield-path-check':raise OSError('synthetic audit failure')
   return original(directory,state)
  with mock.patch.object(task_runtime,'save_state',side_effect=broken):
   with self.assertRaises(OSError):
    with task_runtime.verification_session(self.root,'case',gate_paths=(self.root/'.migration-workspace-v2/evidence',self.base/'outside/report.json')):self.fail('must not enter')
  state=read_json(self.directory/'state.json');self.assertEqual(len(state['pendingExecutions']),1);self.assertFalse(self.marker.exists())
 def test_audit_error_after_atomic_replacement_requires_reread(self):
  import task_runtime
  real_replace=task_runtime.os.replace
  def replace_then_fail(src,dst):
   real_replace(src,dst)
   if Path(dst)==self.directory/'state.json':
    state=read_json(Path(dst))
    if state.get('executionHistory') and state['executionHistory'][-1].get('proofKind')=='framework-pre-yield-path-check':raise OSError('synthetic failure after replacement')
  with mock.patch.object(task_runtime.os,'replace',side_effect=replace_then_fail):
   with self.assertRaisesRegex(OSError,'after replacement'):
    with task_runtime.verification_session(self.root,'case',gate_paths=(self.root/'.migration-workspace-v2/evidence',self.base/'outside/report.json')):self.fail('must not enter')
  state=read_json(self.directory/'state.json');self.assertFalse(state['pendingExecutions']);self.assertEqual(state['executionHistory'][-1]['proofKind'],'framework-pre-yield-path-check');self.assertFalse(self.marker.exists())
 def test_precommand_state_drift_retains_lease(self):
  import task_runtime
  def drift(*args):
   state=read_json(self.directory/'state.json');state['nextAction']='synthetic concurrent update';task_runtime.atomic_json(self.directory/'state.json',state);raise ValueError('synthetic invalid path')
  with mock.patch.object(task_runtime,'validate_gate_paths',side_effect=drift):
   with self.assertRaisesRegex(ValueError,'漂移'):
    with task_runtime.verification_session(self.root,'case',gate_paths=(self.root/'.migration-workspace-v2/evidence',self.base/'outside/report.json')):self.fail('must not enter')
  state=read_json(self.directory/'state.json');self.assertEqual(len(state['pendingExecutions']),1);self.assertEqual(state['nextAction'],'synthetic concurrent update')
 def test_other_execution_record_is_never_removed(self):
  import task_runtime
  def drift(*args):
   state=read_json(self.directory/'state.json');state['pendingExecutions']['f'*64]=dict(next(iter(state['pendingExecutions'].values())));task_runtime.atomic_json(self.directory/'state.json',state);raise ValueError('synthetic invalid path')
  with mock.patch.object(task_runtime,'validate_gate_paths',side_effect=drift):
   with self.assertRaisesRegex(ValueError,'漂移'):
    with task_runtime.verification_session(self.root,'case',gate_paths=(self.root/'.migration-workspace-v2/evidence',self.base/'outside/report.json')):self.fail('must not enter')
  state=read_json(self.directory/'state.json');self.assertEqual(len(state['pendingExecutions']),2);self.assertIn('f'*64,state['pendingExecutions'])
 def test_exception_after_yield_never_auto_releases(self):
  import task_runtime
  with self.assertRaisesRegex(ValueError,'after yield'):
   with task_runtime.verification_session(self.root,'case',gate_paths=(self.root/'.migration-workspace-v2/evidence',self.root/'.migration-workspace-v2/reports/x.json')):raise ValueError('after yield')
  self.assertEqual(len(read_json(self.directory/'state.json')['pendingExecutions']),1)

if __name__=='__main__':unittest.main(verbosity=2)

#!/usr/bin/env python3
"""Synthetic temp-only tests; no business project or device."""
import os,sys,tempfile,unittest,subprocess
from unittest import mock
from pathlib import Path
from runtime_self_test import fixture
from task_runtime import begin,run_verification,atomic_json,task_dir,read_json

class OutputTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(prefix='report-guard-');self.root=fixture(Path(self.tmp.name).resolve()/'project');begin(self.root,'case',['one.swift'],'synthetic')
  self.command=[sys.executable,'-c',"from pathlib import Path;Path('ran.marker').write_text('ran')"]
  atomic_json(self.root/'.ai-project/verification-commands.json',{'commands':[{'confirmed':True,'argv':self.command,'kind':'compiled','profiles':['D2']}]})
  self.directory=task_dir(self.root,'case');self.before=(self.directory/'state.json').read_bytes()
 def tearDown(self):self.tmp.cleanup()
 def reject(self,path):
  with self.assertRaises((ValueError,OSError)):run_verification(self.root,'case','D2','compiled',self.command,path)
  self.assertEqual((self.directory/'state.json').read_bytes(),self.before)
  self.assertEqual(read_json(self.directory/'state.json').get('pendingExecutions',{}),{})
  self.assertFalse((self.root/'ran.marker').exists())
 def test_directory_target(self):
  target=self.directory/'report-dir';target.mkdir();self.reject(target)
 def test_outside_and_reserved(self):
  for target in (self.root/'outside.json',self.directory/'state.json',self.directory/'baseline.json',self.directory/'verification-history'/'x.json'):
   with self.subTest(target=target):self.reject(target)
 def test_reserved_case_equivalents_and_file_parent(self):
  for path in (self.directory/'STATE.JSON',self.directory/'Baseline.JSON',self.directory/'Verification-History'/'report.json'):
   with self.subTest(path=path):self.reject(path)
  parent=self.directory/'parent-file';parent.write_text('synthetic');self.reject(parent/'report.json')
 def test_equivalent_node_identity_is_rejected_on_defensive_fixture(self):
  import task_runtime
  from types import SimpleNamespace
  inode=SimpleNamespace(st_dev=42,st_ino=101)
  def fake_stat(name,**kwargs):
   if name in ('alias','state.json'):return inode
   raise FileNotFoundError(name)
  with mock.patch.object(task_runtime.os,'stat',side_effect=fake_stat):
   self.assertTrue(task_runtime._reserved_node_alias(123,'alias'))
   self.assertFalse(task_runtime._reserved_node_alias(123,'other'))
 def test_cli_and_api_accept_only_confirmed_project_root_alias(self):
  marker=self.root.parent/'alias-ran';self.command=[sys.executable,'-c',f'from pathlib import Path;Path({str(marker)!r}).write_text("ran")']
  atomic_json(self.root/'.ai-project/verification-commands.json',{'commands':[{'confirmed':True,'argv':self.command,'kind':'compiled','profiles':['D2']}]})
  alias=self.root.parent/'project-alias';alias.symlink_to(self.root,target_is_directory=True)
  report=alias/'.ai-project/tasks/case/nested/cli.json'
  command=[sys.executable,str(Path(__file__).with_name('task_runtime.py')),'verify','--project',str(alias),'--task-id','case','--profile','D2','--kind','compiled','--report',str(report),'--argv',*self.command]
  result=subprocess.run(command,capture_output=True,text=True)
  self.assertEqual(result.returncode,0,result.stdout+result.stderr);self.assertTrue((self.directory/'nested/cli.json').is_file())
  self.assertTrue(marker.exists());marker.unlink();before=(self.directory/'state.json').read_bytes()
  wrong=self.root.parent/'other-project';wrong.mkdir()
  wrong_report=wrong/'.ai-project/tasks/case/report.json';command[command.index(str(report))]=str(wrong_report)
  result=subprocess.run(command,capture_output=True,text=True)
  self.assertNotEqual(result.returncode,0);self.assertEqual((self.directory/'state.json').read_bytes(),before);self.assertFalse(marker.exists())
  # 同一别名下控制名大小写等价和内部链接仍然不能成为报告目标。
  for forbidden in (alias/'.ai-project/tasks/case/STATE.JSON',alias/'.ai-project/tasks/case/report-link/x.json'):
   link=self.directory/'report-link'
   if not link.exists():link.symlink_to(self.root,target_is_directory=True)
   command[command.index(str(wrong_report))]=str(forbidden)
   result=subprocess.run(command,capture_output=True,text=True)
   self.assertNotEqual(result.returncode,0);self.assertEqual((self.directory/'state.json').read_bytes(),before);self.assertFalse(marker.exists())
   command[command.index(str(forbidden))]=str(wrong_report)
  link.unlink()
  # 函数入口同样保留用户提供的已核实根别名。
  self.command=[sys.executable,'-c','pass'];atomic_json(self.root/'.ai-project/verification-commands.json',{'commands':[{'confirmed':True,'argv':self.command,'kind':'compiled','profiles':['D2']}]})
  result=run_verification(alias,'case','D2','compiled',self.command,alias/'.ai-project/tasks/case/api.json')
  self.assertEqual(result['status'],'passed')
 def test_macos_tmp_project_alias_cli(self):
  canonical_tmp=Path('/tmp').resolve()
  if not self.root.is_relative_to(canonical_tmp):self.skipTest('fixture is outside system temporary directory')
  alias=Path('/tmp')/self.root.relative_to(canonical_tmp)
  if not alias.exists() or not os.path.samefile(alias,self.root):self.skipTest('system temporary alias unavailable')
  command=[sys.executable,'-c','pass'];atomic_json(self.root/'.ai-project/verification-commands.json',{'commands':[{'confirmed':True,'argv':command,'kind':'compiled','profiles':['D2']}]})
  report=alias/'.ai-project/tasks/case/tmp-alias.json'
  cli=[sys.executable,str(Path(__file__).with_name('task_runtime.py')),'verify','--project',str(alias),'--task-id','case','--profile','D2','--kind','compiled','--report',str(report),'--argv',*command]
  result=subprocess.run(cli,capture_output=True,text=True)
  self.assertEqual(result.returncode,0,result.stdout+result.stderr);self.assertTrue((self.directory/'tmp-alias.json').is_file())
 def test_symlinks_and_hardlinks(self):
  target=self.directory/'link.json';target.symlink_to(self.root/'one.swift');self.reject(target);target.unlink()
  os.link(self.root/'one.swift',target);self.reject(target);target.unlink()
  target.symlink_to(self.root,target_is_directory=True);self.reject(target/'report.json')
 def test_valid_path_executes_and_finishes(self):
  self.command=[sys.executable,'-c','pass'];atomic_json(self.root/'.ai-project/verification-commands.json',{'commands':[{'confirmed':True,'argv':self.command,'kind':'compiled','profiles':['D2']}]})
  report=run_verification(self.root,'case','D2','compiled',self.command,self.directory/'report.json')
  self.assertEqual(report['status'],'passed');self.assertEqual(read_json(self.directory/'state.json').get('pendingExecutions',{}),{})
 def test_valid_new_nested_parent_is_created(self):
  self.command=[sys.executable,'-c','pass'];atomic_json(self.root/'.ai-project/verification-commands.json',{'commands':[{'confirmed':True,'argv':self.command,'kind':'compiled','profiles':['D2']}]})
  target=self.directory/'new-parent'/'nested'/'report.json';report=run_verification(self.root,'case','D2','compiled',self.command,target)
  self.assertEqual(report['status'],'passed');self.assertTrue(target.is_file());self.assertEqual(read_json(self.directory/'state.json').get('pendingExecutions',{}),{})
 def test_post_execution_report_failure_retains_unproven_descendants(self):
  import task_runtime
  self.command=[sys.executable,'-c','pass'];atomic_json(self.root/'.ai-project/verification-commands.json',{'commands':[{'confirmed':True,'argv':self.command,'kind':'compiled','profiles':['D2']}]})
  original=task_runtime.atomic_json
  def broken(path,value):
   if Path(path).name=='report.json':raise OSError('synthetic output failure')
   return original(path,value)
  with mock.patch.object(task_runtime,'atomic_json',side_effect=broken):
   with self.assertRaises(OSError):run_verification(self.root,'case','D2','compiled',self.command,self.directory/'report.json')
  state=read_json(self.directory/'state.json');self.assertEqual(len(state['pendingExecutions']),1)
 def test_timeout_with_report_failure_keeps_execution_unconfirmed(self):
  import task_runtime
  self.command=[sys.executable,'-c','pass'];atomic_json(self.root/'.ai-project/verification-commands.json',{'commands':[{'confirmed':True,'argv':self.command,'kind':'compiled','profiles':['D2']}]})
  original_atomic=task_runtime.atomic_json;original_run=task_runtime.subprocess.run
  def broken(path,value):
   if Path(path).name=='report.json':raise OSError('synthetic output failure')
   return original_atomic(path,value)
  def timeout(argv,*args,**kwargs):
   if argv==self.command:raise subprocess.TimeoutExpired(argv,1)
   return original_run(argv,*args,**kwargs)
  with mock.patch.object(task_runtime,'atomic_json',side_effect=broken),mock.patch.object(task_runtime.subprocess,'run',side_effect=timeout):
   with self.assertRaises(OSError):run_verification(self.root,'case','D2','compiled',self.command,self.directory/'report.json')
  self.assertEqual(len(read_json(self.directory/'state.json')['pendingExecutions']),1)

if __name__=='__main__':unittest.main(verbosity=2)

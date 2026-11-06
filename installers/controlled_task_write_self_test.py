#!/usr/bin/env python3
"""Synthetic fixtures only: partial task, nine files, refusal, locking and rollback."""
import json, os, sys, tempfile, unittest, subprocess
from pathlib import Path
from unittest import mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'shared/task-routing'))
from read_boundary import BoundaryError
from task_runtime import atomic_json, observe_identity, begin, project_lock, task_dir
from transactional_files import sha
import controlled_task_write as controlled

class ControlledTaskScenarios(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='controlled-write-synthetic-')
        self.base=Path(self.tmp.name).resolve();self.project=self.base/'original';self.project.mkdir()
        self.isolated=self.base/'isolated';self.isolated.mkdir();(self.project/'.ai-project').mkdir()
        (self.project/'Package.swift').write_text('// synthetic identity')
        secret=self.project/'.test/unknown.json';secret.parent.mkdir();secret.write_text('SYNTHETIC PRIVATE')
        self.names=[f'Payment/file{i}.swift' for i in range(9)]
        for name in self.names:
            for root,text in ((self.project,'// original'),(self.isolated,'// changed')):
                path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text+'\n')
        allow=['Package.swift','.ai-project/**']+self.names
        self.policy={'schemaVersion':'1.0','confirmed':True,'projectRoot':str(self.project),'scopeKind':'project','mode':'partial','allow':allow,'deny':['.test/**'],'scopeDescription':'synthetic exact whitelist'}
        atomic_json(self.project/'.ai-project/read-policy.json',self.policy)
        atomic_json(self.project/'.ai-project/identity.json',observe_identity(self.project,'ios'))
        self.state=begin(self.project,'pay',self.names,'synthetic task')
        self.dir=task_dir(self.project,'pay')/'controlled-writes';self.dir.mkdir()
        self.tx=self.dir/'transaction'
        self.plan={'root':str(self.project),'candidate':str(self.isolated),'reviewedBy':'synthetic reviewer','reviewNote':'synthetic sources and preexisting changes reviewed','files':[{'path':n,'beforeSha256':sha((self.project/n).read_bytes()),'afterSha256':sha((self.isolated/n).read_bytes()),'mode':420} for n in self.names]}
    def tearDown(self):self.tmp.cleanup()
    def preview(self):return controlled.controlled_write(self.project,'pay',self.plan,self.tx)
    def test_partial_no_private_access_no_subprocess_and_apply(self):
        open0,stat0=os.open,os.stat
        def guard_open(path,*args,**kwargs):
            if str(path) in {'.test','unknown.json'}:raise AssertionError('private open')
            return open0(path,*args,**kwargs)
        def guard_stat(path,*args,**kwargs):
            if str(path) in {'.test','unknown.json'}:raise AssertionError('private stat')
            return stat0(path,*args,**kwargs)
        with mock.patch('os.open',side_effect=guard_open),mock.patch('os.stat',side_effect=guard_stat),mock.patch('task_runtime.subprocess.run',side_effect=AssertionError('Git/command invoked')):
            out=self.preview();self.assertEqual(out['coverage']['status'],'partial')
            self.assertEqual(self.state['baseRevision'],'unverified-read-scope');self.assertFalse(self.state['baselineDirtyStatusVerified'])
            self.assertEqual(len(out['files']),9)
            result=controlled.controlled_write(self.project,'pay',self.plan,self.tx,True,out['previewSha256'])
        self.assertEqual(result['status'],'applied-needs-review-and-checkpoint')
        self.assertTrue(all((self.project/n).read_text()=='// changed\n' for n in self.names))
    def test_pending_execution_blocks_preview_and_stale_apply_without_writes(self):
        """即使任务仍 active，未确认退出的验证也不能与受控源码回写并发。"""
        old = self.preview()
        state_path = task_dir(self.project, 'pay') / 'state.json'
        saved = json.loads(state_path.read_text())
        saved['pendingExecutions'] = {'synthetic-execution': {'startedAt': '2026-11-06T00:00:00+00:00'}}
        atomic_json(state_path, saved)
        before = state_path.read_bytes()
        with mock.patch.object(controlled, 'apply_files', side_effect=AssertionError('must refuse before source write')):
            with self.assertRaises(ValueError):
                self.preview()
            with self.assertRaises(ValueError):
                controlled.controlled_write(self.project, 'pay', self.plan, self.tx, True, old['previewSha256'])
        self.assertEqual(state_path.read_bytes(), before)
        self.assertFalse(self.tx.exists())
        self.assertTrue(all((self.project/name).read_text() == '// original\n' for name in self.names))

    def test_preview_stale_and_changed_checkpoint_are_rejected(self):
        old=self.preview();(self.project/self.names[0]).write_text('later change')
        with self.assertRaises(ValueError):controlled.controlled_write(self.project,'pay',self.plan,self.tx,True,old['previewSha256'])
        self.assertEqual((self.project/self.names[0]).read_text(),'later change')
    def test_task_lock_conflict_blocks_apply(self):
        old=self.preview()
        with project_lock(self.project,'.ai-project/tasks.lock'):
            with self.assertRaises((ValueError,BlockingIOError)):controlled.controlled_write(self.project,'pay',self.plan,self.tx,True,old['previewSha256'])
        self.assertFalse(self.tx.exists())
    def test_interrupted_write_rolls_back(self):
        old=self.preview();real=controlled.apply_files
        def interrupted(plan,transaction):real(plan,transaction);raise RuntimeError('synthetic interruption')
        with mock.patch.object(controlled,'apply_files',side_effect=interrupted):
            with self.assertRaises(RuntimeError):controlled.controlled_write(self.project,'pay',self.plan,self.tx,True,old['previewSha256'])
        self.assertTrue(all((self.project/n).read_text()=='// original\n' for n in self.names))
        self.assertEqual(json.loads((self.tx/'transaction.json').read_text())['status'],'restored')
    def test_link_and_hardlink_candidate_are_rejected(self):
        path=self.isolated/self.names[0];path.unlink();path.symlink_to(self.project/'.test/unknown.json')
        with self.assertRaises((ValueError,OSError)):self.preview()
        path.unlink();os.link(self.project/'.test/unknown.json',path)
        with self.assertRaises((ValueError,OSError)):self.preview()
    def test_outside_task_and_changed_policy_rejected(self):
        self.plan['files'][0]['path']='Package.swift'
        with self.assertRaises(ValueError):self.preview()
        self.plan['files'][0]['path']=self.names[0];self.policy['deny'].append('Other/**');atomic_json(self.project/'.ai-project/read-policy.json',self.policy)
        with self.assertRaises(ValueError):self.preview()
    def test_rollback_refuses_concurrent_user_change(self):
        out=self.preview();real=controlled.apply_files
        def conflicting(plan,transaction):
            real(plan,transaction);(self.project/self.names[0]).write_text('concurrent user edit')
            raise RuntimeError('synthetic interruption')
        with mock.patch.object(controlled,'apply_files',side_effect=conflicting):
            with self.assertRaisesRegex(ValueError,'恢复受阻'):controlled.controlled_write(self.project,'pay',self.plan,self.tx,True,out['previewSha256'])
        self.assertEqual((self.project/self.names[0]).read_text(),'concurrent user edit')
        self.assertEqual(json.loads((self.tx/'transaction.json').read_text())['status'],'applied')

    def test_existing_transaction_is_not_reused_or_rolled_back(self):
        out=self.preview();controlled.controlled_write(self.project,'pay',self.plan,self.tx,True,out['previewSha256'])
        record=(self.tx/'transaction.json').read_bytes()
        for name in self.names:(self.project/name).write_text('// original\n')
        fresh=self.preview()
        with self.assertRaises(ValueError):controlled.controlled_write(self.project,'pay',self.plan,self.tx,True,fresh['previewSha256'])
        self.assertEqual((self.tx/'transaction.json').read_bytes(),record)
        self.assertTrue(all((self.project/n).read_text()=='// original\n' for n in self.names))
    def test_forged_review_and_wrong_token_are_rejected(self):
        self.plan['reviewedBy']=''
        with self.assertRaises(ValueError):self.preview()
        self.plan['reviewedBy']='synthetic reviewer'
        with self.assertRaises(ValueError):controlled.controlled_write(self.project,'pay',self.plan,self.tx,True,'0'*64)
        self.assertFalse(self.tx.exists())

    def test_cli_preview_has_no_writes(self):
        plan_path=self.base/'plan.json';plan_path.write_text(json.dumps(self.plan))
        command=[sys.executable,str(Path(controlled.__file__).resolve()),'--project',str(self.project),'--task-id','pay','--plan',str(plan_path),'--transaction',str(self.tx)]
        result=subprocess.run(command,capture_output=True,text=True,check=True)
        report=json.loads(result.stdout)
        self.assertEqual(report['status'],'dry-run');self.assertFalse(self.tx.exists())
        self.assertFalse((self.dir/'transaction.audit.json').exists())

    def test_unsafe_backup_directory_rejected(self):
        out=self.preview();self.dir.rmdir();self.dir.symlink_to(self.base,target_is_directory=True)
        with self.assertRaises((ValueError,OSError)):controlled.controlled_write(self.project,'pay',self.plan,self.tx,True,out['previewSha256'])

if __name__=='__main__':unittest.main(verbosity=2)

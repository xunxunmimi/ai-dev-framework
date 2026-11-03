#!/usr/bin/env python3
"""Synthetic projects only: opt-in, exact scopes, existing-design priority and isolation."""
from pathlib import Path
import hashlib,json,os,subprocess,sys,tempfile,unittest
SUITE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(SUITE/'capabilities/unslop-ui/scripts'))
from check_ui_scope import decide
from enable_unslop_ui import enable
from pin_release import pin,verify

class OptionalUiScenarios(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='optional-ui-synthetic-')
        self.base=Path(self.tmp.name).resolve();self.project=self.make_project('selected');self.other=self.make_project('other')
        self.file='Sources/Home.swift'
    def tearDown(self):self.tmp.cleanup()
    def make_project(self,name):
        project=self.base/name;project.mkdir();(project/'.ai-project').mkdir();(project/'Package.swift').write_text('// synthetic identity\n')
        (project/'Sources').mkdir();(project/'Sources/Home.swift').write_text('// existing synthetic UI; never edited by installation\n')
        policy={'schemaVersion':'1.0','confirmed':True,'projectRoot':str(project),'mode':'complete','allow':['**'],'deny':[],'scopeDescription':'Synthetic optional capability test only'}
        (project/'.ai-project/read-policy.json').write_text(json.dumps(policy));return project
    def inventory(self,project):
        return {p.relative_to(project).as_posix():p.read_bytes() for p in project.rglob('*') if p.is_file()}
    def selection(self,project=None):
        return json.loads(((project or self.project)/'.agents/skills/unslop-ui/selection.json').read_text())
    def install(self,intent='new-ui',design='none'):
        return enable(self.project,intent,design,[self.file],True)
    def gate(self,project=None,intent='new-ui',design='none',file=None):
        command=[sys.executable,'-B',str(self.project/'.agents/skills/unslop-ui/scripts/check_ui_scope.py'),'--project',str(project or self.project),'--intent',intent,'--design-status',design,'--file',file or self.file]
        process=subprocess.run(command,text=True,capture_output=True)
        return process.returncode,json.loads(process.stdout)
    def test_unselected_and_source_copy_are_off(self):
        self.assertFalse(decide(None,str(self.project),'new-ui','none',[self.file])['eligible'])
        script=SUITE/'capabilities/unslop-ui/scripts/check_ui_scope.py'
        process=subprocess.run([sys.executable,'-B',str(script),'--project',str(self.project),'--intent','new-ui','--design-status','none','--file',self.file],text=True,capture_output=True)
        self.assertEqual(process.returncode,2);self.assertEqual(json.loads(process.stdout)['reason'],'project-not-selected')
        self.assertFalse((self.project/'.agents').exists())
    def test_preview_has_no_writes(self):
        before=self.inventory(self.project)
        plan=enable(self.project,'new-ui','none',[self.file])
        self.assertEqual(plan['status'],'dry-run');self.assertEqual(plan['writes'],[])
        self.assertEqual(self.inventory(self.project),before);self.assertFalse((self.project/'.agents').exists())
    def test_explicit_install_is_local_and_preserves_existing_metadata_and_ui(self):
        before=self.inventory(self.project);other=self.inventory(self.other)
        out=self.install();self.assertEqual(out['status'],'enabled')
        code,result=self.gate();self.assertEqual(code,0);self.assertEqual(result['mode'],'new-ui-direction')
        for name,content in before.items():self.assertEqual((self.project/name).read_bytes(),content)
        self.assertEqual(self.inventory(self.other),other)
        for name in ['LICENSE.md','NOTICE.md','references/upstream-citation.md']:
            self.assertTrue((self.project/'.agents/skills/unslop-ui'/name).is_file())
        self.assertFalse((self.project/'.ai-project/framework-installation.json').exists())
    def test_another_project_cannot_reuse_selection(self):
        self.install();before=self.inventory(self.other)
        code,result=self.gate(project=self.other)
        self.assertEqual(code,2);self.assertFalse(result['eligible']);self.assertEqual(self.inventory(self.other),before)
        selection=self.selection()
        self.assertFalse(decide(selection,str(self.other),'new-ui','none',[self.file])['eligible'])
    def test_ordinary_task_unknown_design_and_other_files_are_off(self):
        self.install()
        for intent,design,file in [('none','none',self.file),('new-ui','unknown',self.file),('optimize-ui','none',self.file),('new-ui','none','Sources/Other.swift')]:
            code,out=self.gate(intent=intent,design=design,file=file);self.assertEqual(code,2);self.assertFalse(out['eligible'])
    def test_new_ui_rejects_existing_design_without_writing(self):
        before=self.inventory(self.project)
        with self.assertRaisesRegex(ValueError,'existing-design-takes-priority'):
            enable(self.project,'new-ui','existing',[self.file],True)
        self.assertEqual(self.inventory(self.project),before)
    def test_new_design_evidence_overrides_old_no_design_selection(self):
        self.install();code,out=self.gate(design='existing')
        self.assertEqual(code,2);self.assertEqual(out['reason'],'existing-design-takes-priority');self.assertFalse(out['mayRedesign'])
    def test_existing_design_optimization_never_authorizes_redesign(self):
        self.install('optimize-ui','existing')
        for current in ['none','existing']:
            code,out=self.gate(intent='optimize-ui',design=current)
            self.assertEqual(code,0);self.assertEqual(out['mode'],'design-fidelity-only');self.assertFalse(out['mayRedesign'])
    def test_explicit_polish_without_design_has_limited_mode(self):
        self.install('optimize-ui','none');code,out=self.gate(intent='optimize-ui')
        self.assertEqual(code,0);self.assertEqual(out['mode'],'audit-and-polish');self.assertFalse(out['mayRedesign'])
    def test_disabled_or_missing_selection_stays_off(self):
        self.install();path=self.project/'.agents/skills/unslop-ui/selection.json';data=self.selection();data['enabled']=False;path.write_text(json.dumps(data))
        self.assertEqual(self.gate()[0],2);path.unlink();self.assertEqual(self.gate()[0],2)
    def test_existing_skill_and_local_edits_are_not_overwritten(self):
        self.install();path=self.project/'.agents/skills/unslop-ui/SKILL.md';path.write_text('synthetic user edit')
        with self.assertRaises(ValueError):self.install()
        self.assertEqual(path.read_text(),'synthetic user edit')
    def test_unsafe_scopes_are_rejected(self):
        for file in ['../Other.swift','/tmp/Other.swift','Sources/**','.env','.agents/skills/other/SKILL.md','Pods/Library.swift']:
            with self.assertRaises(ValueError):enable(self.project,'new-ui','none',[file],True)
        self.assertFalse((self.project/'.agents').exists())
    def test_missing_or_narrow_policy_is_not_expanded(self):
        path=self.project/'.ai-project/read-policy.json';path.unlink()
        with self.assertRaises(ValueError):self.install()
        self.assertFalse(path.exists())
        data={'schemaVersion':'1.0','confirmed':True,'projectRoot':str(self.project),'mode':'partial','allow':['Package.swift'],'deny':[],'scopeDescription':'synthetic narrow policy'}
        path.write_text(json.dumps(data));before=path.read_bytes()
        with self.assertRaises(ValueError):self.install()
        self.assertEqual(path.read_bytes(),before);self.assertFalse((self.project/'.agents').exists())
    def test_symlink_target_and_hardlink_selection_are_rejected(self):
        (self.project/'.agents').symlink_to(self.other,target_is_directory=True)
        with self.assertRaises((ValueError,OSError)):self.install()
        (self.project/'.agents').unlink();self.install()
        selection=self.project/'.agents/skills/unslop-ui/selection.json';os.link(selection,self.base/'linked-selection.json')
        self.assertEqual(self.gate()[0],2)
    def test_default_framework_install_does_not_select_optional_ui(self):
        process=subprocess.run(['bash',str(SUITE/'installers/setup-project.sh'),'ios-daily',str(self.project)],text=True,capture_output=True)
        self.assertEqual(process.returncode,0,process.stdout+process.stderr)
        manifest=(self.project/'.ai-project/framework-installation.json').read_bytes()
        self.assertFalse((self.project/'.agents/skills/unslop-ui').exists())
        self.install();self.assertEqual((self.project/'.ai-project/framework-installation.json').read_bytes(),manifest)
    def test_fixed_snapshot_retains_legal_notices(self):
        created=pin(SUITE,self.base/'pins',True);root=Path(created['root']);record=verify(root)
        for name in ['LICENSE.md','NOTICE.md','references/upstream-citation.md','references/upstream-signs.md','references/upstream-tokens.md']:
            relative='capabilities/unslop-ui/'+name;self.assertIn(relative,record['files'])
            self.assertEqual((root/relative).read_bytes(),(SUITE/relative).read_bytes())
    def test_command_line_defaults_to_preview(self):
        command=[sys.executable,'-B',str(SUITE/'installers/enable_unslop_ui.py'),'--project',str(self.project),'--intent','new-ui','--design-status','none','--file',self.file]
        process=subprocess.run(command,text=True,capture_output=True)
        self.assertEqual(process.returncode,0,process.stderr);self.assertEqual(json.loads(process.stdout)['status'],'dry-run')
        self.assertFalse((self.project/'.agents').exists())

if __name__=='__main__':unittest.main(verbosity=2)

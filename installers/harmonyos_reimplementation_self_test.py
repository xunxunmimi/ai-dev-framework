#!/usr/bin/env python3
"""隔离合成项目：只验证可选安装，不冒充业务重实现验收。"""
import json
from pathlib import Path
import tempfile
import unittest
from enable_harmonyos_reimplementation import enable, TARGET, CAPABILITY
from pin_release import pin, verify

class ReimplementationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.base=Path(self.tmp.name).resolve()
        self.source=self.project('source');self.target=self.project('target')
    def tearDown(self):
        self.tmp.cleanup()
    def project(self,name):
        root=self.base/name;root.mkdir();(root/'.ai-project').mkdir()
        (root/'main.ets').write_text('// synthetic untouched business')
        (root/'.ai-project/read-policy.json').write_text(json.dumps({'schemaVersion':'1.0','confirmed':True,'projectRoot':str(root),'mode':'complete','allow':['**'],'deny':[],'scopeDescription':'synthetic fixture only'}))
        return root
    def inventory(self,root):
        return {str(p.relative_to(root)):p.read_bytes() for p in root.rglob('*') if p.is_file()}
    def test_preview_has_no_side_effects(self):
        before=(self.inventory(self.source),self.inventory(self.target))
        self.assertEqual(enable(self.target,self.source)['status'],'dry-run')
        self.assertEqual(before,(self.inventory(self.source),self.inventory(self.target)))
    def test_install_only_target_and_exact_payload(self):
        source=self.inventory(self.source);target=self.inventory(self.target)
        self.assertEqual(enable(self.target,self.source,True)['status'],'enabled')
        self.assertEqual(source,self.inventory(self.source))
        for name,value in target.items():self.assertEqual((self.target/name).read_bytes(),value)
        for p in CAPABILITY.rglob('*'):
            if p.is_file():self.assertEqual(p.read_bytes(),(self.target/TARGET/p.relative_to(CAPABILITY)).read_bytes())
        selection=json.loads((self.target/TARGET/'selection.json').read_text())
        self.assertEqual(selection['projectRoot'],str(self.target));self.assertEqual(selection['sourceRoot'],str(self.source))
        self.assertTrue(selection['sourceReadOnly']);self.assertFalse((self.target/'AGENTS.md').exists())
    def test_existing_edits_not_overwritten(self):
        enable(self.target,self.source,True)
        p=self.target/TARGET/'SKILL.md';p.write_text('user edits')
        with self.assertRaises(ValueError):enable(self.target,self.source,True)
        self.assertEqual(p.read_text(),'user edits')
    def test_missing_policy_not_bootstrapped(self):
        for root in (self.source,self.target):
            p=root/'.ai-project/read-policy.json';saved=p.read_bytes();p.unlink()
            with self.assertRaises(ValueError):enable(self.target,self.source,True)
            self.assertFalse(p.exists());p.write_bytes(saved)
        self.assertFalse((self.target/TARGET).exists())
    def test_narrow_target_policy_not_expanded(self):
        p=self.target/'.ai-project/read-policy.json';data=json.loads(p.read_text());data.update(mode='partial',allow=['main.ets']);p.write_text(json.dumps(data));before=self.inventory(self.target)
        with self.assertRaises(ValueError):enable(self.target,self.source,True)
        self.assertEqual(before,self.inventory(self.target))
    def test_same_and_nested_roots_blocked(self):
        with self.assertRaises(ValueError):enable(self.source,self.source,True)
        nested=self.source/'nested';nested.mkdir()
        with self.assertRaises(ValueError):enable(nested,self.source,True)
        with self.assertRaises(ValueError):enable(self.source,nested,True)
    def test_symlink_parent_blocked(self):
        (self.target/'.agents').symlink_to(self.source,target_is_directory=True)
        before=self.inventory(self.source)
        with self.assertRaises(ValueError):enable(self.target,self.source,True)
        self.assertEqual(before,self.inventory(self.source))
    def test_global_and_missing_roots_blocked(self):
        for root in (Path('/'),Path.home(),self.base/'absent'):
            with self.assertRaises(ValueError):enable(root,self.source,True)
    def test_wrong_root_policy_and_symlink_source_blocked(self):
        p=self.source/'.ai-project/read-policy.json';data=json.loads(p.read_text());data['projectRoot']=str(self.target);p.write_text(json.dumps(data))
        with self.assertRaises(ValueError):enable(self.target,self.source,True)
        alias=self.base/'alias';alias.symlink_to(self.source,target_is_directory=True)
        with self.assertRaises(ValueError):enable(self.target,alias,True)
        self.assertFalse((self.target/TARGET).exists())
    def test_cli_defaults_to_preview(self):
        import subprocess,sys
        before=self.inventory(self.target)
        result=subprocess.run([sys.executable,'-B',str(Path(__file__).with_name('enable_harmonyos_reimplementation.py')),'--project',str(self.target),'--source',str(self.source)],capture_output=True,text=True)
        self.assertEqual(result.returncode,0);self.assertEqual(json.loads(result.stdout)['status'],'dry-run')
        self.assertEqual(before,self.inventory(self.target))
    def test_lock_links_do_not_modify_source(self):
        import os
        lock_path=self.target/'.ai-project/reimplementation-install.lock'
        absent=self.source/'not-created'
        lock_path.symlink_to(absent)
        with self.assertRaises(ValueError):enable(self.target,self.source,True)
        self.assertFalse(absent.exists());lock_path.unlink()
        original=self.source/'main.ets';before=original.read_bytes()
        os.link(original,lock_path)
        with self.assertRaises(ValueError):enable(self.target,self.source,True)
        self.assertEqual(before,original.read_bytes());self.assertFalse((self.target/TARGET).exists())
    def test_capability_is_pinnable(self):
        from enable_harmonyos_reimplementation import SUITE
        result=pin(SUITE,self.base/'pins',True);record=verify(Path(result['root']))
        self.assertIn('capabilities/harmonyos-app-reimplementation/SKILL.md',record['files'])

if __name__=='__main__':unittest.main()

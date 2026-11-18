import hashlib,json,tempfile,unittest,os
from pathlib import Path
from unittest import mock
import staged_skill_publish as module
class PublishTest(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name).resolve();(self.root/'.ai-project').mkdir()
  (self.root/'.ai-project/read-policy.json').write_text(json.dumps({'schemaVersion':'1.0','confirmed':True,'projectRoot':str(self.root),'mode':'complete','allow':['**'],'deny':[],'scopeDescription':'synthetic'}))
  self.parent=self.root/module.PARENT;self.parent.mkdir(parents=True);self.name='.restyle-stage-'+'a'*32;self.stage=self.parent/self.name;self.stage.mkdir();(self.stage/'SKILL.md').write_bytes(b'skill');self.hashes={'SKILL.md':hashlib.sha256(b'skill').hexdigest()}
 def tearDown(self):self.tmp.cleanup()
 def call(self):return module.publish(self.root,self.name,self.hashes)
 def test_publish(self):
  self.assertEqual(self.call()['status'],'published');self.assertFalse(self.stage.exists());self.assertEqual((self.parent/module.DEST/'SKILL.md').read_bytes(),b'skill')
 def test_no_clobber_even_race_empty_directory(self):
  real=module._rename_exclusive
  def race(fd,name):(self.parent/module.DEST).mkdir();real(fd,name)
  with mock.patch.object(module,'_rename_exclusive',race),self.assertRaises(FileExistsError):self.call()
  self.assertTrue(self.stage.exists())
 def test_changed_bytes(self):
  (self.stage/'SKILL.md').write_bytes(b'changed')
  with self.assertRaises(ValueError):self.call()
 def test_hardlink_and_unknown_material(self):
  os.link(self.stage/'SKILL.md',self.stage/'other')
  with self.assertRaises(ValueError):self.call()
 def test_parent_symlink(self):
  other=self.root/'other';self.parent.rename(other);self.parent.symlink_to(other,target_is_directory=True)
  with self.assertRaises(ValueError):self.call()
 def test_unknown_empty_directory(self):
  (self.stage/'unknown').mkdir()
  with self.assertRaises(ValueError):self.call()
 def test_policy_denial(self):
  p=self.root/'.ai-project/read-policy.json';d=json.loads(p.read_text());d.update(mode='partial',deny=[module.PARENT+'/'+self.name+'/**']);p.write_text(json.dumps(d))
  with self.assertRaises(ValueError):self.call()
if __name__=='__main__':unittest.main(verbosity=2)

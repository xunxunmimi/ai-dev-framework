import json
from pathlib import Path
import tempfile
import unittest
from governance_journal import apply_exact,rollback_exact,LOCK,JOURNAL
class JournalTest(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name).resolve()
  for d in ['.ai-project','control',JOURNAL]:(self.root/d).mkdir()
  (self.root/LOCK).touch();(self.root/'.ai-project/read-policy.json').write_text(json.dumps({'schemaVersion':'1.0','confirmed':True,'projectRoot':str(self.root),'mode':'complete','allow':['**'],'deny':[],'scopeDescription':'synthetic'}))
  self.changes={'.ai-project/framework-installation.json':(b'a',b'A'),'.ai-project/lifecycle.json':(b'b',b'B')}
  for p,(before,_) in self.changes.items():(self.root/p).write_bytes(before)
 def tearDown(self):self.tmp.cleanup()
 def test_interrupted_apply_and_rollback(self):
  receipts=[]
  def stop(point,r):
   receipts.append(r)
   if point=='.ai-project/framework-installation.json':raise RuntimeError('crash')
  with self.assertRaises(RuntimeError):apply_exact(self.root,JOURNAL,self.changes,'synthetic stop',stop)
  self.assertEqual((self.root/'.ai-project/framework-installation.json').read_bytes(),b'A');self.assertEqual((self.root/'.ai-project/lifecycle.json').read_bytes(),b'b')
  rollback_exact(self.root,JOURNAL,receipts[0],'synthetic stop')
  self.assertEqual((self.root/'.ai-project/framework-installation.json').read_bytes(),b'a')
 def test_changed_postimage_blocks_all_restores(self):
  r=apply_exact(self.root,JOURNAL,self.changes,'synthetic stop');(self.root/'.ai-project/lifecycle.json').write_bytes(b'user')
  with self.assertRaises(ValueError):rollback_exact(self.root,JOURNAL,r,'synthetic stop')
  self.assertEqual((self.root/'.ai-project/framework-installation.json').read_bytes(),b'A')
 def test_unknown_material(self):
  (self.root/JOURNAL/'unknown').touch()
  with self.assertRaises(ValueError):apply_exact(self.root,JOURNAL,self.changes,'synthetic stop')
 def test_interrupted_rollback_is_replayable(self):
  r=apply_exact(self.root,JOURNAL,self.changes,'synthetic stop')
  def stop(*_):raise RuntimeError('crash')
  with self.assertRaises(RuntimeError):rollback_exact(self.root,JOURNAL,r,'synthetic stop',stop)
  rollback_exact(self.root,JOURNAL,r,'synthetic stop')
  self.assertEqual((self.root/'.ai-project/lifecycle.json').read_bytes(),b'b')
 def test_links_rejected(self):
  (self.root/'.ai-project/framework-installation.json').unlink();(self.root/'.ai-project/framework-installation.json').symlink_to(self.root/'.ai-project/lifecycle.json')
  with self.assertRaises(ValueError):apply_exact(self.root,JOURNAL,self.changes,'synthetic stop')
 def test_arbitrary_path_and_journal_rejected(self):
  with self.assertRaises(ValueError):apply_exact(self.root,JOURNAL,{'control/arbitrary':(b'a',b'b')},'synthetic')
  with self.assertRaises(ValueError):apply_exact(self.root,'journal',self.changes,'synthetic')
 def test_lock_replacement_blocks_targets(self):
  def replace_lock(point,r):
   if point=='journal-durable':
    (self.root/LOCK).unlink();(self.root/LOCK).touch()
  with self.assertRaises(ValueError):apply_exact(self.root,JOURNAL,self.changes,'synthetic',replace_lock)
  self.assertEqual((self.root/'.ai-project/framework-installation.json').read_bytes(),b'a')
 def test_journal_no_clobber(self):
  r=apply_exact(self.root,JOURNAL,self.changes,'synthetic')
  old=(self.root/JOURNAL/'journal.json').read_bytes()
  with self.assertRaises(ValueError):apply_exact(self.root,JOURNAL,{n:(v[1],v[0]) for n,v in self.changes.items()},'synthetic')
  self.assertEqual(old,(self.root/JOURNAL/'journal.json').read_bytes())
 def test_bad_schema_and_oversize_rejected(self):
  import hashlib
  apply_exact(self.root,JOURNAL,self.changes,'synthetic')
  p=self.root/JOURNAL/'journal.json';d=json.loads(p.read_text());d['items']['arbitrary']={'before':'','after':''}
  raw=json.dumps(d).encode();p.write_bytes(raw)
  with self.assertRaises(ValueError):rollback_exact(self.root,JOURNAL,hashlib.sha256(raw).hexdigest(),'synthetic')
  with self.assertRaises(ValueError):apply_exact(self.root,JOURNAL,{'.ai-project/lifecycle.json':(b'B',b'x'*65537)},'synthetic')
if __name__=='__main__':unittest.main(verbosity=2)

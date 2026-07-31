from __future__ import annotations
import importlib.util,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; SPEC=importlib.util.spec_from_file_location("pc_bind",ROOT/"pipelines"/"pc_saved_identity_bind.py"); assert SPEC and SPEC.loader; m=importlib.util.module_from_spec(SPEC);sys.modules[SPEC.name]=m;SPEC.loader.exec_module(m)
class PcSavedIdentityBindTests(unittest.TestCase):
 def test_collector_accepts_fraction_base_but_not_other_number(self):
  self.assertTrue(m.collector_match("205/165","Mew ex #205 Prices"));self.assertFalse(m.collector_match("205/165","Mew ex #206 Prices"))
 def test_title_policy_rejects_japanese_db_for_english_pc_title(self):
  self.assertIn("japanese",m.norm("Pokemon Japanese Card 151"));self.assertNotIn("japanese",m.norm("Pokemon Scarlet & Violet 151"))
 def test_plan_hash_is_content_addressed(self):
  doc={"contract":m.CONTRACT,"readOnly":True,"approved":[],"quarantined":[],"outcomes":{}};doc["planSha256"]=m.sha256({k:v for k,v in doc.items() if k!="planSha256"});self.assertEqual(doc["planSha256"],m.sha256({k:v for k,v in doc.items() if k!="planSha256"}))
if __name__=="__main__":unittest.main()

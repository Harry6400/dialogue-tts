import json
from pathlib import Path
import unittest
from .normalize import normalize,InputError

FIXTURES=Path(__file__).resolve().parent.parent/'examples'

def payload():
 return {'format':'xml_pair','xml_time_unit':'ms','xml_files':[{'name':n,'content':(FIXTURES/n).read_text(encoding="utf-8")} for n in ('agent_demo.xml','user_demo.xml')]}

class XMLPairTests(unittest.TestCase):
 def test_interleave_and_mute(self):
  r=normalize(payload())
  self.assertEqual([x['start_ms'] for x in r['rows']],[200,3000,5200,8000,9000])
  self.assertEqual([x['speaker'] for x in r['rows']],['USER','AGENT','USER','USER','AGENT'])
  self.assertEqual(r['rows'][3]['text'],'已静音片段');self.assertTrue(r['rows'][3]['muted'])
  self.assertEqual(len(r['script']['lines']),4)
  self.assertEqual(r['muted_count'],1)
  self.assertEqual(len(r['info_notes']),2)
  self.assertTrue(all(note in r['warnings'] for note in r['info_notes']))
  self.assertTrue(any(x['overlap'] for x in r['rows']))
  self.assertNotIn('fictional-demo-001',json.dumps(r))
 def test_dtd_and_entity_rejected(self):
  for marker in ('<!DOCTYPE analyze [<!ENTITY x SYSTEM "file:///etc/passwd">]>', '<!DOCTYPE analyze SYSTEM "https://evil.example/data">'):
   p=payload();p['xml_files'][0]['content']=p['xml_files'][0]['content'].replace('?>', '?>'+marker, 1)
   with self.assertRaises(InputError):normalize(p)
 def test_missing_time_rejected(self):
  p=payload();p['xml_files'][0]['content']=p['xml_files'][0]['content'].replace('<time>','<missing>').replace('</time>','</missing>')
  with self.assertRaises(InputError):normalize(p)
 def test_call_mismatch_rejected_without_echo(self):
  p=payload();p['xml_files'][0]['content']=p['xml_files'][0]['content'].replace('fictional-demo-001','private-secret-id')
  with self.assertRaises(InputError) as cm:normalize(p)
  self.assertNotIn('private-secret-id',str(cm.exception))
 def test_missing_call_id_rejected_even_single_pair(self):
  for tag in ('callReference', 'callreference'):
   p=payload();p['xml_files'][0]['content']=p['xml_files'][0]['content'].replace('<callReference>fictional-demo-001</callReference>', '<'+tag+'></'+tag+'>')
   with self.assertRaises(InputError):normalize(p)
 def test_metadata_disagreement_warns(self):
  p=payload();p['xml_files'][0]['content']=p['xml_files'][0]['content'].replace('<callInfo>', '<callInfo><startTime>one</startTime>');p['xml_files'][1]['content']=p['xml_files'][1]['content'].replace('<callInfo>', '<callInfo><startTime>two</startTime>')
  r=normalize(p)
  self.assertTrue(any('开始时间' in x for x in r['warnings']))
  self.assertFalse(any('开始时间' in x for x in r['info_notes']))
 def test_muted_text_not_exported(self):
  p=payload();p['xml_files'][1]['content']=p['xml_files'][1]['content'].replace('这是一句虚构的屏蔽段落。','secret-never-speak')
  r=normalize(p);self.assertNotIn('secret-never-speak',json.dumps(r))
 def test_invalid_mute_fails_closed(self):
  p=payload();p['xml_files'][1]['content']=p['xml_files'][1]['content'].replace('start="8000"','start="invalid"')
  with self.assertRaises(InputError):normalize(p)
 def test_partial_muted_overlap_is_protected(self):
  p=payload();p['xml_files'][1]['content']=p['xml_files'][1]['content'].replace('start="8000" end="8500"','start="8100" end="8200"')
  r=normalize(p);self.assertTrue(r['rows'][3]['muted']);self.assertEqual(len(r['script']['lines']),4)
 def test_same_role_rejected(self):
  p=payload();p['xml_files'][1]=dict(p['xml_files'][0])
  with self.assertRaises(InputError):normalize(p)

if __name__=='__main__':unittest.main()

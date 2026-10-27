"""Synthetic synthesis identity regression tests; never calls Edge."""
import copy
import unittest
from .normalize import normalize
from .test_batch_import import xml, f
from .batch_import import preview_batch


class FingerprintTests(unittest.TestCase):
    def payload(self):
        return {'format':'xml_pair', 'xml_files':[f(xml('AGENT')), f(xml('USER').replace('0,1000','1000,2000'))]}

    def fingerprint(self, payload):
        value = normalize(payload)['fingerprint']
        self.assertRegex(value, r'^[0-9a-f]{64}$')
        return value

    def test_sound_or_reference_change_changes_identity(self):
        original = self.payload(); baseline = self.fingerprint(original)
        for key, value in (('rate',1.1), ('pause_ms',300), ('voices',{'USER':'zh-CN-YunxiNeural'})):
            changed = copy.deepcopy(original); changed[key] = value
            self.assertNotEqual(baseline, self.fingerprint(changed))
        for old, new in (('你好','不同文本'), ('fiction-call-1','0009007199254740993')):
            changed=copy.deepcopy(original)
            for item in changed['xml_files']:item['content']=item['content'].replace(old,new)
            self.assertNotEqual(baseline,self.fingerprint(changed))

    def test_filename_metadata_and_muted_text_not_hashed(self):
        original=self.payload(); original['xml_files'][0]['content']=xml('AGENT', text='hidden', mute=True)
        changed=copy.deepcopy(original)
        for item in changed['xml_files']:
            item['name']='another.xml'
            item['content']=item['content'].replace('2026-01-01T00:00:00','different').replace('<duration>4','<duration>9').replace('hidden','different muted text')
        self.assertEqual(self.fingerprint(original), self.fingerprint(changed))
        # Reverse input files without changing actual spoken order (distinct timestamps).
        p=self.payload()
        for index,item in enumerate(p['xml_files']):
            if index == 1:item['content']=item['content'].replace('2000,3000','3000,4000')
        swapped=copy.deepcopy(p);swapped['xml_files'].reverse()
        self.assertEqual(self.fingerprint(p),self.fingerprint(swapped))

    def test_batch_matches_single_and_ignores_client_hash(self):
        p=self.payload();p['fingerprint']='0'*64
        batch=preview_batch({'files':p['xml_files'], 'fingerprint':'0'*64})
        self.assertEqual(batch['groups'][0]['fingerprint'],self.fingerprint(p))
        self.assertNotEqual(self.fingerprint(p),'0'*64)

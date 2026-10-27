import unittest
from .batch_import import preview_batch
from .test_batch_import import xml, f
from .normalize import InputError, normalize

class AutoVoiceTests(unittest.TestCase):
    def payload(self):
        return {'files':[f(xml('AGENT','one',text='先生，您好。')),f(xml('USER','one')),f(xml('AGENT','two',text='女士，您好。')),f(xml('USER','two'))], 'auto_voice':True}
    def test_each_call_resolves_before_fingerprint(self):
        payload=self.payload(); r=preview_batch(payload); one,two=r['groups']
        self.assertEqual(one['preview']['voice_choice']['user'],'zh-CN-YunxiNeural')
        self.assertEqual(two['preview']['voice_choice']['user'],'zh-CN-XiaoyiNeural')
        self.assertTrue(all(x['voice']=='zh-CN-YunxiNeural' for x in one['preview']['script']['lines'] if x['speaker']=='USER'))
        payload['voice_overrides']={one['id']:'zh-CN-XiaoyiNeural'}
        override=preview_batch(payload)['groups'];self.assertNotEqual(one['fingerprint'],override[0]['fingerprint']);self.assertEqual(two['fingerprint'],override[1]['fingerprint'])
        self.assertEqual(override[0]['preview']['voice_choice']['reason'],'手动选择')
    def test_manual_global_and_ambiguous_preserve(self):
        payload=self.payload();payload['manual_user_voice']=True;payload['voices']={'USER':'zh-CN-YunyangNeural'}
        self.assertTrue(all(g['preview']['voice_choice']['user']=='zh-CN-YunyangNeural' for g in preview_batch(payload)['groups']))
        payload=self.payload();payload['files'][0]=f(xml('AGENT','one',text='先生，您好。女士，您好。'))
        self.assertEqual(preview_batch(payload)['groups'][0]['preview']['voice_choice']['reason'],'称呼未判定')

class SingleAutoVoiceTests(unittest.TestCase):
    def test_unavailable_old_selection_is_not_silently_changed(self):
        payload={'format':'xml_pair','xml_files':[f(xml('AGENT',text='女士，您好。')),f(xml('USER'))], 'voices':{'USER':'en-US-JennyNeural'}, 'auto_voice':True}
        with self.assertRaises(InputError): normalize(payload)

class InvalidVoiceTests(unittest.TestCase):
    def test_single_and_batch_reject_non_string_voice(self):
        for bad in ([], {}):
            payload={'format':'xml_pair','xml_files':[f(xml('AGENT')),f(xml('USER'))], 'voices':{'USER':bad}, 'auto_voice':True}
            with self.subTest(bad=bad), self.assertRaises(InputError): normalize(payload)
            batch=preview_batch({'files':payload['xml_files'], 'voices':{'USER':bad}, 'auto_voice':True})
            self.assertEqual(batch['groups'][0]['status'],'error')
            self.assertIn('音色无效',batch['groups'][0]['error'])

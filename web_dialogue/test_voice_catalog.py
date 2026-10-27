import json
from pathlib import Path
import tempfile
import unittest
from .voice_catalog import canonicalize, fallback, load_catalog, revision, validate_voice_id
from .normalize import InputError, normalize

class CatalogTests(unittest.TestCase):
    def setUp(self): self.tmp=tempfile.TemporaryDirectory(); self.path=Path(self.tmp.name)/'catalog.json'
    def tearDown(self): self.tmp.cleanup()
    def sample(self):
        return fallback()+[{'ShortName':'en-US-JennyNeural','Locale':'en-US','Gender':'Female'}, {'ShortName':'zh-CN-liaoning-XiaobeiNeural','Locale':'zh-CN-liaoning','Gender':'Female'}, {'ShortName':'iu-Latn-CA-SiqiniqNeural','Locale':'iu-Latn-CA','Gender':'Female'}]
    def fail(self): raise TimeoutError('offline')
    def test_online_deduplicates_and_revision_stable(self):
        raw=self.sample(); data=load_catalog(self.path,lambda:raw+[raw[-1]])
        self.assertEqual(data['source'],'online');self.assertEqual(len(data['voices']),9)
        self.assertEqual(data['revision'],revision(data['voices']))
        self.assertTrue(validate_voice_id('en-US-JennyNeural',data['voices']))
    def test_timeout_uses_last_valid_cache_without_overwrite(self):
        online=load_catalog(self.path,self.sample); previous=self.path.read_bytes()
        cached=load_catalog(self.path,self.fail)
        self.assertEqual(cached['source'],'cache');self.assertEqual(cached['revision'],online['revision']);self.assertEqual(self.path.read_bytes(),previous)
    def test_invalid_network_or_old_cache_falls_back(self):
        self.path.write_text('{"version":0,"voices":[]}')
        data=load_catalog(self.path,lambda:[{'ShortName':'https://evil','Locale':'en-US','Gender':'Female'}])
        self.assertEqual(data['source'],'fallback');self.assertEqual(len(data['voices']),6)
        self.assertEqual(json.loads(self.path.read_text())['version'],0)
    def test_bad_duplicate_and_unknown_id_rejected(self):
        with self.assertRaises(ValueError): canonicalize(self.sample()+[{'ShortName':'en-US-JennyNeural','Locale':'en-US','Gender':'Male'}])
        catalog=load_catalog(self.path,self.sample)['voices']
        payload={'data':[{'speaker':'USER','text':'虚构'}], 'voices':{'USER':'en-US-JennyNeural'}}
        self.assertEqual(normalize(payload,catalog)['script']['lines'][0]['voice'],'en-US-JennyNeural')
        payload['voices']['USER']='en-US-NotInCatalogNeural'
        with self.assertRaises(InputError): normalize(payload,catalog)
    def test_cache_corruption_falls_back(self):
        load_catalog(self.path,self.sample); record=json.loads(self.path.read_text());record['revision']='bad';self.path.write_text(json.dumps(record))
        self.assertEqual(load_catalog(self.path,self.fail)['source'],'fallback')

import unittest
from unittest.mock import patch
from .voice_catalog import canonicalize, fallback, load_catalog, revision, validate_voice_id
from .normalize import InputError, normalize


class CatalogTests(unittest.TestCase):
    def test_startup_never_uses_online_directory_or_files(self):
        with patch('urllib.request.build_opener', side_effect=AssertionError('network')), patch('pathlib.Path.open', side_effect=AssertionError('cache')):
            first = load_catalog(); second = load_catalog()
        self.assertEqual(first, second)
        self.assertEqual(first['source'], 'builtin')
        self.assertEqual(len(first['voices']), 6)
        self.assertTrue(all(v['locale'] == 'zh-CN' for v in first['voices']))
        self.assertEqual(first['revision'], revision(first['voices']))

    def test_foreign_and_regional_voices_are_not_advertised(self):
        extra = [{'id': 'en-US-JennyNeural', 'locale': 'en-US', 'gender': 'Female'},
                 {'id': 'zh-CN-liaoning-XiaobeiNeural', 'locale': 'zh-CN-liaoning', 'gender': 'Female'}]
        self.assertEqual(canonicalize(fallback() + extra), fallback())
        self.assertFalse(validate_voice_id('en-US-JennyNeural', extra))

    def test_backend_rejects_foreign_voice_even_in_injected_catalog(self):
        foreign = {'id': 'en-US-JennyNeural', 'locale': 'en-US', 'gender': 'Female'}
        payload = {'data': [{'speaker': 'USER', 'text': '虚构'}], 'voices': {'USER': foreign['id']}}
        with self.assertRaises(InputError): normalize(payload, fallback() + [foreign])

    def test_changed_builtin_identity_is_rejected(self):
        raw = fallback(); raw[0] = {**raw[0], 'gender': 'Male'}
        with self.assertRaises(ValueError): canonicalize(raw)

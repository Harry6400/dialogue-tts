"""Entirely fictional fixtures; no private conversation data."""
import json
import random
import unittest
from .batch_import import preview_batch
from .normalize import InputError


def xml(role, ref='fiction-call-1', start='2026-01-01T00:00:00', text='你好', mute=False):
    return ('<analyze><subject><function><text>' + text + '</text><time>0,1000</time></function>'
            '<function><text>再见</text><time>2000,3000</time></function></subject>'
            '<callInfo><role>' + role + '</role><callReference>' + ref + '</callReference>'
            '<startTime>' + start + '</startTime><duration>4</duration><phone>secret-phone</phone></callInfo>'
            + ('<mutedSegments><muted start="0" end="1000"/></mutedSegments>' if mute else '') + '</analyze>')


def f(content, name=None):
    if name is None:
        import hashlib
        import xml.etree.ElementTree as ET
        try:
            root=ET.fromstring(content);ref=root.findtext('./callInfo/callReference') or 'invalid'
            prefix=str(int.from_bytes(hashlib.sha256(ref.encode()).digest()[:8]))
            name=prefix+('0' if root.findtext('./callInfo/role')=='AGENT' else '1')+'.xml'
        except ET.ParseError: name='invalid.xml'
    return {'name': name, 'content': content}


class BatchImportTests(unittest.TestCase):
    def test_unordered_two_calls(self):
        r = preview_batch({'files': [f(xml('USER', 'call-2')), f(xml('AGENT')), f(xml('USER')), f(xml('AGENT', 'call-2'))]})
        self.assertEqual(r['counts'], {'ready': 2, 'error': 0})
        self.assertEqual([g['file_indices'] for g in r['groups']], [[0, 3], [1, 2]])
        dumped = json.dumps(r)
        for private in ('secret-phone', 'call-2', 'fiction-call-1', '2026-01-01'):
            self.assertNotIn(private, dumped)

    def test_missing_track_not_guessed(self):
        r = preview_batch({'files': [f(xml('USER')), f(xml('AGENT', 'other-call'))]})
        self.assertEqual(r['counts'], {'ready': 0, 'error': 2})
        self.assertTrue(all(g['error_code'] == 'missing_track' for g in r['groups']))

    def test_missing_time_still_groups_by_ref(self):
        files = [f(xml('USER', start='')), f(xml('AGENT', start=''))]
        self.assertEqual(preview_batch({'files': files})['counts']['ready'], 1)

    def test_manual_pairing_disabled(self):
        files = [f(xml('USER')), f(xml('AGENT', 'other-call'))]
        with self.assertRaises(InputError):
            preview_batch({'files': files, 'pairs': [[0, 1]]})

    def test_duplicate_and_overfull(self):
        for files in ([f(xml('USER')), f(xml('AGENT')), f(xml('USER'))],
                      [f(xml('USER')), f(xml('AGENT')), f(xml('AGENT', text='other'))],
                      [f(xml('USER')), f(xml('AGENT')), f(xml('OTHER'))]):
            result = preview_batch({'files': files})
            self.assertEqual(result['counts'], {'ready': 0, 'error': 1})
            self.assertIn('重复声道', result['groups'][0]['error'])

    def test_same_role(self):
        r = preview_batch({'files': [f(xml('USER')), f(xml('USER', text='不同内容'))]})
        self.assertEqual(r['counts']['error'], 1)

    def test_invalid_pair_indices(self):
        files = [f(xml('USER')), f(xml('AGENT'))]
        for pairs in ([[0, 0]], [[0, 2]], [[True, 1]], [[0, 1], [1, 0]], 'bad'):
            with self.assertRaises(InputError):
                preview_batch({'files': files, 'pairs': pairs})

    def test_muted_protection_and_no_filename_export(self):
        r = preview_batch({'files': [f(xml('AGENT', text='never-emit-this', mute=True), '../../phone-number.xml'), f(xml('USER'))]})
        self.assertEqual(r['counts']['ready'], 0)
        self.assertEqual(r['groups'][0]['error_code'], 'invalid_filename')
        self.assertNotIn('never-emit-this', json.dumps(r))
        self.assertNotIn('phone-number', json.dumps(r))
        self.assertEqual(r['groups'][0]['preview']['muted_count'], 1)

    def test_xxe_is_per_file_error(self):
        evil = '<!DOCTYPE analyze [<!ENTITY a SYSTEM "file:///etc/passwd">]>' + xml('AGENT')
        r = preview_batch({'files': [f(evil), f(xml('AGENT', 'other')), f(xml('USER', 'other'))]})
        self.assertEqual(r['counts'], {'ready': 1, 'error': 1})

    def test_json_and_other_formats_rejected(self):
        for name in ('a.json', 'a.txt', 'a.JSON'):
            r = preview_batch({'files': [f('[{"speaker":"A","text":"好"}]', name)]})
            self.assertEqual(r['counts'], {'ready': 0, 'error': 1})
            self.assertIn('仅支持XML', r['groups'][0]['error'])

    def test_xml_stable_voices(self):
        r = preview_batch({'files': [f(xml('USER')), f(xml('AGENT')), f(xml('AGENT', 'other')), f(xml('USER', 'other'))]})
        maps = [{line['speaker']: line['voice'] for line in g['preview']['script']['lines']} for g in r['groups']]
        self.assertEqual(maps[0], maps[1])

    def test_duration_conflict(self):
        files = [f(xml('AGENT')), f(xml('USER').replace('<duration>4</duration>', '<duration>5</duration>'))]
        result = preview_batch({'files': files})
        self.assertEqual(result['counts']['ready'], 1)
        self.assertTrue(any('时长' in x for x in result['groups'][0]['preview']['warnings']))

    def test_explicit_voice_persists_across_batch(self):
        files = [f(xml('AGENT')), f(xml('USER')), f(xml('AGENT', 'other')), f(xml('USER', 'other'))]
        r = preview_batch({'files': files, 'voices': {'USER': 'zh-CN-YunxiNeural'}})
        self.assertEqual(r['counts']['ready'], 2)
        self.assertTrue(all(line['voice'] == 'zh-CN-YunxiNeural' for g in r['groups'] for line in g['preview']['script']['lines'] if line['speaker'] == 'USER'))

    def test_hundred_shuffled_same_filenames_fifty_calls(self):
        files = [f(xml(role, '00000000000000000000000000000000000000000000000' + str(i)))
                 for i in range(50) for role in ('AGENT', 'USER')]
        random.Random(7).shuffle(files)
        result = preview_batch({'files': files})
        self.assertEqual(result['counts'], {'ready': 50, 'error': 0})
        for group in result['groups']:
            left, right = group['file_indices']
            self.assertNotEqual(left, right)

    def test_leading_zeros_case_and_precision_are_identity(self):
        refs = ('0009007199254740993', '9007199254740993', '9007199254740992', 'Call', 'call')
        files = [f(xml(role, ref)) for ref in refs for role in ('USER', 'AGENT')]
        self.assertEqual(preview_batch({'files': files})['counts']['ready'], 5)

    def test_missing_or_wrong_case_id_cannot_pair(self):
        for change in (lambda x: x.replace('callReference', 'callreference'),
                       lambda x: x.replace('fiction-call-1', ''),
                       lambda x: x.replace('</callReference>', '</callReference><callReference>second</callReference>')):
            result = preview_batch({'files': [f(change(xml('AGENT'))), f(xml('USER'))]})
            self.assertEqual(result['counts']['ready'], 0)

    def test_missing_role_is_explicit(self):
        for role, missing in (('AGENT', 'USER'), ('USER', 'AGENT')):
            result = preview_batch({'files': [f(xml(role))]})
            self.assertIn('缺少 ' + missing, result['groups'][0]['error'])

    def test_start_time_mismatch_is_warning_not_group_key(self):
        result = preview_batch({'files': [f(xml('AGENT')), f(xml('USER', start='different'))]})
        self.assertEqual(result['counts'], {'ready': 1, 'error': 0})
        self.assertTrue(any('开始时间' in x for x in result['groups'][0]['preview']['warnings']))

    def test_limits(self):
        for files in ([], [f('{}', 'a.json')] * 101, [f('a' * (2 * 1024 * 1024 + 1))], [f('a' * (2 * 1024 * 1024))] * 11):
            with self.assertRaises(InputError):
                preview_batch({'files': files})


if __name__ == '__main__':
    unittest.main()

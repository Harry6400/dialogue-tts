"""Entirely fictional fixtures; no private conversation data."""
import json
import unittest
from .batch_import import preview_batch
from .normalize import InputError


def xml(role, ref='fiction-call-1', start='2026-01-01T00:00:00', text='你好', mute=False):
    return ('<analyze><subject><function><text>' + text + '</text><time>0,1000</time></function>'
            '<function><text>再见</text><time>2000,3000</time></function></subject>'
            '<callInfo><role>' + role + '</role><callReference>' + ref + '</callReference>'
            '<startTime>' + start + '</startTime><duration>4</duration><phone>secret-phone</phone></callInfo>'
            + ('<mutedSegments><muted start="0" end="1000"/></mutedSegments>' if mute else '') + '</analyze>')


def f(content, name='input.xml'):
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

    def test_missing_time_needs_manual(self):
        files = [f(xml('USER', start='')), f(xml('AGENT', start=''))]
        self.assertEqual(preview_batch({'files': files})['counts']['ready'], 0)
        self.assertEqual(preview_batch({'files': files, 'pairs': [[1, 0]]})['counts']['ready'], 1)

    def test_manual_conflicting_metadata(self):
        files = [f(xml('USER')), f(xml('AGENT', 'other-call'))]
        r = preview_batch({'files': files, 'pairs': [[0, 1]]})
        self.assertEqual(r['counts']['error'], 1)
        self.assertNotIn('other-call', json.dumps(r))

    def test_duplicate_and_overfull(self):
        for files in ([f(xml('USER')), f(xml('AGENT')), f(xml('USER'))],
                      [f(xml('USER')), f(xml('AGENT')), f(xml('OTHER'))]):
            self.assertEqual(preview_batch({'files': files})['counts']['ready'], 0)
            self.assertEqual(preview_batch({'files': files, 'pairs': [[0, 1]]})['counts']['ready'], 0)

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
        self.assertEqual(r['counts']['ready'], 1)
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
        self.assertEqual(preview_batch({'files': files})['counts']['error'], 1)

    def test_explicit_voice_persists_across_batch(self):
        files = [f(xml('AGENT')), f(xml('USER')), f(xml('AGENT', 'other')), f(xml('USER', 'other'))]
        r = preview_batch({'files': files, 'voices': {'USER': 'zh-CN-YunxiNeural'}})
        self.assertEqual(r['counts']['ready'], 2)
        self.assertTrue(all(line['voice'] == 'zh-CN-YunxiNeural' for g in r['groups'] for line in g['preview']['script']['lines'] if line['speaker'] == 'USER'))

    def test_limits(self):
        for files in ([], [f('{}', 'a.json')] * 41, [f('a' * (2 * 1024 * 1024 + 1))], [f('a' * (2 * 1024 * 1024))] * 4):
            with self.assertRaises(InputError):
                preview_batch({'files': files})


if __name__ == '__main__':
    unittest.main()

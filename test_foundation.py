"""Regression checks for the approved takeover behavior, using only fiction."""
import os
from pathlib import Path
import tempfile
import unittest
import zipfile
from unittest.mock import patch

from native_close import close_decision
from tools.package_macos import package_macos
from tools.package_sources import source_files
from web_dialogue.normalize import normalize, InputError
from web_dialogue.test_xml_pair import payload
from web_dialogue.safe_updater import extract_safe, fetch_release, UpdateError


class FoundationTests(unittest.TestCase):
    def test_fixed_ms_and_agent_first_independent_of_input_order(self):
        value = payload()
        value['xml_files'][0]['content'] = value['xml_files'][0]['content'].replace('3000,5500', '200,5500')
        before = normalize(value)
        self.assertEqual([r['speaker'] for r in before['rows'][:2]], ['AGENT', 'USER'])
        value['xml_files'].reverse()
        self.assertEqual(normalize(value)['rows'], before['rows'])
        self.assertEqual(before['speakers'], ['AGENT', 'USER'])
        value['xml_time_unit'] = 's'
        with self.assertRaisesRegex(InputError, '毫秒'):
            normalize(value)

    def test_exit_requires_complete_state_and_real_saves(self):
        state = dict(running=False, saving=False, pending=False, unsaved=False)
        for bad in (None, {}, {**state, 'unsaved': 'false'}):
            self.assertEqual(close_decision(bad, prompt=lambda k: 'discard'), 'cancel')
        self.assertEqual(close_decision(state, prompt=lambda k: 'cancel'), 'exit')
        self.assertEqual(close_decision({**state, 'unsaved': True}, prompt=lambda k: 'save'), 'save')
        self.assertEqual(close_decision({**state, 'unsaved': True}, prompt=lambda k: 'discard'), 'exit')
        self.assertEqual(close_decision(state, running=True, prompt=lambda k: 'cancel'), 'cancel')
        self.assertEqual(close_decision(state, saving=True, prompt=lambda k: 'discard'), 'cancel')
        self.assertEqual(close_decision({**state, 'pending': True}, prompt=lambda k: 'discard'), 'cancel')

    @unittest.skipIf(os.name == 'nt', 'POSIX symlink packaging')
    def test_unicode_distribution_preserves_safe_links_and_modes(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw); stage = root / 'DialogueTTS-macOS-arm64'
            app = stage / '对白.app'; (app / 'Contents').mkdir(parents=True)
            executable = app / 'Contents' / '测试'; executable.write_bytes(b'fiction'); executable.chmod(0o755)
            (app / 'current').symlink_to('Contents/测试')
            archive = root / 'candidate.zip'; package_macos(stage, archive)
            with zipfile.ZipFile(archive) as z:
                self.assertTrue(all(x.flag_bits & 0x800 for x in z.infolist()))
            extracted = extract_safe(archive, root / 'payload', 'darwin')
            self.assertEqual((extracted / 'current').read_bytes(), b'fiction')
            self.assertTrue((extracted / 'Contents/测试').stat().st_mode & 0o111)

    def test_source_export_includes_workflow_and_frontend_without_dependencies(self):
        root = Path(__file__).resolve().parent
        files = {p.relative_to(root).as_posix() for p in source_files(root)}
        for name in ('test_windows_ci_classification.py', 'test_foundation.py', 'native_close.py',
                     'frontend/src/app/App.tsx', 'frontend/AGENTS.md', 'package-lock.json',
                     'requirements-windows-build.lock', 'web_dialogue/silence.mp3'):
            self.assertIn(name, files)
        self.assertFalse(any('node_modules' in p or '/dist/' in p for p in files))

    def test_verification_profile_is_explicit_and_private(self):
        from web_dialogue.verification_profile import ENV, verification_root
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(verification_root())
            with tempfile.TemporaryDirectory() as raw:
                root = Path(raw); root.chmod(0o700)
                with patch.dict(os.environ, {ENV: str(root)}):
                    self.assertEqual(verification_root(), root.resolve())
                    if os.name != 'nt':
                        root.chmod(0o755)
                        with self.assertRaises(ValueError): verification_root()
                        root.chmod(0o700)
                with patch.dict(os.environ, {ENV: '.'}):
                    with self.assertRaises(ValueError): verification_root()

    def test_tag_lookup_rejects_untrusted_paths_without_network(self):
        with patch('urllib.request.build_opener') as op:
            for tag in ('v1.2.3/../../latest', 'https://evil', '1.2.3', ''):
                with self.assertRaises(UpdateError): fetch_release(tag)
            op.assert_not_called()

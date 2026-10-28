"""Isolated native captures retain complete audio independently of job routes."""
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from native_audio import AudioCaptureBusy, AudioCaptureError, NativeAudioCaptures
from native_workspace import NativeWorkspaceAccess


class NativeAudioTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.state = self.root / 'state'
        self.output = self.root / 'output'; self.output.mkdir()
        self.access = NativeWorkspaceAccess(self.state)
        self.folder = self.access.register(self.output, 'output')['folder_id']
        self.audio = (Path(__file__).parent / 'silence.mp3').read_bytes()
        self.captures = NativeAudioCaptures(self.state, self.access)
        self.route = '/api/jobs/' + 'a' * 32 + '/audio'

    def tearDown(self):
        self.captures._active.clear()
        self.captures.close(); self.access.close(); self.temp.cleanup()

    def reserve(self, **patches):
        return self.captures.reserve({'resource_path': self.route, 'filename': '000001.mp3', 'fingerprint': 'b' * 64, 'size': len(self.audio), **patches})

    def capture(self, **patches):
        capture = self.reserve(**patches)
        Path(capture['path']).write_bytes(self.audio)
        self.captures.complete(capture['capture_id'])
        return capture

    def test_source_route_can_disappear_and_only_verified_ledger_save_clears_capture(self):
        captured = self.capture()
        with patch.object(self.access, 'save_audio', side_effect=OSError('fictional disk full')):
            with self.assertRaises(OSError): self.captures.save(captured['capture_id'], self.folder)
        self.assertEqual(Path(captured['path']).read_bytes(), self.audio)
        self.assertEqual(self.captures.find(self.route)['capture']['capture_id'], captured['capture_id'])
        receipt = self.captures.save(captured['capture_id'], self.folder)
        self.assertEqual((self.output / '000001.mp3').read_bytes(), self.audio)
        self.assertEqual(receipt['resource_path'], self.route)
        self.assertEqual(receipt['state'], 'saved')
        self.assertIsNone(self.captures.find(self.route)['capture'])
        self.assertFalse(Path(captured['path']).exists())

    def test_complete_capture_and_response_metadata_survive_restart(self):
        captured = self.capture()
        self.captures.close()
        self.captures = NativeAudioCaptures(self.state, self.access)
        self.assertTrue(self.captures.safety()['unsaved'])
        self.assertEqual(self.captures.pending()['captures'][0]['filename'], '000001.mp3')
        self.assertTrue(self.captures.save(captured['capture_id'], self.folder)['ok'])

    def test_full_file_before_completion_callback_is_recovered_but_partial_is_not_saved(self):
        captured = self.reserve(); Path(captured['path']).write_bytes(self.audio)
        self.captures._active.clear(); self.captures.close()
        self.captures = NativeAudioCaptures(self.state, self.access)
        self.assertEqual(self.captures.pending()['captures'][0]['state'], 'ready')
        self.captures.discard(captured['capture_id'])
        partial = self.reserve(); Path(partial['path']).write_bytes(self.audio[:17])
        self.captures.abort(partial['capture_id'])
        self.assertEqual(self.captures.pending()['captures'][0]['state'], 'interrupted')
        with self.assertRaises(ValueError): self.captures.save(partial['capture_id'], self.folder)
        self.assertFalse((self.output / '000001.mp3').exists())
        self.captures.discard(partial['capture_id'])

    def test_invalid_native_metadata_and_invalid_mp3_never_publish_audio(self):
        for patch_values in ({'filename': '../000001.mp3'}, {'filename': 'custom.mp3'}, {'fingerprint': 'wrong'}, {'resource_path': '/api/config'}, {'size': True}, {'size': 0}):
            with self.subTest(values=patch_values), self.assertRaises(ValueError): self.reserve(**patch_values)
        with self.assertRaises(ValueError): self.captures.reserve({'path': '/private/not-authorized'})
        captured = self.reserve(); Path(captured['path']).write_bytes(b'x' * len(self.audio))
        with self.assertRaises(ValueError): self.captures.complete(captured['capture_id'])
        self.assertEqual(list(self.output.iterdir()), [])
        self.assertEqual(self.captures.pending()['captures'][0]['state'], 'interrupted')

    def test_capture_content_change_remains_until_explicit_discard(self):
        captured = self.capture()
        Path(captured['path']).write_bytes(self.audio * 2)
        with self.assertRaises(ValueError): self.captures.save(captured['capture_id'], self.folder)
        self.assertTrue(Path(captured['path']).exists())
        self.assertTrue(self.captures.safety()['unsaved'])
        self.captures.discard(captured['capture_id'])
        self.assertFalse(self.captures.safety()['unsaved'])

    def test_cache_reservation_rejects_overflow_without_evicting_existing_audio(self):
        self.captures.close(); self.captures = NativeAudioCaptures(self.state, self.access, max_pending=1, max_bytes=len(self.audio))
        captured = self.capture()
        with self.assertRaises(AudioCaptureBusy): self.reserve(resource_path='/api/jobs/' + 'c' * 32 + '/audio')
        self.assertEqual(Path(captured['path']).read_bytes(), self.audio)

    def test_download_read_and_save_leases_block_discard_and_close(self):
        captured = self.reserve()
        self.assertTrue(self.captures.safety()['pending'])
        with self.assertRaises(AudioCaptureBusy): self.captures.discard(captured['capture_id'])
        with self.assertRaises(AudioCaptureBusy): self.captures.close()
        Path(captured['path']).write_bytes(self.audio); self.captures.complete(captured['capture_id'])
        with self.captures.lease(captured['capture_id']):
            with self.assertRaises(AudioCaptureBusy): self.captures.discard(captured['capture_id'])
            self.assertTrue(self.captures.safety()['pending'])
        entered, release = threading.Event(), threading.Event()
        original = self.access.save_audio
        results = []
        def blocked(*args, **kwargs):
            entered.set(); release.wait(5)
            return original(*args, **kwargs)
        with patch.object(self.access, 'save_audio', side_effect=blocked):
            thread = threading.Thread(target=lambda: results.append(self.captures.save(captured['capture_id'], self.folder))); thread.start()
            self.assertTrue(entered.wait(3))
            self.assertTrue(self.captures.safety()['saving'])
            with self.assertRaises(AudioCaptureBusy): self.captures.discard(captured['capture_id'])
            with self.assertRaises(AudioCaptureBusy): self.captures.save(captured['capture_id'], self.folder)
            release.set(); thread.join(5)
        self.assertTrue(results[0]['ok'])

    def test_conflict_and_receipt_failure_retain_complete_capture_for_retry(self):
        captured = self.capture(); target = self.output / '000001.mp3'; target.write_bytes(b'foreign existing file')
        with self.assertRaises(FileExistsError): self.captures.save(captured['capture_id'], self.folder)
        self.assertEqual(target.read_bytes(), b'foreign existing file')
        target.unlink()
        self.access._db.execute("CREATE TRIGGER reject_capture_receipt BEFORE INSERT ON receipts BEGIN SELECT RAISE(FAIL,'disk full'); END")
        with self.assertRaises(OSError): self.captures.save(captured['capture_id'], self.folder)
        self.assertTrue(Path(captured['path']).exists())
        self.assertEqual(target.read_bytes(), self.audio)
        self.access._db.execute('DROP TRIGGER reject_capture_receipt')
        self.assertTrue(self.captures.save(captured['capture_id'], self.folder)['ok'])

    def test_unknown_files_and_replaced_owned_inode_are_not_deleted(self):
        captured = self.capture(); path = Path(captured['path'])
        original = path.with_name('original.mp3'); path.rename(original); path.write_bytes(self.audio)
        with self.assertRaises(ValueError): self.captures.discard(captured['capture_id'])
        self.assertTrue(path.exists()); self.assertTrue(original.exists())
        foreign = self.captures.root / 'unknown.mp3'; foreign.write_bytes(b'not ours')
        self.assertTrue(self.captures.safety()['unknown'])
        foreign.unlink(); path.unlink(); original.rename(path)
        self.captures.discard(captured['capture_id'])

    def test_ledger_rechecks_expected_capture_bytes_during_actual_publication(self):
        captured = self.capture()
        original = self.access._read
        def changed(path, limit, *args):
            data = original(path, limit, *args)
            return data + self.audio
        with patch.object(self.access, '_read', side_effect=changed):
            with self.assertRaises(ValueError): self.captures.save(captured['capture_id'], self.folder)
        self.assertFalse((self.output / '000001.mp3').exists())
        self.assertTrue(Path(captured['path']).exists())

"""Native grants and the indexed ledger, entirely inside temporary profiles."""
import hashlib
import json
import os
import sqlite3
from pathlib import Path
import tempfile
import unittest
import threading
import urllib.request
import urllib.error
from types import SimpleNamespace
from unittest.mock import patch
from contextlib import ExitStack

from native_workspace import NativeWorkspaceAccess, MAX_XML


class NativeWorkspaceAccessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.input = self.root / 'input'; self.input.mkdir()
        self.output = self.root / 'output'; self.output.mkdir()
        self.state = self.root / 'state'
        self.access = NativeWorkspaceAccess(self.state)
        self.inc = self.access.register(self.input, 'input')['folder_id']
        self.out = self.access.register(self.output, 'output')['folder_id']
        self.audio = Path(__file__).parent / 'silence.mp3'

    def tearDown(self):
        self.access.close()
        self.temp.cleanup()

    def test_grants_inactive_after_restart_and_directory_replacement(self):
        self.access.close(); self.access = NativeWorkspaceAccess(self.state)
        with self.assertRaises(ValueError): self.access.directory(self.inc, 'input')
        self.assertEqual(self.access.restore(self.inc)['folder_id'], self.inc)
        self.input.rename(self.root / 'previous-input'); self.input.mkdir()
        with self.assertRaises(ValueError): self.access.restore(self.inc)
        with self.assertRaises(ValueError): self.access.register(str(self.input), 'unknown')
        with self.assertRaises(ValueError): self.access.register(None, 'input')

    def test_streaming_names_has_no_legacy_100_file_limit(self):
        for i in range(1201): (self.input / f'{i}.xml').write_bytes(b'<x/>')
        (self.input / 'ignore.txt').write_text('not XML')
        names = self.access.iter_files(self.inc)
        self.assertIs(iter(names), names)
        self.assertEqual(len(list(names)), 1201)
        self.assertEqual(self.access.read_file(self.inc, '0.xml'), b'<x/>')
        with self.assertRaises(ValueError): self.access.read_file(self.inc, '../secret.xml')
        (self.input / 'big.xml').write_bytes(b'x' * (MAX_XML + 1))
        with self.assertRaises(ValueError): self.access.read_file(self.inc, 'big.xml')
        if os.name != 'nt':
            (self.input / 'linked.xml').symlink_to(self.audio)
            with self.assertRaises(ValueError): self.access.read_file(self.inc, 'linked.xml')

    @unittest.skipIf(os.name == 'nt', 'Windows does not allow backslashes in basenames')
    def test_unsupported_real_filename_is_one_invalid_item_not_scan_failure(self):
        from .app import Jobs
        from .test_batch_import import xml
        from .workspaces import Workspaces
        import time
        for role, suffix in (('AGENT', '0'), ('USER', '1')):
            (self.input / ('001' + suffix + '.xml')).write_text(xml(role, ref='fictional-valid-pair'))
        bad_name = 'bad\\name.xml'
        (self.input / bad_name).write_text('<analyze/>')
        self.assertIn(bad_name, list(self.access.iter_files(self.inc)))
        with self.assertRaises(ValueError): self.access.read_file(self.inc, bad_name)
        jobs = Jobs()
        work = Workspaces(self.state, jobs, self.access, synthesizer=lambda preview: self.fail('scan must not synthesize'))
        try:
            ident = work.import_folder(self.inc)['id']; deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                result = work.summary(ident)
                if result['status'] != 'scanning': break
                time.sleep(.01)
            self.assertEqual(result['status'], 'ready', result)
            self.assertEqual(result['ready'], 1); self.assertEqual(result['error'], 1)
            self.assertEqual(result['total'], 2)
            self.assertTrue((self.input / bad_name).exists())
        finally:
            work.close(); jobs.close()

    def test_publication_no_replace_and_real_receipt_validation(self):
        first = self.access.save_audio(self.out, self.audio, '000001.mp3', 'a' * 64)
        self.assertEqual(first['state'], 'saved')
        self.assertEqual(self.access.save_audio(self.out, self.audio, '000001.mp3', 'a' * 64)['save_id'], first['save_id'])
        with self.assertRaises(FileExistsError): self.access.save_audio(self.out, self.audio, '000001.mp3', 'b' * 64)
        target = self.output / '000001.mp3'; target.write_bytes(b'changed')
        self.assertEqual(self.access.verify_output(self.out, target.name, 'a' * 64)['state'], 'conflict')
        target.unlink()
        self.assertEqual(self.access.verify_output(self.out, target.name, 'a' * 64)['state'], 'missing')
        empty = self.root / 'empty.mp3'; empty.write_bytes(b'')
        with self.assertRaises(ValueError): self.access.save_audio(self.out, empty, '2.mp3', 'c' * 64)
        self.assertFalse((self.output / '2.mp3').exists())

    def test_crash_between_publication_and_receipt_recovers_owned_inode(self):
        class SimulatedCrash(BaseException): pass
        with patch.object(self.access, '_after_publish', side_effect=SimulatedCrash):
            with self.assertRaises(SimulatedCrash): self.access.save_audio(self.out, self.audio, '5.mp3', 'd' * 64)
        self.assertEqual(len(list(self.output.glob('*.mp3'))), 1)
        self.access.close(); self.access = NativeWorkspaceAccess(self.state)
        self.access.restore(self.out)
        result = self.access.verify_output(self.out, '5.mp3', 'd' * 64)
        self.assertEqual(result['state'], 'saved')
        self.assertEqual(list(self.output.glob('.dialogue-*.tmp')), [])
        self.assertEqual(self.access._db.execute('SELECT count(*) FROM intents').fetchone()[0], 0)

    def test_racing_foreign_file_is_never_adopted_or_removed(self):
        target = self.output / '6.mp3'
        original_link = os.link
        def competing_link(*args, **kwargs):
            target.write_bytes(b'external')
            return original_link(*args, **kwargs)
        if os.name == 'nt': self.skipTest('POSIX publication race; Windows rename covered on Windows CI')
        with patch('native_workspace.os.link', side_effect=competing_link):
            with self.assertRaises(FileExistsError): self.access.save_audio(self.out, self.audio, target.name, 'e' * 64)
        self.assertEqual(target.read_bytes(), b'external')
        self.assertEqual(self.access.verify_output(self.out, target.name, 'e' * 64)['state'], 'conflict')

    def test_receipt_database_failure_preserves_published_audio_for_recovery(self):
        self.access._db.execute("CREATE TRIGGER fail_receipt BEFORE INSERT ON receipts BEGIN SELECT RAISE(FAIL,'disk failure'); END")
        with self.assertRaises(OSError): self.access.save_audio(self.out, self.audio, '7.mp3', 'e' * 64)
        self.assertEqual((self.output / '7.mp3').read_bytes(), self.audio.read_bytes())
        self.access._db.execute('DROP TRIGGER fail_receipt')
        self.assertEqual(self.access.verify_output(self.out, '7.mp3', 'e' * 64)['state'], 'saved')

    def test_swift_legacy_publication_intent_is_recovered_only_on_authorized_check(self):
        if os.name == 'nt': self.skipTest('Legacy macOS hard-link journal')
        import uuid
        data = self.audio.read_bytes(); temporary = '.dialogue-' + str(uuid.uuid4()) + '.tmp'
        (self.output / temporary).write_bytes(data); os.link(self.output / temporary, self.output / '8.mp3')
        pending = {'folder': str(self.output), 'temporary': temporary, 'fingerprint': 'a' * 64, 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
        legacy = self.state / 'swift-legacy.json'; legacy.write_text(json.dumps({'pending_outputs': [pending]}))
        self.access.migrate_preferences(legacy)
        self.assertEqual(self.access._db.execute('SELECT count(*) FROM receipts').fetchone()[0], 0)
        self.assertTrue((self.output / temporary).exists())
        self.assertEqual(self.access.verify_output(self.out, '8.mp3', 'a' * 64)['state'], 'saved')
        self.assertFalse((self.output / temporary).exists())

    def test_60001_legacy_receipts_keep_earliest_actual_result(self):
        data = self.audio.read_bytes(); digest = hashlib.sha256(data).hexdigest()
        (self.output / '000000.mp3').write_bytes(data)
        # Exercise the real migration path, well beyond the old 2,000 eviction cap.
        records = [{'folder': str(self.output), 'name': f'{i:06}.mp3', 'fingerprint': f'{i:064x}', 'size': len(data), 'sha256': digest} for i in range(60001)]
        legacy = self.state / 'legacy-preferences.json'; legacy.write_text(json.dumps({'records': records}))
        self.access.migrate_preferences(legacy)
        self.assertEqual(self.access._db.execute('SELECT count(*) FROM receipts').fetchone()[0], 60001)
        self.access.close(); self.access = NativeWorkspaceAccess(self.state); self.access.restore(self.out)
        self.assertEqual(self.access.verify_output(self.out, '000000.mp3', '0' * 64)['state'], 'saved')
        self.assertEqual(self.access.verify_output(self.out, '060000.mp3', f'{60000:064x}')['state'], 'missing')

    def test_windows_legacy_receipts_and_exports_migrate_once(self):
        data = self.audio.read_bytes(); (self.output / '9.mp3').write_bytes(data)
        record = {'name': '9.mp3', 'fingerprint': 'f' * 64, 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
        old = self.state / 'windows-prefs.json'
        old.write_text(json.dumps({'receipts': {str(self.output): {'f' * 64: record}}, 'exports': {str(self.output) + '|/api/jobs/old/audio': record}}))
        self.access.migrate_preferences(old); self.access.migrate_preferences(old)
        self.assertEqual(self.access._db.execute('SELECT count(*) FROM receipts').fetchone()[0], 1)
        self.assertEqual(self.access.verify_output(self.out, '9.mp3', 'f' * 64)['state'], 'saved')


class NativeWorkspaceBridgeTests(unittest.TestCase):
    def test_owned_ledger_closes_but_borrowed_bridge_cannot_close_shared_access(self):
        from windows_launcher import NativeDownloads
        with tempfile.TemporaryDirectory() as temp, ExitStack() as cleanup:
            root = Path(temp); target = root / 'output'; target.mkdir()
            owner = NativeDownloads('http://127.0.0.1:1', root / 'state' / 'preferences.json')
            cleanup.callback(owner._close_folders)
            access = owner._ensure_workspace_access()
            self.assertTrue(owner._owns_workspace_access)
            grant = access.register(target, 'output')
            borrower = NativeDownloads('http://127.0.0.1:1', root / 'state' / 'preferences.json', workspace_access=access)
            self.assertFalse(borrower._owns_workspace_access)
            borrower._close_folders(); borrower._close_folders()
            self.assertEqual(access.directory(grant['folder_id'], 'output'), target)
            owner._close_folders(); owner._close_folders()
            self.assertIsNone(owner._workspace_access)
            self.assertFalse(owner._owns_workspace_access)
            self.assertTrue(access._closed)
            with self.assertRaises(sqlite3.ProgrammingError): access._db.execute('SELECT 1')
            access.close()  # Repeated server/bridge teardown is safe.
            # Removal follows explicit close; Windows cannot unlink an open SQLite DB.
            import shutil
            shutil.rmtree(root / 'state')
            self.assertFalse((root / 'state').exists())

    def test_private_token_registration_and_native_directory_choice_never_scan(self):
        from .app import make_server
        from windows_launcher import NativeDownloads
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); source = root / 'input'; source.mkdir(); output = root / 'output'; output.mkdir()
            secret = 'native-secret-' + 'a' * 48
            server = make_server(0, state_dir=root / 'state', native_token=secret)
            thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
            base = f'http://127.0.0.1:{server.server_address[1]}'
            try:
                request = urllib.request.Request(base + '/api/native/folders', data=json.dumps({'path': str(source), 'kind': 'input'}).encode(), headers={'Content-Type': 'application/json', 'X-App-Token': server.token})
                with self.assertRaises(urllib.error.HTTPError) as denied: urllib.request.urlopen(request)
                self.assertEqual(denied.exception.code, 403)
                bridge = NativeDownloads(base, root / 'state' / 'preferences.json', native_token=secret, workspace_access=server.native_access)
                bridge._window = SimpleNamespace(create_file_dialog=lambda *a, **k: (str(source),))
                webview = SimpleNamespace(FileDialog=SimpleNamespace(FOLDER=20))
                with patch.dict('sys.modules', {'webview': webview}), patch.object(bridge, '_scan', side_effect=AssertionError('workspace selection must not enumerate XML')):
                    selected = bridge.choose_workspace_input()
                self.assertTrue(selected['ok'], selected); self.assertNotIn('files', selected)
                self.assertEqual(server.native_access.directory(selected['folder_id'], 'input'), source)
                safety = bridge._native_workspace_request('/api/native/workspaces/safety', {})
                self.assertTrue(all(type(safety.get(key)) is bool for key in ('busy', 'unsaved', 'saving', 'pending')))
                self.assertFalse(any(safety[key] for key in ('busy', 'unsaved', 'saving', 'pending')))
                grant = server.native_access.register(output, 'output')
                with patch('os.startfile', create=True) as reveal:
                    self.assertTrue(bridge.reveal_output(grant['folder_id'])['ok'])
                    reveal.assert_called_once_with(str(output), 'explore')
                with urllib.request.urlopen(base + '/api/config') as response: config = json.load(response)
                self.assertNotIn(secret, json.dumps(config))
            finally:
                server.shutdown(); server.server_close(); server.workspaces.close(); server.jobs.close(); server.native_access.close()


if __name__ == '__main__': unittest.main()

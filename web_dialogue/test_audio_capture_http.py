"""Offline loopback acceptance of native-only audio ownership and receipts."""
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

from .app import close_resources, make_server


class AudioCaptureHTTPTests(unittest.TestCase):
    native_token = 'isolated-audio-capture-native'

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='dialogue-audio-http-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / 'state'
        self.output = self.root / 'fictional-output'
        self.output.mkdir()
        self.sentinel = self.output / 'existing.mp3'
        self.sentinel.write_bytes(b'keep unrelated output')
        self.audio = (Path(__file__).parent / 'silence.mp3').read_bytes()
        self.digest = hashlib.sha256(self.audio).hexdigest()
        self.route = '/api/jobs/' + 'a' * 32 + '/audio'
        self.metadata = {'resource_path': self.route, 'filename': '000001.mp3',
                         'fingerprint': 'b' * 64, 'size': len(self.audio)}
        self.no_synthesis = patch('web_dialogue.jobs.Jobs.start', side_effect=AssertionError('HTTP capture checks must never synthesize'))
        self.start_synthesis = self.no_synthesis.start()
        self.addCleanup(self.no_synthesis.stop)
        self.server = None
        self.addCleanup(self.stop_server)
        self.start_server()

    def start_server(self):
        self.server = make_server(0, state_dir=self.state, native_token=self.native_token)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
        self.thread.start()
        self.base = 'http://127.0.0.1:' + str(self.server.server_address[1])
        self.folder_id = self.server.native_access.register(self.output, 'output')['folder_id']

    def stop_server(self):
        if self.server is None:
            return
        server, self.server = self.server, None
        server.shutdown()
        self.thread.join(timeout=5)
        self.assertFalse(self.thread.is_alive(), 'Loopback server did not stop')
        server.server_close()
        self.assertEqual(close_resources(server), {'ok': True, 'errors': []})
        self.assertEqual(close_resources(server), {'ok': True, 'errors': []})
        self.assertEqual(server._closed_resources, {'workspaces', 'batches', 'jobs', 'reports', 'audio_captures', 'native_access'})
        self.assertEqual(self.sentinel.read_bytes(), b'keep unrelated output')
        self.start_synthesis.assert_not_called()

    def request(self, path, body=None, *, native=False, token=None, origin=None):
        headers = {'Origin': origin or self.base}
        if body is not None:
            headers['Content-Type'] = 'application/json'
            headers['X-Native-Token' if native else 'X-App-Token'] = token or (self.native_token if native else self.server.token)
        request = urllib.request.Request(self.base + path, data=None if body is None else json.dumps(body).encode('utf-8'), headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as issue:
            return issue.code, json.load(issue)

    def private(self, action, body):
        return self.request('/api/native/audio-captures/' + action, body, native=True)

    def capture(self, *, ready=True, **metadata):
        status, captured = self.private('reserve', {**self.metadata, **metadata})
        self.assertEqual(status, 200, captured)
        self.assertTrue(captured['ok'])
        self.assertEqual(captured['state'], 'downloading')
        path = Path(captured['path'])
        self.assertEqual(path.parent.parent, self.state / 'native-audio')
        path.write_bytes(self.audio)
        if ready:
            status, completed = self.private('complete', {'capture_id': captured['capture_id']})
            self.assertEqual(status, 200, completed)
            self.assertEqual(completed['state'], 'ready')
            self.assertNotIn('path', completed)
        return captured

    def pending(self):
        status, result = self.private('pending', {})
        self.assertEqual(status, 200, result)
        return result['captures']

    def save(self, captured, *, folder_id=None):
        return self.private('save', {'capture_id': captured['capture_id'], 'folder_id': folder_id or self.folder_id})

    def assert_saved(self, receipt, name='000001.mp3'):
        self.assertIs(receipt['ok'], True)
        self.assertEqual(receipt['state'], 'saved')
        self.assertEqual(receipt['name'], name)
        self.assertEqual(receipt['size'], len(self.audio))
        self.assertEqual(receipt['sha256'], self.digest)
        self.assertEqual(receipt['path'], str(self.output / name))
        self.assertEqual((self.output / name).read_bytes(), self.audio)
        self.assertRegex(receipt['save_id'], r'^[a-f0-9]{32}$')
        with sqlite3.connect(self.state / 'native-workspace.sqlite3') as database:
            row = database.execute('SELECT fingerprint,size,sha256,save_id FROM receipts WHERE folder=? AND name=?', (str(self.output), name)).fetchone()
        self.assertEqual(row, (self.metadata['fingerprint'], len(self.audio), self.digest, receipt['save_id']))

    def test_real_startup_and_save_use_verified_ledger_without_original_job(self):
        self.assertEqual(self.request('/api/config')[0], 200)
        self.assertEqual(self.request(self.route)[0], 404)
        captured = self.capture()
        pending = self.pending()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]['capture_id'], captured['capture_id'])
        self.assertNotIn('path', pending[0])
        status, receipt = self.save(captured)
        self.assertEqual(status, 200, receipt)
        self.assert_saved(receipt)
        self.assertEqual(receipt['resource_path'], self.route)
        self.assertEqual(self.pending(), [])
        self.assertFalse(Path(captured['path']).exists())
        self.assertEqual(self.request(self.route)[0], 404)

    def test_app_token_cannot_operate_private_captures_or_authorize_paths(self):
        captured = self.capture()
        actions = {'reserve': self.metadata, 'complete': {'capture_id': captured['capture_id']},
                   'abort': {'capture_id': captured['capture_id']}, 'find': {'resource_path': self.route},
                   'pending': {}, 'save': {'capture_id': captured['capture_id'], 'folder_id': self.folder_id},
                   'discard': {'capture_id': captured['capture_id']}}
        for action, body in actions.items():
            with self.subTest(action=action):
                path = '/api/native/audio-captures/' + action
                self.assertEqual(self.request(path, body)[0], 403)
                self.assertEqual(self.request(path, body, native=True, token=self.server.token)[0], 403)
                self.assertEqual(self.private(action, {**body, 'unexpected': True})[0], 400)
                self.assertEqual(self.private(action, {**body, 'path': str(self.output)})[0], 400)
        self.assertEqual(self.request('/api/native/folders', {'path': str(self.output), 'kind': 'output'})[0], 403)
        self.assertEqual(self.private('save', {'capture_id': captured['capture_id'], 'folder_id': str(self.output)})[0], 400)
        self.assertEqual(self.request('/api/native/audio-captures/pending', {}, native=True, origin='https://untrusted.invalid')[0], 403)
        self.assertEqual([item['capture_id'] for item in self.pending()], [captured['capture_id']])
        self.assertEqual(Path(captured['path']).read_bytes(), self.audio)
        self.assertFalse((self.output / '000001.mp3').exists())

    def test_restart_recovers_complete_capture_but_requires_output_authorization_again(self):
        captured = self.capture(ready=False)
        self.assertEqual(self.private('abort', {'capture_id': captured['capture_id']})[1]['state'], 'ready')
        old_folder_id = self.folder_id
        self.stop_server()
        # Startup must recover captures before the new shell restores any grant.
        self.server = make_server(0, state_dir=self.state, native_token=self.native_token)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
        self.thread.start()
        self.base = 'http://127.0.0.1:' + str(self.server.server_address[1])
        self.assertEqual(self.pending()[0]['state'], 'ready')
        status, failure = self.save(captured, folder_id=old_folder_id)
        self.assertEqual(status, 400, failure)
        self.assertEqual(Path(captured['path']).read_bytes(), self.audio)
        self.folder_id = self.server.native_access.register(self.output, 'output')['folder_id']
        status, receipt = self.save(captured)
        self.assertEqual(status, 200, receipt)
        self.assert_saved(receipt)

    def current_manual_source(self):
        from .test_job_ownership import completed
        job_id=completed(self.server.jobs,name=self.metadata['filename'],fingerprint=self.metadata['fingerprint'])
        self.route=f'/api/jobs/{job_id}/audio';self.metadata['resource_path']=self.route
        return job_id

    def assert_current_links(self):
        with urllib.request.urlopen(self.base+self.route,timeout=5) as response:
            self.assertEqual(response.status,200);self.assertEqual(response.read(),self.audio)
        for kind in ('normalized','timeline'):
            self.assertEqual(self.request(self.route.rsplit('/',1)[0]+'/'+kind)[0],200)

    def test_complete_keeps_current_manual_audio_and_auxiliary_links_until_saved(self):
        job_id=self.current_manual_source()
        captured=self.capture()
        self.assertTrue(self.server.jobs.is_known(job_id));self.assert_current_links()
        status,receipt=self.save(captured)
        self.assertEqual(status,200,receipt);self.assert_saved(receipt)
        self.assertFalse(self.server.jobs.is_known(job_id))
        self.assertEqual(self.request(self.route)[0],404)

    def test_complete_capture_remains_savable_after_original_source_is_released(self):
        job_id=self.current_manual_source()
        captured=self.capture();self.assert_current_links()
        self.server.jobs.release(job_id)
        self.assertEqual(self.request(self.route)[0],404)
        status,receipt=self.save(captured)
        self.assertEqual(status,200,receipt);self.assert_saved(receipt)

    def test_collision_retains_capture_and_explicit_discard_only_removes_that_capture(self):
        captured = self.capture()
        other = self.capture(resource_path='/api/jobs/' + 'c' * 32 + '/audio', filename='000002.mp3')
        target = self.output / '000001.mp3'
        target.write_bytes(b'keep colliding output')
        status, failure = self.save(captured)
        self.assertEqual(status, 500, failure)
        self.assertEqual(Path(captured['path']).read_bytes(), self.audio)
        self.assertEqual(target.read_bytes(), b'keep colliding output')
        self.assertEqual(self.private('discard', {'capture_id': captured['capture_id']}), (200, {'ok': True}))
        self.assertFalse(Path(captured['path']).exists())
        self.assertEqual(Path(other['path']).read_bytes(), self.audio)
        self.assertEqual([item['capture_id'] for item in self.pending()], [other['capture_id']])
        self.assertEqual(target.read_bytes(), b'keep colliding output')
        with sqlite3.connect(self.state / 'native-workspace.sqlite3') as database:
            self.assertEqual(database.execute('SELECT count(*) FROM receipts').fetchone()[0], 0)

    def test_downloading_and_read_lease_protect_cleanup_and_report_distinct_safety(self):
        captured = self.capture(ready=False)
        for expected_pending in (True, False):
            status, safety = self.request('/api/workspaces/safety')
            self.assertEqual(status, 200, safety)
            self.assertIs(safety['unsaved'], False)
            self.assertIs(safety['legacy_unsaved'], True)
            self.assertIs(safety['pending'], expected_pending)
            self.assertIs(safety['busy'], expected_pending)
            self.assertEqual(self.request('/api/update/lock', {})[0], 409)
            if expected_pending:
                self.assertEqual(self.private('discard', {'capture_id': captured['capture_id']})[0], 400)
                self.assertEqual(self.private('discard', {})[0], 400)
                self.assertEqual(self.private('complete', {'capture_id': captured['capture_id']})[0], 200)
        with self.server.audio_captures.lease(captured['capture_id']):
            safety = self.private('pending', {})[1]
            self.assertEqual(len(safety['captures']), 1)
            safety = self.request('/api/workspaces/safety')[1]
            self.assertIs(safety['pending'], True)
            self.assertIs(safety['busy'], True)
            self.assertIs(safety['saving'], False)
            self.assertEqual(self.private('discard', {})[0], 400)
            self.assertEqual(self.request('/api/update/lock', {})[0], 409)
        self.assertEqual(self.private('discard', {'capture_id': captured['capture_id']})[0], 200)
        safety = self.request('/api/workspaces/safety')[1]
        self.assertFalse(any(safety[key] for key in ('busy', 'unsaved', 'saving', 'pending', 'legacy_unsaved')))

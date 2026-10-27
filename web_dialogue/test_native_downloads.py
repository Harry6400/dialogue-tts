import io
import tempfile
import unittest
from email.message import Message
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from windows_launcher import NativeDownloads

class NativeSaveTests(unittest.TestCase):
    def test_save_cancel_and_reveal(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'saved.mp3'
            bridge = NativeDownloads('http://127.0.0.1:1234')
            callbacks = []
            bridge._window = SimpleNamespace(create_file_dialog=lambda *a, **k: (str(target),), evaluate_js=callbacks.append)
            data = b'fictional-audio'
            response = io.BytesIO(data)
            response.headers = Message(); response.headers['Content-Type'] = 'audio/mpeg'; response.headers['Content-Length'] = str(len(data))
            with patch.dict('sys.modules', {'webview': SimpleNamespace(FileDialog=SimpleNamespace(SAVE=30))}), patch('urllib.request.build_opener') as op:
                op.return_value.open.return_value = response
                saved = bridge.save_download('/api/jobs/'+'a'*32+'/audio')
                self.assertTrue(saved['ok']); self.assertEqual(target.read_bytes(), data)
                self.assertEqual(len(callbacks), 1)
                with patch('os.startfile', create=True) as start:
                    self.assertFalse(bridge.reveal_saved(str(target))['ok'])
                    self.assertTrue(bridge.reveal_saved(saved['save_id'])['ok'])
                    start.assert_called_once_with(str(target.parent), 'explore')
                bridge._window.create_file_dialog = lambda *a, **k: None
                self.assertTrue(bridge.save_download('/api/jobs/'+'a'*32+'/audio')['cancelled'])
                self.assertEqual(len(callbacks), 1)
    def test_reject_arbitrary_routes(self):
        bridge = NativeDownloads('http://127.0.0.1:1234')
        for route in ('https://evil.example/a', '/api/jobs/../secret', '/api/config', '/api/jobs/'+'a'*32+'/audio?x=1', None):
            self.assertFalse(bridge.save_download(route)['ok'])
    def test_incomplete_download_preserves_existing_file(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'saved.mp3'; target.write_bytes(b'existing')
            bridge = NativeDownloads('http://127.0.0.1:1234'); callbacks = []
            bridge._window = SimpleNamespace(create_file_dialog=lambda *a, **k: (str(target),), evaluate_js=callbacks.append)
            response = io.BytesIO(b'short'); response.headers = Message()
            response.headers['Content-Type'] = 'audio/mpeg'; response.headers['Content-Length'] = '200'
            with patch.dict('sys.modules', {'webview': SimpleNamespace(FileDialog=SimpleNamespace(SAVE=30))}), patch('urllib.request.build_opener') as op:
                op.return_value.open.return_value = response
                self.assertFalse(bridge.save_download('/api/jobs/'+'a'*32+'/audio')['ok'])
            self.assertEqual(target.read_bytes(), b'existing'); self.assertFalse(callbacks)
            self.assertEqual(list(Path(folder).iterdir()), [target]); self.assertFalse(bridge._saved)

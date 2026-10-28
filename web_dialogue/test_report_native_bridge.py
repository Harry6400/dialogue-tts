"""Windows save-panel contract using a disposable host, never a real window."""
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from windows_launcher import NativeDownloads
from .runtime_safety import combined_safety


class ReportNativeBridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.host = NativeDownloads('http://127.0.0.1:1234', self.root / 'prefs.json', native_token='test-native')
        self.calls = []
        self.target = self.root / '虚构清单.csv'

        def request(route, payload):
            self.assertTrue(self.host._lock.locked())
            self.calls.append((route, payload))
            if route.endswith('/info'):
                return {'report_id': 'a' * 32, 'filename': '虚构清单.csv', 'rows': 120000, 'size': 1024}
            return {'ok': True, 'name': self.target.name, 'size': 1024}

        self.host._native_workspace_request = request
        self.host._window = SimpleNamespace(create_file_dialog=lambda *args, **kwargs: [str(self.target)])

    def tearDown(self):
        self.host._close_folders()
        self.temp.cleanup()

    def call(self):
        with patch.dict('sys.modules', {'webview': SimpleNamespace(FileDialog=SimpleNamespace(SAVE='save'))}):
            return self.host.save_workspace_report('a' * 32)

    def test_destination_comes_from_native_panel_and_save_lock_covers_both_calls(self):
        self.assertTrue(self.call()['ok'])
        self.assertEqual(self.calls, [('/api/native/reports/info', {'report_id': 'a' * 32}),
                                    ('/api/native/reports/save', {'report_id': 'a' * 32, 'path': str(self.target)})])
        self.assertFalse(self.host._lock.locked())

    def test_cancel_and_invalid_extension_never_request_a_file_write(self):
        self.host._window.create_file_dialog = lambda *args, **kwargs: None
        self.assertTrue(self.call()['cancelled'])
        self.assertEqual(len(self.calls), 1)
        self.host._window.create_file_dialog = lambda *args, **kwargs: str(self.root / 'kept.mp3')
        self.assertFalse(self.call()['ok'])
        self.assertTrue(all(route.endswith('/info') for route, _ in self.calls))
        self.assertFalse(self.host._lock.locked())

    def test_page_cannot_supply_a_destination_or_start_a_second_save(self):
        self.assertFalse(self.host.save_workspace_report({'report_id': 'a' * 32, 'path': str(self.target)})['ok'])
        self.host._lock.acquire()
        try:
            self.assertFalse(self.call()['ok'])
        finally:
            self.host._lock.release()
        self.assertFalse(self.calls)

    def test_report_safety_is_conservative_without_marking_completed_exports_as_audio(self):
        work = SimpleNamespace(safety=lambda: dict(busy=False, unsaved=False, saving=False, pending=False))
        reports = SimpleNamespace(safety=lambda: dict(busy=False, saving=False, pending=False, reports=4))
        self.assertFalse(any(combined_safety(work, reports).values()))
        reports.safety = lambda: dict(busy=True, saving=True, pending=False)
        self.assertTrue(combined_safety(work, reports)['saving'])
        self.assertFalse(combined_safety(work, reports)['unsaved'])
        reports.safety = lambda: dict(busy=0, saving=False, pending=False)
        self.assertTrue(combined_safety(work, reports)['unknown'])

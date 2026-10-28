"""Real loopback CSV authorization and lifecycle checks with fictional XML."""
import codecs
import csv
import hashlib
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

from .app import make_server, close_resources
from native_close import workspace_safety


class ReportHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='dialogue-report-http-')
        self.root = Path(self.temp.name)
        self.source, self.output = self.root / 'fictional-input', self.root / 'fictional-output'
        self.source.mkdir(); self.output.mkdir()
        for index in range(2):
            for role, suffix in [('AGENT', '0'), ('USER', '1')]:
                (self.source / f'000{index}{suffix}.xml').write_text('<analyze><subject><function><text>不应进入CSV的虚构正文。</text><time>0,1000</time></function></subject>'
                    f'<callInfo><callReference>fictional-{index}</callReference><role>{role}</role></callInfo></analyze>', encoding='utf-8')
        self.original = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in self.source.iterdir()}
        self.mp3 = self.output / 'kept.mp3'; self.mp3.write_bytes(b'untouched sentinel')
        self.server = make_server(0, state_dir=self.root / 'state', native_token='isolated-report-native')
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()
        self.base = 'http://127.0.0.1:' + str(self.server.server_address[1])
        grant = self.server.native_access.register(self.source, 'input')['folder_id']
        self.ident = self.server.workspaces.import_folder(grant)['id']
        deadline = time.monotonic() + 5
        while self.server.workspaces.summary(self.ident)['status'] == 'scanning':
            if time.monotonic() > deadline: self.fail('Fictional scan did not finish')
            time.sleep(.01)

    def tearDown(self):
        self.server.shutdown(); self.thread.join(timeout=5); self.server.server_close()
        close_resources(self.server)
        self.assertEqual(self.mp3.read_bytes(), b'untouched sentinel')
        self.assertEqual({path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in self.source.iterdir()}, self.original)
        self.temp.cleanup()

    def request(self, path, body=None, native=False):
        headers = {'Origin': self.base}
        if body is not None:
            headers.update({'Content-Type': 'application/json', 'X-Native-Token' if native else 'X-App-Token': 'isolated-report-native' if native else self.server.token})
        request = urllib.request.Request(self.base + path, data=None if body is None else json.dumps(body).encode(), headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as issue:
            return issue.code, json.load(issue)

    def create(self):
        status, value = self.request('/api/workspaces/' + self.ident + '/report', {})
        self.assertEqual(status, 200, value)
        self.assertEqual(value['rows'], 4)
        return value

    def test_full_metadata_snapshot_and_native_selected_save(self):
        report = self.create()
        self.assertEqual(report['scope'], 'whole_workspace')
        self.assertFalse(any(key in report for key in ('path', 'content', 'xml_files')))
        target = self.output / '中文清单.csv'
        status, saved = self.request('/api/native/reports/save', {'report_id': report['report_id'], 'path': str(target)}, native=True)
        self.assertEqual(status, 200); self.assertTrue(saved['ok'], saved)
        raw = target.read_bytes(); self.assertTrue(raw.startswith(codecs.BOM_UTF8))
        self.assertEqual(hashlib.sha256(raw).hexdigest(), report['sha256'])
        text = raw.decode('utf-8-sig')
        self.assertNotIn('不应进入CSV', text); self.assertNotIn(str(self.source), text)
        rows = list(csv.DictReader(io.StringIO(text)))
        self.assertEqual(len(rows), 4)
        self.assertEqual({row['源文件名'] for row in rows}, set(self.original))
        status, deleted = self.request('/api/reports/' + report['report_id'] + '/discard', {})
        self.assertEqual(status, 200); self.assertTrue(deleted['ok'])
        self.assertTrue(target.is_file())

    def test_browser_paths_filters_and_private_token_confusion_are_rejected(self):
        report = self.create(); target = self.output / 'unauthorized.csv'
        self.assertEqual(self.request('/api/native/reports/save', {'report_id': report['report_id'], 'path': str(target)})[0], 403)
        self.assertEqual(self.request('/api/workspaces/' + self.ident + '/report', {'path': str(target)})[0], 400)
        self.assertEqual(self.request('/api/workspaces/' + self.ident + '/report?q=0000', {})[0], 400)
        self.assertFalse(target.exists())
        self.assertEqual(self.server.reports.info(report['report_id'])['rows'], 4)

    def test_collision_or_non_csv_failure_preserves_snapshot_for_retry(self):
        report = self.create(); target = self.output / 'existing.csv'; target.write_bytes(b'keep csv')
        status, value = self.request('/api/native/reports/save', {'report_id': report['report_id'], 'path': str(target)}, native=True)
        self.assertEqual(status, 200); self.assertFalse(value['ok']); self.assertTrue(value['conflict'])
        self.assertEqual(target.read_bytes(), b'keep csv')
        status, value = self.request('/api/native/reports/save', {'report_id': report['report_id'], 'path': str(self.mp3)}, native=True)
        self.assertEqual(status, 200); self.assertFalse(value['ok'])
        self.assertEqual(self.server.reports.info(report['report_id'])['size'], report['size'])
        self.assertTrue(self.request('/api/native/reports/save', {'report_id': report['report_id'], 'path': str(self.output / 'retry.csv')}, native=True)[1]['ok'])

    def test_export_lease_blocks_update_and_cancel_then_idle_cache_is_cancelled(self):
        report = self.create()
        with self.server.reports.lease(report['report_id']):
            self.assertTrue(self.request('/api/workspaces/safety')[1]['pending'])
            self.assertTrue(workspace_safety(self.server)['pending'])
            self.assertEqual(self.request('/api/update/lock', {})[0], 409)
            self.assertEqual(self.request('/api/reports/cancel-pending', {})[0], 400)
        self.assertFalse(self.request('/api/workspaces/safety')[1]['unsaved'])
        status, result = self.request('/api/reports/cancel-pending', {})
        self.assertEqual(status, 200); self.assertEqual(result, {'ok': True, 'cancelled': 1})
        self.assertEqual(self.server.workspaces.summary(self.ident)['total'], 2)

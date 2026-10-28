"""Real SQLite issue counts, bounded navigation, and fixed selection membership."""
from contextlib import contextmanager
import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

from native_workspace import NativeWorkspaceAccess
from .app import make_server
from .normalize import InputError
from .workspace_issues import ISSUE_KEYS, issue_counts
from .workspaces import DEFAULT_SETTINGS, Workspaces


class OfflineJobs:
    batch_active = None
    items = {}


def seed_workspace(work, access, root):
    source = root / 'input'; source.mkdir()
    grant = access.register(source, 'input')
    ident = 'fictional-issues'
    with work.db() as db:
        db.execute('INSERT INTO workspaces(id,status,phase,input_id,input_path,settings,selection_id,created) VALUES(?,?,?,?,?,?,?,0)',
                   (ident, 'ready', 'ready', grant['folder_id'], str(source), json.dumps(DEFAULT_SETTINGS), 'initial-selection'))
    return ident


def insert_items(db, ident, rows):
    db.executemany('INSERT INTO items(wid,id,filename,status,error_code,source_name) VALUES(?,?,?,?,?,?)',
                   ((ident, item_id, filename, status, code, source) for item_id, filename, status, code, source in rows))


class WorkspaceIssueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.state = self.root / 'state'
        self.access = NativeWorkspaceAccess(self.state)
        self.work = Workspaces(self.state, OfflineJobs(), self.access)
        self.ident = seed_workspace(self.work, self.access, self.root)

    def tearDown(self):
        self.work.close(); self.access.close(); self.temp.cleanup()

    def test_counts_use_real_codes_and_exclude_pending_audio_and_workspace_phases(self):
        categories = {
            'pairing': ('invalid', ['missing_track', 'duplicate_track']),
            'input': ('invalid', ['invalid_xml', 'invalid_content', 'invalid_filename']),
            'changed': ('changed', ['input_changed']),
            'conflict': ('conflict', ['duplicate_filename', 'output_conflict']),
            'synthesis': ('synthesis_error', ['synthesis_failed', 'missing_cache']),
            'save': ('save_error', ['save_failed', 'cache_changed']),
            'other': ('invalid', ['future_error_code', None]),
        }
        with self.work.db() as db:
            insert_items(db, self.ident, ((category + str(index), f'{category}-{index}.mp3', status, code, 'fictional.xml')
                                         for category, (status, codes) in categories.items() for index, code in enumerate(codes)))
            insert_items(db, self.ident, [('ready', 'ready.mp3', 'ready', 'future_error_code', None), ('pending', 'pending.mp3', 'save_pending', 'save_failed', None), ('saving', 'saving.mp3', 'saving', None, None)])
            db.execute("UPDATE workspaces SET phase='directory_error' WHERE id=?", (self.ident,))
        expected = {'total': 14, **{key: len(value[1]) for key, value in categories.items()}}
        with patch.object(self.access, 'read_file', side_effect=AssertionError('Issue browsing must not read XML')):
            summary = self.work.summary(self.ident)
            page = self.work.page(self.ident, filter='error', issue='pairing', page=2, limit=1)
        self.assertEqual(summary['issues'], expected)
        self.assertEqual(summary['error'], expected['total'])
        self.assertEqual(summary['save_pending'], 1)
        self.assertEqual(summary['phase'], 'directory_error')
        self.assertEqual(page['issues'], expected)  # global, not the current page
        self.assertEqual(page['matched_total'], 2)
        self.assertEqual(len(page['items']), 1)
        self.assertEqual(page['items'][0]['error_code'], 'duplicate_track')

    def test_sixty_thousand_rows_are_aggregated_in_sql_and_pages_stay_bounded(self):
        variants = [('invalid', 'missing_track'), ('invalid', 'invalid_xml'), ('changed', 'input_changed'), ('conflict', 'output_conflict'), ('synthesis_error', 'synthesis_failed'), ('save_error', 'save_failed'), ('invalid', 'future_code')]
        with self.work.db() as db:
            insert_items(db, self.ident, ((f'large-{index:06d}', f'{index:06d}.mp3', *variants[index % 7], None) for index in range(60000)))
            steps = []
            db.set_progress_handler(lambda: steps.append(1) or 0, 1000)
            counts = issue_counts(db, self.ident)
            db.set_progress_handler(None, 0)
        expected = {'total': 60000, **{key: 60000 // 7 + (index < 60000 % 7) for index, key in enumerate(ISSUE_KEYS)}}
        self.assertEqual(counts, expected)
        self.assertLess(len(steps), 10000)  # bounded linear SQL work, not N queries
        with patch.object(self.access, 'read_file', side_effect=AssertionError('No XML body reads')):
            page = self.work.page(self.ident, filter='error', issue='save', page=2)
        self.assertEqual(page['issues'], expected)
        self.assertEqual(page['matched_total'], expected['save'])
        self.assertEqual(len(page['items']), 100)
        self.assertTrue(all(item['error_code'] == 'save_failed' for item in page['items']))
        self.assertLess(len(json.dumps(page)), 128 * 1024)
        next_item = self.work.next_issue(self.ident, issue='save', after_id='large-000698')
        self.assertEqual((next_item['item_id'], next_item['page'], next_item['matched_total']), ('large-000705', 2, expected['save']))

    def test_next_issue_seeks_current_query_wraps_and_reports_the_actual_page(self):
        with self.work.db() as db:
            insert_items(db, self.ident, ((f'item-{index:03d}', f'issue-{index:03d}.mp3', 'invalid', 'invalid_xml', None) for index in range(205)))
            insert_items(db, self.ident, [('null-a', None, 'invalid', 'invalid_xml', 'unreadable-a.xml'), ('null-b', None, 'invalid', 'invalid_xml', 'unreadable-b.xml'), ('ready', 'issue-050z.mp3', 'ready', None, None)])
        traces = []
        original_db = self.work.db
        @contextmanager
        def observed_db():
            with original_db() as db:
                db.set_trace_callback(traces.append)
                yield db
        with patch.object(self.access, 'read_file', side_effect=AssertionError('No XML body reads')), patch.object(self.work, 'db', observed_db):
            first = self.work.next_issue(self.ident, issue='input')
        self.assertEqual((first['item_id'], first['page'], first['wrapped'], first['matched_total']), ('item-000', 1, False, 207))
        targets = [query for query in traces if query.startswith('SELECT id,filename')]
        self.assertTrue(targets)
        self.assertTrue(all(query.endswith('LIMIT 1') for query in targets))
        self.assertNotIn('items', first)
        self.assertLess(len(json.dumps(first)), 1024)
        second = self.work.next_issue(self.ident, issue='input', after_id='item-099')
        self.assertEqual((second['item_id'], second['page']), ('item-100', 2))
        self.assertEqual(self.work.next_issue(self.ident, issue='input', after_id='ready')['item_id'], 'item-051')
        null_first = self.work.next_issue(self.ident, issue='input', after_id='item-204')
        self.assertEqual((null_first['item_id'], null_first['page']), ('null-a', 3))
        self.assertEqual(self.work.next_issue(self.ident, issue='input', after_id='null-a')['item_id'], 'null-b')
        wrapped = self.work.next_issue(self.ident, issue='input', after_id='null-b')
        self.assertEqual((wrapped['item_id'], wrapped['page'], wrapped['wrapped']), ('item-000', 1, True))
        searched = self.work.next_issue(self.ident, q='issue-1', issue='input', after_id='item-199')
        self.assertEqual((searched['item_id'], searched['matched_total'], searched['wrapped']), ('item-100', 100, True))
        empty = self.work.next_issue(self.ident, issue='save')
        self.assertEqual((empty['item_id'], empty['matched_total'], empty['wrapped']), (None, 0, False))

    def test_selection_keeps_fixed_members_and_last_operation_query_after_reopen(self):
        with self.work.db() as db:
            insert_items(db, self.ident, ((f'item-{index:03d}', f'entry-{index:03d}.mp3', 'ready', None, None) for index in range(205)))
            insert_items(db, self.ident, [('invalid', None, 'invalid', 'invalid_xml', 'broken.xml')])
        first = self.work.select(self.ident, {'mode': 'all', 'q': 'entry-', 'filter': 'ready', 'issue': ''})
        self.assertEqual(first['count'], 205)
        page_two = self.work.page(self.ident, q='entry-', filter='ready', page=2)
        cancelled = self.work.select(self.ident, {'mode': 'page', 'item_ids': [row['id'] for row in page_two['items']], 'selected': False, 'q': 'entry-', 'filter': 'ready', 'issue': 'all'})
        self.assertEqual(cancelled['count'], 105)
        category = self.work.select(self.ident, {'mode': 'all', 'filter': 'error', 'issue': 'input'})
        self.assertEqual((category['count'], category['hidden_count']), (105, 105))
        self.assertEqual(category['selection_query'], {'q': '', 'filter': 'error', 'issue': 'input'})
        self.work.select(self.ident, {'mode': 'clear'})
        selected = self.work.select(self.ident, {'mode': 'all', 'q': 'entry-0', 'filter': 'ready', 'issue': ''})
        with self.work.db() as db:
            db.execute("UPDATE items SET status='changed',error_code='input_changed' WHERE id='item-000'")
            insert_items(db, self.ident, [('late-member', 'entry-0999.mp3', 'ready', None, None)])
        self.work.close()
        self.work = Workspaces(self.state, OfflineJobs(), self.access)
        summary = self.work.summary(self.ident)
        self.assertEqual(summary['selected'], 100)
        self.assertEqual(summary['ready_selected'], 99)
        self.assertEqual(summary['selection_id'], selected['selection_id'])
        self.assertEqual(summary['selection_query'], {'q': 'entry-0', 'filter': 'ready', 'issue': ''})
        with self.work.db() as db:
            self.assertEqual(db.execute("SELECT selected FROM items WHERE id='item-000'").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT selected FROM items WHERE id='late-member'").fetchone()[0], 0)
        # An explicit ID cannot escape the operation's issue condition.
        outside = self.work.select(self.ident, {'mode': 'toggle', 'item_id': 'late-member', 'selected': True, 'issue': 'input'})
        self.assertEqual(outside['count'], 100)

    def test_unknown_issue_and_oversized_queries_fail_without_changing_selection(self):
        for issue in ('unknown', "input' OR 1=1", None, [], 12):
            with self.subTest(issue=issue):
                with self.assertRaises(InputError): self.work.page(self.ident, issue=issue)
                with self.assertRaises(InputError): self.work.select(self.ident, {'mode': 'all', 'issue': issue})
                with self.assertRaises(InputError): self.work.next_issue(self.ident, issue=issue)
        for operation in (lambda: self.work.page(self.ident, q='x' * 256), lambda: self.work.next_issue(self.ident, q='x' * 256), lambda: self.work.next_issue(self.ident, limit=99), lambda: self.work.next_issue(self.ident, after_id='x' * 129), lambda: self.work.select(self.ident, {'mode': 'all', 'unexpected': True})):
            with self.assertRaises(InputError): operation()
        self.assertEqual(self.work.summary(self.ident)['selection_id'], 'initial-selection')


class WorkspaceIssueHTTPTests(unittest.TestCase):
    def test_issue_filters_selection_and_next_route_reject_unknown_parameters(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            server = make_server(0, state_dir=root / 'state', native_token='fictional-issue-token')
            ident = seed_workspace(server.workspaces, server.native_access, root)
            with server.workspaces.db() as db:
                insert_items(db, ident, ((f'item-{index:03d}', f'issue-{index:03d}.mp3', 'save_error', 'save_failed', None) for index in range(220)))
            thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
            base = 'http://127.0.0.1:' + str(server.server_port) + '/api/workspaces/' + ident
            def request(path, body=None):
                data = json.dumps(body).encode() if body is not None else None
                req = urllib.request.Request(base + path, data=data, headers={'X-App-Token': server.token, 'Content-Type': 'application/json'})
                try: response = urllib.request.urlopen(req, timeout=5)
                except urllib.error.HTTPError as error: response = error
                with response: return response.status, json.load(response)
            try:
                status, page = request('?issue=save&filter=error&page=2')
                self.assertEqual(status, 200)
                self.assertEqual((len(page['items']), page['matched_total'], page['issues']['save']), (100, 220, 220))
                status, next_item = request('/next-issue?issue=save&after_id=item-099')
                self.assertEqual(status, 200)
                self.assertEqual((next_item['item_id'], next_item['page']), ('item-100', 2))
                status, selection = request('/selection', {'mode': 'all', 'issue': 'save', 'filter': 'error'})
                self.assertEqual(status, 200)
                self.assertEqual(selection['selection_query']['issue'], 'save')
                self.assertEqual(selection['count'], 0)  # invalid rows are not generatable
                for path in ('?issue=unknown', '?bogus=', '?issue=save&issue=input', '?q=' + 'x' * 256, '/next-issue?limit=101', '/next-issue?unexpected=true', '/next-issue?issue=unknown'):
                    with self.subTest(path=path): self.assertEqual(request(path)[0], 400)
                self.assertEqual(request('/selection', {'mode': 'all', 'issue': 'unknown'})[0], 400)
            finally:
                server.shutdown(); server.server_close(); server.workspaces.close(); server.jobs.close(); server.native_access.close(); thread.join(3)

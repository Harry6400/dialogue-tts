"""Real CSV files and real SQLite snapshots; no XML reads or online synthesis."""
import codecs
import csv
import hashlib
import io
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import tracemalloc
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from native_reports import NativeReportDestination, save_report_to_destination
from .workspace_reports import HEADERS, ReportBusy, ReportError, WorkspaceReports, display_cell


class WorkspaceReportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.index = self.root / 'index.sqlite3'
        self.cache = self.root / 'reports'
        self.output = self.root / 'selected-output'
        self.output.mkdir()
        self.wid = 'a' * 32
        self.db = sqlite3.connect(self.index)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''
          CREATE TABLE workspaces(id TEXT PRIMARY KEY,status TEXT,phase TEXT,revision INTEGER,input_path TEXT);
          CREATE TABLE items(wid TEXT,id TEXT PRIMARY KEY,identity TEXT,filename TEXT,status TEXT,error TEXT,error_code TEXT,source_name TEXT);
          CREATE INDEX item_display ON items(wid,(filename IS NULL),filename,id);
          CREATE TABLE files(wid TEXT,name TEXT,identity TEXT,role TEXT,digest TEXT,error TEXT,PRIMARY KEY(wid,name));
          CREATE INDEX file_pair ON files(wid,identity,name,digest,role);
        ''')
        self.db.execute('INSERT INTO workspaces VALUES(?,?,?,?,?)', (self.wid, 'ready', 'ready', 7, '/not-exported/private-source'))
        self.db.commit()
        self.reports = WorkspaceReports(self.index, self.cache)
        self.original_xml = self.root / 'private.xml'
        self.original_xml.write_text('<original>private dialogue must never appear</original>')
        self.original_mp3 = self.root / 'existing.mp3'
        self.original_mp3.write_bytes(b'original saved MP3 fixture')

    def tearDown(self):
        self.reports.close()
        self.db.close()
        self.temporary.cleanup()

    def add(self, number=1, *, name='0000010.xml', identity='pair-one', status='ready', error=None, code=None, filename='000001.mp3', role='AGENT'):
        ident = f'{number:032x}'
        self.db.execute('INSERT INTO items VALUES(?,?,?,?,?,?,?,?)', (self.wid, ident, identity, filename, status, error, code, name))
        self.db.execute('INSERT INTO files VALUES(?,?,?,?,?,?)', (self.wid, name, identity, role, 'b' * 64, error if identity is None else None))
        self.db.commit()
        return ident

    def rows(self, report_id):
        with self.reports.lease(report_id) as (handle, _):
            return list(csv.reader(io.StringIO(handle.read().decode('utf-8-sig'), newline='')))

    def test_whole_snapshot_contains_each_source_and_never_dialogue_or_paths(self):
        first = self.add()
        self.db.execute('INSERT INTO files VALUES(?,?,?,?,?,?)', (self.wid, '0000011.xml', 'pair-one', 'USER', 'c' * 64, None))
        self.add(2, name='bad.xml', identity=None, status='invalid', error='虚构坏项', code='invalid_xml', filename=None, role=None)
        result = self.reports.create(self.wid)
        self.assertEqual((result['revision'], result['rows'], result['items'], result['sources']), (7, 3, 2, 3))
        self.assertEqual(result['scope'], 'whole_workspace')
        self.assertNotIn('path', result)
        data = self.rows(result['report_id'])
        self.assertEqual(tuple(data[0]), HEADERS)
        self.assertEqual({row[3] for row in data[1:]}, {'0000010.xml', '0000011.xml', 'bad.xml'})
        self.assertEqual(sum(row[1] == '文本：' + first for row in data[1:]), 2)
        self.assertEqual({row[4] for row in data[1:]}, {'AGENT（客服）', 'USER（客户）', '未识别'})
        self.assertNotIn('private dialogue', str(data))
        self.assertNotIn('/not-exported/private-source', str(data))
        with self.assertRaises(TypeError):
            self.reports.create(self.wid, q='bad')

    def test_csv_bom_quotes_newlines_hazardous_cells_and_long_numeric_ids(self):
        name = '=HYPERLINK("fictional");,\r\n较长中文来源' + '名称' * 30 + '.xml'
        self.add(name=name, identity=None, status='invalid', code='invalid_xml', error='错误说明含逗号, 双引号"与\r\n换行')
        report = self.reports.create(self.wid)
        with self.reports.lease(report['report_id']) as (handle, manifest):
            raw = handle.read()
        self.assertTrue(raw.startswith(codecs.BOM_UTF8 + '"批次编号"'.encode()))
        self.assertTrue(raw.endswith(b'\r\n'))
        self.assertIn(b'""fictional""', raw)
        self.assertEqual(len(raw), manifest['size'])
        self.assertEqual(hashlib.sha256(raw).hexdigest(), manifest['sha256'])
        row = self.rows(report['report_id'])[1]
        self.assertEqual(row[2], '文本：000001')
        self.assertEqual(row[3], '文本：' + name)
        self.assertEqual(row[5], '000001.mp3')
        self.assertEqual(row[8], '错误说明含逗号, 双引号"与\r\n换行')
        for value in ['=1+1', '+CMD', '-1+2', '@SUM(1)', '＝1+1', '＋1', '－1', '＠SUM(1)', '\t=1', '\r=1', '\n=1', ' \t=1', '\ufeff=1', '\u200b=1', '000123', '1234567890123456']:
            self.assertEqual(display_cell(value), '文本：' + value)
        for value in ['normal.xml', '000001.mp3', '12345', '没有公式', '', None]:
            self.assertEqual(display_cell(value), '' if value is None else value)

    def test_empty_complete_workspace_and_schema_extra_fields_are_supported(self):
        self.db.execute('PRAGMA user_version=42')
        self.db.execute('ALTER TABLE workspaces ADD COLUMN future_field TEXT')
        self.db.commit()
        report = self.reports.create(self.wid)
        self.assertEqual((report['rows'], report['items'], report['sources']), (0, 0, 0))
        self.assertEqual(self.rows(report['report_id']), [list(HEADERS)])
        connection = self.reports._connect()
        try:
            self.assertEqual(connection.execute('PRAGMA query_only').fetchone()[0], 1)
            with self.assertRaises(sqlite3.OperationalError):
                connection.execute('UPDATE workspaces SET revision=8')
        finally:
            connection.close()
        self.assertEqual(self.db.execute('SELECT revision FROM workspaces').fetchone()[0], 7)

    def test_partial_scan_cancelled_recovery_and_finished_indexes_are_not_complete_exports(self):
        for status, phase in [('scanning', 'reading'), ('cancelled', 'cancelled'), ('interrupted', 'recovery_scan'), ('error', 'scan_error'), ('done', 'finished')]:
            with self.subTest(status=status, phase=phase):
                self.db.execute('UPDATE workspaces SET status=?,phase=?', (status, phase))
                self.db.commit()
                with self.assertRaises(ReportError):
                    self.reports.create(self.wid)
                self.assertEqual(self.reports.safety()['reports'], 0)
                self.assertEqual(list(self.reports.root.iterdir()), [])

    def test_older_optional_source_name_is_compatible_when_identity_is_known(self):
        self.add()
        self.db.execute('ALTER TABLE items DROP COLUMN source_name')
        self.db.commit()
        report = self.reports.create(self.wid)
        self.assertEqual(self.rows(report['report_id'])[1][3], '0000010.xml')

    def test_snapshot_is_fixed_while_generation_updates_continue(self):
        self.add()
        self.db.execute("UPDATE workspaces SET status='running',phase='generating'")
        self.db.commit()
        original_writer = csv.writer
        changed = False
        owner = self
        class Writer:
            def __init__(self, *args, **kwargs):
                self.delegate = original_writer(*args, **kwargs)
            def writerow(self, values):
                nonlocal changed
                self.delegate.writerow(values)
                if not changed:
                    changed = True
                    owner.db.execute("UPDATE items SET status='saved'")
                    owner.db.execute('UPDATE workspaces SET revision=8')
                    owner.db.commit()
        with patch('web_dialogue.workspace_reports.csv.writer', Writer):
            report = self.reports.create(self.wid)
        self.assertEqual(report['revision'], 7)
        self.assertEqual(self.rows(report['report_id'])[1][6], '待生成')
        self.assertEqual(self.db.execute('SELECT revision FROM workspaces').fetchone()[0], 8)

    def test_sixty_thousand_rows_are_real_and_python_working_set_is_bounded(self):
        calls = 30000
        self.db.executemany('INSERT INTO items VALUES(?,?,?,?,?,?,?,?)',
                            ((self.wid, f'{i:032x}', 'pair-' + str(i), f'{i:09d}.mp3', 'ready', None, None, f'{i:09d}0.xml') for i in range(calls)))
        self.db.executemany('INSERT INTO files VALUES(?,?,?,?,?,?)',
                            ((self.wid, f'{i:09d}{role}.xml', 'pair-' + str(i), 'AGENT' if role == 0 else 'USER', 'b' * 64, None) for i in range(calls) for role in (0, 1)))
        self.db.commit()
        tracemalloc.start()
        try:
            report = self.reports.create(self.wid)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertEqual((report['rows'], report['items'], report['sources']), (60000, 30000, 60000))
        self.assertLess(peak, 4 * 1024 * 1024)
        with self.reports.lease(report['report_id']) as (handle, _):
            with io.TextIOWrapper(handle, encoding='utf-8-sig', newline='') as text:
                reader = csv.reader(text)
                self.assertEqual(next(reader), list(HEADERS))
                self.assertEqual(sum(1 for _ in reader), 60000)

    def test_sixty_thousand_duplicate_sources_are_not_aggregated_or_truncated(self):
        self.db.execute('INSERT INTO items VALUES(?,?,?,?,?,?,?,?)', (self.wid, 'b' * 32, 'duplicate', None, 'invalid', '重复声道', 'duplicate_track', '000000.xml'))
        self.db.executemany('INSERT INTO files VALUES(?,?,?,?,?,?)', ((self.wid, f'{i:06d}.xml', 'duplicate', 'AGENT', 'c' * 64, None) for i in range(60000)))
        self.db.commit()
        report = self.reports.create(self.wid)
        self.assertEqual((report['rows'], report['items'], report['sources']), (60000, 1, 60000))
        with self.reports.lease(report['report_id']) as (handle, _):
            with io.TextIOWrapper(handle, encoding='utf-8-sig', newline='') as text:
                reader = csv.reader(text)
                next(reader)
                count, last = 0, None
                for row in reader:
                    self.assertEqual(len(row), len(HEADERS))
                    self.assertEqual(row[7], 'duplicate_track')
                    count, last = count + 1, row
                self.assertEqual(count, 60000)
                self.assertEqual(last[3], '059999.xml')
        plan = self.db.execute('EXPLAIN QUERY PLAN SELECT name,role,error FROM files INDEXED BY file_pair WHERE wid=? AND identity=? ORDER BY name', (self.wid, 'duplicate')).fetchall()
        self.assertTrue(any('SEARCH files USING INDEX file_pair' in row[3] for row in plan), plan)
        self.assertFalse(any('SCAN ' in row[3] or 'TEMP B-TREE' in row[3] for row in plan), plan)

    def test_missing_source_associations_fail_without_silent_omission(self):
        self.add()
        self.db.execute('INSERT INTO files VALUES(?,?,?,?,?,?)', (self.wid, 'orphan.xml', 'not-indexed', 'USER', 'c' * 64, None))
        self.db.commit()
        with self.assertRaises(ReportError):
            self.reports.create(self.wid)
        self.assertEqual(list(self.reports.root.iterdir()), [])

    def test_limit_creation_failure_and_discard_do_not_touch_other_files(self):
        self.add()
        foreign = self.cache / 'unknown.mp3'
        foreign.write_bytes(b'not owned by reports')
        reports = [self.reports.create(self.wid) for _ in range(4)]
        with self.assertRaises(ReportBusy):
            self.reports.create(self.wid)
        self.assertEqual(self.reports.safety()['reports'], 4)
        self.reports.discard(reports[0]['report_id'])
        with self.assertRaises(KeyError):
            self.reports.info(reports[0]['report_id'])
        self.assertEqual(self.reports.create(self.wid)['rows'], 1)
        self.assertEqual(foreign.read_bytes(), b'not owned by reports')
        self.assertEqual(self.original_xml.read_text(), '<original>private dialogue must never appear</original>')
        self.assertEqual(self.original_mp3.read_bytes(), b'original saved MP3 fixture')

    def test_io_or_byte_limit_failure_never_publishes_a_partial_report(self):
        self.add()
        with patch.object(self.reports, '_snapshot', side_effect=OSError('fictional disk full')):
            with self.assertRaises(OSError):
                self.reports.create(self.wid)
        self.assertEqual(list(self.reports.root.iterdir()), [])
        limited = WorkspaceReports(self.index, self.cache, max_bytes=20)
        try:
            with self.assertRaises(ReportError):
                limited.create(self.wid)
            self.assertEqual(list(limited.root.iterdir()), [])
            self.assertEqual(limited.safety()['reports'], 0)
        finally:
            limited.close()

    def test_schema_read_failure_is_actionable_and_does_not_leave_a_false_report(self):
        self.db.execute('DROP TABLE items')
        self.db.commit()
        with self.assertRaisesRegex(ReportError, '本机任务索引暂不可读'):
            self.reports.create(self.wid)
        self.assertEqual(self.reports.safety()['reports'], 0)
        self.assertEqual(list(self.reports.root.iterdir()), [])

    def test_read_lease_blocks_discard_and_close_and_detects_tampering(self):
        self.add()
        report = self.reports.create(self.wid)
        ident = report['report_id']
        with self.reports.lease(ident):
            self.assertTrue(self.reports.safety()['pending'])
            with self.assertRaises(ReportBusy): self.reports.discard(ident)
            with self.assertRaises(ReportBusy): self.reports.cancel_pending()
            with self.assertRaises(ReportBusy): self.reports.close()
        self.assertFalse(self.reports.safety()['busy'])
        path = self.reports.root / (ident + '.csv')
        data = path.read_bytes()
        path.write_bytes(data[:-1] + b'X')
        with self.assertRaises(ReportError):
            with self.reports.lease(ident): self.fail('tampered report must not be leased')
        self.assertFalse(self.reports.safety()['busy'])
        self.reports.discard(ident)

    def test_building_and_saving_are_visible_and_concurrent_cleanup_is_rejected(self):
        self.add()
        entered, release = threading.Event(), threading.Event()
        original = self.reports._snapshot
        outcome = []
        def slow_snapshot(*args):
            entered.set()
            if not release.wait(5): raise AssertionError('test did not release snapshot')
            return original(*args)
        with patch.object(self.reports, '_snapshot', side_effect=slow_snapshot):
            worker = threading.Thread(target=lambda: outcome.append(self.reports.create(self.wid)))
            worker.start()
            self.assertTrue(entered.wait(5))
            try:
                ident = next(iter(self.reports._reports))
                self.assertTrue(self.reports.safety()['pending'])
                with self.assertRaises(ReportBusy): self.reports.info(ident)
                with self.assertRaises(ReportBusy): self.reports.discard(ident)
                with self.assertRaises(ReportBusy): self.reports.cancel_pending()
                with self.assertRaises(ReportBusy): self.reports.close()
            finally:
                release.set(); worker.join(5)
        self.assertFalse(worker.is_alive())
        report = outcome[0]
        entered.clear(); release.clear(); outcome.clear()
        destination = NativeReportDestination.from_native_selection(self.output / 'saved.csv')
        def slow_save(*args):
            entered.set()
            if not release.wait(5): raise AssertionError('test did not release save')
            return save_report_to_destination(*args)
        with patch('web_dialogue.workspace_reports.save_report_to_destination', side_effect=slow_save):
            worker = threading.Thread(target=lambda: outcome.append(self.reports.save_to_native_destination(report['report_id'], destination)))
            worker.start()
            self.assertTrue(entered.wait(5))
            try:
                self.assertTrue(self.reports.safety()['saving'])
                with self.assertRaises(ReportBusy): self.reports.discard(report['report_id'])
                with self.assertRaises(ReportBusy): self.reports.cancel_pending()
                with self.assertRaises(ReportBusy): self.reports.close()
                with self.assertRaises(ReportBusy): self.reports.save_to_native_destination(report['report_id'], destination)
            finally:
                release.set(); worker.join(5)
        self.assertTrue(outcome[0]['ok'])
        self.assertFalse(self.reports.safety()['busy'])

    def test_native_save_success_conflict_failure_retry_and_separate_receipt(self):
        self.add()
        report = self.reports.create(self.wid)
        ident = report['report_id']
        destination = NativeReportDestination.from_native_selection(self.output / '完整清单.csv')
        receipt = self.reports.save_to_native_destination(ident, destination)
        target = self.output / '完整清单.csv'
        self.assertTrue(receipt['ok'])
        self.assertEqual(receipt['size'], target.stat().st_size)
        self.assertEqual(receipt['sha256'], hashlib.sha256(target.read_bytes()).hexdigest())
        self.assertNotIn('fingerprint', receipt)
        self.assertEqual(self.reports.info(ident)['report_id'], ident)
        with self.assertRaises(FileExistsError):
            self.reports.save_to_native_destination(ident, destination)
        failing = NativeReportDestination.from_native_selection(self.output / 'retry.csv')
        with patch('native_reports.os.fsync', side_effect=OSError('fictional disk full')):
            with self.assertRaises(OSError):
                self.reports.save_to_native_destination(ident, failing)
        self.assertFalse((self.output / 'retry.csv').exists())
        self.assertFalse(list(self.output.glob('.dialogue-report-*.tmp')))
        self.assertEqual(self.reports.info(ident)['report_id'], ident)
        self.assertTrue(self.reports.save_to_native_destination(ident, failing)['ok'])
        with self.assertRaises(ReportError):
            self.reports.save_to_native_destination(ident, str(self.output / 'not-native.csv'))
        self.reports.discard(ident)
        self.assertTrue(target.is_file())
        self.assertEqual(self.original_mp3.read_bytes(), b'original saved MP3 fixture')

    def test_native_destination_rejects_links_wrong_extension_and_parent_replacement(self):
        self.add()
        report = self.reports.create(self.wid)
        for target in [Path('relative.csv'), self.output / 'bad.mp3', self.output / 'wrong.csv.exe']:
            with self.assertRaises(ValueError): NativeReportDestination.from_native_selection(target)
        chosen = NativeReportDestination.from_native_selection(self.output / 'chosen.csv')
        old = self.root / 'previous-output'
        self.output.rename(old)
        self.output.mkdir()
        with self.assertRaises(ValueError):
            self.reports.save_to_native_destination(report['report_id'], chosen)
        self.assertFalse((self.output / 'chosen.csv').exists())
        if os.name != 'nt':
            (self.output / 'linked.csv').symlink_to(self.original_mp3)
            with self.assertRaises(FileExistsError): NativeReportDestination.from_native_selection(self.output / 'linked.csv')
            (self.root / 'linked-parent').symlink_to(self.output, target_is_directory=True)
            with self.assertRaises(ValueError): NativeReportDestination.from_native_selection(self.root / 'linked-parent/report.csv')
        self.assertEqual(self.original_mp3.read_bytes(), b'original saved MP3 fixture')

    def test_preexisting_temporary_file_is_never_deleted_on_exclusive_create_failure(self):
        self.add()
        report = self.reports.create(self.wid)
        destination = NativeReportDestination.from_native_selection(self.output / 'report.csv')
        foreign = self.output / ('.dialogue-report-' + 'd' * 32 + '.tmp')
        foreign.write_bytes(b'preexisting unrelated temporary file')
        with patch('native_reports.uuid.uuid4', return_value=SimpleNamespace(hex='d' * 32)):
            with self.assertRaises(FileExistsError):
                self.reports.save_to_native_destination(report['report_id'], destination)
        self.assertEqual(foreign.read_bytes(), b'preexisting unrelated temporary file')
        self.assertFalse((self.output / 'report.csv').exists())
        self.assertEqual(self.reports.info(report['report_id'])['report_id'], report['report_id'])

    def test_close_cleans_only_own_report_session_and_is_idempotent(self):
        self.add()
        self.reports.create(self.wid)
        own_root = self.reports.root
        other = WorkspaceReports(self.index, self.cache)
        try:
            other_report = other.create(self.wid)
            self.reports.close(); self.reports.close()
            self.assertFalse(own_root.exists())
            self.assertEqual(other.info(other_report['report_id'])['rows'], 1)
            self.assertTrue(self.original_xml.exists())
            self.assertTrue(self.original_mp3.exists())
        finally:
            other.close()

    def test_failed_creation_cleanup_remains_counted_but_is_not_a_permanent_builder(self):
        self.add()
        limited = WorkspaceReports(self.index, self.cache, max_pending=1)
        original_unlink = Path.unlink
        def cannot_remove_temporary(path, *args, **kwargs):
            if path.parent == limited.root and path.name.endswith('.csv.tmp'):
                raise PermissionError('fictional temporary file sharing lock')
            return original_unlink(path, *args, **kwargs)
        def failed_snapshot(_workspace_id, output):
            output.write(b'fictional partially written CSV')
            raise OSError('fictional disk failure')
        try:
            with patch.object(limited, '_snapshot', side_effect=failed_snapshot), patch.object(Path, 'unlink', cannot_remove_temporary):
                with self.assertRaisesRegex(ReportError, '临时 CSV 未能清理'):
                    limited.create(self.wid)
                state = limited.safety()
                self.assertEqual((state['reports'], state['cleanup_pending']), (1, 1))
                self.assertFalse(state['busy'])
                self.assertFalse(state['pending'])
                ident = next(iter(limited._reports))
                with self.assertRaisesRegex(ReportError, '生成未完成'):
                    limited.info(ident)
                with self.assertRaises(ReportBusy): limited.create(self.wid)
                with self.assertRaises(ReportError): limited.discard(ident)
                with self.assertRaises(ReportError): limited.cancel_pending()
                with self.assertRaises(ReportError): limited.close()
                self.assertEqual(len(list(limited.root.iterdir())), 1)
            self.assertEqual(limited.cancel_pending(), {'ok': True, 'cancelled': 1})
            self.assertEqual(limited.safety()['reports'], 0)
            self.assertEqual(list(limited.root.iterdir()), [])
            self.assertEqual(limited.create(self.wid)['rows'], 1)
        finally:
            limited.close()

    def test_cancel_pending_retries_partial_cleanup_and_preserves_saved_and_unrelated_files(self):
        self.add()
        first, second, third = [self.reports.create(self.wid) for _ in range(3)]
        destination = NativeReportDestination.from_native_selection(self.output / 'keep.csv')
        self.reports.save_to_native_destination(first['report_id'], destination)
        saved = (self.output / 'keep.csv').read_bytes()
        blocked = self.reports.root / (second['report_id'] + '.csv')
        original_unlink = Path.unlink
        def cannot_remove_one(path, *args, **kwargs):
            if path == blocked:
                raise PermissionError('fictional locked report')
            return original_unlink(path, *args, **kwargs)
        with patch.object(Path, 'unlink', cannot_remove_one):
            with self.assertRaisesRegex(ReportError, '已取消 2 份'):
                self.reports.cancel_pending()
        self.assertEqual(self.reports.safety()['reports'], 1)
        self.assertEqual(self.reports.safety()['cleanup_pending'], 1)
        self.assertFalse(self.reports.safety()['busy'])
        for removed in (first, third):
            with self.assertRaises(KeyError): self.reports.info(removed['report_id'])
        self.assertEqual(self.reports.info(second['report_id'])['report_id'], second['report_id'])
        self.assertEqual(self.reports.cancel_pending(), {'ok': True, 'cancelled': 1})
        self.assertEqual(self.reports.cancel_pending(), {'ok': True, 'cancelled': 0})
        self.assertEqual((self.output / 'keep.csv').read_bytes(), saved)
        self.assertEqual(self.original_xml.read_text(), '<original>private dialogue must never appear</original>')
        self.assertEqual(self.original_mp3.read_bytes(), b'original saved MP3 fixture')

    def test_cancel_pending_is_atomic_while_any_report_has_a_lease(self):
        self.add()
        first, second = [self.reports.create(self.wid) for _ in range(2)]
        with self.reports.lease(first['report_id']):
            with self.assertRaises(ReportBusy): self.reports.cancel_pending()
            self.assertEqual(self.reports.info(second['report_id'])['rows'], 1)
            self.assertEqual(self.reports.safety()['reports'], 2)
        self.assertEqual(self.reports.cancel_pending(), {'ok': True, 'cancelled': 2})

    def test_replaced_owned_path_is_never_deleted_and_cleanup_can_be_retried(self):
        self.add()
        report = self.reports.create(self.wid)
        path = self.reports.root / (report['report_id'] + '.csv')
        original = self.root / 'original-report-inode'
        path.rename(original)
        path.write_bytes(b'unrelated replacement file')
        with self.assertRaises(ReportError): self.reports.cancel_pending()
        self.assertEqual(path.read_bytes(), b'unrelated replacement file')
        self.assertEqual(self.reports.safety()['reports'], 1)
        path.unlink()
        original.rename(path)
        self.assertEqual(self.reports.cancel_pending(), {'ok': True, 'cancelled': 1})

    def test_close_does_not_recursively_delete_unknown_session_files(self):
        self.add()
        self.reports.create(self.wid)
        foreign = self.reports.root / 'unrelated.mp3'
        foreign.write_bytes(b'not a report and not owned by this manager')
        with self.assertRaises(ReportError): self.reports.close()
        self.assertEqual(foreign.read_bytes(), b'not a report and not owned by this manager')
        foreign.unlink()
        self.reports.close()


if __name__ == '__main__':
    unittest.main()

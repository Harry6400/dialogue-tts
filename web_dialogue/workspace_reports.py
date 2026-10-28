"""Bounded, whole-workspace CSV snapshots of indexed metadata only.

The index and cache paths are supplied by the trusted service owner. CSV never
reads XML, includes dialogue text, accepts a filter, or changes synthesis state.
Each indexed source XML gets its own row, including large duplicate identities.
"""
from contextlib import contextmanager
import codecs
import csv
from dataclasses import dataclass, field
import hashlib
import os
from pathlib import Path
import re
import sqlite3
import stat
import tempfile
import threading
import unicodedata
import uuid

from native_reports import NativeReportDestination, save_report_to_destination
from .output_names import valid_output_name

MAX_REPORT_BYTES = 256 * 1024 * 1024
MAX_CELL_CHARACTERS = 65536
HEADERS = ('批次编号', '内部通话记录编号', '输出编号', '源文件名', '识别角色', '结果文件名', '处理状态', '异常代码', '异常说明')
STATUS_LABELS = {'ready': '待生成', 'invalid': '输入异常', 'queued': '排队中', 'running': '正在合成',
                 'saving': '正在保存', 'save_pending': '待保存', 'saved': '已保存',
                 'synthesis_error': '合成失败', 'save_error': '保存失败', 'conflict': '输出重名',
                 'changed': '输入已变化', 'unverified': '待核验'}
INCOMPLETE_PHASES = {'enumerating', 'reading', 'pairing', 'validating', 'cancelled', 'recovery_scan', 'scan_error', 'finished'}


class ReportError(ValueError):
    pass


class ReportBusy(ReportError):
    pass


def _id(value):
    if not isinstance(value, str) or not re.fullmatch('[a-f0-9]{32}', value):
        raise ReportError('报告或批次标识无效。')
    return value


def _regular(info):
    return stat.S_ISREG(info.st_mode) and not getattr(info, 'st_file_attributes', 0) & 0x400


def display_cell(value):
    """Use a visible text marker, not spreadsheet-specific quote escaping.

    The export is a human-readable checklist. The marker also preserves long
    pure-digit / leading-zero identifiers without formula syntax such as ="...".
    Actual result names ending in .mp3 stay unchanged.
    """
    text = '' if value is None else str(value)
    if len(text) > MAX_CELL_CHARACTERS:
        raise ReportError('报告单元格超出安全长度；未截断或导出不完整清单。')
    if not text:
        return text
    start = text[0]
    unsafe = start in '=+-@＝＋－＠' or start.isspace() or unicodedata.category(start) in {'Cc', 'Cf', 'Zl', 'Zp'}
    numeric = bool(re.fullmatch('[0-9]+', text)) and (len(text) > 15 or len(text) > 1 and text.startswith('0'))
    return '文本：' + text if unsafe or numeric else text


class _UTF8CSVTarget:
    """A text writer with no newline translation and a bounded byte budget."""
    def __init__(self, output, maximum):
        self.output, self.maximum = output, maximum
        self.size, self.digest = 0, hashlib.sha256()
        self._bytes(codecs.BOM_UTF8)

    def _bytes(self, data):
        if self.size + len(data) > self.maximum:
            raise ReportError('报告超过安全字节上限；未截断或导出不完整清单。')
        self.output.write(data)
        self.size += len(data)
        self.digest.update(data)

    def write(self, text):
        self._bytes(text.encode('utf-8'))
        return len(text)


@dataclass
class _Report:
    path: Path
    manifest: dict | None = None
    identity: tuple | None = None
    leases: int = 0
    saving: bool = False
    building: bool = True
    cleanup_failed: bool = False
    owned: dict = field(default_factory=dict)


class WorkspaceReports:
    def __init__(self, index_path, cache_dir, *, max_pending=4, max_bytes=MAX_REPORT_BYTES):
        if not isinstance(max_pending, int) or isinstance(max_pending, bool) or max_pending < 1:
            raise ValueError('报告数量上限无效。')
        if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or max_bytes < 1:
            raise ValueError('报告字节上限无效。')
        self.index_path = Path(index_path).absolute()
        cache_dir = Path(cache_dir).absolute()
        cache_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        info = cache_dir.lstat()
        if not stat.S_ISDIR(info.st_mode) or cache_dir.is_symlink() or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ReportError('报告临时目录不可用。')
        if hasattr(os, 'getuid') and info.st_uid != os.getuid():
            raise ReportError('报告临时目录所有者无效。')
        os.chmod(cache_dir, 0o700)
        # Own a session subdirectory, so close never sweeps another instance's
        # report, task index, MP3, or an unknown pre-existing cache file.
        self.root = Path(tempfile.mkdtemp(prefix='session-', dir=cache_dir))
        self.max_pending, self.max_bytes = max_pending, max_bytes
        self._lock = threading.RLock()
        self._reports = {}
        self._closed = False

    def _record(self, report_id):
        _id(report_id)
        if self._closed:
            raise ReportError('报告会话已结束，请重新导出。')
        record = self._reports.get(report_id)
        if record is None:
            raise KeyError('报告不存在或已经取消，请重新导出。')
        return record

    def _connect(self):
        before = self.index_path.lstat()
        if not _regular(before) or self.index_path.is_symlink():
            raise ReportError('本机任务索引不可用。')
        db = sqlite3.connect(self.index_path.as_uri() + '?mode=ro', uri=True, timeout=5)
        try:
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA query_only=ON')
            db.execute('PRAGMA trusted_schema=OFF')
            db.execute('PRAGMA temp_store=FILE')
            db.execute('PRAGMA cache_size=-2048')
        except BaseException:
            db.close()
            raise
        return db

    def _snapshot(self, workspace_id, output):
        db = self._connect()
        try:
            db.execute('BEGIN')
            workspace = db.execute('SELECT id,status,phase,revision FROM workspaces WHERE id=?', (workspace_id,)).fetchone()
            if workspace is None:
                raise KeyError('批次不存在。')
            if workspace['status'] in {'scanning', 'cancelled', 'error'} or workspace['phase'] in INCOMPLETE_PHASES:
                raise ReportError('请先完成扫描和全局配对，再导出本批次完整清单。')
            columns = {row[1] for row in db.execute('PRAGMA table_info(items)')}
            source_column = 'source_name' if 'source_name' in columns else 'NULL AS source_name'
            source_count = db.execute('SELECT count(*) FROM files WHERE wid=?', (workspace_id,)).fetchone()[0]
            target = _UTF8CSVTarget(output, self.max_bytes)
            writer = csv.writer(target, quoting=csv.QUOTE_ALL, lineterminator='\r\n')
            writer.writerow(HEADERS)
            rows, items, represented = 0, 0, 0
            statement = ('SELECT id,identity,filename,status,error,error_code,' + source_column +
                         ' FROM items WHERE wid=? ORDER BY filename IS NULL,filename,id')
            for item in db.execute(statement, (workspace_id,)):
                items += 1
                output_number = item['filename'][:-4] if valid_output_name(item['filename']) else ''
                if item['identity'] is not None:
                    sources = db.execute('SELECT name,role,error FROM files INDEXED BY file_pair WHERE wid=? AND identity=? ORDER BY name',
                                         (workspace_id, item['identity']))
                else:
                    sources = db.execute('SELECT name,role,error FROM files WHERE wid=? AND name=?',
                                         (workspace_id, item['source_name']))
                found = False
                for source in sources:
                    found = True
                    represented += 1
                    role = {'AGENT': 'AGENT（客服）', 'USER': 'USER（客户）'}.get(source['role'], '未识别')
                    values = (workspace_id, item['id'], output_number, source['name'], role, item['filename'],
                              STATUS_LABELS.get(item['status'], item['status']), item['error_code'], item['error'] or source['error'])
                    writer.writerow(tuple(display_cell(value) for value in values))
                    rows += 1
                if not found:
                    # Retain the item even in an old index without source_name;
                    # source-count consistency below still rejects missing XML.
                    values = (workspace_id, item['id'], output_number, item['source_name'] or '未记录来源', '未识别', item['filename'],
                              STATUS_LABELS.get(item['status'], item['status']), item['error_code'], item['error'])
                    writer.writerow(tuple(display_cell(value) for value in values))
                    rows += 1
            if represented != source_count:
                raise ReportError('源文件索引尚未完整；未导出可能遗漏来源的清单。')
            return {'workspace_id': workspace_id, 'revision': workspace['revision'], 'rows': rows,
                    'items': items, 'sources': represented, 'size': target.size,
                    'sha256': target.digest.hexdigest(), 'scope': 'whole_workspace', 'schema_version': 1}
        finally:
            db.close()

    def create(self, workspace_id):
        workspace_id = _id(workspace_id)
        with self._lock:
            if self._closed:
                raise ReportError('报告会话已结束。')
            if len(self._reports) >= self.max_pending:
                raise ReportBusy('已有未处理的报告，请先保存或取消其中一份，再导出新清单。')
            for _ in range(8):
                report_id = uuid.uuid4().hex
                path = self.root / (report_id + '.csv')
                if report_id not in self._reports and not os.path.lexists(path) and not os.path.lexists(path.with_suffix('.csv.tmp')):
                    break
            else:
                raise ReportError('无法分配独立报告标识，请重试。')
            record = self._reports[report_id] = _Report(path)
        staging = path.with_suffix('.csv.tmp')
        try:
            fd = os.open(staging, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0), 0o600)
            created = os.fstat(fd)
            created_identity = (created.st_dev, created.st_ino)
            # Both exact paths may refer to this inode after publication. Track
            # them before writing, so any failure leaves bounded cleanup work.
            record.owned = {staging: created_identity, path: created_identity}
            with os.fdopen(fd, 'wb') as output:
                manifest = self._snapshot(workspace_id, output)
                output.flush()
                os.fsync(output.fileno())
                info = os.fstat(output.fileno())
            if os.name == 'nt':
                os.rename(staging, path)
            else:
                os.link(staging, path, follow_symlinks=False)
                staging.unlink()
            record.owned.pop(staging)
            manifest.update(report_id=report_id, filename='对白-批次清单-' + report_id[:8] + '.csv', content_type='text/csv; charset=utf-8')
            with self._lock:
                record.manifest = manifest
                record.identity = (info.st_dev, info.st_ino)
                record.building = False
            return dict(manifest)
        except BaseException as issue:
            with self._lock:
                try:
                    self._cleanup_record(record)
                finally:
                    # A cleanup error is idle, retryable work, not a builder
                    # that runs forever. Retain its admission slot and inode
                    # ownership until its own files have really been removed.
                    record.building = False
                    record.cleanup_failed = bool(record.owned)
                    if not record.owned:
                        self._reports.pop(report_id, None)
            if record.cleanup_failed and isinstance(issue, Exception):
                raise ReportError('报告未完成，且临时 CSV 未能清理；请取消未保存的临时 CSV 清单后重试。') from issue
            if isinstance(issue, sqlite3.Error):
                raise ReportError('本机任务索引暂不可读，未导出不完整清单；请恢复任务后重试。') from issue
            raise

    def info(self, report_id):
        with self._lock:
            record = self._record(report_id)
            if record.manifest is None:
                if record.building:
                    raise ReportBusy('报告正在生成，请稍候。')
                raise ReportError('报告生成未完成；请取消未保存的临时 CSV 清单后重试。')
            return dict(record.manifest)

    @contextmanager
    def lease(self, report_id):
        with self._lock:
            record = self._record(report_id)
            if record.manifest is None:
                if record.building:
                    raise ReportBusy('报告正在生成，请稍候。')
                raise ReportError('报告生成未完成；请取消未保存的临时 CSV 清单后重试。')
            before = record.path.lstat()
            if not _regular(before) or (before.st_dev, before.st_ino) != record.identity or before.st_size != record.manifest['size']:
                raise ReportError('报告缓存已变化，请取消并重新导出。')
            fd = os.open(record.path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0))
            handle = os.fdopen(fd, 'rb')
            actual = os.fstat(handle.fileno())
            if not _regular(actual) or (actual.st_dev, actual.st_ino) != record.identity:
                handle.close()
                raise ReportError('报告缓存身份已变化。')
            record.leases += 1
            manifest = dict(record.manifest)
        try:
            digest, size = hashlib.sha256(), 0
            while True:
                block = handle.read(65536)
                if not block:
                    break
                size += len(block)
                if size > manifest['size']:
                    raise ReportError('报告缓存内容已变化。')
                digest.update(block)
            if size != manifest['size'] or digest.hexdigest() != manifest['sha256']:
                raise ReportError('报告缓存校验失败，请重新导出。')
            handle.seek(0)
            yield handle, manifest
        finally:
            handle.close()
            with self._lock:
                record.leases -= 1

    def save_to_native_destination(self, report_id, destination):
        if not isinstance(destination, NativeReportDestination):
            raise ReportError('报告保存需要原生系统面板授权。')
        with self._lock:
            record = self._record(report_id)
            if record.saving:
                raise ReportBusy('报告正在保存，请等待完成。')
            record.saving = True
        try:
            with self.lease(report_id) as (handle, manifest):
                return save_report_to_destination(handle, manifest, destination)
        finally:
            with self._lock:
                record.saving = False

    @staticmethod
    def _cleanup_record(record):
        """Try every owned exact path; failed paths stay counted for a retry."""
        for candidate, identity in tuple(record.owned.items()):
            try:
                owned = candidate.lstat()
                if not _regular(owned) or (owned.st_dev, owned.st_ino) != identity:
                    continue  # Never remove a replacement owned by somebody else.
                candidate.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                continue
            record.owned.pop(candidate, None)
        record.cleanup_failed = bool(record.owned)

    def discard(self, report_id):
        with self._lock:
            record = self._record(report_id)
            if record.building or record.leases or record.saving:
                raise ReportBusy('报告正在生成、读取或保存，请等待完成后再取消。')
            self._cleanup_record(record)
            if record.owned:
                raise ReportError('临时 CSV 未能清理或身份已变化，未删除其他文件；请恢复权限后重试取消。')
            del self._reports[report_id]
        return {'ok': True, 'report_id': report_id}

    def cancel_pending(self):
        """Explicitly cancel this service session's rebuildable CSV snapshots.

        There is no partial cancellation when a builder, reader or save is
        active. Filesystem failures may leave bounded, counted cleanup records;
        only complete real cleanup returns an ok result.
        """
        with self._lock:
            if self._closed:
                return {'ok': True, 'cancelled': 0}
            if self.safety()['busy']:
                raise ReportBusy('报告正在生成、读取或保存，请等待完成后再取消临时清单。')
            cancelled = 0
            for report_id, record in tuple(self._reports.items()):
                self._cleanup_record(record)
                if not record.owned:
                    del self._reports[report_id]
                    cancelled += 1
            if self._reports:
                raise ReportError(f'已取消 {cancelled} 份临时 CSV，仍有 {len(self._reports)} 份未能清理或身份已变化；请恢复权限后重试取消。')
            return {'ok': True, 'cancelled': cancelled}

    def safety(self):
        with self._lock:
            building = any(record.building for record in self._reports.values())
            saving = any(record.saving for record in self._reports.values())
            reading = any(record.leases for record in self._reports.values())
            return {'busy': bool(building or saving or reading), 'saving': bool(saving),
                    'pending': bool(building or reading), 'reports': len(self._reports),
                    'cleanup_pending': sum(record.cleanup_failed for record in self._reports.values())}

    def close(self):
        with self._lock:
            if self._closed:
                return
            if self.safety()['busy']:
                raise ReportBusy('报告正在生成、读取或保存，暂不能清理。')
            self.cancel_pending()
            try:
                self.root.rmdir()
            except FileNotFoundError:
                pass
            except OSError as issue:
                raise ReportError('CSV 会话临时目录未能清理，未删除未知文件；请恢复权限后重试。') from issue
            self._closed = True

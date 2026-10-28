"""Native-authorized directory access and durable, verified output receipts.

Only the native host may register/restore grants. Persisted grants deliberately
start inactive: loading the database never authorizes a directory or reads XML.
"""
from contextlib import contextmanager
from functools import wraps
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import threading
import uuid

MAX_XML = 2 * 1024 * 1024
MAX_AUDIO = 256 * 1024 * 1024
FP = re.compile(r"[a-f0-9]{64}\Z")
LEGACY_NAME = re.compile(r"dialogue(?:-[0-9]{3})?(?: \([0-9]+\))?\.mp3\Z")


def _regular(info):
    return stat.S_ISREG(info.st_mode) and not getattr(info, 'st_file_attributes', 0) & 0x400


def _directory(path):
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or path.is_symlink() or getattr(info, 'st_file_attributes', 0) & 0x400:
        raise ValueError('目录不可用，请重新选择')
    return info.st_dev, info.st_ino


def _name(name, xml=False, legacy=False):
    if not isinstance(name, str) or not name or name in {'.', '..'} or '/' in name or '\\' in name or '\x00' in name:
        raise ValueError('文件名称无效')
    if len(os.fsencode(name)) > 255:
        raise ValueError('文件名称过长')
    if xml:
        if not name.lower().endswith('.xml'): raise ValueError('仅支持 XML')
    else:
        from web_dialogue.output_names import valid_output_name
        if not valid_output_name(name) and not (legacy and LEGACY_NAME.fullmatch(name)):
            raise ValueError('输出名称无效')
    return name


def _fingerprint(value):
    if not isinstance(value, str) or not FP.fullmatch(value): raise ValueError('指纹无效')
    return value


def _sync_directory(path):
    if os.name == 'nt': return  # Windows no-replace rename follows a flushed file.
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0) | getattr(os, 'O_NOFOLLOW', 0))
    try: os.fsync(fd)
    finally: os.close(fd)


def _storage_errors(method):
    @wraps(method)
    def guarded(*args, **kwargs):
        try: return method(*args, **kwargs)
        except sqlite3.Error as exc: raise OSError('本机保存账本暂时不可用，请检查空间和权限') from exc
    return guarded


class NativeWorkspaceAccess:
    @_storage_errors
    def __init__(self, state_dir):
        self.state_dir = Path(state_dir).absolute()
        self.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        _directory(self.state_dir)
        info = self.state_dir.stat()
        if hasattr(os, 'getuid') and info.st_uid != os.getuid(): raise ValueError('状态目录所有者无效')
        os.chmod(self.state_dir, 0o700)
        self._lock = threading.RLock()
        self._closed = False
        self._active = set()
        database = self.state_dir / 'native-workspace.sqlite3'
        if database.is_symlink(): raise ValueError('状态数据库不能是链接')
        self._db = sqlite3.connect(database, check_same_thread=False, timeout=30)
        self._db.row_factory = sqlite3.Row
        self._db.execute('PRAGMA journal_mode=WAL')
        self._db.execute('PRAGMA synchronous=FULL')
        self._db.executescript('''
            CREATE TABLE IF NOT EXISTS grants(id TEXT PRIMARY KEY,path TEXT NOT NULL,kind TEXT NOT NULL,dev INTEGER NOT NULL,ino INTEGER NOT NULL);
            CREATE INDEX IF NOT EXISTS grants_location ON grants(path,kind,dev,ino);
            CREATE TABLE IF NOT EXISTS receipts(folder TEXT NOT NULL,name TEXT NOT NULL,fingerprint TEXT NOT NULL,size INTEGER NOT NULL,sha256 TEXT NOT NULL,save_id TEXT NOT NULL,PRIMARY KEY(folder,name));
            CREATE INDEX IF NOT EXISTS receipts_fingerprint ON receipts(folder,fingerprint);
            CREATE TABLE IF NOT EXISTS intents(id TEXT PRIMARY KEY,folder TEXT NOT NULL,name TEXT NOT NULL,fingerprint TEXT NOT NULL,size INTEGER NOT NULL,sha256 TEXT NOT NULL,temporary TEXT NOT NULL,dev INTEGER NOT NULL,ino INTEGER NOT NULL);
            CREATE INDEX IF NOT EXISTS intents_target ON intents(folder,name);
            CREATE TABLE IF NOT EXISTS migrations(source TEXT PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS legacy_pending(item TEXT PRIMARY KEY,folder TEXT NOT NULL);
        ''')
        os.chmod(database, 0o600)
        self.migrate_preferences(self.state_dir / 'preferences.json')

    @_storage_errors
    def close(self):
        with self._lock:
            if not self._closed:
                self._db.close()
                self._closed = True

    @_storage_errors
    def register(self, path, kind):
        if kind not in {'input', 'output'}: raise ValueError('目录用途无效')
        if not isinstance(path, (str, os.PathLike)) or not Path(path).is_absolute(): raise ValueError('目录路径无效')
        path = Path(path).absolute()
        dev, ino = _directory(path)
        with self._lock:
            row = self._db.execute('SELECT id FROM grants WHERE path=? AND kind=? AND dev=? AND ino=?', (str(path), kind, dev, ino)).fetchone()
            ident = row['id'] if row else uuid.uuid4().hex
            if not row:
                with self._db: self._db.execute('INSERT INTO grants VALUES(?,?,?,?,?)', (ident, str(path), kind, dev, ino))
            self._active.add(ident)
            return {'folder_id': ident, 'path': str(path), 'valid': True}

    @_storage_errors
    def restore(self, folder_id):
        with self._lock:
            row = self._grant(folder_id)
            if _directory(Path(row['path'])) != (row['dev'], row['ino']):
                self._active.discard(folder_id)
                raise ValueError('目录已变化，请重新选择')
            self._active.add(folder_id)
            return self.info(folder_id, row['kind'])

    def _grant(self, ident):
        if not isinstance(ident, str): raise ValueError('目录授权无效')
        row = self._db.execute('SELECT * FROM grants WHERE id=?', (ident,)).fetchone()
        if not row: raise ValueError('目录授权无效')
        return row

    @_storage_errors
    def directory(self, folder_id, kind):
        with self._lock:
            row = self._grant(folder_id)
            if kind not in {'input', 'output'} or row['kind'] != kind or folder_id not in self._active:
                raise ValueError('请先恢复目录授权或重新选择')
            path = Path(row['path'])
            if _directory(path) != (row['dev'], row['ino']):
                self._active.discard(folder_id)
                raise ValueError('目录已变化，请重新选择')
            return path

    def info(self, folder_id, kind):
        return {'folder_id': folder_id, 'path': str(self.directory(folder_id, kind)), 'valid': True}

    @contextmanager
    def _opened_directory(self, folder_id, kind):
        path = self.directory(folder_id, kind)
        fd = None
        if os.name != 'nt':
            fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | getattr(os, 'O_NOFOLLOW', 0))
            opened = os.fstat(fd)
            if (opened.st_dev, opened.st_ino) != _directory(path):
                os.close(fd); raise ValueError('目录已变化')
        try:
            yield path, fd
            self.directory(folder_id, kind)
        finally:
            if fd is not None: os.close(fd)

    def iter_files(self, folder_id):
        with self._opened_directory(folder_id, 'input') as (path, fd):
            with os.scandir(fd if fd is not None else path) as entries:
                for entry in entries:
                    if entry.name.lower().endswith('.xml'):
                        # Names are untrusted metadata here. read_file validates
                        # before opening, inside the scanner's per-file error gate.
                        yield entry.name

    def _read(self, path, maximum, directory_fd=None, name=None):
        before = os.stat(name, dir_fd=directory_fd, follow_symlinks=False) if directory_fd is not None else path.lstat()
        if not _regular(before) or not 0 <= before.st_size <= maximum: raise ValueError('文件类型或大小不受支持')
        flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0)
        fd = os.open(name, flags, dir_fd=directory_fd) if directory_fd is not None else os.open(path, flags)
        with os.fdopen(fd, 'rb') as handle:
            actual = os.fstat(handle.fileno())
            if not _regular(actual) or (before.st_dev, before.st_ino) != (actual.st_dev, actual.st_ino): raise ValueError('文件已变化')
            data = handle.read(maximum + 1)
            after = os.fstat(handle.fileno())
            if len(data) > maximum or (actual.st_size, actual.st_mtime_ns) != (after.st_size, after.st_mtime_ns) or len(data) != after.st_size:
                raise ValueError('文件已变化或过大')
        return data

    def read_file(self, folder_id, name):
        name = _name(name, xml=True)
        with self._opened_directory(folder_id, 'input') as (path, fd):
            return self._read(path / name, MAX_XML, fd, name)

    def _record(self, folder, name, fingerprint, size, digest, save_id=None):
        self._db.execute('INSERT INTO receipts VALUES(?,?,?,?,?,?) ON CONFLICT(folder,name) DO UPDATE SET fingerprint=excluded.fingerprint,size=excluded.size,sha256=excluded.sha256,save_id=excluded.save_id',
                         (str(folder), name, fingerprint, size, digest, save_id or uuid.uuid4().hex))

    @_storage_errors
    def migrate_preferences(self, path):
        """Import legacy metadata once; output files remain untouched and untrusted until verified."""
        path = Path(path)
        with self._lock:
            if self._db.execute('SELECT 1 FROM migrations WHERE source=?', (str(path),)).fetchone(): return
            try: values = json.loads(self._read(path, 128 * 1024 * 1024))
            except (OSError, ValueError, TypeError): return
            if not isinstance(values, dict): return
            candidates = []
            for folder, items in values.get('receipts', {}).items() if isinstance(values.get('receipts'), dict) else []:
                if isinstance(items, dict): candidates.extend((folder, item) for item in items.values())
            for key, item in values.get('exports', {}).items() if isinstance(values.get('exports'), dict) else []:
                if isinstance(key, str) and '|' in key: candidates.append((key.rsplit('|', 1)[0], item))
            if isinstance(values.get('records'), list):
                candidates.extend((item.get('folder'), item) for item in values['records'] if isinstance(item, dict))
            with self._db:
                for folder, item in candidates:
                    if not isinstance(folder, str) or not Path(folder).is_absolute() or not isinstance(item, dict): continue
                    try:
                        name = _name(item.get('name'), legacy=True)
                        fp = _fingerprint(item.get('fingerprint')); digest = _fingerprint(item.get('sha256'))
                        size = item.get('size')
                        if isinstance(size, bool) or not isinstance(size, int) or not 0 < size <= MAX_AUDIO: continue
                        # Existing SQLite receipts are newer than this legacy snapshot.
                        self._db.execute('INSERT OR IGNORE INTO receipts VALUES(?,?,?,?,?,?)', (folder, name, fp, size, digest, uuid.uuid4().hex))
                    except (ValueError, TypeError): continue
                self._db.execute('INSERT INTO migrations VALUES(?)', (str(path),))
                for item in values.get('pending_outputs', []) if isinstance(values.get('pending_outputs'), list) else []:
                    if isinstance(item, dict) and isinstance(item.get('folder'), str):
                        self._db.execute('INSERT OR IGNORE INTO legacy_pending VALUES(?,?)', (json.dumps(item), item['folder']))

    def _migrate_pending(self, item):
        if not isinstance(item, dict): return
        folder, temporary = item.get('folder'), item.get('temporary')
        if not isinstance(folder, str) or not Path(folder).is_absolute() or not isinstance(temporary, str) or not re.fullmatch(r'\.dialogue-[A-Fa-f0-9-]{36}\.tmp', temporary): return
        try:
            root = Path(folder); _directory(root)
            original = (root / temporary).lstat()
            if not _regular(original): return
            fp = _fingerprint(item.get('fingerprint')); digest = _fingerprint(item.get('sha256'))
            size = item.get('size')
            if not isinstance(size, int) or not 0 < size <= MAX_AUDIO: return
            with os.scandir(root) as entries:
                for entry in entries:
                    try: name = _name(entry.name, legacy=True)
                    except ValueError: continue
                    candidate = entry.stat(follow_symlinks=False)
                    if _regular(candidate) and (candidate.st_dev, candidate.st_ino) == (original.st_dev, original.st_ino):
                        data = self._read(root / name, MAX_AUDIO)
                        if len(data) == size and hashlib.sha256(data).hexdigest() == digest:
                            with self._db: self._record(root, name, fp, size, digest)
                            (root / temporary).unlink(missing_ok=True)
                            return True
        except (OSError, ValueError): return False
        return False

    def _recover(self, folder, name):
        # Access is active here; startup migration itself never traverses outputs.
        for row in self._db.execute('SELECT item FROM legacy_pending WHERE folder=?', (str(folder),)).fetchall():
            if self._migrate_pending(json.loads(row['item'])):
                with self._db: self._db.execute('DELETE FROM legacy_pending WHERE item=?', (row['item'],))
        rows = self._db.execute('SELECT * FROM intents WHERE folder=? AND name=?', (str(folder), name)).fetchall()
        for row in rows:
            target = folder / name
            temporary = folder / row['temporary']
            try:
                info = target.lstat()
                owned = _regular(info) and (info.st_dev, info.st_ino) == (row['dev'], row['ino'])
                if owned:
                    data = self._read(target, MAX_AUDIO)
                    if len(data) != row['size'] or hashlib.sha256(data).hexdigest() != row['sha256']: continue
                    with self._db:
                        self._record(folder, name, row['fingerprint'], row['size'], row['sha256'])
                        self._db.execute('DELETE FROM intents WHERE id=?', (row['id'],))
                elif target.exists():
                    continue
                else:
                    with self._db: self._db.execute('DELETE FROM intents WHERE id=?', (row['id'],))
                if temporary.exists():
                    info = temporary.lstat()
                    if _regular(info) and (info.st_dev, info.st_ino) == (row['dev'], row['ino']): temporary.unlink()
            except (OSError, ValueError): continue

    @_storage_errors
    def verify_output(self, folder_id, filename, fingerprint):
        filename = _name(filename, legacy=True); fingerprint = _fingerprint(fingerprint)
        with self._lock:
            folder = self.directory(folder_id, 'output')
            self._recover(folder, filename)
            target = folder / filename
            if not os.path.lexists(target): return {'state': 'missing', 'name': filename, 'fingerprint': fingerprint}
            row = self._db.execute('SELECT * FROM receipts WHERE folder=? AND name=? AND fingerprint=?', (str(folder), filename, fingerprint)).fetchone()
            if row:
                try:
                    with self._opened_directory(folder_id, 'output') as (path, fd):
                        data = self._read(path / filename, MAX_AUDIO, fd, filename)
                    if len(data) == row['size'] and hashlib.sha256(data).hexdigest() == row['sha256']:
                        return {'state': 'saved', 'ok': True, 'save_id': row['save_id'], 'name': filename, 'path': str(target), 'fingerprint': fingerprint, 'size': row['size'], 'sha256': row['sha256']}
                except (OSError, ValueError): pass
            return {'state': 'conflict', 'name': filename, 'fingerprint': fingerprint, 'error': '同名文件已存在且无法核实，请移动旧文件或选择其他目录。'}

    @_storage_errors
    def names_for_fingerprint(self, folder_id, fingerprint):
        folder = self.directory(folder_id, 'output'); fingerprint = _fingerprint(fingerprint)
        with self._lock:
            return [row['name'] for row in self._db.execute('SELECT name FROM receipts WHERE folder=? AND fingerprint=?', (str(folder), fingerprint))]

    def _after_publish(self):
        """Fault-injection seam for crash recovery tests; never exposed to JS."""

    @_storage_errors
    def save_audio(self, folder_id, source, filename, fingerprint):
        filename = _name(filename); fingerprint = _fingerprint(fingerprint)
        with self._lock:
            prior = self.verify_output(folder_id, filename, fingerprint)
            if prior['state'] == 'saved': return prior
            if prior['state'] == 'conflict': raise FileExistsError(prior['error'])
            data = self._read(Path(source), MAX_AUDIO)
            from web_dialogue.mp3_frames import parse_frames
            if not parse_frames(data): raise ValueError('音频为空')
            digest = hashlib.sha256(data).hexdigest()
            with self._opened_directory(folder_id, 'output') as (folder, directory_fd):
                temporary = '.dialogue-' + str(uuid.uuid4()) + '.tmp'
                path = folder / temporary
                flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0)
                fd = os.open(temporary, flags, 0o600, dir_fd=directory_fd) if directory_fd is not None else os.open(path, flags, 0o600)
                journal = uuid.uuid4().hex; published = False; journaled = False
                try:
                    with os.fdopen(fd, 'wb') as out:
                        out.write(data); out.flush(); os.fsync(out.fileno()); info = os.fstat(out.fileno())
                    self.directory(folder_id, 'output')
                    with self._db:
                        self._db.execute('INSERT INTO intents VALUES(?,?,?,?,?,?,?,?,?)', (journal, str(folder), filename, fingerprint, len(data), digest, temporary, info.st_dev, info.st_ino))
                    journaled = True
                    if os.name == 'nt': os.rename(path, folder / filename)
                    else: os.link(temporary, filename, src_dir_fd=directory_fd, dst_dir_fd=directory_fd, follow_symlinks=False)
                    published = True
                    _sync_directory(folder)
                    self._after_publish()
                    with self._db:
                        self._record(folder, filename, fingerprint, len(data), digest)
                        self._db.execute('DELETE FROM intents WHERE id=?', (journal,))
                    path.unlink(missing_ok=True)
                    result = self.verify_output(folder_id, filename, fingerprint)
                    if result['state'] != 'saved': raise OSError('保存后校验失败')
                    return result
                finally:
                    # Never delete a published output, even if receipt persistence fails.
                    if not published:
                        path.unlink(missing_ok=True)
                        if journaled:
                            with self._db: self._db.execute('DELETE FROM intents WHERE id=?', (journal,))

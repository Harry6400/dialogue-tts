"""Trusted native-download captures; MP3 publication stays in the one ledger.

Only native-token endpoints may reserve a capture. A page never supplies a
source path or the captured response metadata. Complete audio survives failed
saves and process restart until a real ledger receipt or explicit discard.
"""
from contextlib import contextmanager
import os
from pathlib import Path
import re
import sqlite3
import stat
import threading
import time
import uuid

from web_dialogue.mp3_frames import validated_file_digest
from web_dialogue.output_names import valid_output_name

ROUTE = re.compile(r'/api/(?:jobs/[a-f0-9]{32}/audio|batches/[a-f0-9]{32}/audio/[0-9]{3})\Z')
IDENT = re.compile(r'[a-f0-9]{32}\Z')
FINGERPRINT = re.compile(r'[a-f0-9]{64}\Z')
MAX_AUDIO = 256 * 1024 * 1024


class AudioCaptureError(ValueError):
    pass


class AudioCaptureBusy(AudioCaptureError):
    pass


def _regular(info):
    return stat.S_ISREG(info.st_mode) and not getattr(info, 'st_file_attributes', 0) & 0x400


def _directory(path):
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or path.is_symlink() or getattr(info, 'st_file_attributes', 0) & 0x400:
        raise AudioCaptureError('原生音频缓存目录不可用。')
    if hasattr(os, 'getuid') and info.st_uid != os.getuid():
        raise AudioCaptureError('原生音频缓存所有者无效。')
    return info.st_dev, info.st_ino


def _id(value):
    if not isinstance(value, str) or not IDENT.fullmatch(value):
        raise AudioCaptureError('原生音频标识无效。')
    return value


class NativeAudioCaptures:
    def __init__(self, state_dir, native_access, *, max_pending=16, max_bytes=512 * 1024 * 1024):
        if type(max_pending) is not int or not 1 <= max_pending <= 128 or type(max_bytes) is not int or max_bytes < 1:
            raise ValueError('原生音频缓存限制无效。')
        self.root = Path(state_dir).absolute() / 'native-audio'
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._root_identity = _directory(self.root)
        os.chmod(self.root, 0o700)
        database = self.root / 'captures.sqlite3'
        if os.path.lexists(database) and not _regular(database.lstat()):
            raise AudioCaptureError('原生音频索引不可用。')
        self._db = sqlite3.connect(database, timeout=30, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute('PRAGMA journal_mode=WAL')
        self._db.executescript('''CREATE TABLE IF NOT EXISTS captures(
          id TEXT PRIMARY KEY,route TEXT NOT NULL,name TEXT NOT NULL,fingerprint TEXT NOT NULL,
          expected INTEGER NOT NULL,state TEXT NOT NULL,sha256 TEXT,device INTEGER,inode INTEGER,
          slot_device INTEGER,slot_inode INTEGER,created REAL NOT NULL);
          CREATE INDEX IF NOT EXISTS capture_route ON captures(route,created);''')
        os.chmod(database, 0o600)
        self.access, self.max_pending, self.max_bytes = native_access, max_pending, max_bytes
        self._lock = threading.RLock()
        self._active, self._checking, self._saving = set(), set(), set()
        self._leases = {}
        self._closed = False
        with self._db:
            self._db.execute("UPDATE captures SET state='interrupted' WHERE state='downloading'")
        for row in self._db.execute('SELECT id FROM captures').fetchall():
            try: self.complete(row['id'])
            except (ValueError, OSError): pass  # Preserve interrupted/corrupt files for an explicit decision.

    def _guard(self):
        if self._closed or _directory(self.root) != self._root_identity:
            raise AudioCaptureError('原生音频缓存状态无法确认。')

    def _record(self, capture_id):
        self._guard()
        row = self._db.execute('SELECT * FROM captures WHERE id=?', (_id(capture_id),)).fetchone()
        if row is None: raise KeyError('原生音频不存在。')
        return row

    def _path(self, row):
        slot = self.root / _id(row['id'])
        identity = _directory(slot)
        if row['slot_device'] is not None and identity != (row['slot_device'], row['slot_inode']):
            raise AudioCaptureError('原生音频缓存目录身份已变化。')
        return slot / 'audio.mp3'

    @staticmethod
    def _info(row):
        return {'capture_id': row['id'], 'resource_path': row['route'], 'filename': row['name'], 'size': row['expected'], 'state': row['state']}

    def reserve(self, metadata):
        if not isinstance(metadata, dict) or set(metadata) != {'resource_path', 'filename', 'fingerprint', 'size'}:
            raise AudioCaptureError('原生音频响应信息无效。')
        route, name, fingerprint, size = (metadata[key] for key in ('resource_path', 'filename', 'fingerprint', 'size'))
        if not isinstance(route, str) or not ROUTE.fullmatch(route) or not valid_output_name(name) or not isinstance(fingerprint, str) or not FINGERPRINT.fullmatch(fingerprint) or type(size) is not int or not 0 < size <= MAX_AUDIO:
            raise AudioCaptureError('原生音频响应信息无效。')
        with self._lock:
            self._guard()
            count, used = self._db.execute('SELECT count(*),coalesce(sum(expected),0) FROM captures').fetchone()
            if count >= self.max_pending or used + size > self.max_bytes:
                raise AudioCaptureBusy('未保存音频缓存已满，请先保存或明确放弃已有结果。')
            if self._db.execute('SELECT 1 FROM captures WHERE route=?', (route,)).fetchone():
                raise AudioCaptureBusy('此音频已在缓存中，请先重试保存已有结果。')
            for _ in range(8):
                ident = uuid.uuid4().hex
                slot = self.root / ident
                if not os.path.lexists(slot) and not self._db.execute('SELECT 1 FROM captures WHERE id=?', (ident,)).fetchone(): break
            else: raise AudioCaptureError('无法分配音频缓存，请重试。')
            with self._db:
                self._db.execute('INSERT INTO captures(id,route,name,fingerprint,expected,state,created) VALUES(?,?,?,?,?,?,?)', (ident, route, name, fingerprint, size, 'interrupted', time.time()))
            try:
                slot.mkdir(mode=0o700)
                device, inode = _directory(slot)
                with self._db:
                    self._db.execute("UPDATE captures SET slot_device=?,slot_inode=?,state='downloading' WHERE id=?", (device, inode, ident))
                self._active.add(ident)
            except BaseException:
                # The record remains cancellable; never sweep a possible file.
                raise
            return {'ok': True, **self._info(self._record(ident)), 'path': str(slot / 'audio.mp3')}

    def complete(self, capture_id):
        with self._lock:
            row = self._record(capture_id)
            if capture_id in self._checking or capture_id in self._saving:
                raise AudioCaptureBusy('原生音频正在核验或保存。')
            self._checking.add(capture_id)
        try:
            path = self._path(row)
            info = path.lstat()
            if not _regular(info) or row['device'] is not None and (info.st_dev, info.st_ino) != (row['device'], row['inode']):
                raise AudioCaptureError('原生音频缓存身份已变化。')
            size, digest = validated_file_digest(path)
            current = path.lstat()
            if (current.st_dev,current.st_ino) != (info.st_dev,info.st_ino):
                raise AudioCaptureError('原生音频缓存身份已变化。')
            if size != row['expected'] or row['sha256'] and digest != row['sha256']:
                raise AudioCaptureError('原生音频未完整下载或内容已变化。')
            with self._lock, self._db:
                self._db.execute("UPDATE captures SET state='ready',sha256=?,device=?,inode=? WHERE id=?", (digest, info.st_dev, info.st_ino, capture_id))
                return {'ok': True, **self._info(self._record(capture_id))}
        except BaseException:
            with self._lock, self._db:
                self._db.execute("UPDATE captures SET state='interrupted' WHERE id=?", (capture_id,))
            raise
        finally:
            with self._lock:
                self._checking.discard(capture_id); self._active.discard(capture_id)

    def abort(self, capture_id):
        try: return self.complete(capture_id)
        except (ValueError, OSError):
            with self._lock:
                return {'ok': True, **self._info(self._record(capture_id))}

    def find(self, resource_path):
        if not isinstance(resource_path, str) or not ROUTE.fullmatch(resource_path):
            raise AudioCaptureError('音频资源地址无效。')
        with self._lock:
            self._guard()
            row = self._db.execute('SELECT * FROM captures WHERE route=? ORDER BY created DESC LIMIT 1', (resource_path,)).fetchone()
            return {'ok': True, 'capture': self._info(row) if row is not None else None}

    def pending(self):
        with self._lock:
            self._guard()
            return {'ok': True, 'captures': [self._info(row) for row in self._db.execute('SELECT * FROM captures ORDER BY created,id')]}

    @contextmanager
    def lease(self, capture_id):
        with self._lock:
            if capture_id in self._active: raise AudioCaptureBusy('音频仍在下载。')
        self.complete(capture_id)
        with self._lock:
            row = self._record(capture_id)
            self._leases[capture_id] = self._leases.get(capture_id, 0) + 1
        try: yield self._path(row), dict(row)
        finally:
            with self._lock:
                self._leases[capture_id] -= 1
                if not self._leases[capture_id]: del self._leases[capture_id]

    def save(self, capture_id, folder_id):
        with self._lock:
            self._record(capture_id)
            if capture_id in self._active or capture_id in self._saving: raise AudioCaptureBusy('音频仍在下载或保存。')
        self.complete(capture_id)
        with self._lock:
            row = self._record(capture_id)
            if capture_id in self._saving or capture_id in self._checking: raise AudioCaptureBusy('音频仍在核验或保存。')
            self._saving.add(capture_id)
            self._leases[capture_id] = self._leases.get(capture_id,0)+1
        try:
            result = self.access.save_audio(folder_id, self._path(row), row['name'], row['fingerprint'], expected_size=row['expected'], expected_sha256=row['sha256'])
        finally:
            with self._lock:
                self._saving.discard(capture_id)
                self._leases[capture_id] -= 1
                if not self._leases[capture_id]: del self._leases[capture_id]
        if result.get('ok') is not True or result.get('state') != 'saved': raise AudioCaptureError('未收到真实音频保存回执。')
        self.discard(capture_id)
        return {**result, 'resource_path': row['route']}

    def discard(self, capture_id=None):
        with self._lock:
            self._guard()
            rows = [self._record(capture_id)] if capture_id is not None else self._db.execute('SELECT * FROM captures ORDER BY created,id').fetchall()
            requested = {row['id'] for row in rows}
            if requested.intersection(self._active | self._checking | self._saving | set(self._leases)):
                raise AudioCaptureBusy('音频正在下载、核验或保存，请等待完成。')
            for row in rows:
                slot = self.root / _id(row['id'])
                if os.path.lexists(slot):
                    path = self._path(row)
                    entries = list(slot.iterdir())
                    if any(entry.name != 'audio.mp3' for entry in entries): raise AudioCaptureError('缓存目录含未知文件，未清理。')
                    if os.path.lexists(path):
                        info = path.lstat()
                        if not _regular(info) or row['device'] is not None and (info.st_dev, info.st_ino) != (row['device'], row['inode']):
                            raise AudioCaptureError('缓存身份已变化，未清理。')
                        path.unlink()
                    slot.rmdir()
                with self._db: self._db.execute('DELETE FROM captures WHERE id=?', (row['id'],))
            return {'ok': True}

    def safety(self):
        try:
            with self._lock:
                self._guard()
                ids = {row['id'] for row in self._db.execute('SELECT id FROM captures')}
                for path in self.root.iterdir():
                    if path.name not in {'captures.sqlite3', 'captures.sqlite3-wal', 'captures.sqlite3-shm', '.DS_Store'} and path.name not in ids:
                        raise AudioCaptureError('存在未知原生音频缓存。')
                return {'busy': bool(self._active or self._checking or self._saving or self._leases), 'unsaved': bool(ids), 'saving': bool(self._saving), 'pending': bool(self._active or self._checking or self._leases)}
        except (ValueError, OSError, sqlite3.Error):
            return {'busy': True, 'unsaved': True, 'saving': True, 'pending': True, 'unknown': True}

    def close(self):
        with self._lock:
            if self._closed: return
            if self.safety()['busy']: raise AudioCaptureBusy('原生音频仍在使用，暂不能关闭缓存。')
            self._db.close(); self._closed = True

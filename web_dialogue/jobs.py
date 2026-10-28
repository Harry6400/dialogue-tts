"""Synthesis workers, result leases and explicit ownership of temporary output."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import uuid

from .normalize import InputError
from .mp3_frames import validated_file_digest
from .output_names import valid_output_name

ROOT = Path(__file__).resolve().parent.parent

class ResultLease:
    """One immutable worker result; a live lease prevents release and close."""
    def __init__(self, manager, ident, handle, size, filename, fingerprint, sha256=None, local_path=None):
        self._manager, self._ident, self._handle = manager, ident, handle
        self._size, self._filename, self._fingerprint = size, filename, fingerprint
        self._sha256, self._local_path = sha256, local_path
        self.closed = False
    @property
    def size(self): return self._size
    @property
    def filename(self): return self._filename
    @property
    def fingerprint(self): return self._fingerprint
    @property
    def sha256(self): return self._sha256
    @property
    def local_path(self):
        if self.closed: raise InputError('结果租约已关闭。')
        return self._local_path
    def read(self, *args): return self._handle.read(*args)
    def seek(self, *args): return self._handle.seek(*args)
    def tell(self): return self._handle.tell()
    def seekable(self): return self._handle.seekable()
    def close(self):
        if not self.closed:
            self._handle.close()
            with self._manager.lock:
                self._manager.items[self._ident]['leases'] -= 1
            self.closed = True
    def __enter__(self): return self
    def __exit__(self, *args): self.close()


def _regular(info):
    return stat.S_ISREG(info.st_mode) and not getattr(info, 'st_file_attributes', 0) & 0x400


def _pid_alive(pid):
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return True  # A launch may have happened before its PID was persisted.
    if os.name == 'nt':
        import ctypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.restype = ctypes.c_void_p
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return ctypes.get_last_error() not in (87, 1168)
        try:
            code = ctypes.c_ulong()
            return not kernel.GetExitCodeProcess(ctypes.c_void_p(handle), ctypes.byref(code)) or code.value == 259
        finally: kernel.CloseHandle(ctypes.c_void_p(handle))
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


class Jobs:
    def __init__(self, timeout_seconds=900, *, state_dir=None):
        self.timeout_seconds = timeout_seconds
        self.request_ids = {}
        self.temp = tempfile.TemporaryDirectory(prefix="dialogue-tts-")
        self.root = Path(self.temp.name)
        self.items = {}
        self.lock = threading.RLock()
        self._batch_owner = None
        self._closed = False
        self._closing = False
        self._unknown = set()
        self._persistent_root = None
        if state_dir is not None:
            root = Path(state_dir) / 'worker-results'
            root.mkdir(parents=True, exist_ok=True, mode=0o700)
            info = root.lstat()
            if not stat.S_ISDIR(info.st_mode) or root.is_symlink() or getattr(info, 'st_file_attributes', 0) & 0x400:
                raise InputError('持久合成缓存目录无效。')
            if hasattr(os, 'getuid') and info.st_uid != os.getuid():
                raise InputError('持久合成缓存所有者无效。')
            os.chmod(root, 0o700)
            self._persistent_root = root
            self._recover()

    @property
    def batch_active(self):
        return self._batch_owner

    @contextmanager
    def transaction(self):
        with self.lock:
            if self._closed: raise InputError('合成服务已关闭。')
            yield

    def has_running(self):
        with self.lock:
            return bool(self._unknown) or any(item.get('termination_uncertain') or item.get('process') is not None and item['process'].poll() is None for item in self.items.values())

    def ensure_available(self, owner=None):
        with self.lock:
            if self._closed: raise InputError('合成服务已关闭。')
            if self._batch_owner is not None and self._batch_owner != owner:
                raise InputError('已有批次正在处理，请等待或暂停。')
            if self.has_running():
                raise InputError('已有合成进程运行或缓存状态待核验，请先处理。')

    def claim_batch(self, owner):
        if not isinstance(owner, str) or not owner: raise InputError('批次所有者无效。')
        with self.lock:
            self.ensure_available(owner)
            self._batch_owner = owner

    def release_batch(self, owner):
        with self.lock:
            if self._batch_owner == owner:
                self._batch_owner = None
            elif self._batch_owner is not None:
                raise InputError('批次所有者不匹配。')

    def wait_finished(self, ident, timeout=1):
        with self.lock:
            item = self.items[ident]
            process = item.get('process')
            if item.get('orphan_pid_pending'):
                if _pid_alive(item.get('pid')): return False
                item['orphan_pid_pending'] = False
                self._unknown.discard(ident)
        if process is None: return True
        try: process.wait(timeout=timeout)
        except subprocess.TimeoutExpired: return False
        return process.poll() is not None

    def temporary_bytes(self):
        with self.lock:
            total = 0
            for item in self.items.values():
                for path in item['folder'].iterdir():
                    info = path.lstat()
                    if not _regular(info): raise InputError('合成缓存中存在未知文件类型。')
                    total += info.st_size
            return total

    def result_available(self, ident):
        """Even an unverified MP3 must not be discarded as an empty job."""
        with self.lock:
            return os.path.lexists(self.items[ident]['folder'] / 'dialogue.mp3')

    def is_known(self, ident):
        with self.lock:
            return ident in self.items or ident in self._unknown

    def discard_workspace_results(self, workspace_id):
        """Explicit user discard, scoped to stopped results of one workspace."""
        with self.lock:
            owned = [ident for ident,item in self.items.items()
                     if (item.get('context') or {}).get('workspace_id') == workspace_id]
            for ident in owned:
                item=self.items[ident]
                if item['leases'] or item.get('orphan_pid_pending') or item.get('termination_uncertain') or item.get('process') is not None and item['process'].poll() is None:
                    raise InputError('此批次合成源仍在运行或读取，暂不能放弃。')
            for ident in owned:self.release(ident)
            return len(owned)

    def _metadata(self, ident, item):
        return {'version': 1, 'id': ident, 'filename': item['filename'], 'fingerprint': item.get('fingerprint'),
                'total': item['total'], 'request_binding': item['request_binding'], 'context': item.get('context'),
                'pid': item.get('process').pid if item.get('process') is not None else item.get('pid'),
                'size': item.get('size'), 'sha256': item.get('sha256')}

    def _persist(self, ident, item):
        if not item.get('persistent'): return
        target = item['folder'] / 'recovery.json'
        temporary = target.with_suffix('.tmp')
        with open(temporary, 'w', encoding='utf-8') as output:
            os.chmod(temporary, 0o600)
            json.dump(self._metadata(ident, item), output, ensure_ascii=False)
            output.flush(); os.fsync(output.fileno())
        os.replace(temporary, target)
        if os.name != 'nt':
            fd = os.open(item['folder'], os.O_RDONLY | os.O_DIRECTORY)
            try: os.fsync(fd)
            finally: os.close(fd)

    @staticmethod
    def _clean_auxiliary(item):
        for name in ('normalized.json', 'timeline.json', 'process.log', 'worker_status.json', 'worker_status.tmp', 'dialogue.partial'):
            (item['folder'] / name).unlink(missing_ok=True)

    def _fact(self, ident, item):
        path = item['folder'] / 'dialogue.mp3'
        info = path.lstat()
        token = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        if item.get('checked') != token:
            size, digest = validated_file_digest(path)
            if item.get('sha256') and (item['sha256'] != digest or item['size'] != size):
                raise InputError('持久合成结果校验不一致。')
            item.update(size=size, sha256=digest, checked=token)
        if not item.get('persisted_result'):
            self._persist(ident, item)
            item['persisted_result'] = True
        item['result_validated'] = True
        if item.get('persistent') and (item.get('context') is not None or item.get('recovered') or self._closing) and not item.get('orphan_pid_pending') and (item.get('process') is None or item['process'].poll() is not None):
            self._clean_auxiliary(item)
        return {'id': ident, 'filename': item['filename'], 'fingerprint': item.get('fingerprint'),
                'context': item.get('context'), 'size': item['size'], 'sha256': item['sha256']}

    def _recover(self):
        import re
        for folder in self._persistent_root.iterdir():
            try:
                info = folder.lstat()
                if not stat.S_ISDIR(info.st_mode) or folder.is_symlink() or not re.fullmatch('[a-f0-9]{32}', folder.name): raise ValueError('unknown')
                metadata = folder / 'recovery.json'
                info = metadata.lstat()
                if not _regular(info) or info.st_size > 16384: raise ValueError('metadata')
                value = json.loads(metadata.read_text(encoding='utf-8'))
                if value.get('version') != 1 or value.get('id') != folder.name or not valid_output_name(value.get('filename')) or not re.fullmatch('[a-f0-9]{64}', value.get('fingerprint') or ''): raise ValueError('binding')
                context = value.get('context')
                if context is not None and (not isinstance(context, dict) or set(context) != {'workspace_id', 'item_id'} or any(not isinstance(v, str) or not re.fullmatch('[a-f0-9]{32}', v) for v in context.values())): raise ValueError('context')
                item = {'folder': folder, 'process': None, 'timer': None, 'pid': value.get('pid'), 'total': value['total'],
                        'filename': value['filename'], 'fingerprint': value['fingerprint'], 'request_binding': value['request_binding'],
                        'context': context, 'size': value.get('size'), 'sha256': value.get('sha256'), 'persistent': True,
                        'audition': False, 'cancelled': False, 'timed_out': False, 'leases': 0, 'recovered': True}
                if (folder / 'dialogue.mp3').exists():
                    if _pid_alive(value.get('pid')):
                        item['orphan_pid_pending'] = True
                        self._unknown.add(folder.name)
                    self.items[folder.name] = item
                    self._fact(folder.name, item)
                elif _pid_alive(value.get('pid')):
                    item['orphan_pid_pending'] = True
                    self.items[folder.name] = item
                    self._unknown.add(folder.name)
                else:
                    self._clean_auxiliary(item)
                    (folder / 'recovery.json').unlink()
                    folder.rmdir()
            except (OSError, ValueError, TypeError, KeyError):
                self._unknown.add(folder.name)

    def recovery_results(self):
        with self.lock:
            results = []
            for ident, item in tuple(self.items.items()):
                if not item.get('persistent') or item.get('process') is not None and item['process'].poll() is None: continue
                if item.get('orphan_pid_pending'):
                    if _pid_alive(item.get('pid')): continue
                    item['orphan_pid_pending'] = False
                    self._unknown.discard(ident)
                if not (item['folder'] / 'dialogue.mp3').exists():
                    if item.get('recovered'):
                        try:self.release(ident)
                        except (ValueError,OSError):self._unknown.add(ident)
                    continue
                try: results.append(self._fact(ident, item))
                except (OSError, ValueError): self._unknown.add(ident)
            return results

    def recovery_safety(self):
        with self.lock:
            results = self.recovery_results()
            return {'unknown': bool(self._unknown), 'pending': bool(self._unknown), 'unsaved': bool(results or self._unknown),
                    'results': results, 'unknown_count': len(self._unknown)}

    def open_result(self, ident, kind='audio', *, recovery=False):
        names = {'audio': 'dialogue.mp3', 'normalized': 'normalized.json', 'timeline': 'timeline.json'}
        with self.lock:
            item = self.items[ident]
            if item.get('orphan_pid_pending'): raise InputError('先前合成进程状态尚未确认。')
            if kind not in names or not recovery and self.snapshot(ident)['status'] != 'done': raise KeyError('结果尚未完成。')
            if kind == 'audio': self._fact(ident, item)
            target = item['folder'] / names[kind]
            before = target.lstat()
            fd = os.open(target, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0))
            handle = os.fdopen(fd, 'rb'); actual = os.fstat(fd)
            if not _regular(actual) or (before.st_dev, before.st_ino) != (actual.st_dev, actual.st_ino):
                handle.close(); raise InputError('合成结果身份已变化。')
            item['leases'] += 1
            return ResultLease(self, ident, handle, actual.st_size, item['filename'] if kind == 'audio' else names[kind], item.get('fingerprint') if kind == 'audio' else None, item.get('sha256') if kind=='audio' else None, target)

    def recovery_context(self, ident):
        with self.lock:
            value=self.items[ident].get('context')
            return dict(value) if value is not None else None

    def release(self, ident):
        with self.lock:
            item = self.items[ident]
            if item['leases'] or item.get('orphan_pid_pending') or item.get('process') is not None and item['process'].poll() is None:
                raise InputError('合成进程或结果读取尚未完成，不能释放。')
            if item.get('termination_uncertain'): raise InputError('无法确认合成进程完全停止。')
            if item.get('timer'): item['timer'].cancel()
            shutil.rmtree(item['folder'])
            del self.items[ident]
            self._unknown.discard(ident)
            self.request_ids = {key: value for key, value in self.request_ids.items() if value != ident}

    def start(self, script, request_id=None, batch_owner=None, fingerprint=None, audition=False, filename='audition.mp3', recovery_context=None):
        import hashlib
        from .output_names import valid_output_name
        if not audition and not valid_output_name(filename):
            raise InputError('输出文件名无效。')
        binding = hashlib.sha256(json.dumps([script, fingerprint, filename, audition], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        with self.lock:
            if self._closed: raise InputError('合成服务已关闭。')
            if self._batch_owner and batch_owner != self._batch_owner:
                raise InputError("已有批次正在合成，请等待完成或先取消。")
            if request_id is not None:
                if not isinstance(request_id, str) or not 8 <= len(request_id) <= 100 or not all(c.isalnum() or c in "-_" for c in request_id):
                    raise InputError("请求标识无效，请刷新页面。")
                if request_id in self.request_ids:
                    old_id = self.request_ids[request_id]
                    if old_id in self.items:
                        if self.items[old_id]['request_binding'] != binding:
                            raise InputError('同一请求标识的内容或文件名发生变化，请重新生成请求。')
                        return self.snapshot(old_id)
            self.ensure_available(batch_owner)
            audition_ids = [key for key, item in self.items.items() if item.get("audition")]
            victim = next((key for key in audition_ids if not self.items[key]['leases'] and (self.items[key].get('process') is None or self.items[key]['process'].poll() is not None)), None) if audition and len(audition_ids) >= 3 else None
            if audition and len(audition_ids) >= 3 and victim is None:
                raise InputError('试听结果仍被读取，请等待或关闭播放器。')
            production_ids = [key for key, item in self.items.items() if not item.get("audition")]
            if not audition and len(production_ids) >= 30:
                victim = next((key for key in production_ids if not self.items[key]['leases'] and not (self.items[key]['folder']/'dialogue.mp3').exists() and (self.items[key].get('process') is None or self.items[key]['process'].poll() is not None)), None)
                if victim is None: raise InputError('完整合成结果缓存已达上限，请先保存或明确清理后再生成。')
            if victim is not None:
                self.release(victim)
            persistent = self._persistent_root is not None and not audition and recovery_context is not None
            if persistent:
                import re
                if not isinstance(fingerprint, str) or not re.fullmatch('[a-f0-9]{64}', fingerprint): raise InputError('持久结果指纹无效。')
                if recovery_context is not None and (not isinstance(recovery_context, dict) or set(recovery_context) != {'workspace_id','item_id'} or any(not isinstance(value,str) or not re.fullmatch('[a-f0-9]{32}',value) for value in recovery_context.values())): raise InputError('持久结果归属无效。')
            ident = uuid.uuid4().hex
            folder = (self._persistent_root if persistent else self.root) / ident
            folder.mkdir(mode=0o700)
            item = {'folder':folder,'process':None,'timer':None,'total':len(script['lines']),
                    'fingerprint':fingerprint,'filename':filename,'request_binding':binding,'audition':audition,
                    'started':time.monotonic(),'cancelled':False,'timed_out':False,'persistent':persistent,
                    'context':recovery_context,'leases':0}
            self.items[ident] = item
            try:
                (folder / "normalized.json").write_text(json.dumps(script, ensure_ascii=False, indent=2), encoding="utf-8")
                self._persist(ident,item)
                with open(folder / "process.log", "wb") as log:
                    item['process'] = subprocess.Popen(
                        ([sys.executable, "--worker", str(folder)] if getattr(sys, "frozen", False) else [sys.executable, "-m", "web_dialogue.worker", str(folder)]), cwd=str(ROOT),
                        stdout=log, stderr=log, start_new_session=(os.name != "nt"),
                        env={**os.environ, "PYTHONUNBUFFERED": "1"},
                    )
                self._persist(ident,item)
            except BaseException:
                # A failed PID commit must not leave an unmonitored owned child.
                # Complete results survive publication/metadata failures.
                try:self.kill(item)
                except (OSError,subprocess.TimeoutExpired):item['termination_uncertain']=True
                if not self.result_available(ident) and not item.get('termination_uncertain'):
                    try:self.release(ident)
                    except (ValueError,OSError):self._unknown.add(ident)
                raise
            timer = threading.Timer(self.timeout_seconds, self.expire, args=(ident,))
            timer.daemon = True
            self.items[ident]["timer"] = timer
            if request_id:
                self.request_ids[request_id] = ident
            timer.start()
            return self.snapshot(ident)

    def expire(self, ident):
        with self.lock:
            item = self.items.get(ident)
            if item and item.get("process") is not None and item["process"].poll() is None:
                item["timed_out"] = True
                self.kill(item)

    def kill(self, item):
        proc = item["process"]
        if proc is None: return
        if proc.poll() is None:
            try:
                if os.name != "nt":
                    os.killpg(proc.pid, signal.SIGTERM)
                else:
                    # Frozen one-file builds have a bootloader child: terminate the
                    # process tree we started, not unrelated app processes.
                    taskkill = Path(os.environ.get("SystemRoot", "C:\\Windows")) / "System32" / "taskkill.exe"
                    try:
                        killed = subprocess.run([str(taskkill), "/PID", str(proc.pid), "/T", "/F"],
                                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5, check=False)
                        if killed.returncode != 0 and proc.poll() is None:
                            item["termination_uncertain"] = True
                            proc.terminate()
                    except (OSError, subprocess.TimeoutExpired):
                        item["termination_uncertain"] = True
                        proc.terminate()
                proc.wait(timeout=3)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                if proc.poll() is None:
                    if os.name != "nt":
                        os.killpg(proc.pid, signal.SIGKILL)
                    else:
                        proc.kill()
                    proc.wait(timeout=3)

    def snapshot(self, ident):
        with self.lock:
            if ident not in self.items:
                raise KeyError("任务不存在，或服务器已重启。")
            item = self.items[ident]
            result = {"id": ident, "status": "queued", "completed": 0, "total": item["total"], "filename": item['filename']}
            if item.get('recovered') or item.get('result_validated') and (item.get('process') is None or item['process'].poll() is not None):
                result.update(status='done',completed=item['total'])
            status_file = item["folder"] / "worker_status.json"
            if status_file.exists():
                try:
                    result.update(json.loads(status_file.read_text(encoding="utf-8")))
                except (OSError, ValueError):
                    pass  # atomic writes; a transient read never means success
            if item.get("termination_uncertain"):
                result.update(status="error", error="已停止启动器，但无法确认后台完全退出。请关闭应用后重试。")
            elif item["cancelled"]:
                result["status"] = "cancelled"
            elif item["timed_out"]:
                result.update(status="error", error="任务超过 15 分钟，已停止。请拆分较长对话后再试。")
            elif item.get("process") is not None and item["process"].poll() is not None and result["status"] not in {"done", "error"}:
                result.update(status="error", error="合成进程意外结束，请检查依赖或重新运行。")
            if result["status"] == "done":
                if not (item["folder"] / "dialogue.mp3").is_file():
                    result.update(status="error", error="输出文件不存在，不能认定合成成功。")
                else:
                    result["download_url"] = f"/api/jobs/{ident}/audio"
                    result["normalized_url"] = f"/api/jobs/{ident}/normalized"
                    result["timeline_url"] = f"/api/jobs/{ident}/timeline"
            if item.get("fingerprint"):
                result["fingerprint"] = item["fingerprint"]
            return result

    def cancel(self, ident):
        with self.lock:
            item = self.items.get(ident)
            if item is None:
                raise KeyError("任务不存在。")
            if self.snapshot(ident)["status"] in {"queued", "running"}:
                item["cancelled"] = True
                if item.get('timer'): item["timer"].cancel()
                self.kill(item)
                # A worker may have atomically published complete audio just
                # before cancellation. It remains owned until an explicit
                # successful transfer/release; never delete it here.
            return self.snapshot(ident)

    def close(self):
        with self.lock:
            if self._closed: return
            if any(item['leases'] for item in self.items.values()):
                raise InputError('合成结果仍在读取，暂不能关闭结果缓存。')
            self._closing = True
            errors=[]
            for item in self.items.values():
                if item.get('timer'): item["timer"].cancel()
                try:self.kill(item)
                except (OSError,subprocess.SubprocessError):errors.append(item)
            if errors:
                raise InputError('部分合成进程未能停止；缓存仍保留，请重试关闭。')
            for ident, item in tuple(self.items.items()):
                if item.get('persistent'):
                    if (item['folder']/'dialogue.mp3').exists():
                        try: self._fact(ident,item)
                        except (ValueError,OSError): self._unknown.add(ident)
                    elif not item.get('termination_uncertain'):
                        self.release(ident)
            self.temp.cleanup()
            self._closed = True

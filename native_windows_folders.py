"""Native-only folder capabilities, private preferences and verified output receipts."""
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
import threading
import urllib.request
import uuid
from web_dialogue.output_names import valid_output_name

MAX_AUDIO = 256 * 1024 * 1024
VOICES = {'zh-CN-XiaoxiaoNeural','zh-CN-XiaoyiNeural','zh-CN-YunxiNeural','zh-CN-YunjianNeural','zh-CN-YunxiaNeural','zh-CN-YunyangNeural'}
AUDIO_ROUTE = re.compile(r'/api/(?:jobs/[a-f0-9]{32}/audio|batches/[a-f0-9]{32}/audio/[0-9]{3})')
FP = re.compile(r'[a-f0-9]{64}')


def _regular(info):
    return stat.S_ISREG(info.st_mode) and not (getattr(info, 'st_file_attributes', 0) & 0x400)


def _directory(path):
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or path.is_symlink() or getattr(info, 'st_file_attributes', 0) & 0x400:
        raise ValueError('目录不可用')
    return (info.st_dev, info.st_ino)


def _read_regular(path, maximum):
    before = path.lstat()
    if not _regular(before) or before.st_size > maximum: raise ValueError('文件类型或大小不受支持')
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0))
    with os.fdopen(fd, 'rb') as handle:
        actual = os.fstat(handle.fileno())
        if not _regular(actual) or (before.st_dev, before.st_ino) != (actual.st_dev, actual.st_ino):
            raise ValueError('文件已变化')
        data = handle.read(maximum + 1)
        if len(data) > maximum: raise ValueError('文件过大')
    return data


class NativeFolderMixin:
    def _setup_folders(self, storage=None):
        self._folder_lock = threading.RLock()
        self._caps = {}
        self._selected = {'input': None, 'output': None}
        self._prefs = {'voices': {}, 'rate': 1, 'pause_ms': 250, 'theme': 'light', 'auto_voice': True, 'manual_user_voice': False, 'paths': {}, 'receipts': {}, 'exports': {}}
        self._prefs_path = Path(storage) if storage else Path(os.environ.get('LOCALAPPDATA', str(Path.home() / 'AppData' / 'Local'))) / 'DialogueTTS' / 'preferences.json'
        try:
            loaded = json.loads(_read_regular(self._prefs_path, 4 * 1024 * 1024))
            if not isinstance(loaded, dict): raise ValueError('设置文件无效')
            if loaded.get('version') == 1:
                for key in self._prefs:
                    if isinstance(loaded.get(key), (int,float) if key == 'rate' else type(self._prefs[key])): self._prefs[key] = loaded[key]
                if 'manual_user_voice' not in loaded and self._prefs['voices'].get('USER') not in (None, 'zh-CN-XiaoyiNeural'):
                    self._prefs['manual_user_voice'] = True; self._prefs['auto_voice'] = False
                self._validate_settings(self._prefs)
        except (OSError, ValueError, TypeError):
            self._prefs = {'voices': {}, 'rate': 1, 'pause_ms': 250, 'theme': 'light', 'auto_voice': True, 'manual_user_voice': False, 'paths': {}, 'receipts': {}, 'exports': {}}
        if not hasattr(self, '_workspace_access'): self._workspace_access = None
        if not hasattr(self, '_owns_workspace_access'): self._owns_workspace_access = False
        if self._workspace_access is None and (self._prefs['receipts'] or self._prefs['exports']):
            from native_workspace import NativeWorkspaceAccess
            self._workspace_access = NativeWorkspaceAccess(self._prefs_path.parent)
            self._owns_workspace_access = True
        if self._workspace_access is not None: self._workspace_access.migrate_preferences(self._prefs_path)
        # Receipts now live in the indexed ledger, never in growing preferences JSON.
        self._prefs['receipts'] = {}; self._prefs['exports'] = {}
        for kind in ('input', 'output'):
            path = self._prefs['paths'].get(kind)
            if isinstance(path, str):
                try: self._selected[kind] = self._authorize(Path(path), kind)
                except (OSError, ValueError): self._selected[kind] = {'folder_id': '', 'path': path, 'valid': False}

    def _validate_settings(self, values):
        voices = values.get('voices', {})
        if not isinstance(voices, dict) or any(k not in {'AGENT','USER'} or not isinstance(v,str) or not re.fullmatch(r'[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,16}){1,4}-[A-Za-z0-9]{1,50}Neural',v) for k,v in voices.items()): raise ValueError('音色无效')
        if values.get('theme', 'light') not in ('light', 'dark'): raise ValueError('主题无效')
        if not isinstance(values.get('auto_voice', True), bool) or not isinstance(values.get('manual_user_voice', False), bool): raise ValueError('音色匹配设置无效')
        rate = values.get('rate', 1); pause = values.get('pause_ms', 250)
        if isinstance(rate, bool) or not isinstance(rate, (int,float)) or not math.isfinite(rate) or not .5 <= rate <= 2: raise ValueError('语速无效')
        if isinstance(pause, bool) or not isinstance(pause, int) or not 0 <= pause <= 3000: raise ValueError('停顿无效')

    def _persist(self):
        self._prefs_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, name = tempfile.mkstemp(prefix='.prefs-', dir=self._prefs_path.parent)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as out:
                json.dump({'version':1, **self._prefs}, out, ensure_ascii=False)
                out.flush(); os.fsync(out.fileno())
            os.replace(name, self._prefs_path)
        finally:
            Path(name).unlink(missing_ok=True)

    def _authorize(self, path, kind):
        path = path.absolute(); identity = _directory(path)
        ident = uuid.uuid4().hex
        self._caps[ident] = (path, kind, identity)
        return {'folder_id': ident, 'path': str(path), 'valid': True}

    def _cap(self, ident, kind=None):
        item = self._caps.get(ident) if isinstance(ident, str) else None
        if item is None and kind in {'input', 'output'}:
            return self._ensure_workspace_access().directory(ident, kind)
        if not item or (kind and item[1] != kind) or _directory(item[0]) != item[2]: raise ValueError('请选择有效目录')
        return item[0]

    def _native_workspace_request(self, route, payload):
        token = getattr(self, '_native_token', None)
        if not token: raise ValueError('原生目录服务未就绪')
        request = urllib.request.Request(self._base + route, data=json.dumps(payload).encode('utf-8'),
            headers={'Content-Type': 'application/json', 'X-Native-Token': token})
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs): return None
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        with opener.open(request, timeout=30) as response:
            data = response.read(1024 * 1024 + 1)
            if len(data) > 1024 * 1024: raise ValueError('原生响应过大')
            result = json.loads(data)
            if not isinstance(result, dict) or result.get('ok') is False: raise ValueError('原生目录授权失败')
            return result

    def _choose_workspace(self, kind):
        import webview
        choice = self._window.create_file_dialog(webview.FileDialog.FOLDER)
        if not choice: return {'cancelled': True}
        result = self._native_workspace_request('/api/native/folders', {'kind': kind, 'path': str(Path(choice[0]).absolute())})
        return {'ok': True, **result}

    def choose_workspace_input(self):
        try: return self._choose_workspace('input')
        except (OSError, ValueError, TypeError): return {'ok': False, 'error': '输入目录授权失败，请重新选择'}

    def choose_workspace_output(self):
        try: return self._choose_workspace('output')
        except (OSError, ValueError, TypeError): return {'ok': False, 'error': '输出目录授权失败，请重新选择'}

    def restore_workspace_access(self, workspace_id):
        try:
            if not isinstance(workspace_id, str) or not re.fullmatch(r'[a-f0-9]{32}', workspace_id): raise ValueError('任务标识无效')
            return self._native_workspace_request('/api/native/workspaces/restore', {'workspace_id': workspace_id})
        except (OSError, ValueError, TypeError): return {'ok': False, 'error': '目录授权未恢复，请重新选择原目录'}

    def _ledger_grant(self, ident, kind='output'):
        path = self._cap(ident, kind)
        return self._ensure_workspace_access().register(path, kind)['folder_id']

    def _ensure_workspace_access(self):
        if self._workspace_access is None:
            from native_workspace import NativeWorkspaceAccess
            self._workspace_access = NativeWorkspaceAccess(self._prefs_path.parent)
            self._owns_workspace_access = True
        self._workspace_access.migrate_preferences(self._prefs_path)
        return self._workspace_access

    def _close_folders(self):
        """Only the creator closes a ledger; a desktop bridge borrows the server's."""
        with self._folder_lock:
            if self._owns_workspace_access and self._workspace_access is not None:
                self._workspace_access.close()
                self._workspace_access = None
                self._owns_workspace_access = False

    def check_output_space(self, folder_id):
        try:
            with self._folder_lock:
                path = self._cap(folder_id, 'output')
                fd, name = tempfile.mkstemp(prefix='.dialogue-check-', dir=path)
                try: os.close(fd)
                finally: Path(name).unlink(missing_ok=True)
                try: free = shutil.disk_usage(path).free
                except OSError: free = None
                return {'ok': True, 'writable': True, 'free_bytes': free}
        except (OSError, ValueError):
            return {'ok': False, 'error': '无法写入结果文件夹，请重新选择。'}

    def get_preferences(self):
        with self._folder_lock:
            for kind in ('input','output'):
                item = self._selected[kind]
                if item and item.get('valid'):
                    try: self._cap(item['folder_id'], kind)
                    except (OSError, ValueError): item['valid'] = False
            return {'ok': True, 'input_folder': self._selected['input'], 'output_folder': self._selected['output'],
                    **{k: self._prefs[k] for k in ('voices','rate','pause_ms','theme','auto_voice','manual_user_voice')}}

    def set_preferences(self, values):
        try:
            if not isinstance(values, dict) or set(values) - {'voices','rate','pause_ms','theme','auto_voice','manual_user_voice'}: raise ValueError('设置无效')
            if 'voices' in values and (not isinstance(values['voices'], dict) or any(not isinstance(v, str) or v not in VOICES for v in values['voices'].values())):
                raise ValueError('请重新选择内置普通话音色')
            with self._folder_lock:
                merged = {**self._prefs, **values}; self._validate_settings(merged)
                previous = self._prefs; self._prefs = merged
                try: self._persist()
                except Exception: self._prefs = previous; raise
            return {'ok': True}
        except (OSError, ValueError, TypeError): return {'ok':False, 'error':'设置未能保存'}

    def _choose(self, kind):
        import webview
        choice = self._window.create_file_dialog(webview.FileDialog.FOLDER)
        if not choice: return {'cancelled': True}
        with self._folder_lock:
            record = self._authorize(Path(choice[0]), kind)
            files = self._scan(record['folder_id']) if kind == 'input' else None
            self._prefs['paths'][kind] = record['path']; self._persist()
            self._selected[kind] = record
            return {'ok': True, **record, **({'files':files} if files is not None else {})}

    def choose_input_directory(self):
        try: return self._choose('input')
        except (OSError, ValueError, UnicodeError): return {'ok':False,'error':'目录读取失败，请检查 XML 数量、大小与权限'}

    def choose_output_directory(self):
        try: return self._choose('output')
        except (OSError, ValueError): return {'ok':False,'error':'输出目录不可用'}

    def _scan(self, ident):
        path = self._cap(ident, 'input'); names = []
        with os.scandir(path) as entries:
            for i, entry in enumerate(entries):
                if i >= 10000: raise ValueError('目录项目过多')
                if not entry.name.lower().endswith('.xml'): continue
                info = entry.stat(follow_symlinks=False)
                if not _regular(info): raise ValueError('不读取链接或特殊 XML 文件')
                names.append(entry.name)
                if len(names) > 100: raise ValueError('XML 文件过多')
        total = 0; files = []
        for name in sorted(names):
            raw = _read_regular(path / name, 2 * 1024 * 1024); total += len(raw)
            if total > 20 * 1024 * 1024: raise ValueError('XML 总大小过大')
            files.append({'name':name, 'content':raw.decode('utf-8-sig')})
        self._cap(ident, 'input')
        return files

    def refresh_input_directory(self, folder_id):
        try:
            with self._folder_lock:
                path = self._cap(folder_id, 'input')
                return {'ok':True,'folder_id':folder_id,'path':str(path),'files':self._scan(folder_id)}
        except (OSError, ValueError, UnicodeError): return {'ok':False,'error':'刷新失败，请检查 XML 文件与目录权限'}

    def reveal_output(self, folder_id):
        try:
            if not isinstance(folder_id, str): raise ValueError('目录授权无效')
            if folder_id in self._caps: path = self._cap(folder_id, 'output')
            else:
                result = self._native_workspace_request('/api/native/folders/info', {'folder_id': folder_id, 'kind': 'output'})
                path = Path(result['path'])
            os.startfile(str(path), 'explore'); return {'ok': True}
        except (OSError, ValueError, KeyError, TypeError): return {'ok':False,'error':'无法打开输出目录'}

    def _receipt(self, folder, record):
        if not isinstance(record, dict): return None
        if not isinstance(record.get('fingerprint'),str) or not FP.fullmatch(record['fingerprint']): return None
        name = record.get('name')
        if not isinstance(name,str) or not (valid_output_name(name) or re.fullmatch(r'dialogue(?:-[0-9]{3})?(?: \([0-9]+\))?\.mp3', name)): return None
        try:
            data = _read_regular(folder / name, MAX_AUDIO)
            if len(data) != record.get('size') or hashlib.sha256(data).hexdigest() != record.get('sha256'): return None
            ident = uuid.uuid4().hex; self._saved[ident] = folder / name
            return {'save_id':ident,'name':name,'path':str(folder / name),'fingerprint':record['fingerprint']}
        except (OSError,ValueError,KeyError): return None

    def check_completed(self, folder_id, fingerprints, expected_names=None):
        try:
            if not isinstance(fingerprints, list) or len(fingerprints) > 50 or any(not isinstance(f, str) or not FP.fullmatch(f) for f in fingerprints): raise ValueError('指纹无效')
            if expected_names is not None and (not isinstance(expected_names, dict) or set(expected_names) != set(fingerprints) or any(not valid_output_name(n) for n in expected_names.values())): raise ValueError('预期名称无效')
            with self._folder_lock:
                grant = self._ledger_grant(folder_id)
                results, conflicts = [], []
                for fp in fingerprints:
                    names = [expected_names[fp]] if expected_names is not None else self._workspace_access.names_for_fingerprint(grant, fp)
                    for name in names:
                        item = self._workspace_access.verify_output(grant, name, fp)
                        if item['state'] == 'saved':
                            self._saved[item['save_id']] = Path(item['path'])
                            results.append(item); break
                        if item['state'] == 'conflict' and expected_names is not None: conflicts.append(item)
                return {'ok': True, 'completed': results, 'conflicts': conflicts}
        except (OSError, ValueError, TypeError): return {'ok': False, 'error': '无法核对已有输出'}

    def save_to_output(self, resource_path, folder_id):
        if not isinstance(resource_path, str) or not AUDIO_ROUTE.fullmatch(resource_path): return {'ok': False, 'error': '下载地址无效'}
        temporary = None
        try:
            with self._folder_lock:
                folder = self._cap(folder_id, 'output')
                class NoRedirect(urllib.request.HTTPRedirectHandler):
                    def redirect_request(self, *args, **kwargs): return None
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
                with opener.open(self._base + resource_path, timeout=30) as response:
                    fp = response.headers.get('X-Dialogue-Fingerprint', '')
                    match = re.fullmatch(r'(?:attachment|inline); filename="([0-9]+\.mp3)"', response.headers.get('Content-Disposition', ''))
                    if not match or not valid_output_name(match[1]) or not FP.fullmatch(fp): raise ValueError('输出响应无效')
                    name = match[1]
                    if os.path.lexists(folder / name) and self._workspace_access is None and not self._prefs_path.exists() and not (self._prefs_path.parent / 'native-workspace.sqlite3').exists():
                        return {'ok': False, 'conflict': True, 'error': '同名文件已存在且无法核实，请处理冲突后重试保存'}
                    grant = self._ledger_grant(folder_id)
                    prior = self._workspace_access.verify_output(grant, name, fp)
                    if prior['state'] == 'saved':
                        self._saved[prior['save_id']] = Path(prior['path'])
                        return {'ok': True, **prior, 'resource_path': resource_path}
                    if prior['state'] == 'conflict': return {'ok': False, 'conflict': True, 'error': prior['error']}
                    size = int(response.headers.get('Content-Length', '-1'))
                    if response.headers.get_content_type() != 'audio/mpeg' or not 0 < size <= MAX_AUDIO: raise ValueError('输出响应无效')
                    fd, temporary = tempfile.mkstemp(prefix='.dialogue-download-', dir=self._prefs_path.parent)
                    count = 0
                    with os.fdopen(fd, 'wb') as out:
                        while True:
                            block = response.read(65536)
                            if not block: break
                            count += len(block)
                            if count > size: raise ValueError('输出过大')
                            out.write(block)
                        out.flush(); os.fsync(out.fileno())
                    if count != size: raise ValueError('输出未完整下载')
                result = self._workspace_access.save_audio(grant, Path(temporary), name, fp)
                self._saved[result['save_id']] = Path(result['path'])
                result['resource_path'] = resource_path
                try: self._window.evaluate_js('window.dialogueNativeSaved && window.dialogueNativeSaved(' + json.dumps(result, ensure_ascii=True) + ')')
                except Exception: pass
                return {'ok': True, **result}
        except FileExistsError: return {'ok': False, 'conflict': True, 'error': '同名文件已存在，请处理冲突后重试保存'}
        except (OSError, ValueError, TypeError): return {'ok': False, 'error': '保存失败，请检查输出目录后重试'}
        finally:
            if temporary: Path(temporary).unlink(missing_ok=True)

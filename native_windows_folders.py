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

MAX_AUDIO = 256 * 1024 * 1024
VOICES = {'zh-CN-XiaoxiaoNeural','zh-CN-XiaoyiNeural','zh-CN-YunxiNeural','zh-CN-YunjianNeural'}
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
        self._prefs = {'voices': {}, 'rate': 1, 'pause_ms': 250, 'paths': {}, 'receipts': {}, 'exports': {}}
        self._prefs_path = Path(storage) if storage else Path(os.environ.get('LOCALAPPDATA', str(Path.home() / 'AppData' / 'Local'))) / 'DialogueTTS' / 'preferences.json'
        try:
            loaded = json.loads(_read_regular(self._prefs_path, 4 * 1024 * 1024))
            if not isinstance(loaded, dict): raise ValueError('设置文件无效')
            if loaded.get('version') == 1:
                for key in self._prefs:
                    if isinstance(loaded.get(key), (int,float) if key == 'rate' else type(self._prefs[key])): self._prefs[key] = loaded[key]
                self._validate_settings(self._prefs)
        except (OSError, ValueError, TypeError):
            self._prefs = {'voices': {}, 'rate': 1, 'pause_ms': 250, 'paths': {}, 'receipts': {}, 'exports': {}}
        for kind in ('input', 'output'):
            path = self._prefs['paths'].get(kind)
            if isinstance(path, str):
                try: self._selected[kind] = self._authorize(Path(path), kind)
                except (OSError, ValueError): self._selected[kind] = {'folder_id': '', 'path': path, 'valid': False}

    def _validate_settings(self, values):
        voices = values.get('voices', {})
        if not isinstance(voices, dict) or any(k not in {'AGENT','USER'} or v not in VOICES for k,v in voices.items()): raise ValueError('音色无效')
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
        if not item or (kind and item[1] != kind) or _directory(item[0]) != item[2]: raise ValueError('请选择有效目录')
        return item[0]

    def get_preferences(self):
        with self._folder_lock:
            for kind in ('input','output'):
                item = self._selected[kind]
                if item and item.get('valid'):
                    try: self._cap(item['folder_id'], kind)
                    except (OSError, ValueError): item['valid'] = False
            return {'ok': True, 'input_folder': self._selected['input'], 'output_folder': self._selected['output'],
                    **{k: self._prefs[k] for k in ('voices','rate','pause_ms')}}

    def set_preferences(self, values):
        try:
            if not isinstance(values, dict) or set(values) - {'voices','rate','pause_ms'}: raise ValueError('设置无效')
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
        try: os.startfile(str(self._cap(folder_id, 'output')), 'explore'); return {'ok':True}
        except (OSError, ValueError): return {'ok':False,'error':'无法打开输出目录'}

    def _receipt(self, folder, record):
        if not isinstance(record, dict): return None
        if not isinstance(record.get('fingerprint'),str) or not FP.fullmatch(record['fingerprint']): return None
        name = record.get('name')
        if not isinstance(name,str) or not re.fullmatch(r'dialogue(?:-[0-9]{3})?(?: \([0-9]+\))?\.mp3', name): return None
        try:
            data = _read_regular(folder / name, MAX_AUDIO)
            if len(data) != record.get('size') or hashlib.sha256(data).hexdigest() != record.get('sha256'): return None
            ident = uuid.uuid4().hex; self._saved[ident] = folder / name
            return {'save_id':ident,'name':name,'path':str(folder / name),'fingerprint':record['fingerprint']}
        except (OSError,ValueError,KeyError): return None

    def check_completed(self, folder_id, fingerprints):
        try:
            if not isinstance(fingerprints,list) or len(fingerprints)>50 or any(not isinstance(f,str) or not FP.fullmatch(f) for f in fingerprints): raise ValueError('指纹无效')
            with self._folder_lock:
                folder = self._cap(folder_id,'output'); records = self._prefs['receipts'].get(str(folder), {})
                if not isinstance(records, dict): records = {}
                results = [self._receipt(folder, records.get(fp)) for fp in fingerprints]
                return {'ok':True,'completed':[r for r in results if r]}
        except (OSError,ValueError,TypeError): return {'ok':False,'error':'无法核对已有输出'}

    def save_to_output(self, resource_path, folder_id):
        if not isinstance(resource_path,str) or not AUDIO_ROUTE.fullmatch(resource_path): return {'ok':False,'error':'下载地址无效'}
        temporary = None; target = None; recorded = False
        try:
            with self._folder_lock:
                folder = self._cap(folder_id,'output'); key = str(folder)+'|'+resource_path
                prior = self._receipt(folder, self._prefs['exports'].get(key))
                if prior: return {'ok':True, **prior}
                class NoRedirect(urllib.request.HTTPRedirectHandler):
                    def redirect_request(self,*args,**kwargs): return None
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
                with opener.open(self._base+resource_path,timeout=30) as response:
                    fp = response.headers.get('X-Dialogue-Fingerprint','')
                    size = int(response.headers.get('Content-Length','-1'))
                    if not FP.fullmatch(fp) or response.headers.get_content_type() != 'audio/mpeg' or not 0<size<=MAX_AUDIO: raise ValueError('输出响应无效')
                    fd,temporary = tempfile.mkstemp(prefix='.dialogue-output-',dir=folder)
                    digest = hashlib.sha256(); count = 0
                    with os.fdopen(fd,'wb') as out:
                        while True:
                            block = response.read(65536)
                            if not block: break
                            count += len(block)
                            if count>size: raise ValueError('输出过大')
                            digest.update(block);out.write(block)
                        out.flush();os.fsync(out.fileno())
                    if count != size: raise ValueError('输出未完整下载')
                from web_dialogue.mp3_frames import parse_frames
                if not parse_frames(Path(temporary).read_bytes()): raise ValueError('音频为空')
                self._cap(folder_id,'output')
                stem = 'dialogue-'+resource_path.rsplit('/',1)[1] if '/batches/' in resource_path else 'dialogue'
                for n in range(10000):
                    candidate = folder/(stem+('' if n==0 else f' ({n})')+'.mp3')
                    try:
                        # Exclusive creation works on NTFS and removable FAT/exFAT alike.
                        # Until fsync + receipt, a partially written file is never considered completed.
                        with candidate.open('xb') as output:
                            target = candidate
                            with open(temporary,'rb') as source: shutil.copyfileobj(source,output)
                            output.flush(); os.fsync(output.fileno())
                        break
                    except FileExistsError: continue
                if target is None: raise ValueError('同名文件过多')
                record = {'name':target.name,'size':size,'sha256':digest.hexdigest(),'fingerprint':fp}
                receipts = self._prefs['receipts'].setdefault(str(folder),{})
                if not isinstance(receipts,dict): receipts={}; self._prefs['receipts'][str(folder)]=receipts
                receipts[fp]=record;self._prefs['exports'][key]=record
                # Bounded private receipt history; eviction only disables future skip, never deletes output.
                while len(self._prefs['exports'])>2000: del self._prefs['exports'][next(iter(self._prefs['exports']))]
                while len(receipts)>2000: del receipts[next(iter(receipts))]
                self._persist(); recorded=True
                result=self._receipt(folder,record)
                if not result: raise ValueError('保存后校验失败')
                try: self._window.evaluate_js('window.dialogueNativeSaved && window.dialogueNativeSaved('+json.dumps(result,ensure_ascii=True)+')')
                except Exception: pass
                return {'ok':True,**result}
        except (OSError,ValueError,TypeError): return {'ok':False,'error':'保存失败，请检查输出目录后重试'}
        finally:
            if temporary: Path(temporary).unlink(missing_ok=True)
            if target and not recorded: target.unlink(missing_ok=True)

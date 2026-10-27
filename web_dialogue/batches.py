"""Bounded sequential batches. Outputs contain audio only, never source metadata."""
import hashlib
import json
from pathlib import Path
import shutil
import threading
import time
import uuid
import zipfile
from .normalize import InputError
from .batch_import import preview_batch, MAX_CALLS


class DownloadLease:
    def __init__(self, manager, path, handle):
        self.manager, self.path, self.handle = manager, path, handle
        self.closed = False
    def read(self, *args): return self.handle.read(*args)
    def seek(self, *args): return self.handle.seek(*args)
    def tell(self): return self.handle.tell()
    def seekable(self): return self.handle.seekable()
    def close(self):
        if not self.closed:
            self.handle.close()
            with self.manager.lock:
                self.manager.leases[self.path] -= 1
                if not self.manager.leases[self.path]: del self.manager.leases[self.path]
            self.closed = True
    def __enter__(self): return self
    def __exit__(self, *args): self.close()


class Batches:
    def __init__(self, jobs, catalog=None):
        self.jobs = jobs
        self.catalog = catalog
        self.root = jobs.root / 'batches'
        self.root.mkdir(mode=0o700)
        self.items = {}
        self.requests = {}
        self.lock = threading.RLock()
        self.thread = None
        self.closing = False
        self.active = None
        self.leases = {}
        self.retired = []

    def start(self, payload):
        if payload.get('consent') is not True:
            raise InputError('请确认将脱敏文本发送至微软语音服务。')
        request = payload.get('request_id')
        if not isinstance(request, str) or not 8 <= len(request) <= 100 or not all(c.isalnum() or c in '-_' for c in request):
            raise InputError('批次请求标识无效。')
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        with self.jobs.lock, self.lock:
            if request in self.requests:
                ident, previous = self.requests[request]
                if previous != digest:
                    raise InputError('同一请求标识的内容发生变化，请重新生成请求。')
                return self.snapshot(ident)
            self._available()
            preview = preview_batch(payload, self.catalog)
            excluded = payload.get('excluded', [])
            if not isinstance(excluded, list) or any(not isinstance(x, str) for x in excluded):
                raise InputError('排除列表无效。')
            groups = [g for g in preview['groups'] if g['id'] not in excluded and g['status'] == 'ready']
            if not groups or len(groups) > MAX_CALLS:
                raise InputError('每批请选择 1–50 通对话。')
            if any(g['status'] != 'ready' for g in groups):
                raise InputError('请先处理或排除有错误的文件组。')
            self._reap_retired()
            if len(self.items) >= 5:
                if len(self.retired) >= 5:
                    raise InputError('旧批次文件仍被占用，请完成下载后再试。')
                old = next((key for key in self.items if not any(path.parent == self.root / key for path in self.leases)), None)
                if old is None:
                    raise InputError('历史批次仍在下载，请完成下载后再新建批次。')
                folder = self.root / old
                retired = self.root / ('retired-' + old)
                try:
                    folder.rename(retired)
                except OSError:
                    raise InputError('旧批次文件仍被占用，请完成下载后再试。')
                self.retired.append(retired)
                del self.items[old]
                self.requests = {k:v for k,v in self.requests.items() if v[0] != old}
                self._reap_retired()
            ident = uuid.uuid4().hex
            (self.root / ident).mkdir(mode=0o700)
            self.items[ident] = {'id': ident, 'status': 'queued', 'cancel': False, 'generation': 0,
                'items': [{'id': g['id'], 'name': g['name'], 'status': 'queued',
                    'completed': 0, 'total': len(g['preview']['script']['lines']),
                    'fingerprint': g['fingerprint'], 'filename': g['filename'], 'script': g['preview']['script'], 'number': preview['groups'].index(g) + 1} for i,g in enumerate(groups)]}
            self.requests[request] = (ident, digest)
            self._launch(ident)
            return self.snapshot(ident)

    def _available(self):
        if self.closing or self.active:
            raise InputError('已有批次正在处理，请先等待或取消。')
        if any(self.jobs.snapshot(i)['status'] in {'queued', 'running'} for i in self.jobs.items):
            raise InputError('已有单通对话正在合成，请先等待或取消。')

    def _launch(self, ident):
        self.active = ident
        self.jobs.batch_active = ident
        self.thread = threading.Thread(target=self._run, args=(ident,), daemon=True)
        self.thread.start()

    def _run(self, ident):
        batch = self.items[ident]
        try:
            with self.lock:
                batch['status'] = 'running'
            for row in batch['items']:
                with self.lock:
                    if batch['cancel'] or self.closing:
                        break
                    if row['status'] != 'queued':
                        continue
                    row['status'] = 'running'
                try:
                    job = self.jobs.start(row['script'], batch_owner=ident, fingerprint=row['fingerprint'], filename=row['filename'])
                    with self.lock:
                        row['job'] = job['id']
                    while True:
                        if batch['cancel'] or self.closing:
                            self.jobs.cancel(job['id'])
                        result = self.jobs.snapshot(job['id'])
                        with self.lock:
                            row['completed'] = result['completed']
                        if result['status'] not in {'queued', 'running'}:
                            break
                        time.sleep(.1)
                    with self.lock:
                        row['status'] = result['status']
                        if result['status'] == 'done':
                            source = self.jobs.items[job['id']]['folder'] / 'dialogue.mp3'
                            existing = sum(p.stat().st_size for p in (self.root / ident).glob('*.mp3'))
                            if existing + source.stat().st_size > 200 * 1024 * 1024:
                                raise InputError('批次输出超过 200MB，请缩小批次。')
                            shutil.copyfile(source, self.root / ident / f'{row["number"]:03d}.mp3')
                        elif result.get('error'):
                            row['error'] = result['error']
                except Exception:
                    # Never expose exception text: libraries may include submitted text/URLs.
                    with self.lock:
                        row.update(status='error', error='此通合成失败；其他对话继续，可单独重试失败项。')
                finally:
                    with self.lock:
                        row.pop('job', None)
            with self.lock:
                for row in batch['items']:
                    if row['status'] == 'queued':
                        row['status'] = 'cancelled'
                batch['status'] = 'cancelled' if batch['cancel'] else ('done' if all(r['status'] == 'done' for r in batch['items']) else 'partial')
        finally:
            with self.jobs.lock, self.lock:
                self.active = None
                self.jobs.batch_active = None

    def audio_fingerprint(self, ident, number):
        with self.lock:
            row = next((r for r in self.items[ident]['items'] if f"{r['number']:03d}" == number), None)
            if row is None or row['status'] != 'done':
                raise KeyError('Audio unavailable')
            return row['fingerprint']

    def snapshot(self, ident):
        with self.lock:
            batch = self.items[ident]
            rows = []
            for r in batch['items']:
                out = {k:r[k] for k in ('id','name','status','completed','total','fingerprint','filename')}
                if r.get('error'): out['error'] = r['error']
                if r['status'] == 'done':
                    out['download_url'] = f'/api/batches/{ident}/audio/{r["number"]:03d}'
                rows.append(out)
            result = {'id':ident, 'status':batch['status'], 'items':rows,
                      'completed':sum(r['status'] == 'done' for r in rows), 'total':len(rows)}
            if any(r['status'] == 'done' for r in rows) and batch['status'] not in {'running','queued'}:
                result['zip_url'] = f'/api/batches/{ident}/zip'
            return result

    def cancel(self, ident):
        with self.lock:
            batch = self.items[ident]
            batch['cancel'] = True
        # Worker observes cancellation; no lock inversion with Jobs.
        return self.snapshot(ident)

    def retry(self, ident):
        with self.jobs.lock, self.lock:
            self._available()
            batch = self.items[ident]
            failed = [r for r in batch['items'] if r['status'] == 'error']
            if not failed:
                raise InputError('没有失败项需要重试；已成功与已取消项不会重复生成。')
            batch['generation'] += 1
            for r in failed:
                r.update(status='queued', completed=0)
                r.pop('error', None)
            batch.update(status='queued', cancel=False)
            self._launch(ident)
            return self.snapshot(ident)

    def output(self, ident, number=None):
        with self.lock:
            batch = self.items[ident]
            if number is not None:
                if number not in {f'{r["number"]:03d}' for r in batch['items'] if r['status'] == 'done'}:
                    raise KeyError('音频不存在。')
                return self.root / ident / f'{number}.mp3'
            if batch['status'] in {'queued','running'}:
                raise KeyError('请等待批次停止后下载。')
            target = self.root / ident / f'dialogues-{batch["generation"]}.zip'
            if target.exists():
                return target
            for old in target.parent.glob('dialogues-*.zip'):
                if old not in self.leases:
                    try: old.unlink()
                    except OSError: pass  # Windows download/antivirus may still hold it.
            if len(list(target.parent.glob('dialogues-*.zip'))) >= 3:
                raise InputError('旧 ZIP 仍在下载或被占用，请稍后重新下载。')
            # At most three immutable ZIP generations plus 200MB audio per batch.
            with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_STORED) as archive:
                for row in batch['items']:
                    if row['status'] == 'done':
                        archive.write(self.root / ident / f'{row["number"]:03d}.mp3', row['filename'])
            return target

    def open_output(self, ident, number=None):
        with self.lock:
            target = self.output(ident, number)
            handle = target.open('rb')
            self.leases[target] = self.leases.get(target, 0) + 1
            name = 'dialogues.zip' if number is None else next(r['filename'] for r in self.items[ident]['items'] if f"{r['number']:03d}" == number)
            return DownloadLease(self, target, handle), name, target.stat().st_size

    def _reap_retired(self):
        for folder in self.retired[:]:
            try: shutil.rmtree(folder)
            except OSError: continue
            self.retired.remove(folder)

    def close(self):
        with self.lock:
            self.closing = True
            for batch in self.items.values(): batch['cancel'] = True
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(timeout=15)

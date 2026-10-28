"""Private, disk-backed XML workspaces. No XML or dialogue text is persisted.

The native capability owner is the only filesystem authority. SQLite contains
metadata, selection snapshots and recovery state; synthesis is explicitly started.
"""
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import sqlite3
import stat
import subprocess
import threading
import time
import tempfile
import uuid
from contextlib import contextmanager, nullcontext

from .batch_import import _metadata
from .normalize import InputError, VOICES
from .mp3_frames import validated_file_digest
from .output_names import pair_filename, valid_output_name
from .workspace_issues import ERROR_STATUSES, ERROR_WHERE, issue_counts, issue_where, normalize_issue

DEFAULT_SETTINGS = {'voices': {'AGENT': VOICES[0]['id'], 'USER': VOICES[1]['id']},
                    'rate': 1, 'pause_ms': 250, 'auto_voice': True, 'manual_user_voice': False}
ACTIVE = {'running', 'pausing'}
WORKSPACE_STATUSES = frozenset({'scanning','ready','running','pausing','paused','interrupted','done','cancelled','error'})
RECOVERY_PHASES = frozenset({'recovery_settings','recovery_checking','recovery_scan'})
WORKSPACE_FIELDS = frozenset({'status','phase','discovered','scanned','input_id','output_id','settings',
    'run_settings','selection_id','message','active_item','input_path','output_path','selection_query','verification_origin'})
FILTERS = {'all': '1', 'ready': "status='ready'", 'pending': "status='ready'",
           'running': "status IN ('queued','running','saving','save_pending')", 'processing': "status IN ('queued','running','saving','save_pending')",
           'error': ERROR_WHERE,
           'issues': ERROR_WHERE,
           'saved': "status='saved'", 'selected': 'selected=1'}


class Workspaces:
    def __init__(self, state_dir, jobs, native_access, catalog=None, synthesizer=None,
                 cache_limit=512 * 1024 * 1024):
        self.root = Path(state_dir) / 'workspaces'
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.cache = self.root / 'audio'
        self.cache.mkdir(exist_ok=True, mode=0o700)
        self.path = self.root / 'index.sqlite3'
        self.jobs, self.access = jobs, native_access
        self.catalog = (catalog or {}).get('voices', VOICES)
        self.synthesizer, self.cache_limit = synthesizer, cache_limit
        self.lock = threading.RLock()
        self.cache_lock = threading.RLock()
        self.cache_checks = {}
        self._recovery_lock = threading.RLock()
        self._cache_facts = {'protected':0,'unknown':0,'by_workspace':{}}
        self._source_facts = {'protected':0,'unknown':0,'by_workspace':{}}
        self.stop = threading.Event()
        self._closed = False
        self.threads = {}
        self.scan_cancel = {}
        self.active = None
        self.active_job = None
        with self.db() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS workspaces(
              id TEXT PRIMARY KEY,status TEXT,phase TEXT,revision INTEGER DEFAULT 0,
              discovered INTEGER DEFAULT 0,scanned INTEGER DEFAULT 0,input_id TEXT,
              output_id TEXT,settings TEXT,run_settings TEXT,selection_id TEXT,
              message TEXT,active_item TEXT,created REAL,input_path TEXT,output_path TEXT,selection_query TEXT);
            CREATE TABLE IF NOT EXISTS files(
              wid TEXT,name TEXT,identity TEXT,role TEXT,digest TEXT,error TEXT,
              PRIMARY KEY(wid,name));
            CREATE INDEX IF NOT EXISTS file_identity ON files(wid,identity);
            CREATE INDEX IF NOT EXISTS file_pair ON files(wid,identity,name,digest,role);
            CREATE TABLE IF NOT EXISTS items(
              wid TEXT,id TEXT PRIMARY KEY,identity TEXT,filename TEXT,status TEXT,
              error TEXT,error_code TEXT,customer_voice TEXT,voice_source TEXT,
              line_count INTEGER DEFAULT 0,fingerprint TEXT,selected INTEGER DEFAULT 0,
              override TEXT,planned INTEGER DEFAULT 0,plan_voice TEXT,revision INTEGER DEFAULT 0,saved_folder_id TEXT,source_name TEXT,audio_hash TEXT,audio_size INTEGER);
            CREATE INDEX IF NOT EXISTS item_workspace ON items(wid,status);
            CREATE INDEX IF NOT EXISTS item_issues ON items(wid,status,error_code);
            CREATE INDEX IF NOT EXISTS item_filename ON items(wid,filename);
            CREATE INDEX IF NOT EXISTS item_display ON items(wid,(filename IS NULL),filename,id);
            CREATE INDEX IF NOT EXISTS item_revision ON items(wid,revision);
            CREATE INDEX IF NOT EXISTS item_selection ON items(wid,selected);
            CREATE INDEX IF NOT EXISTS item_queue ON items(wid,planned,status,filename,id);
            CREATE TABLE IF NOT EXISTS requests(wid TEXT,request_id TEXT,digest TEXT,
              PRIMARY KEY(wid,request_id));
            ''')
            if 'saved_folder_id' not in {r[1] for r in db.execute('PRAGMA table_info(items)')}:
                db.execute('ALTER TABLE items ADD COLUMN saved_folder_id TEXT')
            if 'source_name' not in {r[1] for r in db.execute('PRAGMA table_info(items)')}:
                db.execute('ALTER TABLE items ADD COLUMN source_name TEXT')
            item_columns={r[1] for r in db.execute('PRAGMA table_info(items)')}
            for column,kind in (('audio_hash','TEXT'),('audio_size','INTEGER'),('pending_job','TEXT')):
                if column not in item_columns:db.execute('ALTER TABLE items ADD COLUMN '+column+' '+kind)
            db.execute('CREATE INDEX IF NOT EXISTS item_worker_source ON items(pending_job) WHERE pending_job IS NOT NULL')
            workspace_columns={r[1] for r in db.execute('PRAGMA table_info(workspaces)')}
            for column in ('input_path','output_path','selection_query','verification_origin'):
                if column not in workspace_columns:db.execute('ALTER TABLE workspaces ADD COLUMN '+column+' TEXT')
            db.execute("UPDATE workspaces SET phase=CASE WHEN phase='settings' THEN 'recovery_settings' WHEN phase='checking' THEN 'recovery_checking' WHEN status='scanning' THEN 'recovery_scan' ELSE 'recovery' END,status='interrupted',active_item=NULL,"
                       "message='任务已中断；核对目录后手动继续。' WHERE status IN ('scanning','running','pausing')")
            db.execute("UPDATE items SET status='queued' WHERE status='running'")
            db.execute("UPDATE items SET status='save_pending' WHERE status='saving'")
            db.execute("UPDATE items SET status='unverified' WHERE status='saved'")
        os.chmod(self.path, 0o600)
        self.reconcile()

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('PRAGMA foreign_keys=ON')
        try:
            with db:
                yield db
        finally:
            db.close()

    def _ws(self, db, ident):
        row = db.execute('SELECT * FROM workspaces WHERE id=?', (ident,)).fetchone()
        if row is None:
            raise KeyError('工作区不存在。')
        return row

    def _bump(self, db, ident, **fields):
        """Single indexed state-write boundary; the caller owns its transaction."""
        if set(fields)-WORKSPACE_FIELDS:
            raise InputError('工作区状态字段无效。')
        if 'status' in fields and fields['status'] not in WORKSPACE_STATUSES:
            raise InputError('工作区状态无效。')
        if 'verification_origin' in fields and fields['verification_origin'] not in (None,'done'):
            raise InputError('输出核验来源状态无效。')
        fields['revision'] = self._ws(db, ident)['revision'] + 1
        db.execute('UPDATE workspaces SET ' + ','.join(k + '=?' for k in fields) + ' WHERE id=?',
                   (*fields.values(), ident))
        return fields['revision']

    def _grant(self, ident, kind, label=None):
        if not ident:
            return None
        try:
            return self.access.info(ident, kind)
        except (OSError, ValueError):
            return {'folder_id': ident, 'path': label or '', 'valid': False}

    def summary(self, ident):
        self.reconcile()
        return self.query_summary(ident)

    def query_summary(self, ident):
        """Indexed projection; callers explicitly request actual reconciliation."""
        cache_facts=self._combined_cache_facts()
        with self.db() as db:
            db.execute('BEGIN')
            row = self._ws(db, ident)
            counts = dict(db.execute('SELECT status,count(*) FROM items WHERE wid=? GROUP BY status', (ident,)))
            issues = issue_counts(db, ident)
            selected,ready_selected = db.execute("SELECT count(*),count(CASE WHEN status='ready' THEN 1 END) FROM items WHERE wid=? AND selected=1", (ident,)).fetchone()
        result = {k: row[k] for k in ('id','status','phase','revision','discovered','scanned','message','selection_id')}
        result.update(total=sum(counts.values()), ready=counts.get('ready',0), saved=counts.get('saved',0),
                      error=sum(counts.get(k,0) for k in ERROR_STATUSES), issues=issues,
                      selected=selected, ready_selected=ready_selected, processed=sum(counts.get(k,0) for k in ('saved','synthesis_error','save_error','conflict','changed')),
                      settings=json.loads(row['settings']), input_folder=self._grant(row['input_id'],'input',row['input_path']),
                      output_folder=self._grant(row['output_id'],'output',row['output_path']),
                      active_item=json.loads(row['active_item']) if row['active_item'] else None)
        result['can_edit'] = bool(result['input_folder'] and result['input_folder']['valid']) and row['status'] not in ACTIVE | {'scanning'} and row['phase'] not in RECOVERY_PHASES and not (row['run_settings'] and row['status'] in {'paused','interrupted'})
        result['has_run_plan'] = bool(row['run_settings'])
        result['save_error'] = counts.get('save_error',0)
        result['synthesis_error'] = counts.get('synthesis_error',0)
        result['save_pending'] = counts.get('save_pending',0)
        result['counts'] = counts
        result['selection_query'] = json.loads(row['selection_query']) if row['selection_query'] else {'q':'','filter':'all','issue':''}
        result['has_unsaved_audio'] = cache_facts['by_workspace'].get(ident,0)>0
        result['global_unsaved_audio'] = cache_facts['protected']>0
        result['unsaved_audio_count'] = cache_facts['by_workspace'].get(ident,0)
        return result

    _cache_digest = staticmethod(validated_file_digest)

    def _combined_cache_facts(self):
        by_workspace=dict(self._cache_facts['by_workspace'])
        for ident,count in self._source_facts['by_workspace'].items():
            by_workspace[ident]=by_workspace.get(ident,0)+count
        return {'protected':self._cache_facts['protected']+self._source_facts['protected'],
                'unknown':self._cache_facts['unknown']+self._source_facts['unknown'], 'by_workspace':by_workspace}

    def reconcile(self):
        """Check complete worker sources and cache files without reading XML."""
        with self._recovery_lock:
            self._reconcile_worker_results()
            self._cache_facts=self._reconcile_cache()
            return self._combined_cache_facts()

    def _reconcile_worker_results(self):
        pending=self.jobs.recovery_safety()
        # A worker that exited without a full MP3 can be regenerated only after
        # the user resumes; an absent source must not masquerade as saved audio.
        with self.db() as db:
            for row in db.execute('SELECT wid,id,pending_job FROM items WHERE pending_job IS NOT NULL'):
                if not self.jobs.is_known(row['pending_job']):
                    db.execute('UPDATE items SET pending_job=NULL WHERE id=?',(row['id'],))
        facts={'protected':pending['unknown_count'],'unknown':pending['unknown_count'],'by_workspace':{}}
        for result in pending['results']:
            context=result['context']
            if not isinstance(context,dict):
                facts['protected']+=1;facts['unknown']+=1;continue
            ident,item_id=context['workspace_id'],context['item_id']
            if self.active==ident and self.active_job==result['id']:
                continue
            valid=False
            try:
                with self.db() as db:
                    ws=self._ws(db,ident)
                    row=db.execute('SELECT * FROM items WHERE wid=? AND id=?',(ident,item_id)).fetchone()
                    valid=bool(row and row['planned'] and ws['run_settings'] and row['fingerprint']==result['fingerprint'] and row['filename']==result['filename'] and row['status'] not in {'invalid','changed','conflict'})
                    if not valid:raise ValueError('worker binding changed')
                    if row['status']=='saved':
                        receipt=self.access.verify_output(row['saved_folder_id'] or ws['output_id'],row['filename'],row['fingerprint'])
                        if receipt['state']=='saved' and receipt['size']==result['size'] and receipt['sha256']==result['sha256']:
                            self.jobs.release(result['id']);continue
                self._adopt_worker_result(ident,item_id,result)
                continue
            except (ValueError,OSError,KeyError):
                facts['protected']+=1
                facts['by_workspace'][ident]=facts['by_workspace'].get(ident,0)+1
                if not valid:
                    facts['unknown']+=1;continue
                try:
                    with self.db() as db:
                        current=db.execute('SELECT status,pending_job FROM items WHERE wid=? AND id=?',(ident,item_id)).fetchone()
                        if current['status']!='save_error' or current['pending_job']!=result['id']:
                            self._update_item(db,ident,item_id,status='save_error',pending_job=result['id'],error='完整合成源已保留；缓存写入未完成，请恢复空间或权限后重试保存。',error_code='save_failed')
                except (ValueError,OSError,sqlite3.Error):facts['unknown']+=1
        self._source_facts=facts

    def _adopt_worker_result(self,ident,item_id,result):
        cache=self._audio_path(ident,item_id)
        staging=cache.with_suffix('.worker.tmp')
        with self.cache_lock:
            if cache.exists():
                if self._cache_digest(cache)!=(result['size'],result['sha256']):raise ValueError('existing cache differs')
            else:
                try:
                    with self.jobs.open_result(result['id'],recovery=True) as source,open(staging,'wb') as output:
                        digest=hashlib.sha256();size=0
                        for block in iter(lambda:source.read(65536),b''):
                            size+=len(block);digest.update(block);output.write(block)
                        output.flush();os.fsync(output.fileno())
                    if size!=result['size'] or digest.hexdigest()!=result['sha256']:raise ValueError('worker source changed')
                    os.replace(staging,cache)
                    self._sync_cache()
                    self._after_cache_publish(ident,item_id)
                finally:staging.unlink(missing_ok=True)
            if self._cache_digest(cache)!=(result['size'],result['sha256']):raise ValueError('published cache differs')
            with self.db() as db:
                self._update_item(db,ident,item_id,status='save_pending',pending_job=None,audio_hash=result['sha256'],audio_size=result['size'],error=None,error_code=None)
        self.jobs.release(result['id'])

    def _reconcile_cache(self):
        """Use published cache facts, never database status alone, for protection.

        A .tmp is not a complete publication. Unknown/unreadable .mp3 files are
        retained and conservatively protected; no cleanup is performed without
        a verified native receipt or explicit per-workspace discard.
        """
        facts={'protected':0,'unknown':0,'by_workspace':{}}
        inspected_bytes=0
        with self.cache_lock,self.db() as db,os.scandir(self.cache) as entries:
            for entry in entries:
                if not entry.name.endswith('.mp3'):continue
                match=re.fullmatch(r'([a-f0-9]{32})-([a-f0-9]{32})\.mp3',entry.name)
                wid,item_id=match.groups() if match else (None,None)
                row=db.execute('SELECT * FROM items WHERE wid=? AND id=?',(wid,item_id)).fetchone() if match else None
                path=Path(entry.path)
                valid=False
                try:
                    info=entry.stat(follow_symlinks=False)
                    token=(info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns,info.st_ctime_ns)
                    checked=self.cache_checks.get(entry.name)
                    if checked and checked[0]==token:size,digest=checked[1:]
                    else:
                        if inspected_bytes+info.st_size>self.cache_limit:raise ValueError('缓存核验超过受限预算。')
                        inspected_bytes+=info.st_size
                        size,digest=self._cache_digest(path)
                        self.cache_checks[entry.name]=(token,size,digest)
                    valid=bool(row and valid_output_name(row['filename']) and isinstance(row['fingerprint'],str) and re.fullmatch('[a-f0-9]{64}',row['fingerprint']))
                except (ValueError,OSError):
                    size,digest=None,None
                if valid and row['status']=='saved':
                    ws=self._ws(db,wid)
                    try:
                        receipt=self.access.verify_output(row['saved_folder_id'] or ws['output_id'],row['filename'],row['fingerprint'])
                        if receipt['state']=='saved':
                            path.unlink(missing_ok=True);self.cache_checks.pop(entry.name,None);continue
                    except (ValueError,OSError):pass
                facts['protected']+=1
                if wid:facts['by_workspace'][wid]=facts['by_workspace'].get(wid,0)+1
                if not valid:
                    facts['unknown']+=1;continue
                mismatch=bool(row['audio_hash'] and (digest!=row['audio_hash'] or size!=row['audio_size']))
                status='save_error' if mismatch else 'save_pending'
                if row['status']=='saving':continue  # a live native save remains in flight
                if not mismatch and row['status']=='save_error' and row['audio_hash']==digest and row['audio_size']==size:continue
                if row['status']==status and (mismatch or (row['audio_hash']==digest and row['audio_size']==size)):continue
                revision=self._bump(db,wid)
                db.execute('UPDATE items SET status=?,planned=1,audio_hash=?,audio_size=?,error=?,error_code=?,revision=? WHERE wid=? AND id=?',
                           (status,row['audio_hash'] if mismatch else digest,row['audio_size'] if mismatch else size,
                            '缓存校验不一致；请核对或明确放弃。' if mismatch else None,
                            'cache_changed' if mismatch else None,revision,wid,item_id))
        return facts

    def _after_cache_publish(self,ident,item_id):
        """Crash-injection seam after rename and before the database commit."""

    def list(self):
        with self.db() as db:
            ids = [r[0] for r in db.execute('SELECT id FROM workspaces ORDER BY created DESC LIMIT 100')]
        return {'workspaces': [self.query_summary(i) for i in ids]}

    def import_folder(self, input_id):
        directory=self.access.directory(input_id, 'input')
        ident = uuid.uuid4().hex
        with self.lock, self.db() as db:
            scanner=db.execute("SELECT id,input_id FROM workspaces WHERE status='scanning' LIMIT 1").fetchone()
            if scanner:
                if scanner['input_id']==input_id:return self.summary(scanner['id'])
                raise InputError('已有目录正在扫描，请等待或取消后再导入。')
            db.execute('INSERT INTO workspaces(id,status,phase,input_id,settings,selection_id,created,input_path) VALUES(?,?,?,?,?,?,?,?)',
                       (ident,'scanning','enumerating',input_id,json.dumps(DEFAULT_SETTINGS),uuid.uuid4().hex,time.time(),str(directory)))
            cancel = self.scan_cancel[ident] = threading.Event()
            thread = self.threads[ident] = threading.Thread(target=self._scan,args=(ident,cancel),daemon=True)
            db.commit()
            thread.start()
        return self.summary(ident)

    def _cancelled(self, cancel):
        return cancel.is_set() or self.stop.is_set()

    def _scan(self, ident, cancel):
        try:
            with self.db() as db:
                input_id = self._ws(db,ident)['input_id']
                count = 0
                for name in self.access.iter_files(input_id):
                    if self._cancelled(cancel):
                        break
                    ref, role, digest, error = None,None,None,None
                    try:
                        raw = self.access.read_file(input_id,name)
                        digest = hashlib.sha256(raw).hexdigest()
                        identity, _, _, role = _metadata(raw.decode('utf-8-sig'))
                        ref = hashlib.sha256(identity.encode()).hexdigest()
                    except (ValueError,UnicodeError,OSError):
                        error = 'XML 无法读取、格式无效或声道标识不支持。'
                    db.execute('INSERT INTO files VALUES(?,?,?,?,?,?)', (ident,name,ref,role,digest,error))
                    count += 1
                    if count % 100 == 0:
                        self._bump(db,ident,discovered=count,scanned=count,phase='reading')
                        db.commit()
                self._bump(db,ident,discovered=count,scanned=count,phase='pairing')
                db.commit()
                if self._cancelled(cancel):
                    self._bump(db,ident,status='cancelled',phase='cancelled',message='扫描已取消；没有发送文本。')
                    return
                # File failures are standalone items. Identities are grouped globally,
                # independent of file iteration order or eventual execution chunks.
                for row in db.execute('SELECT name,error FROM files WHERE wid=? AND identity IS NULL',(ident,)):
                    db.execute('INSERT INTO items(wid,id,status,error,error_code,source_name) VALUES(?,?,?,?,?,?)',
                               (ident,uuid.uuid4().hex,'invalid',row['error'],'invalid_xml',row['name']))
                db.commit()
                groups = db.execute('SELECT identity,count(*) AS n FROM files WHERE wid=? AND identity IS NOT NULL GROUP BY identity',(ident,))
                for index, group in enumerate(groups):
                    if self._cancelled(cancel):
                        break
                    item_id = uuid.uuid4().hex
                    members = db.execute('SELECT name,role FROM files WHERE wid=? AND identity=? LIMIT 3',(ident,group['identity'])).fetchall()
                    error, code, filename = None,None,None
                    if group['n'] != 2 or {r['role'] for r in members} != {'AGENT','USER'}:
                        error,code = ('此通话缺少声道。','missing_track') if group['n']==1 else ('同一通话存在重复声道或无效角色。','duplicate_track')
                    else:
                        try:
                            filename = pair_filename([{'name':r['name']} for r in members])
                        except ValueError as exc:
                            error,code = str(exc),'invalid_filename'
                    db.execute('INSERT INTO items(wid,id,identity,filename,status,error,error_code,source_name) VALUES(?,?,?,?,?,?,?,?)',
                               (ident,item_id,group['identity'],filename,'invalid' if error else 'ready',error,code,members[0]['name']))
                    if not error:
                        try:
                            preview = self._preview(db,ident,item_id)
                            self._preview_metadata(db,item_id,preview)
                        except (ValueError,OSError,UnicodeError):
                            db.execute("UPDATE items SET status='invalid',error='XML 内容无法生成，请核对输入。',error_code='invalid_content' WHERE id=?",(item_id,))
                    if index % 100 == 0:
                        self._bump(db,ident,phase='validating')
                        db.commit()
                db.execute("UPDATE items SET status='invalid',error_code='duplicate_filename',error='整个目录存在重复目标文件名。' WHERE wid=? AND filename IN (SELECT filename FROM items WHERE wid=? AND filename IS NOT NULL GROUP BY filename HAVING count(*)>1)",(ident,ident))
                self._bump(db,ident,status='cancelled' if self._cancelled(cancel) else 'ready',phase='ready',message=None)
        except Exception:
            with self.db() as db:
                self._bump(db,ident,status='error',phase='scan_error',message='扫描失败，请核对目录权限和可用空间后重新导入。')

    def _preview(self, db, ident, item_id, settings=None, override_marker=False, override=None):
        from .source_processing import normalize_source
        ws = self._ws(db,ident)
        item = db.execute('SELECT * FROM items WHERE wid=? AND id=?',(ident,item_id)).fetchone()
        if item is None:
            raise KeyError('通话不存在。')
        files = []
        for row in db.execute('SELECT name,digest FROM files WHERE wid=? AND identity=? ORDER BY name',(ident,item['identity'])):
            raw = self.access.read_file(ws['input_id'],row['name'])
            if hashlib.sha256(raw).hexdigest() != row['digest']:
                raise InputError('扫描后输入文件已改变，请重新导入。')
            files.append({'name':row['name'],'content':raw.decode('utf-8-sig')})
        if len(files) != 2:
            raise InputError('通话声道不完整。')
        values = json.loads(json.dumps(settings or json.loads(ws['settings'])))
        voice = override if override_marker else item['override']
        if voice:
            values['voices'] = {**values.get('voices',{}),'USER':voice}
            values['manual_user_voice'] = True
        preview=normalize_source({'format':'xml_pair','xml_files':files,'xml_time_unit':'ms',**values},self.catalog)
        choice=preview.get('voice_choice',{})
        if choice.get('address') in {'female','male'} and str(choice.get('reason','')).startswith('称呼：'):
            from .honorific import direct_address
            for row in preview['rows']:
                if row.get('speaker')=='AGENT' and not row.get('muted') and direct_address([row])==choice['address']:
                    choice['evidence_row_id']=row['id'];break
        return preview

    @staticmethod
    def _preview_metadata(db, item_id, preview):
        db.execute('UPDATE items SET customer_voice=?,voice_source=?,line_count=?,fingerprint=? WHERE id=?',
                   (preview['voice_choice']['user'],preview['voice_choice']['reason'],len(preview['script']['lines']),preview['fingerprint'],item_id))

    @staticmethod
    def _item(row):
        result = {k:row[k] for k in ('id','filename','status','error','error_code','customer_voice','voice_source','line_count')}
        result['selected'] = bool(row['selected'])
        result['has_audio'] = row['status'] in {'saved','save_error','saving','save_pending'} and not row['pending_job']
        result['source_name'] = row['source_name']
        return result

    def _where(self, q='', filter='all', issue=''):
        if not isinstance(q,str) or len(q)>255 or not isinstance(filter,str) or filter not in FILTERS:
            raise InputError('筛选参数无效。')
        category, args = issue_where(issue)
        return FILTERS[filter] + " AND instr(coalesce(filename,source_name,''),?)>0 AND (" + category + ')', (q,*args)

    def page(self, ident, q='', filter='all', page=1, limit=100, sort='filename', issue=''):
        page,limit = int(page),int(limit)
        if page < 1 or not 1 <= limit <= 100 or sort != 'filename':
            raise InputError('分页参数无效。')
        where,args = self._where(q,filter,issue)
        with self.db() as db:
            self._ws(db,ident)
            total = db.execute('SELECT count(*) FROM items WHERE wid=? AND '+where,(ident,*args)).fetchone()[0]
            rows = db.execute('SELECT * FROM items WHERE wid=? AND '+where+' ORDER BY filename IS NULL,filename,id LIMIT ? OFFSET ?',
                              (ident,*args,limit,(page-1)*limit)).fetchall()
            hidden = db.execute('SELECT count(*) FROM items WHERE wid=? AND selected=1 AND NOT ('+where+')',(ident,*args)).fetchone()[0]
        return {**self.query_summary(ident),'items':[self._item(r) for r in rows], 'matched_total':total,'page':page,'page_size':limit,'selection_hidden_count':hidden,'issue':normalize_issue(issue)}

    def next_issue(self, ident, q='', issue='', after_id=None, limit=100):
        if str(limit) != '100':raise InputError('异常定位每页固定为 100 条。')
        if after_id is not None and (not isinstance(after_id,str) or not after_id or len(after_id)>128):
            raise InputError('通话定位参数无效。')
        where,args = self._where(q,'error',issue)
        with self.db() as db:
            db.execute('BEGIN')
            self._ws(db,ident)
            after = None
            if after_id is not None:
                after=db.execute('SELECT filename,id FROM items WHERE wid=? AND id=?',(ident,after_id)).fetchone()
                if after is None:raise KeyError('通话不存在。')
            total=db.execute('SELECT count(*) FROM items WHERE wid=? AND '+where,(ident,*args)).fetchone()[0]
            target=None
            if after is not None:
                seek,values=('filename IS NULL AND id>?',(after['id'],)) if after['filename'] is None else ('(filename IS NULL OR filename>? OR (filename=? AND id>?))',(after['filename'],after['filename'],after['id']))
                target=db.execute('SELECT id,filename FROM items WHERE wid=? AND '+where+' AND ('+seek+') ORDER BY filename IS NULL,filename,id LIMIT 1',(ident,*args,*values)).fetchone()
            wrapped=after is not None and target is None and total>0
            if target is None:
                target=db.execute('SELECT id,filename FROM items WHERE wid=? AND '+where+' ORDER BY filename IS NULL,filename,id LIMIT 1',(ident,*args)).fetchone()
            page=1
            if target is not None:
                before,values=('(filename IS NOT NULL OR (filename IS NULL AND id<?))',(target['id'],)) if target['filename'] is None else ('filename IS NOT NULL AND (filename<? OR (filename=? AND id<?))',(target['filename'],target['filename'],target['id']))
                position=db.execute('SELECT count(*) FROM items WHERE wid=? AND '+where+' AND ('+before+')',(ident,*args,*values)).fetchone()[0]
                page=position//100+1
        return {'item_id':target['id'] if target is not None else None,'page':page,'page_size':100,'wrapped':wrapped,'matched_total':total,'filter':'error','issue':normalize_issue(issue)}

    def detail(self, ident, item_id):
        with self.db() as db:
            row = db.execute('SELECT * FROM items WHERE wid=? AND id=?',(ident,item_id)).fetchone()
            if row is None:raise KeyError('通话不存在。')
            if row['status']=='invalid':
                sources=db.execute('SELECT name,role FROM files WHERE wid=? AND '+('identity=?' if row['identity'] else 'name=?')+' LIMIT 100',
                                   (ident,row['identity'] or row['source_name'])).fetchall()
                return {'filename':row['filename'],'item':self._item(row),'workspace_id':ident,'rows':[],
                        'script':{'lesson_id':'','title':'','language':'zh-CN','lines':[],
                                  'settings':{'speech_rate':1,'initial_silence_ms':0,'default_pause_ms':250}},
                        'speakers':[],'warnings':[row['error']] if row['error'] else [],'fingerprint':'','muted_count':0,
                        'sources':[{'name':r['name'],'role':r['role'],'segments':0} for r in sources]}
            preview = self._preview(db,ident,item_id)
        return {**preview,'item':self._item(row),'workspace_id':ident,
                'result_settings_changed': row['status'] in {'saved','save_error'} and row['fingerprint'] != preview['fingerprint']}

    def select(self, ident, payload):
        if set(payload)-{'q','filter','issue','mode','item_ids','item_id','selected'}:raise InputError('选择参数无效。')
        q,filter,issue=payload.get('q',''),payload.get('filter','all'),payload.get('issue','')
        where,args = self._where(q,filter,issue)
        query={'q':q,'filter':filter,'issue':normalize_issue(issue)}
        with self.jobs.transaction(),self.lock,self.db() as db:
            ws=self._ws(db,ident)
            if ws['status'] in ACTIVE or ws['status']=='scanning':
                raise InputError('请在扫描完成、任务停止后调整选择。')
            if ws['phase'] in RECOVERY_PHASES:raise InputError('请先继续尚未完成的离线检查。')
            if ws['run_settings'] and ws['status'] in {'paused','interrupted'}:
                raise InputError('请先继续或结束未完成任务，再改变选择。')
            mode=payload.get('mode')
            if not isinstance(mode,str):raise InputError('选择操作无效。')
            if mode=='clear':
                db.execute('UPDATE items SET selected=0 WHERE wid=?',(ident,))
            elif mode=='all':
                db.execute("UPDATE items SET selected=1 WHERE wid=? AND status='ready' AND "+where,(ident,*args))
            elif mode in {'page','toggle'}:
                ids=payload.get('item_ids',[]) if mode=='page' else [payload.get('item_id')]
                if not isinstance(ids,list) or len(ids)>100 or any(not isinstance(i,str) for i in ids):
                    raise InputError('选择参数无效。')
                for item_id in ids:
                    selected=payload.get('selected')
                    if selected is not None and not isinstance(selected,bool): raise InputError('选择状态无效。')
                    db.execute("UPDATE items SET selected="+('1-selected' if selected is None else '?')+" WHERE wid=? AND id=? AND status='ready' AND "+where,
                               ((ident,item_id,*args) if selected is None else (int(selected),ident,item_id,*args)))
            else: raise InputError('选择操作无效。')
            token=uuid.uuid4().hex
            self._bump(db,ident,selection_id=token,selection_query=json.dumps(query))
            count=db.execute('SELECT count(*) FROM items WHERE wid=? AND selected=1',(ident,)).fetchone()[0]
            visible=db.execute('SELECT count(*) FROM items WHERE wid=? AND selected=1 AND '+where,(ident,*args)).fetchone()[0]
        return {'selection_id':token,'count':count,'hidden_count':count-visible,'selection_query':query}

    def settings(self,ident,payload):
        from .source_processing import normalize_source
        from .xml_example import XML_EXAMPLE
        with self.lock,self.db() as db:
            ws=self._ws(db,ident)
            try:self.access.directory(ws['input_id'],'input')
            except (ValueError,OSError):raise InputError('请先恢复来源文件夹访问，再修改声音设置。') from None
            if ws['status'] in ACTIVE or ws['status']=='scanning': raise InputError('请在任务停止后修改设置。')
            if ws['phase'] in RECOVERY_PHASES:raise InputError('请先继续尚未完成的离线检查。')
            if ws['run_settings'] and ws['status'] in {'paused','interrupted'}:
                raise InputError('请先继续或结束未完成任务，再修改设置。')
            if payload.get('selection_id') or payload.get('item_id'):
                if payload.get('selection_id') and payload['selection_id']!=ws['selection_id']: raise InputError('选择已改变，请刷新。')
                voice=payload.get('customer_voice')
                if voice is not None and (not isinstance(voice,str) or voice not in {v['id'] for v in self.catalog}): raise InputError('客户音色无效。')
                if payload.get('item_id'):
                    db.execute("UPDATE items SET override=? WHERE wid=? AND id=? AND status='ready'",(voice,ident,payload['item_id']))
                else:
                    db.execute("UPDATE items SET override=? WHERE wid=? AND selected=1 AND status='ready'",(voice,ident))
            else:
                values=payload.get('settings')
                if not isinstance(values,dict): raise InputError('设置无效。')
                values={k:values[k] for k in DEFAULT_SETTINGS if k in values}
                values={**DEFAULT_SETTINGS,**values}
                normalize_source({'format':'xml_pair','xml_files':XML_EXAMPLE,**values},self.catalog)
                db.execute('UPDATE workspaces SET settings=? WHERE id=?',(json.dumps(values),ident))
            self._bump(db,ident,status='scanning',phase='settings',message=None,selection_id=uuid.uuid4().hex)
            cancel=self.scan_cancel[ident]=threading.Event()
            db.commit()
        # Recompute only metadata, on a background thread, so bulk changes never
        # make a giant response or block the main UI request.
        thread=self.threads[ident]=threading.Thread(target=self._refresh_metadata,args=(ident,payload.get('item_id'),bool(payload.get('selection_id')),cancel),daemon=True)
        thread.start()
        return self.summary(ident)

    def _refresh_metadata(self,ident,item_id=None,selected_only=False,cancel=None):
        cancel=cancel or threading.Event()
        try:
            with self.db() as db:
                suffix=' AND id=?' if item_id else (' AND selected=1' if selected_only else '')
                cursor=db.execute("SELECT id FROM items WHERE wid=? AND status='ready'"+suffix,(ident,item_id) if item_id else (ident,))
                for index,row in enumerate(cursor):
                    if self._cancelled(cancel): break
                    try: self._preview_metadata(db,row['id'],self._preview(db,ident,row['id']))
                    except (ValueError,OSError,UnicodeError):
                        db.execute("UPDATE items SET status='changed',error='输入文件已变化或不可读。',error_code='input_changed' WHERE id=?",(row['id'],))
                    if index%100==0:db.commit()
                stopped=self._cancelled(cancel)
                rev=self._bump(db,ident,status='interrupted' if stopped else 'ready',phase='recovery_settings' if stopped else 'ready',
                               message='设置更新已停止；可继续离线重算。' if stopped else None)
                db.execute("UPDATE items SET revision=? WHERE wid=? AND status='ready'",(rev,ident))
        except Exception:
            with self.db() as db:self._bump(db,ident,status='error',phase='settings_error',message='设置核验失败，请重试。')

    def start(self,ident,payload):
        if payload.get('consent') is not True: raise InputError('请允许将本次文本发送给微软合成语音。')
        request=payload.get('request_id')
        if not isinstance(request,str) or not 8<=len(request)<=100: raise InputError('请求标识无效。')
        digest=hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()
        cache_facts=self.reconcile()
        with self.jobs.transaction(),self.lock,self.db() as db:
            ws=self._ws(db,ident)
            prior=db.execute('SELECT digest FROM requests WHERE wid=? AND request_id=?',(ident,request)).fetchone()
            if prior:
                if prior[0]!=digest: raise InputError('请求标识对应内容已改变。')
                return self.summary(ident)
            if cache_facts['by_workspace'].get(ident,0):raise InputError('请先保存或明确放弃当前未保存音频。')
            self._available()
            if ws['status'] not in {'ready','done','paused','interrupted'} or self.active:
                raise InputError('已有任务运行或扫描尚未完成。')
            if ws['run_settings'] and ws['status'] in {'paused','interrupted'}:
                raise InputError('已有冻结计划，请继续或结束当前任务。')
            if ws['phase'] in RECOVERY_PHASES:raise InputError('离线检查尚未完成，不能生成。')
            if payload.get('selection_id')!=ws['selection_id']: raise InputError('选择已改变，请刷新后重试。')
            directory=self.access.directory(payload.get('output_folder_id'),'output')
            fd,probe=tempfile.mkstemp(prefix='.dialogue-check-',dir=directory)
            os.close(fd);Path(probe).unlink()
            if shutil.disk_usage(directory).free < 16*1024*1024:
                raise InputError('结果文件夹可用空间不足。')
            count=db.execute("SELECT count(*) FROM items WHERE wid=? AND selected=1 AND status='ready'",(ident,)).fetchone()[0]
            if not count: raise InputError('没有可生成的已选通话。')
            db.execute('UPDATE items SET planned=0 WHERE wid=?',(ident,))
            db.execute("UPDATE items SET planned=1,plan_voice=override,status='queued' WHERE wid=? AND selected=1 AND status='ready'",(ident,))
            db.execute('INSERT INTO requests VALUES(?,?,?)',(ident,request,digest))
            self._bump(db,ident,status='running',phase='generating',output_id=payload['output_folder_id'],output_path=str(directory),run_settings=ws['settings'],verification_origin=None,message=None)
            db.commit()
            self._launch(ident)
        return self.summary(ident)

    def _launch(self,ident,mode='all'):
        self._available()
        self.jobs.claim_batch('workspace:'+ident)
        self.active=ident
        thread=self.threads[ident]=threading.Thread(target=self._run,args=(ident,mode),daemon=True)
        thread.start()

    def _available(self):
        if self.active:raise InputError('已有任务正在运行，请先等待或暂停。')
        self.jobs.ensure_available()

    def _update_item(self,db,ident,item_id,**fields):
        phase=fields.pop('ws_phase',None)
        fields['revision']=self._bump(db,ident,**({'phase':phase} if phase else {}))
        db.execute('UPDATE items SET '+','.join(k+'=?' for k in fields)+' WHERE wid=? AND id=?',(*fields.values(),ident,item_id))
        db.commit()

    def _audio_path(self,ident,item_id):
        return self.cache / (ident+'-'+item_id+'.mp3')

    def _sync_cache(self):
        if os.name!='nt':
            descriptor=os.open(self.cache,os.O_RDONLY|os.O_DIRECTORY)
            try:os.fsync(descriptor)
            finally:os.close(descriptor)

    def _temporary_bytes(self):
        return self.jobs.temporary_bytes()

    def _release_job(self,transferred=False):
        ident=self.active_job
        if ident is None:return
        try:
            if not self.jobs.wait_finished(ident,1):return
            if not transferred and self.jobs.result_available(ident):return
            self.jobs.release(ident)
            self.active_job=None
        except (KeyError,ValueError,OSError):
            pass  # Complete sources retain their ownership and recovery binding.

    @contextmanager
    def _synthesize(self,ident,item_id,preview):
        if self.synthesizer:
            source=Path(self.synthesizer(preview))
            with source.open('rb') as handle:yield handle,source.stat().st_size,None
            return
        job=self.jobs.start(preview['script'],batch_owner='workspace:'+ident,fingerprint=preview['fingerprint'],filename=preview['filename'],recovery_context={'workspace_id':ident,'item_id':item_id})
        self.active_job=job['id']
        with self.db() as db:
            self._update_item(db,ident,item_id,pending_job=job['id'])
        while not self.stop.wait(.2):
            snapshot=self.jobs.snapshot(job['id'])
            with self.db() as db:
                db.execute('UPDATE workspaces SET active_item=? WHERE id=?',
                           (json.dumps({'id':item_id,'filename':preview['filename'],'completed':snapshot['completed'],'total':snapshot['total']}),ident))
            if snapshot['status']=='done':
                if not self.jobs.wait_finished(job['id'],3):raise InputError('合成进程尚未完成退出。')
                with self.jobs.open_result(job['id']) as result:yield result,result.size,result.sha256
                return
            if snapshot['status'] in {'error','cancelled'}:
                raise InputError(snapshot.get('error') or '合成未完成。')
        self.jobs.cancel(job['id'])
        raise InputError('任务已中断。')

    @staticmethod
    def _next_save(db,ident):
        # An IN predicate plus filename ordering can make SQLite prefer the
        # filename index and scan every completed row. Two equality lookups
        # each seek one queue entry; merge only those two bounded candidates.
        result=None
        for status in ('save_error','save_pending'):
            row=db.execute("SELECT * FROM items INDEXED BY item_queue WHERE wid=? AND planned=1 AND status=? ORDER BY filename,id LIMIT 1",(ident,status)).fetchone()
            if row is not None and (result is None or (row['filename'] or '',row['id'])<(result['filename'] or '',result['id'])):
                result=row
        return result

    def _run(self,ident,mode='all'):
        failures=0
        try:
            with self.db() as db:
                while not self.stop.is_set():
                    ws=self._ws(db,ident)
                    if ws['status']=='pausing':
                        self._bump(db,ident,status='paused',phase='paused',active_item=None)
                        break
                    row=None
                    if mode!='synthesis':
                        row=self._next_save(db,ident)
                    if row is None and mode!='save':
                        extra=" AND error_code='retry_synthesis'" if mode=='synthesis' else ''
                        row=db.execute("SELECT * FROM items WHERE wid=? AND planned=1 AND status='queued'"+extra+" ORDER BY filename,id LIMIT 1",(ident,)).fetchone()
                    if not row:
                        pending=db.execute("SELECT 1 FROM items WHERE wid=? AND planned=1 AND status IN ('queued','save_error','save_pending') LIMIT 1",(ident,)).fetchone()
                        self._bump(db,ident,status='paused' if pending else 'done',phase='paused' if pending else 'done',active_item=None)
                        break
                    item_id=row['id']
                    cache=self._audio_path(ident,item_id)
                    if cache.is_file() and row['audio_hash']:
                        digest=hashlib.sha256()
                        with open(cache,'rb') as handle:
                            for block in iter(lambda:handle.read(65536),b''):digest.update(block)
                        if cache.stat().st_size!=row['audio_size'] or digest.hexdigest()!=row['audio_hash']:
                            self._update_item(db,ident,item_id,status='save_error',error='缓存校验不一致；请核对或明确放弃。',error_code='cache_changed')
                            self._bump(db,ident,status='paused',phase='save_error',message='缓存校验失败，原文件已保留。')
                            break
                    # Directory loss is a global boundary, not 60,000 row errors.
                    try:
                        if not cache.is_file():self.access.directory(ws['input_id'],'input')
                        self.access.directory(ws['output_id'],'output')
                    except (ValueError,OSError):
                        self._bump(db,ident,status='paused',phase='directory_error',message='目录不可用，请重新授权后继续。',active_item=None)
                        break
                    if not cache.is_file():
                        if row['pending_job']:
                            self._bump(db,ident,status='paused',phase='save_error',message='完整合成源仍已保留，请恢复缓存空间或权限后重试保存。')
                            break
                        if mode=='save':
                            self._update_item(db,ident,item_id,status='synthesis_error',error='完整缓存缺失，无法只重试保存。',error_code='missing_cache')
                            continue
                        try:
                            try:
                                preview=self._preview(db,ident,item_id,json.loads(ws['run_settings']),True,row['plan_voice'])
                            except (ValueError,OSError,UnicodeError):
                                self._update_item(db,ident,item_id,status='changed',error='输入文件已变化或不可读，请重新导入。',error_code='input_changed')
                                continue
                            checked=self.access.verify_output(ws['output_id'],preview['filename'],preview['fingerprint'])
                            if checked['state']=='saved':
                                self._update_item(db,ident,item_id,status='saved',fingerprint=preview['fingerprint'],saved_folder_id=ws['output_id'],error=None,error_code=None)
                                failures=0
                                continue
                            if checked['state']=='conflict':
                                self._update_item(db,ident,item_id,status='conflict',error='同名输出无法核实；未覆盖已有文件。',error_code='output_conflict')
                                continue
                            used=sum(p.stat().st_size for p in self.cache.glob('*.mp3'))
                            if used+self._temporary_bytes() >= self.cache_limit or shutil.disk_usage(self.cache).free < 16*1024*1024:
                                self._bump(db,ident,status='paused',phase='storage_error',message='临时结果空间不足，请先保存失败结果。')
                                break
                            self._update_item(db,ident,item_id,status='running',fingerprint=preview['fingerprint'],ws_phase='generating')
                            with self._synthesize(ident,item_id,preview) as (source,size,expected_hash):
                                if not size or size+used+self._temporary_bytes()>self.cache_limit: raise OSError('cache limit')
                                staging=cache.with_suffix('.tmp')
                                digest=hashlib.sha256();copied=0
                                with open(staging,'wb') as dst:
                                    while True:
                                        block=source.read(65536)
                                        if not block:break
                                        copied+=len(block);digest.update(block);dst.write(block)
                                    dst.flush();os.fsync(dst.fileno())
                                if copied!=size or expected_hash is not None and digest.hexdigest()!=expected_hash:
                                    raise InputError('完整合成源复制校验失败。')
                                if self._cache_digest(staging)!=(size,digest.hexdigest()):
                                    raise InputError('合成缓存格式或内容校验失败。')
                                with self.cache_lock:
                                    os.replace(staging,cache)
                                    self._sync_cache()
                                    self._after_cache_publish(ident,item_id)
                                    self._update_item(db,ident,item_id,status='saving',pending_job=None,error=None,error_code=None,audio_hash=digest.hexdigest(),audio_size=size,ws_phase='saving')
                            self._release_job(transferred=True)
                            row=db.execute('SELECT * FROM items WHERE id=?',(item_id,)).fetchone()
                        except (ValueError,OSError,UnicodeError,KeyError):
                            self._release_job()
                            try:cache.with_suffix('.tmp').unlink(missing_ok=True)
                            except OSError:pass
                            if self.stop.is_set(): break
                            if self.active_job is not None and self.jobs.result_available(self.active_job):
                                self._update_item(db,ident,item_id,status='save_error',pending_job=self.active_job,error='完整合成源已保留；缓存写入未完成，可重试保存。',error_code='save_failed')
                                self._bump(db,ident,status='paused',phase='save_error',message='完整合成源已保留，请恢复空间或权限后重试保存。')
                                break
                            self._update_item(db,ident,item_id,status='synthesis_error',pending_job=None,error='合成失败或输入已变化，可单独重试。',error_code='synthesis_failed')
                            failures+=1
                            if failures>=3:
                                self._bump(db,ident,status='paused',phase='service_error',message='连续三通未完成，已暂停；请检查网络和输入。')
                                break
                            continue
                    try:
                        if row['status']!='saving':self._update_item(db,ident,item_id,status='saving',error=None,ws_phase='saving')
                        receipt=self.access.save_audio(ws['output_id'],cache,row['filename'],row['fingerprint'])
                        if receipt.get('ok') is False: raise ValueError('save failed')
                        self._update_item(db,ident,item_id,status='saved',saved_folder_id=ws['output_id'],error=None,error_code=None,ws_phase='generating')
                        with self.cache_lock:
                            cache.unlink(missing_ok=True)
                            self.cache_checks.pop(cache.name,None)
                        failures=0
                    except (ValueError,OSError):
                        self._update_item(db,ident,item_id,status='save_error',error='保存失败；完整音频已保留，可重试保存。',error_code='save_failed')
                        self._bump(db,ident,status='paused',phase='save_error',message='保存失败，已暂停；重试保存不会重新合成。',active_item=None)
                        break
        except Exception:
            with self.db() as db:
                self._bump(db,ident,status='interrupted',phase='error',message='任务已中断；已完成输出及完整缓存保留。',active_item=None)
        finally:
            with self.jobs.transaction(),self.lock:
                self.active=None
                self.active_job=None
                self.jobs.release_batch('workspace:'+ident)

    def action(self,ident,action,payload):
        cache_facts=self.reconcile() if action in {'finish','resume','retry'} else None
        with self.jobs.transaction(),self.lock,(self.cache_lock if action=='finish' else nullcontext()),self.db() as db:
            ws=self._ws(db,ident)
            if action=='cancel-scan':
                if ident in self.scan_cancel: self.scan_cancel[ident].set()
            elif action=='pause':
                if ws['status']=='running': self._bump(db,ident,status='pausing',phase='pausing')
            elif action in {'resume','retry'}:
                if set(payload)-({'consent','kind'} if action=='retry' else {'consent'}):raise InputError('继续或重试参数无效。')
                kind=payload.get('kind') if action=='retry' else None
                if action=='retry' and kind not in ('synthesis','save'):raise InputError('重试类型无效。')
                save_only=action=='retry' and kind=='save'
                if ws['status'] not in {'paused','interrupted','done'} or self.active: raise InputError('任务尚未停止。')
                self._available()
                if action=='resume' and ws['phase']=='recovery_settings':
                    self.access.directory(ws['input_id'],'input')
                    cancel=self.scan_cancel[ident]=threading.Event()
                    self._bump(db,ident,status='scanning',phase='settings',message=None)
                    db.commit()
                    thread=self.threads[ident]=threading.Thread(target=self._refresh_metadata,args=(ident,None,False,cancel),daemon=True)
                    thread.start()
                    return self.summary(ident)
                if action=='resume' and not ws['run_settings'] and ws['phase']=='recovery_scan':
                    self.access.directory(ws['input_id'],'input')
                    db.execute('DELETE FROM files WHERE wid=?',(ident,));db.execute('DELETE FROM items WHERE wid=?',(ident,))
                    self._bump(db,ident,status='scanning',phase='enumerating',discovered=0,scanned=0,message=None)
                    db.commit()
                    cancel=self.scan_cancel[ident]=threading.Event()
                    thread=self.threads[ident]=threading.Thread(target=self._scan,args=(ident,cancel),daemon=True)
                    thread.start()
                    return self.summary(ident)
                if not save_only and payload.get('consent') is not True: raise InputError('继续合成前请确认联网授权。')
                if not ws['run_settings']: raise InputError('此任务尚未启动，请选择通话后生成。')
                if db.execute("SELECT 1 FROM items WHERE wid=? AND status='unverified' LIMIT 1",(ident,)).fetchone():
                    raise InputError('请先恢复目录，完成已有结果核验。')
                if not save_only:self.access.directory(ws['input_id'],'input')
                self.access.directory(ws['output_id'],'output')
                if action=='retry':
                    if kind=='synthesis':
                        db.execute("UPDATE items SET status='queued',error=NULL,error_code='retry_synthesis' WHERE wid=? AND planned=1 AND status='synthesis_error'",(ident,))
                self._bump(db,ident,status='running',phase='generating',message=None)
                db.commit();self._launch(ident,payload['kind'] if action=='retry' else 'all')
            elif action=='finish':
                if ws['status'] in ACTIVE or ws['status']=='scanning': raise InputError('请先暂停任务。')
                if (cache_facts['by_workspace'].get(ident,0) or db.execute("SELECT 1 FROM items WHERE wid=? AND status IN ('save_error','save_pending','saving') LIMIT 1",(ident,)).fetchone()) and payload.get('discard_unsaved') is not True:
                    raise InputError('请先保存尚未保存的完整音频。')
                if payload.get('discard_unsaved') is True:
                    self.jobs.discard_workspace_results(ident)
                db.execute('DELETE FROM items WHERE wid=?',(ident,));db.execute('DELETE FROM files WHERE wid=?',(ident,));db.execute('DELETE FROM requests WHERE wid=?',(ident,))
                self._bump(db,ident,status='done',phase='finished',active_item=None,run_settings=None,verification_origin=None,selection_id=uuid.uuid4().hex)
                with self.cache_lock:
                    for cache in self.cache.glob(ident+'-*'):
                        cache.unlink(missing_ok=True);self.cache_checks.pop(cache.name,None)
            else: raise InputError('操作不存在。')
        return self.summary(ident)

    def snapshot(self,ident,revision=0):
        revision=int(revision)
        with self.db() as db:
            self._ws(db,ident)
            rows=db.execute('SELECT * FROM items WHERE wid=? AND revision>? ORDER BY revision,id LIMIT 101',(ident,revision)).fetchall()
        return {**self.query_summary(ident),'items':[self._item(r) for r in rows[:100]],'reset':len(rows)>100}

    def folders(self,ident,payload):
        with self.lock,self.db() as db:
            ws=self._ws(db,ident)
            if ws['status'] not in {'ready','paused','interrupted','done'} or self.active:
                raise InputError('请先暂停任务，再更换目录。')
            fields={}
            for key,kind in (('input_folder_id','input'),('output_folder_id','output')):
                if key in payload:
                    directory=self.access.directory(payload[key],kind)
                    fields['input_id' if kind=='input' else 'output_id']=payload[key]
                    fields['input_path' if kind=='input' else 'output_path']=str(directory)
            if not fields:raise InputError('请选择需要恢复的目录。')
            if 'output_id' in fields:
                db.execute("UPDATE items SET saved_folder_id=? WHERE wid=? AND status IN ('saved','unverified') AND saved_folder_id IS NULL",(ws['output_id'],ident))
                db.execute("UPDATE items SET status='queued',error=NULL,error_code=NULL WHERE wid=? AND planned=1 AND status='conflict'",(ident,))
            self._bump(db,ident,**fields,message=None)
        return self.summary(ident)

    def restore(self,ident):
        with self.lock,self.db() as db:
            ws=self._ws(db,ident)
            source=self.access.restore(ws['input_id'])
            target=self.access.restore(ws['output_id']) if ws['output_id'] else None
            for row in db.execute('SELECT DISTINCT saved_folder_id FROM items WHERE wid=? AND saved_folder_id IS NOT NULL',(ident,)):
                try:self.access.restore(row['saved_folder_id'])
                except (ValueError,OSError):pass
            pending=db.execute("SELECT 1 FROM items WHERE wid=? AND status='unverified' LIMIT 1",(ident,)).fetchone()
            if pending and target:
                previous=self.threads.get(ident)
                if ws['status']=='scanning' and previous and previous.is_alive():
                    return {'ok':True,'input_folder':source,'output_folder':target}
                self._bump(db,ident,status='scanning',phase='checking',
                           verification_origin='done' if ws['status']=='done' else ws['verification_origin'],message='正在核验已有输出。')
                cancel=self.scan_cancel[ident]=threading.Event()
                db.commit()
                thread=self.threads[ident]=threading.Thread(target=self._verify_restored,args=(ident,cancel),daemon=True)
                thread.start()
            elif not pending and ws['phase']=='recovery_checking':
                # Repair a completed check left in the cancellation phase by an
                # earlier run. No remaining item requires a worker or network.
                self._finish_verification(db,ident)
        return {'ok':True,'input_folder':source,'output_folder':target}

    def _finish_verification(self,db,ident,pending=False,stopped=False):
        ws=self._ws(db,ident)
        completed=bool(ws['verification_origin']=='done' and not pending and not stopped and
            not db.execute("SELECT 1 FROM items WHERE wid=? AND planned=1 AND (status IS NULL OR status!='saved') LIMIT 1",(ident,)).fetchone())
        status='done' if completed else ('interrupted' if ws['run_settings'] or pending or stopped else 'ready')
        phase='done' if completed else ('recovery_checking' if stopped else ('directory_error' if pending else ('recovery' if ws['run_settings'] else 'ready')))
        message='已有输出已核验，可生成新的已选通话。' if completed else ('核验已停止；恢复目录访问可继续剩余核验。' if stopped else ('部分旧目录待恢复。' if pending else '核验完成；可手动继续。'))
        self._bump(db,ident,status=status,phase=phase,message=message,
                   verification_origin=ws['verification_origin'] if pending else None)

    def _verify_restored(self,ident,cancel=None):
        cancel=cancel or threading.Event()
        try:
            with self.db() as db:
                ws=self._ws(db,ident)
                rows=db.execute("SELECT id,filename,fingerprint,planned,saved_folder_id FROM items WHERE wid=? AND status='unverified'",(ident,))
                for index,row in enumerate(rows):
                    if self._cancelled(cancel):break
                    try:
                        receipt=self.access.verify_output(row['saved_folder_id'] or ws['output_id'],row['filename'],row['fingerprint'])
                    except (ValueError,OSError):
                        continue
                    state='saved' if receipt['state']=='saved' else ('conflict' if receipt['state']=='conflict' else ('queued' if row['planned'] else 'ready'))
                    if state!='saved' and ws['verification_origin']:
                        self._bump(db,ident,verification_origin=None)
                        ws=self._ws(db,ident)
                    db.execute('UPDATE items SET status=?,error=?,error_code=? WHERE id=?',
                               (state,None if state=='saved' else '先前输出已变化或缺失。','output_conflict' if state=='conflict' else None,row['id']))
                    if index%100==0:
                        self._bump(db,ident,phase='checking');db.commit()
                pending=db.execute("SELECT 1 FROM items WHERE wid=? AND status='unverified' LIMIT 1",(ident,)).fetchone()
                # A cancel arriving during the final file hash cannot leave a
                # resumable-check state when every item has already completed.
                stopped=self._cancelled(cancel) and pending is not None
                self._finish_verification(db,ident,pending is not None,stopped)
        except (ValueError,OSError):
            with self.db() as db:self._bump(db,ident,status='interrupted',phase='directory_error',message='输出核验中断，请恢复目录后重试。')

    def audio(self,ident,item_id):
        with self.db() as db:
            ws=self._ws(db,ident)
            row=db.execute('SELECT * FROM items WHERE wid=? AND id=?',(ident,item_id)).fetchone()
            if row is None or row['status'] not in {'saved','save_error','saving','save_pending'}: raise KeyError('音频尚未完成。')
            path=self._audio_path(ident,item_id)
            if not path.is_file():
                folder=row['saved_folder_id'] or ws['output_id']
                receipt=self.access.verify_output(folder,row['filename'],row['fingerprint'])
                if receipt['state']!='saved': raise KeyError('已保存文件已变化或不可用。')
                path=self.access.directory(folder,'output')/row['filename']
        return path,row['filename'],row['fingerprint']

    def audio_lease(self,ident,item_id):
        with self.db() as db:
            ws=self._ws(db,ident)
            row=db.execute('SELECT * FROM items WHERE wid=? AND id=?',(ident,item_id)).fetchone()
            if row is None or row['status'] not in {'saved','save_error','saving','save_pending'}:raise KeyError('音频尚未完成。')
            path=self._audio_path(ident,item_id)
            expected_hash,expected_size=row['audio_hash'],row['audio_size']
            if not path.is_file():
                folder=row['saved_folder_id'] or ws['output_id']
                receipt=self.access.verify_output(folder,row['filename'],row['fingerprint'])
                if receipt['state']!='saved':raise KeyError('已保存文件已变化或不可用。')
                path=self.access.directory(folder,'output')/row['filename']
                expected_hash,expected_size=receipt['sha256'],receipt['size']
            before=path.lstat()
            descriptor=os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_BINARY',0))
            handle=os.fdopen(descriptor,'rb')
            try:
                info=os.fstat(handle.fileno())
                if not stat.S_ISREG(info.st_mode) or (info.st_dev,info.st_ino)!=(before.st_dev,before.st_ino) or not 0<info.st_size<=256*1024*1024:
                    raise KeyError('音频文件类型或身份已变化。')
                digest=hashlib.sha256()
                while True:
                    block=handle.read(65536)
                    if not block:break
                    digest.update(block)
                if expected_hash and (info.st_size!=expected_size or digest.hexdigest()!=expected_hash):raise KeyError('音频内容已变化。')
                handle.seek(0)
                return handle,row['filename'],row['fingerprint'],info.st_size
            except BaseException:
                handle.close();raise

    def safety(self):
        try:
            cache_facts=self.reconcile()
            with self.db() as db:
                busy=db.execute("SELECT 1 FROM workspaces WHERE status IN ('scanning','running','pausing') LIMIT 1").fetchone() is not None
                unsaved=db.execute("SELECT 1 FROM items WHERE status IN ('save_error','save_pending','saving') LIMIT 1").fetchone() is not None
                saving=db.execute("SELECT 1 FROM items WHERE status='saving' LIMIT 1").fetchone() is not None
            return {'busy':bool(busy),'unsaved':bool(unsaved or cache_facts['protected']),'saving':bool(saving),'pending':bool(cache_facts['unknown']),
                    'unsaved_audio_count':cache_facts['protected'],'unknown_cache_count':cache_facts['unknown']}
        except Exception:
            return {'busy':True,'unsaved':True,'saving':True,'pending':True,'unknown':True}

    def close(self):
        if self._closed:return
        self.stop.set()
        if self.active_job:
            try:self.jobs.cancel(self.active_job)
            except (KeyError,OSError):pass
        for thread in list(self.threads.values()):
            if thread is not threading.current_thread():thread.join(timeout=5)
        if any(thread.is_alive() and thread is not threading.current_thread() for thread in self.threads.values()):
            raise InputError('工作区后台任务尚未完全停止。')
        with self.db() as db:
            db.execute("UPDATE workspaces SET phase=CASE WHEN phase='settings' THEN 'recovery_settings' WHEN phase='checking' THEN 'recovery_checking' WHEN status='scanning' THEN 'recovery_scan' ELSE 'recovery' END,status='interrupted',active_item=NULL WHERE status IN ('running','pausing','scanning')")
        self.reconcile()
        self._closed=True

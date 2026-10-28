"""Offline, real-filesystem workspace behavior and native trust boundaries."""
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

from native_workspace import NativeWorkspaceAccess
from .app import Jobs, make_server
from .normalize import InputError
from .test_batch_import import xml
from .workspaces import Workspaces


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.input=self.root/'input';self.input.mkdir()
        self.output=self.root/'output';self.output.mkdir()
        self.state=self.root/'state'
        self.access=NativeWorkspaceAccess(self.state)
        self.source=self.access.register(self.input,'input')
        self.target=self.access.register(self.output,'output')
        self.jobs=Jobs()
        self.calls=[]
        def fake(preview):
            self.calls.append(preview['filename'])
            return Path(__file__).parent/'silence.mp3'
        self.work=Workspaces(self.state,self.jobs,self.access,synthesizer=fake)

    def tearDown(self):
        self.work.close();self.jobs.close();self.access.close();self.temp.cleanup()

    def pair(self,prefix,reference=None):
        for role,suffix in [('AGENT','0'),('USER','1')]:
            (self.input/(prefix+suffix+'.xml')).write_text(xml(role,ref=reference or 'fictional-'+prefix),encoding='utf-8')

    def wait(self,ident,scanning=False):
        deadline=time.monotonic()+10
        while time.monotonic()<deadline:
            summary=self.work.summary(ident)
            if (summary['status']!='scanning') if scanning else (self.work.active is None):return self.work.summary(ident)
            time.sleep(.01)
        self.fail('background operation did not finish')

    def import_ready(self):
        ident=self.work.import_folder(self.source['folder_id'])['id']
        result=self.wait(ident,True)
        self.assertEqual(result['status'],'ready',result)
        return ident

    def start(self,ident):
        selection=self.work.select(ident,{'mode':'all'})
        payload={'selection_id':selection['selection_id'],'output_folder_id':self.target['folder_id'],
                 'consent':True,'request_id':'fictional-request-'+ident}
        return self.work.start(ident,payload),payload

    def test_global_pairing_paging_and_private_metadata(self):
        for i in range(120):self.pair(f'{i:06d}')
        (self.input/'missing0.xml').write_text(xml('AGENT',ref='fictional-missing'))
        ident=self.import_ready()
        result=self.work.page(ident,limit=100)
        self.assertEqual(result['total'],121)
        self.assertEqual(result['ready'],120)
        self.assertEqual(len(result['items']),100)
        self.assertEqual(len(self.work.page(ident,page=2)['items']),21)
        self.assertNotIn('fictional-missing',json.dumps(result))
        self.assertNotIn('secret-phone',json.dumps(result))
        invalid=self.work.page(ident,filter='error')['items'][0]
        detail=self.work.detail(ident,invalid['id'])
        self.assertEqual(detail['sources'][0]['name'],'missing0.xml')
        self.assertEqual(detail['sources'][0]['role'],'AGENT')
        self.assertEqual(detail['rows'],[])
        selected=self.work.select(ident,{'mode':'all','q':'0000'})
        self.assertEqual(selected['count'],100)
        self.assertEqual(self.work.page(ident,q='000119')['selection_hidden_count'],100)
        with self.work.db() as db:
            columns={r[1] for r in db.execute('PRAGMA table_info(files)')}
        self.assertNotIn('content',columns)

    def test_output_save_real_receipts_and_start_idempotence(self):
        self.pair('000123')
        ident=self.import_ready()
        _,payload=self.start(ident)
        result=self.wait(ident)
        self.assertEqual(result['saved'],1)
        self.assertEqual(self.work.start(ident,payload)['saved'],1)
        self.assertEqual(self.calls,['000123.mp3'])
        item=self.work.page(ident)['items'][0]
        path,name,fp=self.work.audio(ident,item['id'])
        self.assertEqual(name,'000123.mp3')
        self.assertEqual(path.read_bytes(),(Path(__file__).parent/'silence.mp3').read_bytes())
        self.assertEqual(self.access.verify_output(self.target['folder_id'],name,fp)['state'],'saved')

    def test_save_failure_restart_reuses_complete_cache(self):
        self.pair('001')
        ident=self.import_ready()
        with patch.object(self.access,'save_audio',side_effect=OSError('fictional volume failure')):
            self.start(ident)
            result=self.wait(ident)
        self.assertEqual(result['status'],'paused')
        self.assertEqual(result['error'],1)
        self.work.close()
        self.access.close()
        self.access=NativeWorkspaceAccess(self.state)
        self.work=Workspaces(self.state,self.jobs,self.access,synthesizer=lambda p:self.fail('must not synthesize cached audio'))
        self.access.restore(self.target['folder_id'])
        self.assertFalse(self.work.summary(ident)['input_folder']['valid'])
        self.work.action(ident,'retry',{'kind':'save'})
        self.assertEqual(self.wait(ident)['saved'],1)
        self.assertEqual(self.calls,['001.mp3'])

    def test_save_only_missing_cache_does_not_synthesize_or_run_queued_calls(self):
        self.pair('001');self.pair('002')
        ident=self.import_ready()
        with patch.object(self.access,'save_audio',side_effect=OSError('fictional volume failure')):
            self.start(ident);self.wait(ident)
        row=next(item for item in self.work.page(ident)['items'] if item['filename']=='001.mp3')
        self.work._audio_path(ident,row['id']).unlink()
        with patch.object(self.work,'_synthesize',side_effect=AssertionError('save-only cannot synthesize')) as synthesize:
            self.work.action(ident,'retry',{'kind':'save','consent':False})
            result=self.wait(ident)
            synthesize.assert_not_called()
        items={item['filename']:item for item in self.work.page(ident)['items']}
        self.assertEqual(items['001.mp3']['error_code'],'missing_cache')
        self.assertEqual(items['002.mp3']['status'],'queued')
        self.assertEqual(result['status'],'paused')
        self.assertEqual(self.calls,['001.mp3'])
        self.assertFalse((self.output/'002.mp3').exists())

    def test_save_retry_rejects_unknown_kinds_and_parameters_without_touching_cache(self):
        self.pair('001');ident=self.import_ready()
        with patch.object(self.access,'save_audio',side_effect=OSError('fictional volume failure')):
            self.start(ident);self.wait(ident)
        row=self.work.page(ident)['items'][0]
        cache=self.work._audio_path(ident,row['id']);original=cache.read_bytes()
        for payload in ({'kind':'unknown'},{'kind':None},{'kind':'save','unexpected':True}):
            with self.subTest(payload=payload),self.assertRaises(InputError):self.work.action(ident,'retry',payload)
        self.assertEqual(cache.read_bytes(),original)
        self.assertEqual(self.work.summary(ident)['status'],'paused')

    def test_modified_input_never_sent_and_conflict_never_overwritten(self):
        self.pair('001');self.pair('002')
        ident=self.import_ready()
        (self.input/'0010.xml').write_text(xml('AGENT',ref='fictional-001',text='changed after scan'))
        (self.output/'002.mp3').write_bytes(b'old file')
        self.start(ident)
        result=self.wait(ident)
        self.assertEqual(self.calls,[])
        self.assertEqual(result['error'],2)
        self.assertEqual({i['status'] for i in self.work.page(ident)['items']},{'changed','conflict'})
        self.assertEqual((self.output/'002.mp3').read_bytes(),b'old file')

    def test_pause_at_call_boundary_and_frozen_selection(self):
        self.pair('001');self.pair('002')
        entered,release=threading.Event(),threading.Event()
        def fake(preview):
            entered.set();release.wait(5)
            self.calls.append(preview['filename'])
            return Path(__file__).parent/'silence.mp3'
        self.work.synthesizer=fake
        ident=self.import_ready();self.start(ident)
        self.assertTrue(entered.wait(3))
        self.work.action(ident,'pause',{})
        release.set()
        self.assertEqual(self.wait(ident)['status'],'paused')
        self.assertEqual(len(self.calls),1)
        with self.assertRaises(InputError):self.work.select(ident,{'mode':'clear'})
        with self.assertRaises(InputError):self.work.settings(ident,{'settings':{'rate':1.2}})
        self.work.action(ident,'resume',{'consent':True})
        self.assertEqual(self.wait(ident)['saved'],2)

    def test_three_failures_pause_and_only_failed_retry(self):
        for i in range(4):self.pair(str(i))
        ident=self.import_ready()
        self.work.synthesizer=lambda p:(_ for _ in ()).throw(InputError('offline failed'))
        self.start(ident)
        self.assertEqual(self.wait(ident)['status'],'paused')
        self.assertEqual(self.work.summary(ident)['error'],3)
        self.work.synthesizer=lambda p:Path(__file__).parent/'silence.mp3'
        self.work.action(ident,'retry',{'kind':'synthesis','consent':True})
        self.assertEqual(self.wait(ident)['saved'],3)
        self.work.action(ident,'resume',{'consent':True})
        self.assertEqual(self.wait(ident)['saved'],4)

    def test_change_output_directory_keeps_saved_result_and_retries_only_save(self):
        self.pair('001');self.pair('002');self.pair('003')
        ident=self.import_ready()
        save=self.access.save_audio
        def failure_on_second(folder,source,name,fp):
            if name=='002.mp3':raise OSError('fictional save failure')
            return save(folder,source,name,fp)
        with patch.object(self.access,'save_audio',side_effect=failure_on_second):
            self.start(ident);self.wait(ident)
        new=self.root/'new';new.mkdir()
        grant=self.access.register(new,'output')
        self.work.folders(ident,{'output_folder_id':grant['folder_id']})
        self.work.action(ident,'retry',{'kind':'save','consent':True})
        self.assertEqual(self.wait(ident)['saved'],2)
        self.assertEqual(self.calls,['001.mp3','002.mp3'])
        self.assertTrue((self.output/'001.mp3').exists())
        self.assertTrue((new/'002.mp3').exists())
        first=next(i for i in self.work.page(ident)['items'] if i['filename']=='001.mp3')
        self.assertEqual(self.work.audio(ident,first['id'])[0].parent,self.output)
        self.work.action(ident,'resume',{'consent':True})
        self.assertEqual(self.wait(ident)['saved'],3)
        self.assertTrue((new/'003.mp3').exists())

    def test_interrupted_scan_can_restart_same_workspace(self):
        self.pair('001')
        ident=self.import_ready()
        with self.work.db() as db:
            db.execute("UPDATE workspaces SET status='interrupted',phase='recovery_scan' WHERE id=?",(ident,))
        self.work.action(ident,'resume',{'consent':True})
        result=self.wait(ident,True)
        self.assertEqual(result['ready'],1)
        self.assertEqual(result['total'],1)

    def test_active_legacy_job_does_not_get_replaced(self):
        self.pair('001');ident=self.import_ready()
        selection=self.work.select(ident,{'mode':'all'})
        with patch.object(self.jobs,'items',{'fictional-active':{}}),patch.object(self.jobs,'snapshot',return_value={'status':'running'}):
            with self.assertRaises(InputError):
                self.work.start(ident,{'selection_id':selection['selection_id'],'output_folder_id':self.target['folder_id'],
                                       'consent':True,'request_id':'exclusive-request'})
        self.assertEqual(self.work.summary(ident)['status'],'ready')
        self.assertIsNone(getattr(self.jobs,'batch_active',None))

    def test_audio_fd_keeps_verified_inode_after_path_replacement(self):
        self.pair('001');ident=self.import_ready();self.start(ident);self.wait(ident)
        item=self.work.page(ident)['items'][0]
        handle,filename,fp,size=self.work.audio_lease(ident,item['id'])
        expected=(self.output/filename).read_bytes()
        (self.output/filename).rename(self.output/'old.mp3')
        (self.output/filename).write_bytes(b'replacement')
        with handle:self.assertEqual(handle.read(),expected)
        with self.assertRaises(KeyError):self.work.audio_lease(ident,item['id'])

    def test_normal_save_is_not_reported_as_failure(self):
        self.pair('001');ident=self.import_ready()
        entered,release=threading.Event(),threading.Event()
        save=self.access.save_audio
        def blocking_save(*args):
            entered.set();release.wait(5)
            return save(*args)
        with patch.object(self.access,'save_audio',side_effect=blocking_save):
            self.start(ident);self.assertTrue(entered.wait(3))
            summary=self.work.summary(ident)
            self.assertEqual(summary['error'],0)
            self.assertEqual(summary['counts']['saving'],1)
            self.assertEqual(summary['phase'],'saving')
            release.set();self.wait(ident)

    def test_import_reuses_active_scan_and_rejects_parallel_directory(self):
        self.pair('001')
        entered,release=threading.Event(),threading.Event()
        iterator=self.access.iter_files
        def delayed(folder):
            entered.set();release.wait(5)
            yield from iterator(folder)
        other=self.root/'other';other.mkdir();grant=self.access.register(other,'input')
        with patch.object(self.access,'iter_files',side_effect=delayed):
            first=self.work.import_folder(self.source['folder_id'])
            self.assertTrue(entered.wait(3))
            self.assertEqual(self.work.import_folder(self.source['folder_id'])['id'],first['id'])
            with self.assertRaises(InputError):self.work.import_folder(grant['folder_id'])
            release.set();self.wait(first['id'],True)

    def test_cancel_settings_leaves_offline_resumable_incomplete_state(self):
        for i in range(3):self.pair(str(i))
        ident=self.import_ready()
        entered,release=threading.Event(),threading.Event()
        preview=self.work._preview
        def delayed(*args,**kwargs):
            entered.set();release.wait(5)
            return preview(*args,**kwargs)
        with patch.object(self.work,'_preview',side_effect=delayed):
            self.work.settings(ident,{'settings':{'rate':1.2}})
            self.assertTrue(entered.wait(3))
            self.work.action(ident,'cancel-scan',{})
            release.set()
            stopped=self.wait(ident,True)
        self.assertEqual(stopped['phase'],'recovery_settings')
        self.assertEqual(stopped['status'],'interrupted')
        self.assertFalse(stopped['can_edit'])
        self.work.action(ident,'resume',{})  # offline metadata needs no TTS consent
        finished=self.wait(ident,True)
        self.assertEqual(finished['status'],'ready')
        self.assertEqual(finished['settings']['rate'],1.2)
        self.assertEqual(self.calls,[])

    def test_cancel_restored_verification_only_rechecks_remaining_rows(self):
        for i in range(3):self.pair(str(i))
        ident=self.import_ready();self.start(ident);self.wait(ident)
        self.work.close()
        self.work=Workspaces(self.state,self.jobs,self.access)
        entered,release=threading.Event(),threading.Event()
        verify=self.access.verify_output
        def delayed(*args):
            entered.set();release.wait(5)
            return verify(*args)
        with patch.object(self.access,'verify_output',side_effect=delayed) as checking:
            self.work.restore(ident);self.assertTrue(entered.wait(3))
            self.work.action(ident,'cancel-scan',{})
            release.set();stopped=self.wait(ident,True)
            self.assertEqual(checking.call_count,1)
        self.assertEqual(stopped['phase'],'recovery_checking')
        self.assertEqual(stopped['counts']['unverified'],2)
        self.assertEqual(stopped['saved'],1)
        with patch.object(self.access,'verify_output',wraps=verify) as checking:
            self.work.restore(ident);finished=self.wait(ident,True)
            self.assertEqual(checking.call_count,2)
        self.assertEqual(finished['saved'],3)
        self.assertEqual(len(self.calls),3)

    def test_cancel_during_final_hash_finishes_and_repairs_old_empty_check_phase(self):
        self.pair('001');ident=self.import_ready();self.start(ident);self.wait(ident)
        self.work.close()
        self.work=Workspaces(self.state,self.jobs,self.access,synthesizer=lambda p:self.fail('verification must never synthesize'))
        verify=self.access.verify_output
        def cancel_after_hash(*args):
            receipt=verify(*args)
            self.work.scan_cancel[ident].set()
            return receipt
        with patch.object(self.access,'verify_output',side_effect=cancel_after_hash) as checking:
            self.work.restore(ident);finished=self.wait(ident,True)
            self.assertEqual(checking.call_count,1)
        self.assertEqual(finished['saved'],1)
        self.assertEqual(finished['counts'].get('unverified',0),0)
        self.assertEqual(finished['phase'],'recovery')
        self.assertEqual(len(self.calls),1)
        # An older persisted cancellation phase must also converge without
        # inventing a pending item, starting a thread, or repeating file hashes.
        with self.work.db() as db:
            db.execute("UPDATE workspaces SET phase='recovery_checking',status='interrupted' WHERE id=?",(ident,))
        with patch.object(self.access,'verify_output',wraps=verify) as checking:
            self.work.restore(ident)
            self.assertEqual(checking.call_count,0)
        self.assertEqual(self.work.summary(ident)['phase'],'recovery')

    def test_rename_before_state_commit_recovers_and_protects_complete_audio(self):
        self.pair('001');ident=self.import_ready()
        selection=self.work.select(ident,{'mode':'all'})
        window={}
        class ProcessCrash(BaseException):pass
        def crash(wid,item_id):
            with self.work.db() as db:
                row=db.execute('SELECT status,audio_hash FROM items WHERE id=?',(item_id,)).fetchone()
            window.update(item_id=item_id,status=row['status'],hash=row['audio_hash'],exists=self.work._audio_path(wid,item_id).is_file())
            raise ProcessCrash()
        # Skip only the caller's post-start presentation: no safety check runs
        # between the crash seam and reconstructing the workspace manager.
        with patch.object(self.work,'_after_cache_publish',side_effect=crash),patch.object(self.work,'summary',return_value={'id':ident}),patch('threading.excepthook',lambda args:None):
            self.work.start(ident,{'selection_id':selection['selection_id'],'output_folder_id':self.target['folder_id'],
                                   'consent':True,'request_id':'rename-before-commit'})
            self.work.threads[ident].join(5)
        self.assertEqual(window['status'],'running')
        self.assertIsNone(window['hash'])
        self.assertTrue(window['exists'])
        cache=self.work._audio_path(ident,window['item_id'])
        original=cache.read_bytes()
        self.work.close()
        self.work=Workspaces(self.state,self.jobs,self.access,synthesizer=lambda p:self.fail('cached recovery must not synthesize'))
        self.assertEqual(self.work.page(ident)['items'][0]['status'],'save_pending')
        self.assertTrue(self.work.safety()['unsaved'])
        self.assertTrue(self.work.summary(ident)['has_unsaved_audio'])
        with self.assertRaises(InputError):self.work.action(ident,'finish',{})
        self.assertEqual(cache.read_bytes(),original)
        self.assertEqual(list(self.output.iterdir()),[])
        self.work.action(ident,'retry',{'kind':'save','consent':True})
        self.assertEqual(self.wait(ident)['saved'],1)
        self.assertEqual((self.output/'001.mp3').read_bytes(),original)
        self.assertEqual(self.calls,['001.mp3'])

    def test_safety_and_finish_reconcile_live_cache_facts_and_ignore_tmp(self):
        self.pair('001');ident=self.import_ready()
        row=self.work.page(ident)['items'][0]
        cache=self.work._audio_path(ident,row['id'])
        cache.with_suffix('.tmp').write_bytes(b'incomplete fragment')
        self.assertFalse(self.work.safety()['unsaved'])
        cache.write_bytes((Path(__file__).parent/'silence.mp3').read_bytes())
        with self.work.db() as db:
            db.execute("UPDATE items SET status='queued',planned=1,audio_hash=NULL,audio_size=NULL WHERE id=?",(row['id'],))
        with self.assertRaises(InputError):self.work.action(ident,'finish',{})
        self.assertTrue(self.work.safety()['unsaved'])
        self.assertEqual(self.work.page(ident)['items'][0]['status'],'save_pending')
        with self.assertRaises(InputError):self.work.action(ident,'finish',{})
        self.assertTrue(cache.exists())
        self.work.action(ident,'finish',{'discard_unsaved':True})
        self.assertFalse(cache.exists())
        self.assertFalse(self.work.safety()['unsaved'])

    def test_settings_require_restored_input_without_mutating_ready_metadata(self):
        self.pair('001');ident=self.import_ready()
        self.work.close();self.access.close()
        self.access=NativeWorkspaceAccess(self.state)
        self.work=Workspaces(self.state,self.jobs,self.access)
        before=self.work.summary(ident)
        self.assertFalse(before['can_edit'])
        with self.assertRaises(InputError):self.work.settings(ident,{'settings':{'rate':1.2}})
        after=self.work.summary(ident)
        self.assertEqual(after['ready'],before['ready'])
        self.assertEqual(after['settings'],before['settings'])

    def test_honorific_evidence_uses_only_same_visible_matching_rule(self):
        for prefix,text,muted in [('001','女士，您好。',False),('002','他说“女士，您好”。',False),('003','女士，您好。',True)]:
            (self.input/(prefix+'0.xml')).write_text(xml('AGENT',ref='fictional-'+prefix,text=text,mute=muted))
            (self.input/(prefix+'1.xml')).write_text(xml('USER',ref='fictional-'+prefix,text='你好'))
        ident=self.import_ready()
        for item in self.work.page(ident)['items']:
            detail=self.work.detail(ident,item['id'])
            choice=detail['voice_choice']
            if item['filename']=='001.mp3':
                self.assertEqual(choice['address'],'female')
                evidence=next(r for r in detail['rows'] if r['id']==choice['evidence_row_id'])
                self.assertEqual(evidence['speaker'],'AGENT')
                self.assertFalse(evidence.get('muted'))
            else:self.assertNotIn('evidence_row_id',choice)

    def test_missing_or_false_consent_never_starts_or_resumes_synthesis(self):
        self.pair('001');ident=self.import_ready()
        selection=self.work.select(ident,{'mode':'all'})
        for extra in ({},{'consent':False}):
            with self.assertRaises(InputError):
                self.work.start(ident,{'selection_id':selection['selection_id'],'output_folder_id':self.target['folder_id'],
                                       'request_id':'no-network-consent',**extra})
        self.assertEqual(self.calls,[])
        self.work.synthesizer=lambda p:(_ for _ in ()).throw(InputError('fictional service error'))
        self.start(ident);self.wait(ident)
        for action,extra in [('resume',{}),('retry',{'kind':'synthesis'})]:
            for consent in ({},{'consent':False}):
                with self.assertRaises(InputError):self.work.action(ident,action,{**extra,**consent})

    def test_valid_but_changed_complete_cache_is_retained_until_explicit_discard(self):
        self.pair('001');ident=self.import_ready()
        with patch.object(self.access,'save_audio',side_effect=OSError('fictional volume error')):
            self.start(ident);self.wait(ident)
        row=self.work.page(ident)['items'][0]
        cache=self.work._audio_path(ident,row['id'])
        changed=cache.read_bytes()*2  # still valid fixed-format MP3, different hash and size
        cache.write_bytes(changed)
        self.assertTrue(self.work.safety()['unsaved'])
        self.work.action(ident,'retry',{'kind':'save','consent':True});self.wait(ident)
        self.assertEqual(cache.read_bytes(),changed)
        self.assertEqual(self.calls,['001.mp3'])
        with self.assertRaises(InputError):self.work.action(ident,'finish',{})
        self.work.action(ident,'finish',{'discard_unsaved':True})
        self.assertFalse(cache.exists())

    def test_receipt_committed_cache_not_yet_removed_does_not_resynthesize(self):
        self.pair('001');ident=self.import_ready();self.start(ident);self.wait(ident)
        row=self.work.page(ident)['items'][0]
        cache=self.work._audio_path(ident,row['id'])
        expected=(self.output/'001.mp3').read_bytes()
        self.work.close()
        cache.write_bytes(expected)  # exact durable receipt -> cache cleanup boundary
        self.work=Workspaces(self.state,self.jobs,self.access,synthesizer=lambda p:self.fail('receipt recovery must not synthesize'))
        self.work.restore(ident)
        self.work.action(ident,'retry',{'kind':'save','consent':True})
        self.assertEqual(self.wait(ident)['saved'],1)
        self.assertEqual((self.output/'001.mp3').read_bytes(),expected)
        self.assertFalse(cache.exists())

    def test_save_queue_seeks_are_bounded_with_sixty_thousand_items(self):
        with self.work.db() as db:
            db.executemany('INSERT INTO items(wid,id,filename,status,planned) VALUES(?,?,?,?,1)',
                           (('large-query',f'query-{i:06d}',f'{i:06d}.mp3','saved') for i in range(60000)))
            queries=[]
            db.set_trace_callback(queries.append)
            steps=[]
            db.set_progress_handler(lambda:steps.append(1) or 0,100)
            self.assertIsNone(self.work._next_save(db,'large-query'))
            db.set_progress_handler(None,0)
            db.set_trace_callback(None)
            self.assertLessEqual(len(steps),10)  # VM work bound, no flaky wall-clock deadline
            self.assertEqual(len(queries),2)
            for query in queries:
                plan=' '.join(row[3] for row in db.execute('EXPLAIN QUERY PLAN '+query))
                self.assertIn('USING INDEX item_queue',plan)
                self.assertNotIn('SCAN ',plan)
                self.assertNotIn('TEMP B-TREE',plan)
            # A large matching population must still fetch just one candidate
            # per state, then choose the first filename across those candidates.
            db.execute("UPDATE items SET status='save_error' WHERE wid='large-query'")
            db.execute("UPDATE items SET status='save_pending' WHERE id='query-000003'")
            steps=[]
            db.set_progress_handler(lambda:steps.append(1) or 0,100)
            self.assertEqual(self.work._next_save(db,'large-query')['id'],'query-000000')
            db.set_progress_handler(None,0)
            self.assertLessEqual(len(steps),10)

    def test_restart_verifies_real_output_and_finish_keeps_mp3_receipt(self):
        self.pair('001')
        ident=self.import_ready();self.start(ident);self.wait(ident)
        self.work.close()
        self.work=Workspaces(self.state,self.jobs,self.access)
        self.assertEqual(self.work.summary(ident)['saved'],0)
        (self.output/'001.mp3').write_bytes(b'modified')
        self.work.restore(ident);self.wait(ident,True)
        restored=self.work.page(ident)
        self.assertEqual(restored['items'][0]['status'],'conflict')
        self.assertEqual(restored['items'][0]['error_code'],'output_conflict')
        self.assertEqual(restored['issues']['conflict'],1)
        self.work.action(ident,'finish',{})
        self.assertEqual(self.work.summary(ident)['total'],0)
        self.assertEqual((self.output/'001.mp3').read_bytes(),b'modified')

    def test_restore_verified_output_clears_prior_issue_code(self):
        self.pair('001')
        ident=self.import_ready();self.start(ident);self.wait(ident)
        with self.work.db() as db:
            db.execute("UPDATE items SET error_code='output_conflict',error='先前的冲突提示' WHERE wid=?",(ident,))
        self.work.close()
        self.work=Workspaces(self.state,self.jobs,self.access,synthesizer=lambda preview:self.fail('restore must not synthesize'))
        self.work.restore(ident);self.wait(ident,True)
        restored=self.work.page(ident)
        self.assertEqual(restored['items'][0]['status'],'saved')
        self.assertIsNone(restored['items'][0]['error_code'])
        self.assertEqual(restored['issues']['total'],0)


class WorkspaceHTTPTests(unittest.TestCase):
    def test_global_safety_route_priority_types_and_private_auth(self):
        with tempfile.TemporaryDirectory() as root:
            server=make_server(0,state_dir=Path(root)/'state',native_token='private-safety-token')
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            base='http://127.0.0.1:'+str(server.server_port)
            try:
                def get():return json.loads(urllib.request.urlopen(base+'/api/workspaces/safety',timeout=5).read())
                def post(token,app=False):
                    req=urllib.request.Request(base+'/api/native/workspaces/safety',data=b'{}',headers={'X-App-Token' if app else 'X-Native-Token':token,'Content-Type':'application/json'})
                    return json.loads(urllib.request.urlopen(req,timeout=5).read())
                for result in (get(),post('private-safety-token')):
                    self.assertEqual({k:result[k] for k in ('busy','unsaved','saving','pending')},dict.fromkeys(('busy','unsaved','saving','pending'),False))
                with self.assertRaises(urllib.error.HTTPError) as issue:post(server.token,app=True)
                self.assertEqual(issue.exception.code,403)
                for fake in ({'busy':0,'unsaved':False,'saving':False,'pending':False},{'busy':False}):
                    with patch.object(server.workspaces,'safety',return_value=fake):
                        for result in (get(),post('private-safety-token')):
                            self.assertTrue(result['unknown'])
                            self.assertTrue(all(result[k] is True for k in ('busy','unsaved','saving','pending')))
                with patch.object(server.workspaces,'safety',side_effect=OSError('fictional storage failure')):
                    self.assertTrue(get()['unknown'])
            finally:
                server.shutdown();server.server_close();server.jobs.close();server.native_access.close();thread.join(3)

    def test_native_token_never_replaced_by_browser_token(self):
        with tempfile.TemporaryDirectory() as root:
            server=make_server(0,state_dir=Path(root)/'state',native_token='fictional-private-native-token')
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            base='http://127.0.0.1:'+str(server.server_port)
            try:
                def request(path,payload,headers):
                    req=urllib.request.Request(base+path,data=json.dumps(payload).encode(),headers={'Content-Type':'application/json',**headers})
                    return json.loads(urllib.request.urlopen(req,timeout=5).read())
                for headers in ({'X-App-Token':server.token},{'X-Native-Token':'wrong'}, {'X-Native-Token':'fictional-private-native-token','Origin':'https://evil.invalid'}):
                    with self.assertRaises(urllib.error.HTTPError) as error:
                        request('/api/native/folders',{'path':root,'kind':'input'},headers)
                    self.assertEqual(error.exception.code,403)
                grant=request('/api/native/folders',{'path':root,'kind':'input'},{'X-Native-Token':'fictional-private-native-token'})
                self.assertTrue(grant['valid'])
                config=json.loads(urllib.request.urlopen(base+'/api/config').read())
                self.assertNotIn('native_token',config)
                self.assertNotIn('fictional-private-native-token',json.dumps(config))
                result=request('/api/workspaces/import',{'input_folder_id':grant['folder_id']},{'X-App-Token':server.token})
                self.assertIn('id',result)
            finally:
                server.shutdown();server.server_close();server.jobs.close();server.native_access.close();thread.join(3)


if __name__=='__main__':unittest.main()

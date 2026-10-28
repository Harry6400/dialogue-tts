"""Offline ownership, transfer fidelity and persistent worker boundaries."""
from contextlib import contextmanager
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import uuid

from native_workspace import NativeWorkspaceAccess
from .app import close_resources
from .jobs import Jobs
from .normalize import InputError
from .test_batch_import import xml
from .workspaces import Workspaces

AUDIO=(Path(__file__).parent/'silence.mp3').read_bytes()
DIGEST=hashlib.sha256(AUDIO).hexdigest()


def completed(jobs, *, context=None, name='001.mp3', fingerprint='a'*64, audio=AUDIO):
    ident=uuid.uuid4().hex
    persistent=context is not None and jobs._persistent_root is not None
    folder=(jobs._persistent_root if persistent else jobs.root)/ident;folder.mkdir()
    (folder/'dialogue.mp3').write_bytes(audio)
    (folder/'normalized.json').write_text('{"lines":[]}')
    (folder/'timeline.json').write_text('[]')
    (folder/'worker_status.json').write_text('{"status":"done","completed":1,"total":1}')
    jobs.items[ident]={'folder':folder,'process':None,'pid':2147483647,'timer':None,'leases':0,
        'filename':name,'fingerprint':fingerprint,'request_binding':'fictional-binding','total':1,
        'cancelled':False,'timed_out':False,'audition':False,'persistent':persistent,'context':context}
    jobs._persist(ident,jobs.items[ident])
    return ident


class JobOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.state=Path(self.temp.name)/'state'
        self.jobs=Jobs(state_dir=self.state);self.addCleanup(lambda:self.jobs.close())

    def test_result_lease_blocks_release_and_close(self):
        ident=completed(self.jobs)
        with self.jobs.open_result(ident) as result:
            self.assertEqual((result.size,result.sha256,result.read()),(len(AUDIO),DIGEST,AUDIO))
            self.assertEqual(result.local_path.read_bytes(),AUDIO)
            with self.assertRaises(InputError):self.jobs.release(ident)
            with self.assertRaises(InputError):self.jobs.close()
        with self.assertRaises(InputError):_ = result.local_path
        self.jobs.release(ident);self.assertFalse(self.jobs.is_known(ident))

    def test_batch_claim_is_exclusive_and_wrong_release_cannot_unlock(self):
        self.jobs.claim_batch('workspace:a')
        with self.assertRaises(InputError):self.jobs.ensure_available()
        with self.assertRaises(InputError):self.jobs.claim_batch('legacy:b')
        with self.assertRaises(InputError):self.jobs.release_batch('legacy:b')
        self.jobs.ensure_available('workspace:a')
        self.jobs.release_batch('workspace:a');self.jobs.ensure_available()

    def test_scoped_discard_is_atomic_while_a_result_is_leased(self):
        first=completed(self.jobs,context={'workspace_id':'b'*32,'item_id':'c'*32})
        second=completed(self.jobs,context={'workspace_id':'b'*32,'item_id':'d'*32})
        unrelated=completed(self.jobs,context={'workspace_id':'e'*32,'item_id':'f'*32})
        with self.jobs.open_result(second) as result:
            with self.assertRaises(InputError):self.jobs.discard_workspace_results('b'*32)
            self.assertTrue(all(self.jobs.is_known(ident) for ident in (first,second,unrelated)))
        self.assertEqual(self.jobs.discard_workspace_results('b'*32),2)
        self.assertTrue(self.jobs.is_known(unrelated))

    def test_capacity_never_evicts_complete_production_results(self):
        ids=[completed(self.jobs,name=f'{number:03d}.mp3') for number in range(30)]
        with patch('web_dialogue.jobs.subprocess.Popen') as spawn:
            with self.assertRaises(InputError):self.jobs.start({'lines':[]},filename='999.mp3')
        spawn.assert_not_called()
        self.assertTrue(all(self.jobs.is_known(ident) for ident in ids))
        self.assertEqual(self.jobs.temporary_bytes(),sum(p.stat().st_size for p in self.jobs.root.glob('*/*')))

    def test_jobs_close_is_local_and_composition_attempts_all_then_retries(self):
        upper=Mock();self.jobs.workspaces=upper;self.jobs.batches=upper
        self.jobs.close();upper.close.assert_not_called()
        components={name:Mock() for name in ('workspaces','batches','jobs','reports','audio_captures','native_access')}
        components['reports'].close.side_effect=[OSError('fictional locked report'),None]
        server=SimpleNamespace(**components)
        self.assertEqual(close_resources(server),{'ok':False,'errors':['reports']})
        self.assertTrue(all(component.close.call_count==1 for component in components.values()))
        self.assertEqual(close_resources(server),{'ok':True,'errors':[]})
        self.assertEqual(components['reports'].close.call_count,2)
        self.assertTrue(all(component.close.call_count==1 for name,component in components.items() if name!='reports'))
        self.assertEqual(close_resources(server),{'ok':True,'errors':[]})

    def test_optional_native_safety_flags_are_strict_booleans(self):
        from native_close import merge_close_safety
        page=dict(running=False,saving=False,pending=False,unsaved=False)
        backend=dict(busy=False,saving=False,pending=False,unsaved=False)
        for value in ('false',None,0,[]):
            self.assertIsNone(merge_close_safety(page,{**backend,'legacy_unsaved':value}))
        self.assertIs(merge_close_safety(page,{**backend,'legacy_unsaved':True})['unsaved'],True)

    def test_shutdown_failure_still_attempts_other_owned_workers_and_keeps_results(self):
        first=completed(self.jobs);second=completed(self.jobs)
        with patch.object(self.jobs,'kill',side_effect=[OSError('fictional stop failure'),None]) as stop:
            with self.assertRaises(InputError):self.jobs.close()
        self.assertEqual(stop.call_count,2)
        self.assertTrue(all(self.jobs.is_known(ident) for ident in (first,second)))
        self.assertTrue(all((self.jobs.items[ident]['folder']/'dialogue.mp3').exists() for ident in (first,second)))

    def test_persistent_source_survives_close_restart_without_auxiliary_text(self):
        context={'workspace_id':'b'*32,'item_id':'c'*32}
        ident=completed(self.jobs,context=context)
        with self.jobs.open_result(ident) as result:self.assertEqual(result.read(),AUDIO)
        self.assertEqual(self.jobs.snapshot(ident)['status'],'done')
        with self.jobs.open_result(ident) as result:self.assertEqual(result.read(),AUDIO)
        folder=self.jobs.items[ident]['folder'];self.jobs.close()
        self.assertTrue((folder/'dialogue.mp3').is_file())
        self.assertFalse((folder/'normalized.json').exists())
        with patch('web_dialogue.jobs._pid_alive',return_value=False):self.jobs=Jobs(state_dir=self.state)
        result=self.jobs.recovery_results()[0]
        self.assertEqual((result['id'],result['context'],result['size'],result['sha256']),(ident,context,len(AUDIO),DIGEST))
        with self.jobs.open_result(ident,recovery=True) as lease:self.assertEqual(lease.read(),AUDIO)
        self.jobs.release(ident)

    def test_native_manual_source_stays_temporary_and_legacy_links_live(self):
        ident=completed(self.jobs)
        folder=self.jobs.items[ident]['folder']
        for kind in ('audio','normalized','timeline'):
            with self.jobs.open_result(ident,kind) as result:self.assertGreater(len(result.read()),0)
        self.assertFalse((folder/'recovery.json').exists())
        self.assertEqual(self.jobs.recovery_results(),[])
        self.jobs.close();self.assertFalse(folder.exists())

    def test_orphan_live_then_finishes_is_rechecked_without_another_restart(self):
        ident=uuid.uuid4().hex;folder=self.state/'worker-results'/ident;folder.mkdir()
        context={'workspace_id':'d'*32,'item_id':'e'*32}
        process=subprocess.Popen([sys.executable,'-c','import time;time.sleep(20)'],start_new_session=os.name!='nt')
        self.addCleanup(lambda:process.poll() is None and process.kill())
        (folder/'recovery.json').write_text(json.dumps({'version':1,'id':ident,'filename':'001.mp3',
            'fingerprint':'a'*64,'total':1,'request_binding':'fictional','context':context,'pid':process.pid}))
        self.jobs.close();self.jobs=Jobs(state_dir=self.state)
        self.assertTrue(self.jobs.recovery_safety()['unknown'])
        self.assertTrue(self.jobs.is_known(ident))
        (folder/'dialogue.mp3').write_bytes(AUDIO)
        process.terminate();process.wait(timeout=5)
        facts=self.jobs.recovery_safety()
        self.assertFalse(facts['unknown']);self.assertEqual(facts['results'][0]['id'],ident)
        self.jobs.release(ident)

    def test_failed_launch_cleans_only_own_incomplete_job(self):
        existing=completed(self.jobs)
        with patch('web_dialogue.jobs.subprocess.Popen',side_effect=OSError('fictional spawn failure')):
            with self.assertRaises(OSError):self.jobs.start({'lines':[]},filename='999.mp3')
        self.assertEqual(set(self.jobs.items),{existing})

    def test_failed_pid_commit_terminates_own_child_without_losing_complete_output(self):
        original=subprocess.Popen;created=[];persist=self.jobs._persist
        def spawn(command,**kwargs):
            if command[0] != sys.executable:return original(command,**kwargs)
            process=original([sys.executable,'-c','import time;time.sleep(20)'],**kwargs);created.append(process);return process
        def fail_after_launch(ident,item):
            if item['process'] is not None:raise OSError('fictional pid commit failure')
            return persist(ident,item)
        with patch('web_dialogue.jobs.subprocess.Popen',side_effect=spawn),patch.object(self.jobs,'_persist',side_effect=fail_after_launch):
            with self.assertRaises(OSError):self.jobs.start({'lines':[]},filename='001.mp3',fingerprint='a'*64,recovery_context={'workspace_id':'b'*32,'item_id':'c'*32})
        self.assertEqual(len(created),1);self.assertIsNotNone(created[0].poll());self.assertEqual(self.jobs.items,{})


class WorkerTransferTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.state=self.root/'state'
        self.input=self.root/'input';self.input.mkdir();self.output=self.root/'output';self.output.mkdir()
        for role,suffix in (('AGENT','0'),('USER','1')):(self.input/f'001{suffix}.xml').write_text(xml(role,ref='fictional-transfer'),encoding='utf-8')
        self.access=NativeWorkspaceAccess(self.state)
        self.source=self.access.register(self.input,'input');self.target=self.access.register(self.output,'output')
        self.jobs=Jobs(state_dir=self.state);self.work=Workspaces(self.state,self.jobs,self.access)
        self.ident=self.work.import_folder(self.source['folder_id'])['id'];self.wait(scan=True)
        self.item=self.work.page(self.ident)['items'][0]
        self.started=[]
        def fake_start(script,**kwargs):
            ident=completed(self.jobs,context=kwargs['recovery_context'],name=kwargs['filename'],fingerprint=kwargs['fingerprint'])
            self.started.append(ident);return self.jobs.snapshot(ident)
        self.start_patch=patch.object(self.jobs,'start',side_effect=fake_start);self.start_patch.start()

    def tearDown(self):
        self.start_patch.stop();self.work.close();self.jobs.close();self.access.close();self.temp.cleanup()

    def wait(self,scan=False):
        deadline=time.monotonic()+10
        while time.monotonic()<deadline:
            result=self.work.query_summary(self.ident)
            if (result['status']!='scanning') if scan else self.work.active is None:
                return self.work.query_summary(self.ident)
            time.sleep(.01)
        self.fail('offline work did not settle')

    def start(self):
        selected=self.work.select(self.ident,{'mode':'all'})
        self.work.start(self.ident,{'consent':True,'selection_id':selected['selection_id'],
            'output_folder_id':self.target['folder_id'],'request_id':'fictional-transfer'})

    def test_index_projection_does_not_run_cache_reconciliation(self):
        with patch.object(self.work,'reconcile',side_effect=AssertionError('index must stay a query')):
            self.assertEqual(self.work.query_summary(self.ident)['ready'],1)
            self.assertEqual(len(self.work.page(self.ident)['items']),1)
            self.assertIn('items',self.work.snapshot(self.ident))

    def corrupted_transfer(self,short):
        real_open=self.jobs.open_result
        @contextmanager
        def bad_open(ident,*args,**kwargs):
            with real_open(ident,*args,**kwargs) as original:
                stream=io.BytesIO(AUDIO[:144] if short else AUDIO)
                yield SimpleNamespace(read=stream.read,size=original.size,sha256=original.sha256 if short else 'f'*64)
        with patch.object(self.jobs,'open_result',side_effect=bad_open),patch.object(self.work,'_adopt_worker_result',side_effect=OSError('fictional blocked retry')):
            self.start();result=self.wait()
            self.assertEqual(result['saved'],0);self.assertEqual(result['status'],'paused')
            self.assertEqual(list(self.output.glob('*.mp3')),[])
            self.assertTrue(self.jobs.is_known(self.started[0]))
            self.assertEqual((self.jobs.items[self.started[0]]['folder']/'dialogue.mp3').read_bytes(),AUDIO)
            self.assertFalse(self.work._audio_path(self.ident,self.item['id']).exists())
        self.work.action(self.ident,'retry',{'kind':'save'})
        self.assertEqual(self.wait()['saved'],1)
        self.assertEqual((self.output/'001.mp3').read_bytes(),AUDIO)
        self.assertEqual(len(self.started),1)

    def test_short_legal_mp3_stream_preserves_complete_source_for_save_retry(self):self.corrupted_transfer(True)
    def test_digest_mismatch_preserves_complete_source_for_save_retry(self):self.corrupted_transfer(False)

    def test_explicit_finish_discards_bound_source_without_orphaning_it(self):
        with patch.object(self.work,'_sync_cache',side_effect=OSError('fictional full volume')),patch.object(self.work,'_adopt_worker_result',side_effect=OSError('fictional full volume')):
            self.start();self.wait()
            self.assertTrue(self.jobs.is_known(self.started[0]))
            with self.assertRaises(InputError):self.work.action(self.ident,'finish',{})
            self.work.action(self.ident,'finish',{'discard_unsaved':True})
        self.assertFalse(self.jobs.is_known(self.started[0]))
        self.assertFalse(self.work.safety()['pending']);self.assertFalse(self.work.safety()['unsaved'])
        self.assertEqual(list(self.output.glob('*.mp3')),[])

    def test_changed_binding_stays_unknown_and_requires_explicit_discard(self):
        with patch.object(self.work,'_sync_cache',side_effect=OSError('fictional full volume')),patch.object(self.work,'_adopt_worker_result',side_effect=OSError('fictional full volume')):
            self.start();self.wait()
        self.work._audio_path(self.ident,self.item['id']).unlink(missing_ok=True)
        with self.work.db() as db:db.execute('UPDATE items SET fingerprint=? WHERE id=?',('f'*64,self.item['id']))
        safety=self.work.safety()
        self.assertTrue(safety['pending']);self.assertTrue(self.jobs.is_known(self.started[0]))
        self.assertEqual(list(self.output.glob('*.mp3')),[])
        self.work.action(self.ident,'finish',{'discard_unsaved':True})
        self.assertFalse(self.jobs.is_known(self.started[0]));self.assertFalse(self.work.safety()['pending'])

    def test_restart_adopts_frozen_complete_source_without_synthesis(self):
        with patch.object(self.work,'_sync_cache',side_effect=OSError('fictional full volume')),patch.object(self.work,'_adopt_worker_result',side_effect=OSError('fictional full volume')):
            self.start();self.wait();self.work.close()
        self.jobs.close()
        with patch('web_dialogue.jobs._pid_alive',return_value=False):self.jobs=Jobs(state_dir=self.state)
        self.start_patch.stop()
        self.work=Workspaces(self.state,self.jobs,self.access)
        self.assertEqual(self.work.query_summary(self.ident)['save_pending'],1)
        self.assertEqual(self.jobs.recovery_results(),[])
        with patch.object(self.jobs,'start',side_effect=AssertionError('resume save must not synthesize')):
            self.work.action(self.ident,'retry',{'kind':'save'});self.assertEqual(self.wait()['saved'],1)
        self.assertEqual((self.output/'001.mp3').read_bytes(),AUDIO)


if __name__=='__main__':unittest.main()

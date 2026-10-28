from pathlib import Path
import hashlib
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import zipfile
from .batches import Batches
from .jobs import Jobs, ResultLease
from .normalize import InputError
from .test_batch_import import xml, f

class FakeJobs(Jobs):
    def __init__(self):
        super().__init__()
        self.calls=[];self.fail={'bad'};self.wait=False
    def start(self,script,batch_owner=None,fingerprint=None,filename=None):
        self.ensure_available(batch_owner)
        ident=str(len(self.calls));text=script['lines'][0]['text'];self.calls.append(text)
        folder=self.root/ident;folder.mkdir();(folder/'dialogue.mp3').write_bytes(b'fake-mp3')
        owner=self
        class Process:
            def poll(self):return None if owner.items[ident]['status']=='running' else 0
            def wait(self,timeout=None):
                if self.poll() is None:raise subprocess.TimeoutExpired('fake',timeout)
                return 0
        self.items[ident]={'folder':folder,'status':'running' if self.wait else ('error' if text in self.fail else 'done'),
                           'process':Process(),'leases':0,'timer':None,'persistent':False,'filename':filename,'fingerprint':fingerprint}
        return {'id':ident}
    def snapshot(self,ident):return {'id':ident,'status':self.items[ident]['status'],'completed':1,'total':1}
    def cancel(self,ident):self.items[ident]['status']='cancelled';return self.snapshot(ident)
    def result_available(self,ident):return self.items[ident]['status']=='done'
    def open_result(self,ident,kind='audio',recovery=False):
        item=self.items[ident];handle=(item['folder']/'dialogue.mp3').open('rb');item['leases']+=1
        return ResultLease(self,ident,handle,8,item['filename'],item['fingerprint'],hashlib.sha256(b'fake-mp3').hexdigest(),item['folder']/'dialogue.mp3')

class BatchTests(unittest.TestCase):
    def setUp(self): self.jobs=FakeJobs(); self.b=Batches(self.jobs)
    def tearDown(self): self.b.close();self.jobs.close()
    def payload(self, texts=('good','bad')):
        return {'files':[f(xml(role, ref=f'fictional-{i}', text=t if role=='AGENT' else '答复'), f'{i+1:03d}{0 if role=="AGENT" else 1}.xml') for i,t in enumerate(texts) for role in ('AGENT','USER')], 'consent':True,'request_id':'request-123'}
    def finish(self, ident):
        deadline=time.monotonic()+5
        while self.b.active and time.monotonic()<deadline: time.sleep(.01)
        self.assertIsNone(self.b.active)
        return self.b.snapshot(ident)
    def test_fifty_call_batch_generates_all(self):
        payload = self.payload(tuple('good' for _ in range(50)))
        result = self.finish(self.b.start(payload)['id'])
        self.assertEqual(result['completed'], 50)
        self.assertEqual(len(self.jobs.calls), 50)
        for row in result['items']:
            self.assertRegex(row['fingerprint'], r'^[0-9a-f]{64}$')
            number = row['download_url'].rsplit('/', 1)[1]
            self.assertEqual(self.b.audio_fingerprint(result['id'], number), row['fingerprint'])
        with zipfile.ZipFile(self.b.output(result['id'])) as archive:
            self.assertEqual(len(archive.namelist()), 50)

    def test_failure_isolation_retry_and_idempotence(self):
        p=self.payload(('good','bad','later')); ident=self.b.start(p)['id']
        self.assertEqual(self.b.start(p)['id'],ident)
        self.assertEqual(self.finish(ident)['completed'],2)
        self.jobs.fail.clear();self.b.retry(ident)
        self.assertEqual(self.finish(ident)['completed'],3)
        self.assertEqual(self.jobs.calls,['good','bad','later','bad'])
        with self.assertRaises(InputError): self.b.retry(ident)
        self.assertEqual(self.b.start(p)['id'],ident)
        with self.assertRaises(InputError): self.b.start(self.payload(('changed',)))
    def test_cancel_does_not_repeat_success(self):
        self.jobs.wait=True; ident=self.b.start(self.payload())['id']
        while not self.jobs.calls: time.sleep(.01)
        self.b.cancel(ident);result=self.finish(ident)
        self.assertEqual(result['status'],'cancelled');self.assertEqual(len(self.jobs.calls),1)
        with self.assertRaises(InputError): self.b.retry(ident)
    def test_zip_safe_and_audio_individual(self):
        ident=self.b.start(self.payload(('good','good')))['id'];self.finish(ident)
        with zipfile.ZipFile(self.b.output(ident)) as z:
            self.assertEqual(z.namelist(),['001.mp3','002.mp3'])
            self.assertEqual(z.read('001.mp3'),b'fake-mp3')
        self.assertEqual(self.b.output(ident,'001').read_bytes(),b'fake-mp3')
        with self.assertRaises(KeyError): self.b.output(ident,'../../secret')
    def test_zip_cached_and_open_snapshot_stable(self):
        ident=self.b.start(self.payload(('good','bad')))['id'];self.finish(ident)
        target=self.b.output(ident);before=target.stat().st_mtime_ns
        handle, name, size=self.b.open_output(ident)
        self.assertEqual(self.b.output(ident).stat().st_mtime_ns,before)
        with handle, zipfile.ZipFile(handle) as z:
            self.assertEqual(z.namelist(),['001.mp3'])
        self.jobs.fail.clear();self.b.retry(ident);self.finish(ident)
        with zipfile.ZipFile(self.b.output(ident)) as z:
            self.assertEqual(len(z.namelist()),2)

    def test_windows_locked_zip_retry_preserves_old_download(self):
        ident=self.b.start(self.payload(('good','bad')))['id'];self.finish(ident)
        old=self.b.output(ident); handle, _, _=self.b.open_output(ident)
        self.jobs.fail.clear()
        with patch.object(Path,'unlink',side_effect=PermissionError('Windows sharing violation')):
            self.b.retry(ident);self.finish(ident)
            new=self.b.output(ident)
            self.assertNotEqual(old,new)
            with zipfile.ZipFile(handle) as z:self.assertEqual(len(z.namelist()),1)
            with zipfile.ZipFile(new) as z:self.assertEqual(len(z.namelist()),2)
        handle.close()

    def test_full_results_are_not_automatically_retired_at_capacity(self):
        ids=[]
        for index in range(5):
            payload=self.payload(('good',));payload['request_id']=f'request-{index:03d}'
            ident=self.b.start(payload)['id'];self.finish(ident);ids.append(ident)
        payload=self.payload(('good',));payload['request_id']='request-over-limit'
        with self.assertRaises(InputError):self.b.start(payload)
        self.assertTrue(all(self.b.output(ident,'001').read_bytes()==b'fake-mp3' for ident in ids))
        self.b.release(ids[0])
        self.finish(self.b.start(payload)['id'])

    def test_zip_generations_bounded_while_downloads_open(self):
        ident=self.b.start(self.payload())['id'];self.finish(ident);handles=[]
        for _ in range(3):
            handles.append(self.b.open_output(ident)[0])
            self.b.retry(ident);self.finish(ident)
        with self.assertRaises(InputError):self.b.output(ident)
        for handle in handles:handle.close()
        self.assertTrue(self.b.output(ident).is_file())
        self.assertEqual(len(list((self.b.root/ident).glob('*.zip'))),1)

    def test_active_download_not_evicted(self):
        leases=[]
        for i in range(5):
            p=self.payload(('good',));p['request_id']=f'request-{i:03d}'
            ident=self.b.start(p)['id'];self.finish(ident)
            leases.append(self.b.open_output(ident)[0])
        p=self.payload(('good',));p['request_id']='request-new-batch'
        with self.assertRaises(InputError):self.b.start(p)
        for handle in leases:handle.close()
        self.b.release(next(iter(self.b.items)))
        self.finish(self.b.start(p)['id'])

    def test_excluded_group_preserves_public_number_and_filename(self):
        p=self.payload(('good','good'));p['excluded']=['g0_1']
        ident=self.b.start(p)['id'];result=self.finish(ident)
        self.assertEqual(result['items'][0]['name'],'对话002')
        self.assertEqual(result['items'][0]['filename'],'002.mp3')
        self.assertTrue(result['items'][0]['download_url'].endswith('/002'))
        handle,name,_=self.b.open_output(ident,'002');handle.close()
        self.assertEqual(name,'002.mp3')
        with zipfile.ZipFile(self.b.output(ident)) as z:self.assertEqual(z.namelist(),['002.mp3'])

    def test_invalid_groups_exclusion(self):
        p=self.payload();p['files'][2]['content']='not xml'
        ident=self.b.start(p)['id']
        self.assertEqual(self.finish(ident)['completed'],1)
    def test_parallel_batch_guard(self):
        self.jobs.wait=True;ident=self.b.start(self.payload())['id']
        p=self.payload();p['request_id']='request-456'
        with self.assertRaises(InputError):self.b.start(p)
        self.b.cancel(ident);self.finish(ident)

if __name__=='__main__':unittest.main()

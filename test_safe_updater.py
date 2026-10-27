import hashlib, json, os, stat, tempfile, unittest, zipfile
from pathlib import Path
from unittest.mock import patch
from web_dialogue.safe_updater import *
from web_dialogue.update_helper import RecoveryRequired

class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
    def tearDown(self):self.tmp.cleanup()
    def payload(self):
        assets=[]
        for name in (ASSETS['win32'],ASSETS['win32']+'.sha256'):
            assets.append({'name':name,'browser_download_url':f'https://github.com/{REPO}/releases/download/v0.3.1/{name}','size':123,'digest':'sha256:'+'a'*64})
        return {'draft':False,'prerelease':False,'tag_name':'v0.3.1','assets':assets}
    def test_release(self):
        p=self.payload();self.assertEqual(release_offer(p,'0.3.0','win32')['state'],'available')
        self.assertEqual(release_offer(p,'0.4.0','win32')['state'],'latest')
        p['assets'][0]['digest']=None
        with self.assertRaises((UpdateError,TypeError)):release_offer(p,'0.3.0','win32')
    def test_release_wrong_origin(self):
        p=self.payload();p['assets'][0]['browser_download_url']='https://evil.invalid/a.zip'
        with self.assertRaises(UpdateError):release_offer(p,'0.3.0','win32')
    def test_prerelease(self):
        p=self.payload();p['prerelease']=True
        with self.assertRaises(UpdateError):release_offer(p,'0.3.0','win32')
    def archive(self,entries):
        p=self.root/'a.zip'
        with zipfile.ZipFile(p,'w') as z:
            for name,data in entries:z.writestr(name,data)
        return p
    def test_extract_windows(self):
        z=self.archive([('DialogueTTS-Windows-x64/DialogueTTS.exe',b'fixture')])
        self.assertEqual(extract_safe(z,self.root/'out','win32').read_bytes(),b'fixture')
    def test_traversal(self):
        z=self.archive([('DialogueTTS-Windows-x64/../evil',b'x')])
        with self.assertRaises(UpdateError):extract_safe(z,self.root/'out','win32')
        self.assertFalse((self.root/'out').exists())
    def test_case_duplicate(self):
        z=self.archive([('DialogueTTS-Windows-x64/X',b'a'),('DialogueTTS-Windows-x64/x',b'b')])
        with self.assertRaises(UpdateError):extract_safe(z,self.root/'out','win32')
    @unittest.skipIf(os.name == "nt", "Mac symlink extraction is validated on Unix; Windows does not install Mac bundles")
    def test_safe_mac_link(self):
        prefix='DialogueTTS-macOS-arm64/对白.app/Contents/Frameworks/Python.framework/'
        link=zipfile.ZipInfo(prefix+'Versions/Current');link.external_attr=(stat.S_IFLNK|0o777)<<16
        z=self.archive([(prefix+'Versions/3.12/Python',b'ok'),(link,b'3.12')])
        app=extract_safe(z,self.root/'out','darwin')
        self.assertEqual((app/'Contents/Frameworks/Python.framework/Versions/Current/Python').read_bytes(),b'ok')
    def test_escape_link(self):
        link=zipfile.ZipInfo('DialogueTTS-macOS-arm64/对白.app/x');link.external_attr=(stat.S_IFLNK|0o777)<<16
        z=self.archive([(link,b'../../../../etc')])
        with self.assertRaises(UpdateError):extract_safe(z,self.root/'out','darwin')
    def test_windows_link(self):
        link=zipfile.ZipInfo('DialogueTTS-Windows-x64/x');link.external_attr=(stat.S_IFLNK|0o777)<<16
        z=self.archive([(link,b'a')])
        with self.assertRaises(UpdateError):extract_safe(z,self.root/'out','win32')
    def test_transaction_preserves_prefs(self):
        app=self.root/'DialogueTTS.exe';app.write_bytes(b'old');new=self.root/'new';new.write_bytes(b'new')
        prefs=self.root/'preferences.json';prefs.write_text('{"input":"X","output":"Y"}')
        backup=replace_transaction(app,new,lambda p:self.assertEqual(p.read_bytes(),b'new'))
        self.assertEqual(backup.read_bytes(),b'old');self.assertEqual(json.loads(prefs.read_text())['output'],'Y')
    def test_launch_failure_rollback(self):
        app=self.root/'DialogueTTS.exe';app.write_bytes(b'old');new=self.root/'new';new.write_bytes(b'new')
        def fail(_):raise OSError('launch rejected')
        with self.assertRaises(OSError):replace_transaction(app,new,fail)
        self.assertEqual(app.read_bytes(),b'old')
    def test_pending_os_prompt_does_not_rollback(self):
        app=self.root/'DialogueTTS.exe';app.write_bytes(b'old');new=self.root/'new';new.write_bytes(b'new')
        def pending(_):raise RecoveryRequired('OS confirmation')
        with self.assertRaises(RecoveryRequired):replace_transaction(app,new,pending)
        self.assertEqual(app.read_bytes(),b'new');self.assertEqual(len(list(self.root.glob('*.update-backup-*'))),1)
    def test_repeat_updates_keep_backups(self):
        app=self.root/'DialogueTTS.exe';app.write_bytes(b'v1');new=self.root/'new';new.write_bytes(b'v2')
        replace_transaction(app,new,lambda _:None);new.write_bytes(b'v3');replace_transaction(app,new,lambda _:None)
        self.assertEqual(app.read_bytes(),b'v3')

class MoreSafetyTests(unittest.TestCase):
    def test_receipt_arbitrary_path_refused(self):
        from web_dialogue.update_helper import acknowledge_receipt
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'receipt.json';p.write_text(json.dumps({'nonce':'a'*64,'state':'waiting'}))
            self.assertFalse(acknowledge_receipt(p,'a'*64));self.assertEqual(json.loads(p.read_text())['state'],'waiting')
    def test_installation_lock_second_instance(self):
        from web_dialogue.install_lock import InstallationLease
        with tempfile.TemporaryDirectory() as d,patch('web_dialogue.updater_cli.state_directory',return_value=Path(d)):
            first=InstallationLease(Path(d)/'app').acquire()
            try:
                with self.assertRaises(UpdateError):InstallationLease(Path(d)/'app').acquire()
            finally:first.close()
            second=InstallationLease(Path(d)/'app').acquire();second.close()
    def test_reserved_device(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'a.zip'
            with zipfile.ZipFile(p,'w') as z:z.writestr('DialogueTTS-Windows-x64/NUL.txt',b'x')
            with self.assertRaises(UpdateError):extract_safe(p,Path(d)/'out','win32')
    def test_bomb(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'a.zip'
            with zipfile.ZipFile(p,'w',zipfile.ZIP_DEFLATED) as z:z.writestr('DialogueTTS-Windows-x64/a',b'0'*(2*1024*1024))
            with self.assertRaises(UpdateError):extract_safe(p,Path(d)/'out','win32')
    def test_file_below_link(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'a.zip';link=zipfile.ZipInfo('DialogueTTS-macOS-arm64/对白.app/x');link.external_attr=(stat.S_IFLNK|0o777)<<16
            with zipfile.ZipFile(p,'w') as z:
                z.writestr(link,b'y');z.writestr('DialogueTTS-macOS-arm64/对白.app/x/a',b'x')
            with self.assertRaises(UpdateError):extract_safe(p,Path(d)/'out','darwin')

class HelperFlowTests(unittest.TestCase):
    def test_disarmed_helper_never_replaces(self):
        from web_dialogue.update_helper import main
        import sys
        with tempfile.TemporaryDirectory() as d,patch('web_dialogue.updater_cli.state_directory',return_value=Path(d)):
            stage=Path(d)/'updates'/'abc';stage.mkdir(parents=True)
            target=Path(d)/'DialogueTTS.exe';target.write_bytes(b'old')
            payload=stage/'extracted'/'DialogueTTS.exe';payload.parent.mkdir();payload.write_bytes(b'new')
            plan={'target':str(target),'payload':str(payload),'platform':sys.platform,'old_version':'0.3.0','version':'0.3.1','old_entry_sha256':sha256(target),'old_pids':[os.getpid()],'helper_nonce':'b'*64}
            (stage/'plan.json').write_text(json.dumps(plan));(stage/'cancelled').write_text('b'*64)
            with patch('web_dialogue.update_helper.validate_payload'):
                result=main(stage/'plan.json')
            self.assertEqual(result['state'],'cancelled');self.assertEqual(target.read_bytes(),b'old')
            self.assertFalse((stage/'helper-ready.json').exists())
    def test_bad_plan_never_writes_status_outside_state(self):
        from web_dialogue.update_helper import main
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'plan.json';p.write_text('{}')
            with self.assertRaises(UpdateError):main(p)
            self.assertFalse((Path(d)/'status.json').exists())

class NativeGateTests(unittest.TestCase):
    def bridge(self,safety):
        import threading,types
        from native_windows_updater import NativeUpdaterMixin
        bridge=NativeUpdaterMixin();bridge._lock=threading.Lock();bridge._folder_lock=threading.RLock();bridge._update_gate=threading.Lock()
        bridge._update_server=types.SimpleNamespace(update_lock=threading.RLock(),update_in_progress=False,jobs=types.SimpleNamespace(lock=threading.RLock(),items={}),batches=types.SimpleNamespace(lock=threading.RLock(),active=None))
        bridge._updater=types.SimpleNamespace(state={'state':'available'},offer={'version':'0.3.1'})
        bridge._window=types.SimpleNamespace(evaluate_js=lambda code:safety if code=='window.dialogueUpdateSafety()' else None,create_confirmation_dialog=lambda *_:False)
        return bridge
    def test_unsaved_refuses_before_confirmation(self):
        bridge=self.bridge({'busy':False,'unsaved':True});self.assertEqual(bridge.install_update()['state'],'error');self.assertFalse(bridge._update_server.update_in_progress)
    def test_cancel_releases_all_gates(self):
        bridge=self.bridge({'busy':False,'unsaved':False});self.assertEqual(bridge.install_update()['state'],'cancelled');self.assertFalse(bridge._update_server.update_in_progress);self.assertTrue(bridge._lock.acquire(False));bridge._lock.release()
    def test_busy_batch_refuses(self):
        bridge=self.bridge({'busy':False,'unsaved':False});bridge._update_server.batches.active='id';self.assertEqual(bridge.install_update()['state'],'error')

if __name__=='__main__':unittest.main()

class CancellationAndAliasTests(unittest.TestCase):
    def test_native_cancel_capability_and_replace_boundary(self):
        from web_dialogue.update_cancel import cancel_transaction,begin_replacement
        with tempfile.TemporaryDirectory() as d,patch('web_dialogue.updater_cli.state_directory',return_value=Path(d)):
            token='b'*64;ident='a'*32;stage=Path(d)/'updates'/ident;stage.mkdir(parents=True)
            (stage/'plan.json').write_text(json.dumps({'helper_nonce':token}))
            with self.assertRaises(UpdateError):cancel_transaction(ident,'c'*64)
            with self.assertRaises(UpdateError):cancel_transaction('../'+ident,token)
            self.assertTrue(cancel_transaction(ident,token)['cancelled'])
            self.assertFalse(begin_replacement(stage))
            (stage/'cancelled').unlink();self.assertTrue(begin_replacement(stage))
            self.assertEqual(cancel_transaction(ident,token)['state'],'too_late')
            self.assertFalse((stage/'cancelled').exists())
    @unittest.skipIf(os.name=='nt','Mac directory aliases/symlinks tested on Unix')
    def test_extract_mac_internal_link_through_directory_alias(self):
        with tempfile.TemporaryDirectory() as d:
            base=Path(d);real=base/'private';real.mkdir();alias=base/'var';alias.symlink_to(real,target_is_directory=True)
            zpath=base/'app.zip';prefix='DialogueTTS-macOS-arm64/对白.app/'
            link=zipfile.ZipInfo(prefix+'Current');link.external_attr=(stat.S_IFLNK|0o777)<<16
            with zipfile.ZipFile(zpath,'w') as z:z.writestr(prefix+'Versions/1/Python',b'ok');z.writestr(link,'Versions/1')
            app=extract_safe(zpath,alias/'extracted','darwin')
            self.assertTrue(app.is_relative_to(real.resolve()));self.assertEqual((app/'Current/Python').read_bytes(),b'ok')

class BoundedShellTests(unittest.TestCase):
    def test_blocked_shell_is_bounded_and_never_rolls_back(self):
        import threading,time
        from web_dialogue.update_helper import launch_normal,RecoveryRequired
        release=threading.Event()
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);app=root/'DialogueTTS.exe';app.write_bytes(b'old');new=root/'new';new.write_bytes(b'new');receipt=root/'receipt.json';receipt.write_text(json.dumps({'nonce':'n','state':'waiting'}))
            with patch('web_dialogue.update_helper.sys.platform','win32'),patch('web_dialogue.update_helper._windows_shell_open',side_effect=lambda *_:release.wait(2)):
                started=time.monotonic()
                try:
                    with self.assertRaises(RecoveryRequired):replace_transaction(app,new,lambda target:launch_normal(target,receipt,'n',timeout=.03))
                    self.assertLess(time.monotonic()-started,1)
                    self.assertEqual(app.read_bytes(),b'new');self.assertEqual(len(list(root.glob('*.update-backup-*'))),1)
                    self.assertEqual(json.loads((root/'launch.json').read_text())['state'],'needs_attention')
                finally:release.set();time.sleep(.02)
    def test_shell_explicit_rejection_is_error(self):
        from web_dialogue.update_helper import launch_normal
        with tempfile.TemporaryDirectory() as d:
            receipt=Path(d)/'receipt.json';receipt.write_text('{}')
            with patch('web_dialogue.update_helper.sys.platform','win32'),patch('web_dialogue.update_helper._windows_shell_open',side_effect=OSError('denied')):
                with self.assertRaises(OSError):launch_normal(Path(d)/'app.exe',receipt,'n',timeout=.2)
    def test_real_ready_receipt_wins_pending_shell(self):
        import threading,time
        from web_dialogue.update_helper import launch_normal
        release=threading.Event()
        with tempfile.TemporaryDirectory() as d:
            receipt=Path(d)/'receipt.json';receipt.write_text(json.dumps({'nonce':'n','state':'ready'}))
            with patch('web_dialogue.update_helper.sys.platform','win32'),patch('web_dialogue.update_helper._windows_shell_open',side_effect=lambda *_:release.wait(2)):
                try:launch_normal(Path(d)/'app.exe',receipt,'n',timeout=.03)
                finally:release.set();time.sleep(.02)

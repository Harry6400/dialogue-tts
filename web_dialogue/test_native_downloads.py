"""Directory-only manual saves with fixed trusted response names; no user profile."""
import io
from contextlib import ExitStack
import tempfile
import unittest
from email.message import Message
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from windows_launcher import NativeDownloads
from native_audio import NativeAudioCaptures
from native_workspace import NativeWorkspaceAccess

class NativeSaveTests(unittest.TestCase):
    def test_restarted_capture_can_return_to_native_save_without_original_route(self):
        with tempfile.TemporaryDirectory() as temp, ExitStack() as cleanup:
            root=Path(temp);output=root/'output';output.mkdir();state=root/'state'
            access=NativeWorkspaceAccess(state);cleanup.callback(access.close)
            captures=NativeAudioCaptures(state,access)
            audio=(Path(__file__).parent/'silence.mp3').read_bytes()
            capture=captures.reserve({'resource_path':'/api/jobs/'+'a'*32+'/audio','filename':'000001.mp3','fingerprint':'b'*64,'size':len(audio)})
            Path(capture['path']).write_bytes(audio);captures.complete(capture['capture_id']);captures.close()
            captures=NativeAudioCaptures(state,access);cleanup.callback(captures.close)
            bridge=NativeDownloads('http://127.0.0.1:1',state/'prefs.json',workspace_access=access)
            callbacks=[];bridge._attach_window(SimpleNamespace(create_file_dialog=lambda *a,**k:(str(output),),evaluate_js=callbacks.append))
            calls=[]
            def request(route,payload):
                calls.append((route,payload))
                if route.endswith('/pending'):return captures.pending()
                if route=='/api/native/folders':return access.register(payload['path'],payload['kind'])
                if route.endswith('/save'):return captures.save(payload['capture_id'],payload['folder_id'])
                raise AssertionError('Original job route must not be fetched')
            with patch.dict('sys.modules',{'webview':SimpleNamespace(FileDialog=SimpleNamespace(FOLDER=20))}),patch.object(bridge,'_native_workspace_request',side_effect=request):
                result=bridge._return_to_unsaved_audio()
            self.assertEqual(result['state'],'saved');self.assertEqual((output/'000001.mp3').read_bytes(),audio)
            self.assertEqual([route for route,_ in calls],['/api/native/audio-captures/pending','/api/native/folders','/api/native/audio-captures/save'])
            self.assertEqual(len(callbacks),1);self.assertFalse(captures.safety()['unsaved'])

    def test_return_save_cancel_or_error_keeps_capture_and_does_not_notify_success(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);bridge=NativeDownloads('http://127.0.0.1:1',root/'prefs.json');callbacks=[]
            bridge._attach_window(SimpleNamespace(create_file_dialog=lambda *a,**k:None,evaluate_js=callbacks.append))
            capture={'capture_id':'a'*32,'state':'ready'}
            with patch.dict('sys.modules',{'webview':SimpleNamespace(FileDialog=SimpleNamespace(FOLDER=20))}),patch.object(bridge,'_native_workspace_request',return_value={'ok':True,'captures':[capture]}) as request:
                self.assertTrue(bridge._return_to_unsaved_audio()['cancelled'])
                request.assert_called_once_with('/api/native/audio-captures/pending',{})
                bridge._window.create_file_dialog=lambda *a,**k:(str(root),)
                request.side_effect=[{'ok':True,'captures':[capture]},{'folder_id':'b'*32},OSError('disk full')]
                self.assertFalse(bridge._return_to_unsaved_audio()['ok'])
            self.assertFalse(callbacks);self.assertTrue(bridge._lock.acquire(False));bridge._lock.release();bridge._close_folders()

    def test_no_native_capture_uses_legacy_page_return_save(self):
        with tempfile.TemporaryDirectory() as temp:
            bridge=NativeDownloads('http://127.0.0.1:1',Path(temp)/'prefs.json');callbacks=[]
            bridge._attach_window(SimpleNamespace(evaluate_js=callbacks.append))
            with patch.dict('sys.modules',{'webview':SimpleNamespace()}),patch.object(bridge,'_native_workspace_request',return_value={'ok':True,'captures':[]}):
                self.assertTrue(bridge._return_to_unsaved_audio()['ok'])
            self.assertIn('dialogueShowUnsaved',callbacks[0]);bridge._close_folders()

    def test_save_cancel_reveal_and_batch_default_unchanged(self):
        with tempfile.TemporaryDirectory() as folder, ExitStack() as cleanup:
            root=Path(folder);output=root/'manual';output.mkdir();default=root/'batch';default.mkdir()
            bridge=NativeDownloads('http://127.0.0.1:1234',root/'prefs.json');callbacks=[];dialogs=[]
            cleanup.callback(bridge._close_folders)
            chosen=bridge._authorize(default,'output');bridge._selected['output']=chosen
            def choose(kind,**kwargs): dialogs.append((kind,kwargs));return(str(output),)
            bridge._window=SimpleNamespace(create_file_dialog=choose,evaluate_js=callbacks.append)
            data=(Path(__file__).parent/'silence.mp3').read_bytes();response=io.BytesIO(data);response.headers=Message()
            for k,v in {'Content-Type':'audio/mpeg','Content-Length':str(len(data)),'X-Dialogue-Fingerprint':'b'*64,'Content-Disposition':'attachment; filename="001.mp3"'}.items():response.headers[k]=v
            with patch.dict('sys.modules',{'webview':SimpleNamespace(FileDialog=SimpleNamespace(FOLDER=20,SAVE=30))}),patch('urllib.request.build_opener') as op:
                op.return_value.open.return_value=response
                saved=bridge.save_download('/api/jobs/'+'a'*32+'/audio')
                self.assertTrue(saved['ok'],saved);self.assertEqual((output/'001.mp3').read_bytes(),data)
                self.assertEqual(dialogs,[(20,{})]);self.assertEqual(bridge._selected['output'],chosen)
                self.assertEqual(len(callbacks),1)
                with patch('os.startfile',create=True) as start:
                    self.assertFalse(bridge.reveal_saved(str(output/'001.mp3'))['ok'])
                    self.assertTrue(bridge.reveal_saved(saved['save_id'])['ok']);start.assert_called_once_with(str(output),'explore')
                bridge._window.create_file_dialog=lambda *a,**k:None
                self.assertTrue(bridge.save_download('/api/jobs/'+'a'*32+'/audio')['cancelled'])
                self.assertEqual(len(callbacks),1)
    def test_reject_arbitrary_routes(self):
        with tempfile.TemporaryDirectory() as folder, ExitStack() as cleanup:
            bridge=NativeDownloads('http://127.0.0.1:1234',Path(folder)/'prefs.json')
            cleanup.callback(bridge._close_folders)
            for route in ('https://evil.example/a','/api/jobs/../secret','/api/config','/api/jobs/'+'a'*32+'/audio?x=1',None):self.assertFalse(bridge.save_download(route)['ok'])
    def test_conflict_and_incomplete_never_replace_existing(self):
        with tempfile.TemporaryDirectory() as folder, ExitStack() as cleanup:
            root=Path(folder);target=root/'001.mp3';target.write_bytes(b'existing')
            bridge=NativeDownloads('http://127.0.0.1:1234',root/'state'/'prefs.json');callbacks=[]
            cleanup.callback(bridge._close_folders)
            bridge._window=SimpleNamespace(create_file_dialog=lambda *a,**k:(folder,),evaluate_js=callbacks.append)
            response=io.BytesIO(b'short');response.headers=Message()
            for k,v in {'Content-Type':'audio/mpeg','Content-Length':'200','X-Dialogue-Fingerprint':'b'*64,'Content-Disposition':'attachment; filename="001.mp3"'}.items():response.headers[k]=v
            with patch.dict('sys.modules',{'webview':SimpleNamespace(FileDialog=SimpleNamespace(FOLDER=20))}),patch('urllib.request.build_opener') as op:
                op.return_value.open.return_value=response
                self.assertFalse(bridge.save_download('/api/jobs/'+'a'*32+'/audio')['ok'])
            self.assertEqual(target.read_bytes(),b'existing');self.assertFalse(callbacks)
            self.assertEqual(list(root.iterdir()),[target]);self.assertFalse(bridge._saved)

"""Directory-only manual saves with fixed trusted response names; no user profile."""
import io
import tempfile
import unittest
from email.message import Message
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from windows_launcher import NativeDownloads

class NativeSaveTests(unittest.TestCase):
    def test_save_cancel_reveal_and_batch_default_unchanged(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);output=root/'manual';output.mkdir();default=root/'batch';default.mkdir()
            bridge=NativeDownloads('http://127.0.0.1:1234',root/'prefs.json');callbacks=[];dialogs=[]
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
        with tempfile.TemporaryDirectory() as folder:
            bridge=NativeDownloads('http://127.0.0.1:1234',Path(folder)/'prefs.json')
            for route in ('https://evil.example/a','/api/jobs/../secret','/api/config','/api/jobs/'+'a'*32+'/audio?x=1',None):self.assertFalse(bridge.save_download(route)['ok'])
    def test_conflict_and_incomplete_never_replace_existing(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);target=root/'001.mp3';target.write_bytes(b'existing')
            bridge=NativeDownloads('http://127.0.0.1:1234',root/'state'/'prefs.json');callbacks=[]
            bridge._window=SimpleNamespace(create_file_dialog=lambda *a,**k:(folder,),evaluate_js=callbacks.append)
            response=io.BytesIO(b'short');response.headers=Message()
            for k,v in {'Content-Type':'audio/mpeg','Content-Length':'200','X-Dialogue-Fingerprint':'b'*64,'Content-Disposition':'attachment; filename="001.mp3"'}.items():response.headers[k]=v
            with patch.dict('sys.modules',{'webview':SimpleNamespace(FileDialog=SimpleNamespace(FOLDER=20))}),patch('urllib.request.build_opener') as op:
                op.return_value.open.return_value=response
                self.assertFalse(bridge.save_download('/api/jobs/'+'a'*32+'/audio')['ok'])
            self.assertEqual(target.read_bytes(),b'existing');self.assertFalse(callbacks)
            self.assertEqual(list(root.iterdir()),[target]);self.assertFalse(bridge._saved)

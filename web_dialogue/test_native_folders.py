import io
import json
from email.message import Message
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from native_windows_folders import NativeFolderMixin

class Host(NativeFolderMixin):
    def __init__(self, root):
        self._base='http://127.0.0.1:1234';self._saved={};self._window=SimpleNamespace(evaluate_js=lambda x:None)
        self._setup_folders(root/'prefs.json')

class FolderTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.input=self.root/'input';self.input.mkdir();self.output=self.root/'output';self.output.mkdir()
        self._hosts=[]
        def new_host():
            host=Host(self.root);self._hosts.append(host);return host
        self.new_host=new_host
        self.host=self.new_host()
        self.inc=self.host._authorize(self.input,'input')['folder_id'];self.out=self.host._authorize(self.output,'output')['folder_id']
    def tearDown(self):
        for host in reversed(self._hosts):host._close_folders()
        self.temp.cleanup()
    def response(self, fp='a'*64):
        data=(Path(__file__).parent/'silence.mp3').read_bytes()
        r=io.BytesIO(data);r.headers=Message();r.headers['Content-Length']=str(len(data));r.headers['Content-Type']='audio/mpeg';r.headers['X-Dialogue-Fingerprint']=fp;r.headers['Content-Disposition']='attachment; filename="001.mp3"'
        return r
    def save(self,route=None,fp='a'*64):
        with patch('native_windows_folders.urllib.request.build_opener') as op:
            op.return_value.open.return_value=self.response(fp)
            return self.host.save_to_output(route or '/api/jobs/'+'b'*32+'/audio',self.out)
    def test_empty_mixed_and_explicit_refresh(self):
        self.assertEqual(self.host.refresh_input_directory(self.inc)['files'],[])
        (self.input/'notes.txt').write_text('ignored');(self.input/'one.xml').write_text('<root/>')
        first=self.host.refresh_input_directory(self.inc);self.assertEqual(len(first['files']),1)
        (self.input/'two.xml').write_text('<root/>')
        self.assertEqual(len(first['files']),1);self.assertEqual(len(self.host.refresh_input_directory(self.inc)['files']),2)
        self.assertFalse(self.host.refresh_input_directory(str(self.input))['ok'])
    def test_bounds_and_symlink(self):
        for i in range(101):(self.input/f'{i}.xml').write_text('<x/>')
        self.assertFalse(self.host.refresh_input_directory(self.inc)['ok'])
        for file in self.input.iterdir():file.unlink()
        (self.input/'big.xml').write_bytes(b'x'*(2*1024*1024+1))
        self.assertFalse(self.host.refresh_input_directory(self.inc)['ok']);(self.input/'big.xml').unlink()
        try:(self.input/'link.xml').symlink_to(self.root/'elsewhere')
        except OSError:self.skipTest('Creating symbolic links not permitted')
        self.assertFalse(self.host.refresh_input_directory(self.inc)['ok'])
    def test_total_bound(self):
        for i in range(11):(self.input/f'{i}.xml').write_bytes(b'x'*(2*1024*1024))
        self.assertFalse(self.host.refresh_input_directory(self.inc)['ok'])
    def test_preferences_restore_without_scan_and_cancel(self):
        self.host._window.create_file_dialog=lambda *a,**k:(str(self.input),)
        with patch.dict('sys.modules',{'webview':SimpleNamespace(FileDialog=SimpleNamespace(FOLDER=20))}):
            selected=self.host.choose_input_directory();self.assertTrue(selected['ok'])
            self.host._window.create_file_dialog=lambda *a,**k:None
            self.assertTrue(self.host.choose_input_directory()['cancelled'])
        self.assertTrue(self.host.set_preferences({'voices':{'AGENT':'zh-CN-YunxiNeural'},'rate':1.5,'pause_ms':500})['ok'])
        with patch.object(Host,'_scan',side_effect=AssertionError('must not scan')):
            restored=self.new_host().get_preferences()
        self.assertEqual(restored['rate'],1.5);self.assertTrue(restored['input_folder']['valid'])
        self.assertNotEqual(restored['input_folder']['folder_id'], selected['folder_id'])
        self.assertFalse(self.host.set_preferences({'paths':{'output':'C:/escape'}})['ok'])
    def test_legacy_nondefault_user_voice_becomes_manual(self):
        (self.root/'prefs.json').write_text(json.dumps({'version':1,'voices':{'USER':'zh-CN-YunxiNeural'},'rate':1.2,'pause_ms':400,'paths':{},'receipts':{},'exports':{}}))
        restored=self.new_host().get_preferences()
        self.assertEqual(restored['voices']['USER'],'zh-CN-YunxiNeural')
        self.assertTrue(restored['manual_user_voice']);self.assertFalse(restored['auto_voice'])
    def test_legacy_foreign_preferences_survive_read_but_new_foreign_writes_fail(self):
        voices={'AGENT':'en-US-JennyNeural','USER':'zh-CN-XiaoyiNeural'}
        (self.root/'prefs.json').write_text(json.dumps({'version':1,'voices':voices,'paths':{},'receipts':{},'exports':{}}))
        self.assertEqual(self.new_host().get_preferences()['voices'],voices)
        self.assertFalse(self.host.set_preferences({'voices':voices})['ok'])
        self.assertFalse(self.host.set_preferences({'voices':{'USER':'https://evil/voice'}})['ok'])
        self.assertEqual(self.new_host().get_preferences()['voices'],voices)
    def test_new_and_legacy_voices_persist(self):
        self.assertTrue(self.host.set_preferences({'voices': {'AGENT': 'zh-CN-YunxiaNeural', 'USER': 'zh-CN-YunyangNeural'}})['ok'])
        self.assertEqual(self.new_host().get_preferences()['voices'], {'AGENT': 'zh-CN-YunxiaNeural', 'USER': 'zh-CN-YunyangNeural'})
        self.assertTrue(self.host.set_preferences({'voices': {'AGENT': 'zh-CN-XiaoxiaoNeural', 'USER': 'zh-CN-XiaoyiNeural'}})['ok'])
        self.assertEqual(self.new_host().get_preferences()['voices']['USER'], 'zh-CN-XiaoyiNeural')
    def test_output_space_uses_capability_and_cleans_probe(self):
        result = self.host.check_output_space(self.out)
        self.assertTrue(result['ok'])
        self.assertTrue(result['writable'])
        self.assertGreater(result['free_bytes'], 0)
        self.assertEqual(list(self.output.iterdir()), [])
        self.assertFalse(self.host.check_output_space(str(self.output))['ok'])
        self.assertFalse(self.host.check_output_space(self.inc)['ok'])
    def test_theme_persists_and_rejects_invalid_values(self):
        self.assertEqual(self.host.get_preferences()['theme'], 'light')
        self.assertTrue(self.host.set_preferences({'theme': 'dark'})['ok'])
        self.assertEqual(self.new_host().get_preferences()['theme'], 'dark')
        self.assertFalse(self.host.set_preferences({'theme': 'invalid'})['ok'])
        self.assertEqual(self.new_host().get_preferences()['theme'], 'dark')
    def test_save_idempotent_collision_receipt_restart_delete_and_change(self):
        target=self.output/'001.mp3';target.write_bytes(b'unrelated')
        first=self.save();self.assertFalse(first['ok']);self.assertTrue(first['conflict'])
        self.assertEqual(target.read_bytes(),b'unrelated');target.unlink()
        first=self.save();self.assertTrue(first['ok'],first);self.assertEqual(first['name'],'001.mp3')
        again=self.save();self.assertEqual(again['path'],first['path'])
        second=self.save('/api/jobs/'+'c'*32+'/audio');self.assertTrue(second['ok']);self.assertEqual(first['path'],second['path'])
        restarted=self.new_host();cap=restarted._authorize(self.output,'output')['folder_id']
        names={'a'*64:'001.mp3'}
        self.assertEqual(len(restarted.check_completed(cap,['a'*64],names)['completed']),1)
        target.write_bytes(b'changed')
        checked=restarted.check_completed(cap,['a'*64],names)
        self.assertEqual(checked['completed'],[]);self.assertEqual(len(checked['conflicts']),1)
        target.unlink();self.assertEqual(restarted.check_completed(cap,['a'*64],names)['completed'],[])

    def test_new_fingerprint_and_bad_header(self):
        one=self.save();self.assertTrue(one['ok'])
        self.assertEqual(self.host.check_completed(self.out,['f'*64])['completed'],[])
        bad=self.save('/api/jobs/'+'d'*32+'/audio','invalid');self.assertFalse(bad['ok'])
        self.assertFalse(self.host.save_to_output('https://evil.example/',self.out)['ok'])
        self.assertFalse(self.host.save_to_output('/api/jobs/'+'b'*32+'/audio',str(self.output))['ok'])
    def test_reveal_only_authorized(self):
        with patch('os.startfile',create=True) as reveal:
            self.assertFalse(self.host.reveal_output(str(self.output))['ok'])
            self.assertTrue(self.host.reveal_output(self.out)['ok']);reveal.assert_called_once_with(str(self.output),'explore')
    def test_one_hundred_files_and_invalid_utf8(self):
        for i in range(100):(self.input/f'{i}.xml').write_text('<root/>',encoding='utf-8')
        self.assertEqual(len(self.host.refresh_input_directory(self.inc)['files']),100)
        (self.input/'0.xml').write_bytes(b'\xff\xfe')
        self.assertFalse(self.host.refresh_input_directory(self.inc)['ok'])
    def test_stale_capability_and_invalid_settings(self):
        self.input.rename(self.root/'old-input');self.input.mkdir()
        self.assertFalse(self.host.refresh_input_directory(self.inc)['ok'])
        for values in ({'rate':False},{'rate':3},{'pause_ms':-1},{'pause_ms':1.5},{'voices':{'AGENT':'not-supported'}}):
            self.assertFalse(self.host.set_preferences(values)['ok'])

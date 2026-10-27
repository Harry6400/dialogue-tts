import hashlib, unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from web_dialogue import update_sources as s, gitee_provider as g, safe_updater as safe

def sample():
 name=g.ASSETS['darwin']; data=b'candidate'; raw=(hashlib.sha256(data).hexdigest()+'  '+name+'\n').encode()
 a={'name':name,'size':len(data),'sha256':hashlib.sha256(data).hexdigest(),'url':'https://gitee.com/yongchu-64/dialogue-tts/releases/download/v0.4.0/'+name}
 c={'name':name+'.sha256','size':len(raw),'sha256':hashlib.sha256(raw).hexdigest(),'url':a['url']+'.sha256'}
 return {'state':'available','version':'0.4.0','platform':'darwin','source':'gitee','archive':{**a,'id':1},'checksum':{**c,'id':2}},data,raw

class Integration(unittest.TestCase):
 def test_network_fallback_and_invalid_fail_closed(self):
  choice,_,_=sample()
  with patch.object(g,'make_opener',return_value=object()), patch.object(g,'offer',side_effect=g.NetworkError()), patch.object(s,'fetch_release',return_value={}), patch.object(s,'release_offer',return_value={**choice,'source':'github'}):
   self.assertEqual(s.discover('0.3.2','darwin')['source'],'github')
  for error in (g.ValidationError('bad'),):
   with patch.object(g,'make_opener',return_value=object()),patch.object(g,'offer',side_effect=error),patch.object(s,'fetch_release') as gh:
    with self.assertRaises(g.ValidationError):s.discover('0.3.2','darwin')
    gh.assert_not_called()
 def test_gitee_latest_never_downgrades(self):
  with patch.object(g,'make_opener',return_value=object()),patch.object(g,'offer',return_value={'state':'latest','current_version':'0.3.2'}),patch.object(s,'fetch_release') as gh:
   self.assertEqual(s.discover('0.3.2','darwin')['state'],'latest');gh.assert_not_called()
 def test_exact_version_and_hash_binding(self):
  choice,_,_=sample(); alternate={**choice,'source':'github','archive':dict(choice['archive']),'checksum':dict(choice['checksum'])}
  with patch.object(s,'fetch_release',return_value={}),patch.object(s,'release_offer',return_value=alternate):
   self.assertEqual(s.matching_github(choice)['version'],'0.4.0')
   alternate['version']='0.5.0'
   with self.assertRaises(s.UpdateError):s.matching_github(choice)
   alternate['version']='0.4.0';alternate['archive']['sha256']='f'*64
   with self.assertRaises(s.UpdateError):s.matching_github(choice)
 def test_gitee_offer_and_prepare_existing_validation(self):
  choice,data,raw=sample()
  with patch.object(g,'make_opener',return_value=object()),patch.object(g,'offer',return_value=choice),patch.object(g,'request',return_value=object()),patch.object(g,'bounded_read',return_value=raw):
   found=s.discover('0.3.2','darwin');self.assertEqual(found['archive']['sha256'],hashlib.sha256(data).hexdigest())
  with TemporaryDirectory() as d:
   stage=Path(d)/'stage'; payload=stage/'payload'
   def write(op,offer,path,progress):Path(path).write_bytes(data)
   with patch.object(g,'make_opener',return_value=object()),patch.object(g,'download_verified',side_effect=write),patch.object(safe,'extract_safe',return_value=payload) as extract,patch.object(safe,'validate_payload') as validate,patch.object(safe.os,'setxattr',create=True):
    self.assertEqual(safe.prepare(found,stage),payload);extract.assert_called_once();validate.assert_called_once_with(payload,'0.4.0','darwin')
 def test_download_fallback_only_network(self):
  choice,data,raw=sample()
  with TemporaryDirectory() as d:
   def write(asset,path,progress=None):Path(path).write_bytes(raw if asset['name'].endswith('.sha256') else data)
   with patch.object(g,'make_opener',return_value=object()),patch.object(g,'download_verified',side_effect=g.NetworkError()),patch.object(s,'matching_github',return_value=choice),patch.object(safe,'download',side_effect=write),patch.object(safe,'extract_safe',return_value=Path(d)/'payload'),patch.object(safe,'validate_payload'),patch.object(safe.os,'setxattr',create=True):
    safe.prepare(choice,Path(d)/'stage')
  with TemporaryDirectory() as d,patch.object(g,'make_opener',return_value=object()),patch.object(g,'download_verified',side_effect=g.ValidationError()),patch.object(s,'matching_github') as fallback:
   with self.assertRaises(g.ValidationError):safe.prepare(choice,Path(d)/'stage')
   fallback.assert_not_called()
 def test_mac_cli_rejects_changed_offer_identity_before_prepare(self):
  import io,json
  from web_dialogue import updater_cli
  choice,_,_=sample()
  fake=unittest.mock.MagicMock();fake.offer=choice;fake.check.return_value={'latest_version':'0.4.0'}
  with patch.object(updater_cli,'Updater',return_value=fake),patch('sys.stdout',new_callable=io.StringIO) as out:
   self.assertTrue(updater_cli.dispatch(['--update-install','/tmp/app','0.3.2','0.4.0','10','11','0'*64]))
   self.assertEqual(json.loads(out.getvalue())['state'],'error')
   fake.prepare_confirmed.assert_not_called()
 def test_identity_changes_for_byte_or_size(self):
  x,_,_=sample();v=s.offer_identity(x);x['archive']['size']+=1;self.assertNotEqual(v,s.offer_identity(x))
if __name__=='__main__':unittest.main()

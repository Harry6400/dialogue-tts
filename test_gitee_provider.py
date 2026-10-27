import io,json,tempfile,unittest,urllib.error
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch
from web_dialogue import gitee_provider as g

class Response(io.BytesIO):
 def __init__(self,data):
  super().__init__(data); self.timeouts=[]; self.fp=SimpleNamespace(raw=SimpleNamespace(_sock=SimpleNamespace(settimeout=self.timeouts.append)))
class Fake:
 def __init__(self, fn): self.fn=fn;self.urls=[]
 def open(self,req,timeout):
  self.urls.append(req.full_url); x=self.fn(req.full_url)
  if isinstance(x,Exception): raise x
  return Response(x)
def redir(url): return urllib.error.HTTPError(url,302,'redirect',{'Location':url},io.BytesIO())
def release(v,i=1,pre=False): return {'tag_name':v,'id':i,'prerelease':pre}
def assets(platform='darwin',tag='v0.4.0',data=b'audio'):
 import hashlib
 name=g.ASSETS[platform]; check=(hashlib.sha256(data).hexdigest()+'  '+name+'\n').encode()
 urls=[f'https://gitee.com/{g.OWNER}/{g.REPO}/releases/download/{tag}/{n}' for n in (name,name+'.sha256')]
 a=[{'name':n,'size':s,'browser_download_url':u,'id':idx} for idx,(n,s,u) in enumerate(zip((name,name+'.sha256'),(len(data),len(check)),urls),1)]
 return a,urls,check
class Tests(unittest.TestCase):
 def test_pagination_empty_page_continue(self):
  pages={1:[release('v0.4.0')],2:[],3:[release('v0.5.0',2)],4:[],5:[]}
  f=Fake(lambda u:json.dumps(pages[int(u.split('page=')[1].split('&')[0])]).encode())
  x=g.all_pages(f,'releases');self.assertEqual(g.select_release(x,'0.3.0')['tag_name'],'v0.5.0');self.assertEqual(len(f.urls),5)
 def test_real_http_response_partial_read_and_bounds(self):
  import http.client,socket
  def response(payload):
   server,client=socket.socketpair()
   server.sendall(b'HTTP/1.1 200 OK\r\nContent-Length: '+str(len(payload)).encode()+b'\r\n\r\n'+payload)
   server.close()
   resp=http.client.HTTPResponse(client);resp.begin();return resp,client
  r,c=response(b'abc')
  try: self.assertEqual(g.bounded_read(r,3),b'abc')
  finally: c.close()
  r,c=response(b'abcd')
  try:
   with self.assertRaises(g.ValidationError):g.bounded_read(r,3)
  finally: c.close()
 def test_metadata_deadline(self):
  r=Response(b'ab')
  with patch.object(g.time,'monotonic',side_effect=[0,59,59.5,61]):
   with self.assertRaises(g.NetworkError):g.bounded_read(r,10)
  self.assertEqual(r.timeouts,[0.5])
 def test_bounds_and_invalid_metadata(self):
  with self.assertRaises(g.ValidationError):g.api_json(Fake(lambda u:b'x'*(g.MAX_JSON+1)),g.ROOT+'/releases')
  with self.assertRaises(g.ValidationError):g.all_pages(Fake(lambda u:b'{}'),'releases')
  with patch.object(g,'MAX_PAGES',2):
   with self.assertRaises(g.ValidationError):g.all_pages(Fake(lambda u:b'[{}]'),'releases')
 def test_versions_and_duplicates(self):
  self.assertIsNone(g.select_release([release('v0.3.0'),release('v0.6.0-rc1',pre=True)],'0.3.1'))
  with self.assertRaises(g.ValidationError):g.select_release([release('v0.4.0'),release('v0.4.0',2)],'0.3.1')
  a,_,_=assets()
  with self.assertRaises(g.ValidationError):g.select_assets(a+[a[0]],'v0.4.0','darwin')
 def test_url_and_redirects(self):
  a,u,_=assets();self.assertFalse(g.url_ok('https://gitee.com.evil.invalid/x'))
  f=Fake(lambda url:redir('https://evil.invalid/a'))
  with self.assertRaises(g.ValidationError):g.request(f,u[0])
  f=Fake(lambda url:redir(url))
  with self.assertRaises(g.ValidationError):g.request(f,u[0])
  with self.assertRaises(g.ValidationError):g.request(Fake(lambda _:b''),'https://gitee.com.evil.invalid/x')
  cdn='https://foruda.gitee.com/attach_file/x?token=temporary'
  f=Fake(lambda url: redir(cdn) if url==u[0] else b'ok')
  self.assertEqual(g.bounded_read(g.request(f,u[0]),10),b'ok')
  selected=g.select_assets(a,'v0.4.0','darwin')[0]
  middle=f"https://gitee.com/{g.OWNER}/{g.REPO}/attach_files/{selected['id']}/download/{selected['name']}"
  f=Fake(lambda url: redir(middle) if url==u[0] else redir(cdn) if url==middle else b'ok')
  self.assertEqual(g.bounded_read(g.request(f,u[0],attachment=selected),10),b'ok')
  for bad in (middle.replace(g.OWNER,'wrong'),middle.replace('/1/','/9/'),middle+'x'):
   with self.assertRaises(g.ValidationError):g.request(Fake(lambda _:redir(bad)),u[0],attachment=selected)
  self.assertTrue(g.url_ok(cdn))
  with self.assertRaises(g.ValidationError):g.request(Fake(lambda _:b''),cdn)
  self.assertFalse(g.url_ok('https://foruda.gitee.com/elsewhere?token=x'))
 def test_verified_download_and_bad_bytes(self):
  import hashlib
  data=b'audio';a,u,c=assets(data=data); selected=dict(zip(('archive','checksum'),g.select_assets(a,'v0.4.0','darwin')))
  selected['archive']['sha256']=__import__('hashlib').sha256(data).hexdigest();selected['checksum']['sha256']=__import__('hashlib').sha256(c).hexdigest()
  with tempfile.TemporaryDirectory() as d:
   dest=Path(d)/'x'; f=Fake(lambda url:c if url==u[1] else data)
   self.assertEqual(g.download_verified(f,selected,dest),hashlib.sha256(data).hexdigest());self.assertEqual(dest.read_bytes(),data)
   dest.unlink()
   for bad in (b'audi',b'audio!',b'wrong'):
    with self.assertRaises(g.ValidationError):g.download_verified(Fake(lambda url:c if url==u[1] else bad),selected,dest)
    self.assertFalse(dest.exists())
 def test_total_deadline_and_timeout_bound(self):
  data=b'audio';a,u,c=assets(data=data);selected=dict(zip(('archive','checksum'),g.select_assets(a,'v0.4.0','darwin')))
  selected['archive']['sha256']=__import__('hashlib').sha256(data).hexdigest();selected['checksum']['sha256']=__import__('hashlib').sha256(c).hexdigest()
  responses=[]
  class Tracking(Fake):
   def open(self,req,timeout):
    r=super().open(req,timeout);responses.append(r);return r
  with tempfile.TemporaryDirectory() as d:
   dest=Path(d)/'x'; f=Tracking(lambda url:c if url==u[1] else data)
   with patch.object(g,'bounded_read',return_value=c), patch.object(g.time,'monotonic',side_effect=[0,599,599.5,601]):
    with self.assertRaises(g.NetworkError):g.download_verified(f,selected,dest)
   self.assertFalse(dest.exists())
   self.assertEqual(responses[-1].timeouts,[0.5])
 def test_checksum_and_fallback(self):
  data=b'audio';a,u,c=assets(data=data); selected=dict(zip(('archive','checksum'),g.select_assets(a,'v0.4.0','darwin')))
  selected['archive']['sha256']=__import__('hashlib').sha256(data).hexdigest();selected['checksum']['sha256']=__import__('hashlib').sha256(c).hexdigest()
  with tempfile.TemporaryDirectory() as d:
   with self.assertRaises(g.ValidationError):g.download_verified(Fake(lambda url:b'bad'),selected,Path(d)/'x')
  self.assertEqual(g.with_network_fallback(lambda:(_ for _ in ()).throw(g.NetworkError()),lambda:'github'),'github')
  with self.assertRaises(g.ValidationError):g.with_network_fallback(lambda:(_ for _ in ()).throw(g.ValidationError()),lambda:'github')
 def test_tls_failure_never_fallback(self):
  import ssl
  f=Fake(lambda _:urllib.error.URLError(ssl.SSLCertVerificationError('bad cert')))
  with self.assertRaises(g.ValidationError):g.with_network_fallback(lambda:g.request(f,g.ROOT+'/releases',api=True),lambda:'github')
 def test_metadata_redirect_and_http_errors(self):
  with self.assertRaises(g.ValidationError):g.request(Fake(lambda _:redir('https://foruda.gitee.com/attach_file/x')),g.ROOT+'/releases',api=True)
  def err(code):return urllib.error.HTTPError(g.ROOT+'/releases',code,'error',{},io.BytesIO())
  with self.assertRaises(g.NetworkError):g.request(Fake(lambda _:err(503)),g.ROOT+'/releases',api=True)
  with self.assertRaises(g.ValidationError):g.request(Fake(lambda _:err(404)),g.ROOT+'/releases',api=True)
if __name__=='__main__':unittest.main()

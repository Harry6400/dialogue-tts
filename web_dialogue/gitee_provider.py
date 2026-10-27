"""Bounded anonymous Gitee provider used by the production source selector.

The caller must pass the resulting archive through safe_updater's existing
extract_safe/validate_payload path. A checksum served by the same provider is
an integrity check, not an independent signature of publisher identity.
"""
from __future__ import annotations
import hashlib, json, os, re, ssl, time, urllib.error, urllib.parse, urllib.request
from pathlib import Path

OWNER='yongchu-64'; REPO='dialogue-tts'
ROOT=f'https://gitee.com/api/v5/repos/{OWNER}/{REPO}'
ASSETS={'darwin':'DialogueTTS-macOS-arm64.zip','win32':'DialogueTTS-Windows-x64.zip'}
MAX_ARCHIVE=128*1024*1024; MAX_JSON=1024*1024; MAX_PAGES=20; MAX_REDIRECTS=5
class ValidationError(ValueError): pass
class NetworkError(Exception): pass

class NoRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,*args,**kwargs): return None

def make_opener():
 import certifi
 ctx=ssl.create_default_context(); ctx.load_verify_locations(certifi.where())
 return urllib.request.build_opener(NoRedirect(),urllib.request.HTTPSHandler(context=ctx))

def url_ok(url, api=False):
 try:
  u=urllib.parse.urlsplit(url)
  if u.scheme!='https' or u.username or u.password or u.port not in (None,443) or u.fragment: return False
  if api:
   return u.hostname=='gitee.com' and u.path.startswith(f'/api/v5/repos/{OWNER}/{REPO}/') and '..' not in urllib.parse.unquote(u.path).split('/')
  if u.hostname=='gitee.com':
   return u.path.startswith(f'/{OWNER}/{REPO}/releases/download/') and not u.query and '..' not in urllib.parse.unquote(u.path).split('/')
  return u.hostname=='foruda.gitee.com' and u.path.startswith('/attach_file/') and '..' not in urllib.parse.unquote(u.path).split('/')
 except (TypeError,ValueError): return False

def request(opener,url,*,api=False,attachment=None):
 """Follow a bounded number of redirects only after validating every URL."""
 seen=set()
 # The CDN may appear only after a redirect from the fixed repository.
 if not api and urllib.parse.urlsplit(url).hostname != 'gitee.com': raise ValidationError('Initial URL must be the repository')
 for _ in range(MAX_REDIRECTS+1):
  allowed=url_ok(url,api)
  if not api and attachment is not None and len(seen)==1:
   expected=f"https://gitee.com/{OWNER}/{REPO}/attach_files/{attachment['id']}/download/{attachment['name']}"
   allowed=allowed or url==expected
  if not allowed: raise ValidationError('Unsafe URL or redirect')
  if url in seen: raise ValidationError('Redirect loop')
  seen.add(url)
  try:
   response=opener.open(urllib.request.Request(url,headers={'User-Agent':'DialogueTTS-Updater','Accept':'application/json' if api else '*/*'}),timeout=30)
  except urllib.error.HTTPError as e:
   if e.code in (301,302,303,307,308):
    location=e.headers.get('Location'); e.close()
    if not location: raise ValidationError('Redirect without location')
    url=urllib.parse.urljoin(url,location); continue
   e.close()
   if e.code==429 or 500<=e.code<=599: raise NetworkError('Gitee service unavailable') from e
   raise ValidationError('Gitee refused request') from e
  except urllib.error.URLError as e:
   if isinstance(e.reason,ssl.SSLError): raise ValidationError('TLS identity verification failed') from e
   raise NetworkError('Gitee connection unavailable') from e
  except ssl.SSLError as e: raise ValidationError('TLS identity verification failed') from e
  except (TimeoutError,ConnectionError,OSError) as e: raise NetworkError('Gitee connection unavailable') from e
  return response
 raise ValidationError('Too many redirects')

def bounded_read(response,limit):
 deadline=time.monotonic()+60; chunks=[]; count=0
 try:
  with response:
   while True:
    if time.monotonic()>deadline: raise NetworkError('Response timed out')
    if getattr(response,'isclosed',lambda:False)(): break
    set_read_timeout(response,deadline-time.monotonic())
    block=response.read1(min(65536,limit-count+1))
    if not block: break
    count+=len(block)
    if count>limit: raise ValidationError('Response too large')
    chunks.append(block)
  return b''.join(chunks)
 except ssl.SSLError as e: raise ValidationError('TLS verification failed') from e
 except (TimeoutError,ConnectionError,OSError) as e: raise NetworkError('Response interrupted') from e

def api_json(opener,url):
 try: return json.loads(bounded_read(request(opener,url,api=True),MAX_JSON))
 except (ValueError,UnicodeDecodeError) as e:
  if isinstance(e,ValidationError): raise
  raise ValidationError('Invalid metadata JSON') from e

def version(tag):
 if not isinstance(tag,str) or len(tag)>64 or not re.fullmatch(r'v(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)',tag): raise ValidationError('Invalid stable tag')
 return tuple(map(int,tag[1:].split('.')))

def all_pages(opener,path):
 items=[]; empties=0
 for page in range(1,MAX_PAGES+1):
  data=api_json(opener,f'{ROOT}/{path}?page={page}&per_page=100')
  if not isinstance(data,list) or len(data)>100: raise ValidationError('Invalid metadata page')
  items.extend(data)
  empties=empties+1 if not data else 0
  if empties==2: return items
 raise ValidationError('Pagination limit reached')

def select_release(releases,current):
 now=version('v'+current); candidates=[]
 if not isinstance(releases,list): raise ValidationError('Invalid release list')
 for r in releases:
  if not isinstance(r,dict): raise ValidationError('Invalid release')
  if r.get('prerelease') is not False or r.get('draft') is True: continue
  try: v=version(r.get('tag_name'))
  except ValidationError: continue
  if v>now: candidates.append((v,r))
 if not candidates: return None
 top=max(v for v,_ in candidates); matches=[r for v,r in candidates if v==top]
 if len(matches)!=1: raise ValidationError('Ambiguous release')
 r=matches[0]
 if type(r.get('id')) is not int or r['id']<=0: raise ValidationError('Invalid release ID')
 return r

def select_assets(attachments,tag,platform):
 version(tag)
 if platform not in ASSETS or not isinstance(attachments,list): raise ValidationError('Unsupported input')
 name=ASSETS[platform]; out=[]
 for n in (name,name+'.sha256'):
  matches=[a for a in attachments if isinstance(a,dict) and a.get('name')==n]
  if len(matches)!=1: raise ValidationError('Missing or ambiguous attachment')
  a=matches[0]; limit=MAX_ARCHIVE if n==name else 4096
  expected=f'https://gitee.com/{OWNER}/{REPO}/releases/download/{tag}/{n}'
  if a.get('browser_download_url')!=expected or not url_ok(expected): raise ValidationError('Invalid attachment URL')
  if type(a.get('size')) is not int or not 0<a['size']<=limit: raise ValidationError('Invalid attachment size')
  if type(a.get('id')) is not int or a['id']<=0: raise ValidationError('Invalid attachment ID')
  out.append({'name':n,'url':expected,'size':a['size'],'id':a['id']})
 return out

def offer(opener,current,platform):
 r=select_release(all_pages(opener,'releases'),current)
 if r is None: return {'state':'latest','current_version':current}
 tag=r['tag_name']; assets=select_assets(all_pages(opener,f"releases/{r['id']}/attach_files"),tag,platform)
 return {'state':'available','version':tag[1:],'platform':platform,'archive':assets[0],'checksum':assets[1]}

def parse_checksum(data,filename):
 try: text=data.decode('ascii').strip()
 except (UnicodeDecodeError,AttributeError): raise ValidationError('Invalid checksum')
 m=re.fullmatch(r'([0-9a-f]{64})  '+re.escape(filename),text)
 if not m: raise ValidationError('Invalid checksum')
 return m.group(1)

def set_read_timeout(response,seconds):
 # urllib's HTTPS response holds the verified TLS socket here. Fail closed if
 # the runtime cannot expose it; never claim a deadline we cannot enforce.
 try: sock=response.fp.raw._sock
 except AttributeError as e: raise ValidationError('Cannot enforce download deadline') from e
 if seconds<=0: raise NetworkError('Download timed out')
 sock.settimeout(min(30,seconds))

def download_verified(opener,selected,destination,progress=None):
 """Save exclusively; validate the declared size and the supplied SHA-256."""
 archive=selected['archive']; check=selected['checksum']
 raw=bounded_read(request(opener,check['url'],attachment=check),4096)
 if len(raw)!=check['size']: raise ValidationError('Checksum size mismatch')
 expected=parse_checksum(raw,archive['name'])
 if expected!=archive.get('sha256') or hashlib.sha256(raw).hexdigest()!=check.get('sha256'): raise ValidationError('Checksum changed since update check')
 h=hashlib.sha256(); count=0; created=False; deadline=time.monotonic()+600
 try:
  with request(opener,archive['url'],attachment=archive) as response, open(destination,'xb') as f:
   created=True
   while True:
    if time.monotonic()>deadline: raise NetworkError('Download timed out')
    if getattr(response,'isclosed',lambda:False)(): break
    set_read_timeout(response,deadline-time.monotonic())
    try: block=response.read1(min(256*1024,archive['size']-count+1))
    except ssl.SSLError as e: raise ValidationError('TLS verification failed') from e
    except (TimeoutError,ConnectionError,OSError) as e: raise NetworkError('Archive transfer interrupted') from e
    if not block: break
    count+=len(block)
    if count>archive['size']: raise ValidationError('Archive exceeds declared size')
    f.write(block);h.update(block)
    if progress: progress(count,archive['size'])
   f.flush(); os.fsync(f.fileno())
  if count!=archive['size'] or h.hexdigest()!=expected: raise ValidationError('Archive size or SHA-256 mismatch')
  return expected
 except OSError as e:
  if created: Path(destination).unlink(missing_ok=True)
  raise ValidationError('Local archive write failed') from e
 except BaseException:
  if created: Path(destination).unlink(missing_ok=True)
  raise

def with_network_fallback(gitee_call,github_call):
 try: return gitee_call()
 except NetworkError: return github_call()

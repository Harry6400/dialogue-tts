"""Source selection and exact-version fallback for the existing updater."""
import hashlib, json, sys
from . import gitee_provider as g
from .safe_updater import fetch_release, release_offer, UpdateError


def discover(current, platform):
 try:
  op=g.make_opener(); result=g.offer(op,current,platform)
  result['source']='gitee'
  if result['state']=='available':
   check=result['checksum']
   raw=g.bounded_read(g.request(op,check['url'],attachment=check),4096)
   if len(raw)!=check['size']: raise g.ValidationError('Checksum size mismatch')
   result['archive']['sha256']=g.parse_checksum(raw,result['archive']['name'])
   result['checksum']['sha256']=hashlib.sha256(raw).hexdigest()
  return result
 except g.NetworkError:
  result=release_offer(fetch_release(),current,platform);result['source']='github';return result


def matching_github(selected):
 """Fallback may use only precisely the selected version and identical bytes."""
 candidate=release_offer(fetch_release(),'0.0.0',selected['platform'])
 if candidate.get('state')!='available' or candidate.get('version')!=selected['version']:
  raise UpdateError('备用源版本不一致，请重新检查更新')
 for key in ('archive','checksum'):
  for field in ('name','size','sha256'):
   if candidate[key][field]!=selected[key][field]: raise UpdateError('备用源校验信息不一致')
 candidate['source']='github'
 return candidate


def offer_identity(offer):
 if offer.get('state')!='available': raise UpdateError('No update identity')
 value={'version':offer['version'],'platform':offer['platform'],
        'archive':{k:offer['archive'][k] for k in ('name','size','sha256')},
        'checksum':{k:offer['checksum'][k] for k in ('name','size','sha256')}}
 return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()

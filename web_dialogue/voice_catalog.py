"""Validated snapshot of the public Edge voice directory; no dialogue is sent."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

from .normalize import VOICES

_ID = re.compile(r'^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,16}){1,4}-[A-Za-z0-9]{1,50}Neural$')
_LOCALE = re.compile(r'^[a-z]{2,3}(?:-[A-Z][a-z]{3})?-[A-Z]{2}(?:-[a-z0-9]{2,16})?$')

def validate_voice_id(value, voices):
    return isinstance(value, str) and any(v['id'] == value for v in voices)

def canonicalize(raw):
    if not isinstance(raw, list) or not 1 <= len(raw) <= 2000: raise ValueError('无效音色目录')
    result = {}
    for entry in raw:
        if not isinstance(entry, dict): raise ValueError('无效音色目录项')
        ident = entry.get('ShortName', entry.get('id')); locale = entry.get('Locale', entry.get('locale'))
        gender = entry.get('Gender', entry.get('gender'))
        if not isinstance(ident, str) or not _ID.fullmatch(ident) or not isinstance(locale, str) or not _LOCALE.fullmatch(locale) or not ident.startswith(locale+'-') or gender not in ('Male','Female'):
            raise ValueError('无效音色元数据')
        label = next((v['label'] for v in VOICES if v['id'] == ident), ident)
        voice = {'id': ident, 'locale': locale, 'gender': gender, 'label': label}
        if ident in result and result[ident] != voice: raise ValueError('重复音色元数据不一致')
        result[ident] = voice
    return sorted(result.values(), key=lambda v:v['id'])

def revision(voices):
    payload=json.dumps(voices,ensure_ascii=True,sort_keys=True,separators=(',',':')).encode()
    return hashlib.sha256(payload).hexdigest()

def cache_path():
    if os.name == 'nt': root=Path(os.environ.get('LOCALAPPDATA', Path.home()/'AppData'/'Local'))
    elif __import__('sys').platform == 'darwin': root=Path.home()/'Library'/'Application Support'
    else: root=Path(os.environ.get('XDG_DATA_HOME', Path.home()/'.local'/'share'))
    return root/'DialogueTTS'/'voice-catalog.json'

def fallback():
    return canonicalize([{'id':v['id'], 'locale':'zh-CN', 'gender':'Female' if i < 2 else 'Male'} for i,v in enumerate(VOICES)])

async def _fetch():
    import edge_tts
    return await asyncio.wait_for(edge_tts.list_voices(), timeout=5)

def load_catalog(path=None, fetch=None):
    """Freeze a single catalog revision for one service lifetime.

    fetch is injected only for tests. Cache stores public metadata, independently
    of preferences. Failure never modifies existing preferences or cache.
    """
    path = Path(path) if path is not None else cache_path()
    try:
        voices=canonicalize((fetch or (lambda: asyncio.run(_fetch())))())
        if not all(validate_voice_id(v['id'], voices) for v in fallback()): raise ValueError('目录缺少默认音色')
        record={'version':1,'revision':revision(voices),'voices':voices}
        try:
            path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            fd,name=tempfile.mkstemp(prefix='.voice-catalog-',dir=path.parent)
            try:
                with os.fdopen(fd,'w',encoding='utf-8') as f: json.dump(record,f,ensure_ascii=False)
                os.replace(name,path)
            finally:
                Path(name).unlink(missing_ok=True)
        except OSError: pass
        return {**record,'source':'online'}
    except Exception:
        try:
            if path.stat().st_size > 1024*1024: raise ValueError('缓存过大')
            record=json.loads(path.read_text(encoding='utf-8'))
            voices=canonicalize(record['voices'])
            if record.get('version')!=1 or record.get('revision')!=revision(voices) or not all(validate_voice_id(v['id'],voices) for v in fallback()): raise ValueError('缓存无效')
            return {'version':1,'revision':revision(voices),'voices':voices,'source':'cache'}
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            voices=fallback()
            return {'version':1,'revision':revision(voices),'voices':voices,'source':'fallback'}

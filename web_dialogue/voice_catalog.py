"""Versioned built-in Mandarin directory. Startup never uses network or cache."""
import hashlib
import json

from .normalize import VOICES


def validate_voice_id(value, voices):
    allowed = {voice['id'] for voice in VOICES}
    return isinstance(value, str) and value in allowed and any(voice['id'] == value for voice in voices)


def canonicalize(raw):
    if not isinstance(raw, list) or not 1 <= len(raw) <= 2000:
        raise ValueError('无效音色目录')
    supported = {voice['id']: voice for voice in VOICES}
    result = {}
    for entry in raw:
        if not isinstance(entry, dict):
            raise ValueError('无效音色目录项')
        ident = entry.get('ShortName', entry.get('id'))
        locale = entry.get('Locale', entry.get('locale'))
        gender = entry.get('Gender', entry.get('gender'))
        if not isinstance(ident, str) or not isinstance(locale, str) or gender not in ('Female', 'Male'):
            raise ValueError('无效音色元数据')
        if locale != 'zh-CN' or ident not in supported:
            continue
        voice = {**supported[ident], 'locale': locale, 'gender': gender}
        if voice != supported[ident] or ident in result and result[ident] != voice:
            raise ValueError('音色元数据不一致')
        result[ident] = voice
    return sorted(result.values(), key=lambda voice: voice['id'])


def revision(voices):
    payload = json.dumps(voices, ensure_ascii=True, sort_keys=True, separators=(',', ':')).encode()
    return hashlib.sha256(payload).hexdigest()


def fallback():
    # Kept for existing internal/test callers; it is now the normal directory.
    return canonicalize(VOICES)


def load_catalog():
    voices = fallback()
    return {'version': 1, 'revision': revision(voices), 'voices': voices, 'source': 'builtin'}

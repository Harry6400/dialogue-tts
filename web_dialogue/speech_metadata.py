"""Text-free, bounded metadata for one verified synthesis result."""
import json
import math
import re

MAX_METADATA = 4 * 1024 * 1024


def read_metadata(path):
    import os
    import stat
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or path.is_symlink() or before.st_size > MAX_METADATA:
        raise ValueError('Invalid speech metadata')
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0))
    with os.fdopen(fd, 'rb') as handle:
        if os.fstat(handle.fileno()).st_ino != before.st_ino:
            raise ValueError('Speech metadata changed')
        data = handle.read(MAX_METADATA + 1)
    if len(data) > MAX_METADATA:
        raise ValueError('Speech metadata too large')
    return json.loads(data)


def compact_timing(value, digest, total=None):
    if not isinstance(value, dict) or value.get('audio_sha256') != digest:
        return None
    duration, rows = value.get('duration_ms'), value.get('segments')
    if type(duration) is not int or not 0 < duration <= 24 * 60 * 60 * 1000 or not isinstance(rows, list) or not 0 < len(rows) <= 60000:
        return None
    if total is not None and len(rows) != total:
        return None
    cleaned, ids, end = [], set(), 0
    for row in rows:
        if not isinstance(row, dict): return None
        ident, start, stop = row.get('id'), row.get('start_ms'), row.get('end_ms')
        if any(type(n) is not int for n in (ident, start, stop)) or ident < 0 or ident in ids or start % 24 or stop % 24 or not end <= start < stop <= duration:
            return None
        ids.add(ident); end = stop
        cleaned.append({'id': ident, 'start_ms': start, 'end_ms': stop})
    if end != duration or cleaned[0]['start_ms'] != 0:
        return None
    return {'duration_ms': duration, 'audio_sha256': digest, 'segments': cleaned}


EVENTS = {'connecting', 'dns_start', 'dns_done', 'connected', 'waiting_audio', 'first_audio', 'receiving', 'retrying', 'segment_done', 'merging', 'done', 'error'}
PHASES = {'connecting', 'waiting_audio', 'receiving', 'retrying', 'merging', 'saving', 'done', 'error'}


def safe_diagnostics(value):
    if not isinstance(value, dict): return None
    mode = value.get('connection_mode')
    if mode not in ('direct', 'system_proxy'): return None
    result = {'connection_mode': mode, 'proxy_in_use': value.get('proxy_in_use') is True, 'events': []}
    events=value.get('events', [])
    if not isinstance(events,list):return None
    for event in events[-100:]:
        if not isinstance(event, dict) or event.get('event') not in EVENTS: continue
        entry = {'event': event['event']}
        for key in ('elapsed_ms', 'segment', 'attempt', 'dns_ms', 'connection_ms', 'first_audio_ms', 'bytes', 'http_status', 'errno'):
            n = event.get(key)
            if type(n) in (int, float) and math.isfinite(n) and 0 <= n <= 10**12: entry[key] = round(n)
        name = event.get('error_type')
        if isinstance(name, str) and re.fullmatch('[A-Za-z][A-Za-z0-9_]{0,79}', name): entry['error_type'] = name
        result['events'].append(entry)
    return result

"""Versioned synthesis identity. Never expose raw call metadata."""
import hashlib
import json


def dialogue_fingerprint(call_reference, script):
    if not isinstance(call_reference, str) or not call_reference.strip():
        raise ValueError('A non-empty string call reference is required')
    identity = {
        'schema': 'dialogue-tts-fingerprint-v1',
        'format': 'edge-mp3-24khz-48kbitrate-mono',
        'call_reference': call_reference,
        'initial_silence_ms': script.get('settings', {}).get('initial_silence_ms', 0),
        'lines': [{key: line[key] for key in ('speaker', 'text', 'voice', 'pause_after_ms', 'speech_rate')}
                  for line in script['lines']],
    }
    return hashlib.sha256(json.dumps(identity, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()

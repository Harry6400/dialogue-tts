"""Worker-local progress and whitelisted connection timings; no request text."""
import time
from collections import deque


class SpeechProgress:
    def __init__(self, publish, total, mode, proxy, attempt_limit=5):
        self.publish = publish
        self.started = time.monotonic()
        self.phase_started = self.started
        self.events = deque(maxlen=100)
        self.fields = dict(status='running', phase='connecting', completed=0, total=total,
                           segment=1, attempt=1, attempt_limit=attempt_limit,
                           connection_mode=mode, received_bytes=0)
        self.mode, self.proxy = mode, bool(proxy)
        self.connection_started = self.started
        self.dns_started = self.started
        self.first_audio = False
        self.published_at = 0

    def emit(self, force=False):
        now = time.monotonic()
        if not force and now-self.published_at < .5: return
        self.published_at = now
        self.publish(**self.fields, elapsed_ms=round((now-self.started)*1000),
                     phase_elapsed_ms=round((now-self.phase_started)*1000),
                     diagnostics={'connection_mode': self.mode, 'proxy_in_use': self.proxy, 'events': list(self.events)})

    def update(self, phase, event=None, **values):
        changed = phase != self.fields['phase']
        if changed:
            self.phase_started = time.monotonic()
        self.fields.update(phase=phase, **values)
        if event:
            self.record(event)
        self.emit(force=changed or event is not None)

    def record(self, event, **values):
        self.events.append(dict(event=event, elapsed_ms=round((time.monotonic()-self.started)*1000),
                                segment=self.fields['segment'], attempt=self.fields['attempt'], **values))

    def trace(self):
        import aiohttp
        config = aiohttp.TraceConfig()
        async def dns_start(*_):
            self.dns_started = time.monotonic(); self.record('dns_start')
        async def dns_done(*_):
            self.record('dns_done', dns_ms=(time.monotonic()-self.dns_started)*1000)
        async def connection_start(*_):
            self.connection_started = time.monotonic()
            self.update('connecting', 'connecting')
        async def connection_done(*_):
            self.record('connected', connection_ms=(time.monotonic()-self.connection_started)*1000)
            self.emit()
        async def response(*_):
            self.update('waiting_audio', 'waiting_audio')
        config.on_dns_resolvehost_start.append(dns_start)
        config.on_dns_resolvehost_end.append(dns_done)
        config.on_connection_create_start.append(connection_start)
        config.on_connection_create_end.append(connection_done)
        config.on_request_end.append(response)
        return config

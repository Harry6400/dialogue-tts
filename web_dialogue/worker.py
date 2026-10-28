"""Standalone Edge TTS worker. No code or imports from the earlier repository."""
import asyncio
import io
import hashlib
import time
import json
from pathlib import Path
import ssl
import sys
from urllib.parse import urlsplit
from urllib.request import getproxies, proxy_bypass
from .speech_settings import (DEFAULT_RETRY_ATTEMPTS, retry_delay_seconds,
                              segment_timeout_seconds, validate_retry_attempts)


class SpeechHTTP:
    """Confine proxy selection to this worker; disable aiohttp implicit lookup."""
    def __init__(self, module, progress=None):
        self.module = module
        self.progress = progress

    def __getattr__(self, name):
        return getattr(self.module, name)

    def ClientSession(self, *args, **kwargs):
        kwargs['trust_env'] = False
        if self.progress:
            kwargs['trace_configs'] = [*kwargs.get('trace_configs', []), self.progress.trace()]
        return self.module.ClientSession(*args, **kwargs)


def speech_proxy(url):
    """Use the HTTPS system proxy for secure WebSockets as well.

    aiohttp looks up `wss` separately; macOS normally only supplies `https`.
    Keep explicit WebSocket proxies and the system bypass list authoritative.
    """
    host = urlsplit(url).hostname
    if host and proxy_bypass(host):
        return None
    proxies = getproxies()
    return proxies.get('wss') or proxies.get('https') or proxies.get('http')


def failure_message(exc):
    import aiohttp
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        return "微软语音服务响应超时（单段时限随尝试次数调整，最多 5 分钟）。请稍后重试。"
    if isinstance(exc, (ssl.SSLError, aiohttp.ClientSSLError)):
        return "HTTPS 证书或 TLS 连接验证失败。请检查系统时间、可信证书和代理，勿关闭 TLS 验证。"
    if isinstance(exc, aiohttp.ClientProxyConnectionError):
        return "无法连接系统代理。请确认代理应用已运行、端口可用后重试。"
    if isinstance(exc, aiohttp.ClientConnectorError) and isinstance(exc.os_error, ConnectionResetError):
        return '语音连接被中断，请稍后重试。'
    if isinstance(exc, aiohttp.ClientConnectorError):
        return "无法连接微软语音服务。请检查 DNS 和服务可用性后重试。"
    if isinstance(exc, aiohttp.ClientResponseError):
        if exc.status == 429:
            return "微软语音服务暂时限流，请稍后重试。"
        if exc.status in (401, 403):
            return "微软语音服务拒绝连接。请稍后重试。"
        return "微软语音服务连接未成功，请稍后重试。"
    if isinstance(exc, ValueError):
        return "音频格式与当前版本不兼容，请检查更新后重试。"
    return "语音合成或音频处理失败。请检查网络与服务可用性后重试；输入文件没有被修改。"


def write_status(folder, **data):
    target = folder / "worker_status.json"
    staging = folder / "worker_status.tmp"
    staging.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    staging.replace(target)


async def generate(folder):
    import edge_tts
    import edge_tts.communicate
    import certifi
    from edge_tts.constants import WSS_URL
    from .mp3_frames import join_segments

    # Preserve OS trust and add the bundled public root certificates.
    context = ssl.create_default_context()
    context.load_verify_locations(cafile=certifi.where())
    edge_tts.communicate._SSL_CTX = context
    import aiohttp
    script = json.loads((folder / "normalized.json").read_text(encoding="utf-8"))
    mode = script.get('connection_mode', 'direct')
    if mode not in ('direct', 'system_proxy'):
        raise ValueError('Invalid connection mode')
    retry_attempts = script.get('retry_attempts', DEFAULT_RETRY_ATTEMPTS)
    validate_retry_attempts(retry_attempts)
    segment_timeout = segment_timeout_seconds(retry_attempts)
    proxy = speech_proxy(WSS_URL) if mode == 'system_proxy' else None
    lines = script["lines"]
    from .speech_progress import SpeechProgress
    progress = SpeechProgress(lambda **data: write_status(folder, **data), len(lines), mode, proxy, retry_attempts)
    edge_tts.communicate.aiohttp = SpeechHTTP(aiohttp, progress)
    progress.emit()
    segments = []

    async def synthesize(line):
        percent = round((line["speech_rate"] - 1.0) * 100)
        for attempt in range(1, retry_attempts + 1):
            progress.update('connecting', 'connecting', attempt=attempt)
            first_audio = True
            attempt_started = time.monotonic()
            try:
                stream = edge_tts.Communicate(line["text"], line["voice"], rate=f"{percent:+d}%", proxy=proxy)
                buffer = io.BytesIO()
                async for event in stream.stream():
                    if event["type"] == "audio":
                        buffer.write(event["data"])
                        if first_audio:
                            progress.record('first_audio', first_audio_ms=(time.monotonic()-attempt_started)*1000)
                            first_audio = False
                        progress.update('receiving', received_bytes=buffer.tell())
                if buffer.tell() == 0:
                    raise RuntimeError("No audio was returned")
                return buffer.getvalue()
            except aiohttp.ClientConnectorError as exc:
                # Connector failures precede the WebSocket/audio stream. Never
                # retry TLS failures, switch routes, or repeat completed lines.
                if not isinstance(exc.os_error, ConnectionResetError) or attempt == retry_attempts:
                    raise
                progress.record('error', error_type=type(exc.os_error).__name__)
                progress.update('retrying', 'retrying')
                await asyncio.sleep(retry_delay_seconds(attempt))

    async def heartbeat():
        while True:
            await asyncio.sleep(.5)
            progress.emit()

    pulse = asyncio.create_task(heartbeat())
    try:
        for index, line in enumerate(lines):
            progress.update('connecting', segment=index+1, received_bytes=0)
            clip = await asyncio.wait_for(synthesize(line), timeout=segment_timeout)
            segments.append(clip)
            progress.update('receiving', 'segment_done', completed=index+1)
    except BaseException as exc:
        cause = getattr(exc, 'os_error', None) or exc
        progress.record('error', error_type=type(cause).__name__, http_status=getattr(exc,'status',None), errno=getattr(cause,'errno',None))
        progress.emit(force=True)
        raise
    finally:
        pulse.cancel()
        try: await pulse
        except asyncio.CancelledError: pass
    progress.update('merging', 'merging')

    silence = (Path(__file__).resolve().parent / "silence.mp3").read_bytes()
    audio, spans, duration = join_segments(segments, [line["pause_after_ms"] for line in lines], silence)
    output = folder / "dialogue.mp3"
    temporary = folder / "dialogue.partial"
    temporary.write_bytes(audio)
    temporary.replace(output)
    if output.stat().st_size < 100 or duration == 0:
        raise RuntimeError("Empty output")
    timeline = [{'id': line['id'], 'start_ms': span[0], 'end_ms': span[1]}
                for line, span in zip(lines, spans)]
    metadata = {'duration_ms': duration, 'audio_sha256': hashlib.sha256(audio).hexdigest(), 'segments': timeline}
    staging = folder / 'timeline.tmp'
    staging.write_text(json.dumps(metadata), encoding='utf-8')
    staging.replace(folder / 'timeline.json')
    progress.update('done', 'done', status='done', completed=len(lines), duration_ms=duration)


def main(folder_path=None):
    folder = Path(folder_path if folder_path is not None else sys.argv[1]).resolve()
    try:
        asyncio.run(generate(folder))
    except Exception as exc:
        from .speech_metadata import read_metadata, safe_diagnostics
        previous = {}
        try:
            old = read_metadata(folder / 'worker_status.json')
            previous = {key: old[key] for key in ('completed', 'total', 'segment', 'attempt', 'attempt_limit', 'connection_mode', 'elapsed_ms', 'phase_elapsed_ms') if key in old}
            previous['diagnostics'] = safe_diagnostics(old.get('diagnostics'))
            previous['failed_phase'] = old.get('phase')
        except (OSError, ValueError, TypeError): pass
        mode_label='系统代理' if previous.get('connection_mode')=='system_proxy' else '直连'
        completed,total=previous.get('completed',0),previous.get('total',0)
        context=f'{mode_label}，已完成 {completed}/{total} 段。' if total else f'{mode_label}。'
        attempt, limit = previous.get('attempt'), previous.get('attempt_limit')
        if previous.get('failed_phase') in {'connecting', 'retrying'} and type(attempt) is int and type(limit) is int:
            context += f'连接已尝试 {attempt}/{limit} 次。'
        write_status(folder, **previous, status='error', phase='error', error=context+failure_message(exc), error_type=type(exc).__name__)
        sys.exit(1)


if __name__ == "__main__":
    main()

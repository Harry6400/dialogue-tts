"""Standalone Edge TTS worker. No code or imports from the earlier repository."""
import asyncio
import io
import json
from pathlib import Path
import ssl
import sys


def write_status(folder, **data):
    target = folder / "worker_status.json"
    staging = folder / "worker_status.tmp"
    staging.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    staging.replace(target)


async def generate(folder):
    import edge_tts
    import edge_tts.communicate
    import certifi
    from .mp3_frames import join_segments

    # Preserve OS trust and add the bundled public root certificates.
    context = ssl.create_default_context()
    context.load_verify_locations(cafile=certifi.where())
    edge_tts.communicate._SSL_CTX = context
    script = json.loads((folder / "normalized.json").read_text(encoding="utf-8"))
    lines = script["lines"]
    write_status(folder, status="running", completed=0, total=len(lines))
    segments = []
    timeline = []
    position = 0

    async def synthesize(line):
        percent = round((line["speech_rate"] - 1.0) * 100)
        stream = edge_tts.Communicate(line["text"], line["voice"], rate=f"{percent:+d}%")
        buffer = io.BytesIO()
        async for event in stream.stream():
            if event["type"] == "audio":
                buffer.write(event["data"])
        if buffer.tell() == 0:
            raise RuntimeError("No audio was returned")
        return buffer.getvalue()

    for index, line in enumerate(lines):
        clip = await asyncio.wait_for(synthesize(line), timeout=90)
        segments.append(clip)
        write_status(folder, status="running", completed=index + 1, total=len(lines))

    silence = (Path(__file__).resolve().parent / "silence.mp3").read_bytes()
    audio, spans, duration = join_segments(segments, [line["pause_after_ms"] for line in lines], silence)
    output = folder / "dialogue.mp3"
    temporary = folder / "dialogue.partial"
    temporary.write_bytes(audio)
    temporary.replace(output)
    if output.stat().st_size < 100 or duration == 0:
        raise RuntimeError("Empty output")
    timeline = [{"id": line["id"], "speaker": line["speaker"], "text": line["text"],
                 "start_ms": span[0], "end_ms": span[1], "audio_duration_ms": span[1] - span[0]}
                for line, span in zip(lines, spans)]
    (folder / "timeline.json").write_text(json.dumps({
        "duration_ms": duration, "segments": timeline,
        "note": "新音频时间轴，不对应原录音；MP3帧为24毫秒，设定停顿量化误差不超过12毫秒。"
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    write_status(folder, status="done", completed=len(lines), total=len(lines), duration_ms=duration)


def main(folder_path=None):
    folder = Path(folder_path if folder_path is not None else sys.argv[1]).resolve()
    try:
        asyncio.run(generate(folder))
    except Exception as exc:
        detail = str(exc).lower()
        if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
            message = "微软语音服务响应超时（单段 90 秒）。请缩短文本或稍后重试。"
        elif "certificate" in detail or "ssl" in detail:
            message = "HTTPS 证书验证失败。请更新系统可信证书，勿关闭 TLS 验证。"
        elif isinstance(exc, ValueError):
            message = "音频格式与当前版本不兼容，请检查更新后重试。"
        else:
            message = "语音合成或音频处理失败。请检查网络与服务可用性后重试；输入文件没有被修改。"
        write_status(folder, status="error", error=message, error_type=type(exc).__name__)
        sys.exit(1)


if __name__ == "__main__":
    main()

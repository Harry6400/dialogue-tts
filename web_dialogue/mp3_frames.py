"""Original fixed-format MP3 frame utility shared by the desktop builds.
No decoder, external backend, local model or FFmpeg binary is required.
The Edge service format must stay MPEG2 Layer III, mono 24 kHz / 48 kbit/s.
"""
def parse_frames(data):
 """Strict fixed-format MPEG2 Layer III parser. Reject format drift, don't guess."""
 start=0
 if data[:3]==b'ID3':
  if len(data)<10:raise ValueError('音频标签不完整')
  start=10+sum((data[6+i]&127)<<(21-7*i) for i in range(4))
 frames=[]
 while start<len(data):
  if data[start:start+3]==b'TAG' and len(data)-start==128:break
  if len(data)-start<4:raise ValueError('音频帧不完整')
  h=int.from_bytes(data[start:start+4],'big')
  if (h>>21)!=0x7ff or ((h>>19)&3)!=2 or ((h>>17)&3)!=1:raise ValueError('音频格式变化，请更新应用')
  bi=(h>>12)&15;si=(h>>10)&3
  rates=[8000,16000,24000,32000,40000,48000,56000,64000,80000,96000,112000,128000,144000,160000]
  if bi not in range(1,15) or si not in range(3):raise ValueError('音频帧头无效')
  bitrate=rates[bi-1]; sample=[22050,24000,16000][si]
  if bitrate!=48000 or sample!=24000 or ((h>>6)&3)!=3:raise ValueError('不支持的音频格式')
  size=72*bitrate//sample+((h>>9)&1)
  frame=data[start:start+size]
  if len(frame)!=size:raise ValueError('音频帧截断')
  # The service format is CBR. Skip encoder index headers when present.
  if not any(tag in frame[4:40] for tag in (b'Xing',b'Info',b'VBRI')):frames.append(frame)
  start+=size
 if not frames:raise ValueError('没有有效音频')
 return frames

FRAME_MS = 24

def independent_start(frames):
    header = int.from_bytes(frames[0][:4], "big")
    side_info = 4 if (header >> 16) & 1 else 6
    if frames[0][side_info] != 0:
        raise ValueError("音频首帧依赖缺失的历史数据，请更新应用")

def join_segments(parts, pauses, silence_data):
    silence = parse_frames(silence_data)
    independent_start(silence)
    output = bytearray()
    spans = []
    frame_count = 0
    for index, data in enumerate(parts):
        frames = parse_frames(data)
        independent_start(frames)
        if index:
            gap = round(pauses[index - 1] / FRAME_MS)
            for n in range(gap): output.extend(silence[n % len(silence)])
            frame_count += gap
        start_ms = frame_count * FRAME_MS
        for frame in frames: output.extend(frame)
        frame_count += len(frames)
        spans.append((start_ms, frame_count * FRAME_MS))
    return bytes(output), spans, frame_count * FRAME_MS

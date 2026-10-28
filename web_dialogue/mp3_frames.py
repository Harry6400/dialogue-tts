"""Original fixed-format MP3 frame utility shared by the desktop builds.
No decoder, external backend, local model or FFmpeg binary is required.
The Edge service format must stay MPEG2 Layer III, mono 24 kHz / 48 kbit/s.
"""
import hashlib
import os
import stat

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


def validated_file_digest(path, *, with_duration=False):
    """Validate one bounded MP3 and hash through the same no-follow fd.

    Unlike parse_frames this keeps neither the complete file nor its frames
    in RAM. It accepts exactly the existing fixed service MP3 format.
    """
    before=path.lstat()
    descriptor=os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_BINARY',0))
    with os.fdopen(descriptor,'rb') as handle:
        info=os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or (info.st_dev,info.st_ino)!=(before.st_dev,before.st_ino) or not 0<info.st_size<=256*1024*1024:
            raise ValueError('缓存文件类型或大小无效。')
        prefix=handle.read(10)
        start=0
        if prefix[:3]==b'ID3':
            if len(prefix)<10:raise ValueError('缓存标签不完整。')
            start=10+sum((prefix[6+i]&127)<<(21-7*i) for i in range(4))
            if start>info.st_size:raise ValueError('缓存标签越界。')
        handle.seek(start)
        frames=0
        while start<info.st_size:
            header=handle.read(4)
            if header[:3]==b'TAG' and info.st_size-start==128:
                if len(handle.read(124))!=124:raise ValueError('缓存标签不完整。')
                start=info.st_size;break
            if len(header)!=4:raise ValueError('缓存帧不完整。')
            word=int.from_bytes(header,'big')
            if word>>21!=0x7ff or (word>>19)&3!=2 or (word>>17)&3!=1 or (word>>12)&15!=6 or (word>>10)&3!=1 or (word>>6)&3!=3:
                raise ValueError('缓存音频格式无效。')
            size=144+((word>>9)&1)
            body=handle.read(size-4)
            if len(body)!=size-4:raise ValueError('缓存音频截断。')
            if not any(tag in body[:36] for tag in (b'Xing',b'Info',b'VBRI')):frames+=1
            start+=size
        if not frames:raise ValueError('缓存没有有效音频。')
        handle.seek(0);digest=hashlib.sha256()
        for block in iter(lambda:handle.read(65536),b''):digest.update(block)
        after=os.fstat(handle.fileno())
        if (after.st_size,after.st_mtime_ns,after.st_ctime_ns)!=(info.st_size,info.st_mtime_ns,info.st_ctime_ns):
            raise ValueError('核验期间缓存已变化。')
        result=(info.st_size,digest.hexdigest())
        return (*result,frames*FRAME_MS) if with_duration else result

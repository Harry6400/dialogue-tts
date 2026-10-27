import unittest
from pathlib import Path
from .mp3_frames import parse_frames, independent_start, join_segments

class FrameTests(unittest.TestCase):
 def setUp(self):self.data=(Path(__file__).parent/'silence.mp3').read_bytes()
 def test_format_and_independence(self):
  frames=parse_frames(self.data)
  for frame in frames:independent_start([frame])
 def test_two_clips_and_pause(self):
  frames=parse_frames(self.data);duration=len(frames)*24
  audio,spans,total=join_segments([self.data,self.data],[250,0],self.data)
  self.assertEqual(spans,[(0,duration),(duration+240,duration*2+240)])
  self.assertEqual(total,duration*2+240)
  self.assertEqual(len(parse_frames(audio))*24,total)
 def test_damaged_input(self):
  for b in (b'',b'ID3',b'not audio',self.data[:-1]):
   with self.subTest(data=b[:10]),self.assertRaises(ValueError):parse_frames(b)

if __name__=='__main__':unittest.main()

import subprocess
import sys
import time
import unittest
from unittest.mock import patch
from .app import Jobs, audition_script
from .normalize import InputError, normalize
from .test_normalize import EXAMPLE


class JobsTests(unittest.TestCase):
    def setUp(self):
        self.jobs = Jobs(timeout_seconds=0.4)
        self.script = normalize({'data': EXAMPLE})['script']
        self.original_popen = subprocess.Popen
        self.patch = patch('web_dialogue.app.subprocess.Popen', self.sleep_process)
        self.patch.start()
    def sleep_process(self, *args, **kwargs):
        # Replace only synthesis workers; taskkill must remain a real subprocess.
        command = args[0] if args else kwargs.get('args', [])
        if 'web_dialogue.worker' in command or '--worker' in command:
            return self.original_popen([sys.executable, '-c', 'import time; time.sleep(20)'], **kwargs)
        return self.original_popen(*args, **kwargs)
    def tearDown(self):
        self.jobs.close(); self.patch.stop()
    def test_watchdog_without_polling(self):
        job = self.jobs.start(self.script)
        proc = self.jobs.items[job['id']]['process']
        deadline = time.monotonic() + 10
        while proc.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertIsNotNone(proc.poll())
        result = self.jobs.snapshot(job['id'])
        self.assertEqual(result['status'], 'error')
        self.assertIn('15 分钟', result['error'])
    def test_cancel_and_restart(self):
        job = self.jobs.start(self.script)
        self.assertEqual(self.jobs.cancel(job['id'])['status'], 'cancelled')
        self.assertIsNotNone(self.jobs.items[job['id']]['process'].poll())
        next_job = self.jobs.start(self.script)
        self.assertNotEqual(next_job['id'], job['id'])
        self.jobs.cancel(next_job['id'])
    def test_parallel_guard_and_idempotency(self):
        a = self.jobs.start(self.script, 'request-123')
        b = self.jobs.start(self.script, 'request-123')
        self.assertEqual(a['id'], b['id'])
        with self.assertRaises(InputError): self.jobs.start(self.script)
        self.jobs.cancel(a['id'])
        self.assertEqual(self.jobs.start(self.script, 'request-123')['id'], a['id'])
    def test_audition_uses_fixed_text_and_current_voices(self):
        payload = {'consent': True, 'voices': {'AGENT': 'zh-CN-YunxiNeural', 'USER': 'zh-CN-XiaoxiaoNeural'}, 'rate': 1.25, 'pause_ms': 500, 'data': 'private input'}
        sample = audition_script(payload)
        self.assertEqual(len(sample['lines']), 2)
        self.assertEqual({x['speaker']: x['voice'] for x in sample['lines']}, payload['voices'])
        self.assertNotIn('private input', str(sample))
        self.assertTrue(all(x['speech_rate'] == 1.25 for x in sample['lines']))
        with self.assertRaises(InputError): audition_script({'consent': False})
    def test_audition_inherits_new_voices(self):
        voices = {'AGENT': 'zh-CN-YunxiaNeural', 'USER': 'zh-CN-YunyangNeural'}
        script = audition_script({'consent': True, 'voices': voices})
        self.assertEqual({line['speaker']: line['voice'] for line in script['lines']}, voices)
    def test_auditions_do_not_evict_production_jobs(self):
        production = self.jobs.start(self.script, fingerprint='a'*64)
        self.jobs.cancel(production['id'])
        for i in range(29):
            other = self.jobs.start(self.script)
            self.jobs.cancel(other['id'])
        for i in range(30):
            sample = self.jobs.start(self.script, audition=True)
            self.jobs.cancel(sample['id'])
        self.assertIn(production['id'], self.jobs.items)
        self.assertEqual(self.jobs.items[production['id']]['fingerprint'], 'a'*64)
        self.assertEqual(sum(bool(x.get('audition')) for x in self.jobs.items.values()), 3)
        self.assertTrue(all(x['fingerprint'] is None for x in self.jobs.items.values() if x.get('audition')))
    def test_audition_history_does_not_reduce_production_capacity(self):
        ids = []
        for _ in range(27):
            job = self.jobs.start(self.script); ids.append(job['id']); self.jobs.cancel(job['id'])
        for _ in range(3):
            job = self.jobs.start(self.script, audition=True); self.jobs.cancel(job['id'])
        job = self.jobs.start(self.script); self.jobs.cancel(job['id'])
        self.assertTrue(all(ident in self.jobs.items for ident in ids))
        self.assertEqual(sum(not item.get('audition') for item in self.jobs.items.values()), 28)
    def test_bad_voice_type(self):
        for voice in ([], {}, None, 3):
            with self.subTest(voice=voice), self.assertRaises(InputError):
                normalize({'data': EXAMPLE, 'voices': {'A': voice}})


if __name__ == '__main__': unittest.main()

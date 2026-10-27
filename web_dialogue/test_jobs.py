import subprocess
import sys
import time
import unittest
from unittest.mock import patch
from .app import Jobs
from .normalize import EXAMPLE, InputError, normalize


class JobsTests(unittest.TestCase):
    def setUp(self):
        self.jobs = Jobs(timeout_seconds=0.4)
        self.script = normalize({'data': EXAMPLE})['script']
        self.original_popen = subprocess.Popen
        self.patch = patch('web_dialogue.app.subprocess.Popen', self.sleep_process)
        self.patch.start()
    def sleep_process(self, *args, **kwargs):
        return self.original_popen([sys.executable, '-c', 'import time; time.sleep(20)'], **kwargs)
    def tearDown(self):
        self.jobs.close(); self.patch.stop()
    def test_watchdog_without_polling(self):
        job = self.jobs.start(self.script)
        proc = self.jobs.items[job['id']]['process']
        time.sleep(0.7)
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
    def test_bad_voice_type(self):
        for voice in ([], {}, None, 3):
            with self.subTest(voice=voice), self.assertRaises(InputError):
                normalize({'data': EXAMPLE, 'voices': {'A': voice}})


if __name__ == '__main__': unittest.main()

"""Exercise real main-loop/queue reentry without a GUI or production profile."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(sys.platform == 'darwin', 'Mac run-loop termination contract')
class CloseSchedulerTests(unittest.TestCase):
    def test_nested_termination_loop_can_receive_main_queue_reply(self):
        source = (ROOT/'native_macos/App.swift').read_text()
        helper = 'func scheduleNativeTermination(' + source.split('func scheduleNativeTermination(',1)[1].split('\n}',1)[0] + '\n}'
        harness = r'''
import Foundation
HELPER
var finished = false
DispatchQueue.main.async {
    scheduleNativeTermination {
        var replied = false
        DispatchQueue.main.async { replied = true }
        let deadline = Date().addingTimeInterval(1)
        while !replied && Date() < deadline {
            _ = RunLoop.current.run(mode: .default, before: Date().addingTimeInterval(0.01))
        }
        guard replied else { exit(9) }
        finished = true
    }
}
let deadline = Date().addingTimeInterval(3)
while !finished && Date() < deadline {
    _ = RunLoop.current.run(mode: .default, before: Date().addingTimeInterval(0.01))
}
guard finished else { exit(8) }
print("PASS: nested loop received asynchronous main-queue termination reply")
'''.replace('HELPER',helper)
        with tempfile.TemporaryDirectory(prefix='dialogue-close-scheduler-') as tmp:
            root = Path(tmp); swift = root/'main.swift'; executable = root/'check'
            swift.write_text(harness)
            built = subprocess.run(['/usr/bin/swiftc','-swift-version','5','-module-cache-path',str(ROOT/'build/test-swift-cache'),str(swift),'-o',str(executable)],capture_output=True,text=True,timeout=90)
            self.assertEqual(built.returncode,0,built.stderr)
            checked = subprocess.run([str(executable)],capture_output=True,text=True,timeout=10)
            self.assertEqual(checked.returncode,0,checked.stderr)

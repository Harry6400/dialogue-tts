"""Opt-in local-candidate transaction; never targets an existing installation.

Uses the real distribution ZIP, frozen helper, normal LaunchServices policy and
nonce receipt. The old wait PID is an isolated process, not a live user app.
Network release discovery is deliberately tested separately.
"""
from pathlib import Path
import hashlib
import json
import os
import plistlib
import secrets
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]

def main():
    if sys.platform != 'darwin':
        raise SystemExit('Run explicitly on macOS with the built candidate.')
    sys.path.insert(0, str(ROOT))
    from web_dialogue.safe_updater import extract_safe, validate_payload, sha256, mark_macos_quarantine
    from web_dialogue.updater_bridge import Updater
    from web_dialogue.verification_profile import ENV
    root = ROOT / 'verification' / ('mac-update-' + secrets.token_hex(6))
    root.mkdir(parents=True, mode=0o700)
    profile = root / 'state'; profile.mkdir(mode=0o700)
    os.environ[ENV] = str(profile)
    archive = ROOT / 'dist/DialogueTTS-macOS-arm64.zip'
    with (ROOT / 'dist/对白.app/Contents/Info.plist').open('rb') as f:
        version = plistlib.load(f)['CFBundleShortVersionString']
    report = {'scope': 'local candidate; real ZIP/helper/replacement/OS launch; isolated old wait process',
              'network_discovery': 'not exercised', 'candidate_sha256': sha256(archive), 'passed': False}
    try:
        target = root / 'installation/对白.app'; target.parent.mkdir()
        shutil.copytree(ROOT / 'dist/对白.app', target, symlinks=True)
        info = target / 'Contents/Info.plist'
        value = plistlib.loads(info.read_bytes()); value['CFBundleShortVersionString'] = '0.3.1'
        info.write_bytes(plistlib.dumps(value))
        subprocess.run(['/usr/bin/codesign', '--force', '--sign', '-', str(target)], check=True)
        validate_payload(target, '0.3.1', 'darwin')
        sentinels = {'preferences.json': b'{"voices":{"AGENT":"zh-CN-XiaoxiaoNeural","USER":"zh-CN-XiaoyiNeural"},"theme":"light","rate":1,"pause_ms":250}',
                     'retained-test.bin': b'fictional-private-record'}
        for name, data in sentinels.items(): (profile / name).write_bytes(data)
        updater = Updater(target, '0.3.1', profile)
        updater.stage = updater.root / secrets.token_hex(16); updater.stage.mkdir(mode=0o700)
        updater.payload = extract_safe(archive, updater.stage / 'extracted', 'darwin')
        validate_payload(updater.payload, version, 'darwin')
        # Apply the production internet-origin marking; never clear quarantine.
        mark_macos_quarantine(updater.payload)
        updater.offer = {'version': version}; updater.state = {'state': 'prepared'}
        old = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(10)'])
        initial = sha256(target / 'Contents/MacOS/DialogueTTS')
        result = updater.start_helper([old.pid])
        report['helper_ready'] = result['state'] == 'waiting_for_exit'
        report['waits_for_old_pid'] = old.poll() is None and initial == sha256(target / 'Contents/MacOS/DialogueTTS')
        old.wait(timeout=15)
        deadline = time.monotonic() + 120
        status = updater.stage / 'status.json'
        while not status.exists() and time.monotonic() < deadline: time.sleep(.2)
        report['transaction'] = json.loads(status.read_text()) if status.exists() else {'state': 'unknown'}
        receipt = updater.stage / 'receipt.json'
        if receipt.exists():
            ready = json.loads(receipt.read_text()); report['receipt_state'] = ready.get('state')
        report['preferences_and_records_preserved'] = all((profile / name).read_bytes() == data for name, data in sentinels.items())
        report['old_backup_retained'] = any(target.parent.glob(target.name + '.update-backup-*'))
        report['passed'] = (report['transaction'].get('state') == 'complete' and report.get('receipt_state') == 'ready'
                            and report['helper_ready'] and report['waits_for_old_pid'] and report['preferences_and_records_preserved'] and report['old_backup_retained'])
        report['private_evidence'] = str(updater.stage.relative_to(ROOT))
    except Exception as exc:
        report['error'] = type(exc).__name__ + ': ' + str(exc)
    evidence = ROOT / 'evidence/foundation/mac-upgrade.json'; evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report['passed'] else 1

if __name__ == '__main__':
    raise SystemExit(main())

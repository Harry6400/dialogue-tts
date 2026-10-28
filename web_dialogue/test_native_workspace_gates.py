"""Exercise real native gates with idle pages and non-idle global workspaces.

All updater collaborators are substitutes; no helper, installation or network
service is launched. Swift checks compile only the pure native safety merger.
"""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from native_close import close_decision, merge_close_safety, workspace_safety
from native_windows_updater import NativeUpdaterMixin
from windows_launcher import NativeDownloads

ROOT = Path(__file__).resolve().parents[1]
PAGE_IDLE = dict(running=False, saving=False, pending=False, unsaved=False, persistent_workspace=True)
GLOBAL_IDLE = dict(busy=False, unsaved=False, saving=False, pending=False)


class ObservedLock:
    def __init__(self): self.held = False
    def __enter__(self): self.held = True
    def __exit__(self, *args): self.held = False


class NativeWorkspaceGateTests(unittest.TestCase):
    def server(self, state):
        lock = ObservedLock()
        def safety():
            if isinstance(state, Exception): raise state
            return state
        return SimpleNamespace(update_lock=lock, update_in_progress=False,
            workspaces=SimpleNamespace(safety=Mock(side_effect=safety)),
            jobs=SimpleNamespace(transaction=threading.RLock, has_running=Mock(return_value=False)),
            batches=SimpleNamespace(transaction=threading.RLock, has_running=Mock(return_value=False)))

    def test_windows_updater_checks_global_state_under_update_lock_before_any_helper(self):
        for field in GLOBAL_IDLE:
            with self.subTest(field=field):
                server = self.server({**GLOBAL_IDLE, field: True})
                def checked():
                    self.assertTrue(server.update_lock.held)
                    return {**GLOBAL_IDLE, field: True}
                server.workspaces.safety.side_effect = checked
                bridge = NativeUpdaterMixin()
                bridge._update_server = server
                bridge._lock = threading.Lock(); bridge._folder_lock = threading.RLock(); bridge._update_gate = threading.Lock()
                bridge._updater = SimpleNamespace(offer={'version': '0.4.0'}, state={}, prepare_confirmed=Mock(), start_helper=Mock())
                bridge._window = SimpleNamespace(evaluate_js=Mock(return_value={'busy': False, 'unsaved': False}), create_confirmation_dialog=Mock(return_value=True), destroy=Mock())
                result = bridge.install_update()
                self.assertEqual(result['state'], 'error')
                self.assertIn('后台工作区', result['message'])
                server.workspaces.safety.assert_called_once()
                bridge._updater.prepare_confirmed.assert_not_called(); bridge._updater.start_helper.assert_not_called()
                bridge._window.create_confirmation_dialog.assert_not_called()
                self.assertFalse(server.update_in_progress)

    def test_windows_updater_unknown_global_state_never_starts_helper(self):
        for state in (None, {}, {'busy': False, 'unsaved': False}, {**GLOBAL_IDLE, 'saving': 0}, OSError('unavailable')):
            with self.subTest(state=state):
                bridge = NativeUpdaterMixin(); bridge._update_server = self.server(state)
                bridge._lock = threading.Lock(); bridge._folder_lock = threading.RLock(); bridge._update_gate = threading.Lock()
                bridge._updater = SimpleNamespace(offer={'version': '0.4.0'}, state={}, prepare_confirmed=Mock(), start_helper=Mock())
                bridge._window = SimpleNamespace(evaluate_js=Mock(return_value={'busy': False, 'unsaved': False}), create_confirmation_dialog=Mock(), destroy=Mock())
                self.assertEqual(bridge.install_update()['state'], 'error')
                bridge._updater.start_helper.assert_not_called()

    def test_actual_windows_close_snapshot_includes_other_workspace_and_keeps_cache(self):
        with tempfile.TemporaryDirectory() as temp:
            downloads = NativeDownloads('http://127.0.0.1:1', Path(temp) / 'preferences.json')
            downloads._update_server = self.server({**GLOBAL_IDLE, 'busy': True, 'unsaved': True})
            state = downloads._close_snapshot(PAGE_IDLE)
            self.assertTrue(state['running']); self.assertTrue(state['persistent_unsaved']); self.assertFalse(state['unsaved'])
            prompts = []
            self.assertEqual(close_decision(state, prompt=lambda kind: prompts.append(kind) or 'preserve'), 'exit')
            self.assertEqual(prompts, ['persistent'])
            self.assertEqual(close_decision(state, prompt=lambda kind: 'cancel'), 'cancel')
            # A real legacy manual result retains its separate return-save/discard gate.
            legacy = downloads._close_snapshot({**PAGE_IDLE, 'unsaved': True})
            self.assertEqual(close_decision(legacy, prompt=lambda kind: 'save' if kind == 'unsaved' else 'preserve'), 'save')

    def test_saving_pending_unknown_and_background_worker_block_or_prompt(self):
        for field in ('saving', 'pending'):
            state = merge_close_safety(PAGE_IDLE, {**GLOBAL_IDLE, field: True})
            prompts = []
            self.assertEqual(close_decision(state, prompt=lambda kind: prompts.append(kind) or 'preserve'), 'cancel')
            self.assertEqual(prompts, ['saving'])
        self.assertIsNone(workspace_safety(self.server(OSError('unreadable'))))
        self.assertIsNone(merge_close_safety(PAGE_IDLE, {'busy': False}))
        with tempfile.TemporaryDirectory() as temp:
            downloads = NativeDownloads('http://127.0.0.1:1', Path(temp) / 'preferences.json')
            downloads._update_server = self.server(GLOBAL_IDLE)
            downloads._update_server.jobs.has_running.return_value=True
            self.assertTrue(downloads._close_snapshot(PAGE_IDLE)['running'])

    def test_missing_service_or_work_pool_protocol_never_looks_idle(self):
        with tempfile.TemporaryDirectory() as temp:
            downloads=NativeDownloads('http://127.0.0.1:1',Path(temp)/'preferences.json')
            self.assertIsNone(downloads._close_snapshot(PAGE_IDLE))
            downloads._update_server=self.server(GLOBAL_IDLE)
            downloads._update_server.jobs=SimpleNamespace()
            self.assertIsNone(downloads._close_snapshot(PAGE_IDLE))
            downloads._update_server=self.server(GLOBAL_IDLE)
            downloads._update_server.jobs.has_running.return_value=0
            self.assertIsNone(downloads._close_snapshot(PAGE_IDLE))
            downloads._close_folders()


@unittest.skipUnless(sys.platform == 'darwin', 'Compile the actual Swift global safety merger on macOS')
class MacGlobalSafetyTests(unittest.TestCase):
    def test_native_page_and_backend_merger_fails_closed_and_preserves_cache(self):
        source = (ROOT / 'native_macos' / 'App.swift').read_text().split('final class AppDelegate:')[0]
        harness = r'''
let page: [String: Any] = ["running":false,"saving":false,"pending":false,"unsaved":false]
let idle: [String: Any] = ["busy":false,"saving":false,"pending":false,"unsaved":false]
var backend = idle; backend["busy"] = true; backend["unsaved"] = true
let merged = mergeNativeCloseSafety(page, backend)!
assert(merged["running"] as? Bool == true)
assert(merged["persistent_unsaved"] as? Bool == true)
assert(merged["unsaved"] as? Bool == false)
var legacy = page; legacy["unsaved"] = true
assert(mergeNativeCloseSafety(legacy, backend)!["unsaved"] as? Bool == true)
var saving = idle; saving["saving"] = true
assert(mergeNativeCloseSafety(page, saving)!["saving"] as? Bool == true)
var pending = idle; pending["pending"] = true
assert(mergeNativeCloseSafety(page, pending)!["pending"] as? Bool == true)
assert(mergeNativeCloseSafety(page, nil) == nil)
assert(mergeNativeCloseSafety(page, ["busy":false]) == nil)
let malformed = try! JSONSerialization.jsonObject(with: Data("{\"busy\":false,\"saving\":0,\"pending\":false,\"unsaved\":false}".utf8)) as! [String:Any]
assert(mergeNativeCloseSafety(page, malformed) == nil)
var legacyCache = idle; legacyCache["legacy_unsaved"] = true
let legacyMerged = mergeNativeCloseSafety(page, legacyCache)!
assert(legacyMerged["unsaved"] as? Bool == true)
assert(legacyMerged["persistent_unsaved"] as? Bool == false)
legacyCache["legacy_unsaved"] = "unknown"
assert(mergeNativeCloseSafety(page, legacyCache) == nil)
print("PASS: global workspace safety and legacy manual protection merged fail-closed")
'''
        with tempfile.TemporaryDirectory(prefix='dialogue-safety-swift-') as temp:
            root = Path(temp); script = root / 'main.swift'; script.write_text(source + harness)
            compiled = subprocess.run(['/usr/bin/swiftc', '-swift-version', '5', '-module-cache-path', str(ROOT / 'build/test-swift-cache'), str(script), '-o', str(root / 'check')], capture_output=True, text=True, timeout=120)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            checked = subprocess.run([str(root / 'check')], capture_output=True, text=True, timeout=10)
            self.assertEqual(checked.returncode, 0, checked.stderr)
            self.assertIn('PASS:', checked.stdout)

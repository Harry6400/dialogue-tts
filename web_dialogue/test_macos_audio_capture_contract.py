"""Compile real Swift capture-response and theme-only contracts, without GUI."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(sys.platform == 'darwin', 'Mac native capture contract')
class MacAudioCaptureContractTests(unittest.TestCase):
    def test_active_download_and_bridge_routes_share_capture_ledger(self):
        source=(ROOT/'native_macos/App.swift').read_text()
        # Retain the historical JSON adapter for migration regressions, but
        # reject a production call site reconnecting it to a native entry point.
        self.assertEqual(source.count('performAudioSave('),1)
        adapter_start=source.index('    private func performAudioSave(')
        adapter_end=source.index('\n    private func ',adapter_start+1)
        active=source[:adapter_start]+source[adapter_end:]
        self.assertNotIn('folderState.save(',active)
        destination=source[source.index('    func download(_ download: WKDownload, decideDestinationUsing'):source.index('    func downloadDidFinish(')]
        self.assertIn('trustedNativeAudioMetadata(response)',destination)
        self.assertIn('/api/native/audio-captures/reserve',destination)
        self.assertIn('completionHandler(captureURL)',destination)
        finished=source[source.index('    func downloadDidFinish('):source.index('    func download(_ download: WKDownload, didFailWithError')]
        self.assertIn('/api/native/audio-captures/complete',finished)
        self.assertIn('saveCapturedAudio(captureID',finished)
        saving=source[source.index('    private func saveCapturedAudio('):source.index('    private func chooseCapturedAudioDestination(')]
        self.assertIn('/api/native/audio-captures/save',saving)
        self.assertNotIn('URLSession',saving)

    def test_trusted_response_metadata_and_theme_only_settings(self):
        source = (ROOT / 'native_macos/App.swift').read_text().split('final class AppDelegate:')[0]
        harness = r'''
let root = URL(fileURLWithPath: CommandLine.arguments[1], isDirectory: true)
let path = "/api/jobs/" + String(repeating:"a",count:32) + "/audio"
let headers = ["Content-Type":"audio/mpeg", "Content-Length":"3312", "Content-Disposition":"attachment; filename=\"000001.mp3\"", "X-Dialogue-Fingerprint":String(repeating:"b",count:64)]
func metadata(_ modifications: [String:String] = [:], urlPath: String? = nil, status: Int = 200) -> [String:Any]? {
    let response = HTTPURLResponse(url:URL(string:"http://127.0.0.1:1234" + (urlPath ?? path))!, statusCode:status, httpVersion:"HTTP/1.1", headerFields:headers.merging(modifications){_,new in new})!
    return trustedNativeAudioMetadata(response)
}
let valid = metadata()!
assert(valid["resource_path"] as? String == path)
assert(valid["filename"] as? String == "000001.mp3")
assert(valid["size"] as? Int64 == 3312)
assert(metadata(["Content-Type":"application/zip"]) == nil)
assert(metadata(["Content-Length":"0"]) == nil)
assert(metadata(["Content-Length":"268435457"]) == nil)
assert(metadata(["X-Dialogue-Fingerprint":"untrusted"]) == nil)
assert(metadata(["Content-Disposition":"attachment; filename=\"../000001.mp3\""]) == nil)
assert(metadata(["Content-Disposition":"attachment; filename=\"custom.mp3\""]) == nil)
assert(metadata(urlPath:path + "?source_path=/private/escape") == nil)
assert(metadata(urlPath:"/api/config") == nil)
assert(metadata(status:404) == nil)
let state = FolderState(storeOverride:root.appendingPathComponent("preferences.json"))
let sound: [String:Any] = ["voices":["AGENT":"zh-CN-YunxiaNeural","USER":"zh-CN-YunyangNeural"],"rate":1.35,"pause_ms":650,"theme":"light","auto_voice":false,"manual_user_voice":true]
let configured = try state.setPreferences(sound)
assert(configured)
let changed = try state.setApplicationPreferences(["theme":"dark"])
assert(changed)
let preferences = state.preferences()
assert(preferences["rate"] as? Double == 1.35)
assert(preferences["pause_ms"] as? Int == 650)
assert((preferences["voices"] as? [String:String])?["USER"] == "zh-CN-YunyangNeural")
assert(preferences["auto_voice"] as? Bool == false)
assert(preferences["theme"] as? String == "dark")
let invalidChange = try state.setApplicationPreferences(["theme":"light","rate":1.0])
assert(!invalidChange)
enum WriteFailure: Error {case unavailable}
let blocked = FolderState(storeOverride:root.appendingPathComponent("blocked.json"),persistHook:{throw WriteFailure.unavailable})
do { _ = try blocked.setApplicationPreferences(["theme":"dark"]); fatalError("expected failure") } catch WriteFailure.unavailable {}
assert(blocked.preferences()["theme"] as? String == "light")
print("PASS: trusted response, no arbitrary source path, theme-only preservation and rollback")
'''
        with tempfile.TemporaryDirectory(prefix='dialogue-capture-swift-') as temp:
            root = Path(temp); swift = root / 'main.swift'; swift.write_text(source + harness)
            compiled = subprocess.run(['/usr/bin/swiftc', '-swift-version', '5', '-module-cache-path', str(ROOT / 'build/test-swift-cache'), str(swift), '-o', str(root / 'check')], capture_output=True, text=True, timeout=120)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = subprocess.run([str(root / 'check'), str(root / 'state')], capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)

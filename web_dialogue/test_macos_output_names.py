"""Compile the real native save implementation; exercise only isolated files."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(sys.platform == 'darwin', 'Mac native filesystem contract')
class MacNameTests(unittest.TestCase):
    def test_atomic_name_conflict_recovery_and_expected_names(self):
        source = (ROOT/'native_macos/App.swift').read_text().split('final class AppDelegate:')[0]
        harness = r'''
let root = URL(fileURLWithPath: CommandLine.arguments[1], isDirectory: true)
let output = root.appendingPathComponent("output", isDirectory: true)
try FileManager.default.createDirectory(at: output, withIntermediateDirectories: true)
let store = root.appendingPathComponent("preferences.json")
let registry = SavedDownloadRegistry()
let state = FolderState(storeOverride: store)
let data = Data("fictional audio bytes".utf8)
let fp = String(repeating: "a", count: 64)
let route = "/api/jobs/" + String(repeating: "b", count: 32) + "/audio"
let first = try state.save(data, route: route, fingerprint: fp, filename: "000123.mp3", directory: output, registry: registry)
assert(first["name"] as? String == "000123.mp3")
let again = try state.save(data, route: route + "-new", fingerprint: fp, filename: "000123.mp3", directory: output, registry: registry)
assert(again["path"] as? String == first["path"] as? String)
assert(state.completed([fp], directory: output, registry: registry, expectedNames: [fp:"000124.mp3"]).isEmpty)
do {
    _ = try state.save(data, route: route, fingerprint: String(repeating:"c",count:64), filename:"000123.mp3", directory:output,registry:registry)
    fatalError("A conflicting file was replaced")
} catch SelectedXMLFolderError.nameConflict {}
let firstData = try Data(contentsOf:output.appendingPathComponent("000123.mp3")); assert(firstData == data)
let raced = FolderState(storeOverride:root.appendingPathComponent("raced.json"),beforeLinkHook:{
    try Data("competing writer".utf8).write(to:output.appendingPathComponent("777.mp3"))
})
do {
    _ = try raced.save(data,route:route,fingerprint:fp,filename:"777.mp3",directory:output,registry:registry)
    fatalError("Race silently overwrote a file")
} catch SelectedXMLFolderError.nameConflict {}
let racedData = try Data(contentsOf:output.appendingPathComponent("777.mp3")); assert(racedData == Data("competing writer".utf8))
enum Interrupted: Error { case afterLink }
let interruptedStore=root.appendingPathComponent("interrupted.json")
let interrupted=FolderState(storeOverride:interruptedStore,afterLinkHook:{throw Interrupted.afterLink})
do {
    _ = try interrupted.save(data,route:route,fingerprint:fp,filename:"888.mp3",directory:output,registry:registry)
    fatalError("Expected injected interruption")
} catch Interrupted.afterLink {}
let recovered=FolderState(storeOverride:interruptedStore)
assert(recovered.completed([fp],directory:output,registry:registry,expectedNames:[fp:"888.mp3"]).count == 1)
try Data("modified".utf8).write(to:output.appendingPathComponent("888.mp3"))
let completed=recovered.completed([fp],directory:output,registry:registry,expectedNames:[fp:"888.mp3"])
assert(completed.isEmpty)
assert(recovered.conflicts([fp:"888.mp3"],completed:completed,directory:output).count == 1)
let finalNames = try FileManager.default.contentsOfDirectory(atPath:output.path); assert(finalNames.filter{$0.hasSuffix(".mp3")}.count == 3)
print("PASS: native fixed names, idempotence, conflicts, race and interrupted receipt recovery")
'''
        with tempfile.TemporaryDirectory(prefix='dialogue-native-names-') as tmp:
            folder = Path(tmp)
            swift = folder/'main.swift'; swift.write_text(source+harness)
            executable = folder/'native-names'
            compiled = subprocess.run(['/usr/bin/swiftc','-swift-version','5','-module-cache-path',str(ROOT/'build'/'test-swift-cache'),str(swift),'-o',str(executable)],capture_output=True,text=True,timeout=120)
            self.assertEqual(compiled.returncode,0,compiled.stderr)
            checked = subprocess.run([str(executable),str(folder/'state')],capture_output=True,text=True,timeout=15)
            self.assertEqual(checked.returncode,0,checked.stderr)
            self.assertIn('PASS:',checked.stdout)

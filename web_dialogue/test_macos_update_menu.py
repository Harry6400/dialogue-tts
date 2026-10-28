"""Exercise the exact menu-dispatch JavaScript without a GUI or any network."""
import json
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('node'), 'Menu dispatch contract requires the project Node runtime')
class MacUpdateMenuTests(unittest.TestCase):
    def test_menu_prefers_shared_react_check_and_preserves_legacy_disabled_state(self):
        source = (ROOT / 'native_macos/App.swift').read_text(encoding='utf-8')
        script = source.split('let updateMenuCheckScript = """', 1)[1].split('"""', 1)[0]
        harness = r'''
const assert = require('node:assert/strict');
const script = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
const dispatch = new Function('window', 'document', 'return ' + script.trim());
let reactCalls = 0, legacyCalls = 0;
const active = {disabled:false, click:() => legacyCalls++};
const documentFor = button => ({getElementById:id => {assert.equal(id,'checkupdate'); return button;}});
assert.equal(dispatch({dialogueCheckForUpdates:() => reactCalls++}, documentFor(active)), 'react');
assert.equal(reactCalls, 1); assert.equal(legacyCalls, 0);
assert.equal(dispatch({}, documentFor(active)), 'legacy');
assert.equal(legacyCalls, 1);
assert.equal(dispatch({}, documentFor({...active, disabled:true})), 'busy');
assert.equal(legacyCalls, 1);
assert.equal(dispatch({}, documentFor(null)), 'unavailable');
assert.throws(() => dispatch({dialogueCheckForUpdates:() => {throw new Error('not ready');}}, documentFor(active)), /not ready/);
assert.equal(legacyCalls, 1);
'''
        result = subprocess.run([shutil.which('node'), '-e', harness], input=json.dumps(script), text=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)

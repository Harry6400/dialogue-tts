// Existing DOM regressions use only fictional inputs and test doubles.
const fs = require('node:fs');
const path = require('node:path');
const {spawnSync} = require('node:child_process');
const root = path.resolve(__dirname, '..');
for (const file of fs.readdirSync(__dirname).filter(name => /^test_.*\.cjs$/.test(name)).sort()) {
  process.stdout.write(`\n[legacy UI] ${file}\n`);
  const result = spawnSync(process.execPath, [path.join(__dirname, file)], {cwd: root, stdio: 'inherit', timeout: 30000});
  if (result.error || result.status !== 0) {if (result.error) console.error(result.error.message); process.exit(result.status || 1);}
}

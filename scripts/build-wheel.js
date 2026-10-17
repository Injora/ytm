'use strict';

// Build the PixelFM wheel into python/ (runs automatically before `npm pack`).
const fs = require('node:fs');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const { findPython, cleanEnv } = require('../lib/launcher.js');

const root = path.resolve(__dirname, '..');
const out = path.join(root, 'python');
const pkg = JSON.parse(fs.readFileSync(path.join(root, 'package.json'), 'utf8'));
const pyproject = fs.readFileSync(path.join(root, 'pyproject.toml'), 'utf8');
const pyVersion = (pyproject.match(/^version = "([^"]+)"/m) || [])[1];
if (pyVersion !== pkg.version) {
  console.error(`package.json version ${pkg.version} != pyproject.toml version ${pyVersion}`);
  process.exit(1);
}
const python = findPython(process.env);
if (python.error) {
  console.error('No Python 3.12+ found to build the wheel.');
  process.exit(1);
}
for (const name of fs.readdirSync(out)) {
  if (name.endsWith('.whl')) fs.rmSync(path.join(out, name));
}
const result = spawnSync(python.path, ['-m', 'pip', 'wheel', '.', '--no-deps', '--quiet', '-w', out], {
  cwd: root,
  env: cleanEnv(process.env),
  stdio: 'inherit',
});
if (result.status !== 0) process.exit(result.status || 1);
const wheels = fs.readdirSync(out).filter((n) => n.endsWith('.whl'));
if (wheels.length !== 1 || !wheels[0].startsWith(`pixelfm-${pkg.version}-`)) {
  console.error(`unexpected wheel output: ${wheels.join(', ')}`);
  process.exit(1);
}
console.log(`built python/${wheels[0]}`);

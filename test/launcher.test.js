'use strict';

// Launcher tests. They never touch the network or the development .venv:
// interpreters and runtimes are small shell scripts in temp folders.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');
const { spawn, spawnSync } = require('node:child_process');

const launcher = require('../lib/launcher.js');

const ROOT = path.resolve(__dirname, '..');
const BIN = path.join(ROOT, 'bin', 'ytm.js');

function tempDir(label = 'pixelfm test') {
  // Spaces on purpose: every path must survive them.
  return fs.mkdtempSync(path.join(os.tmpdir(), `${label} `));
}

function script(dir, name, body) {
  const file = path.join(dir, name);
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(file, `#!/bin/sh\n${body}\n`);
  fs.chmodSync(file, 0o755);
  return file;
}

/** A runtime folder whose "python" is a script, marked as up to date. */
function fakeRuntime(body) {
  const dir = path.join(tempDir('pixelfm runtime'), 'runtime dir');
  script(path.join(dir, 'bin'), 'python', body);
  fs.writeFileSync(
    path.join(dir, 'pixelfm-runtime.json'),
    JSON.stringify({ fingerprint: launcher.fingerprint(), python: '3.13.0', app: 'PixelFM test' }),
  );
  return dir;
}

function baseEnv(extra = {}) {
  return { PATH: '/usr/bin:/bin', HOME: tempDir('pixelfm home'), ...extra };
}

test('version check', () => {
  assert.ok(launcher.versionOk('3.12.0'));
  assert.ok(launcher.versionOk('3.14.1'));
  assert.ok(launcher.versionOk('4.0.0'));
  assert.ok(!launcher.versionOk('3.11.9'));
});

test('runtime folder per platform, with overrides', () => {
  assert.equal(
    launcher.runtimeDir({}, 'darwin', '/Users/a b'),
    '/Users/a b/Library/Application Support/PixelFM/runtime',
  );
  assert.equal(launcher.runtimeDir({}, 'linux', '/home/a'), '/home/a/.local/share/pixelfm/runtime');
  assert.equal(launcher.runtimeDir({ XDG_DATA_HOME: '/data' }, 'linux', '/h'), '/data/pixelfm/runtime');
  assert.equal(launcher.runtimeDir({ PIXELFM_RUNTIME_DIR: '/x y/rt' }, 'linux', '/h'), '/x y/rt');
});

test('finds the newest suitable Python and explains rejects', () => {
  const bin = tempDir('fake bin');
  script(bin, 'python3', 'echo 3.11.4');
  script(bin, 'python3.13', 'echo 3.13.2');
  const found = launcher.findPython({ PATH: bin });
  assert.equal(found.path, path.join(bin, 'python3.13'));
  assert.equal(found.version, '3.13.2');

  fs.rmSync(path.join(bin, 'python3.13'));
  const rejected = launcher.findPython({ PATH: bin });
  assert.ok(rejected.error);
  assert.match(rejected.tried.join('\n'), /3\.11\.4 is too old/);
});

test('reports a Python without venv support', () => {
  const bin = tempDir('fake bin');
  script(bin, 'python3', 'echo "ModuleNotFoundError: No module named \'ensurepip\'" >&2; exit 1');
  const result = launcher.findPython({ PATH: bin });
  assert.ok(result.error);
  assert.match(result.tried[0], /python3-venv/);
});

test('PIXELFM_PYTHON overrides discovery', () => {
  const bin = tempDir('fake bin');
  const custom = script(tempDir('custom py'), 'my python', 'echo 3.12.9');
  script(bin, 'python3.13', 'echo 3.13.0');
  const found = launcher.findPython({ PATH: bin, PIXELFM_PYTHON: custom });
  assert.equal(found.path, custom);
});

test('child environment is isolated from the user Python setup', () => {
  const env = launcher.cleanEnv({ PYTHONPATH: '/evil', PYTHONHOME: '/x', VIRTUAL_ENV: '/dev/.venv', KEEP: '1' });
  assert.equal(env.PYTHONPATH, undefined);
  assert.equal(env.PYTHONHOME, undefined);
  assert.equal(env.VIRTUAL_ENV, undefined);
  assert.equal(env.PYTHONNOUSERSITE, '1');
  assert.equal(env.KEEP, '1');
});

test('missing Python: clear message and exit code 1 (no dev venv used)', () => {
  const env = baseEnv({ PATH: tempDir('empty bin'), PIXELFM_RUNTIME_DIR: path.join(tempDir(), 'rt') });
  const result = spawnSync(process.execPath, [BIN], { env, encoding: 'utf8', cwd: os.tmpdir() });
  assert.equal(result.status, 1);
  assert.match(result.stderr, /needs Python 3\.12 or newer/);
  assert.match(result.stderr, /brew install python@3\.13/);
  assert.match(result.stderr, /apt install python3\.12 python3\.12-venv/);
});

test('arguments, exit codes, and environment pass through', () => {
  const out = path.join(tempDir('out'), 'args.txt');
  const runtime = fakeRuntime(
    `printf '%s\\n' "$@" > "${out}"; echo "launcher=$PIXELFM_LAUNCHER pythonpath=$PYTHONPATH" >> "${out}"; exit 7`,
  );
  const env = baseEnv({ PIXELFM_RUNTIME_DIR: runtime, PYTHONPATH: '/should/not/leak' });
  const result = spawnSync(process.execPath, [BIN, '--art', 'glyph', 'two words'], {
    env,
    encoding: 'utf8',
    cwd: os.tmpdir(),
  });
  assert.equal(result.status, 7);
  const lines = fs.readFileSync(out, 'utf8').trim().split('\n');
  assert.deepEqual(lines.slice(0, 4), ['-m', 'pixelfm', '--art', 'glyph']);
  assert.equal(lines[4], 'two words');
  assert.equal(lines[5], 'launcher=npm pythonpath=');
});

function startLauncher(runtime) {
  return spawn(process.execPath, [BIN], {
    env: baseEnv({ PIXELFM_RUNTIME_DIR: runtime }),
    cwd: os.tmpdir(),
    stdio: ['ignore', 'pipe', 'pipe'],
  });
}

function waitForFile(file, ms = 5000) {
  const deadline = Date.now() + ms;
  return new Promise((resolve, reject) => {
    const tick = () => {
      if (fs.existsSync(file)) return resolve();
      if (Date.now() > deadline) return reject(new Error(`timeout waiting for ${file}`));
      setTimeout(tick, 25);
    };
    tick();
  });
}

for (const signal of ['SIGTERM', 'SIGINT', 'SIGHUP']) {
  test(`${signal} is forwarded to PixelFM, which exits cleanly`, async () => {
    const dir = tempDir('sig');
    const ready = path.join(dir, 'ready');
    const got = path.join(dir, 'got');
    const runtime = fakeRuntime(
      `trap 'echo ${signal} > "${got}"; exit 0' ${signal.replace('SIG', '')}\n` +
        `touch "${ready}"\nwhile true; do sleep 0.05; done`,
    );
    const child = startLauncher(runtime);
    await waitForFile(ready);
    process.kill(child.pid, signal);
    const [code] = await new Promise((resolve) => child.on('exit', (c, s) => resolve([c, s])));
    assert.equal(code, 0);
    assert.equal(fs.readFileSync(got, 'utf8').trim(), signal);
  });
}

test('a child killed by a signal makes the launcher exit the same way', async () => {
  const runtime = fakeRuntime('kill -TERM $$');
  const child = startLauncher(runtime);
  const [code, signal] = await new Promise((resolve) => child.on('exit', (c, s) => resolve([c, s])));
  assert.equal(code, null);
  assert.equal(signal, 'SIGTERM');
});

test('a setup that cannot find Python leaves existing folders untouched', () => {
  const base = tempDir('stale');
  const runtime = path.join(base, 'runtime');
  const stale = `${runtime}.tmp-999999`; // no such process
  fs.mkdirSync(stale, { recursive: true });
  const env = baseEnv({ PATH: tempDir('empty'), PIXELFM_RUNTIME_DIR: runtime });
  // Setup fails (no Python) but must clean up stale build folders first? It
  // fails before touching the filesystem, so the stale folder survives here...
  assert.throws(() => launcher.ensureRuntime({ env, log: () => {} }), launcher.LauncherError);
  // ...and is removed by the next real setup attempt (see the bootstrap test).
  assert.ok(fs.existsSync(stale));
});

test('--runtime-info works before any setup', () => {
  const env = baseEnv({ PIXELFM_RUNTIME_DIR: path.join(tempDir(), 'rt') });
  const result = spawnSync(process.execPath, [BIN, '--runtime-info'], { env, encoding: 'utf8' });
  assert.equal(result.status, 0);
  assert.match(result.stdout, /not set up yet/);
});

test(
  'real first-run setup in a fresh folder (opt-in: PIXELFM_TEST_BOOTSTRAP=1, needs internet)',
  { skip: process.env.PIXELFM_TEST_BOOTSTRAP !== '1' },
  () => {
    const base = tempDir('bootstrap');
    const runtime = path.join(base, 'Application Support', 'runtime');
    fs.mkdirSync(`${runtime}.tmp-999999`, { recursive: true }); // stale leftover
    const env = { ...process.env, PIXELFM_RUNTIME_DIR: runtime, HOME: base };
    delete env.VIRTUAL_ENV;
    const result = spawnSync(process.execPath, [BIN, '--version'], { env, encoding: 'utf8' });
    assert.equal(result.status, 0, result.stderr);
    const { version } = require('../package.json');
    assert.equal(result.stdout.trim(), `PixelFM ${version}`);
    assert.ok(!fs.existsSync(`${runtime}.tmp-999999`));
    // Second run reuses the environment (no setup messages).
    const again = spawnSync(process.execPath, [BIN, '--version'], { env, encoding: 'utf8' });
    assert.equal(again.status, 0);
    assert.doesNotMatch(again.stderr, /setting up/);
  },
);

'use strict';

// System tools (FFmpeg, Tesseract on Linux): what is checked per OS, which
// package manager installs it, and that nothing is installed without a yes.
// Package managers here are fake scripts; nothing is really installed.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');
const { spawnSync } = require('node:child_process');

const deps = require('../lib/system-deps.js');

const BIN = path.join(__dirname, '..', 'bin', 'pixelfm.js');

function tempDir(label) {
  return fs.mkdtempSync(path.join(os.tmpdir(), `${label} `));
}

function tool(dir, name) {
  fs.mkdirSync(dir, { recursive: true });
  const file = path.join(dir, name);
  fs.writeFileSync(file, '#!/bin/sh\nexit 0\n');
  fs.chmodSync(file, 0o755);
  return file;
}

/** A PATH with ``names`` as fake executables. */
function fakePath(...names) {
  const dir = tempDir('pixelfm bin');
  for (const name of names) tool(dir, name);
  return dir;
}

function quietLog() {
  const lines = [];
  return { lines, log: (message) => lines.push(message) };
}

test('each OS checks its own tools', () => {
  assert.deepEqual(deps.toolsFor('darwin').map((t) => t.id), ['ffmpeg']); // Vision is built in
  assert.deepEqual(deps.toolsFor('linux').map((t) => t.id), ['ffmpeg', 'tesseract']);
  assert.deepEqual(deps.toolsFor('win32'), []); // Windows has its own text recognition
  const env = { PATH: fakePath('ffplay') };
  assert.deepEqual(deps.missingTools(env, 'linux').map((t) => t.id), ['tesseract']);
  assert.deepEqual(deps.missingTools(env, 'darwin'), []);
});

test('the package manager follows the OS', () => {
  const linux = { PATH: fakePath('dnf', 'sudo') };
  const dnf = deps.findManager(linux, 'linux');
  assert.equal(dnf.name, 'dnf');
  assert.equal(
    deps.commandText(deps.installCommand(dnf, deps.toolsFor('linux'), { root: false, env: linux })),
    'sudo dnf install -y ffmpeg-free tesseract tesseract-langpack-eng',
  );
  const apt = deps.findManager({ PATH: fakePath('apt-get') }, 'linux');
  assert.equal(
    deps.commandText(deps.installCommand(apt, deps.toolsFor('linux'), { root: true })),
    'apt-get install -y ffmpeg tesseract-ocr', // root: no sudo
  );
  // Without sudo (and not root) nothing can be installed automatically.
  assert.equal(deps.installCommand(apt, deps.toolsFor('linux'), { root: false, env: { PATH: '' } }), null);
  const mac = { PATH: fakePath('brew') };
  const brew = deps.findManager(mac, 'darwin');
  assert.equal(brew.name, 'brew');
  assert.equal(deps.commandText(deps.installCommand(brew, deps.toolsFor('darwin'))), 'brew install ffmpeg');
  assert.equal(deps.findManager({ PATH: fakePath('brew') }, 'linux'), null);
});

test('asks first, then installs, then checks again', () => {
  const bin = fakePath('apt-get');
  const env = { PATH: bin };
  const asked = [];
  const ran = [];
  const { lines, log } = quietLog();
  const result = deps.ensureSystemTools({
    env,
    platform: 'linux',
    interactive: true,
    root: true,
    log,
    ask: (question) => {
      asked.push(question);
      return true;
    },
    run: (command) => {
      ran.push(deps.commandText(command));
      tool(bin, 'ffplay'); // "installed"
      tool(bin, 'tesseract');
      return true;
    },
  });
  assert.equal(asked.length, 1);
  assert.match(asked[0], /FFmpeg \(audio playback\) and Tesseract \(reading songs from screenshots\)/);
  assert.match(asked[0], /`apt-get install -y ffmpeg tesseract-ocr`\? \[Y\/n\]/);
  assert.deepEqual(ran, ['apt-get install -y ffmpeg tesseract-ocr']);
  assert.deepEqual(result, { missing: [], installed: ['ffmpeg', 'tesseract'] });
  assert.match(lines.at(-1), /installed FFmpeg and Tesseract/);
});

test('apt lists are refreshed when the first try fails', () => {
  const bin = fakePath('apt-get');
  const ran = [];
  deps.ensureSystemTools({
    env: { PATH: bin },
    platform: 'linux',
    interactive: true,
    root: true,
    log: () => {},
    ask: () => true,
    run: (command) => {
      ran.push(deps.commandText(command));
      if (ran.length === 1) return false; // stale package lists
      if (command.includes('install')) tool(bin, 'ffplay') && tool(bin, 'tesseract');
      return true;
    },
  });
  assert.deepEqual(ran, [
    'apt-get install -y ffmpeg tesseract-ocr',
    'apt-get update',
    'apt-get install -y ffmpeg tesseract-ocr',
  ]);
});

test('a no is remembered for this version; --install-deps asks again', () => {
  const stateFile = path.join(tempDir('pixelfm state'), 'system-deps.json');
  const env = { PATH: fakePath('brew') };
  let asked = 0;
  const options = {
    env,
    platform: 'darwin',
    interactive: true,
    stateFile,
    version: '2.3.0',
    log: () => {},
    ask: () => {
      asked += 1;
      return false;
    },
    run: () => assert.fail('nothing is installed after a no'),
  };
  assert.deepEqual(deps.ensureSystemTools(options).missing, ['ffmpeg']);
  deps.ensureSystemTools(options);
  assert.equal(asked, 1); // not asked again on the next start
  deps.ensureSystemTools({ ...options, force: true });
  assert.equal(asked, 2);
  deps.ensureSystemTools({ ...options, version: '2.4.0' });
  assert.equal(asked, 3); // a new PixelFM version asks once more
});

test('without a terminal it only says what to run', () => {
  const { lines, log } = quietLog();
  const result = deps.ensureSystemTools({
    env: { PATH: fakePath('pacman', 'sudo') },
    platform: 'linux',
    interactive: false,
    root: false,
    log,
    ask: () => assert.fail('no questions without a terminal'),
    run: () => assert.fail('nothing is installed without a terminal'),
  });
  assert.deepEqual(result.missing, ['ffmpeg', 'tesseract']);
  assert.match(lines[0], /sudo pacman -S --needed --noconfirm ffmpeg tesseract tesseract-data-eng/);
  const opted = quietLog();
  deps.ensureSystemTools({
    env: { PATH: fakePath('brew'), PIXELFM_NO_INSTALL: '1' },
    platform: 'darwin',
    interactive: true,
    log: opted.log,
    ask: () => assert.fail('PIXELFM_NO_INSTALL=1 never asks'),
  });
  assert.match(opted.lines[0], /brew install ffmpeg/);
});

test('without a package manager it explains what to do by hand', () => {
  const { lines, log } = quietLog();
  const result = deps.ensureSystemTools({
    env: { PATH: '' },
    platform: 'linux',
    interactive: true,
    log,
    ask: () => assert.fail('nothing to offer'),
  });
  assert.deepEqual(result.missing, ['ffmpeg', 'tesseract']);
  assert.match(lines[0], /FFmpeg \(audio playback\) and Tesseract .* are not installed/);
  assert.match(lines[0], /sudo apt install ffmpeg tesseract-ocr/);
  // macOS without Homebrew: never runs Homebrew's remote installer itself.
  assert.match(deps.manualHelp('darwin', deps.toolsFor('darwin')), /brew\.sh\)[^]*brew install ffmpeg/);
});

test('pixelfm --install-deps and quiet commands', () => {
  const runtime = path.join(tempDir('pixelfm rt'), 'runtime');
  const env = { HOME: tempDir('pixelfm home'), PIXELFM_RUNTIME_DIR: runtime };
  const complete = { ...env, PATH: `${fakePath('ffplay', 'tesseract')}:/usr/bin:/bin` };
  const all = spawnSync(process.execPath, [BIN, '--install-deps'], { env: complete, encoding: 'utf8' });
  assert.equal(all.status, 0);
  assert.match(all.stdout, /Everything PixelFM uses is installed/);

  const bare = { ...env, PATH: '/usr/bin:/bin', PIXELFM_NO_INSTALL: '1' };
  const some = spawnSync(process.execPath, [BIN, '--install-deps'], { env: bare, encoding: 'utf8' });
  assert.equal(some.status, 1);
  assert.match(some.stderr, /FFmpeg \(audio playback\)/);
  const info = spawnSync(process.execPath, [BIN, '--runtime-info'], { env: bare, encoding: 'utf8' });
  assert.match(info.stdout, /installer /);
});

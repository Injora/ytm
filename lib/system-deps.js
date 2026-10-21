'use strict';

/**
 * System tools PixelFM uses but an npm package can't carry:
 *
 * - FFmpeg, everywhere: its ffplay plays the audio. A bundled copy isn't
 *   workable: ffplay needs the system's audio and SDL libraries, there are no
 *   dependable static builds of it for Linux, and it would add ~80 MB.
 * - Tesseract, on Linux only: it reads songs from screenshots. macOS has text
 *   recognition built in (Apple Vision) and so does Windows, so nothing is
 *   installed there.
 *
 * The OS decides what is checked and which package manager installs it:
 * Homebrew (or MacPorts) on macOS; apt, dnf, pacman, zypper or apk on Linux.
 * When something is missing the launcher says what it is for and asks in the
 * terminal first; sudo asks for the password itself. It never installs
 * without a yes, never in a run without a terminal (it prints the command
 * instead), and never installs a package manager (Homebrew's installer is a
 * remote script). A "no" is remembered until the next PixelFM version;
 * `pixelfm --install-deps` asks again.
 */

const fs = require('node:fs');
const path = require('node:path');
const { spawnSync } = require('node:child_process');

const TOOLS = [
  {
    id: 'ffmpeg',
    binary: 'ffplay',
    label: 'FFmpeg',
    purpose: 'audio playback',
    platforms: ['darwin', 'linux'],
  },
  {
    id: 'tesseract',
    binary: 'tesseract',
    label: 'Tesseract',
    purpose: 'reading songs from screenshots',
    platforms: ['linux'],
  },
];

// Package names per manager. Keep in sync with src/pixelfm/services/deps.py.
const MANAGERS = [
  {
    name: 'brew',
    platform: 'darwin',
    sudo: false,
    install: ['install'],
    packages: { ffmpeg: ['ffmpeg'] },
    paths: ['/opt/homebrew/bin/brew', '/usr/local/bin/brew'],
  },
  {
    name: 'port',
    platform: 'darwin',
    sudo: true,
    install: ['install'],
    packages: { ffmpeg: ['ffmpeg'] },
    paths: ['/opt/local/bin/port'],
  },
  {
    name: 'apt-get',
    platform: 'linux',
    sudo: true,
    install: ['install', '-y'],
    refresh: ['update'],
    packages: { ffmpeg: ['ffmpeg'], tesseract: ['tesseract-ocr'] },
  },
  {
    name: 'dnf',
    platform: 'linux',
    sudo: true,
    install: ['install', '-y'],
    packages: { ffmpeg: ['ffmpeg-free'], tesseract: ['tesseract', 'tesseract-langpack-eng'] },
  },
  {
    name: 'pacman',
    platform: 'linux',
    sudo: true,
    install: ['-S', '--needed', '--noconfirm'],
    packages: { ffmpeg: ['ffmpeg'], tesseract: ['tesseract', 'tesseract-data-eng'] },
  },
  {
    name: 'zypper',
    platform: 'linux',
    sudo: true,
    install: ['--non-interactive', 'install'],
    packages: { ffmpeg: ['ffmpeg'], tesseract: ['tesseract-ocr', 'tesseract-ocr-traineddata-english'] },
  },
  {
    name: 'apk',
    platform: 'linux',
    sudo: true,
    install: ['add'],
    packages: { ffmpeg: ['ffmpeg'], tesseract: ['tesseract-ocr', 'tesseract-ocr-data-eng'] },
  },
];

/** Find an executable on PATH (or at one of ``extra``) without a shell. */
function which(name, env = process.env, extra = []) {
  const dirs = (env.PATH || '').split(path.delimiter).filter(Boolean);
  for (const candidate of [...dirs.map((dir) => path.join(dir, name)), ...extra]) {
    try {
      fs.accessSync(candidate, fs.constants.X_OK);
      if (fs.statSync(candidate).isFile()) return candidate;
    } catch {
      // not here
    }
  }
  return null;
}

/** The tools this system should have, for this OS. */
function toolsFor(platform = process.platform) {
  return TOOLS.filter((tool) => tool.platforms.includes(platform));
}

function missingTools(env = process.env, platform = process.platform) {
  return toolsFor(platform).filter((tool) => !which(tool.binary, env));
}

/** The first package manager of this OS that is installed. */
function findManager(env = process.env, platform = process.platform) {
  for (const manager of MANAGERS) {
    if (manager.platform !== platform) continue;
    const executable = which(manager.name, env, manager.paths || []);
    if (executable) return { ...manager, executable };
  }
  return null;
}

function isRoot() {
  return typeof process.getuid === 'function' && process.getuid() === 0;
}

/** The command (argv) that installs ``tools`` with ``manager``. */
function installCommand(manager, tools, { root = isRoot(), env = process.env } = {}) {
  const packages = tools.flatMap((tool) => manager.packages[tool.id] || []);
  if (!packages.length) return null;
  const command = [manager.executable, ...manager.install, ...packages];
  if (!manager.sudo || root) return command;
  const sudo = which('sudo', env);
  return sudo ? [sudo, ...command] : null;
}

/** Command text for messages: "sudo apt-get install -y ffmpeg". */
function commandText(command) {
  return command.map((part) => (path.isAbsolute(part) ? path.basename(part) : part)).join(' ');
}

/** What to do by hand when nothing can be installed automatically. */
function manualHelp(platform, tools) {
  const names = tools.map((tool) => tool.id).join(' ');
  if (platform === 'darwin') {
    return [
      'Install Homebrew (https://brew.sh), then run:',
      `  brew install ${names}`,
    ].join('\n');
  }
  return [
    'Install with your package manager, for example:',
    `  Debian/Ubuntu:  sudo apt install ${tools.map((t) => (t.id === 'tesseract' ? 'tesseract-ocr' : t.id)).join(' ')}`,
    `  Fedora:         sudo dnf install ${tools.map((t) => (t.id === 'tesseract' ? 'tesseract' : 'ffmpeg-free')).join(' ')}`,
    `  Arch:           sudo pacman -S ${tools.map((t) => (t.id === 'tesseract' ? 'tesseract tesseract-data-eng' : t.id)).join(' ')}`,
  ].join('\n');
}

function readState(file) {
  try {
    return JSON.parse(fs.readFileSync(file, 'utf8'));
  } catch {
    return {};
  }
}

function writeState(file, state) {
  try {
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.writeFileSync(file, JSON.stringify(state, null, 2));
  } catch {
    // only a convenience: asking again is fine
  }
}

/** Ask a yes/no question on the terminal itself (not stdin, which PixelFM
 * inherits next). Enter means yes. */
function askTerminal(question) {
  let fd;
  try {
    fd = fs.openSync('/dev/tty', 'r+');
    fs.writeSync(fd, question);
    const buffer = Buffer.alloc(64);
    const read = fs.readSync(fd, buffer, 0, buffer.length, null);
    const answer = buffer.toString('utf8', 0, read).trim().toLowerCase();
    return answer === '' || answer === 'y' || answer === 'yes';
  } catch {
    return false;
  } finally {
    if (fd !== undefined) fs.closeSync(fd);
  }
}

function runInherited(command) {
  const result = spawnSync(command[0], command.slice(1), { stdio: 'inherit' });
  return !result.error && result.status === 0;
}

function joinLabels(tools) {
  const labels = tools.map((tool) => `${tool.label} (${tool.purpose})`);
  return labels.length > 1 ? `${labels.slice(0, -1).join(', ')} and ${labels.at(-1)}` : labels[0];
}

/**
 * Check this OS's tools and offer to install the missing ones.
 * Returns { missing, installed } (tool ids).
 */
function ensureSystemTools({
  env = process.env,
  platform = process.platform,
  interactive = Boolean(process.stdin.isTTY && process.stderr.isTTY),
  ask = askTerminal,
  run = runInherited,
  log = (message) => process.stderr.write(`${message}\n`),
  stateFile = null,
  version = '',
  force = false,
  root = isRoot(),
} = {}) {
  const missing = missingTools(env, platform);
  if (!missing.length) return { missing: [], installed: [] };
  const ids = missing.map((tool) => tool.id);
  const state = stateFile ? readState(stateFile) : {};
  const declined =
    state.declined && state.declined.version === version && ids.every((id) => state.declined.tools.includes(id));
  if (declined && !force) return { missing: ids, installed: [] };

  const what = joinLabels(missing);
  const verb = missing.length > 1 ? 'are' : 'is';
  const manager = findManager(env, platform);
  const command = manager && installCommand(manager, missing, { root, env });
  if (!command) {
    log(`PixelFM: ${what} ${verb} not installed.\n${manualHelp(platform, missing)}`);
    return { missing: ids, installed: [] };
  }
  const text = commandText(command);
  if (env.PIXELFM_NO_INSTALL === '1' || !interactive) {
    log(`PixelFM: ${what} ${verb} not installed. Install with:\n  ${text}`);
    return { missing: ids, installed: [] };
  }
  const yes = ask(`PixelFM uses ${what}, which ${verb} not installed.\nInstall now with \`${text}\`? [Y/n] `);
  if (!yes) {
    if (stateFile) writeState(stateFile, { ...state, declined: { version, tools: ids } });
    log('PixelFM: skipped. Run `pixelfm --install-deps` to install later.');
    return { missing: ids, installed: [] };
  }
  let ok = run(command);
  if (!ok && manager.refresh) {
    // A fresh system may not know the package yet: refresh the lists, retry.
    const refresh = command.slice(0, command.indexOf(manager.executable) + 1).concat(manager.refresh);
    ok = run(refresh) && run(command);
  }
  const still = missingTools(env, platform).map((tool) => tool.id);
  const installed = ids.filter((id) => !still.includes(id));
  if (stateFile) {
    const { declined: _, ...rest } = state;
    writeState(stateFile, rest);
  }
  if (still.length) {
    const left = missing.filter((tool) => still.includes(tool.id));
    log(`PixelFM: ${joinLabels(left)} still ${left.length > 1 ? 'are' : 'is'} not installed${ok ? '' : ' (the install failed)'}.\n${manualHelp(platform, left)}`);
  } else {
    log(`PixelFM: installed ${missing.map((tool) => tool.label).join(' and ')}.`);
  }
  return { missing: still, installed };
}

module.exports = {
  MANAGERS,
  TOOLS,
  askTerminal,
  commandText,
  ensureSystemTools,
  findManager,
  installCommand,
  manualHelp,
  missingTools,
  toolsFor,
  which,
};

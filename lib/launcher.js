'use strict';

/**
 * npm launcher for PixelFM (a Python/Textual application).
 *
 * On first run it creates a private Python virtual environment in the user's
 * data folder and installs the PixelFM wheel shipped inside this npm package,
 * together with dependencies pinned in python/requirements.lock. It never
 * modifies the system Python, never uses a development checkout's .venv, and
 * never runs remote scripts. Later runs reuse the environment until the
 * package (wheel or lock file) changes.
 *
 * The app then runs as a child process with the terminal inherited. Signals
 * are forwarded and the child's exit status becomes ours.
 */

const crypto = require('node:crypto');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawn, spawnSync } = require('node:child_process');

const PKG_ROOT = path.resolve(__dirname, '..');
const MIN_PYTHON = [3, 12];
// Tested versions first; newer ones next; then whatever `python3` is.
const PYTHON_CANDIDATES = ['python3.13', 'python3.12', 'python3.14', 'python3'];
const MARKER = 'pixelfm-runtime.json';
const FORWARDED_SIGNALS = ['SIGINT', 'SIGTERM', 'SIGHUP', 'SIGQUIT'];

class LauncherError extends Error {}

const PYTHON_HELP = [
  'PixelFM needs Python 3.12 or newer (with the venv module).',
  '  macOS:          brew install python@3.13',
  '  Debian/Ubuntu:  sudo apt install python3.12 python3.12-venv',
  '                  (or python3 python3-venv if your release ships 3.12+)',
  '  Fedora:         sudo dnf install python3.12',
  'Or point PixelFM at a specific interpreter: PIXELFM_PYTHON=/path/to/python3 ytm',
].join('\n');

const FFMPEG_HELP = [
  'Audio playback needs ffplay, which comes with FFmpeg:',
  '  macOS:          brew install ffmpeg',
  '  Debian/Ubuntu:  sudo apt install ffmpeg',
  '  Fedora:         sudo dnf install ffmpeg   (RPM Fusion)',
  'Search works without it. Run `ytm --check` after installing.',
].join('\n');

function packageVersion() {
  return JSON.parse(fs.readFileSync(path.join(PKG_ROOT, 'package.json'), 'utf8')).version;
}

/** Where the private environment lives (no shell expansion, spaces are fine). */
function runtimeDir(env = process.env, platform = process.platform, home = os.homedir()) {
  if (env.PIXELFM_RUNTIME_DIR) return path.resolve(env.PIXELFM_RUNTIME_DIR);
  if (platform === 'darwin') {
    return path.join(home, 'Library', 'Application Support', 'PixelFM', 'runtime');
  }
  const base = env.XDG_DATA_HOME || path.join(home, '.local', 'share');
  return path.join(base, 'pixelfm', 'runtime');
}

/** Find an executable on PATH without invoking a shell. */
function which(name, env = process.env) {
  for (const dir of (env.PATH || '').split(path.delimiter)) {
    if (!dir) continue;
    const candidate = path.join(dir, name);
    try {
      fs.accessSync(candidate, fs.constants.X_OK);
      if (fs.statSync(candidate).isFile()) return candidate;
    } catch {
      // not here
    }
  }
  return null;
}

function versionOk(version) {
  const [major, minor] = version.split('.').map(Number);
  return major > MIN_PYTHON[0] || (major === MIN_PYTHON[0] && minor >= MIN_PYTHON[1]);
}

/** Run a candidate interpreter and check its version and venv support. */
function probePython(executable, env = process.env) {
  const result = spawnSync(
    executable,
    ['-c', 'import sys, venv, ensurepip; print("%d.%d.%d" % sys.version_info[:3])'],
    { encoding: 'utf8', env: cleanEnv(env), timeout: 20000 },
  );
  if (result.error || result.status !== 0) {
    const detail = (result.stderr || String(result.error || '')).trim().split('\n').pop();
    if (/ensurepip|venv/.test(detail || '')) {
      return { ok: false, reason: 'venv/ensurepip missing (install the python3-venv package)' };
    }
    return { ok: false, reason: detail || 'could not run' };
  }
  const version = result.stdout.trim();
  return versionOk(version)
    ? { ok: true, version }
    : { ok: false, version, reason: `Python ${version} is too old` };
}

/** First suitable interpreter, or an explanation of what was tried. */
function findPython(env = process.env) {
  const tried = [];
  const candidates = env.PIXELFM_PYTHON
    ? [env.PIXELFM_PYTHON]
    : PYTHON_CANDIDATES.map((name) => which(name, env)).filter(Boolean);
  for (const candidate of [...new Set(candidates)]) {
    const probe = probePython(candidate, env);
    if (probe.ok) return { path: candidate, version: probe.version };
    tried.push(`${candidate}: ${probe.reason}`);
  }
  return { error: true, tried };
}

/** Environment for Python children: isolated from the user's Python setup. */
function cleanEnv(env = process.env) {
  const result = { ...env, PYTHONNOUSERSITE: '1', PIP_DISABLE_PIP_VERSION_CHECK: '1' };
  delete result.PYTHONPATH;
  delete result.PYTHONHOME;
  delete result.VIRTUAL_ENV;
  delete result.PIP_REQUIRE_VIRTUALENV;
  return result;
}

function bundledWheel() {
  const dir = path.join(PKG_ROOT, 'python');
  const wheels = fs.existsSync(dir)
    ? fs.readdirSync(dir).filter((name) => /^pixelfm-.*\.whl$/.test(name))
    : [];
  if (wheels.length !== 1) {
    throw new LauncherError(
      `expected exactly one PixelFM wheel in ${dir}, found ${wheels.length}. ` +
        'Reinstall the package (npm install -g pixelfm), or run `npm run build:wheel` in a checkout.',
    );
  }
  return path.join(dir, wheels[0]);
}

function sha256(file) {
  return crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
}

function runtimePython(dir) {
  return path.join(dir, 'bin', 'python');
}

function readMarker(dir) {
  try {
    return JSON.parse(fs.readFileSync(path.join(dir, MARKER), 'utf8'));
  } catch {
    return null;
  }
}

function fingerprint() {
  const lock = path.join(PKG_ROOT, 'python', 'requirements.lock');
  return `${sha256(bundledWheel())}:${sha256(lock)}`;
}

function runtimeReady(dir, print = fingerprint()) {
  const marker = readMarker(dir);
  return Boolean(marker && marker.fingerprint === print && fs.existsSync(runtimePython(dir)));
}

function pidAlive(pid) {
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}

/** Remove half-built environments left by an interrupted first run. */
function removeStaleBuilds(dir) {
  const parent = path.dirname(dir);
  const prefix = `${path.basename(dir)}.tmp-`;
  let entries = [];
  try {
    entries = fs.readdirSync(parent);
  } catch {
    return;
  }
  for (const name of entries) {
    if (!name.startsWith(prefix)) continue;
    const pid = Number(name.slice(prefix.length));
    if (!pid || !pidAlive(pid)) fs.rmSync(path.join(parent, name), { recursive: true, force: true });
  }
}

function step(command, args, env, what) {
  const result = spawnSync(command, args, { encoding: 'utf8', env, stdio: ['ignore', 'pipe', 'pipe'] });
  if (result.error || result.status !== 0) {
    const output = `${result.stdout || ''}\n${result.stderr || ''}`.trim().split('\n').slice(-15).join('\n');
    throw new LauncherError(`${what} failed.\n${output || String(result.error)}`);
  }
  return result.stdout;
}

/** Create or refresh the private environment; returns its directory. */
function ensureRuntime({ env = process.env, log = (m) => process.stderr.write(`${m}\n`), force = false } = {}) {
  const dir = runtimeDir(env);
  const print = fingerprint();
  if (!force && runtimeReady(dir, print)) return dir;

  const python = findPython(env);
  if (python.error) {
    const tried = python.tried.length ? `\nTried:\n  ${python.tried.join('\n  ')}` : '';
    throw new LauncherError(`${PYTHON_HELP}${tried}`);
  }
  fs.mkdirSync(path.dirname(dir), { recursive: true });
  removeStaleBuilds(dir);
  const tmp = `${dir}.tmp-${process.pid}`;
  fs.rmSync(tmp, { recursive: true, force: true });
  const childEnv = cleanEnv(env);
  log(`PixelFM: setting up a private Python environment (one-time, about a minute)`);
  log(`         using Python ${python.version} at ${python.path}`);
  log(`         in ${dir}`);
  try {
    step(python.path, ['-m', 'venv', tmp], childEnv, 'Creating the virtual environment');
    const venvPython = runtimePython(tmp);
    step(
      venvPython,
      [
        '-m', 'pip', 'install', '--no-input', '--quiet', '--prefer-binary',
        '-r', path.join(PKG_ROOT, 'python', 'requirements.lock'),
        bundledWheel(),
      ],
      childEnv,
      'Installing PixelFM and its pinned dependencies (needs internet once)',
    );
    const version = step(venvPython, ['-m', 'pixelfm', '--version'], childEnv, 'Checking the installation').trim();
    fs.writeFileSync(
      path.join(tmp, MARKER),
      JSON.stringify({ fingerprint: print, package: packageVersion(), python: python.version, app: version }, null, 2),
    );
  } catch (error) {
    fs.rmSync(tmp, { recursive: true, force: true });
    throw error;
  }
  fs.rmSync(dir, { recursive: true, force: true });
  try {
    fs.renameSync(tmp, dir);
  } catch (error) {
    // Another `ytm` finished setting up at the same moment: use that one.
    fs.rmSync(tmp, { recursive: true, force: true });
    if (!runtimeReady(dir, print)) throw error;
  }
  log('PixelFM: ready.');
  if (!which('ffplay', env)) log(`\nNote: ffplay was not found.\n${FFMPEG_HELP}\n`);
  return dir;
}

/** Run PixelFM with the terminal inherited; resolve to an exit code. */
function launch(dir, args, env = process.env) {
  return new Promise((resolve) => {
    const child = spawn(runtimePython(dir), ['-m', 'pixelfm', ...args], {
      stdio: 'inherit',
      env: { ...cleanEnv(env), PIXELFM_LAUNCHER: 'npm' },
    });
    const handlers = new Map();
    for (const signal of FORWARDED_SIGNALS) {
      const handler = () => {
        try {
          process.kill(child.pid, signal);
        } catch {
          // child already gone
        }
      };
      handlers.set(signal, handler);
      process.on(signal, handler);
    }
    const detach = () => {
      for (const [signal, handler] of handlers) process.removeListener(signal, handler);
    };
    child.on('error', (error) => {
      detach();
      process.stderr.write(`ytm: could not start PixelFM: ${error.message}\n`);
      resolve(1);
    });
    child.on('exit', (code, signal) => {
      detach();
      if (signal) {
        // Exit the way the child did, so shells see e.g. "terminated by SIGTERM".
        process.kill(process.pid, signal);
        resolve(128 + (os.constants.signals[signal] || 1));
        return;
      }
      resolve(code === null ? 1 : code);
    });
  });
}

function runtimeInfo(env = process.env) {
  const dir = runtimeDir(env);
  const marker = readMarker(dir);
  const lines = [
    `pixelfm npm package ${packageVersion()}`,
    `runtime   ${dir}`,
    `status    ${marker ? `ready (Python ${marker.python}, ${marker.app})` : 'not set up yet'}`,
    `ffplay    ${which('ffplay', env) || 'not found'}`,
  ];
  return lines.join('\n');
}

async function main(argv = process.argv.slice(2), env = process.env) {
  if (process.platform === 'win32') {
    process.stderr.write('ytm: PixelFM supports macOS and Linux only.\n');
    return 1;
  }
  try {
    if (argv[0] === '--runtime-info') {
      process.stdout.write(`${runtimeInfo(env)}\n`);
      return 0;
    }
    if (argv[0] === '--reinstall-runtime') {
      ensureRuntime({ env, force: true });
      return 0;
    }
    const dir = ensureRuntime({ env });
    if (argv[0] === '--update-ytdlp') {
      step(
        runtimePython(dir),
        ['-m', 'pip', 'install', '--no-input', '--upgrade', 'yt-dlp[default]'],
        cleanEnv(env),
        'Updating yt-dlp',
      );
      process.stdout.write('yt-dlp updated inside PixelFM\'s environment.\n');
      return 0;
    }
    return await launch(dir, argv, env);
  } catch (error) {
    if (error instanceof LauncherError) {
      process.stderr.write(`ytm: ${error.message}\n`);
      return 1;
    }
    throw error;
  }
}

module.exports = {
  FFMPEG_HELP,
  LauncherError,
  PYTHON_HELP,
  bundledWheel,
  cleanEnv,
  ensureRuntime,
  fingerprint,
  findPython,
  launch,
  main,
  probePython,
  runtimeDir,
  runtimeInfo,
  runtimePython,
  versionOk,
  which,
};

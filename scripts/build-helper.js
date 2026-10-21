#!/usr/bin/env node
// Builds PixelFM's macOS helpers into src/pixelfm/native/PixelFMMedia.app, a
// minimal, hidden app bundle, ad-hoc signed: the media-controls bridge
// (native/macos/NowPlaying.swift, the bundle's executable) and the screenshot
// text reader (native/macos/RecognizeText.swift). Both are universal (arm64 +
// x86_64) binaries. Needs Xcode or the
// Command Line Tools (swiftc, lipo, codesign); runs on macOS only. The
// bundle ships inside the wheel, so users never compile anything.
'use strict';

const { execFileSync } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const ROOT = path.resolve(__dirname, '..');
const NATIVE = path.join(ROOT, 'native', 'macos');
const PLIST = path.join(NATIVE, 'Info.plist');
const OUT = path.join(ROOT, 'src', 'pixelfm', 'native', 'PixelFMMedia.app');
// The first is the bundle's executable (CFBundleExecutable in Info.plist).
const TOOLS = [
  { name: 'pixelfm-nowplaying', source: path.join(NATIVE, 'NowPlaying.swift') },
  { name: 'pixelfm-ocr', source: path.join(NATIVE, 'RecognizeText.swift') },
];
const MIN_MACOS = '12.0';

function run(cmd, args) {
  execFileSync(cmd, args, { stdio: ['ignore', 'inherit', 'inherit'] });
}

function main() {
  if (process.platform !== 'darwin') {
    console.error('build-helper: macOS only; skipping (the helpers are macOS-only).');
    return 0;
  }
  const { version } = require(path.join(ROOT, 'package.json'));
  const work = fs.mkdtempSync(path.join(os.tmpdir(), 'pixelfm-helper-'));
  try {
    fs.rmSync(OUT, { recursive: true, force: true });
    const macos = path.join(OUT, 'Contents', 'MacOS');
    fs.mkdirSync(macos, { recursive: true });
    for (const tool of TOOLS) {
      const slices = [];
      for (const arch of ['arm64', 'x86_64']) {
        const out = path.join(work, `${tool.name}-${arch}`);
        run('swiftc', ['-O', '-target', `${arch}-apple-macos${MIN_MACOS}`, tool.source, '-o', out]);
        slices.push(out);
      }
      run('lipo', ['-create', ...slices, '-output', path.join(macos, tool.name)]);
    }
    const plist = fs.readFileSync(PLIST, 'utf8').replaceAll('VERSION', version);
    fs.writeFileSync(path.join(OUT, 'Contents', 'Info.plist'), plist);
    // Extra executables are signed first; signing the bundle seals them in.
    for (const tool of TOOLS.slice(1)) {
      run('codesign', ['--force', '--sign', '-', '--timestamp=none', path.join(macos, tool.name)]);
    }
    run('codesign', ['--force', '--sign', '-', '--timestamp=none', OUT]);
    run('codesign', ['--verify', '--strict', OUT]);
    console.log(`built ${path.relative(ROOT, OUT)} (universal, macOS ${MIN_MACOS}+)`);
    return 0;
  } finally {
    fs.rmSync(work, { recursive: true, force: true });
  }
}

process.exitCode = main();

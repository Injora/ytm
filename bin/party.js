#!/usr/bin/env node
'use strict';

// `party`: start PixelFM. The work happens in ../lib/launcher.js.
require('../lib/launcher.js')
  .main(process.argv.slice(2))
  .then((code) => {
    if (typeof code === 'number') process.exitCode = code;
  });

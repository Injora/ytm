#!/usr/bin/env python3
"""Stands in for the macOS media helper: same JSON-line protocol.

Logs every message it receives to $FAKE_MEDIA_LOG, and after "ready" sends the
commands listed in $FAKE_MEDIA_COMMANDS ("next,toggle,seek:42", ...), each
after an "update" arrives (so the app has published something first).
"""

import json
import os
import sys

commands = [c for c in os.environ.get("FAKE_MEDIA_COMMANDS", "").split(",") if c]
with open(os.environ["FAKE_MEDIA_LOG"], "a", buffering=1) as log:
    print(json.dumps({"event": "ready"}), flush=True)
    for line in sys.stdin:
        log.write(line)
        message = json.loads(line)
        if message.get("type") == "quit":
            break
        if message.get("type") == "update" and commands:
            name, _, position = commands.pop(0).partition(":")
            event = {"event": "command", "name": name}
            if position:
                event["position"] = float(position)
            print(json.dumps(event), flush=True)
    log.write('{"type": "exited"}\n')

#!/usr/bin/env python3
"""Base Alert Watchdog — restart run-loop.sh if dead."""
from pathlib import Path
import subprocess, time

ROOT = Path(__file__).resolve().parent
RESTART_COOLDOWN = 900  # seconds
last_restart = 0.0
LOG = ROOT / 'watchdog.log'
RUN_LOOP = ROOT / 'run-loop.sh'

while True:
    r = subprocess.run(['pgrep', '-f', str(RUN_LOOP)], capture_output=True)
    if r.returncode != 0:
        now = time.time()
        if now - last_restart > RESTART_COOLDOWN:
            ts = time.strftime('%Y-%m-%d %H:%M:%S')
            msg = f'{ts} run-loop.sh DEAD -- restarting
'
            with LOG.open('a', encoding='utf-8') as f:
                f.write(msg)
            print(msg, end='', flush=True)
            subprocess.Popen(
                ['bash', str(RUN_LOOP)],
                cwd=ROOT,
                stdout=LOG.open('a', encoding='utf-8'),
                stderr=subprocess.STDOUT,
            )
            last_restart = now
        else:
            remaining = int(RESTART_COOLDOWN - (now - last_restart))
            ts = time.strftime('%Y-%m-%d %H:%M:%S')
            print(f'{ts} cooldown skip ({remaining}s left)', flush=True)
    time.sleep(30)

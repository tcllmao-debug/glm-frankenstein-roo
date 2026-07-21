#!/usr/bin/env python3
"""24-hour recurring daemon training. Runs continuously, saving brain every hour."""
import sys, time, json, os, urllib.request
sys.path.insert(0, '/home/z/my-project/patches')
from daemon_intelligence import DaemonIntelligence
daemon = DaemonIntelligence()
daemon.start()
start = time.time()
while time.time() - start < 86400:
    try:
        urllib.request.urlopen("http://127.0.0.1:8787/api/state", timeout=5)
        daemon.observe_communication("scanner","daemon","alive","Scanner responding")
    except: daemon.observe_communication("scanner","daemon","offline","Scanner not responding")
    daemon.brain.save()
    s = daemon.brain.get_stats()
    print(f"[{time.strftime('%H:%M:%S')}] brain={s['brain_size']} conn={s['total_connections']} obs={s['observations']}")
    time.sleep(3600)

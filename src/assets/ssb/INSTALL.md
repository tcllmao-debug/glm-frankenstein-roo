# SSB Z Mark Complete — Install Guide

## Prerequisites
- Python 3.12+
- Node.js 18+ / Bun
- 4GB RAM minimum (8GB recommended)
- 10GB disk space

## Quick Start

```bash
# 1. Clone
git clone https://github.com/tcllmao-debug/SSB-Z-MARK-COMPLETE.git
cd SSB-Z-MARK-COMPLETE

# 2. Reassemble scanner (choose one)
cd scanner && bash reassemble_monolith.sh && cd ..
# OR: cat scanner/monolith_part_* > vfix_ssb_v11_monolith_z_mark.py

# 3. Start scanner
python3 scripts/start_scanner_daemon.py
# Wait for: SCANNER READY

# 4. Build frontend
cd frontend
npm install
NODE_OPTIONS="--max-old-space-size=2048" npx next build
cp -r .next/static .next/standalone/.next/
cp -r public .next/standalone/
cd ..
python3 scripts/start_prod_daemon.py

# 5. Load all patches (soul, vision, consciousness)
python3 scripts/run_everything.py

# 6. Start persistent connection
python3 scripts/persistent_connection.py

# 7. Open browser
# http://localhost:3000/scanner
```

## API Endpoints
- `GET /` — Galaxy brain 3D visualization
- `GET /api/state` — Node/edge/event state (JSON)
- `GET /api/node?id=X` — Node detail with raw preview
- `GET /api/raw?path=X` — Raw file content (chunked)
- `GET /api/raw-full?path=X` — 8-method file reading
- `GET /api/permissions?path=X` — File permissions metadata
- `GET /api/god-eye?target=X` — Deep filesystem inspection
- `GET /api/puppet-edit?target=X&prompt=X` — Puppet bridge
- `POST /api/save-file` — Save file edits `{"filePath":"...","content":"..."}`
- `GET /api/globe-registry` — Forked globe tracking
- `GET /api/god-omni-status` — System status
- `GET /api/hive-status` — Hive mind status
- `GET /api/neural-state` — Neural network state

## CLI Commands (138 total)
```
python3 vfix_ssb_v11_monolith_z_mark.py brain --host 127.0.0.1 --port 8787
python3 vfix_ssb_v11_monolith_z_mark.py god-eye --target /
python3 vfix_ssb_v11_monolith_z_mark.py mcp
python3 vfix_ssb_v11_monolith_z_mark.py vscan
```

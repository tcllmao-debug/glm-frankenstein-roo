#!/bin/bash
# SSB Z-MARK — reassemble + repair the 400MB monolith (repo parts stay untouched)
# Fixes two upstream defects in the shipped parts:
#   1) file ends mid-docstring (truncated tail) -> closes the string
#   2) second module-level `from __future__ import annotations` (SyntaxError) -> neutralized
set -e
SRC_DIR="${1:-.}"          # dir containing monolith_part_00..07
OUT="${2:-vfix_ssb_v11_monolith_z_mark.py}"
cd "$SRC_DIR"
cat monolith_part_00 monolith_part_01 monolith_part_02 monolith_part_03 monolith_part_04 monolith_part_05 monolith_part_06 monolith_part_07 > "$OUT"
printf '\n"""\n# [ssb-beast-repair] closed truncated tail docstring\n' >> "$OUT"
python3 - "$OUT" <<'EOF'
import sys
p = sys.argv[1]
lines = open(p, encoding='utf-8', errors='replace').read().split('\n')
count = 0
for i, ln in enumerate(lines):
    if ln.strip() == 'from __future__ import annotations':
        count += 1
        if count > 1:
            lines[i] = 'pass  # [ssb-beast-repair] duplicate module-level future import neutralized'
open(p, 'w', encoding='utf-8').write('\n'.join(lines))
print('repaired', count - 1, 'duplicate future imports')
EOF
echo "OK -> $OUT ($(stat -c%s "$OUT") bytes)"
echo "Beast layer: SSB_MONOLITH_ORIGINAL=$OUT python3 ssb_beast.py brain --port 8787"

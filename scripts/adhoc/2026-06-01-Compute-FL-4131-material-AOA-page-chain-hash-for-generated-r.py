# Ad hoc script: Compute FL-4131 material AOA page-chain hash for generated runtime identity
# Created: 2026-06-01
# Canonical gap: <describe what tool should own this>

#!/usr/bin/env python3
import hashlib
import json
from pathlib import Path

repo = Path(__file__).resolve().parents[2]
aoa = json.loads((repo / "assets/glyphs/atlases/material.additive.v1.atlas_of_atlases.json").read_text(encoding="utf-8"))
chain = []
for page in aoa.get("pages", []):
    cell_px = page.get("cell_px")
    page_hash = page.get("page_hash")
    if isinstance(cell_px, int) and isinstance(page_hash, str) and len(page_hash) == 64:
        chain.append((cell_px, page_hash))
chain.sort()
payload = json.dumps(chain, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
print(hashlib.sha256(payload).hexdigest())

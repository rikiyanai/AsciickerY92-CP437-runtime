# Ad hoc script: Consolidate per-elv FL-4260 §3 proof artifacts into one PROOF.json
# Created: 2026-06-20
# Canonical gap: <describe what tool should own this>

import json
from pathlib import Path

base = Path('docs/research/ascii/verification/fl4260/2026-06-19-color-precondition-slider-proof')
r0r1 = json.loads((base / 'perelv_r0r1' / 'PROOF.json').read_text())
r2r3 = json.loads((base / 'perelv_r2r3' / 'PROOF.json').read_text())

consolidated = {
    "schema": "fl4260.keyboard_rendered_buffer_25_sliders.color_precondition.v2",
    "map": r0r1["map"],
    "material": r0r1["material"],
    "method": r0r1["method"],
    "floor": r0r1["floor"],
    "palette_starter_pre": r0r1.get("palette_starter_pre"),
    "rendered_cells": r0r1.get("rendered_cells"),
    "jitter_floor": r0r1.get("jitter_floor"),
    "sliders": {**r0r1["sliders"], **r2r3["sliders"]},
}

total = len(consolidated["sliders"])
ok = sum(1 for v in consolidated["sliders"].values() if v.get("changed_render"))
failed = [k for k, v in consolidated["sliders"].items() if not v.get("changed_render")]
consolidated["summary"] = {
    "sliders_total": total,
    "sliders_changed_render": ok,
    "all_changed_render": ok == total,
    "failed_labels": failed,
}

out = base / "PROOF_all16_attempt.json"
out.write_text(json.dumps(consolidated, indent=2))
print(out, total, ok, failed)

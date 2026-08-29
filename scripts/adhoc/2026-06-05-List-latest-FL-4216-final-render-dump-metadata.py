# Ad hoc script: List latest FL-4216 final-render dump metadata
# Created: 2026-06-05
# Canonical gap: analyze_runs.py should expose latest final-render dump
# metadata by FL lane, feature flag, git identity, and pose without requiring
# one-off filesystem scans.

from pathlib import Path
import json

for p in sorted(
    Path(".run/final_render_cell_dump").glob("*/metadata.json"),
    key=lambda p: p.stat().st_mtime,
    reverse=True,
)[:12]:
    m = json.loads(p.read_text())
    fs = m.get("feature_state", {})
    print(
        p.parent,
        "head=", str(m.get("git_head", ""))[:10],
        "schema=", m.get("fact_schema_version"),
        "water=", fs.get("water_current_package"),
        "player=", m.get("player_world"),
        "title=", m.get("dump_title"),
    )

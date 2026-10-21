#!/usr/bin/env python3
"""Generate RQ_FL_CROSSWALK.json and report dangling refs.

Reads:
  - docs/plans/2026-03-22-multiplayer-canonical-spec.md (RQ table rows)
  - the effective failure-log overlay through the canonical sidecar-aware loader

Writes:
  - docs/RQ_FL_CROSSWALK.json

Exit gate: zero dangling RQ refs (FL overlay cites unknown RQ row) and zero
unknown FL refs (RQ table cites FL id absent from FAILURE_LOG overlay block).
"""
import datetime
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from maintainer.lib.failure_log import read_fl_overlay  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SPEC_PATH = ROOT / "docs/plans/2026-03-22-multiplayer-canonical-spec.md"
FL_PATH = ROOT / "docs/FAILURE_LOG.md"
OUT_PATH = ROOT / "docs/RQ_FL_CROSSWALK.json"

FL_RE = re.compile(r"\bFL-\d{4,}\b")
RQ_RE = re.compile(r"\bRQ-\d{3,}[a-z]?\b")

def parse_rq_rows():
    rows = {}
    with open(SPEC_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line.startswith("|"):
                continue
            parts = [p.strip() for p in line.split("|")]
            # Drop empty leading/trailing cells from outer pipes
            parts = [p for p in parts if p]
            if not parts or not RQ_RE.match(parts[0]):
                continue
            rq_id = parts[0]
            state = parts[1] if len(parts) > 1 else ""
            blocker = parts[2] if len(parts) > 2 else ""
            title = parts[3] if len(parts) > 3 else ""
            fl_refs = sorted(set(FL_RE.findall(line)))
            rows[rq_id] = {
                "state": state,
                "blocker": blocker,
                "title": title,
                "fl_refs": fl_refs,
                "raw_line": line
            }
    return rows

def parse_fl_entries():
    """Return effective FL ids and RQ links from the canonical overlay owner."""
    fl_to_rq: dict[str, set[str]] = {}
    overlay = read_fl_overlay(FL_PATH)
    fl_ids = set(overlay)
    for fl, row in overlay.items():
        for rq in row.get("RQRefs", []):
            fl_to_rq.setdefault(fl, set()).add(rq)
    return fl_ids, fl_to_rq

def main() -> int:
    rq_rows = parse_rq_rows()
    fl_ids, fl_to_rq = parse_fl_entries()

    rq_to_fl: dict[str, set[str]] = {rq: set(info["fl_refs"]) for rq, info in rq_rows.items()}
    for fl, rqs in fl_to_rq.items():
        for rq in rqs:
            rq_to_fl.setdefault(rq, set()).add(fl)

    # Dangling: FL overlay cites RQ not in spec
    dangling_rq = []
    for fl, rqs in fl_to_rq.items():
        for rq in rqs:
            if rq not in rq_rows:
                dangling_rq.append({"fl": fl, "rq": rq})

    # Dangling: RQ table cites FL not in failure log overlay block
    dangling_fl = []
    for rq, info in rq_rows.items():
        for fl in info["fl_refs"]:
            if fl not in fl_ids:
                dangling_fl.append({"rq": rq, "fl": fl})

    output = {
        "schema": "rq_fl_crosswalk.v1",
        # FL-4564: stamp the real generation date. A hardcoded literal let this
        # artifact keep claiming 2026-07-08 while its content moved, which is how
        # the stale RQ-172 PARKED row survived a canon change on 2026-09-13.
        "generated_at": datetime.date.today().isoformat(),
        "summary": {
            "rq_rows": len(rq_rows),
            "fl_overlay_entries": len(fl_ids),
            "fl_to_rq_edges": sum(len(v) for v in fl_to_rq.values()),
            "rq_to_fl_edges": sum(len(v) for v in rq_to_fl.values()),
            "dangling_rq_refs": len(dangling_rq),
            "dangling_fl_refs": len(dangling_fl)
        },
        "rq_rows": {k: {"state": v["state"], "blocker": v["blocker"], "title": v["title"], "fl_refs": v["fl_refs"]} for k, v in rq_rows.items()},
        "fl_to_rq": {k: sorted(v) for k, v in fl_to_rq.items()},
        "rq_to_fl": {k: sorted(v) for k, v in rq_to_fl.items()},
        "dangling_rq_refs": dangling_rq,
        "dangling_fl_refs": dangling_fl
    }

    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)

    print(json.dumps(output["summary"], indent=2))
    if dangling_rq or dangling_fl:
        print("DANGLING REFS FOUND")
        return 1
    print(f"wrote {OUT_PATH}")
    return 0

if __name__ == "__main__":
    sys.exit(main())

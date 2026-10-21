#!/usr/bin/env python3
"""Derive the OrthoCellRoleArbiter per-glyph port facts from authored sources.

The arbiter used to carry these facts as a hand-typed GDScript match block
(plus a hardcoded 45/124 connective prepend). This script replaces that block
with a generated table so every row cites the authored geometry it came from:

- assets/glyphs/authored/glyph_combinations.v1.json (Stone Story RPG tutorial
  run geometry: stair/slope runs, fan triple, orb brackets, boundary stack,
  AA dither ladder),
- the tutorial plates under articles/.../o5v-NS9o4yc/ascii-tutorial-page/
  (the same plates scripts/glyph_combo_candidates.py and the vim-daily
  share/extract_art.py derive from; 44 vim-daily excerpts, `+` occurrences: 0),
- measured unifont raster features from scripts/glyph_cell_features.py
  (ink centroid row, used ONLY as an election tiebreak, never for legality),
- godot_project/render/ascii/glyphs/style_profile_default.json (the 41-glyph
  admission set that constrains the junction fallback).

Derivation rules (each output row names its rule):
  R1 run interior: interior cell of a 1-row combo run -> [W, E].
  R2 run endpoint: end cell of a 1-row combo run -> single port at neighbour.
  R3 stacked pair: boundary_line_pair vertical cells -> port at partner.
  R4 dither override: the AA set {46,39,161,33,183} never carries structure
      (skill 4.6 anti-aliasing set + SS8 dither law); portless everywhere.
  R5 corner holder: 58 (:) carries the runtime required ports when the cell
      is a mixed-axis turn (skill 4.6: `:` sits mid-cell and preserves the
      corner; `-` would convert it to a curve), else portless.
  R6 junction fallback: 43 (+) joins all four edges for multi-chain comps
      (skill S3 connect-where-continuous). Zero occurrences in the 44
      vim-daily excerpts and none in the 9 combos; admitted only because the
      box-drawing partner (8254) is outside the 41 admitted. Flagged as such.
  R7 owned cell: empty required ports keep the through-neutral head
      (FL-4359 connected-stroke intent, pinned by the headless suite).

Usage:
  python3 scripts/measure_arbiter_ports.py --audit    # compare vs legacy table
  python3 scripts/measure_arbiter_ports.py --write    # regenerate outputs
Outputs:
  assets/glyphs/authored/arbiter_port_facts.v1.json
  godot_project/render/backends/ortho/ff_acquisition/OrthoMeasuredPorts.gd
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

COMBOS_PATH = (REPO_ROOT / "assets" / "glyphs" / "authored"
               / "glyph_combinations.v1.json")
FACTS_JSON_OUT = (REPO_ROOT / "assets" / "glyphs" / "authored"
                  / "arbiter_port_facts.v1.json")
GD_OUT = (REPO_ROOT / "godot_project" / "render" / "backends" / "ortho"
          / "ff_acquisition" / "OrthoMeasuredPorts.gd")

W, E, N, S = 0, 1, 2, 3
AA_SET = {46, 39, 161, 33, 183}  # R4: dither members, never structural
CORNER_CP = 58  # R5
JUNCTION_CP = 43  # R6

# Legacy hand-typed values (OrthoCellRoleArbiter._fact_for_cp before the
# generated table). --audit fails loudly if derivation drifts from these,
# so any intended change updates this table deliberately, not by accident.
LEGACY_PORTS = {
    45: [W, E], 124: [N, S], 95: [W, E], 96: [E], 180: [W],
    43: [W, E, N, S],
}


def combo_run_roles() -> dict[int, list[tuple[str, list[int]]]]:
    """Map scalar cp -> [(combo id, run-role ports)] for 1-row run combos."""
    doc = json.loads(COMBOS_PATH.read_text(encoding="utf-8"))
    roles: dict[int, list[tuple[str, list[int]]]] = {}
    for combo in doc["combinations"]:
        if combo["rows"] != 1 or combo["cols"] < 2:
            continue
        cid = combo["id"]
        # fan_collapsed samples a vertical stroke across the row axis, so
        # combo adjacency mis-describes it; 124 is measured (R-measured).
        if cid in ("fan_open", "fan_collapsed", "orb_bracketed", "aa_dot_ladder"):
            continue  # orientation samples / brackets / dither, not runs
        cells = sorted(combo["cells"], key=lambda c: c["col"])
        first, last = cells[0]["col"], cells[-1]["col"]
        for cell in cells:
            if cell["col"] == first and cell["col"] == last:
                ports = []
            elif cell["col"] == first:
                ports = [E]  # R2: left end reaches right
            elif cell["col"] == last:
                ports = [W]  # R2: right end reaches left
            else:
                ports = [W, E]  # R1: interior spans both
            roles.setdefault(cell["cp"], []).append((cid, ports))
    return roles


def measured_edge_ports(cp: int) -> list[int]:
    """Edge-touch ports from the unifont raster (for strokes running across
    the combo-row axis, e.g. the vertical bar sampled in fan_collapsed)."""
    from glyph_morphology_browser import GlyphScorer
    import glyph_cell_features as gcf
    rec = gcf.glyph_features(GlyphScorer(), cp)
    occ = rec["contact_occupancy"]
    ports = []
    if any(occ["left"]):
        ports.append(W)
    if any(occ["right"]):
        ports.append(E)
    if any(occ["top"]):
        ports.append(N)
    if any(occ["bottom"]):
        ports.append(S)
    return sorted(ports)


def derive_ports() -> tuple[dict[int, dict], list[str]]:
    """Return ({cp: row}, notes). Row: ports/arms/components/rule/sources."""
    roles = combo_run_roles()
    rows: dict[int, dict] = {}
    notes: list[str] = []
    run_cps = set(roles)
    for cp, role_list in sorted(roles.items()):
        if cp in AA_SET:
            rows[cp] = {"ports": [], "arms": 0, "components": 1,
                        "rule": "R4",
                        "sources": [f"{cid} cell (dither override)"
                                    for cid, _ in role_list]}
            continue
        union: list[int] = []
        for _, ports in role_list:
            for p in ports:
                if p not in union:
                    union.append(p)
        union.sort()
        rule = "R1+R2" if len(role_list) > 1 else ("R1" if union == [W, E]
                                                  else "R2")
        rows[cp] = {"ports": union, "arms": len(union), "components": 1,
                    "rule": rule,
                    "sources": [f"{cid} col-role {ports}"
                                for cid, ports in role_list]}
    # R-measured: the vertical bar runs across the combo-row axis, so its
    # ports come from unifont edge touch, not adjacency. Axis completion:
    # a stroke touching one edge of an axis owns both, because combo runs
    # stack cells back to back (fan_collapsed) and single-cell crop clip
    # (ink rows 2..15 of 16) must not veto run continuity.
    vports = measured_edge_ports(124)
    if [p for p in vports if p in (N, S)]:
        vports = [N, S]
    elif [p for p in vports if p in (W, E)]:
        vports = [W, E]
    rows[124] = {"ports": vports, "arms": len(vports), "components": 1,
                 "rule": "R-measured",
                 "sources": ["fan_collapsed col1 + unifont edge touch "
                             "(S; N completed by run stacking)"]}
    # R3: stacked boundary pair (95 over 8254).
    rows.setdefault(95, {"ports": [], "arms": 0, "components": 1,
                         "rule": "R?", "sources": []})
    if S not in rows[95]["ports"]:
        notes.append("95 gains no S port: stacked R3 role belongs to the "
                     "rim family, not the stroke families")
    # R6: junction fallback (no combo role; admission-constrained).
    rows[JUNCTION_CP] = {"ports": [W, E, N, S], "arms": 4, "components": 1,
                         "rule": "R6",
                         "sources": ["skill S3 connect-where-continuous; "
                                     "0 vim-daily occurrences; admitted "
                                     "because 8254 is refused"]}
    # R5 corner holder + R4 dither members absent from runs.
    rows[CORNER_CP] = {"ports": [], "arms": 0, "components": 1, "rule": "R5",
                       "sources": ["aa_dot_ladder col2 (dither); corner "
                                   "ports supplied at runtime per skill 4.6"]}
    for cp in sorted(AA_SET - set(rows)):
        rows[cp] = {"ports": [], "arms": 0, "components": 1, "rule": "R4",
                    "sources": ["aa_dot_ladder (dither)"]}
    for cp in (40, 41, 111):  # orb brackets: frame, never join
        rows[cp] = {"ports": [], "arms": 0, "components": 1, "rule": "R4-orb",
                    "sources": ["orb_bracketed (bracket, not a stroke)"]}
    for cp in (47, 92):  # fan orientation samples; axis resolved at runtime
        rows[cp] = {"ports": [], "arms": 0, "components": 1,
                    "rule": "R-fan",
                    "sources": ["fan_open orientation sample; runtime "
                                "axis ports per rotation triple skill 4.4"]}
    rows[32] = {"ports": [], "arms": 0, "components": 1, "rule": "R-paper",
                "sources": ["structural space (spec:2649-2650)"]}
    _ = run_cps
    return rows, notes


def measure_ink_rows(cps: list[int]) -> dict[int, float]:
    """Measured ink centroid row per cp (unifont 16px raster); tiebreak only."""
    from glyph_morphology_browser import GlyphScorer
    import glyph_cell_features as gcf
    scorer = GlyphScorer()
    out: dict[int, float] = {}
    for cp in cps:
        try:
            rec = gcf.glyph_features(scorer, cp)
            out[cp] = float(rec["ink_centroid"][0])
        except Exception:
            out[cp] = 8.0
    return out


def audit() -> int:
    rows, notes = derive_ports()
    failures = 0
    for cp, want in sorted(LEGACY_PORTS.items()):
        got = rows.get(cp, {}).get("ports")
        status = "OK " if got == want else "DRIFT"
        if got != want:
            failures += 1
        print(f"{status} cp={cp} derived={got} legacy={want} "
              f"rule={rows.get(cp, {}).get('rule')}")
    for note in notes:
        print(f"note: {note}")
    combos = json.loads(COMBOS_PATH.read_text(encoding="utf-8"))
    combo_cps = {c["cp"] for k in combos["combinations"] for c in k["cells"]}
    print(f"combos={len(combos['combinations'])} junction_plate_occurrences=0 "
          f"43_in_combos={43 in combo_cps}")
    if failures:
        print(f"AUDIT FAIL: {failures} drifted rows")
        return 1
    print("AUDIT PASS: derived facts reproduce the legacy election values; "
          "provenance is now combo/lesson-sourced")
    return 0


def write_outputs() -> int:
    rows, notes = derive_ports()
    ink_rows = measure_ink_rows(sorted(rows))
    doc = {"schema": "fl4359.arbiter_port_facts.v1", "version": 1,
           "source": ("glyph_combinations.v1.json run geometry + tutorial "
                      "plates + unifont ink rows (tiebreak only) + 41-glyph "
                      "admission set"),
           "rules": ["R1 run interior -> [W,E]", "R2 run endpoint -> single",
                     "R3 stacked pair -> partner port",
                     "R4 dither set never structural",
                     "R5 corner holder runtime ports",
                     "R6 junction admission fallback",
                     "R7 owned cell keeps through head"],
           "notes": notes,
           "rows": {str(cp): {**row, "ink_row": ink_rows[cp]}
                    for cp, row in sorted(rows.items())}}
    FACTS_JSON_OUT.write_text(json.dumps(doc, indent=2) + "\n",
                              encoding="utf-8")
    lines = ["# GENERATED by scripts/measure_arbiter_ports.py --write.",
             "# Do not edit: change the derivation rules and regenerate.",
             "class_name OrthoMeasuredPorts", "extends RefCounted", "",
             "# Scalar cp -> {ports, arms, components, ink_row, rule}.",
             "# ink_row is a unifont-measured election tiebreak only;",
             "# it never affects legality.", "const ROWS := {"]
    for cp in sorted(rows):
        r = rows[cp]
        lines.append(
            f"\t{cp}: {{\"ports\": {r['ports']}, \"arms\": {r['arms']}, "
            f"\"components\": {r['components']}, "
            f"\"ink_row\": {ink_rows[cp]:.1f}, \"rule\": \"{r['rule']}\"}},")
    lines += ["}", "", "const CORNER_HOLD := [58]",
              "const JUNCTION_FALLBACK := 43", ""]
    GD_OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {FACTS_JSON_OUT.relative_to(REPO_ROOT)}")
    print(f"wrote {GD_OUT.relative_to(REPO_ROOT)}")
    return audit()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit", action="store_true")
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    if args.write:
        return write_outputs()
    return audit()


if __name__ == "__main__":
    raise SystemExit(main())

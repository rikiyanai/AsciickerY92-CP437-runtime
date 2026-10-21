"""Compute byte-identity hints for unverified XP layers based on user-verified ones.

Reads:
  - Safari localStorage (user's verification state)
  - All XP files (343 role-candidate L>=2 layers)

For each unverified layer:
  - Compute its visible-cell signature
  - Search user-verified layers for a byte-identical signature
  - If found, record a HINT (does NOT change status — layer stays unverified)

Writes:
  - /Users/r/Desktop/bundle_layer_audit_20260520/identity_hints.json
    Map: <layer_id> -> { hint_guess, hint_source, hint_rationale, hint_from, hint_status }

The HTML builder reads this file (if present) and overrides each unverified
layer's pre_guess + educated_guess.rationale with the hint, so the verifier
shows your own previous note as the suggested guess.
"""
from __future__ import annotations
import contextlib, io, json, sqlite3, sys
from pathlib import Path
from collections import Counter

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts" / "pipeline"))
from xp_core import XPFile  # type: ignore

AUDIT_DIR = Path("/Users/r/Desktop/bundle_layer_audit_20260520")
HINTS_OUT = AUDIT_DIR / "identity_hints.json"
LEDGER = AUDIT_DIR / "full_source_layer_ledger_20260520.json"
SAFARI_DB = Path(
    "/Users/r/Library/Containers/com.apple.Safari/Data/Library/WebKit/WebsiteData/Default/"
    "_X7xljDE1LRsPbjvEH_kOMlbWpRGkhN2FJnHFBqWs0Y/_X7xljDE1LRsPbjvEH_kOMlbWpRGkhN2FJnHFBqWs0Y/"
    "LocalStorage/localstorage.sqlite3"
)


def visible_cell_signature(xp_path: Path, layer_index: int):
    if not xp_path.exists():
        return None
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            xp = XPFile(str(xp_path))
    except Exception:
        return None
    if layer_index >= len(xp.layers):
        return None
    layer = xp.layers[layer_index]
    l0 = xp.layers[0]
    cells = []
    for y in range(layer.height):
        for x in range(layer.width):
            cell = layer.data[y][x]
            glyph, fg, bg = cell[0], tuple(cell[1]), tuple(cell[2])
            if x < l0.width and y < l0.height:
                l0_bg = tuple(l0.data[y][x][2])
                if bg == l0_bg:
                    continue
            cells.append((x, y, glyph, fg, bg))
    return frozenset(cells)


def reference_priority(entry: dict) -> int:
    """Higher = better reference for propagating a hint."""
    status = entry.get("status")
    has_note = bool((entry.get("note") or "").strip())
    src = entry.get("pre_source", "")
    if status == "reject" and has_note: return 100
    if status == "partial" and has_note: return 90
    if status == "accept" and has_note: return 80
    if status == "reject": return 70
    if status == "partial": return 60
    if status == "accept" and src == "GLYPH_SIGNATURE": return 50  # user confirmed glyph heuristic
    if status == "accept" and src == "USER_VISUAL": return 40
    if status == "accept" and src == "PHASE3_COMPILER": return 30
    if status == "accept" and src == "AUTO_IDENTITY_PROPAGATION": return 20
    return 10


def build_layer_id(stem: str, layer_index: int) -> str:
    if "-" in stem:
        family, ahsw = stem.split("-", 1)
    else:
        family, ahsw = stem, "base"
    return f"{family}-{ahsw}-L{layer_index}"


def main():
    # 1) Load Safari state
    conn = sqlite3.connect(str(SAFARI_DB))
    conn.execute("PRAGMA wal_checkpoint(FULL);")
    row = conn.execute("SELECT value FROM ItemTable WHERE key='fl4078_phase0_verifier_v2'").fetchone()
    conn.close()
    state = json.loads(row[0].decode("utf-16-le"))
    print(f"Loaded {len(state)} state entries")

    # 2) Load the ledger to get the full list of 343 role-candidate layers
    with LEDGER.open() as f:
        ledger = json.load(f)
    role_rows = [r for r in ledger["rows"] if int(r["layer_index"]) >= 2]
    print(f"Ledger role-candidate layers: {len(role_rows)}")

    # 3) Compute signature for every role-candidate layer
    sigs: dict[str, frozenset] = {}
    print("Computing signatures...")
    for i, r in enumerate(role_rows):
        xp_rel = r["source_xp_path"]
        xp_basename = Path(xp_rel).name
        layer_index = int(r["layer_index"])
        # try several asset roots
        for cand in [
            REPO_ROOT / "assets" / xp_rel,
            REPO_ROOT / "godot_project" / "assets" / xp_rel,
            REPO_ROOT / xp_rel,
        ]:
            if cand.exists():
                xp_full = cand
                break
        else:
            continue
        sig = visible_cell_signature(xp_full, layer_index)
        if sig is None:
            continue
        layer_id = build_layer_id(xp_basename.replace(".xp", ""), layer_index)
        sigs[layer_id] = sig
        if (i + 1) % 50 == 0:
            print(f"  {i+1}/{len(role_rows)}")
    print(f"Got signatures for {len(sigs)} layers")

    # 4) Split into verified-reference set and unverified-target set
    verified_refs: list[tuple[str, frozenset, dict]] = []
    unverified_ids: list[str] = []
    for layer_id in sigs:
        entry = state.get(layer_id, {})
        status = entry.get("status")
        if status in ("accept", "reject", "partial", "ambig"):
            verified_refs.append((layer_id, sigs[layer_id], entry))
        else:
            unverified_ids.append(layer_id)
    print(f"Verified refs: {len(verified_refs)}  | Unverified targets: {len(unverified_ids)}")

    # 5) Build sig -> best-priority reference index
    sig_to_ref: dict[frozenset, tuple[str, dict, int]] = {}
    for ref_id, sig, entry in verified_refs:
        prio = reference_priority(entry)
        prev = sig_to_ref.get(sig)
        if prev is None or prio > prev[2]:
            sig_to_ref[sig] = (ref_id, entry, prio)
    print(f"Distinct verified signatures: {len(sig_to_ref)}")

    # Pre-compute position sets and position->cell maps for near-identity matching
    # Two layers are "near-identical" when they fill EXACTLY the same set of (x,y)
    # positions but a small number of cells differ in (glyph, fg, bg). This
    # captures the upstream artist's mouth-glyph swap pattern: identical silhouette
    # except for 9-18 cells of mouth direction.
    NEAR_DIFF_CELLS_MAX = 40        # absolute cell-diff cap (capture mouth-swap + small adjacent variations)
    NEAR_DIFF_RATIO_MAX = 0.010     # 1.0% of cells
    positions_of: dict[str, frozenset] = {}
    cell_by_pos: dict[str, dict] = {}
    for layer_id, sig in sigs.items():
        positions_of[layer_id] = frozenset((x, y) for (x, y, g, fg, bg) in sig)
        cell_by_pos[layer_id] = {(x, y): (g, fg, bg) for (x, y, g, fg, bg) in sig}

    # Group verified refs by positions-frozenset for fast near-identity lookup
    pos_to_refs: dict[frozenset, list[tuple[str, dict, int]]] = {}
    for ref_id, sig, entry in verified_refs:
        p = positions_of[ref_id]
        pos_to_refs.setdefault(p, []).append((ref_id, entry, reference_priority(entry)))

    # 6) For each unverified layer, look for byte-identical, then shape-identical
    hints: dict[str, dict] = {}
    matches_by_source = Counter()
    matches_by_kind = Counter()
    for uid in unverified_ids:
        sig = sigs[uid]
        match = sig_to_ref.get(sig)
        match_kind = "byte_identical"
        match_diff = 0
        if not match:
            # Try shape-identical (same positions, small content diff).
            # IMPORTANT: every state variable here must be re-initialized per
            # uid -- previous iteration's `best` must NOT leak into this one.
            u_positions = positions_of[uid]
            u_by_pos = cell_by_pos[uid]
            ref_candidates = pos_to_refs.get(u_positions, [])
            scored = []
            for ref_id, ref_entry, prio in ref_candidates:
                ref_by_pos = cell_by_pos[ref_id]
                diff = sum(1 for p in u_positions if u_by_pos[p] != ref_by_pos[p])
                if diff > NEAR_DIFF_CELLS_MAX: continue
                if diff / max(1, len(u_positions)) > NEAR_DIFF_RATIO_MAX: continue
                note_len = len((ref_entry.get("note") or "").strip())
                # Prefer (low diff, high priority, longer descriptive note, alpha id)
                scored.append((diff, -prio, -note_len, ref_id, ref_entry, prio))
            if scored:
                scored.sort()
                diff, _, _, ref_id, ref_entry, prio = scored[0]
                match = (ref_id, ref_entry, prio)
                match_kind = "shape_identical_near"
                match_diff = diff
        if not match:
            continue
        ref_id, ref_entry, prio = match
        ref_status = ref_entry.get("status")
        ref_note = (ref_entry.get("note") or "").strip()
        ref_pre_guess = ref_entry.get("pre_guess", "") or ""
        ref_corrected = ref_entry.get("corrected_label", "") or ""

        # Build hint guess text — prefer the note, fall back to corrected_label, fall back to pre_guess
        if ref_note:
            hint_guess = ref_note
        elif ref_corrected:
            hint_guess = ref_corrected
        elif ref_pre_guess:
            hint_guess = ref_pre_guess
        else:
            hint_guess = f"(see {ref_id})"

        suggested_action = {
            "accept":  "YES — user confirmed this on the identical layer",
            "reject":  "NO — user rejected an identical layer with this correction",
            "partial": "PARTIAL — user marked an identical layer partial with this note",
            "ambig":   "SKIP — user marked an identical layer as ambiguous",
        }.get(ref_status, "")

        if match_kind == "byte_identical":
            hint_source = "HINT_FROM_USER_BYTE_IDENTICAL"
            rationale_prefix = f"byte-identical to {ref_id}"
        else:
            hint_source = f"HINT_FROM_USER_NEAR_IDENTICAL_{match_diff}_CELL_DIFF"
            rationale_prefix = f"near-identical to {ref_id} ({match_diff} cells differ, same silhouette)"
        hints[uid] = {
            "hint_guess": hint_guess,
            "hint_status": ref_status,
            "hint_source": hint_source,
            "hint_kind": match_kind,
            "hint_diff_cells": match_diff,
            "hint_rationale": f"{rationale_prefix} (you marked it {ref_status}{': '+ref_note if ref_note else ''})",
            "hint_from": ref_id,
            "hint_suggested_action": suggested_action,
            "hint_priority": prio,
        }
        matches_by_source[ref_status] += 1
        matches_by_kind[match_kind] += 1

    print()
    print(f"=== {len(hints)} hints generated ===")
    for status, n in matches_by_source.most_common():
        print(f"  from user {status}: {n} hints")
    print(f"  by match kind:")
    for kind, n in matches_by_kind.most_common():
        print(f"    {kind}: {n}")

    HINTS_OUT.write_text(json.dumps(hints, indent=2))
    print(f"\nWrote: {HINTS_OUT}")
    print(f"Re-run build_html_verifier.py to bake these hints into the HTML.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

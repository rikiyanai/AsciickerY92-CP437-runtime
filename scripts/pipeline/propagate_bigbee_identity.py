"""Auto-propagate user's bigbee BIG-BEE / limbless-rider rejects to identical layers.

User has manually rejected ~10 bigbee L>=2 layers with notes like "BIG BEE",
"BIG BEE only", "PLAYER NO ARMOUR... LIMBLESS UNIQUE 3X3 GRID...". For any
other bigbee L>=2 layer whose visible-cell content is BYTE-IDENTICAL to one
of those anchors, copy the reject status + note forward.

HARD RULE: identity test is exact equality of the set
  { (x, y, glyph, fg_rgb, bg_rgb) | cell visible after L0 key transparency }
If a single visible cell differs (glyph or color or position), do NOT propagate.

Scope: bigbee family only. Wolfie/wolack mounts have different anchors and are
not in scope for this run.
"""
from __future__ import annotations

import contextlib
import io
import json
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path("/Users/r/Downloads/asciicker-Y9-2")
sys.path.insert(0, str(REPO_ROOT / "scripts" / "pipeline"))
from xp_core import XPFile  # type: ignore

AUDIT_DIR = Path("/Users/r/Desktop/bundle_layer_audit_20260520")
BACKUP_DIR = AUDIT_DIR / "verifier_state_backups"
SAFARI_DB = Path(
    "/Users/r/Library/Containers/com.apple.Safari/Data/Library/WebKit/WebsiteData/Default/"
    "_X7xljDE1LRsPbjvEH_kOMlbWpRGkhN2FJnHFBqWs0Y/_X7xljDE1LRsPbjvEH_kOMlbWpRGkhN2FJnHFBqWs0Y/"
    "LocalStorage/localstorage.sqlite3"
)


def visible_cell_signature(xp_path: Path, layer_index: int) -> frozenset | None:
    """Return frozenset of (x, y, glyph, fg, bg) tuples for visible cells in this layer.

    Visibility test = cell bg differs from layer0 bg at same (x,y), per upstream
    sprite.cpp L0 key-transparency contract.
    """
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
                    continue  # transparent
            cells.append((x, y, glyph, fg, bg))
    return frozenset(cells)


def find_bigbee_layers(family: str = "bigbee"):
    sprites_dir = REPO_ROOT / "assets" / "sprites"
    files = sorted(sprites_dir.glob(f"{family}-*.xp"))
    if (sprites_dir / f"{family}.xp").exists():
        files.append(sprites_dir / f"{family}.xp")
    out = []
    for fp in files:
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                xp = XPFile(str(fp))
        except Exception:
            continue
        for li in range(2, len(xp.layers)):  # L0/L1 are system
            stem = fp.stem
            # build id matching HTML verifier ids: "bigbee-0010-L3" or "bigbee-base-L2"
            if "-" in stem:
                fam, ahsw = stem.split("-", 1)
            else:
                fam, ahsw = stem, "base"
            row_id = f"{fam}-{ahsw}-L{li}"
            out.append({"id": row_id, "xp_path": fp, "layer_index": li, "stem": stem})
    return out


def main():
    # ----------------------------------------------------------------------
    # 1) Load current verifier state from Safari sqlite
    # ----------------------------------------------------------------------
    conn = sqlite3.connect(str(SAFARI_DB))
    conn.execute("PRAGMA wal_checkpoint(FULL);")
    row = conn.execute(
        "SELECT value FROM ItemTable WHERE key='fl4078_phase0_verifier_v2'"
    ).fetchone()
    conn.close()
    if not row:
        print("ERROR: no verifier state in Safari localStorage")
        return 1
    state_blob: bytes = row[0]
    state_text = state_blob.decode("utf-16-le")
    state = json.loads(state_text)
    print(f"Loaded {len(state)} entries from Safari localStorage")

    # ----------------------------------------------------------------------
    # 2) Define anchors (user's explicit rejects with bee/rider notes)
    # ----------------------------------------------------------------------
    BEE_BODY_NOTE = "BIG BEE MOUNT"  # canonical note for auto-propagated bee-body
    RIDER_TORSO_NOTE = (
        "PLAYER NO ARMOUR NO HELMET / HAIR NO ARMS (PRESUMABLY TO INTERCHANGE WITH "
        "SWORD SHIELD) FOR BIG BEE. ITS A TORSO LIMBLESS UNQIUE 3X3 GRID OF PLAYER "
        "(NO HELM, NO ARMS, NO LEG IN SOME, NO ARMOUR(JUST SHIRT)"
    )
    RIDER_SWORD_NOTE = (
        "PLAYER HOLDING SWORD (NOT SWINGING) NO ARMOUR NO HELMET / HAIR NO ARMS "
        "CURCIAL: NO LEGS, JUST UPPER TORSO (PRESUMABLY TO INTERCHANGE WITH SWORD "
        "SHIELD) FOR BIG BEE. ITS AN UPPER UNQIUE 3X3 GRID OF PLAYER (NO HELM, "
        "ONLY ONE ARM, NO LEG IN SOME, NO ARMOUR(JUST SHIRT)"
    )

    RIDER_SWORD_SHORT = "upper torso holding sword, one arm"

    anchors_meta = [
        # (id, kind_label, propagation_note)
        ("bigbee-0000-L2", "bee_body",     BEE_BODY_NOTE),
        ("bigbee-0001-L2", "bee_body",     BEE_BODY_NOTE),
        ("bigbee-0002-L2", "bee_body",     BEE_BODY_NOTE),
        ("bigbee-0010-L2", "bee_body",     BEE_BODY_NOTE),
        ("bigbee-0011-L2", "bee_body",     BEE_BODY_NOTE),
        ("bigbee-0000-L3", "rider_torso",  RIDER_TORSO_NOTE),
        ("bigbee-0001-L3", "rider_torso",  RIDER_TORSO_NOTE),
        ("bigbee-0002-L3", "rider_torso",  RIDER_TORSO_NOTE),
        ("bigbee-0011-L3", "rider_torso",  RIDER_TORSO_NOTE),
        ("bigbee-0001-L4", "rider_sword_classic",  RIDER_SWORD_NOTE),
        ("bigbee-0111-L4", "rider_sword",  RIDER_SWORD_SHORT),
    ]
    # Layers we declare as user-verified rejects (anchor seeds) at run time.
    DECLARE_USER_REJECTS = {
        "bigbee-0111-L4": RIDER_SWORD_SHORT,
    }
    for aid, note in DECLARE_USER_REJECTS.items():
        existing = state.get(aid, {})
        if existing.get("status") != "reject":
            state[aid] = {
                "status": "reject",
                "note": note,
                "corrected_label": note,
                "pre_source": existing.get("pre_source", "USER_DECLARED_ANCHOR"),
                "pre_guess": existing.get("pre_guess", ""),
                "ts": __import__("datetime").datetime.now().isoformat(),
            }
            print(f"  seeded user anchor: {aid} -> reject, note='{note}'")
    # Sanity-check that all anchors really are user-rejected in state
    missing = [a for a in anchors_meta if a[0] not in state or state[a[0]].get("status") != "reject"]
    if missing:
        print("WARNING: these anchors are not present as rejects in state:")
        for a in missing:
            print(f"  {a[0]} (kind={a[1]})")
        print("Continuing anyway with the anchors that ARE rejected.")

    # ----------------------------------------------------------------------
    # 3) Enumerate all bigbee L>=2 layers and compute signatures
    # ----------------------------------------------------------------------
    layers = find_bigbee_layers("bigbee")
    print(f"Found {len(layers)} bigbee L>=2 layers")
    sigs: dict[str, frozenset] = {}
    for layer in layers:
        sig = visible_cell_signature(layer["xp_path"], layer["layer_index"])
        if sig is not None:
            sigs[layer["id"]] = sig

    # Build anchor signatures and their canonical notes
    anchor_sigs = {}  # sig (frozenset) -> (kind, note, anchor_id)
    for aid, kind, note in anchors_meta:
        if aid not in sigs:
            print(f"  anchor {aid}: signature unavailable, skipping")
            continue
        anchor_sigs.setdefault(sigs[aid], (kind, note, aid))

    print(f"Built {len(anchor_sigs)} distinct anchor signatures")

    # ----------------------------------------------------------------------
    # 4) Find candidates whose signature matches an anchor's signature
    # ----------------------------------------------------------------------
    proposals = []  # list of (candidate_id, anchor_id, kind, note)
    for layer in layers:
        cid = layer["id"]
        if cid not in sigs:
            continue
        # Skip if user has already manually decided this layer
        existing = state.get(cid)
        if existing and existing.get("status") in ("reject", "partial", "accept", "ambig"):
            # Only skip if it's a USER decision, not just a seeded accept with no note.
            # Anchors themselves we MUST skip (they're already correct).
            is_anchor = any(cid == a[0] for a in anchors_meta)
            has_note = bool((existing.get("note") or "").strip())
            is_seeded_accept = (
                existing.get("status") == "accept"
                and existing.get("pre_source") in ("PHASE3_COMPILER", "USER_VISUAL")
                and not has_note
            )
            if is_anchor or has_note or not is_seeded_accept:
                continue
            # Otherwise (seeded accept with no user touch) we may still override
            # if signature matches a reject anchor — print a notice.
        cand_sig = sigs[cid]
        if cand_sig in anchor_sigs:
            kind, note, anchor_id = anchor_sigs[cand_sig]
            if cid == anchor_id:
                continue
            proposals.append((cid, anchor_id, kind, note))

    # ----------------------------------------------------------------------
    # 5) Dry-run report
    # ----------------------------------------------------------------------
    print()
    print("=== PROPOSED AUTO-PROPAGATIONS ===")
    print(f"({len(proposals)} candidate layers exactly match an anchor signature)")
    print()
    by_kind: dict[str, list] = {}
    for cid, aid, kind, _ in proposals:
        by_kind.setdefault(kind, []).append((cid, aid))
    for kind, items in sorted(by_kind.items()):
        print(f"  [{kind}] {len(items)} layers:")
        for cid, aid in items:
            existing = state.get(cid, {})
            prior = existing.get("status", "(unset)")
            print(f"    {cid:30s}  <- identical to {aid}  (current status: {prior})")
        print()

    # Save dry-run for safekeeping
    BACKUP_DIR.mkdir(exist_ok=True)
    dryrun_path = BACKUP_DIR / "auto_propagation_bigbee_proposals.json"
    dryrun_path.write_text(json.dumps([
        {"candidate_id": cid, "anchor_id": aid, "kind": kind, "note": note}
        for cid, aid, kind, note in proposals
    ], indent=2))
    print(f"Wrote dry-run proposals to: {dryrun_path}")

    if "--apply" not in sys.argv:
        print()
        print("DRY RUN ONLY — re-run with --apply to write changes to Safari localStorage.")
        return 0

    # ----------------------------------------------------------------------
    # 6) Apply: update state + write back to Safari sqlite
    # ----------------------------------------------------------------------
    import datetime
    ts = datetime.datetime.now().isoformat()
    applied = 0
    for cid, aid, kind, note in proposals:
        state[cid] = {
            "status": "reject",
            "note": note,
            "corrected_label": note,
            "pre_source": "AUTO_IDENTITY_PROPAGATION",
            "pre_guess": "",
            "auto_propagated_from": aid,
            "auto_propagation_kind": kind,
            "ts": ts,
        }
        applied += 1

    # Backup current state before writing
    pre_backup = BACKUP_DIR / f"state_pre_bigbee_propagation_{ts.replace(':','-')}.json"
    # Re-read current safari state for the backup (in case it changed since step 1)
    conn = sqlite3.connect(str(SAFARI_DB))
    conn.execute("PRAGMA wal_checkpoint(FULL);")
    cur = conn.execute("SELECT value FROM ItemTable WHERE key='fl4078_phase0_verifier_v2'").fetchone()
    pre_backup.write_text(cur[0].decode("utf-16-le"))
    conn.close()
    print(f"Pre-write backup: {pre_backup}")

    # Write new state to Safari sqlite (UTF-16-LE encoding like WebKit uses)
    new_text = json.dumps(state, separators=(",", ":"))
    new_blob = new_text.encode("utf-16-le")
    conn = sqlite3.connect(str(SAFARI_DB))
    conn.execute("PRAGMA wal_checkpoint(FULL);")
    conn.execute(
        "UPDATE ItemTable SET value = ? WHERE key = 'fl4078_phase0_verifier_v2'",
        (new_blob,),
    )
    conn.commit()
    conn.execute("PRAGMA wal_checkpoint(FULL);")
    conn.close()
    print(f"APPLIED: {applied} auto-propagations written to Safari localStorage")
    print()
    print("NEXT: Cmd-R in Safari to reload the verifier and see updated state.")
    print("If Safari was open during write, it MAY hold a stale in-memory state.")
    print("Quit Safari and reopen for the cleanest reload.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

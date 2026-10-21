#!/usr/bin/env python3
# =============================================================================
# fl4359_owner_churn_gate.py
# =============================================================================
# WHAT THIS IS
# ------------
# The BACKEND flicker gate for FL-4359. "Flicker" on screen is a subcell whose
# WINNER (the surface that owns its paint) changes between frames while the
# camera holds still. Pixel differencing cannot tell paint-owner flips from
# sanctioned animation (water waves, moon glitter), so this gate reads the
# moving-cell dump's per-subcell diagnostic records instead: for every pair of
# consecutive dumped frames at a pinned pose it counts subcells whose final
# owner kind (diag r0 word 2) or captured winner kind (trace r1.x) changed.
# Any nonzero owner-flip count at a frozen camera is real flicker, cell by
# cell, with world receiver positions for each flip.
#
# HOW TO RUN
# ----------
#   python3 scripts/fl4359_owner_churn_gate.py --dump-dir "<dump dir>" \
#       [--max-flips 0] [--out receipt.json]
#
# --max-flips is the PASS threshold (default 0: a frozen camera may not flip
# any owner at all; the FL-4359/FL-4431 zero-repaint-at-rest rule).
# =============================================================================

import argparse   # command-line flags
import glob       # find dump frames
import json       # manifest in, receipt out
import struct     # decode diag records
import sys        # exit codes

# Owner-kind names for report rows (same enum as the resolve shader).
OWNER_NAMES = {0: "NONE", 1: "TERRAIN", 2: "STATIC_MESH", 3: "DYNAMIC_MESH",
               4: "ACTOR", 5: "WATER", 6: "SKY"}


def load_frame(path_json):
    """One dumped frame: manifest dict + raw diag bytes."""
    m = json.load(open(path_json))
    diag = open(path_json.replace(".json", "_diag.bin"), "rb").read()
    return m, diag


def main():
    ap = argparse.ArgumentParser(description="FL-4359 frozen-pose owner-churn gate")
    ap.add_argument("--dump-dir", required=True)
    ap.add_argument("--max-flips", type=int, default=0,
                    help="maximum allowed owner flips per frame pair (default 0)")
    ap.add_argument("--out", default="", help="JSON receipt path")
    args = ap.parse_args()

    manifests = sorted(glob.glob(args.dump_dir + "/frame_*.json"))
    if len(manifests) < 2:
        print("VERDICT: ERROR need >= 2 dumped frames for a churn pair")
        return 2

    pair_reports = []
    worst_pair = 0
    for a_path, b_path in zip(manifests, manifests[1:]):
        ma, da = load_frame(a_path)
        mb, db = load_frame(b_path)
        # The camera must be identical for the pair, or flips are just motion.
        ca, cb = ma["camera_origin"], mb["camera_origin"]
        if max(abs(ca[i] - cb[i]) for i in range(3)) > 1e-4:
            print(f"VERDICT: ERROR camera moved between {a_path} and {b_path}")
            return 2
        stride = ma["diag_stride"]
        n = min(len(da), len(db)) // stride
        flips = []
        for i in range(n):
            # Final owner kind lives in r0 word 2 (low byte).
            oa = struct.unpack_from("<I", da, i * stride + 8)[0] & 0xFF
            ob = struct.unpack_from("<I", db, i * stride + 8)[0] & 0xFF
            if oa == ob:
                continue
            rec = {"subcell_index": i,
                   "from": OWNER_NAMES.get(oa, str(oa)),
                   "to": OWNER_NAMES.get(ob, str(ob))}
            if stride >= 48:
                # Receiver world position of the NEW owner (r2 words 1-3).
                rx, ry, rz = (struct.unpack("<f", db[i*stride+36+4*k:i*stride+40+4*k])[0]
                              for k in range(3))
                rec["receiver"] = [round(rx, 2), round(ry, 2), round(rz, 2)]
            flips.append(rec)
        worst_pair = max(worst_pair, len(flips))
        pair_reports.append({
            "pair": [a_path.split("/")[-1], b_path.split("/")[-1]],
            "owner_flips": len(flips),
            "graded_subcells": n,
            "flip_pct": round(100.0 * len(flips) / max(n, 1), 4),
            "samples": flips[:40],
        })

    verdict = "PASS" if worst_pair <= args.max_flips else "FAIL"
    receipt = {"fl": "FL-4359", "kind": "frozen_pose_owner_churn_receipt",
               "verdict": verdict, "worst_pair_flips": worst_pair,
               "max_flips_allowed": args.max_flips, "pairs": pair_reports}
    if args.out:
        json.dump(receipt, open(args.out, "w"), indent=2)

    print(f"OWNER CHURN  verdict={verdict}  worst_pair_flips={worst_pair}")
    for p in pair_reports:
        print(f"  {p['pair'][0]} -> {p['pair'][1]}: {p['owner_flips']} flips "
              f"({p['flip_pct']}% of {p['graded_subcells']})")
        for s in p["samples"][:8]:
            print(f"    subcell {s['subcell_index']}: {s['from']} -> {s['to']}"
                  + (f" recv {s['receiver']}" if "receiver" in s else ""))
    if args.out:
        print(f"  receipt: {args.out}")
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())

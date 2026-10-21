#!/usr/bin/env python3
# =============================================================================
# fl4359_product_identity_churn_gate.py
# =============================================================================
# WHAT THIS IS
# ------------
# The FINE-GRAIN flicker gate for FL-4359. The earlier owner-churn gate
# (fl4359_owner_churn_gate.py) compares only the broad owner KIND per subcell
# (TERRAIN vs WATER vs MESH...), so it is blind to every flicker mode that
# keeps the owner but changes what is actually painted (operator audit
# 2026-07-12):
#
#   - TERRAIN cell A -> TERRAIN cell B   (material / key flip, same kind)
#   - glyph changes                       (same owner, different character)
#   - color changes                       (same glyph, different fg/bg ink)
#   - splat <-> backstop flips that land on the same owner kind
#
# This gate instead compares the FULL 80-byte FinalSubcellProductRecord —
# the exact data the compositor paints from — field by field, between every
# consecutive pair of dumped frames at a pinned camera pose. Any field that
# changes while the camera is frozen is real, visible flicker.
#
# RECORD LAYOUT (cell_owner_product.glsl, struct ProbeSubcellProductRecord):
#   bytes  0..15  r0 = uvec4(status, owner_kind, glyph, material_family)
#   bytes 16..31  r1 = uvec4(material_id, probe_texel_id, entity_id, matcell)
#   bytes 32..47  fg = vec4 foreground color
#   bytes 48..63  bg = vec4 background color
#   bytes 64..79  anchor = vec4 (xyz world anchor, w presence)
#
# WHAT COUNTS AS A FLIP
# ---------------------
# A subcell flips when any VISIBLE field differs between the two frames:
# status, owner_kind, glyph, material_family, material_id, matcell, fg, bg. The
# packed matcell is semantic: terrain glyph, shade, foliage, dither, edge,
# shadow, and color all consume it. Omitting it hid the key drift beneath
# apparently stable material IDs.
#
# The
# probe_texel_id and entity_id words are provenance (which texel supplied the
# paint) — a texel swap that paints IDENTICAL glyph+colors is invisible, so
# those words are reported separately as "provenance_only" churn, not counted
# in the PASS/FAIL verdict. The anchor is position metadata, ignored for the
# same reason. Colors compare with a small epsilon (1/255) because a
# sub-quantization float wiggle cannot change the painted 8-bit ink.
#
# HOW TO RUN
# ----------
#   python3 scripts/fl4359_product_identity_churn_gate.py \
#       --dump-dir "<dir with frame_NNN.json + frame_NNN_products.bin>" \
#       [--max-flips 0] [--out receipt.json]
#
# PASS = no visible-field flips in any consecutive frozen-pose frame pair
# (the FL-4359/FL-4431 zero-repaint-at-rest rule, now at full identity depth).
# =============================================================================

#
# MOVING-CAMERA MODE (operator audit 2026-07-12, "the zero-flips gate froze
# the camera"): when the camera DID move between two dumped frames (the
# --fl4359-operator-ab-move dolly), the gate switches to REPROJECTION
# grading instead of erroring out. For every subcell of frame B whose product
# ANCHOR (the winner's stable world point, record bytes 64..79) reprojects
# into frame A's product grid, the gate compares frame B's record against THAT
# frame-A product record. The two records are gradeable only when their world
# anchors agree within --max-anchor-delta. This deletes the former fixed-index
# comparison, which silently compared different world receivers during motion.

import argparse   # command-line flags
import glob       # find dump frames
import json       # manifest in, receipt out
import math       # reprojection trigonometry
import struct     # decode product records
import sys        # exit codes

# Owner-kind names for report rows (same enum as the resolve shader).
OWNER_NAMES = {0: "NONE", 1: "TERRAIN", 2: "STATIC_MESH", 3: "DYNAMIC_MESH",
               4: "ACTOR", 5: "WATER", 6: "SKY"}

# One painted-ink channel step: product colors are floats but the terminal
# paints 8-bit channels, so differences below 1/255 are invisible.
COLOR_EPS = 1.0 / 255.0


def load_frame(path_json):
    """One dumped frame: manifest dict + raw product bytes (may be empty)."""
    m = json.load(open(path_json))
    prod_path = path_json.replace(".json", "_products.bin")
    try:
        prod = open(prod_path, "rb").read()
    except FileNotFoundError:
        prod = b""
    return m, prod


def rec(buf, i, stride):
    """Decode product record i: (r0 uints, r1 uints, fg floats, bg floats)."""
    base = i * stride
    r0 = struct.unpack_from("<4I", buf, base)
    r1 = struct.unpack_from("<4I", buf, base + 16)
    fg = struct.unpack_from("<4f", buf, base + 32)
    bg = struct.unpack_from("<4f", buf, base + 48)
    return r0, r1, fg, bg


def colors_differ(a, b):
    """True when any channel differs by a paintable amount (>= 1/255)."""
    return any(abs(x - y) >= COLOR_EPS for x, y in zip(a, b))


def product_index_from_subcell(x, y, ansi_w):
    """Map 2xANSI screen coordinates to ProbeSubcellProduct block order."""
    ansi_x, ansi_y = x // 2, y // 2
    slot = (x & 1) | ((y & 1) << 1)
    return (ansi_y * ansi_w + ansi_x) * 4 + slot


def subcell_from_product_index(index, ansi_w):
    """Inverse of product_index_from_subcell."""
    ansi_cell, slot = divmod(index, 4)
    ansi_x, ansi_y = ansi_cell % ansi_w, ansi_cell // ansi_w
    return ansi_x * 2 + (slot & 1), ansi_y * 2 + ((slot >> 1) & 1)


def product_anchor(buf, index, stride):
    """Decode xyz+w anchor from one product record."""
    return struct.unpack_from("<4f", buf, index * stride + 64)


def build_product_projector(manifest):
    """Return world point -> ProbeSubcellProduct index for one frame.

    The product SSBO is ANSI-cell block order, not row-major 2xANSI order:
    (ansi_y * ansi_w + ansi_x) * 4 + slot. The retired moving gate returned
    row-major indices here, so even its projected screen location addressed a
    different record. Uses the exact dumped camera basis and the live 58-degree
    vertical FOV.
    """
    ansi_w = manifest["ansi_w"]
    sw, sh = ansi_w * 2, manifest["ansi_h"] * 2
    tanv = math.tan(math.radians(58.0) / 2.0)
    tanh_ = tanv * (float(sw) / float(sh))   # aspect from the subcell grid

    def product_of(p):
        cam = manifest["camera_origin"]
        bx = manifest["camera_basis_x"]; by = manifest["camera_basis_y"]; bz = manifest["camera_basis_z"]
        rel = [p[k] - cam[k] for k in range(3)]
        cx = sum(rel[k] * bx[k] for k in range(3))
        cy = sum(rel[k] * by[k] for k in range(3))
        cz = sum(rel[k] * bz[k] for k in range(3))
        depth = -cz
        if depth <= 0.01:
            return -1
        nx = cx / (depth * tanh_); ny = cy / (depth * tanv)
        if abs(nx) > 1.0 or abs(ny) > 1.0:
            return -1
        sx = min(max(int((nx * 0.5 + 0.5) * sw), 0), sw - 1)
        sy = min(max(int((0.5 - ny * 0.5) * sh), 0), sh - 1)
        return product_index_from_subcell(sx, sy, ansi_w)

    return product_of


def main():
    ap = argparse.ArgumentParser(
        description="FL-4359 frozen-pose FULL-IDENTITY churn gate")
    ap.add_argument("--dump-dir", required=True)
    ap.add_argument("--max-flips", type=int, default=0,
                    help="maximum allowed visible flips per frame pair (default 0)")
    ap.add_argument("--max-anchor-delta", type=float, default=0.10,
                    help="maximum A/B world-anchor distance for moving comparison")
    ap.add_argument("--out", default="", help="JSON receipt path")
    args = ap.parse_args()

    manifests = sorted(glob.glob(args.dump_dir + "/frame_*.json"))
    if len(manifests) < 2:
        print("VERDICT: ERROR need >= 2 dumped frames for a churn pair")
        return 2

    pair_reports = []
    worst_pair = 0
    for a_path, b_path in zip(manifests, manifests[1:]):
        ma, pa = load_frame(a_path)
        mb, pb = load_frame(b_path)
        # Frozen pair -> every subcell is gradeable. Moved pair -> grade only
        # subcells whose winner anchor reprojects into the SAME cell under
        # the other frame's camera (reprojection mode; see file header).
        ca, cb = ma["camera_origin"], mb["camera_origin"]
        moving = max(abs(ca[i] - cb[i]) for i in range(3)) > 1e-4
        project_a = None
        project_b = None
        if moving:
            project_a = build_product_projector(ma)
            project_b = build_product_projector(mb)
        stride = ma.get("products_stride", 80)
        if not pa or not pb:
            print("VERDICT: ERROR frame pair missing _products.bin "
                  "(re-dump with the perspective products readback enabled)")
            return 2
        n = min(len(pa), len(pb)) // stride
        # Per-field flip counters: which KIND of identity changed. One subcell
        # can tick several counters (e.g. owner + glyph + colors together).
        field_flips = {"status": 0, "owner": 0, "glyph": 0, "family": 0,
                       "material": 0, "matcell": 0, "anchor_y_band": 0,
                       "fg": 0, "bg": 0}
        provenance_only = 0
        visible_count = 0
        graded = 0
        rejected_self_projection = 0
        rejected_anchor_delta = 0
        rejected_offscreen = 0
        flips = []
        for i in range(n):
            a_index = i
            if moving:
                # Reprojection mode: frame B supplies the tracked world point.
                # Compare against the frame-A record at that point's projected
                # screen location, then require both records to name the same
                # world receiver. This is motion compensation, not a fixed-cell
                # comparison.
                b_anchor = product_anchor(pb, i, stride)
                if b_anchor[3] == 0.0:
                    continue
                if project_b(b_anchor[:3]) != i:
                    rejected_self_projection += 1
                    continue
                a_index = project_a(b_anchor[:3])
                if a_index < 0 or a_index >= n:
                    rejected_offscreen += 1
                    continue
                a_anchor = product_anchor(pa, a_index, stride)
                if a_anchor[3] == 0.0:
                    continue
                anchor_delta = math.dist(a_anchor[:3], b_anchor[:3])
                if anchor_delta > args.max_anchor_delta:
                    rejected_anchor_delta += 1
                    continue
            else:
                anchor_delta = 0.0
            a0, a1, afg, abg = rec(pa, a_index, stride)
            b0, b1, bfg, bbg = rec(pb, i, stride)
            a_anchor = product_anchor(pa, a_index, stride)
            b_anchor = product_anchor(pb, i, stride)
            graded += 1
            changed = []
            if a0[0] != b0[0]: changed.append("status")
            if a0[1] != b0[1]: changed.append("owner")
            if a0[2] != b0[2]: changed.append("glyph")
            if a0[3] != b0[3]: changed.append("family")
            if a1[0] != b1[0]: changed.append("material")
            if a1[3] != b1[3]: changed.append("matcell")
            if math.floor(a_anchor[1]) != math.floor(b_anchor[1]):
                changed.append("anchor_y_band")
            if colors_differ(afg, bfg): changed.append("fg")
            if colors_differ(abg, bbg): changed.append("bg")
            if not changed:
                # Same painted result — but did the SUPPLYING texel swap?
                # (Invisible now, but it is the reservoir future flicker
                # draws from, so it is worth reporting.)
                if a1[1] != b1[1] or a1[2] != b1[2]:
                    provenance_only += 1
                continue
            visible_count += 1
            for f in changed:
                field_flips[f] += 1
            if len(flips) < 40:
                flips.append({
                    "frame_a_product_index": a_index,
                    "frame_b_product_index": i,
                    "frame_a_subcell": subcell_from_product_index(a_index, ma["ansi_w"]),
                    "frame_b_subcell": subcell_from_product_index(i, mb["ansi_w"]),
                    "anchor_delta": round(anchor_delta, 6),
                    "fields": changed,
                    "owner": f"{OWNER_NAMES.get(a0[1] & 0xFF, a0[1])}"
                             f"->{OWNER_NAMES.get(b0[1] & 0xFF, b0[1])}",
                    "glyph": f"{a0[2]}->{b0[2]}",
                    "matcell": f"0x{a1[3]:08x}->0x{b1[3]:08x}",
                    "anchor_y_band": f"{math.floor(a_anchor[1])}->{math.floor(b_anchor[1])}",
                })
        worst_pair = max(worst_pair, visible_count)
        pair_reports.append({
            "pair": [a_path.split("/")[-1], b_path.split("/")[-1]],
            "mode": "moving_reprojection" if moving else "frozen",
            "graded_subcells": graded,
            "visible_flips": visible_count,
            "flip_pct": round(100.0 * visible_count / max(graded, 1), 4),
            "field_flips": field_flips,
            "provenance_only_texel_swaps": provenance_only,
            "rejected_self_projection": rejected_self_projection,
            "rejected_anchor_delta": rejected_anchor_delta,
            "rejected_offscreen": rejected_offscreen,
            "samples": flips,
        })

    verdict = "PASS" if worst_pair <= args.max_flips else "FAIL"
    receipt = {"fl": "FL-4359", "kind": "motion_compensated_product_identity_churn_receipt",
               "verdict": verdict, "worst_pair_visible_flips": worst_pair,
               "max_flips_allowed": args.max_flips,
               "max_anchor_delta": args.max_anchor_delta, "pairs": pair_reports}
    if args.out:
        json.dump(receipt, open(args.out, "w"), indent=2)

    print(f"IDENTITY CHURN  verdict={verdict}  worst_pair_visible_flips={worst_pair}")
    for p in pair_reports:
        print(f"  {p['pair'][0]} -> {p['pair'][1]} [{p['mode']}]: {p['visible_flips']} visible flips "
              f"({p['flip_pct']}% of {p['graded_subcells']}), fields={p['field_flips']}, "
              f"provenance-only={p['provenance_only_texel_swaps']}")
        for s in p["samples"][:8]:
            print(f"    products {s['frame_a_product_index']}->{s['frame_b_product_index']} "
                  f"cells {s['frame_a_subcell']}->{s['frame_b_subcell']}: {'+'.join(s['fields'])} "
                  f"owner {s['owner']} glyph {s['glyph']} matcell {s['matcell']} "
                  f"yband {s['anchor_y_band']}")
    if args.out:
        print(f"  receipt: {args.out}")
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())

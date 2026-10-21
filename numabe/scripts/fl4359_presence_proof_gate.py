#!/usr/bin/env python3
# =============================================================================
# fl4359_presence_proof_gate.py
# =============================================================================
# FL-4359 runtime PRESENCE PROOF gate: mesh + foliage in the perspective lane.
#
# WHAT PROBLEM THIS SOLVES
# ------------------------
# After the 52eba7969 card breakthrough, two content owners are "wired in
# source but runtime-unproven":
#   1. Static-world MESHES (tree trunks, rock boulders — owner_kind ==
#      OWNER_STATIC_MESH == 2 in cell_owner_product.glsl) should render in
#      perspective mode.
#   2. FOLIAGE products (grass blades / flowers compiled into the final
#      Product payload by cell_owner_product.glsl) should render.
# Existing dump analyzers COUNT things but never ASSERT anything: there was no
# PASS/FAIL receipt saying "this manual run actually showed mesh/foliage".
# This script is that gate. It reads a moving-cell dump (written by
# _fl4359_maybe_dump_moving_cells in ASCIICompositorEffect.gd) and emits a
# verdict receipt JSON.
#
# WHAT A "MOVING-CELL DUMP" IS
# ----------------------------
# One directory of per-frame triples (plus one optional fourth file):
#   frame_NNN.json          — manifest: ANSI grid size, camera pose, probe
#                             blend state, byte-layout contract
#   frame_NNN_products.bin  — 80 bytes per SUBCELL (4 subcells per ANSI cell):
#                             r0 = uvec4(status, owner_kind, glyph, family), ...
#   frame_NNN_diag.bin      — 16 bytes per subcell; perspective foliage and
#                             flower facts use declared marker bits
#   frame_NNN_owner.bin     — OPTIONAL: the full 368-byte main CellOwner
#                             record. r16 is valid for orthographic comparison,
#                             never as perspective foliage evidence.
# "Subcell" = one quarter of an ANSI character cell (2x2 grid); the probe lane
# resolves at subcell granularity, the product lane at ANSI-cell granularity.
#
# VERDICT SEMANTICS (fail-closed, no fabrication)
# -----------------------------------------------
#   PASS         — enough frames showed the named structural fact.
#   FAIL         — dump decoded fine but the named structural fact was absent.
#   ABI_MISSING  — (foliage only) the dump predates the lane-correct marker
#                  declaration, so presence CANNOT be judged. Distinct from
#                  FAIL so an old dump is never misread as broken foliage.
#   INVALID_LANE — the dump was captured in orthographic mode. These proofs
#                  are PERSPECTIVE-lane claims; an ortho dump proves nothing
#                  here (ortho already renders both owners). Overridable with
#                  --allow-ortho for A/B comparisons only.
#
# PROVENANCE NOTE (binding, from the FL-4359 vocabulary lock)
# -----------------------------------------------------------
# Only dumps captured during an OPERATOR MANUAL RUN (launch via
# scripts/launch_fl4359_manual_play.py, controls enabled) count as acceptance
# evidence. This script cannot verify how the dump was produced; the receipt
# echoes the manifest provenance (camera path, frame indices) so a reviewer
# can cross-check against the operator's session, and records this caveat.
#
# USAGE
#   python3 scripts/fl4359_presence_proof_gate.py \
#       --dump-dir ~/Library/Application\ Support/Godot/app_userdata/.run/fl4359_moving_cell_dump
# Exit codes: 0 = every judged proof PASS; 2 = mesh PASS but foliage
# ABI_MISSING (re-capture with the extended dump); 1 = anything else.
# =============================================================================

import argparse
import json
import struct
import sys
from pathlib import Path

# --- Frozen ABI constants (mirrors of the GDScript/GLSL contract) -----------
# These are COPIES of values pinned in the engine; if the engine changes them
# the manifest stride fields will disagree and this script aborts loudly.
OWNER_TERRAIN = 1              # cell_owner_product.glsl owner_kind enum
OWNER_STATIC_MESH = 2          # cell_owner_product.glsl owner_kind enum
OWNER_DYNAMIC_MESH = 3         # airplane/actors — reported but not gated here
PRODUCT_STRIDE_DEFAULT = 80    # PROBE_SUBCELL_PRODUCT_RECORD_BYTES
CELL_OWNER_STRIDE_DEFAULT = 368  # current main CELL_OWNER_RECORD_BYTES (r0..r22)
CELL_OWNER_R16_OFFSET = 256    # r16 = 16 registers * 16 bytes into the record
SUBCELLS_PER_ANSI_CELL = 4     # 2x2 subcell grid per ANSI character cell

# The final Product ABI stores a versioned status word, not a Boolean. Accepting
# any nonzero word would let a dump from ABI v1, an unknown future version, and
# a corrupt fact bit masquerade as current evidence. These constants exactly
# mirror cell_owner_product.glsl and ascii_compositor.glsl.
FINAL_PRODUCT_STATUS_VALID = 1 << 0
FINAL_PRODUCT_STATUS_FOLIAGE_PRESENT = 1 << 1
FINAL_PRODUCT_STATUS_ABI_MASK = 0xFF000000
FINAL_PRODUCT_STATUS_ABI_V2 = 2 << 24
# FL-4330 foliage role, bits 2..5. This gate never carried the role mask, so it
# has been failing closed on every role-bearing record since FL-4330 landed;
# that omission is corrected here rather than left for the cell mask to inherit.
FINAL_PRODUCT_STATUS_FOLIAGE_ROLE_MASK = 0x0000003C
# FL-4740 presentation cell mask, bits 6..19: bit 6 elected, bit 7 elected mask
# is foliage, bits 8..19 the elected glyph. Mirrors cell_owner_product.glsl and
# ascii_compositor.glsl. A stale mask here makes the presence gate fail closed
# on frames that are in fact correct.
# FL-4848: bits 6..23 are reserved; the elected cell presentation lives only
# in the NativePresentationResult record, never in the b55 status word.
FINAL_PRODUCT_STATUS_KNOWN_MASK = (
    FINAL_PRODUCT_STATUS_ABI_MASK
    | FINAL_PRODUCT_STATUS_VALID
    | FINAL_PRODUCT_STATUS_FOLIAGE_PRESENT
    | FINAL_PRODUCT_STATUS_FOLIAGE_ROLE_MASK
)

# Default install location of Godot userdata dumps on macOS. Kept identical to
# the ad hoc decoders so operators can run this with zero arguments.
DEFAULT_DUMP_DIR = (
    Path.home()
    / "Library/Application Support/Godot/app_userdata/.run/fl4359_moving_cell_dump"
)

RECEIPT_SCHEMA = "fl4359_presence_proof_receipt.v3"


def final_product_status_is_valid(status: int) -> bool:
    """Return true only for a recognized ABI-v2 final Product status."""
    return (
        status & FINAL_PRODUCT_STATUS_VALID != 0
        and status & FINAL_PRODUCT_STATUS_ABI_MASK
        == FINAL_PRODUCT_STATUS_ABI_V2
        and status & ~FINAL_PRODUCT_STATUS_KNOWN_MASK == 0
    )


def load_frames(dump_dir: Path):
    """Collect the per-frame dump triples from a dump directory.

    Returns a list of dicts sorted by capture sequence number, each holding
    the parsed manifest and the paths of the binary payloads. Missing
    lane-correct foliage metadata is handled as ABI_MISSING by build_receipt.
    """
    frames = []
    for meta_path in sorted(dump_dir.glob("frame_*.json")):
        manifest = json.loads(meta_path.read_text())
        # The binaries share the manifest's basename: frame_007.json pairs
        # with frame_007_products.bin / frame_007_owner.bin.
        products_path = meta_path.with_name(meta_path.stem + "_products.bin")
        owner_path = meta_path.with_name(meta_path.stem + "_owner.bin")
        diag_path = meta_path.with_name(meta_path.stem + "_diag.bin")
        diag_owns_perspective_presence = (
            not bool(manifest.get("is_orthogonal", False))
            and "diag_owner_kind_word" in manifest
        )
        if not products_path.exists() and not diag_owns_perspective_presence:
            # A manifest without its products payload is a truncated capture;
            # skipping silently would shrink the denominator, so abort.
            raise SystemExit(f"ABORT: {products_path} missing for {meta_path}")
        if not diag_path.exists():
            raise SystemExit(f"ABORT: {diag_path} missing for {meta_path}")
        frames.append({
            "manifest": manifest,
            "products_path": products_path if products_path.exists() else None,
            "diag_path": diag_path,
            "owner_path": owner_path if owner_path.exists() else None,
        })
    # Sort by the capture sequence number so per-frame rows read in order.
    frames.sort(key=lambda f: int(f["manifest"].get("seq", 0)))
    return frames


def count_mesh_subcells(products: bytes, stride: int):
    """Count ABI-v2-valid subcell products owned by static/dynamic mesh.

    Walks the products buffer record by record. Each record starts with
    r0 = uvec4(status, owner_kind, glyph, family) — four little-endian 32-bit
    unsigned ints. A record counts only when status carries ABI version 2,
    the valid bit, and no unknown fact bits. Status zero remains absence.

    Returns (static_mesh_count, dynamic_mesh_count, owner_histogram).
    """
    static_mesh = 0
    dynamic_mesh = 0
    owner_hist = {}
    for off in range(0, len(products) - stride + 1, stride):
        # '<4I' = four little-endian uint32 = the r0 register of the record.
        status, owner_kind, _glyph, _family = struct.unpack_from(
            "<4I", products, off
        )
        if not final_product_status_is_valid(status):
            continue
        owner_hist[owner_kind] = owner_hist.get(owner_kind, 0) + 1
        if owner_kind == OWNER_STATIC_MESH:
            static_mesh += 1
        elif owner_kind == OWNER_DYNAMIC_MESH:
            dynamic_mesh += 1
    return static_mesh, dynamic_mesh, owner_hist


def count_mesh_diag_subcells(diag: bytes, stride: int, kind_word: int):
    """Count perspective owner kinds from the existing diagnostic kind word."""
    static_mesh = 0
    dynamic_mesh = 0
    owner_hist = {}
    kind_offset = kind_word * 4
    for off in range(0, len(diag) - stride + 1, stride):
        (owner_kind,) = struct.unpack_from("<I", diag, off + kind_offset)
        owner_hist[owner_kind] = owner_hist.get(owner_kind, 0) + 1
        if owner_kind == OWNER_STATIC_MESH:
            static_mesh += 1
        elif owner_kind == OWNER_DYNAMIC_MESH:
            dynamic_mesh += 1
    return static_mesh, dynamic_mesh, owner_hist


def count_foliage_cells(owner_bytes: bytes, stride: int, r16_offset: int):
    """Count ANSI cells whose CellOwner record carries a live foliage fact.

    The foliage fact is packed into r16.x of the 368-byte CellOwner record
    (cell_owner_product.glsl): bit 0 = present, bits 1-3 = role (role 4 means
    flower — the same test ascii_compositor.glsl:412 uses to pick flower
    glyphs). We read only the x component: one uint32 at r16_offset.

    Returns (foliage_count, flower_count).
    """
    foliage = 0
    flowers = 0
    for off in range(0, len(owner_bytes) - stride + 1, stride):
        # r16.x is the first uint of the r16 register (byte offset 256).
        (packed,) = struct.unpack_from("<I", owner_bytes, off + r16_offset)
        if packed & 1:  # bit 0 = foliage product present on this cell
            foliage += 1
            if ((packed >> 1) & 7) == 4:  # bits 1-3 = role; 4 = flower
                flowers += 1
    return foliage, flowers


def count_perspective_foliage_cells(diag_bytes: bytes, stride: int,
                                    foliage_marker_bit: int, flower_marker_bit: int):
    """Count ANSI cells whose resolved perspective payload compiled foliage.

    Diagnostics are per subcell, so four records map to one ANSI cell. A cell
    counts once when any subcell carries the declared foliage marker.
    """
    foliage_cells = set()
    flower_cells = set()
    foliage_mask = 1 << foliage_marker_bit
    flower_mask = 1 << flower_marker_bit
    for record_index, off in enumerate(range(0, len(diag_bytes) - stride + 1, stride)):
        (markers,) = struct.unpack_from("<I", diag_bytes, off + 12)
        cell_index = record_index // SUBCELLS_PER_ANSI_CELL
        if markers & foliage_mask:
            foliage_cells.add(cell_index)
        if markers & flower_mask:
            flower_cells.add(cell_index)
    return len(foliage_cells), len(flower_cells)


def judge(per_frame_counts, min_cells: int, min_frame_fraction: float):
    """Turn per-frame counts into a PASS/FAIL verdict.

    A proof passes when at least `min_frame_fraction` of the captured frames
    each contain `min_cells` or more qualifying cells. The fraction guards
    against a single lucky frame passing a run where the content pops in and
    out (which is exactly the shimmer failure class this FL tracks).
    """
    total = len(per_frame_counts)
    if total == 0:
        return "FAIL", 0  # no frames = nothing proven; fail closed
    meeting = sum(1 for n in per_frame_counts if n >= min_cells)
    verdict = "PASS" if (meeting / total) >= min_frame_fraction else "FAIL"
    return verdict, meeting


def build_receipt(frames, args):
    """Decode every frame and assemble the verdict receipt dictionary."""
    rows = []            # one row per frame: all decoded counts
    foliage_abi_complete = True
    saw_ortho = False
    for f in frames:
        m = f["manifest"]
        if not bool(m.get("is_orthogonal", False)) and "diag_owner_kind_word" in m:
            diag_bytes = f["diag_path"].read_bytes()
            static_mesh, dynamic_mesh, owner_hist = count_mesh_diag_subcells(
                diag_bytes,
                int(m.get("diag_stride", 16)),
                int(m["diag_owner_kind_word"]),
            )
            mesh_evidence_source = "ProbeSubcellDiagnostics"
        else:
            stride = int(m.get("products_stride", PRODUCT_STRIDE_DEFAULT))
            products = f["products_path"].read_bytes()
            static_mesh, dynamic_mesh, owner_hist = count_mesh_subcells(products, stride)
            mesh_evidence_source = "ProbeSubcellProducts"
        row = {
            "seq": m.get("seq"),
            "frame_index": m.get("frame_index"),
            "is_orthogonal": bool(m.get("is_orthogonal", False)),
            "camera_origin": m.get("camera_origin"),
            "static_mesh_subcells": static_mesh,
            "dynamic_mesh_subcells": dynamic_mesh,
            "mesh_evidence_source": mesh_evidence_source,
            # JSON keys must be strings; owner_kind ints become strings here.
            "owner_kind_histogram": {str(k): v for k, v in sorted(owner_hist.items())},
        }
        saw_ortho = saw_ortho or row["is_orthogonal"]
        if not row["is_orthogonal"]:
            if (f["diag_path"] is None
                    or "diag_foliage_marker_bit" not in m
                    or "diag_flower_marker_bit" not in m):
                foliage_abi_complete = False
            else:
                diag_bytes = f["diag_path"].read_bytes()
                foliage, flowers = count_perspective_foliage_cells(
                    diag_bytes,
                    int(m.get("diag_stride", 16)),
                    int(m["diag_foliage_marker_bit"]),
                    int(m["diag_flower_marker_bit"]),
                )
                row["foliage_cells"] = foliage
                row["flower_cells"] = flowers
                row["foliage_evidence_source"] = "ProbeSubcellDiagnostics"
        elif f["owner_path"] is not None:
            owner_bytes = f["owner_path"].read_bytes()
            owner_stride = int(m.get("owner_stride", CELL_OWNER_STRIDE_DEFAULT))
            r16_offset = int(m.get("owner_r16_offset", CELL_OWNER_R16_OFFSET))
            foliage, flowers = count_foliage_cells(owner_bytes, owner_stride, r16_offset)
            row["foliage_cells"] = foliage
            row["flower_cells"] = flowers
            row["foliage_evidence_source"] = "CellOwner.r16"
        else:
            foliage_abi_complete = False
        rows.append(row)

    # --- Lane guard: these are perspective-lane proofs. -----------------
    if saw_ortho and not args.allow_ortho:
        mesh_presence_verdict = mesh_density_verdict = foliage_verdict = "INVALID_LANE"
        mesh_presence_meeting = mesh_density_meeting = foliage_meeting = 0
    else:
        # Structural presence means the perspective lane emitted at least one
        # static-mesh subcell. The former verdict used the density threshold
        # below as the presence owner, so a thin but continuously visible trunk
        # was falsely reported as absent. Density remains useful context, but it
        # cannot decide whether meshes render at all.
        mesh_presence_verdict, mesh_presence_meeting = judge(
            [r["static_mesh_subcells"] for r in rows],
            1, args.min_frame_fraction)
        mesh_density_verdict, mesh_density_meeting = judge(
            [r["static_mesh_subcells"] for r in rows],
            args.min_mesh_subcells, args.min_frame_fraction)
        if not foliage_abi_complete:
            # Old dump ABI: the lane-correct foliage fact never reached disk.
            foliage_verdict, foliage_meeting = "ABI_MISSING", 0
        else:
            foliage_verdict, foliage_meeting = judge(
                [r.get("foliage_cells", 0) for r in rows],
                args.min_foliage_cells, args.min_frame_fraction)

    return {
        "schema": RECEIPT_SCHEMA,
        "fl": "FL-4359",
        "dump_dir": str(args.dump_dir),
        "frames_analyzed": len(rows),
        "thresholds": {
            "min_mesh_presence_subcells": 1,
            "min_mesh_subcells": args.min_mesh_subcells,
            "min_foliage_cells": args.min_foliage_cells,
            "min_frame_fraction": args.min_frame_fraction,
        },
        "verdicts": {
            "mesh_present_in_perspective": mesh_presence_verdict,
            "mesh_density_threshold_met": mesh_density_verdict,
            "foliage_renders_in_perspective": foliage_verdict,
        },
        "frames_meeting_threshold": {
            "mesh_presence": mesh_presence_meeting,
            "mesh_density": mesh_density_meeting,
            "foliage": foliage_meeting,
        },
        # Binding caveat: a receipt is only acceptance evidence when the dump
        # came from an operator manual run (launch_fl4359_manual_play.py).
        # This script cannot verify that — reviewers must cross-check the
        # camera path below against the operator session.
        "provenance_note": (
            "manual-run provenance NOT machine-verified; acceptance requires "
            "operator manual-play capture per FL-4359 vocabulary lock"
        ),
        "per_frame": rows,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="FL-4359 mesh+foliage perspective presence proof gate")
    parser.add_argument("--dump-dir", type=Path, default=DEFAULT_DUMP_DIR,
                        help="moving-cell dump directory (frame_NNN.* files)")
    parser.add_argument("--min-mesh-subcells", type=int, default=8,
                        help="non-blocking per-frame static-mesh density target "
                             "(default 8 = two full ANSI cells of trunk/rock)")
    parser.add_argument("--min-foliage-cells", type=int, default=8,
                        help="per-frame foliage-flagged ANSI cell count required")
    parser.add_argument("--min-frame-fraction", type=float, default=0.9,
                        help="fraction of frames that must meet the threshold; "
                             "0.9 tolerates brief occlusion but fails pop-in/out")
    parser.add_argument("--allow-ortho", action="store_true",
                        help="judge orthographic dumps too (A/B use only; "
                             "ortho frames prove nothing about this FL)")
    parser.add_argument("--out", type=Path, default=None,
                        help="receipt path (default <dump-dir>/fl4359_presence_proof_receipt.json)")
    parser.add_argument("--json", action="store_true",
                        help="also print the full receipt JSON to stdout")
    args = parser.parse_args(argv)

    frames = load_frames(args.dump_dir)
    if not frames:
        print(f"FAIL: no frame_*.json manifests under {args.dump_dir}", file=sys.stderr)
        return 1

    receipt = build_receipt(frames, args)

    out_path = args.out or (args.dump_dir / "fl4359_presence_proof_receipt.json")
    out_path.write_text(json.dumps(receipt, indent=2, sort_keys=False))

    v = receipt["verdicts"]
    print(f"mesh_present_in_perspective:    {v['mesh_present_in_perspective']}")
    print(f"mesh_density_threshold_met:     {v['mesh_density_threshold_met']}")
    print(f"foliage_renders_in_perspective: {v['foliage_renders_in_perspective']}")
    print(f"receipt: {out_path}")
    if args.json:
        print(json.dumps(receipt, indent=2))

    # Exit-code contract (documented in the file header): 0 = all PASS,
    # 2 = mesh presence PASS but foliage unjudgeable on this dump ABI.
    if v["mesh_present_in_perspective"] == "PASS" and v["foliage_renders_in_perspective"] == "PASS":
        return 0
    if v["mesh_present_in_perspective"] == "PASS" and v["foliage_renders_in_perspective"] == "ABI_MISSING":
        return 2
    return 1


if __name__ == "__main__":
    sys.exit(main())

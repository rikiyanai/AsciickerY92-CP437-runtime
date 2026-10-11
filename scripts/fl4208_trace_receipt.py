#!/usr/bin/env python3
"""FL-4208 N0a trace-receipt reader + N0b calibration analyzer.

The native render path writes a versioned binary receipt per frame when
FL4208_TRACE_PATH is set (engine/render/fl4208_surface_identity.h
Fl4208WriteTraceFrame). This is the canonical CONSUMER.

Receipt layout (EXPLICIT little-endian — the C++ writer serializes byte-by-byte,
reviewer flag #5), one or more frames appended:
  8B  magic "FL4208TR"
  u32 version, u32 status (0 OK / 1 INVALID), u64 frame, u64 stamp,
  u32 width, u32 height, u32 anchor_bins, u32 cell_count,
  u32 cells_valid, u32 cells_rejected, u32 records, u32 overflow,
  u8[32] capture_spec_sha256  (v3 provenance digest, round-5 finding #1)
  then cell_count records of: u8 state (0 empty/1 valid/2 rejected)
       + Fl4208SurfaceId (24B): u64 world_generation, u32 owner_kind,
         i32 owner_x, i32 owner_y, u16 anchor_u_q, u16 anchor_v_q
  cell index is row-major: i = y*width + x.

The N0b identity key is ALL SIX Fl4208SurfaceId fields (reviewer flag #1):
(world_generation, owner_kind, owner_x, owner_y, anchor_u_q, anchor_v_q).

N0b metric (reviewer flag #3): screen-cell index diffing is the WRONG measure
because camera motion moves a surface between screen cells. Instead this analyzer
computes, within a single run:
  * same-frame identity COLLISIONS (one identity -> >1 screen cell) — a bins
    candidate with any collision is rejected (identity not unique enough);
  * identity-SET retention across CONSECUTIVE frames R_t = |K_t intersect K_{t-1}|
    over the valid-identity sets, the quantity N0b maximizes.

FAIL-CLOSED (reviewer flags #2/#3/#4/#6): an INVALID frame, unknown magic/version,
truncation, a validation breach (bad dims/bins/states/counts/overflow), a missing
(gapped) frame, or a duplicate frame number is reported and forces a non-zero
exit. None of these may be read as a clean empty terrain run.
"""
import argparse
import json
import struct
import sys

MAGIC = b"FL4208TR"
VERSION = 3          # v3: capture_spec_hash u64 -> capture_spec_sha256[32] (round-5 #1)
STATUS_OK = 0
STATUS_INVALID = 1
ALLOWED_BINS = (4, 8, 16, 32)
OWNER_TERRAIN = 0
RECORD_LIMIT = 57600  # interner live-record cap; records must not exceed it
SPEC_DIGEST_LEN = 32  # SHA-256 provenance digest width
ZERO_DIGEST = b"\x00" * SPEC_DIGEST_LEN

# after magic: ver,status,frame,stamp,w,h,bins,cc,cv,cr,rec,ovf (then 32B sha read separately)
_HDR = struct.Struct("<2I2Q8I")
_CELL_ID = struct.Struct("<QIiiHH")  # 24B Fl4208SurfaceId
_STATE = struct.Struct("<B")


class ReceiptError(Exception):
    """Any structural / validation fault. Always fail-closed (non-zero exit)."""


def _read_exact(buf, off, n):
    if off + n > len(buf):
        raise ReceiptError(f"truncated receipt at offset {off} (need {n}, have {len(buf)-off})")
    return buf[off:off + n], off + n


def parse_frames(buf, want_cells=False):
    """Parse every frame with per-frame structural validation. Cross-frame
    contiguity/uniqueness is enforced by validate_receipt()."""
    if not buf:
        raise ReceiptError("empty receipt (no frames) — fail closed, not an empty run")
    off = 0
    frames = []
    while off < len(buf):
        magic, off = _read_exact(buf, off, 8)
        if magic != MAGIC:
            raise ReceiptError(f"bad magic {magic!r} at frame offset {off-8}")
        raw, off = _read_exact(buf, off, _HDR.size)
        (ver, status, frame, stamp, w, h, bins, cc,
         cv, cr, rec, ovf) = _HDR.unpack(raw)
        cap_sha, off = _read_exact(buf, off, SPEC_DIGEST_LEN)
        cap_sha = bytes(cap_sha)
        if ver != VERSION:
            raise ReceiptError(f"unknown version {ver} (reader supports {VERSION})")
        if status not in (STATUS_OK, STATUS_INVALID):
            raise ReceiptError(f"frame {frame}: unknown status {status}")

        state_hist = {0: 0, 1: 0, 2: 0}
        cells = []          # (x, y, state, key) when want_cells
        valid_keys = {}     # key -> list[(x,y)] for collision + retention
        for i in range(cc):
            st_raw, off = _read_exact(buf, off, _STATE.size)
            state = _STATE.unpack(st_raw)[0]
            id_raw, off = _read_exact(buf, off, _CELL_ID.size)
            wgen, kind, ox, oy, au, av = _CELL_ID.unpack(id_raw)
            if state not in (0, 1, 2):
                raise ReceiptError(f"frame {frame} cell {i}: illegal state {state}")
            if state == 1 and status == STATUS_OK:
                # reviewer flag #3 (round 4): valid-terrain identity invariants
                if wgen == 0:
                    raise ReceiptError(f"frame {frame} cell {i}: valid cell with world_generation 0")
                if kind != OWNER_TERRAIN:
                    raise ReceiptError(f"frame {frame} cell {i}: valid cell owner_kind {kind} != TERRAIN")
                if au >= bins or av >= bins:
                    raise ReceiptError(f"frame {frame} cell {i}: anchor ({au},{av}) >= bins {bins}")
            state_hist[state] += 1
            x = i % w if w else 0
            y = i // w if w else 0
            key = (wgen, kind, ox, oy, au, av)
            if state == 1:
                valid_keys.setdefault(key, []).append((x, y))
            if want_cells:
                cells.append({"x": x, "y": y, "state": state,
                              "world_generation": wgen, "owner_kind": kind,
                              "owner_x": ox, "owner_y": oy,
                              "anchor_u_q": au, "anchor_v_q": av})

        # same-frame collisions: an identity that lands on >1 distinct screen cell
        collisions = {k: v for k, v in valid_keys.items() if len(set(v)) > 1}
        collision_cells = sum(len(set(v)) - 1 for v in collisions.values())

        fr = {
            "frame": frame, "stamp": stamp, "status": status,
            "status_name": "OK" if status == STATUS_OK else "INVALID",
            "width": w, "height": h, "anchor_bins": bins,
            "cell_count": cc, "cells_valid": cv, "cells_rejected": cr,
            "records": rec, "overflow": bool(ovf),
            "capture_spec_sha256": cap_sha.hex(),
            "capture_spec_raw": cap_sha,
            "state_hist": state_hist,
            "key_set": frozenset(valid_keys.keys()),
            "collision_keys": len(collisions),
            "collision_cells": collision_cells,
        }
        if want_cells:
            fr["cells"] = cells
        frames.append(fr)
    return frames


def validate_receipt(frames, expect=None):
    """Strict cross-cutting validation (reviewer flags #2/#4/#6). Raises on any
    breach. INVALID frames are NOT raised here (the caller reports them and exits
    non-zero) so a fail-closed marker is still readable."""
    seen = set()
    for f in frames:
        n = f["frame"]
        if n in seen:
            raise ReceiptError(f"duplicate frame number {n} (stale-run contamination)")
        seen.add(n)
        if f["status"] != STATUS_OK:
            # reviewer flag #3 (round 4): an INVALID marker must carry no cells.
            if f["cell_count"] != 0:
                raise ReceiptError(f"frame {n}: INVALID marker with cell_count {f['cell_count']} != 0")
            continue  # INVALID marker: skip OK-only structural checks
        if f["width"] <= 0 or f["height"] <= 0:
            raise ReceiptError(f"frame {n}: non-positive dims {f['width']}x{f['height']}")
        if f["cell_count"] != f["width"] * f["height"]:
            raise ReceiptError(f"frame {n}: cell_count {f['cell_count']} != w*h {f['width']*f['height']}")
        if f["anchor_bins"] not in ALLOWED_BINS:
            raise ReceiptError(f"frame {n}: anchor_bins {f['anchor_bins']} not in {ALLOWED_BINS}")
        if f["cells_valid"] != f["state_hist"][1]:
            raise ReceiptError(f"frame {n}: cells_valid {f['cells_valid']} != state==1 count {f['state_hist'][1]}")
        if f["cells_rejected"] != f["state_hist"][2]:
            raise ReceiptError(f"frame {n}: cells_rejected {f['cells_rejected']} != state==2 count {f['state_hist'][2]}")
        if f["records"] > RECORD_LIMIT:
            raise ReceiptError(f"frame {n}: records {f['records']} > limit {RECORD_LIMIT}")
        if f["overflow"]:
            raise ReceiptError(f"frame {n}: interner overflow (fail closed)")
    # contiguity: frame numbers must be 0..N-1 with no gaps
    nums = sorted(seen)
    for idx, n in enumerate(nums):
        if idx != n:
            raise ReceiptError(f"non-contiguous frames: expected {idx}, got {n} (missing-frame gap)")
    if expect is not None and len(frames) != expect:
        raise ReceiptError(f"expected {expect} frames, got {len(frames)}")
    # round-5 finding #2: within ONE receipt, dims / anchor_bins / provenance
    # digest must be constant across EVERY frame (a receipt is one capture; a
    # frame that changes them is a spliced/contaminated file). The writer emits
    # real dims even on an INVALID marker, so this is checked over all frames.
    if frames:
        ref = (frames[0]["width"], frames[0]["height"], frames[0]["anchor_bins"],
               frames[0]["capture_spec_raw"])
        for f in frames:
            cur = (f["width"], f["height"], f["anchor_bins"], f["capture_spec_raw"])
            if cur != ref:
                raise ReceiptError(
                    f"frame {f['frame']}: within-receipt drift "
                    f"dims/bins/spec {cur[:3]}/{cur[3].hex()[:12]} != "
                    f"{ref[:3]}/{ref[3].hex()[:12]}")


def retention(frames):
    """R_t = |K_t intersect K_{t-1}| over consecutive OK frames (reviewer #3)."""
    out = []
    ok = [f for f in frames if f["status"] == STATUS_OK]
    for i in range(1, len(ok)):
        prev, cur = ok[i - 1]["key_set"], ok[i]["key_set"]
        inter = len(cur & prev)
        out.append({
            "frame": ok[i]["frame"], "R_t": inter,
            "k_prev": len(prev), "k_cur": len(cur),
            "ratio": (inter / len(prev)) if prev else 0.0,
        })
    return out


def receipt_summary(buf, expect=None):
    """Parse + validate one receipt; return analyzer scores. Raises ReceiptError."""
    frames = parse_frames(buf)
    validate_receipt(frames, expect=expect)
    rets = retention(frames)
    total_coll = sum(f["collision_cells"] for f in frames if f["status"] == STATUS_OK)
    invalid = [f["frame"] for f in frames if f["status"] != STATUS_OK]
    mean_ret = (sum(r["R_t"] for r in rets) / len(rets)) if rets else 0.0
    return frames, {
        "frames": len(frames),
        "invalid_frames": invalid,
        "anchor_bins": frames[0]["anchor_bins"] if frames else None,
        "width": frames[0]["width"] if frames else None,
        "height": frames[0]["height"] if frames else None,
        "capture_spec_sha256": frames[0]["capture_spec_sha256"] if frames else None,
        "collision_cells_total": total_coll,
        "collision_free": total_coll == 0,
        "mean_retention": mean_ret,
        "retention": rets,
    }


def _emit(obj, as_json):
    print(json.dumps(obj, indent=2, default=list) if as_json else obj)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Read/validate an FL-4208 trace receipt; analyze for N0b.")
    ap.add_argument("path", nargs="+", help="receipt file(s); 2+ with --calibrate")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--cells", action="store_true", help="include per-cell identities")
    ap.add_argument("--expect", type=int, help="require exactly N frames (fail-closed)")
    ap.add_argument("--calibrate", action="store_true",
                    help="rank multiple per-bins receipts: reject any with collisions, pick max retention")
    args = ap.parse_args(argv)

    try:
        if args.calibrate:
            if len(args.path) < 2:
                print("--calibrate needs 2+ receipts (one per ANCHOR_BINS candidate)", file=sys.stderr)
                return 4
            # round-5 finding #3: retention needs >=2 frames per receipt, so a
            # one-frame sweep (R_t undefined, mean 0 for all) must not silently
            # tie-break to bins=4. Require --expect (the frozen capture-spec frame
            # count) and at least two frames.
            if args.expect is None or args.expect < 2:
                print("--calibrate requires --expect N with N>=2 (per-receipt frame count)",
                      file=sys.stderr)
                return 4
            ranked = []
            for p in args.path:
                with open(p, "rb") as fh:
                    _, s = receipt_summary(fh.read(), expect=args.expect)
                s["path"] = p
                ranked.append(s)
            # reviewer flag #2 (round 4): receipts are only comparable with shared
            # provenance. Require exactly the {4,8,16,32} sweep, and identical frame
            # count / dims / capture-spec hash across all of them. Reject on mismatch.
            bins_seen = sorted(s["anchor_bins"] for s in ranked)
            if bins_seen != sorted(ALLOWED_BINS):
                raise ReceiptError(f"--calibrate needs exactly one receipt per {ALLOWED_BINS}; got bins {bins_seen}")
            prov = {(s["frames"], s["width"], s["height"], s["capture_spec_sha256"]) for s in ranked}
            if len(prov) != 1:
                raise ReceiptError(f"--calibrate receipts disagree on frame-count/dims/capture-spec: {sorted(prov)}")
            spec_hex = next(iter(prov))[3]
            if spec_hex in (None, ZERO_DIGEST.hex()):
                raise ReceiptError("--calibrate requires a non-zero capture_spec_sha256 (set FL4208_CAPTURE_SPEC)")
            survivors = [s for s in ranked if s["collision_free"] and not s["invalid_frames"]]
            # reviewer flag #1 (round 4): deterministic tie-break — higher mean
            # retention first, then the SMALLER anchor_bins.
            survivors.sort(key=lambda s: (-s["mean_retention"], s["anchor_bins"]))
            report = {
                "candidates": [{"path": s["path"], "anchor_bins": s["anchor_bins"],
                                "collision_free": s["collision_free"],
                                "collision_cells_total": s["collision_cells_total"],
                                "mean_retention": s["mean_retention"],
                                "invalid_frames": s["invalid_frames"]} for s in ranked],
                # CANDIDATE only — freezing requires real VPS-captured receipts and a
                # passing N0a byte-identity proof (the other lane), not this analyzer.
                "best_candidate_bins": survivors[0]["anchor_bins"] if survivors else None,
                "capture_spec_sha256": spec_hex,
                "note": "candidate ranking only; N0b freeze is gated on N0a byte-identity proof",
            }
            _emit(report, args.json)
            return 0 if survivors else 2

        rc = 0
        for p in args.path:
            with open(p, "rb") as fh:
                buf = fh.read()
            frames, s = receipt_summary(buf, expect=args.expect)
            if args.cells:
                frames = parse_frames(buf, want_cells=True)
            if s["invalid_frames"]:
                rc = max(rc, 2)
            if args.json:
                _emit({"path": p, "summary": s,
                       "frames": frames if args.cells else None}, True)
            else:
                for f in frames:
                    print(f"frame {f['frame']} [{f['status_name']}] {f['width']}x{f['height']} "
                          f"bins={f['anchor_bins']} valid={f['cells_valid']} "
                          f"rejected={f['cells_rejected']} records={f['records']} "
                          f"collisions={f['collision_cells']} overflow={f['overflow']}")
                for r in s["retention"]:
                    print(f"  retention@{r['frame']}: R_t={r['R_t']} of k_prev={r['k_prev']} "
                          f"(ratio={r['ratio']:.3f})")
                print(f"{p}: frames={s['frames']} invalid={len(s['invalid_frames'])} "
                      f"collision_free={s['collision_free']} mean_retention={s['mean_retention']:.3f}")
        return rc
    except ReceiptError as e:
        print(f"FL-4208 trace receipt FAIL-CLOSED: {e}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())

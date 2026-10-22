#!/usr/bin/env python3
"""FL-4798: publish the runtime proof that a map's authored fixtures are restored.

Input: one or more frozen-pose capture directories written by Main.gd's
``--fl4359-operator-ab=<dir>`` mode on the SBU map (``operator_baseline_frame_NN.png``
plus the run log with the FL4789_CATALOG_FOR_MAP / FL4798_COMPILED_FIXTURES /
FL4798_ACTIVE_FIXTURES lines).  Output: one labelled PNG per pose, one GIF
cycling every pose, each frame carrying the visible identity the Godot proof
publication contract requires (map, catalog hash, pose, fixture counts,
capture state/commit, cell and glyph-source dimensions, run, role, frame),
plus the publication manifest written, validated and Finder-tagged through
``scripts/godot_proof_publication.py``.

Usage:
  python3 scripts/fl4798_map_fixture_proof_publish.py \
      --pose bench=/tmp/fl4798/ab_bench:/tmp/fl4798/ab_bench.log \
      --pose planter=/tmp/fl4798/ab_planter:/tmp/fl4798/ab_planter.log \
      --map godot_project/assets/maps/sbu_carto_scale225_20260601.a3d \
      --out /abs/archive_dir --run-id sbu-fixtures --commit <40hex> --state DIRTY
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from godot_proof_publication import (  # noqa: E402
    apply_finder_tags,
    deterministic_artifact_name,
    publish_artifacts,
    sha256_file,
    validate_manifest,
)

GODOT_BIN = os.environ.get("GODOT_BIN", "/opt/homebrew/bin/godot")
FONT = ROOT / "assets/fonts/DejaVuSansMono-2.35.ttf"
ARCH = "FL4798-MAP-FIXTURE-CATALOG"
BAND = 104
FOOT = 24


def _font(sz: int):
    try:
        return ImageFont.truetype(str(FONT), sz)
    except OSError:
        return ImageFont.load_default()


def _log_facts(log_path: Path) -> dict:
    text = log_path.read_text(errors="replace") if log_path.is_file() else ""
    facts = {"catalog": "", "compiled_fixtures": -1, "compiled_fixture_faces": -1,
             "active_fixtures": -1, "active_fixture_faces": -1, "catalog_hash": "",
             "map_override": "", "patches": ""}
    m = re.search(r"FL4789_CATALOG_FOR_MAP map=(\S+) catalog=(\S+)", text)
    if m:
        facts["catalog"] = m.group(2)
    m = re.search(r"FL4798_COMPILED_FIXTURES count=(\d+) faces=(\d+) catalog_hash=([0-9a-f]+)", text)
    if m:
        facts["compiled_fixtures"] = int(m.group(1))
        facts["compiled_fixture_faces"] = int(m.group(2))
        facts["catalog_hash"] = m.group(3)
    for m in re.finditer(r"FL4798_ACTIVE_FIXTURES count=(\d+) faces=(\d+)", text):
        facts["active_fixtures"] = int(m.group(1))
        facts["active_fixture_faces"] = int(m.group(2))
    m = re.search(r"PLAYABLE_MAP_OVERRIDE (\S+)", text)
    if m:
        facts["map_override"] = m.group(1)
    m = re.search(r"imported (\d+) patches", text)
    if m:
        facts["patches"] = m.group(1)
    return facts


def _band_lines(meta: dict, pose: dict, role: str, i: int, n: int) -> list[str]:
    f = pose["facts"]
    return [
        f"{pose['label']}",
        f"FL-4798 · Godot {meta['runtime_identity']['engine_version']} runtime · map {Path(meta['map']).name} "
        f"({f['patches']} patches) · FG {meta['foreground_mode']} / BG {meta['background_mode']} · ARCH {meta['architecture']}",
        f"state {meta['capture_source_state']} @ {meta['source_commit'][:12]} · catalog {f['catalog_hash'][:12]} · "
        f"fixtures compiled {f['compiled_fixtures']} ({f['compiled_fixture_faces']} faces) active {f['active_fixtures']} "
        f"({f['active_fixture_faces']} faces) · cell {meta['cell_resolution']['width']}x{meta['cell_resolution']['height']} · "
        f"run {meta['run_id']} · role {role}",
        f"pose {pose['key']} · player {pose['ppos']} · eye {pose['cpos']} · pitch {pose['pitch']} · yaw {pose['yaw']} · "
        f"frozen frame {i}/{n - 1} · chroma baseline · day-cycle {meta['day_cycle']}",
    ]


def _wrap(text: str, width_px: int, font) -> list[str]:
    """Wrap on ' · ' and spaces so every label line stays inside the frame width."""
    words = text.split(" ")
    lines, cur = [], ""
    for w in words:
        cand = (cur + " " + w).strip()
        if cur and font.getlength(cand) > width_px:
            lines.append(cur)
            cur = w
        else:
            cur = cand
    if cur:
        lines.append(cur)
    return lines


def _labelled(meta: dict, pose: dict, role: str, i: int, n: int) -> Image.Image:
    src = Image.open(pose["frames"][i]).convert("RGB")
    big, small, foot = _font(18), _font(13), _font(12)
    wrapped: list[tuple[str, object, int]] = []
    for j, line in enumerate(_band_lines(meta, pose, role, i, n)):
        font = big if j == 0 else small
        for part in _wrap(line, src.width - 16, font):
            wrapped.append((part, font, 24 if j == 0 else 18))
    band = 6 + sum(h for _, _, h in wrapped) + 6
    footer = _wrap("source: Main.gd --fl4359-operator-ab frozen pose · compiled by scripts/compile_fl4789_chunk_catalog.py "
                   "(authored fixtures on by default) · fixture faces = authored .akm geometry, material 240 authored-colour lane",
                   src.width - 16, foot)
    foot_h = 4 + 16 * len(footer) + 4
    out = Image.new("RGB", (src.width, band + src.height + foot_h), (0, 0, 0))
    out.paste(src, (0, band))
    d = ImageDraw.Draw(out)
    y = 6
    for text, font, h in wrapped:
        d.text((8, y), text, font=font, fill=(230, 230, 230))
        y += h
    d.line([(0, band - 1), (out.width, band - 1)], fill=(70, 70, 70))
    y = band + src.height + 4
    for text in footer:
        d.text((8, y), text, font=foot, fill=(160, 160, 160))
        y += 16
    return out


def _pad(im: Image.Image, h: int) -> Image.Image:
    out = Image.new("RGB", (im.width, h), (0, 0, 0))
    out.paste(im, (0, 0))
    return out


def _fingerprint(sources: list[dict]) -> str:
    h = hashlib.sha256()
    for s in sorted(sources, key=lambda x: x["path"]):
        h.update(f"{s['path']}:{s['sha256']}\n".encode())
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--pose", action="append", required=True, help="KEY=/abs/capture_dir:/abs/run.log")
    ap.add_argument("--label", action="append", default=[], help="KEY=caption label")
    ap.add_argument("--pose-spec", action="append", default=[], help="KEY=ppx,ppy,ppz:cx,cy,cz:pitch:yaw")
    ap.add_argument("--map", required=True)
    ap.add_argument("--catalog", default="", help="repo-relative catalog path (default: <map_stem>.fl4789_chunk_catalog.v1.json)")
    ap.add_argument("--payload", default="", help="repo-relative payload path")
    ap.add_argument("--manifest", default="", help="repo-relative FL-4708 manifest path")
    ap.add_argument("--ab", action="append", default=[], help="OFFKEY:ONKEY pose pair rendered side by side (fixtures off | on)")
    ap.add_argument("--extra-source", action="append", default=[], help="additional repo-relative executed source to attest")
    ap.add_argument("--arch", default=ARCH, help="architecture tag for the label band and artifact names")
    ap.add_argument("--out", required=True)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--commit", required=True)
    ap.add_argument("--state", choices=("CLEAN", "DIRTY", "PATCHED"), required=True)
    ap.add_argument("--day-cycle", default="0.5 noon (--fl4408-day-cycle-time)")
    ap.add_argument("--duration-ms", type=int, default=1400)
    ap.add_argument("--failure-log", default="docs/FAILURE_LOG.md")
    ap.add_argument("--godot-goal", default="docs/plans/GODOT_GOAL.MD")
    args = ap.parse_args()

    map_path = Path(args.map)
    map_rel = str(map_path.relative_to(ROOT)) if map_path.is_absolute() else str(map_path)
    stem = map_path.stem
    catalog_rel = args.catalog or f"godot_project/assets/world_partition/{stem}.fl4789_chunk_catalog.v1.json"
    payload_rel = args.payload or f"godot_project/assets/world_partition/{stem}.fl4789_chunk_assets.v1.bin"
    manifest_rel = args.manifest or f"godot_project/assets/world_mesh/{stem}.fl4708_tree_instances.v1.json"
    executed = [
        "godot_project/scripts/World.gd",
        "godot_project/scripts/map/PlayableMap.gd",
        "godot_project/scripts/world/WorldPartition.gd",
        "godot_project/render/product/cell_owner_product.glsl",
        "scripts/compile_fl4708_y8_tree_placements.py",
        "scripts/compile_fl4789_chunk_catalog.py",
        map_rel, manifest_rel, catalog_rel, payload_rel,
        *args.extra_source,
    ]
    sources = [{"path": p, "sha256": sha256_file(ROOT / p)} for p in executed]
    labels = {}
    for spec in args.label:
        k, v = spec.split("=", 1)
        labels[k] = v
    specs = {}
    for spec in args.pose_spec:
        k, v = spec.split("=", 1)
        pp, cp, pitch, yaw = v.split(":")
        specs[k] = {"ppos": pp, "cpos": cp, "pitch": pitch, "yaw": yaw}
    poses = []
    for spec in args.pose:
        k, rest = spec.split("=", 1)
        d, log = rest.split(":", 1)
        frames = sorted(Path(d).glob("operator_baseline_frame_*.png"))
        if not frames:
            print("no frames in", d)
            return 1
        poses.append({"key": k, "label": labels.get(k, k), "frames": frames, "facts": _log_facts(Path(log)),
                      **specs.get(k, {"ppos": "?", "cpos": "?", "pitch": "?", "yaw": "?"})})
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    first = Image.open(poses[0]["frames"][0])
    catalog_header = json.loads((ROOT / catalog_rel).read_text())["header"]
    meta = {
        "provenance_status": "ATTESTED",
        "fl_id": "FL-4798",
        "run_id": args.run_id,
        "proof_kind": "RENDER",
        "architecture": args.arch,
        "foreground_mode": "FLAT",
        "background_mode": "FLAT",
        "capture_source_state": args.state,
        "source_commit": args.commit,
        "effective_tree_fingerprint": _fingerprint(sources),
        "executed_sources": sources,
        "build_identity": {"kind": "godot-binary", "path": GODOT_BIN, "sha256": sha256_file(GODOT_BIN)},
        "runtime_identity": {"target": "macos-arm64-headed-window", "engine_version": "4.6.2.stable"},
        "workload_identity": {
            "runner": "godot_project/scripts/Main.gd --fl4359-operator-ab frozen-pose capture (offline, no Colyseus actor)",
            "scene": "Main.tscn full world: PlayableMap --map override + World retained static world + ASCII compositor",
            "map": map_rel,
            "pose": "; ".join(f"{p['key']}: player {p['ppos']} eye {p['cpos']} pitch {p['pitch']} yaw {p['yaw']}" for p in poses),
            "camera": f"absolute Camera3D transform, {first.width}x{first.height} px capture",
            "timing": "2 frozen frames per pose after 90 warmup frames; consecutive frames identical pose",
        },
        "map": map_rel,
        "day_cycle": args.day_cycle,
        "catalog": {"path": catalog_rel, "catalog_hash": catalog_header["catalog_hash"],
                    "fixture_placement_count": catalog_header.get("fixture_placement_count", 0),
                    "fixture_mesh_sources": catalog_header.get("fixture_mesh_sources", [])},
        "cell_resolution": {"width": 16, "height": 32},
        "glyph_source_resolution": [{"width": 32, "height": 32}],
        "poses": {p["key"]: {"dir": str(p["frames"][0].parent), "facts": p["facts"], "ppos": p["ppos"], "cpos": p["cpos"],
                             "pitch": p["pitch"], "yaw": p["yaw"]} for p in poses},
        "storage": {"status": "PENDING"},
    }
    artifacts = []
    receipts = out / "receipts"
    receipts.mkdir(exist_ok=True)
    for p in poses:
        n = len(p["frames"])
        role = f"pose-{p['key']}-still"
        im = _labelled(meta, p, role, 0, n)
        rec = {"role": role, "frame_count": 1, "extension": ".png",
               "image_dimensions": {"width": im.width, "height": im.height}}
        name = deterministic_artifact_name(meta, rec)
        im.save(out / name)
        rec["path"] = str(out / name)
        artifacts.append(rec)
        rd = receipts / f"pose_{p['key']}"
        rd.mkdir(exist_ok=True)
        for fp in p["frames"]:
            shutil.copy2(fp, rd / fp.name)
        log_src = Path(args.pose[[q["key"] for q in poses].index(p["key"])].split("=", 1)[1].split(":", 1)[1])
        if log_src.is_file():
            shutil.copy2(log_src, rd / "run.log")
    by_key = {p["key"]: p for p in poses}
    for pair in args.ab:
        off_key, on_key = pair.split(":")
        off, on = by_key[off_key], by_key[on_key]
        role = f"ab-{on_key}"
        a = _labelled(meta, off, role, 0, len(off["frames"]))
        b = _labelled(meta, on, role, 0, len(on["frames"]))
        hh = max(a.height, b.height)
        a, b = _pad(a, hh), _pad(b, hh)
        im = Image.new("RGB", (a.width + 16 + b.width, a.height + FOOT), (0, 0, 0))
        im.paste(a, (0, 0))
        im.paste(b, (a.width + 16, 0))
        d = ImageDraw.Draw(im)
        d.line([(a.width + 8, 0), (a.width + 8, im.height)], fill=(70, 70, 70))
        d.text((8, a.height + 4),
               f"left: {off['label']}   right: {on['label']}   same map terrain, meadow, pose, day-cycle and frame; "
               f"only the compiled catalog's authored_fixture family differs   role {role}", font=_font(14), fill=(160, 160, 160))
        rec = {"role": role, "frame_count": 1, "extension": ".png",
               "image_dimensions": {"width": im.width, "height": im.height}}
        name = deterministic_artifact_name(meta, rec)
        im.save(out / name)
        rec["path"] = str(out / name)
        artifacts.append(rec)
    role = "poses-cycle"
    frames = []
    for p in poses:
        n = len(p["frames"])
        for i in range(n):
            frames.append(_labelled(meta, p, role, i, n))
    h = max(f.height for f in frames)
    frames = [f if f.height == h else _pad(f, h) for f in frames]
    rec = {"role": role, "frame_count": len(frames), "extension": ".gif",
           "image_dimensions": {"width": frames[0].width, "height": frames[0].height}}
    name = deterministic_artifact_name(meta, rec)
    frames[0].save(out / name, save_all=True, append_images=frames[1:], duration=args.duration_ms, loop=0)
    rec["path"] = str(out / name)
    artifacts.append(rec)

    manifest_path = out / "publication_manifest.json"
    receipt = receipts / f"pose_{poses[0]['key']}" / "run.log"
    meta["storage"] = {
        "status": "TRACKED",
        "location": str(out.relative_to(ROOT)) if out.is_relative_to(ROOT) else str(out),
        "failure_log": args.failure_log,
        "godot_goal": args.godot_goal,
        "receipt_path": str(receipt.relative_to(ROOT)) if receipt.is_relative_to(ROOT) else str(receipt),
        "receipt_sha256": sha256_file(receipt) if receipt.is_file() else "",
    }
    publish_artifacts(manifest_path, meta, artifacts, artifact_root=out)
    errors = validate_manifest(manifest_path)
    if errors:
        for e in errors if isinstance(errors, list) else [errors]:
            print("VALIDATION", e)
    tags = apply_finder_tags(manifest_path)
    print(f"published {len(artifacts)} artifacts -> {manifest_path}")
    for a in artifacts:
        print("  ", Path(a["path"]).name)
    print(f"finder tags on {len(tags)} files; validation errors: {len(errors) if errors else 0}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())

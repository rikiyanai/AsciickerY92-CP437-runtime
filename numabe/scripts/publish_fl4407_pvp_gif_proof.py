#!/usr/bin/env python3
"""FL-4407: publish the two-client PvP screen-recording proof (raw .mov -> GIF under 100 MB).

The recording is produced outside Godot by scripts/supervise_screen_recording.py
around the headed real client A; both clients run
godot_project/scripts/proofs/fl4407_pvp_bot.gd. This publisher never captures
anything: it derives one indexed-colour GIF from the raw .mov, records the
executed-source identity of the checkout that ran, writes the
godot-proof-publication.v1 manifest, validates it, applies and verifies Finder
tags, and builds the labeled viewing copy. Publication success is not render
acceptance.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from godot_proof_publication import (  # noqa: E402
    apply_finder_tags, deterministic_artifact_name, publish_artifacts, sha256_file,
    validate_manifest, verify_finder_tags,
)

GODOT_BIN = "/opt/homebrew/bin/godot"
GIF_LIMIT_BYTES = 100 * 1024 * 1024
GIF_LADDER = ((10, 640, 96), (8, 640, 80), (8, 560, 64), (6, 480, 64))
MODE = {"F": "FLAT", "C": "CONTINUOUS"}


def _fingerprint(sources: list[dict]) -> str:
    import hashlib
    h = hashlib.sha256()
    for s in sorted(sources, key=lambda x: x["path"]):
        h.update(f"{s['path']}:{s['sha256']}\n".encode())
    return h.hexdigest()


def _executed_sources() -> list[dict]:
    patterns = ["colyseus_server/src", "godot_project/scripts", "godot_project/shaders", "godot_project/scenes",
                "godot_project/project.godot", "scripts/supervise_screen_recording.py"]
    listed = subprocess.run(["git", "ls-files", "--", *patterns], capture_output=True, text=True, cwd=ROOT).stdout.split()
    listed.append("godot_project/scripts/proofs/fl4407_pvp_bot.gd")
    out = []
    for rel in sorted(set(listed)):
        p = ROOT / rel
        if p.is_file() and p.suffix in {".ts", ".gd", ".glsl", ".glslinc", ".gdshader", ".tscn", ".godot", ".py"}:
            out.append({"path": rel, "sha256": sha256_file(p)})
    return out


def _gif(mov: Path, gif: Path, fps: int, width: int, colors: int, start: float = 0.0, seconds: float = 0.0) -> None:
    vf = (f"fps={fps},scale={width}:-2:flags=lanczos,split[a][b];"
          f"[a]palettegen=max_colors={colors}:stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=3:diff_mode=rectangle")
    trim = (["-ss", f"{start:.3f}"] if start > 0 else []) + (["-t", f"{seconds:.3f}"] if seconds > 0 else [])
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *trim, "-i", str(mov), "-vf", vf, "-loop", "0", str(gif)], check=True)


def _probe(mov: Path) -> dict:
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                        "stream=width,height,r_frame_rate,nb_frames:format=duration,size", "-of", "json", str(mov)],
                       capture_output=True, text=True, check=True)
    return json.loads(r.stdout)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--run-dir", required=True, help=".run/fl4407_pvp_gif_20260910")
    ap.add_argument("--out", required=True, help="artifacts/fl4407/pvp-gif-20260910")
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--state", choices=("CLEAN", "DIRTY", "PATCHED"), required=True)
    ap.add_argument("--gif-start", type=float, default=0.0, help="seconds into the raw recording where the GIF starts")
    ap.add_argument("--gif-seconds", type=float, default=0.0, help="GIF length in seconds (0 = to the end)")
    args = ap.parse_args()
    run = Path(args.run_dir).resolve()
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    receipt = json.loads((run / "receipt.json").read_text())
    assert "FL4407_RIG_GLYPH_GRID_SELECTED cell_px=12" in (run / "godot_A.log").read_text(), "cell footprint evidence line missing from godot_A.log"
    conn_a = json.loads((run / "A/connected.json").read_text())
    wit_a = json.loads((run / "A/witness.json").read_text())
    wit_b = json.loads((run / "B/witness.json").read_text())
    mov = run / "fl4407_pvp_A_screen.mov"
    probe = _probe(mov)
    mode = str(conn_a.get("presentation_mode", "")).upper()
    fg, bg = (MODE.get(mode[0], "UNKNOWN"), MODE.get(mode[1], "UNKNOWN")) if len(mode) == 2 else ("UNKNOWN", "UNKNOWN")
    sources = _executed_sources()
    meta = {
        "provenance_status": "ATTESTED",
        "fl_id": "FL-4407",
        "run_id": args.run_id,
        "proof_kind": "RENDER",
        # FL-4407 rig lane: ASCIICompositorEffect selects cell_h=12 when
        # --sekiro-combat-spike and --sekiro-rig-poses are both present
        # (godot_A.log: FL4407_RIG_GLYPH_GRID_SELECTED cell_px=12) and the
        # default "terminal" aspect makes cell_w = cell_h / 2.
        "cell_resolution": {"width": 6, "height": 12},
        "cell_resolution_evidence": "godot_A.log FL4407_RIG_GLYPH_GRID_SELECTED cell_px=12; ASCIICompositorEffect.gd set_cell_aspect terminal => cell_w = 6",
        "glyph_source_resolution": [{"width": 16, "height": 32}],
        "glyph_source_evidence": "godot_project/assets/glyphs/terminal/manifest.json cell_size [16, 32]",
        "architecture": "COLYSEUS-GODOT",
        "foreground_mode": fg,
        "background_mode": bg,
        "capture_source_state": args.state,
        "source_commit": receipt["head"],
        "effective_tree_fingerprint": _fingerprint(sources),
        "executed_sources": sources,
        "build_identity": {"kind": "godot-binary", "path": GODOT_BIN, "sha256": sha256_file(GODOT_BIN)},
        "runtime_identity": {"target": "macos-arm64-headed-window-x2-clients", "engine_version": "4.6.2.stable",
                             "colyseus": "local npm start, " + str(receipt.get("server_env", "COLYSEUS_PORT=2674 PLAYER_SPAWN_X=20 PLAYER_SPAWN_Z=20"))},
        "workload_identity": {
            "runner": "two real Godot clients via --script res://scripts/proofs/fl4407_pvp_bot.gd; screen recorded from outside by scripts/supervise_screen_recording.py + /usr/sbin/screencapture -R (client A window region)",
            "scene": "Main.tscn real client, --colyseus-client --port=2674 --sekiro-combat-spike --sekiro-rig-poses; client A adds " + " ".join(receipt.get("client_a_extra", [])),
            "map": "assets/maps/game_map_y8.a3d (server default), sekiro_spike room, spawn (20, 20) with three training NPCs at spawn offsets",
            "pose": f"bot A vs bot B PvP; A attack_ticks={wit_a['my_attack_ticks']} hurt_ticks={wit_a['my_hurt_ticks']} target_hurt_ticks={wit_a['target_hurt_ticks']} deaths={wit_a['my_deaths']}/{wit_a['target_deaths']}; B attack_ticks={wit_b['my_attack_ticks']} target_hurt_ticks={wit_b['target_hurt_ticks']}",
            "camera": "client A real chase camera (ortho); bot pressed runtime_zoom_in per client_a_extra (zoom.json records _runtime_zoom_scale); HUD hidden via the F3 target; compositor day clock pinned by --fl4408-day-cycle-time; window " + json.dumps(conn_a.get("window", {})) + "; region " + str(receipt.get("a_bounds_points")),
            "timing": f"screencapture native {probe['streams'][0].get('r_frame_rate')} fps, {float(probe['format']['duration']):.1f} s; bot duration {wit_a['duration_s']} s after both clients connected",
        },
        "recorder": {"command_file": "cmd_supervisor.txt", "supervisor_status": "fl4407_pvp_A_screen.mov.supervisor.json"},
        "raw_video": {"path": str(mov.relative_to(ROOT)) if mov.is_relative_to(ROOT) else str(mov), "sha256": sha256_file(mov),
                      "size_bytes": mov.stat().st_size, "probe": probe},
        "storage": {"status": "PENDING"},
    }
    artifacts = []
    # 1. GIF derived from the raw recording, stepping down the ladder until under the 100 MB limit.
    gif_tmp = out / "screen.tmp.gif"
    chosen = None
    global GIF_LADDER
    for fps, width, colors in GIF_LADDER:
        _gif(mov, gif_tmp, fps, width, colors, args.gif_start, args.gif_seconds)
        if gif_tmp.stat().st_size <= GIF_LIMIT_BYTES:
            chosen = (fps, width, colors)
            break
    if chosen is None:
        print("GIF could not be brought under 100 MB", file=sys.stderr)
        return 2
    from PIL import Image
    with Image.open(gif_tmp) as im:
        rec = {"role": "SCREEN-GIF", "frame_count": int(getattr(im, "n_frames", 1)), "extension": ".gif",
               "image_dimensions": {"width": im.width, "height": im.height}}
    print(f"GIF ladder step {chosen}: {gif_tmp.stat().st_size} bytes")
    meta["gif_encoding"] = {"fps": chosen[0], "width": chosen[1], "max_colors": chosen[2], "dither": "bayer scale 3",
                            "raw_start_seconds": args.gif_start, "seconds": args.gif_seconds or "to_end"}
    name = deterministic_artifact_name(meta, rec)
    gif_tmp.rename(out / name)
    rec["path"] = str(out / name)
    artifacts.append(rec)
    # 2. Witnesses, receipts, commands, logs — copied verbatim under canonical names.
    copies = [("WITNESS-A", run / "A/witness.json"), ("WITNESS-B", run / "B/witness.json"),
              ("CONNECTED-A", run / "A/connected.json"), ("CONNECTED-B", run / "B/connected.json"),
              ("STARTED-A", run / "A/started.json"), ("STARTED-B", run / "B/started.json"),
              ("RECEIPT", run / "receipt.json"), ("CMD-A", run / "cmd_A.txt"), ("CMD-B", run / "cmd_B.txt"),
              ("CMD-SUPERVISOR", run / "cmd_supervisor.txt"), ("SUPERVISOR-STATUS", run / "fl4407_pvp_A_screen.mov.supervisor.json"),
              ("LOG-A", run / "godot_A.log"), ("LOG-B", run / "godot_B.log"), ("LOG-SERVER", run / "colyseus.log"),
              ("LOG-SUPERVISOR", run / "supervisor.log"), ("BOT-SCRIPT", ROOT / "godot_project/scripts/proofs/fl4407_pvp_bot.gd"),
              ("ORCHESTRATOR", run / "run.py"), ("ZOOM-A", run / "A/zoom.json")]
    for role, src in copies:
        if not src.is_file():
            continue
        ext = src.suffix.lower() if src.suffix else ".txt"
        if ext == ".gd":
            ext = ".txt"
        rec = {"role": role, "frame_count": 1, "extension": ext}
        name = deterministic_artifact_name(meta, rec)
        shutil.copyfile(src, out / name)
        rec["path"] = str(out / name)
        artifacts.append(rec)
    manifest_path = out / "godot_proof_publication.json"
    receipt_copy = next(a["path"] for a in artifacts if a["role"] == "RECEIPT")
    import os
    rel_root = os.path.relpath(ROOT, out)
    meta["storage"] = {"status": "TRACKED", "location": ".",
                       "failure_log": f"{rel_root}/docs/FAILURE_LOG.md", "godot_goal": f"{rel_root}/docs/plans/GODOT_GOAL.MD",
                       "receipt_path": Path(receipt_copy).name, "receipt_sha256": sha256_file(receipt_copy)}
    publish_artifacts(manifest_path, meta, artifacts, artifact_root=out)
    errors = validate_manifest(manifest_path, require_storage=True)
    for e in errors or []:
        print("VALIDATION", e)
    tags = apply_finder_tags(manifest_path)
    tag_errors = verify_finder_tags(manifest_path)
    for e in tag_errors or []:
        print("TAG", e)
    labeled = out / "labeled"
    if labeled.exists():
        shutil.rmtree(labeled)
    lab = subprocess.run([sys.executable, str(ROOT / "scripts/publish_godot_labeled_media.py"), "--manifest", str(manifest_path),
                          "--output", str(labeled)], capture_output=True, text=True)
    print(lab.stdout[-800:], lab.stderr[-800:])
    labeled_gifs = sorted(labeled.glob("*.gif"))
    labeled_bytes = labeled_gifs[0].stat().st_size if labeled_gifs else -1
    if labeled_bytes > GIF_LIMIT_BYTES:
        print(f"labeled GIF {labeled_bytes} bytes exceeds the 100 MB bound at ladder step {chosen}; stepping down", file=sys.stderr)
        return 3
    gif_path = artifacts[0]["path"]
    print(f"published {len(artifacts)} artifacts -> {manifest_path}")
    print(f"GIF {Path(gif_path).name} {Path(gif_path).stat().st_size} bytes; tags on {len(tags)} files; "
          f"validation errors {len(errors or [])}; tag errors {len(tag_errors or [])}; labeled rc {lab.returncode}")
    return 1 if (errors or tag_errors or lab.returncode) else 0


if __name__ == "__main__":
    # Each attempt starts at the first ladder step not yet rejected; a run that
    # returns 3 (labeled copy over the bound) retries with the next step.
    rc = 3
    while rc == 3 and GIF_LADDER:
        rc = main()
        if rc == 3:
            GIF_LADDER = GIF_LADDER[1:]
    sys.exit(rc)

"""End-to-end check of FACADE-CAPTURE-01 accept -> build on SCRATCH copies (real owners untouched):
map_editor Handler.capture with owner paths redirected to scratch, then compile_sign_sprites.build on a scratch
copy of build_reg_73 with OPERATOR_CAPTURES redirected. Prints the capture record and the build receipt rows."""
import base64
import json
import shutil
import sys
from pathlib import Path

SP = Path(__file__).resolve().parent
ROOT = Path("/Users/r/Projects/asciicker-Y9-2")
sys.path[:0] = [str(ROOT / "artifacts/experiments/fl4876_manual_world"), str(ROOT), str(ROOT / "docs/agent/cli-anything")]
import map_editor as ME  # noqa: E402
from scripts.pipeline import compile_sign_sprites as C  # noqa: E402

build = SP / "e2e_build"
if not build.is_dir():
    shutil.copytree(ROOT / ".run/fl4876_builds/build_reg_73", build)
own = SP / "e2e_owner"
if own.is_dir():
    shutil.rmtree(own)
(own / "review").mkdir(parents=True)
shutil.copyfile(ME.APPEARANCE, own / "authored_building_appearance.json")
shutil.copyfile(ME.QUEUE, own / "review" / "queue.json")
ME.APPEARANCE = own / "authored_building_appearance.json"
ME.CAPTURES = own / "operator_facade_captures.v1.json"
ME.CAPTURE_SPRITES = own / "operator_facade_sprites"
ME.REVIEW = own / "review"
ME.QUEUE = own / "review" / "queue.json"
ME.JOURNAL = own / "review" / "journal.jsonl"
ME.FACADES = own / "review" / "facades"

world = ME.World(build)
h = ME.Handler.__new__(ME.Handler)
h.world = world
shot = ROOT / "artifacts/experiments/fl4876_manual_world/operator_map_review/shots/a3caa18e6d617dd1.png"
bands = json.loads((SP / "fc_bands.json").read_text())
body = {"building_id": "feature:building_footprint:way/725137997", "face_index": 1,
        "face": {"length_m": 8.411, "height_m": 4.74, "bearing_deg": 242.3},
        "source": {"data_url": "data:image/png;base64," + base64.b64encode(shot.read_bytes()).decode(),
                   "kind": "google_streetview_reference"},
        "bands": [dict(b, mode="sprite", encoding="half (e2e test)") for b in bands]
                 + [{"kind": "wall", "mode": "colour", "x0_m": 0, "x1_m": 8.41, "y0_m": 3.3, "y1_m": 4.7, "rgb": [238, 238, 232]}],
        "objects": [{"label": "vending machines", "x0_m": 0, "x1_m": 1.2, "y0_m": 0, "y1_m": 1.8}],
        "replaces_place_fronts": ["place_front_feature_place_node_441523546"],
        "queue_item": "OPQ-4876-0001", "text": "e2e test capture"}
cap = h.capture(body)
print("CAPTURE", json.dumps({k: cap[k] for k in ("capture_id", "status", "bands", "colours_applied", "replaces_place_fronts")})[:900])
app = json.loads(ME.APPEARANCE.read_text())["buildings"]["feature:building_footprint:way/725137997"]
print("APPEARANCE wall_rgb", app.get("wall_rgb"), "photos", len(app.get("operator_facade_photos") or []))
q = json.loads(ME.QUEUE.read_text())["items"]
print("QUEUE", [(i["id"], i["kind"], i["status"]) for i in q])
live = world.captures_live()
print("LIVE", [(c["capture_id"], len(c["sprites"]), c["replaces_place_fronts"]) for c in live["captures"]])

C.OPERATOR_CAPTURES = ME.CAPTURES
doc = C.build(build, build / "output.a3d")
ops = [s for s in doc["signs"] if s["kind"] == "operator_facade_capture"]
print("BUILD signs", len(doc["signs"]), "operator", json.dumps([{k: s[k] for k in ("sprite", "cells", "pos")} for s in ops]))
print("BUILD skipped(operator-related)", [s for s in doc["skipped"] if "operator" in json.dumps(s)])
(build / "sign_sprites_receipt.json").write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n")
# the live preview positions must equal the build positions
print("PREVIEW==BUILD", [s["pos"] for s in live["captures"][0]["sprites"]] == [s["pos"] for s in ops] or
      ([s["pos"] for s in live["captures"][0]["sprites"]], [s["pos"] for s in ops]))

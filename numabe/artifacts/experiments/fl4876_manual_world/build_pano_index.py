"""FL-4876 MANUAL-WORLD-01: index of every local street-level panorama with its camera pose.

Sources merged (first pose wins, later sources fill gaps): fl4810 capture_source_index,
capture snapshots inside the fl4810 / fl4813 annotation files, and every
route_capture_manifest / capture index under assets/source_bundles. Writes
pano_index.json beside this script: capture_id -> {lat, lon, E, N, heading_deg,
projection, width, height, timestamp, local_path, pose_source}.
"""
import glob, json, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))
from scripts.pipeline.register_observations import lonlat_to_epsg32654

CAP_DIR = ROOT / "artifacts/experiments/fl4810_aoi_street_network_boxing_20260915/captures"
idx = {}


def take(cid, rec, src):
    if not cid or rec.get("lat") is None or rec.get("lon") is None or rec.get("heading_deg") is None:
        return
    if cid in idx:
        return
    E, N = lonlat_to_epsg32654(float(rec["lat"]), float(rec["lon"]))
    idx[cid] = {"lat": float(rec["lat"]), "lon": float(rec["lon"]), "E": round(E, 2), "N": round(N, 2),
                "heading_deg": float(rec["heading_deg"]),
                "projection": rec.get("projection_model") or rec.get("projection"),
                "width": rec.get("width"), "height": rec.get("height"), "timestamp": rec.get("timestamp"),
                "pose_source": src}


d = json.loads((ROOT / "artifacts/experiments/fl4810_aoi_street_network_boxing_20260915/capture_source_index.json").read_text())
for c in d["captures"]:
    take(c["capture_id"], c, "fl4810 capture_source_index")
for p in ("artifacts/experiments/fl4810_aoi_street_network_boxing_20260915/annotations.json",
          "artifacts/experiments/fl4813_capture_review_20260915/annotations.json"):
    for a in json.loads((ROOT / p).read_text())["annotations"]:
        take(a.get("capture_id"), a.get("capture_snapshot") or {}, p.split("/")[2] + " annotation snapshot")
for p in sorted(glob.glob(str(ROOT / "assets/source_bundles/*/route_capture_manifest.json")) +
                glob.glob(str(ROOT / "assets/source_bundles/*/capture*index*.json")) +
                glob.glob(str(ROOT / "assets/source_bundles/*/captures/*.json"))):
    try:
        doc = json.loads(Path(p).read_text())
    except Exception:
        continue
    rows = doc if isinstance(doc, list) else (doc.get("captures") or doc.get("frames") or doc.get("entries") or [])
    for c in rows if isinstance(rows, list) else []:
        if isinstance(c, dict):
            take(c.get("capture_id") or c.get("id"), c, Path(p).relative_to(ROOT).as_posix())
files = sorted(p.stem for p in CAP_DIR.glob("mapillary_*.jpg"))
from PIL import Image  # noqa: E402

for cid in files:
    if cid in idx:
        r = idx[cid]
        r["local_path"] = str((CAP_DIR / (cid + ".jpg")).relative_to(ROOT))
        with Image.open(CAP_DIR / (cid + ".jpg")) as im:
            w, h = im.size
        # 2026-10-15: a source record without a projection used to default to equirectangular, which
        # labelled 32 flat 1024x768 photos as 360 panoramas (viewed by hand: all perspective). Only a 2:1
        # image can be equirectangular; otherwise infer perspective from the file itself.
        if r["projection"] is None or (r["projection"] == "equirectangular" and w != 2 * h):
            r["projection_basis"] = ("inferred from image aspect %dx%d" % (w, h)) + (
                "" if r["projection"] is None else " (source said equirectangular)")
            r["projection"] = "equirectangular" if w == 2 * h else "perspective"
        r["width"], r["height"] = w, h
missing = [c for c in files if c not in idx]
out = {"schema": "y92.fl4876.pano_index.v1", "panoramas": {c: idx[c] for c in files if c in idx},
       "no_pose": missing, "counts": {"files": len(files), "with_pose": len(files) - len(missing)}}
(HERE / "pano_index.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
print(out["counts"], "missing pose:", len(missing))

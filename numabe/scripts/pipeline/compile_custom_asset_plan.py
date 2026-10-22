#!/usr/bin/env python3
"""Compile evidence-backed custom asset specifications before lowering."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import Counter
from pathlib import Path


SCHEMA = "y92.custom_asset_plan.v1"
OCR_CONFIDENCE_MIN = 0.5
CP437_TEXT = frozenset("0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ ")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _lonlat_to_epsg32654(lat: float, lon: float) -> tuple[float, float]:
    a, f = 6378137.0, 1.0 / 298.257223563
    k0, lon0 = 0.9996, math.radians(141.0)
    e2 = 2 * f - f * f
    e4, e6 = e2 * e2, e2 * e2 * e2
    ep2 = e2 / (1 - e2)
    lat_r = math.radians(lat)
    dlon = math.radians(lon) - lon0
    nrad = a / math.sqrt(1 - e2 * math.sin(lat_r) ** 2)
    t = math.tan(lat_r) ** 2
    c = ep2 * math.cos(lat_r) ** 2
    aa = math.cos(lat_r) * dlon
    meridian = a * (
        (1 - e2 / 4 - 3 * e4 / 64 - 5 * e6 / 256) * lat_r
        - (3 * e2 / 8 + 3 * e4 / 32 + 45 * e6 / 1024)
        * math.sin(2 * lat_r)
        + (15 * e4 / 256 + 45 * e6 / 1024) * math.sin(4 * lat_r)
        - (35 * e6 / 3072) * math.sin(6 * lat_r)
    )
    easting = k0 * nrad * (
        aa + (1 - t + c) * aa ** 3 / 6
        + (5 - 18 * t + t * t + 72 * c - 58 * ep2) * aa ** 5 / 120
    ) + 500000.0
    northing = k0 * (
        meridian + nrad * math.tan(lat_r) * (
            aa * aa / 2
            + (5 - t + 9 * c + 4 * c * c) * aa ** 4 / 24
            + (61 - 58 * t + t * t + 600 * c - 330 * ep2)
            * aa ** 6 / 720
        )
    )
    return easting, northing


def _angular_extent(snapshot: dict, box: list) -> tuple[float, float]:
    width = float(snapshot.get("width") or 1024)
    height = float(snapshot.get("height") or 768)
    projection = snapshot.get("projection_model") or "perspective"
    hfov = float(snapshot.get("horizontal_fov_deg") or (
        360.0 if projection == "equirectangular" else 60.0))
    if projection == "equirectangular":
        # FL-4876 MANUAL-PLACE-01: a 2:1 equirectangular image spans 360 deg
        # across and 180 deg down, so vfov = hfov * H / W = 360 * H / W. The
        # former 180 * H / W gave 90 deg and halved every box height angle.
        vfov = 360.0 * (height / width)
    else:
        vfov = 2.0 * math.degrees(math.atan(
            math.tan(math.radians(hfov / 2.0)) * (height / width)))
    return float(box[2]) * hfov, float(box[3]) * vfov


PROJECT_ROOT = Path(__file__).resolve().parents[2]
# FL-4876 APPEARANCE-01. The crop ledger stores ABSOLUTE paths, written on
# whichever checkout reviewed the captures. Every one of the 727 rows in
# observation_module_mapping.jsonl begins "/Users/r/Downloads/asciicker-Y9-2/",
# a root that does not exist here, so 0 of 727 resolved and every custom
# asset in the lane fell back to one neutral grey. The crops themselves are
# present and git-tracked in this repository, and all 727 match the digests
# recorded at review time. Re-anchoring them yields 145 distinct colours.
#
# A stored absolute path is not portable evidence. Re-anchor it on this
# repository at the first repo-owned component instead of trusting the
# machine that wrote the ledger.
_PORTABLE_ANCHORS = ("artifacts/", "assets/", ".run/")


def resolve_crop_path(raw: str | None) -> Path | None:
    """The crop this record names, found in THIS checkout, or None if unnamed.

    Deliberately anchored on a repo-owned path component rather than on the
    file's basename: searching the tree for a matching name would happily
    bind a crop to an unrelated image.
    """
    if not raw:
        return None
    candidate = Path(raw)
    if candidate.is_file():
        return candidate
    text = str(raw).replace("\\", "/")
    for anchor in _PORTABLE_ANCHORS:
        index = text.find(anchor)
        if index >= 0:
            rebased = PROJECT_ROOT / text[index:]
            if rebased.is_file():
                return rebased
    return candidate


def _dominant_colours(crop_path: str | None) -> tuple[list[int], list[int], str]:
    fallback = ([150, 150, 144], [48, 48, 48])
    if not crop_path:
        return fallback[0], fallback[1], "no_crop_evidence"
    resolved = resolve_crop_path(crop_path)
    if resolved is None or not resolved.is_file():
        # FL-4876 APPEARANCE-01. A named-but-absent crop is a broken run, not
        # a grey building. This used to fall into the except below, so a
        # stale path silently repainted every storefront in the world.
        # group_capture_review_crops:151 and freeze_capture_review:104 both
        # already raise here; this applies that pipeline's own rule
        # consistently rather than inventing a new one.
        raise ValueError(
            f"custom asset crop evidence is named but absent: {crop_path!r}"
            + (f" (also tried {resolved})" if resolved is not None
               and str(resolved) != str(crop_path) else ""))
    try:
        from PIL import Image
        image = Image.open(resolved).convert("RGB").resize((16, 16))
        pixels = [image.getpixel((x, y)) for y in range(16) for x in range(16)]
        colour = Counter(
            (r // 16 * 16, g // 16 * 16, b // 16 * 16)
            for r, g, b in pixels).most_common(1)[0][0]
        luminance = 0.299 * colour[0] + 0.587 * colour[1] + 0.114 * colour[2]
        text = (32, 32, 32) if luminance >= 128 else (232, 232, 226)
        return list(colour), list(text), "source_crop_dominant"
    except (OSError, ValueError, IndexError):
        return fallback[0], fallback[1], "crop_unreadable"


def _ocr_index(paths: list[Path]) -> dict[str, list[dict]]:
    result: dict[str, list[dict]] = {}
    for path in paths:
        document = json.loads(path.read_text(encoding="utf-8"))
        for row in document.get("observations", []):
            capture = str(row.get("capture_id") or "").split("__yaw")[0]
            result.setdefault(capture, []).append(row)
    return result


def _ocr_text(capture_ids: list[str], index: dict[str, list[dict]]) \
        -> tuple[str | None, str | None]:
    best = None
    for capture_id in capture_ids:
        for observation in index.get(capture_id, []):
            for result in observation.get("passes", []):
                confidence = float(result.get("mean_confidence") or 0.0)
                text = str(result.get("text") or "").strip()
                if (confidence < OCR_CONFIDENCE_MIN or len(text) < 2
                        or any(char not in CP437_TEXT for char in text)):
                    continue
                if best is None or confidence > best[0]:
                    best = (confidence, text, observation)
    if best is None:
        return None, None
    observation = best[2]
    return best[1], (
        f"{observation.get('capture_id')}|"
        f"{observation.get('ocr_observation_id')}|conf={best[0]:.3f}")


MAX_SLIDE_M = 1.4
SIGN_FASCIA_MAX_M = 1.5   # LOGO-CROP-01 (same bound as compile_sign_sprites)
SIGN_BLADE_MAX_M = 3.5
FACE_SPAN_MIN_M = 0.5


def _face_index(canonical_path: Path | None):
    """(CanonicalIndex, {building_id: [edge length m]}) in resolve()'s edge order."""
    if canonical_path is None:
        return None
    from scripts.pipeline.canonical_attachment import CanonicalIndex, _edges
    index = CanonicalIndex(json.loads(Path(canonical_path).read_text(
        encoding="utf-8")))
    lengths = {fid: [math.hypot(b[0] - a[0], b[1] - a[1])
                     for a, b in _edges(ring)]
               for fid, ring in index.buildings}
    return index, lengths


def _fit_on_face(attachment, width: float, face_index):
    """Fit a centred plane of ``width`` onto its host face.

    Returns None when the placement is not face-attached, else
    (width, attachment, pose, record): width capped at the face length and
    the anchor slid along the same face just far enough for both ends to
    stay on it. The anchor clamp in attachment puts many observations at a
    face endpoint (fraction 0 or 1), so a centred plane there hung half its
    width past the building corner.
    """
    if (face_index is None or not isinstance(attachment, dict)
            or attachment.get("anchor_kind") != "building_face"):
        return None
    from scripts.pipeline.canonical_attachment import resolve
    index, lengths = face_index
    edges = lengths.get(str(attachment.get("canonical_building_id")))
    k = attachment.get("face_index")
    if not edges or k is None or not 0 <= int(k) < len(edges):
        return None
    face_len = edges[int(k)]
    t = min(1.0, max(0.0, float(attachment.get("along_face_fraction") or 0.0)))
    s0 = t * face_len
    # The anchor may slide at most MAX_SLIDE_M (inside spatial coherence's
    # 1.5 m attachment tolerance; an unbounded slide moved signs up to 6 m off
    # their observation in build_reg_29). Within that window the centre goes
    # where the widest plane fits; the plane shrinks only if it still cannot.
    s_best = min(max(face_len / 2.0, s0 - MAX_SLIDE_M), s0 + MAX_SLIDE_M)
    fitted = max(FACE_SPAN_MIN_M, min(width, face_len,
                                      2.0 * min(s_best, face_len - s_best)))
    s = min(max(s0, fitted / 2.0), face_len - fitted / 2.0)
    t_new = round(s / face_len, 6) if face_len > 0 else t
    if fitted == width and abs(t_new - t) < 1e-6:
        return None
    new_att = dict(attachment, along_face_fraction=t_new)
    pose = resolve(new_att, index)
    return fitted, new_att, pose, {
        "face_length_m": round(face_len, 3), "source_width_m": width,
        "source_along_face_fraction": t, "along_face_fraction": t_new,
        "slide_m": round((t_new - t) * face_len, 3)}


def build(plan_path: Path, registered_path: Path, mapping_path: Path,
          annotation_paths: list[Path], ocr_paths: list[Path],
          canonical_path: Path | None = None, entities_path: Path | None = None) -> dict:
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    face_index = _face_index(canonical_path)
    registered = {
        str(row["instance_id"]): row
        for row in json.loads(registered_path.read_text(
            encoding="utf-8"))
    }
    # FL-4876 LOGO-CROP-01: an adjudicated business sign is a sign board of
    # the entity's sign width, its height following the sign crop's aspect
    # (fascia <= 1.5 m, vertical blade <= 3.5 m), not the photo box extent,
    # which frames the whole frontage.
    entities = {}
    ent_path = Path(entities_path) if entities_path else Path(registered_path).parent / "identity_entities.json"
    if ent_path.is_file():
        entities = {e["entity_id"]: e for e in json.loads(
            ent_path.read_text(encoding="utf-8"))["entities"]}
    mapping = {}
    for line in mapping_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            mapping[str(row["annotation_id"])] = row
    annotations = {}
    for path in annotation_paths:
        document = json.loads(path.read_text(encoding="utf-8"))
        for row in document.get("annotations", []):
            annotations[str(row["annotation_id"])] = row
    ocr = _ocr_index(ocr_paths)

    rows = []
    for carrier in plan.get("carriers", []):
        for placement in carrier.get("placements", []):
            placement_id = str(placement["placement_id"])
            evidence = placement.get("evidence") or {}
            instance_id = str(evidence.get("instance_id") or "")
            registered_row = registered.get(instance_id)
            if (registered_row is None
                    or registered_row.get("population_role") != "custom_identity"):
                raise ValueError(
                    f"custom placement {placement_id} lacks registered custom "
                    "identity evidence")
            contract = placement.get("asset_contract")
            if not isinstance(contract, dict):
                raise ValueError(
                    f"custom placement {placement_id} lacks asset contract")
            annotation_ids = list(evidence.get("annotation_ids") or
                                  registered_row.get("annotation_ids") or [])
            annotation = annotations.get(str(annotation_ids[0])) \
                if annotation_ids else None
            pose = placement["resolved_pose"]
            width_band = contract["width_m"]
            height_band = contract["height_m"]
            derivation = None
            if annotation is not None and annotation.get("capture_snapshot"):
                snapshot = annotation["capture_snapshot"]
                box = annotation.get("box_norm")
                if not isinstance(box, list) or len(box) < 4:
                    raise ValueError(
                        f"custom placement {placement_id} annotation has no box")
                camera_e, camera_n = _lonlat_to_epsg32654(
                    float(snapshot["lat"]), float(snapshot["lon"]))
                range_m = math.hypot(
                    float(pose["E"]) - camera_e,
                    float(pose["N"]) - camera_n)
                if range_m <= 0.0:
                    raise ValueError(
                        f"custom placement {placement_id} has zero camera range")
                width_angle, height_angle = _angular_extent(snapshot, box)
                raw_width = 2 * range_m * math.tan(
                    math.radians(width_angle / 2.0))
                raw_height = 2 * range_m * math.tan(
                    math.radians(height_angle / 2.0))
                width = min(float(width_band["max"]),
                            max(float(width_band["min"]), raw_width))
                height = min(float(height_band["max"]),
                             max(float(height_band["min"]), raw_height))
                derivation = {
                    "basis": "source_box_angular_extent_at_resolved_pose",
                    "range_m": range_m,
                    "raw_width_m": raw_width,
                    "raw_height_m": raw_height,
                    "width_was_bounded": width != raw_width,
                    "height_was_bounded": height != raw_height,
                }
            else:
                dimensions = placement.get("dims_m") or {}
                if dimensions.get("width_m") is None \
                        or dimensions.get("height_m") is None:
                    raise ValueError(
                        f"custom placement {placement_id} has neither source "
                        "box evidence nor planned dimensions")
                width = float(dimensions["width_m"])
                height = float(dimensions["height_m"])
                derivation = {"basis": "population_plan.dims_m"}
            width = min(float(width_band["max"]),
                        max(float(width_band["min"]), width))
            height = min(float(height_band["max"]),
                         max(float(height_band["min"]), height))
            ent = entities.get(str(evidence.get("entity_id") or "")) or {}
            if ent.get("presentation") == "storefront_sign" and ent.get("sign_width_m"):
                aspect = 0.3
                crop = ent.get("logo_crop")
                crop_p = PROJECT_ROOT / crop if crop else None
                if crop_p is None or not crop_p.is_file():
                    # the review crop of the entity's logo annotation
                    la = (ent.get("crop_eligible_annotations") or [None])[0]
                    raw = (mapping.get(str(la)) or {}).get("crop_path") if la else None
                    crop_p = resolve_crop_path(raw) if raw else None
                if crop_p is not None and crop_p.is_file():
                    from PIL import Image
                    with Image.open(crop_p) as im:
                        aspect = im.height / max(1, im.width)
                width = float(ent["sign_width_m"])
                if aspect > 1.3:
                    height = min(SIGN_BLADE_MAX_M, width * aspect)
                    width = height / aspect
                else:
                    height = min(SIGN_FASCIA_MAX_M, width * aspect)
                derivation = dict(derivation or {}, basis="LOGO-CROP-01 sign board: entity sign width, crop aspect",
                                  photo_box_derivation=derivation)
            # FL-4876 FACE-SPAN-01: a building-face plane is a centred box,
            # so it must fit its host face. 32 of 63 custom planes in
            # build_reg_22 were wider than the face on one side of their
            # anchor, and 10 hung past the corner over the carriageway.
            face_fit = _fit_on_face(placement.get("attachment"), width,
                                    face_index)
            if face_fit is not None:
                width = face_fit[0]
                derivation = dict(derivation or {}, face_fit=face_fit[3])
            crop_path = None
            if annotation_ids:
                crop_path = (mapping.get(str(annotation_ids[0])) or {}).get(
                    "crop_path")
            background, text_colour, colour_basis = _dominant_colours(
                crop_path)
            capture_ids = list(registered_row.get("capture_ids") or [])
            text, text_source = _ocr_text(capture_ids, ocr)
            label = (registered_row.get("custom_identity_subject")
                     or registered_row.get("module_id")
                     or placement.get("custom_identity_subject"))
            rows.append({
                "placement_id": placement_id,
                "label": label,
                "semantic_family": contract["semantic_family"],
                "width_m": width,
                "height_m": height,
                "depth_m": float(contract["depth_m"]),
                "background_rgb": background,
                "text_rgb": text_colour,
                "colour_basis": colour_basis,
                "block_text": text,
                "block_text_source": text_source,
                "annotation_ids": annotation_ids,
                "capture_ids": capture_ids,
                "dimension_derivation": derivation,
                "face_fit": None if face_fit is None else {
                    "attachment": face_fit[1], "resolved_pose": face_fit[2]},
            })
    return {
        "schema": SCHEMA,
        "inputs": {
            "custom_population_plan_sha256": _sha256(plan_path),
            "registered_instances_sha256": _sha256(registered_path),
            "observation_mapping_sha256": _sha256(mapping_path),
            "annotation_sha256": {
                str(path): _sha256(path) for path in annotation_paths},
            "ocr_sha256": {str(path): _sha256(path) for path in ocr_paths},
        },
        "rows": rows,
    }


def write(plan_path: Path, registered_path: Path, mapping_path: Path,
          annotation_paths: list[Path], ocr_paths: list[Path],
          out_path: Path, canonical_path: Path | None = None,
          entities_path: Path | None = None) -> Path:
    document = build(plan_path, registered_path, mapping_path,
                     annotation_paths, ocr_paths, canonical_path, entities_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n", encoding="utf-8")
    return out_path


def apply_exact_contracts(plan_path: Path, asset_plan_path: Path) -> None:
    """Write authored dimensions back into the pre-lowering owner slice."""
    plan_path = Path(plan_path)
    asset_plan_path = Path(asset_plan_path)
    source_sha = _sha256(plan_path)
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    asset_plan = json.loads(asset_plan_path.read_text(encoding="utf-8"))
    if (asset_plan.get("inputs") or {}).get(
            "custom_population_plan_sha256") != source_sha:
        raise ValueError("custom asset plan does not match its dispatch source")
    specs = {str(row["placement_id"]): row
             for row in asset_plan.get("rows", [])}
    seen = set()
    for carrier in plan.get("carriers", []):
        for placement in carrier.get("placements", []):
            placement_id = str(placement["placement_id"])
            specification = specs.get(placement_id)
            if specification is None:
                raise ValueError(
                    f"custom placement {placement_id} has no authored contract")
            seen.add(placement_id)
            dimensions = {
                "width_m": float(specification["width_m"]),
                "depth_m": float(specification["depth_m"]),
                "height_m": float(specification["height_m"]),
            }
            placement["asset_contract"]["exact_dimensions_m"] = dimensions
            placement["asset_contract"]["asset_plan_schema"] = SCHEMA
            # FL-4876 FACE-SPAN-01: the slid anchor is the pose the lowerer
            # builds, on the same face (so the resolved frame is unchanged).
            fit = specification.get("face_fit")
            if fit:
                placement["attachment"] = fit["attachment"]
                placement["resolved_pose"] = dict(
                    placement.get("resolved_pose") or {},
                    E=fit["resolved_pose"]["E"], N=fit["resolved_pose"]["N"])
            prior_envelope = placement.get("asset_physical_envelope") or {}
            placement["asset_physical_envelope"] = {
                **prior_envelope,
                **dimensions,
                "anchor": "placement_anchor",
                "max_longitudinal_overhang_m": 0.0,
                "fit_policy": "exact_authored_asset_bounds",
                "selection_stage": "population_before_dispatch",
            }
    extras = sorted(set(specs) - seen)
    if extras:
        raise ValueError(
            f"custom asset plan has undispatched placements: {extras}")
    plan_path.write_text(
        json.dumps(plan, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8")
    asset_plan["inputs"]["source_custom_population_plan_sha256"] = source_sha
    asset_plan["inputs"]["custom_population_plan_sha256"] = _sha256(plan_path)
    asset_plan_path.write_text(
        json.dumps(asset_plan, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n", encoding="utf-8")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--registered", type=Path, required=True)
    parser.add_argument("--mapping", type=Path, required=True)
    parser.add_argument("--annotations", type=Path, action="append",
                        required=True)
    parser.add_argument("--ocr", type=Path, action="append", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    write(args.plan, args.registered, args.mapping, args.annotations,
          args.ocr, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

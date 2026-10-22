#!/usr/bin/env python3
"""Create a metric A3D base directly for the original Classic C++ runtime.

The compiler deliberately bypasses the legacy SBU/ASCIIID bake lane.  It owns
the stable EPSG:32654 -> Y9 transform and emits a valid empty v4 world whose
terrain, instances, and markers can be filled by the FL-4813/4814 lowerers.
It never launches a runtime.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import struct
import sys
import unicodedata
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.pipeline import geo_frames as GF
from scripts.pipeline.compile_world_frame import PATCH_WORLD, compile_frame
from scripts.pipeline.world_ir import WorldIRStore, assert_valid

DEFAULT_MATERIAL_ID = 1


def _load_format():
    path = REPO_ROOT / "addons" / "io_asciicker" / "scene" / "a3d_format.py"
    spec = importlib.util.spec_from_file_location("classic_base_a3d_format", path)
    if spec is None or spec.loader is None:
        raise ImportError(path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fmt = _load_format()


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _points(geometry: dict):
    kind = geometry.get("kind")
    if kind == "point":
        yield geometry["coordinates"][:2]
    elif kind in {"polyline", "polygon"}:
        yield from (point[:2] for point in geometry["coordinates"])
    elif kind == "polygon_with_holes":
        yield from (point[:2] for point in geometry["outer"])
        for ring in geometry.get("holes", []):
            yield from (point[:2] for point in ring)
    elif kind == "prism":
        yield from (point[:2] for point in geometry["base"])
    elif kind == "field_samples":
        for sample in geometry.get("samples", []):
            yield [sample["easting"], sample["northing"]]


_XTERM_LEVELS = (0, 95, 135, 175, 215, 255)


def _xterm_index(rgb) -> int:
    """Nearest xterm-256 colour-cube index (16 + 36 r + 6 g + b)."""
    q = [min(range(6), key=lambda i: abs(_XTERM_LEVELS[i] - int(c))) for c in rgb[:3]]
    return 16 + 36 * q[0] + 6 * q[1] + q[2]


def _brand_marker_fg(tags: dict, packs) -> int | None:
    """FL-4876 BRAND-SIGN-01: a place whose locale place-front profile is
    matched by brand identity (tokyo.familymart_front, tokyo.mybasket_front)
    carries that brand's primary colour on its marker. The Classic runtime
    draws place markers near the player as glyph signs on that colour."""
    if not packs:
        return None
    from scripts.pipeline.template_resolver import resolve_data_profile
    profile = resolve_data_profile(
        collection='place_front_profiles',
        attributes={'brand_identity': ' '.join(filter(None, (
            tags.get('brand'), tags.get('name:en'), tags.get('name'), tags.get('shop')))),
            'commercial_category': next((f'{k}={tags[k]}' for k in
                                         ('shop', 'amenity', 'healthcare', 'office', 'craft')
                                         if tags.get(k)), 'unclassified'),
            'amenity': tags.get('amenity'), 'religion': tags.get('religion')},
        packs=packs)
    if not profile or 'brand_identity_contains' not in (profile.get('match') or {}):
        return None
    rgb = profile.get('primary_rgb')
    return _xterm_index(rgb) if rgb else None


def compile_base(osm_ir_path: Path, dem_ir_path: Path, aoi_path: Path,
                 palette_map: Path, run_root: Path,
                 locale_context: dict | None = None,
                 home_label: str | None = None) -> dict:
    run_root.mkdir(parents=True, exist_ok=True)
    brand_packs = None
    if locale_context:
        from scripts.pipeline.template_resolver import discover_pack_chain
        brand_packs = discover_pack_chain(
            Path(__file__).resolve().parents[2] / 'assets' / 'world_templates',
            locale_context)
    osm_store = WorldIRStore.load(osm_ir_path)
    dem_store = WorldIRStore.load(dem_ir_path)
    assert_valid(osm_store.to_dict())
    assert_valid(dem_store.to_dict())
    frame = compile_frame(aoi_path)
    home, station = frame.home, frame.station
    min_e = frame.origin_easting
    min_n = frame.origin_northing
    max_e = frame.maximum_easting
    max_n = frame.maximum_northing
    scale = frame.content_scale
    patches_x, patches_y = frame.patches_x, frame.patches_y

    patches = []
    for py in range(patches_y):
        for px in range(patches_x):
            patch = fmt.A3DPatch(px, py)
            patch.visual = [
                [DEFAULT_MATERIAL_ID] * fmt.VISUAL_CELLS
                for _ in range(fmt.VISUAL_CELLS)
            ]
            patch.height = [
                [int(GF.BAKE_HEIGHT_ZERO)] * (fmt.HEIGHT_CELLS + 1)
                for _ in range(fmt.HEIGHT_CELLS + 1)
            ]
            patches.append(patch)

    materials = fmt.read_materials_from_a3d(str(palette_map))
    if len(materials) != 256:
        raise ValueError(f"expected 256 materials in {palette_map}, got {len(materials)}")

    def world_point(feature: dict) -> tuple[float, float]:
        return frame.world_point(feature)

    home_x, home_y = world_point(home)
    station_x, station_y = world_point(station)
    # FL-4814: start on the accepted walking spine, facing its first segment.
    # The address marker remains at home; it need not be a walkable camera pose.
    routes = list(frame.walking_routes)
    spawn_x, spawn_y, spawn_yaw = home_x, home_y, 0.0
    spawn_source = "home_start"
    if routes:
        line = routes[0]["geometry"]["coordinates"]
        if len(line) < 2:
            raise ValueError("walking route requires a first segment")
        spawn_x, spawn_y = world_point({"geometry": {"coordinates": line[0]}})
        next_x, next_y = world_point({"geometry": {"coordinates": line[1]}})
        if math.hypot(spawn_x-home_x, spawn_y-home_y)/scale > 10:
            raise ValueError("walking route start is not near the home landmark")
        # Native render_scene view direction is [-sin(yaw), cos(yaw)].
        spawn_yaw = math.degrees(math.atan2(-(next_x-spawn_x), next_y-spawn_y))
        spawn_source = "walking_route_start"
    player_start = fmt.A3DPlayerStart(
        pos=[spawn_x, spawn_y, GF.BAKE_HEIGHT_ZERO + 16.0], yaw=spawn_yaw, dir=spawn_yaw
    )
    markers = [
        fmt.A3DMinimapMarker(
            # FL-4876 GATE-OWNERS-02: the requirement's home label (its minimap
            # label list names "17-16 Den-en-chōfu Honchō"), not a short code.
            name="home_start", label=(home_label or "Home 17-16"),
            x=home_x, y=home_y, fg=226, glyph="H", marker_type=fmt.A3DMinimapMarker.TYPE_CUSTOM,
        ),
        fmt.A3DMinimapMarker(
            name="numabe_station", label="Numabe Station",
            x=station_x, y=station_y, fg=226, glyph="S", marker_type=fmt.A3DMinimapMarker.TYPE_CUSTOM,
        ),
    ]

    # FL-4814: labels come from canonical features and their source tags.
    # Do not invent a named slope from an out-of-AOI source-survey photograph.
    observations = {o['observation_id']:o for o in osm_store.observations}
    seen_labels = set()
    for feature in sorted(osm_store.features, key=lambda f:f['feature_id']):
        if feature['semantic_class'] not in {
                'road','railway_boundary','crossing','sign','building_footprint','place'}:
            continue
        tags = {}
        for oid in feature.get('supporting_observation_ids', []):
            tags.update(observations.get(oid,{}).get('attributes',{}).get('tags',{}))
        label = tags.get('name:en') or tags.get('name:ja_rm') or tags.get('name')
        if not label or label in seen_labels: continue
        # FL-4814: CP437 runtime reads bytes, not UTF-8. Preserve original names
        # in World IR; use source romanization or a semantic navigation label.
        ascii_label = unicodedata.normalize('NFKD', label).encode('ascii', 'ignore').decode().strip()
        if not ascii_label or any(ord(c) > 127 for c in unicodedata.normalize('NFKD', label)
                                  if not unicodedata.combining(c)):
            place_label = {
                'dentist': 'Dentist', 'pharmacy': 'Pharmacy', 'chemist': 'Pharmacy',
                'post_office': 'Post Office', 'place_of_worship': 'Temple',
                'restaurant': 'Restaurant', 'pub': 'Pub', 'bank': 'Bank',
                'hairdresser': 'Hair Salon', 'bicycle_rental': 'Bike Rental',
                'supermarket': 'Supermarket', 'convenience': 'Convenience Store',
            }
            category = next((tags.get(key) for key in
                             ('shop','healthcare','amenity','office','craft')
                             if tags.get(key)), None)
            ascii_label = {'crossing':'Rail crossing','railway_boundary':'Railway',
                           'road':'Road','sign':'Landmark','building_footprint':'Building',
                           'place':place_label.get(category, 'Place')}[feature['semantic_class']]
        label = ascii_label
        if label in seen_labels: continue
        points = list(_points(feature['geometry']))
        inside = [p for p in points if min_e<=p[0]<=max_e and min_n<=p[1]<=max_n]
        if not inside: continue
        point = inside[len(inside)//2]
        # FL-4876 MINIMAP-ORIGIN-01. MARGIN_WORLD was never defined. It
        # appeared exactly once in this file -- here, at its use site -- from
        # the commit that introduced this loop (fe908e6e4), so the minimap
        # marker path raised NameError the first time it was ever reached and
        # has never produced a marker. The pre-assembly gate refuses before
        # compile_base runs, which is why nothing caught it.
        #
        # The projection is not this function's to invent. compile_world_frame
        # owns it in WorldFrame.world_point: origin shift, times content
        # scale, plus the frame's declared offsets. The phantom constant stood
        # where offset_x/offset_y belong, applied to both axes.
        x = (point[0] - min_e) * scale + frame.offset_x
        y = (point[1] - min_n) * scale + frame.offset_y
        glyph = {'road':'L','railway_boundary':'R','crossing':'X','place':'P'}.get(
            feature['semantic_class'],'i')
        fg = 231
        if feature['semantic_class'] == 'place':
            fg = _brand_marker_fg(tags, brand_packs) or 231
        markers.append(fmt.A3DMinimapMarker(name=feature['feature_id'],label=label,
            x=x,y=y,fg=fg,glyph=glyph,marker_type=fmt.A3DMinimapMarker.TYPE_CUSTOM))
        seen_labels.add(label)

    map_path = run_root / "classic_base.a3d"
    with map_path.open("wb") as fh:
        fmt.A3DHeader(len(patches)).write(fh)
        for patch in patches:
            patch.write(fh)
        for material in materials:
            material.write(fh)
        fh.write(struct.pack("<i", fmt.WORLD_FORMAT_VERSION))
        fh.write(struct.pack("<i", 0))  # instances
        fh.write(struct.pack("<i", 1))
        player_start.write(fh)
        fh.write(struct.pack("<i", 0))  # enemy generators
        fh.write(struct.pack("<i", len(markers)))
        for marker in markers:
            marker.write(fh)

    metadata = frame.metadata
    metadata_path = run_root / "terrain_metadata.json"
    metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n")
    receipt = {
        "schema": "y92.classic_base_compile.v1",
        "target_runtime": "original_cpp_classic",
        "legacy_sbu_runner_used": False,
        "asciiid_used": False,
        "runtime_launched": False,
        "inputs": {
            "osm_world_ir": str(osm_ir_path.resolve()),
            "osm_world_ir_sha256": _sha(osm_ir_path),
            "dem_world_ir": str(dem_ir_path.resolve()),
            "dem_world_ir_sha256": _sha(dem_ir_path),
            "aoi": str(aoi_path.resolve()),
            "aoi_sha256": _sha(aoi_path),
            "palette_map": str(palette_map.resolve()),
            "palette_map_sha256": _sha(palette_map),
        },
        "output": {"map": str(map_path.resolve()), "map_sha256": _sha(map_path)},
        "counts": {
            "patches": len(patches), "patches_x": patches_x, "patches_y": patches_y,
            "osm_features": len(osm_store.features), "dem_features": len(dem_store.features),
            "instances": 0, "minimap_markers": len(markers),
        },
        "world_extent": {"width": patches_x * PATCH_WORLD, "height": patches_y * PATCH_WORLD},
        "spawn": {"x": spawn_x, "y": spawn_y, "yaw": spawn_yaw, "source": spawn_source},
        "station": {"x": station_x, "y": station_y, "label": "沼部駅 / Numabe Station"},
        "projection": metadata,
        "stage": "base_only_not_launchable",
    }
    (run_root / "classic_base_receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )
    return receipt


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--osm-world-ir", required=True, type=Path)
    ap.add_argument("--dem-world-ir", required=True, type=Path)
    ap.add_argument("--aoi", required=True, type=Path)
    ap.add_argument("--palette-map", default=REPO_ROOT / "assets/a3d/game_map_y8.a3d", type=Path)
    ap.add_argument("--run-root", required=True, type=Path)
    args = ap.parse_args(argv)
    print(json.dumps(compile_base(
        args.osm_world_ir, args.dem_world_ir, args.aoi, args.palette_map, args.run_root
    ), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

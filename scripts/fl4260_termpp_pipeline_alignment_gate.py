#!/usr/bin/env python3
"""FL-4260 TERM++ final-presentation alignment gate.

This is a post-run analyzer for headed ASCIIID TERM++ artifacts. It joins the
FL-4260 bridge cell dump to the final TERM++ rendered-buffer dump and checks
that the stated PROFILE pipeline reaches the actual presented cell identity:

  eligible bridge winner_gid > 255
    -> rendered sidecar_gid
    -> rendered final_gid

When before/after dumps are provided, it also checks that a Material Look
scoring edit did not mutate world/material route facts and that changed final
rendered cells are restricted to the selected material.

When before/after TERM++ framebuffer PNGs are also provided, it checks that
final rendered cell changes are visible in the corresponding framebuffer cell
rectangles, and that framebuffer pixel changes do not appear outside changed
rendered cells.

This script does not run ASCIIID and does not mutate runtime state.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

GLYPH_ID_NONE = 0xFFFFFFFF


def _read_jsonl(path: Path) -> tuple[dict[str, Any], dict[tuple[int, int], dict[str, Any]]]:
    if not path.exists():
        raise FileNotFoundError(path)
    header: dict[str, Any] = {}
    cells: dict[tuple[int, int], dict[str, Any]] = {}
    with path.open(encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            text = line.strip()
            if not text:
                continue
            obj = json.loads(text)
            kind = obj.get("kind")
            if kind == "header":
                header = obj
            elif kind == "cell":
                try:
                    key = (int(obj["x"]), int(obj["y"]))
                except KeyError as exc:
                    raise ValueError(f"{path}:{line_no}: cell missing {exc}") from exc
                cells[key] = obj
    return header, cells


def _check_rendered_identity(
    rendered_name: str,
    rendered_cells: dict[tuple[int, int], dict[str, Any]],
) -> tuple[int, list[dict[str, Any]]]:
    failures: list[dict[str, Any]] = []
    checked = 0
    for key, cell in rendered_cells.items():
        checked += 1
        sidecar = int(cell.get("sidecar_gid", GLYPH_ID_NONE))
        extended = int(cell.get("extended", 0)) == 1
        final_gid = int(cell.get("final_gid", -1))
        cp437 = int(cell.get("cp437", -1))
        expected = sidecar if extended and sidecar != GLYPH_ID_NONE and sidecar > 255 else cp437
        if final_gid != expected:
            failures.append({
                "surface": rendered_name,
                "x": key[0],
                "y": key[1],
                "reason": "rendered_final_gid_rule_mismatch",
                "final_gid": final_gid,
                "expected": expected,
                "sidecar_gid": sidecar,
                "extended": 1 if extended else 0,
                "cp437": cp437,
            })
            if len(failures) >= 20:
                break
    return checked, failures


def _check_bridge_to_final(
    bridge_name: str,
    bridge_cells: dict[tuple[int, int], dict[str, Any]],
    rendered_cells: dict[tuple[int, int], dict[str, Any]],
) -> tuple[dict[str, int], list[dict[str, Any]]]:
    stats = {
        "eligible_extended_winner_cells": 0,
        "bridge_cells_missing_rendered_cell": 0,
        "sidecar_matches": 0,
        "final_gid_matches": 0,
    }
    failures: list[dict[str, Any]] = []
    for key, bridge in bridge_cells.items():
        eligible = int(bridge.get("eligible", 0)) == 1
        winner_gid = int(bridge.get("winner_gid", GLYPH_ID_NONE))
        if not eligible or winner_gid <= 255 or winner_gid == GLYPH_ID_NONE:
            continue
        stats["eligible_extended_winner_cells"] += 1
        rendered = rendered_cells.get(key)
        if rendered is None:
            stats["bridge_cells_missing_rendered_cell"] += 1
            failures.append({
                "surface": bridge_name,
                "x": key[0],
                "y": key[1],
                "reason": "bridge_cell_missing_rendered_cell",
                "winner_gid": winner_gid,
            })
            continue
        sidecar_gid = int(rendered.get("sidecar_gid", GLYPH_ID_NONE))
        final_gid = int(rendered.get("final_gid", GLYPH_ID_NONE))
        extended = int(rendered.get("extended", 0))
        if sidecar_gid == winner_gid:
            stats["sidecar_matches"] += 1
        if final_gid == winner_gid:
            stats["final_gid_matches"] += 1
        if sidecar_gid != winner_gid or final_gid != winner_gid or extended != 1:
            failures.append({
                "surface": bridge_name,
                "x": key[0],
                "y": key[1],
                "reason": "bridge_winner_not_final_rendered_gid",
                "winner_gid": winner_gid,
                "sidecar_gid": sidecar_gid,
                "final_gid": final_gid,
                "extended": extended,
                "material_id": bridge.get("material_id"),
                "ramp": bridge.get("ramp"),
                "density": bridge.get("density"),
            })
            if len(failures) >= 20:
                break
    return stats, failures


def _route_tuple(cell: dict[str, Any]) -> tuple[Any, Any, Any, Any, Any, Any]:
    return (
        cell.get("material_id"),
        cell.get("dispatch_surface"),
        cell.get("ramp"),
        cell.get("density"),
        cell.get("resolve_elev"),
        cell.get("resolve_shade"),
    )


def _render_tuple(cell: dict[str, Any]) -> tuple[Any, Any, Any, Any, Any, Any]:
    return (
        cell.get("final_gid"),
        cell.get("sidecar_gid"),
        cell.get("extended"),
        cell.get("cp437"),
        cell.get("fg"),
        cell.get("bk"),
    )


def _check_before_after(
    before_bridge: dict[tuple[int, int], dict[str, Any]],
    before_rendered: dict[tuple[int, int], dict[str, Any]],
    after_bridge: dict[tuple[int, int], dict[str, Any]],
    after_rendered: dict[tuple[int, int], dict[str, Any]],
    target_material: int,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    failures: list[dict[str, Any]] = []
    shared_bridge_keys = set(before_bridge) & set(after_bridge)
    shared_rendered_keys = set(before_rendered) & set(after_rendered)
    stable_fact_mismatch = 0
    changed_total = 0
    changed_target = 0
    changed_non_target = 0
    changed_by_material: dict[str, int] = {}
    changed_without_eligible_target = 0

    for key in sorted(shared_bridge_keys):
        b = before_bridge[key]
        a = after_bridge[key]
        if _route_tuple(b) != _route_tuple(a):
            stable_fact_mismatch += 1
            if len(failures) < 20:
                failures.append({
                    "x": key[0],
                    "y": key[1],
                    "reason": "world_material_route_fact_changed",
                    "before": _route_tuple(b),
                    "after": _route_tuple(a),
                })

    for key in sorted(shared_rendered_keys):
        b_render = before_rendered[key]
        a_render = after_rendered[key]
        if _render_tuple(b_render) == _render_tuple(a_render):
            continue
        changed_total += 1
        b_bridge = before_bridge.get(key)
        a_bridge = after_bridge.get(key)
        material = a_bridge.get("material_id") if a_bridge else None
        changed_by_material[str(material)] = changed_by_material.get(str(material), 0) + 1
        target = material == target_material
        if target:
            changed_target += 1
        else:
            changed_non_target += 1
            if len(failures) < 20:
                failures.append({
                    "x": key[0],
                    "y": key[1],
                    "reason": "non_target_material_final_cell_changed",
                    "material_id": material,
                    "before_rendered": _render_tuple(b_render),
                    "after_rendered": _render_tuple(a_render),
                    "before_bridge": _route_tuple(b_bridge) if b_bridge else None,
                    "after_bridge": _route_tuple(a_bridge) if a_bridge else None,
                })
        if not a_bridge or int(a_bridge.get("eligible", 0)) != 1:
            changed_without_eligible_target += 1
            if len(failures) < 20:
                failures.append({
                    "x": key[0],
                    "y": key[1],
                    "reason": "changed_cell_not_eligible_in_after_bridge",
                    "material_id": material,
                    "after_bridge": _route_tuple(a_bridge) if a_bridge else None,
                })

    stats = {
        "shared_bridge_cells": len(shared_bridge_keys),
        "shared_rendered_cells": len(shared_rendered_keys),
        "stable_fact_mismatch": stable_fact_mismatch,
        "changed_total": changed_total,
        "changed_target": changed_target,
        "changed_non_target": changed_non_target,
        "changed_without_eligible_after_bridge": changed_without_eligible_target,
        "changed_by_material": changed_by_material,
    }
    return stats, failures


def _check_light_before_after(
    before_bridge: dict[tuple[int, int], dict[str, Any]],
    before_rendered: dict[tuple[int, int], dict[str, Any]],
    after_bridge: dict[tuple[int, int], dict[str, Any]],
    after_rendered: dict[tuple[int, int], dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    failures: list[dict[str, Any]] = []
    shared_bridge_keys = set(before_bridge) & set(after_bridge)
    shared_rendered_keys = set(before_rendered) & set(after_rendered)
    stable_material_mismatch = 0
    stable_dispatch_mismatch = 0
    stable_elev_mismatch = 0
    shade_changed = 0
    ramp_changed = 0
    density_changed = 0
    final_changed = 0
    fg_changed = 0
    bk_changed = 0
    cp437_changed = 0

    for key in sorted(shared_bridge_keys):
        b = before_bridge[key]
        a = after_bridge[key]
        if b.get("material_id") != a.get("material_id"):
            stable_material_mismatch += 1
        if b.get("dispatch_surface") != a.get("dispatch_surface"):
            stable_dispatch_mismatch += 1
        if b.get("resolve_elev") != a.get("resolve_elev"):
            stable_elev_mismatch += 1
        if b.get("resolve_shade") != a.get("resolve_shade"):
            shade_changed += 1
        if b.get("ramp") != a.get("ramp"):
            ramp_changed += 1
        if b.get("density") != a.get("density"):
            density_changed += 1
        if len(failures) < 20 and (
            b.get("material_id") != a.get("material_id")
            or b.get("dispatch_surface") != a.get("dispatch_surface")
            or b.get("resolve_elev") != a.get("resolve_elev")
        ):
            failures.append({
                "x": key[0],
                "y": key[1],
                "reason": "light_control_changed_world_or_elevation_fact",
                "before": {
                    "material_id": b.get("material_id"),
                    "dispatch_surface": b.get("dispatch_surface"),
                    "resolve_elev": b.get("resolve_elev"),
                },
                "after": {
                    "material_id": a.get("material_id"),
                    "dispatch_surface": a.get("dispatch_surface"),
                    "resolve_elev": a.get("resolve_elev"),
                },
            })

    for key in sorted(shared_rendered_keys):
        b = before_rendered[key]
        a = after_rendered[key]
        if b.get("final_gid") != a.get("final_gid"):
            final_changed += 1
        if b.get("fg") != a.get("fg"):
            fg_changed += 1
        if b.get("bk") != a.get("bk"):
            bk_changed += 1
        if b.get("cp437") != a.get("cp437"):
            cp437_changed += 1

    stats = {
        "shared_bridge_cells": len(shared_bridge_keys),
        "shared_rendered_cells": len(shared_rendered_keys),
        "material_id_changed": stable_material_mismatch,
        "dispatch_surface_changed": stable_dispatch_mismatch,
        "resolve_elev_changed": stable_elev_mismatch,
        "resolve_shade_changed": shade_changed,
        "ramp_changed": ramp_changed,
        "density_changed": density_changed,
        "final_gid_changed": final_changed,
        "fg_changed": fg_changed,
        "bk_changed": bk_changed,
        "cp437_changed": cp437_changed,
    }
    return stats, failures


def _check_png_cell_alignment(
    before_png: Path,
    after_png: Path,
    rendered_header: dict[str, Any],
    before_rendered: dict[tuple[int, int], dict[str, Any]],
    after_rendered: dict[tuple[int, int], dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    try:
        from PIL import Image
    except ImportError as exc:
        raise ValueError("Pillow is required for --before-png/--after-png checks") from exc

    before_img = Image.open(before_png).convert("RGBA")
    after_img = Image.open(after_png).convert("RGBA")
    if before_img.size != after_img.size:
        raise ValueError(f"PNG size mismatch: {before_img.size} vs {after_img.size}")
    grid_w = int(rendered_header.get("w", 0))
    grid_h = int(rendered_header.get("h", 0))
    if grid_w <= 0 or grid_h <= 0:
        raise ValueError("rendered-buffer header missing positive w/h")
    pixel_w, pixel_h = before_img.size
    if pixel_w % grid_w != 0 or pixel_h % grid_h != 0:
        raise ValueError(
            f"PNG {pixel_w}x{pixel_h} is not an integer cell grid for {grid_w}x{grid_h}"
        )
    cell_w = pixel_w // grid_w
    cell_h = pixel_h // grid_h
    before_px = before_img.load()
    after_px = after_img.load()
    shared_rendered_keys = set(before_rendered) & set(after_rendered)
    rendered_changed = {
        key for key in shared_rendered_keys
        if _render_tuple(before_rendered[key]) != _render_tuple(after_rendered[key])
    }
    pixel_changed: set[tuple[int, int]] = set()
    for y in range(grid_h):
        for x in range(grid_w):
            changed = False
            x0 = x * cell_w
            y0 = y * cell_h
            for py in range(y0, y0 + cell_h):
                for px in range(x0, x0 + cell_w):
                    if before_px[px, py] != after_px[px, py]:
                        changed = True
                        break
                if changed:
                    break
            if changed:
                pixel_changed.add((x, y))

    transforms = {
        "identity": lambda x, y: (x, y),
        "yflip": lambda x, y: (x, grid_h - 1 - y),
        "xflip": lambda x, y: (grid_w - 1 - x, y),
        "xyflip": lambda x, y: (grid_w - 1 - x, grid_h - 1 - y),
    }
    transform_stats: dict[str, dict[str, int]] = {}
    best_name = "identity"
    best_score = (-1, -1)
    best_mapped: set[tuple[int, int]] = set()
    for name, fn in transforms.items():
        mapped = {fn(x, y) for x, y in rendered_changed}
        overlap = len(mapped & pixel_changed)
        transform_stats[name] = {
            "overlap": overlap,
            "rendered_without_pixels": len(mapped - pixel_changed),
            "pixels_without_rendered": len(pixel_changed - mapped),
        }
        score = (overlap, -transform_stats[name]["rendered_without_pixels"] - transform_stats[name]["pixels_without_rendered"])
        if score > best_score:
            best_score = score
            best_name = name
            best_mapped = mapped

    rendered_without_pixels = sorted(best_mapped - pixel_changed)
    pixels_without_rendered = sorted(pixel_changed - best_mapped)
    failures: list[dict[str, Any]] = []
    for key in rendered_without_pixels[:20]:
        source_key = key
        if best_name == "yflip":
            source_key = (key[0], grid_h - 1 - key[1])
        elif best_name == "xflip":
            source_key = (grid_w - 1 - key[0], key[1])
        elif best_name == "xyflip":
            source_key = (grid_w - 1 - key[0], grid_h - 1 - key[1])
        failures.append({
            "x": key[0],
            "y": key[1],
            "rendered_x": source_key[0],
            "rendered_y": source_key[1],
            "reason": "rendered_cell_changed_without_framebuffer_pixels",
            "before_rendered": _render_tuple(before_rendered[source_key]),
            "after_rendered": _render_tuple(after_rendered[source_key]),
        })
    for key in pixels_without_rendered[:20 - len(failures)]:
        before_cell = before_rendered.get(key)
        after_cell = after_rendered.get(key)
        failures.append({
            "x": key[0],
            "y": key[1],
            "reason": "framebuffer_pixels_changed_without_rendered_cell_change",
            "before_rendered": _render_tuple(before_cell) if before_cell else None,
            "after_rendered": _render_tuple(after_cell) if after_cell else None,
        })
    stats = {
        "png_width": pixel_w,
        "png_height": pixel_h,
        "grid_w": grid_w,
        "grid_h": grid_h,
        "cell_w": cell_w,
        "cell_h": cell_h,
        "framebuffer_transform": best_name,
        "transform_stats": transform_stats,
        "rendered_changed_cells": len(rendered_changed),
        "pixel_changed_cells": len(pixel_changed),
        "rendered_changed_with_pixel_change": len(best_mapped & pixel_changed),
        "rendered_changed_without_pixel_change": len(rendered_without_pixels),
        "pixel_changed_without_rendered_change": len(pixels_without_rendered),
    }
    return stats, failures


def evaluate(args: argparse.Namespace) -> dict[str, Any]:
    before_bridge_header, before_bridge = _read_jsonl(args.before_bridge)
    before_rendered_header, before_rendered = _read_jsonl(args.before_rendered)
    after_bridge_header: dict[str, Any] | None = None
    after_rendered_header: dict[str, Any] | None = None
    after_bridge: dict[tuple[int, int], dict[str, Any]] | None = None
    after_rendered: dict[tuple[int, int], dict[str, Any]] | None = None
    if args.after_bridge and args.after_rendered:
        after_bridge_header, after_bridge = _read_jsonl(args.after_bridge)
        after_rendered_header, after_rendered = _read_jsonl(args.after_rendered)

    failures: list[dict[str, Any]] = []
    gates: dict[str, dict[str, Any]] = {}

    def gate(name: str, passed: bool, detail: str) -> None:
        gates[name] = {"pass": bool(passed), "detail": detail}

    for label, header in (
        ("before", before_bridge_header),
        ("after", after_bridge_header),
    ):
        if header is None:
            continue
        profile_routing = int(header.get("profile_routing", 0))
        candidate_count = int(header.get("candidate_count", 0))
        eligible_count = int(header.get("eligible_count", 0))
        winner_count = int(header.get("gpu_shader_winner_count", 0))
        if args.change_kind == "material-look":
            gate(
                f"evidence_fl4260_{label}_profile_bridge_live",
                profile_routing == 1 and candidate_count > 0 and eligible_count > 0 and winner_count > 0,
                f"profile_routing={profile_routing} candidate_count={candidate_count} "
                f"eligible_count={eligible_count} gpu_shader_winner_count={winner_count}",
            )

    rendered_checked, rendered_failures = _check_rendered_identity("before", before_rendered)
    failures.extend(rendered_failures)
    gate(
        "evidence_fl4260_before_rendered_final_gid_rule",
        rendered_checked > 0 and not rendered_failures,
        f"checked={rendered_checked} failures={len(rendered_failures)}",
    )
    bridge_stats, bridge_failures = _check_bridge_to_final("before", before_bridge, before_rendered)
    if args.change_kind == "material-look":
        failures.extend(bridge_failures)
        gate(
            "gameplay_fl4260_before_bridge_winner_reaches_final",
            bridge_stats["eligible_extended_winner_cells"] > 0 and not bridge_failures,
            json.dumps(bridge_stats, sort_keys=True),
        )

    if after_bridge is not None and after_rendered is not None:
        rendered_checked_after, rendered_failures_after = _check_rendered_identity("after", after_rendered)
        failures.extend(rendered_failures_after)
        gate(
            "evidence_fl4260_after_rendered_final_gid_rule",
            rendered_checked_after > 0 and not rendered_failures_after,
            f"checked={rendered_checked_after} failures={len(rendered_failures_after)}",
        )
        bridge_stats_after, bridge_failures_after = _check_bridge_to_final("after", after_bridge, after_rendered)
        if args.change_kind == "material-look":
            failures.extend(bridge_failures_after)
            gate(
                "gameplay_fl4260_after_bridge_winner_reaches_final",
                bridge_stats_after["eligible_extended_winner_cells"] > 0 and not bridge_failures_after,
                json.dumps(bridge_stats_after, sort_keys=True),
            )
            delta_stats, delta_failures = _check_before_after(
                before_bridge,
                before_rendered,
                after_bridge,
                after_rendered,
                args.target_material,
            )
            failures.extend(delta_failures)
            gate(
                "gameplay_fl4260_scoring_edit_keeps_world_facts_stable",
                delta_stats["stable_fact_mismatch"] == 0,
                json.dumps({
                    "stable_fact_mismatch": delta_stats["stable_fact_mismatch"],
                    "shared_bridge_cells": delta_stats["shared_bridge_cells"],
                }, sort_keys=True),
            )
            gate(
                "gameplay_fl4260_scoring_edit_changes_target_material_only",
                delta_stats["changed_total"] > 0
                and delta_stats["changed_non_target"] == 0
                and delta_stats["changed_without_eligible_after_bridge"] == 0,
                json.dumps(delta_stats, sort_keys=True),
            )
        else:
            light_stats, light_failures = _check_light_before_after(
                before_bridge,
                before_rendered,
                after_bridge,
                after_rendered,
            )
            failures.extend(light_failures)
            gate(
                "gameplay_fl4260_light_keeps_world_facts_stable",
                light_stats["material_id_changed"] == 0
                and light_stats["dispatch_surface_changed"] == 0
                and light_stats["resolve_elev_changed"] == 0,
                json.dumps({
                    "material_id_changed": light_stats["material_id_changed"],
                    "dispatch_surface_changed": light_stats["dispatch_surface_changed"],
                    "resolve_elev_changed": light_stats["resolve_elev_changed"],
                    "shared_bridge_cells": light_stats["shared_bridge_cells"],
                }, sort_keys=True),
            )
            gate(
                "gameplay_fl4260_light_changes_resolve_shade",
                light_stats["resolve_shade_changed"] > 0,
                json.dumps(light_stats, sort_keys=True),
            )
            gate(
                "gameplay_fl4260_light_changes_final_presentation",
                light_stats["final_gid_changed"] > 0
                or light_stats["fg_changed"] > 0
                or light_stats["bk_changed"] > 0,
                json.dumps(light_stats, sort_keys=True),
            )
        if args.before_png and args.after_png:
            png_stats, png_failures = _check_png_cell_alignment(
                args.before_png,
                args.after_png,
                before_rendered_header,
                before_rendered,
                after_rendered,
            )
            failures.extend(png_failures)
            gate(
                "evidence_fl4260_png_cell_grid_matches_rendered_buffer",
                png_stats["cell_w"] > 0 and png_stats["cell_h"] > 0,
                json.dumps({
                    "png_width": png_stats["png_width"],
                    "png_height": png_stats["png_height"],
                    "grid_w": png_stats["grid_w"],
                    "grid_h": png_stats["grid_h"],
                    "cell_w": png_stats["cell_w"],
                    "cell_h": png_stats["cell_h"],
                }, sort_keys=True),
            )
            gate(
                "gameplay_fl4260_final_buffer_delta_visible_in_framebuffer",
                png_stats["rendered_changed_cells"] > 0
                and png_stats["rendered_changed_without_pixel_change"] == 0,
                json.dumps(png_stats, sort_keys=True),
            )
            gate(
                "gameplay_fl4260_framebuffer_delta_matches_final_buffer_cells",
                png_stats["pixel_changed_cells"] > 0
                and png_stats["pixel_changed_without_rendered_change"] == 0,
                json.dumps(png_stats, sort_keys=True),
            )

    passed = all(item["pass"] for item in gates.values()) and not failures
    return {
        "schema": "fl4260.termpp.pipeline_alignment.v1",
        "change_kind": args.change_kind,
        "target_material": args.target_material,
        "before_bridge": str(args.before_bridge),
        "before_rendered": str(args.before_rendered),
        "after_bridge": str(args.after_bridge) if args.after_bridge else None,
        "after_rendered": str(args.after_rendered) if args.after_rendered else None,
        "before_png": str(args.before_png) if args.before_png else None,
        "after_png": str(args.after_png) if args.after_png else None,
        "gates": gates,
        "failure_count": len(failures),
        "failures": failures[:20],
        "verdict": "PASS" if passed else "FAIL",
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before-bridge", required=True, type=Path)
    parser.add_argument("--before-rendered", required=True, type=Path)
    parser.add_argument("--after-bridge", type=Path)
    parser.add_argument("--after-rendered", type=Path)
    parser.add_argument("--before-png", type=Path)
    parser.add_argument("--after-png", type=Path)
    parser.add_argument("--change-kind", choices=("material-look", "light"), default="material-look")
    parser.add_argument("--target-material", type=int, default=1)
    parser.add_argument("--json-out", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if bool(args.after_bridge) != bool(args.after_rendered):
        parser.error("--after-bridge and --after-rendered must be provided together")
    if bool(args.before_png) != bool(args.after_png):
        parser.error("--before-png and --after-png must be provided together")
    if (args.before_png or args.after_png) and not (args.after_bridge and args.after_rendered):
        parser.error("PNG checks require --after-bridge and --after-rendered")
    try:
        result = evaluate(args)
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        result = {
            "schema": "fl4260.termpp.pipeline_alignment.v1",
            "verdict": "NO_EVIDENCE",
            "error": str(exc),
        }
        if args.json_out:
            args.json_out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
        print(json.dumps(result, indent=2, sort_keys=True))
        return 2
    if args.json_out:
        args.json_out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(f"verdict={result['verdict']} failures={result['failure_count']}")
        for name, gate_result in result["gates"].items():
            status = "PASS" if gate_result["pass"] else "FAIL"
            print(f"{status} {name}: {gate_result['detail']}")
        for failure in result["failures"][:5]:
            print("failure", json.dumps(failure, sort_keys=True))
    return 0 if result["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

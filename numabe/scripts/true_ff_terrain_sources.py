"""Read authoritative A3D corner/material inputs for the single bank compiler.

Matches A3DMapLoader's far-edge ownership, bilinear height interpolation and
zero-filled absent patches. No output writer or separate feature compiler.
"""
import importlib.util
from pathlib import Path


def map_source(path, bank_module, centered=True):
    spec = importlib.util.spec_from_file_location(
        "true_ff_map_codec", Path(__file__).with_name("compile_fl4789_chunk_catalog.py"))
    codec = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(codec)
    decoded = codec._decode_map(Path(path))
    owner = bank_module._gd_constants(bank_module.PROJ / "scripts/map/A3DMapLoader.gd")
    visual, hc, scale = owner["VISUAL_CELLS"], owner["HEIGHT_CELLS"], owner["HEIGHT_SCALE"]
    minx, miny, maxx, maxy = decoded["bounds"]
    width, height = (maxx - minx + 1) * visual, (maxy - miny + 1) * visual
    patches = {tuple(p["patch_coord"]): p for p in decoded["patches"]}
    heights = []
    for y in range(height + 1):
        py, vy = divmod(y, visual)
        if y == height:
            py, vy = py - 1, visual
        for x in range(width + 1):
            px, vx = divmod(x, visual)
            if x == width:
                px, vx = px - 1, visual
            patch = patches.get((minx + px, miny + py))
            if patch is None:
                heights.append(0.0)
                continue
            hx, hy = vx * hc / visual, vy * hc / visual
            x0, y0 = int(hx), int(hy)
            x1, y1 = min(x0 + 1, hc), min(y0 + 1, hc)
            tx, ty = hx - x0, hy - y0
            raw, stride = patch["corner_heights_u16"], hc + 1
            a = raw[y0 * stride + x0] * (1 - tx) + raw[y0 * stride + x1] * tx
            b = raw[y1 * stride + x0] * (1 - tx) + raw[y1 * stride + x1] * tx
            heights.append(bank_module.f32((a * (1 - ty) + b * ty) / scale))
    materials = bytearray(width * height)
    raw_elevation_flags = bytearray(width * height)
    for (px, py), patch in patches.items():
        ox, oy = (px - minx) * visual, (py - miny) * visual
        for vy in range(visual):
            for vx in range(visual):
                address = (oy + vy) * width + ox + vx
                visual_word = patch["visual_words_u16"][vy * visual + vx]
                materials[address] = visual_word & 255
                raw_elevation_flags[address] = (visual_word >> 15) & 1
    # A3DMapLoader.build_authoritative_elev_image owns the final material-row
    # selector. Reproduce its clamped six-neighbour law exactly; publishing the
    # raw map bit here would make the offline P6 paint a different surface from
    # the runtime for rises and falls.
    elevations = bytearray(width * height)
    for y in range(height):
        y_lo = max(y - 1, 0)
        y_hi = min(y + 1, height - 1)
        for x in range(width):
            x_hi = min(x + 1, width - 1)
            e_mi = raw_elevation_flags[y * width + x] + raw_elevation_flags[y * width + x_hi]
            e_lo = raw_elevation_flags[y_lo * width + x] + raw_elevation_flags[y_lo * width + x_hi]
            e_hi = raw_elevation_flags[y_hi * width + x] + raw_elevation_flags[y_hi * width + x_hi]
            if e_mi == 0:
                elevation_row = 3
            elif e_mi >= 2:
                elevation_row = 1
            elif e_lo <= 0:
                elevation_row = 3 if e_hi <= 0 else 2
            else:
                elevation_row = 0 if e_hi <= 0 else 1
            elevations[y * width + x] = elevation_row
    center = decoded["center_offset"] if centered else (0, 0)
    return {"heights": heights, "materials": list(materials), "elevations": list(elevations),
            "dims": [width, height],
            "origin": [minx * visual - center[0], miny * visual - center[1]],
            "cell": 1.0, "water_level": bank_module.terrain_water_surface_y()}

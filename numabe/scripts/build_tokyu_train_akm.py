#!/usr/bin/env python3
"""Build the Classic Tokyu 7000 series (Tamagawa Line) train meshes (FL-4876).

Writes ASCII-PLY ``.akm`` meshes for the 3-car Tokyu 7000 series set that
runs on the Tokyu Tamagawa Line, plus a small source/consist json:

  assets/meshes/tokyu/tokyu_7000_7100_mc.akm   car 1, DeHa 7100 (Mc), cab at +x
  assets/meshes/tokyu/tokyu_7000_7200_m.akm    car 2, DeHa 7200 (M), 2 pantographs
  assets/meshes/tokyu/tokyu_7000_7300_tc.akm   car 3, KuHa 7300 (Tc), cab at -x
  assets/meshes/tokyu/tokyu_7000_3car.akm      the three cars joined (fallback)
  assets/meshes/tokyu/tokyu_7000_3car.json     sources, dimensions, consist

The Classic runtime (engine/classic_train.cpp) reads the consist from
``tokyu_7000_3car.json`` and places one instance per car, so the set bends
at car boundaries on a curved track.  Without the json it moves the joined
3-car mesh as one rigid body.

Real-world facts (sources):
  * Wikipedia, "Tokyu 7000 series" (https://en.wikipedia.org/wiki/Tokyu_7000_series):
    in service on the Ikegami and Tokyu Tamagawa lines since December 2007;
    3-car sets DeHa 7100 (Mc) - DeHa 7200 (M) - KuHa 7300 (Tc);
    car length 18,100 mm (end cars) / 18,000 mm (intermediate car);
    width 2,800 mm; height 4,050 mm; 3 pairs of doors per side;
    stainless-steel body; car 2 carries two single-arm pantographs.
  * Japanese Wikipedia, "東急7000系電車 (2代)"
    (https://ja.wikipedia.org/wiki/東急7000系電車_(2代)):
    unpainted stainless body with a design in light and dark shades of
    green; the roof paint is also green; streamlined cab front with the
    emergency door offset to the right seen from the cab; LED destination
    display; one fixed window at each car end and two drop windows between
    the passenger doors; one roof air conditioner per car.

Unit convention (stated, measured from existing assets):
  An AKM vertex is in map units = metres x content_scale on all three axes,
  and the placed instance carries a z scale of HEIGHT_SCALE = 16 in its
  transform.  Evidence: build_reg_15/meshes/street_lamp.akm spans
  z 0..17.107 = 5.0 m x 3.4213 (ARCHETYPE_PHYSICAL street_lamp is 5.0 m tall)
  and is placed with tm[10] = 16; the 10 m utility pole spans z 0..34.213.
  (Those spans are the pre-2026-10-13 numbers; they now scale by 1.5.)
  content_scale for the Numabe build is units_per_metre =
  geo_frames.WORLD_UNITS_PER_METRE (5.132 since the operator 1.5x map request)
  in output.trains.json.  Local axes: +x = forward along the track,
  +y = left, +z = up; origin = car (or set) centre at rail-top height.

Colours are snapped to the Classic terminal-safe 6x6x6 cube (the same
SAFE_LEVELS snap scripts/pipeline/compile_facade_shell.write_akm applies);
alpha 255 marks every face presentation-only (no collision), as the facade
shell writer does.

usage: python3 scripts/build_tokyu_train_akm.py [--out-dir DIR] [--units-per-metre U]
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "assets" / "meshes" / "tokyu"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts.pipeline import geo_frames as GF  # noqa: E402

# Derived, not a literal: the train mesh must match the run's content_scale,
# which is GF.WORLD_UNITS_PER_METRE (sprite calibration x OPERATOR_MAP_SCALE,
# 5.132 wu/m since the 2026-10-13 operator 1.5x map request).
DEFAULT_UPM = GF.WORLD_UNITS_PER_METRE

SAFE_LEVELS = (0, 51, 102, 153, 204, 255)
ALPHA_NO_COLLISION = 255

# Livery (exact intent, snapped on write).
STAINLESS = (204, 204, 204)
STAINLESS_DARK = (153, 153, 153)
DOOR = (153, 153, 153)
GLASS = (51, 51, 51)
CAB_GLASS = (0, 0, 0)
GREEN_DARK = (0, 102, 51)
GREEN_LIGHT = (102, 204, 102)
ROOF = (102, 153, 102)          # green roof paint
ROOF_EQUIP = (153, 153, 153)
UNDERFRAME = (51, 51, 51)
BOGIE = (51, 51, 51)
WHEEL = (0, 0, 0)
PANTO = (102, 102, 102)
HEADLIGHT = (255, 255, 204)
TAILLIGHT = (204, 0, 0)
DEST_LED = (255, 153, 0)
GANGWAY = (51, 51, 51)

# Dimensions in metres (sources above; details marked "est." are estimates
# typical of Japanese 18 m commuter stock, not published figures).
CAR_LEN_END = 18.1
CAR_LEN_MID = 18.0
HALF_W = 1.4                     # 2,800 mm width
FLOOR_Z = 1.15                   # est. floor height above rail
BODY_Z0 = 1.0                    # est. body bottom (skirt top)
BODY_Z1 = 3.62                   # est. body side top (eaves)
ROOF_Z = 3.80                    # est. roof crown
HEIGHT = 4.05                    # published height (pantograph locked down)
BOGIE_CENTRES = 6.3              # est. half bogie-centre distance
DOOR_W = 1.3                     # est. door width
DOOR_X = (-6.0, 0.0, 6.0)        # 3 doors per side
WIN_Z = (1.95, 2.85)             # est. window band
GREEN_BANDS = ((1.62, 1.74, GREEN_DARK), (1.74, 1.82, GREEN_LIGHT),
               (3.30, 3.36, GREEN_LIGHT), (3.36, 3.44, GREEN_DARK))


class MeshBuilder:
    def __init__(self) -> None:
        self.verts: list[tuple] = []
        self.faces: list[list[int]] = []

    def quad(self, pts, rgb, normal=None) -> None:
        if normal is None:
            a, b, c = pts[0], pts[1], pts[2]
            u = [b[i] - a[i] for i in range(3)]
            v = [c[i] - a[i] for i in range(3)]
            n = (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0])
            ln = math.sqrt(sum(x * x for x in n)) or 1.0
            normal = tuple(x / ln for x in n)
        first = len(self.verts)
        for p in pts:
            self.verts.append((*p, *normal, *rgb, ALPHA_NO_COLLISION))
        self.faces.append(list(range(first, first + len(pts))))

    def box(self, x0, x1, y0, y1, z0, z1, rgb, skip=()) -> None:
        """Axis-aligned box with outward normals; skip names faces to omit."""
        planes = {
            "-x": ((-1, 0, 0), ((x0, y0, z0), (x0, y0, z1), (x0, y1, z1), (x0, y1, z0))),
            "+x": ((1, 0, 0), ((x1, y0, z0), (x1, y1, z0), (x1, y1, z1), (x1, y0, z1))),
            "-y": ((0, -1, 0), ((x0, y0, z0), (x1, y0, z0), (x1, y0, z1), (x0, y0, z1))),
            "+y": ((0, 1, 0), ((x0, y1, z0), (x0, y1, z1), (x1, y1, z1), (x1, y1, z0))),
            "-z": ((0, 0, -1), ((x0, y0, z0), (x0, y1, z0), (x1, y1, z0), (x1, y0, z0))),
            "+z": ((0, 0, 1), ((x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1))),
        }
        for name, (normal, pts) in planes.items():
            if name not in skip:
                self.quad(pts, rgb, normal)

    def side_panel(self, x0, x1, z0, z1, rgb, inset=0.0) -> None:
        """Thin coloured panel on both body sides (slightly proud of the side)."""
        y = HALF_W + 0.01 - inset
        self.quad(((x0, -y, z0), (x1, -y, z0), (x1, -y, z1), (x0, -y, z1)), rgb, (0, -1, 0))
        self.quad(((x0, y, z0), (x0, y, z1), (x1, y, z1), (x1, y, z0)), rgb, (0, 1, 0))

    def wheel(self, x, y, r, rgb) -> None:
        """Octagonal wheel disc in the x-z plane, faced both ways (4 quads)."""
        pts = [(x + r * math.cos(k * math.pi / 4), y, r + r * math.sin(k * math.pi / 4))
               for k in range(8)]
        for flip in (False, True):
            for a, b, c, d in ((0, 1, 2, 3), (3, 4, 5, 6), (6, 7, 0, 3)):
                q = [pts[a], pts[b], pts[c], pts[d]]
                self.quad(q[::-1] if flip else q, rgb)

    def extend(self, other: "MeshBuilder", dx: float) -> None:
        base = len(self.verts)
        for v in other.verts:
            self.verts.append((v[0] + dx, *v[1:]))
        for f in other.faces:
            self.faces.append([i + base for i in f])


def _bogie(m: MeshBuilder, xc: float) -> None:
    m.box(xc - 1.25, xc + 1.25, -1.05, 1.05, 0.38, 0.82, BOGIE)          # frame
    m.box(xc - 0.35, xc + 0.35, -1.25, 1.25, 0.45, 0.75, BOGIE)          # bolsterless cross beam
    for ax in (-1.05, 1.05):                                             # 2.1 m wheelbase (est.)
        for side in (-0.72, 0.72):
            m.wheel(xc + ax, side, 0.43, WHEEL)                          # 860 mm wheels (est.)


def _body(m: MeshBuilder, length: float, cab: int, pantographs: int) -> None:
    """One car. cab = +1 (cab at +x), -1 (cab at -x) or 0 (no cab)."""
    hx = length / 2.0
    cab_len = 1.6
    bx0 = -hx + (cab_len if cab == -1 else 0.0)
    bx1 = hx - (cab_len if cab == 1 else 0.0)
    # Main stainless shell (ends skipped where the cab continues it).
    m.box(bx0, bx1, -HALF_W, HALF_W, BODY_Z0, BODY_Z1, STAINLESS,
          skip=(("+x",) if cab == 1 else ()) + (("-x",) if cab == -1 else ()))
    # Roof: green-painted crown, slightly narrower (two-step curve).
    m.box(bx0, bx1, -HALF_W + 0.12, HALF_W - 0.12, BODY_Z1, ROOF_Z, ROOF,
          skip=(("+x",) if cab == 1 else ()) + (("-x",) if cab == -1 else ()))
    # Underframe equipment boxes between bogies.
    m.box(-BOGIE_CENTRES + 1.6, BOGIE_CENTRES - 1.6, -1.15, 1.15, 0.55, BODY_Z0, UNDERFRAME)
    # Green livery bands along both sides.
    for z0, z1, rgb in GREEN_BANDS:
        m.side_panel(bx0, bx1, z0, z1, rgb)
    # Doors (3 pairs per side) with their door windows.
    for dxc in DOOR_X:
        m.side_panel(dxc - DOOR_W / 2, dxc + DOOR_W / 2, BODY_Z0 + 0.15, 3.05, DOOR, inset=-0.005)
        m.side_panel(dxc - DOOR_W / 2 + 0.12, dxc - 0.05, WIN_Z[0], WIN_Z[1], GLASS, inset=-0.01)
        m.side_panel(dxc + 0.05, dxc + DOOR_W / 2 - 0.12, WIN_Z[0], WIN_Z[1], GLASS, inset=-0.01)
    # Passenger windows: two between each door pair, one fixed at each end.
    for wx0, wx1 in ((-5.15, -3.15), (-2.85, -0.85), (0.85, 2.85), (3.15, 5.15)):
        m.side_panel(wx0, wx1, WIN_Z[0], WIN_Z[1], GLASS, inset=-0.005)
    if cab != -1:
        m.side_panel(-hx + 0.5, -hx + 1.8, WIN_Z[0], WIN_Z[1], GLASS, inset=-0.005)
    if cab != 1:
        m.side_panel(hx - 1.8, hx - 0.5, WIN_Z[0], WIN_Z[1], GLASS, inset=-0.005)
    # Gangway bellows on non-cab ends.
    if cab != 1:
        m.box(hx - 0.05, hx + 0.0, -0.55, 0.55, FLOOR_Z, 3.15, GANGWAY)
    if cab != -1:
        m.box(-hx - 0.0, -hx + 0.05, -0.55, 0.55, FLOOR_Z, 3.15, GANGWAY)
    # Roof air conditioner (one per car).
    m.box(-1.9, 1.9, -0.95, 0.95, ROOF_Z, ROOF_Z + 0.25, ROOF_EQUIP)
    # Bogies.
    for xc in (-BOGIE_CENTRES, BOGIE_CENTRES):
        _bogie(m, xc)
    # Pantographs (single arm, raised to a 5.1 m contact height).
    for k in range(pantographs):
        px = (-1, 1)[k] * 5.6
        m.box(px - 0.8, px + 0.8, -0.6, 0.6, ROOF_Z, ROOF_Z + 0.12, PANTO)           # base frame
        knee_x = px + 0.9
        z_base, z_knee, z_head = ROOF_Z + 0.12, 4.55, 5.05
        for y in (-0.06, 0.06):
            m.quad(((px - 0.6, y, z_base), (knee_x, y, z_knee),
                    (knee_x, y, z_knee + 0.06), (px - 0.6, y, z_base + 0.06)), PANTO)  # lower arm
            m.quad(((knee_x, y, z_knee), (px, y, z_head),
                    (px, y, z_head + 0.05), (knee_x, y, z_knee + 0.06)), PANTO)        # upper arm
        m.box(px - 0.12, px + 0.12, -0.8, 0.8, z_head, z_head + 0.06, PANTO)          # pan head
    if cab:
        _cab(m, hx, cab, cab_len)


def _cab(m: MeshBuilder, hx: float, cab: int, cab_len: float) -> None:
    """Streamlined cab: vertical lower front, raked windscreen, offset door."""
    s = cab
    xb = s * (hx - cab_len)          # cab section start
    xf = s * hx                      # front face
    xr = s * (hx - 0.55)             # top of the raked windscreen

    def X(x0, x1):
        return (min(x0, x1), max(x0, x1))

    # Cab body block below the windscreen (stainless) and its side windows.
    x0, x1 = X(xb, xf)
    m.box(x0, x1, -HALF_W, HALF_W, BODY_Z0, 2.15, STAINLESS, skip=("-x",) if s > 0 else ("+x",))
    # Side walls of the upper cab (between body top and the raked screen).
    xs0, xs1 = X(xb, xr)
    m.box(xs0, xs1, -HALF_W, HALF_W, 2.15, BODY_Z1, STAINLESS, skip=("-x", "+x"))
    m.box(xs0, xs1, -HALF_W + 0.12, HALF_W - 0.12, BODY_Z1, ROOF_Z, ROOF, skip=("-x", "+x"))
    for y in (-HALF_W - 0.01, HALF_W + 0.01):
        nrm = (0, -1 if y < 0 else 1, 0)
        wx0, wx1 = X(xb + s * 0.2, xr - s * 0.1)
        pts = ((wx0, y, WIN_Z[0]), (wx1, y, WIN_Z[0]), (wx1, y, WIN_Z[1]), (wx0, y, WIN_Z[1]))
        m.quad(pts if y < 0 else pts[::-1], CAB_GLASS, nrm)
        # Green bands continue around the cab sides.
        for z0, z1, rgb in GREEN_BANDS[:2]:
            px0, px1 = X(xb, xf)
            pts = ((px0, y, z0), (px1, y, z0), (px1, y, z1), (px0, y, z1))
            m.quad(pts if y < 0 else pts[::-1], rgb, nrm)
    # Raked windscreen: from (xf, 2.15) up to (xr, ROOF_Z); black glass.
    lean = (math.copysign(1.0, s) * (ROOF_Z - 2.15), 0.0, abs(xf - xr))
    ln = math.hypot(lean[0], lean[2])
    nrm = (lean[0] / ln, 0.0, lean[2] / ln)
    pts = [(xf, -HALF_W, 2.15), (xf, HALF_W, 2.15), (xr, HALF_W, ROOF_Z), (xr, -HALF_W, ROOF_Z)]
    m.quad(pts if s > 0 else pts[::-1], CAB_GLASS, nrm)
    # Side triangles closing the rake (as degenerate-free quads -> triangles).
    for y in (-HALF_W, HALF_W):
        tri = [(xf, y, 2.15), (xr, y, ROOF_Z), (xr, y, 2.15)]
        if (y > 0) == (s > 0):
            tri = tri[::-1]
        m.quad(tri, STAINLESS)
    # Front lower face details: green band across the front, lamps, LED.
    face_x = xf + s * 0.01
    def front_panel(y0, y1, z0, z1, rgb, out=0.01):
        fx = xf + s * out
        pts = [(fx, y0, z0), (fx, y1, z0), (fx, y1, z1), (fx, y0, z1)]
        m.quad(pts if s > 0 else pts[::-1], rgb, (s, 0, 0))
    for z0, z1, rgb in GREEN_BANDS[:2]:
        front_panel(-HALF_W, HALF_W, z0, z1, rgb)
    # Offset emergency door (right seen from the cab = -y for a +x cab).
    dy = -s * 0.75
    front_panel(dy - 0.35, dy + 0.35, BODY_Z0 + 0.1, 2.12, STAINLESS_DARK, out=0.015)
    # Headlights (two, low) and taillights.
    for y in (-0.95, 0.95):
        front_panel(y - 0.22, y + 0.22, 1.30, 1.50, HEADLIGHT, out=0.02)
        front_panel(y - 0.22, y + 0.22, 1.05, 1.22, TAILLIGHT, out=0.02)
    # LED destination display on the screen top.
    lx0, lx1 = X(xr - s * 0.02, xr + s * 0.10)
    m.box(lx0, lx1, -0.7, 0.4, ROOF_Z - 0.32, ROOF_Z - 0.08, DEST_LED)
    # Skirt (snowplough) below the cab.
    sx0, sx1 = X(xf - s * 0.9, xf - s * 0.05)
    m.box(sx0, sx1, -1.2, 1.2, 0.25, BODY_Z0, UNDERFRAME)
    _ = face_x


def build_car(kind: str) -> tuple[MeshBuilder, float]:
    m = MeshBuilder()
    if kind == "mc":
        _body(m, CAR_LEN_END, cab=1, pantographs=0)
        return m, CAR_LEN_END
    if kind == "m":
        _body(m, CAR_LEN_MID, cab=0, pantographs=2)
        return m, CAR_LEN_MID
    _body(m, CAR_LEN_END, cab=-1, pantographs=0)
    return m, CAR_LEN_END


def _snap(value: int) -> int:
    return min(SAFE_LEVELS, key=lambda level: abs(level - value))


def write_akm(path: Path, m: MeshBuilder, scale: float, comment: str) -> None:
    lines = ["ply", "format ascii 1.0", f"comment {comment}",
             f"element vertex {len(m.verts)}",
             "property float x", "property float y", "property float z",
             "property float nx", "property float ny", "property float nz",
             "property uchar red", "property uchar green",
             "property uchar blue", "property uchar alpha",
             f"element face {len(m.faces)}",
             "property list uchar uint vertex_indices", "end_header"]
    for (x, y, z, nx, ny, nz, r, g, b, a) in m.verts:
        lines.append(f"{x * scale:.3f} {y * scale:.3f} {z * scale:.3f} "
                     f"{nx:.2f} {ny:.2f} {nz:.2f} {_snap(r)} {_snap(g)} {_snap(b)} {a}")
    for f in m.faces:
        if len(f) not in (3, 4) or len(set(f)) != len(f):
            raise ValueError(f"bad face {f}")
        lines.append(f"{len(f)} " + " ".join(str(i) for i in f))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--units-per-metre", type=float, default=DEFAULT_UPM)
    a = ap.parse_args(argv)
    a.out_dir.mkdir(parents=True, exist_ok=True)
    upm = a.units_per_metre
    cars = [("tokyu_7000_7100_mc.akm", "mc", "DeHa 7100 (Mc)"),
            ("tokyu_7000_7200_m.akm", "m", "DeHa 7200 (M)"),
            ("tokyu_7000_7300_tc.akm", "tc", "KuHa 7300 (Tc)")]
    consist = []
    joined = MeshBuilder()
    built = [(name, label, *build_car(kind)) for name, kind, label in cars]
    total = sum(length for _, _, _, length in built)
    x = total / 2.0
    for name, label, mesh, length in built:
        write_akm(a.out_dir / name, mesh, upm,
                  f"FL-4876 Tokyu 7000 series {label} - presentation only, alpha=255 => no collision")
        centre = x - length / 2.0
        joined.extend(mesh, centre)
        consist.append({"mesh": name, "car": label, "length_m": length,
                        "faces": len(mesh.faces)})
        x -= length
    write_akm(a.out_dir / "tokyu_7000_3car.akm", joined, upm,
              "FL-4876 Tokyu 7000 series 3-car set - presentation only, alpha=255 => no collision")
    doc = {
        "schema": "y92.classic_train_mesh.v1",
        "generator": "scripts/build_tokyu_train_akm.py",
        "train": "Tokyu 7000 series (2007), Tokyu Tamagawa Line 3-car set",
        "units": {"vertex": "map units = metres x units_per_metre on x, y, z",
                  "units_per_metre": upm, "instance_z_scale": 16.0,
                  "axes": "+x forward along track, +y left, +z up; origin car centre at rail top"},
        "dimensions_m": {"car_length_end": CAR_LEN_END, "car_length_mid": CAR_LEN_MID,
                         "width": HALF_W * 2, "height": HEIGHT, "set_length": total},
        "livery": "unpainted stainless with dark/light green bands, green roof, black raked windscreen",
        "sources": [
            "https://en.wikipedia.org/wiki/Tokyu_7000_series",
            "https://ja.wikipedia.org/wiki/東急7000系電車_(2代)",
        ],
        "consist": consist,
        "joined_mesh": "tokyu_7000_3car.akm",
    }
    (a.out_dir / "tokyu_7000_3car.json").write_text(
        json.dumps(doc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    for c in consist:
        print(f"[tokyu] {c['mesh']} {c['car']} {c['length_m']} m faces={c['faces']}")
    print(f"[tokyu] tokyu_7000_3car.akm set length {total:.1f} m faces={len(joined.faces)}")
    print(f"[tokyu] wrote {a.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

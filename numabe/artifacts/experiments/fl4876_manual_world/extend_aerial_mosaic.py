"""extend_aerial_mosaic.py SRC_TILE_DIR [--y0 N --y1 N] -- data acquisition only (FL-4876 Tama river).

Adds GSI seamless-photo z18 tiles (18_<x>_<y>.jpg, fetched by the operator-approved download) to the editor's aerial
tile cache and writes the WIDE mosaic aerial/mosaic_wide.jpg + mosaic_wide.json (same json schema as fetch_aerial.py).
The original aerial/mosaic.jpg / mosaic.json are NOT touched: map_editor.py reads the affine once at server start and
uses mosaic_wide.* when it exists, so a running server keeps a registered overlay until it is restarted.
The tile range is the existing range extended west/east to cover SRC_TILE_DIR (rows stay the existing rows unless
--y0/--y1 are given). Missing tiles stay black. Decides nothing about any building. Source: 国土地理院 シームレス空中写真, CC BY 4.0.
Run isolated: python3 -I extend_aerial_mosaic.py SRC (the downloaded JPEGs are untrusted data).
"""
import json
import shutil
import sys
from pathlib import Path

try:
    from PIL import Image
except ImportError:  # python3 -I drops the user site; Pillow lives there on this machine
    sys.path.append(str(Path.home() / "Library/Python/3.14/lib/python/site-packages"))
    from PIL import Image

HERE = Path(__file__).resolve().parent
A = HERE / "aerial"


def main(argv):
    src = Path(argv[0])
    meta = json.loads((A / "mosaic.json").read_text())
    z = meta["zoom"]
    have = {}
    for p in list((A / "tiles").glob(f"{z}_*_*.jpg")) + list(src.glob(f"{z}_*_*.jpg")):
        _, x, y = p.stem.split("_")
        have.setdefault((int(x), int(y)), p)
    ys = [int(argv[argv.index("--y0") + 1]) if "--y0" in argv else meta["tile_y0"],
          int(argv[argv.index("--y1") + 1]) if "--y1" in argv else meta["tile_y0"] + meta["tiles_y"] - 1]
    new_x = [x for (x, y) in have if ys[0] <= y <= ys[1]]
    x0 = min(min(new_x), meta["tile_x0"])
    x1 = max(max(new_x), meta["tile_x0"] + meta["tiles_x"] - 1)
    (A / "tiles").mkdir(parents=True, exist_ok=True)
    mosaic = Image.new("RGB", ((x1 - x0 + 1) * 256, (ys[1] - ys[0] + 1) * 256))
    placed = 0
    for x in range(x0, x1 + 1):
        for y in range(ys[0], ys[1] + 1):
            p = have.get((x, y))
            if p is None:
                continue
            dst = A / "tiles" / f"{z}_{x}_{y}.jpg"
            if not dst.is_file():
                shutil.copyfile(p, dst)  # keep the tile beside the others (cache of what the mosaic was built from)
            try:
                im = Image.open(dst)
                im.load()
                mosaic.paste(im.convert("RGB"), ((x - x0) * 256, (y - ys[0]) * 256))
                placed += 1
            except Exception as e:
                print("tile failed", dst.name, e)
    mosaic.save(A / "mosaic_wide.jpg", quality=92)
    (A / "mosaic_wide.json").write_text(json.dumps({
        "source": meta["source"], "zoom": z, "tile_x0": x0, "tile_y0": ys[0], "tiles_x": x1 - x0 + 1, "tiles_y": ys[1] - ys[0] + 1,
        "pixel_of_lonlat": meta["pixel_of_lonlat"].replace("tile_x0", "tile_x0") + " (wide mosaic: original range plus the Tama strip)"},
        indent=1))
    print("mosaic_wide", mosaic.size, "tiles placed", placed, "of", (x1 - x0 + 1) * (ys[1] - ys[0] + 1), "x", x0, "-", x1, "y", ys[0], "-", ys[1])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

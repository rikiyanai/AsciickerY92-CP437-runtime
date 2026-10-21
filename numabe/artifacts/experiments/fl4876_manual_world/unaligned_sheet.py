"""unaligned_sheet.py OUT_DIR -- reading aid (decides nothing): contact sheets of the panoramas that still have no
heading_offset_deg, 6 per page, with index, id tail, native size and a red tick at the image centre column."""
import json, sys
from pathlib import Path
from PIL import Image, ImageDraw
ROOT = Path(__file__).resolve().parents[3]
CAP = ROOT / "artifacts/experiments/fl4810_aoi_street_network_boxing_20260915/captures"
out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
d = json.loads((ROOT / "assets/source_bundles/numabe_connected_neighborhood_scope_20260915/authored_building_appearance.json").read_text())
ks = [k for k, v in d["panoramas"].items() if v.get("heading_offset_deg") is None]
for page in range(0, len(ks), 6):
    sheet = Image.new("RGB", (3 * 400, 2 * 320)); dr = ImageDraw.Draw(sheet)
    for i, k in enumerate(ks[page:page + 6]):
        src = Image.open(CAP / (k + ".jpg")).convert("RGB"); w, h = src.size
        im = src.copy(); im.thumbnail((400, 300))
        x, y = (i % 3) * 400, (i // 3) * 320
        sheet.paste(im, (x, y + 18))
        dr.text((x + 4, y + 2), "%d %s %dx%d" % (page + i, k[-10:], w, h), fill=(255, 255, 0))
        dr.line([(x + im.size[0] // 2, y + 18), (x + im.size[0] // 2, y + 30)], fill=(255, 0, 0), width=2)
    sheet.save(out / ("p%d.png" % (page // 6)))
print(len(ks), [k for k in ks])

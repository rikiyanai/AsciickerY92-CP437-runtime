"""operator_photo_intake.py SRC... -- preserve operator-supplied reference photos (2026-10-13 chat: the home block
17-16 Den-en-chofu Honcho, which no Mapillary panorama shows) under operator_photos/ with sha256 names, and print
median RGB of small boxes for hand sampling:  operator_photo_intake.py --sample NAME x0 y0 x1 y1 ..."""
import hashlib, json, shutil, sys
from pathlib import Path
from PIL import Image
OUT = Path(__file__).resolve().parent / "operator_photos"
OUT.mkdir(exist_ok=True)
if sys.argv[1] == "--sample":
    im = Image.open(OUT / sys.argv[2]).convert("RGB")
    v = [int(a) for a in sys.argv[3:]]
    for i in range(0, len(v), 4):
        box = im.crop(tuple(v[i:i + 4])); px = sorted(box.getdata())
        chan = [sorted(p[c] for p in px)[len(px) // 2] for c in range(3)]
        print(v[i:i + 4], "median", chan)
    sys.exit(0)
idx_path = OUT / "index.json"
idx = json.loads(idx_path.read_text()) if idx_path.exists() else {}
for src in sys.argv[1:]:
    data = Path(src).read_bytes(); h = hashlib.sha256(data).hexdigest()[:16]
    name = "op_%s%s" % (h, Path(src).suffix.lower())
    if not (OUT / name).exists():
        shutil.copyfile(src, OUT / name)
    w, hgt = Image.open(OUT / name).size
    idx[name] = {"sha256_16": h, "source_name": Path(src).name, "size": [w, hgt], "supplied": "operator chat 2026-10-13",
                 "subject": "home block 17-16 Den-en-chofu Honcho (operator reference, Street View screenshot)"}
    print(name, w, hgt)
idx_path.write_text(json.dumps(idx, ensure_ascii=False, indent=1) + "\n")

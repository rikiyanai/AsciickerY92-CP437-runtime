# Ad hoc script: Extract embedded image subresources from a Safari .webarchive into a local ref folder (moonlight reference gathering)
# Created: 2026-06-25
# Canonical gap: <describe what tool should own this>

import plistlib, sys, os, pathlib

src = "/Users/r/Downloads/The Midnight Hour - Atlantic '41 by StephanRewind.webarchive"
out = pathlib.Path("/Users/r/Downloads/midnight-hour-ref")
out.mkdir(parents=True, exist_ok=True)

with open(src, "rb") as f:
    pl = plistlib.load(f)

subs = pl.get("WebSubresources", [])
ext_map = {"image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif", "image/svg+xml": ".svg"}
count = 0
manifest = []
for i, r in enumerate(subs):
    mime = r.get("WebResourceMIMEType", "")
    url = r.get("WebResourceURL", "")
    data = r.get("WebResourceData", b"")
    if mime not in ext_map:
        continue
    # keep only the large content images (itch.zone originals + large pngs); skip tiny icons/svgs
    if mime == "image/svg+xml":
        continue
    if "/original/" not in url and len(data) < 20000:
        continue
    ext = ext_map[mime]
    name = f"{i:03d}_{os.path.basename(url.split('?')[0])[:24]}{ext}"
    (out / name).write_bytes(data)
    manifest.append((name, mime, len(data), url))
    count += 1

print(f"Extracted {count} images to {out}")
for name, mime, sz, url in manifest:
    print(f"  {name:40s} {mime:12s} {sz//1024:5d}KB  {url[:70]}")

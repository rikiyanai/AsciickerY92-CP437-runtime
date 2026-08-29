# Ad hoc script: Verify FL-4208 paper bibliography anchors for temporal coherence references
# Created: 2026-06-29
# Canonical gap: <describe what tool should own this>

import urllib.request
urls = [
    'https://doi.org/10.2312/egst.20111060',
    'https://doi.org/10.1111/cgf.142613',
    'https://doi.org/10.1145/3105762.3105770',
]
for url in urls:
    req = urllib.request.Request(url, headers={'User-Agent':'asciicker-y9-2-bib-audit'})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            print(url, r.status, r.geturl())
    except Exception as exc:
        print(url, type(exc).__name__, exc)

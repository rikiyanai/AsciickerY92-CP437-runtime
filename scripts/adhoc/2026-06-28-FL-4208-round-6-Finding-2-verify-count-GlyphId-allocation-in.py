# Ad hoc script: FL-4208 round-6 Finding-2 verify: count GlyphId allocation in additive + morphology shape catalogs, prove 512-671 packing and next-free id
# Created: 2026-06-28
# Canonical gap: <describe what tool should own this>

import json, sys
add = json.load(open('assets/glyphs/generated/material.additive.v1.shape_catalog.json'))
mor = json.load(open('assets/glyphs/generated/material.morphology.v2.shape_catalog.json'))

def ids(cat):
    out = []
    ents = cat.get('entries')
    if isinstance(ents, dict):
        ents = list(ents.values())
    for e in ents or []:
        for k in ('glyph_id','glyphId','id','gid'):
            if isinstance(e, dict) and k in e:
                out.append(e[k]); break
    return out

for name, cat in (('additive', add), ('morphology', mor)):
    gi = ids(cat)
    ints = [g for g in gi if isinstance(g, int)]
    print(f"== {name} ==")
    print("  entries:", len(cat.get('entries') or []))
    print("  glyph_id values found:", len(gi), " int:", len(ints), " distinct:", len(set(ints)))
    if ints:
        ints_s = sorted(set(ints))
        print("  min:", ints_s[0], " max:", ints_s[-1])
        contiguous = (ints_s == list(range(ints_s[0], ints_s[-1]+1)))
        print("  contiguous:", contiguous, " span:", ints_s[-1]-ints_s[0]+1)
    else:
        # dump one sample entry to see field names
        ents = cat.get('entries')
        if isinstance(ents, dict): ents=list(ents.values())
        print("  SAMPLE ENTRY KEYS:", list(ents[0].keys()) if ents else None)

# Ad hoc script: FL-4231 owner-product cell-dump analyzer: counts owner_product_kind + visibility_winner, lists actor cells with owner kind/alpha/blocked flags, localizes overdraw (actor_occ_present vs owner_product_kind)
# Created: 2026-06-15
# Canonical gap: <describe what tool should own this>

import json, sys, collections, os
d = sys.argv[1] if len(sys.argv) > 1 else "."
cells_path = os.path.join(d, "cells.jsonl")
rows = [json.loads(l) for l in open(cells_path) if l.strip()]
print(f"DUMP: {d}")
print(f"cells: {len(rows)}")
print(f"files: {sorted(os.listdir(d))}")
def cnt(key):
    c = collections.Counter(r.get(key) for r in rows)
    return dict(sorted(c.items(), key=lambda kv: -kv[1]))
def is_actor_owner(kind):
    return kind in ("actor", "actor_sprite")
print("\n== owner_product_kind ==")
for k,v in cnt("owner_product_kind").items(): print(f"  {k!r:18} {v}")
print("\n== visibility_winner ==")
for k,v in cnt("visibility_winner").items(): print(f"  {k!r:32} {v}")
print("\n== row_type ==")
for k,v in cnt("row_type").items(): print(f"  {k!r:20} {v}")
# actor cells
actor_cells = [r for r in rows if r.get("actor_occ_present") or is_actor_owner(r.get("owner_product_kind")) or (r.get("owner_product_actor_pixels") or 0) > 0]
print(f"\n== actor-relevant cells: {len(actor_cells)} ==")
for r in actor_cells[:40]:
    sc = r.get("screen_cell") or {}
    print(f"  cell=({sc.get('x')},{sc.get('y')}) owner={r.get('owner_product_kind')} "
          f"actor_px={r.get('owner_product_actor_pixels')} terr_px={r.get('owner_product_terrain_pixels')} "
          f"stat_px={r.get('owner_product_static_pixels')} none_px={r.get('owner_product_none_pixels')} "
          f"occ_present={r.get('actor_occ_present')} blocked_by_terrain={r.get('actor_blocked_by_terrain')} "
          f"static_winner={r.get('actor_static_world_winner')} vis_winner={r.get('visibility_winner')} "
          f"actor_id={r.get('actor_id')} actor_kind={r.get('actor_kind')}")
# overdraw localization: actor owner but blocked by terrain (should NOT be actor)
overdraw = [r for r in rows if is_actor_owner(r.get("owner_product_kind")) and r.get("actor_blocked_by_terrain")]
print(f"\n== POTENTIAL OVERDRAW (owner=actor AND actor_blocked_by_terrain): {len(overdraw)} ==")
for r in overdraw[:20]:
    sc=r.get("screen_cell") or {}
    print(f"  cell=({sc.get('x')},{sc.get('y')}) actor_px={r.get('owner_product_actor_pixels')} terr_px={r.get('owner_product_terrain_pixels')}")

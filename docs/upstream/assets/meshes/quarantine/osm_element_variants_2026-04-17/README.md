Quarantine for numeric-suffix `element.*.akm` meshes moved out of the root mesh library on 2026-04-17.

Reason:
- unreferenced by tracked `.a3d` files
- not part of the canonical fixture path
- likely leftover OSM/buildify export spill

Kept live at root:
- `assets/meshes/element.akm`

Planned next step:
- delete this folder after the OSM pipeline is re-verified on the intended bbox lanes

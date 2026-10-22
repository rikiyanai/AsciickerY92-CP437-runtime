# CLI-Anything Blender

Drive Blender headlessly from the command line. Every GUI action becomes a CLI command.

## Installation

```bash
cd docs/agent/cli-anything
pip install -e .
```

Requires:
- Python 3.9+
- Blender 4.0+ installed (auto-detected on macOS/Linux/Windows, or set `BLENDER_PATH`)
- `click` (installed automatically)

## Quick Start

```bash
# Show Blender version
cli-anything-blender version

# Open and inspect a blend file
cli-anything-blender info scene.blend

# List objects
cli-anything-blender -f scene.blend obj list

# Render an image
cli-anything-blender -f scene.blend render image /tmp/render.png

# Convert between formats
cli-anything-blender io convert model.fbx model.glb

# JSON output for agent consumption
cli-anything-blender --json -f scene.blend obj list
```

## Command Reference

### Project
- `cli-anything-blender open <path>` — Open blend file and show summary
- `cli-anything-blender info <path>` — Detailed blend file info
- `cli-anything-blender new [-o path]` — Create empty blend file
- `cli-anything-blender save-as <path>` — Save as new file
- `cli-anything-blender version` — Show Blender version

### Objects (`obj`)
- `obj list [--type MESH]` — List objects
- `obj add <type> [--name N] [-l X Y Z]` — Add primitive (cube, sphere, cylinder, plane, cone, monkey, camera, light_point, light_sun, etc.)
- `obj delete <name>` — Delete object
- `obj transform <name> [-l X Y Z] [-r X Y Z] [-s X Y Z]` — Transform
- `obj duplicate <name> [--new-name N] [--offset X Y Z]` — Duplicate

### Render (`render`)
- `render image <output> [-e ENGINE] [-r W H] [-s SAMPLES] [-F FORMAT] [--camera NAME] [--transparent]` — Render frame
- `render animation <dir> [--start N] [--end N] [-e ENGINE]` — Render animation
- `render engines` — List render engines

### Materials (`mat`)
- `mat list` — List materials
- `mat create <name> [--color R G B] [--metallic V] [--roughness V]` — Create material
- `mat assign <object> <material>` — Assign material to object

### Import/Export (`io`)
- `io import <path> [-o blend]` — Import file (fbx, gltf, glb, obj, stl, ply, svg, bvh)
- `io export <path> [-F format]` — Export to format
- `io convert <input> <output>` — Convert between formats
- `io formats` — List supported formats

### Animation (`anim`)
- `anim timeline` — Show timeline info
- `anim keyframes <object>` — List keyframes
- `anim set-range <start> <end>` — Set frame range
- `anim insert-keyframe <object> <frame> [-p property]` — Insert keyframe

### Modifiers (`mod`)
- `mod list <object>` — List modifiers
- `mod add <object> <type> [-n name]` — Add modifier (SUBSURF, MIRROR, ARRAY, etc.)
- `mod remove <object> <modifier>` — Remove modifier
- `mod apply <object> <modifier>` — Apply modifier

### Scene (`scene`)
- `scene list` — List scenes
- `scene info [-n name]` — Scene details
- `scene settings [-e ENGINE] [-r W H] [--fps N] [-F FORMAT]` — Update settings

### Raw Python (`exec`)
- `exec "<code>"` — Execute Python in Blender (code must set `_data`)

## REPL Mode

```bash
cli-anything-blender --repl -f scene.blend
```

Interactive shell with all commands available.

# cli-anything-asciiid

CLI harness for the **Asciicker asciiid** map editor. Controls the editor
via its MCP stdin protocol — place meshes, sprites, sculpt terrain, manage
camera/weather, load/save .a3d worlds — all from the command line or an
interactive REPL.

## Prerequisites

1. **asciiid binary** — Build from the Asciicker engine source:
   ```bash
   cd /path/to/asciicker-Y9-2
   make -f makefile_asciiid
   # Output: .run/asciiid
   ```

2. **Display** — asciiid creates an OpenGL window. On Linux, use Xvfb for
   headless operation:
   ```bash
   Xvfb :99 -screen 0 1024x768x24 &
   export DISPLAY=:99
   ```

3. **Python 3.10+** with `click` and `prompt-toolkit`:
   ```bash
   pip install click prompt-toolkit
   ```

## Installation

```bash
cd /path/to/asciicker-Y9-2/docs/agent/cli-anything
pip install -e .
```

Verify:
```bash
which cli-anything-asciiid
cli-anything-asciiid --help
```

## Usage

### Interactive REPL (default)

```bash
cli-anything-asciiid
# or with explicit project root:
cli-anything-asciiid --project-root /path/to/asciicker-Y9-2
```

### One-shot Commands

```bash
# Start the editor process
cli-anything-asciiid editor start

# Load a world
cli-anything-asciiid project load a3d/game_map_y8.a3d

# Place a sprite
cli-anything-asciiid place sprite player-0100.xp 100 200 0 --yaw 45

# Get camera state (JSON output)
cli-anything-asciiid --json camera get

# Set weather to blizzard
cli-anything-asciiid weather set blizzard

# Send raw MCP command
cli-anything-asciiid raw "LIST_INSTANCES"

# Stop the editor
cli-anything-asciiid editor stop
```

### JSON Mode

All commands support `--json` for machine-readable output:

```bash
cli-anything-asciiid --json project info
```

## Command Reference

| Group | Command | Description |
|-------|---------|-------------|
| editor | start | Launch asciiid --mcp |
| editor | stop | Stop the process |
| editor | status | Check if running |
| project | load [PATH] | Load .a3d world |
| project | save PATH | Save .a3d world |
| project | info | Instance count, camera, weather |
| terrain | set-height H | Set terrain height (0-65535) |
| terrain | probe X Y | Query height at position |
| terrain | grid ALPHA | Set grid visibility (0.0-1.0) |
| place | mesh NAME X Y Z | Place mesh instance |
| place | sprite NAME X Y Z | Place sprite instance |
| place | sprite-active X Y Z | Place active sprite |
| place | sprite-active-rel DX DY DZ | Place relative to camera |
| place | set-active NAME | Set active sprite |
| place | load-sprite PATH | Load sprite file |
| camera | get | Get camera state |
| camera | set X Y Z YAW | Set camera position |
| camera | focus-origin | Jump to origin |
| weather | get | Get weather state |
| weather | set STATE | Set weather (0-3 or name) |
| world | list-instances | List all instances |
| world | render | Render view to base64 ASCII |
| world | dump-matrix | Dump material tables |
| world | debug-axis | Place axis cubes |
| minimap | view [--cx X --cy Y --no-meshes --min-mesh-size N] | Render colored minimap to terminal |
| minimap | add-marker NAME X Y | Add a named marker at world coords |
| minimap | remove-marker NAME | Remove a marker by name |
| minimap | list-markers | List all markers |
| — | echo TEXT | Connectivity test |
| — | raw COMMAND | Send raw MCP command |

## Environment Variables

- `ASCIIID_PROJECT_ROOT` — Override project root auto-detection
- `CLI_ANYTHING_NO_COLOR` — Disable color output
- `CLI_ANYTHING_FORCE_INSTALLED` — Force subprocess tests to use installed command

## Architecture

The CLI wraps the asciiid binary as a subprocess. It does NOT reimplement
any editor logic — all operations are MCP commands sent to the real binary.

```
cli-anything-asciiid  →  asciiid --mcp (subprocess)
     ↕ stdin/stdout          ↕ OpenGL window
  MCP commands           Real editor engine
```

# Test Plan — cli-anything-asciiid

## Test Inventory Plan

- `test_core.py`: ~30 unit tests (synthetic data, no asciiid process)
- `test_full_e2e.py`: ~15 E2E tests (real asciiid process, real .a3d files)

## Unit Test Plan (test_core.py)

### Backend Module (`asciiid_backend.py`)
- `find_asciiid()` — locates binary at project_root/.run/asciiid
- `find_asciiid()` — raises AsciiidNotFound when binary missing
- `AsciiidProcess` — constructor initializes state correctly
- `AsciiidProcess.running` — False before start
- `AsciiidProcess.pid` — None before start

### Session Module (`session.py`)
- `save_session()` — creates session JSON file
- `load_session()` — reads back saved session data
- `update_session()` — modifies specific fields
- `clear_session()` — removes session file
- `load_session()` — returns None when no file exists
- `load_session()` — returns None when PID is dead (stale session cleanup)

### Weather Module (`weather.py`)
- `WEATHER_STATES` — maps all 4 names to ints
- `WEATHER_NAMES` — reverse mapping
- Weather state validation (string and int input)

### Camera Module (`camera.py`)
- Response parsing (key=value format)

### Placement Module (`placement.py`)
- Argument construction for each placement type

### World Module (`world.py`)
- Instance list parsing

### CLI Module (`asciiid_cli.py`)
- `_find_project_root()` — auto-detection logic
- `_output()` — JSON vs human-readable formatting
- Click command registration (all groups and commands exist)

### Edge Cases
- Invalid weather state string → ValueError
- Weather state out of range (negative, >3)
- Session file corruption (invalid JSON)
- Empty response parsing

## E2E Test Plan (test_full_e2e.py)

### Prerequisites
- asciiid binary built at `.run/asciiid`
- Display available (macOS native or Xvfb on Linux)
- `ASCIIID_RUN_REAL_E2E=1` set explicitly before running the real editor suite
- Tests fail (not skip) if prerequisites missing after the explicit opt-in is set

### Workflows

**Workflow 1: Start/Echo/Stop**
- Start editor → echo test → stop editor
- Verifies: process lifecycle, MCP protocol, cleanup

**Workflow 2: Load/Save Round-Trip**
- Start → load default map → save to temp → verify file exists and size > 0
- Verifies: file I/O, .a3d serialization

**Workflow 3: Camera Control**
- Start → set camera → get camera → verify position matches
- Verifies: camera state management, response parsing

**Workflow 4: Weather Cycle**
- Start → set each weather state (0-3) → get → verify matches
- Verifies: weather system, state transitions

**Workflow 5: Sprite Placement**
- Start → load map → place sprite → list instances → verify count increased
- Verifies: placement, instance tracking

**Workflow 6: Mesh Placement**
- Start → place mesh → list instances → verify present
- Verifies: mesh loading, placement, world rebuild

**Workflow 7: Terrain Operations**
- Start → set terrain height → probe → verify height matches
- Verifies: terrain modification, height query

**Workflow 8: Render**
- Start → load map → render → verify base64 data non-empty
- Verifies: render pipeline, base64 encoding, response parsing

**Workflow 9: Full Map Editing Session**
- Start → load map → place 3 sprites → set weather → move camera → save → stop
- Verifies: complete multi-step workflow

### CLI Subprocess Tests

**TestCLISubprocess** class using `_resolve_cli("cli-anything-asciiid")`:
- `test_help` — `--help` exits 0
- `test_json_flag` — `--json editor status` produces valid JSON
- `test_full_workflow` — start → echo → stop via subprocess

### Safe Default

- `test_full_e2e.py` is opt-in and skipped unless `ASCIIID_RUN_REAL_E2E=1`
- This prevents routine repo-root pytest runs from launching the real `asciiid` editor unexpectedly

---

## Test Results

_(To be appended after running tests)_

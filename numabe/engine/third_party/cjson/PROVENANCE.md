# cJSON — vendored copy provenance

**Source:** Dave Gamble's cJSON, https://github.com/DaveGamble/cJSON  
**Version:** 1.7.18 (the stable release at time of vendoring, 2026-05-26)  
**License:** MIT (see LICENSE file in this directory)  
**FL:** FL-4131 — gate `engine_json_library_vendored`  
**Why vendored:** The glyph sidecar parser (engine/glyph_sidecar.cpp) needs a
minimal, single-file C JSON parser with no external dependencies, compatible
with the existing desktop (clang/gcc) and Emscripten (WASM) build targets.
cJSON meets these requirements: single header + single source, MIT license,
zero dependencies, and stable C89/C90 API.

## Files vendored
- `cJSON.h` — public API header
- `cJSON.c` — implementation
- `LICENSE` — MIT license text
- `PROVENANCE.md` — this file

## What was NOT vendored
- CMakeLists.txt, Makefile, tests/, fuzzing/ — build system and tests
  from the upstream repo are not needed; we use the source files directly.

## Update policy
Do not auto-update. Any update must be reviewed against the glyph_sidecar.cpp
usage surface and re-verified against the parity corpus
(assets/glyphs/fixtures/sidecar_parity_corpus.json).

## Build integration
See makefile_game_mac and build-web.sh for source list wiring.
The source file is compiled as part of the game binary and the WASM target.
No preprocessor flags are required for the default cJSON configuration.

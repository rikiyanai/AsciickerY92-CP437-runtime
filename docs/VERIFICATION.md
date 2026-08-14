# Verification record

Date: 2026-08-12 (Asia/Tokyo)

## Product acceptance conditions

- Full Block Feature-era native/server/web game ownership is present, rather
  than a loader or corpus browser.
- The normalized-XP cell contract and bundle-based actor appearance owners are
  connected to the actual runtime.
- A real browser client can join the real authoritative server and visibly run
  the armored composite beside Block Feature world content.
- Native server, native terminal, and browser/WebAssembly targets build from
  this standalone checkout.
- Automated contract checks pass without credentials or parent-repository
  paths.

## Direct evidence

- Python contract suite: 26 collected pytest cases and five standalone script
  checks passed.
- JavaScript web/network suite: 3 passed.
- Currentness/isolation gates: 4 passed (glyph manifests, 192-row actor-visual
  coverage, server reachability, and web diagnostic isolation).
- Native server: linked from makefile_server; its help path executed.
- Native terminal client: linked from makefile_game_term_mac and entered the
  runtime, loaded six audio samples, and admitted the normalized glyph fixture.
- Browser build: Emscripten 4.0.21 produced a 38,131,721-byte WASM binary and a
  24,176,750-byte preload data bundle.
- Live join: the server accepted the browser's 488-byte JOIN_V2 request and
  created one authoritative player.
- Live appearance: ordinary input equipped definitions 410, 411, and 409 in
  server-owned slots 301, 306, and 303.

The layer-detail recording is
`docs/recordings/cp437-runtime-layer-transitions.gif`: 956 by 386 pixels, 52
decoded frames at 150 ms per frame, SHA-256
`9ae5bc26bf1539619ee32fdfa6ab9bdf434eed6fc17a0f21991e333229cdb970`.
Its exact per-frame source, decoded hashes, semantic states, authoritative
positions, slots, definitions, and angle selections are pinned in the adjacent
JSON receipt and checked by `tests/test_recording_contract.py`.

The preserved world-scale recording is
`docs/recordings/armored-block-feature-gameplay.gif`: 520 by 520 pixels, 60
frames at 10 frames per second, SHA-256
`f0c203dd8979e327236014f3a232a8b947368a0c1ddee65ef95a4acda59e852a`.
It proves runtime-world movement, while the player-detail recording carries the
equipment-layer claim.

## Completion stage

The integrated product is Implemented, Connected, Executed, and Verified. The
user also inspected the live local Chrome runtime and reported that it seemed
fine. Personal acceptance of the published README GIF remains a distinct manual
judgment; the repository does not infer it from the automated checks.

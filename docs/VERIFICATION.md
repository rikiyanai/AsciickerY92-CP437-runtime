# Verification record

## Wallace/Gromit upright-rocket correction (2026-08-15)

The current correction is Verified against the staged dirty source. It is not
commit-bound closure and does not infer operator acceptance.

- `python3 -m pytest tests/test_wallace_sand_scene.py -q`: 3 passed. The map
  contract checks continuous varied hills, original yellow material ownership,
  upright rocket transform/seating, exact assets, and standalone Gromit state.
- The exact GLB conversion command ran twice and reproduced AKM SHA-256
  `ceb99d7aa06a00a9555f7bef8f6158e1c8857059215571a270ea376e2d714d7f`
  with 1,128 vertices, 1,012 faces, and separate orange-body/gray-glass colors.
- `./build-web.sh` passed glyph admission, 196-row actor coverage, diagnostic
  isolation, Emscripten compilation, and web staging.
- The real headed browser **PLAY** control opened the current map. The saved
  viewport shows the rocket fully in frame, large, upright, and volumetric with
  a rounded body, fins/landing structure, depth shading, and gray glass on the
  operator-approved yellow sand.
- Same-run authority at tick `1370` reported Wallace profile `201`/skin `102`
  and Gromit profile `202`/skin `103`; both had mount `0`, while Gromit retained
  companion disposition `1` and owner player `0`.

See `docs/recordings/wallace-gromit-upright-rocket.*` for the headed image and
hash-bound dirty-source receipt.

## Wallace/Gromit sand scene (2026-08-14)

The commands and viewport below describe the earlier `9508af7` attempt. The
capture's ignored web slot manifest named an older dirty revision, so this is
Executed historical evidence, not commit-bound verification of the current
correction batch.

- `python3 -m pytest -q tests/test_wallace_sand_scene.py`: 3 passed.
- `make -j4 -f makefile_server`: native server linked successfully.
- `./build-web.sh`: glyph manifest, 192-row appearance coverage, diagnostic
  isolation, Emscripten compile, and web staging passed.
- Headed browser join: map start resolved to `(-2.8,-73.6,128)`, Wallace and
  Gromit rendered on ochre-yellow terrain with the rocket nearby, and no legacy
  block seeds or hostile generators were present.
- Real canvas movement displaced Wallace by more than eight planar units;
  authoritative Gromit followed and settled `6.883` units away inside the
  seven-unit stop band. See the image and JSON receipt under
  `docs/recordings/wallace-gromit-sand-scene.*`.
- Review corrections now give Wallace and Gromit explicit standalone catalog
  profiles, restore the historical normalized aliases, remove Gromit's mount
  state, and give follow hysteresis one persistent state owner. These changes
  are Implemented but intentionally not Executed while the operator run hold
  remains active.

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

- Python contract suite: 29 collected pytest cases and five standalone script
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

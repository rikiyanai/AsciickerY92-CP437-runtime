# Asciicker Y9-2 Forked snapshot (blocks + bundle refactor latest state) 

This standalone repository is the full pre-FL-4512 C++ Asciicker
runtime from the Block Feature candidate lineage, with the final normalized-XP
layer contract and bundle-based actor appearance cutover integrated into that
runtime. It is a game repository: native terminal client, authoritative server,
browser/WebAssembly client, maps, meshes, audio, sprites, and the normalized
appearance compiler/runtime owners are all included.

The principal display path is the historical CP437 byte-cell contract: each
screen cell carries foreground, background, and one byte-sized glyph identity.
The integrated normalized-XP refactor also admits non-CP437 glyphs through the
versioned extended-glyph sidecar, compiled manifest, and hash-bound atlas path.
The repository name describes the runtime lineage; it does **not** mean that
the integrated runtime is CP437-only.

## Real runtime evidence

![Wallace beside the large upright rocket on the sand map](docs/recordings/wallace-gromit-upright-rocket.png)

The canonical scene is now a varied yellow sand map with broad dunes and many
smaller hills. Wallace spawns as the playable actor, the large upright rocket
sits nearby, and authoritative Gromit follows the player while remaining
outside hostile and damage-target paths.
The current [upright-rocket scene receipt](docs/recordings/wallace-gromit-upright-rocket.receipt.json)
binds the headed viewport to the rebuilt web artifacts and same-run authority.
It is dirty-source verification; operator acceptance and commit-bound proof
remain separate stages.

![Same-session base, armor, helmet, and sword transitions in the browser runtime](docs/recordings/cp437-runtime-layer-transitions.gif)

This recording comes from one real browser session connected to the real
authoritative server. Every frame keeps the uncropped Block Feature world at
left and shows a nearest-neighbor enlargement of the same frame's player at
right. The labeled phases expose the unequipped base, armor addition, helmet
addition, visibly held sword, and movement/turning. The inset is a crop of the
real rendered frame, not a sprite reconstruction.

![Wide world movement beside Block Feature blocks](docs/recordings/armored-block-feature-gameplay.gif)

The second, preserved six-second recording is world-scale proof: the real
browser build moves and turns beside placeable blocks while connected to the
authoritative server. Its player is too small to prove individual equipment
layers, so it is not used for that claim.

The separate [capture receipt](docs/recordings/cp437-runtime-layer-transitions.receipt.json)
records the same-session probe checkpoints. The server reported definitions
411, 410, and 409 in armor, head, and weapon slots; that authoritative slot
state corroborates the visible transitions but is not claimed as text visible
inside either GIF. No synthetic TUI or command-entry sequence is used as
product proof.

## Run the browser game

Requirements: Python 3.11+, a C++20 compiler, Emscripten 4.0.21, and the native
audio development library for the server (libpulse-dev on Linux; Apple audio
frameworks are supplied by macOS). The macOS terminal client additionally
requires Homebrew V8 (`brew install v8`).

In one terminal:

~~~sh
make -j4 -f makefile_server
./.run/server
~~~

In another:

~~~sh
./build-web.sh
python3 -m http.server 8765 --directory .web
~~~

Open http://127.0.0.1:8765/, enter a name and
ws://127.0.0.1:8080, then select **Play**.

## Run the terminal client

~~~sh
make -j4 -f makefile_game_term_mac   # macOS
./.run/game_term
~~~

The verified standalone terminal path is macOS. The historical Linux makefile
requires a separately provisioned V8 monolith at
`vendor/v8/v8/out.gn/x64.release/obj/libv8_monolith.a`; it is not a
clean-checkout Linux target. The authoritative server and browser targets are
verified on Linux in CI.

## Verify the integrated contracts

~~~sh
python3 -m pip install -r requirements-test.txt
./scripts/run_standalone_checks.sh
~~~

That command runs 29 collected pytest cases, five standalone Python contract
checks, three JavaScript tests, and four currentness or isolation gates. The
native server and terminal client are separate build acceptance surfaces; the
browser build additionally runs the glyph-manifest and actor-visual coverage
gates before compiling.

See [attribution and exact source identities](docs/ATTRIBUTION.md), the
[player guide](docs/player-guide.md), and the chronological
[failure log](docs/FAILURE_LOG.md).

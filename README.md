# Asciicker Y9-2 Runtime

A fork of [msokalski/asciicker](https://github.com/msokalski/asciicker), a CP437 3D ASCII engine.

This repository contains the full playable C++ runtime used by the Y9-2 branch: an authoritative server, native terminal client, browser/WebAssembly client, maps, meshes, audio, sprites, and the actor-appearance tooling used by the runtime. At the time of this release, I am in the middle of a complete refactor of the 3D glyph rendering engine. 

The renderer keeps the original CP437 byte-cell path for glyph IDs `0..255`. Y9-2 also supports manifest-declared extended glyph IDs through an extended-glyph sidecar, so the current runtime is not limited to CP437 even though that remains its compatibility fast path.

## New Sprites and animations: Wallace and Gromit and more. 

![Wallace beside the upright rocket on the sand map](docs/recordings/wallace-gromit-upright-rocket.png)
![IMG 3909](docs/recordings/IMG_3909.GIF)

![IMG 3910](docs/recordings/IMG_3910.GIF)

![IMG 3911](docs/recordings/IMG_3911.GIF)

![Witch Rapture v2](docs/recordings/witch-rapture-v2.GIF)

## Actor appearance and additional feats

![Base, armor, helmet, sword, and movement transitions in the browser runtime](docs/recordings/armored-block-feature-gameplay.gif)

Many features added and refactored from Gumix's latest y9-2 release: 
Bundle Refactor: Actor appearance is compiled from normalized REXPaint `.xp` source layers into runtime profiles. The server selects reachable appearance state, while the client resolves the corresponding layered presentation. The bundled data includes base sprites as well as equipment layers such as armor, helmets, and weapons.

New/improved/unique runtime features: weather, minimaps, projectile/crossbows, non euclidian geometry mode, placable blocks, gameplay improvements for cross platform play, 
Multiplayer improvements and extensive dev tool support,
Extensive additions in Asset and map tooling ecosystem for example, an OSM -> a3d pipeline, pico cad interop, headless cli, blender add on suite overhaul with direct to a3d, akm authoring, sprite rig authoring, asciiid editor improvements, and more.
Architecture and maintainability: decoupled game engine components into their own modules (no more game.cpp monolith god file!!) 


## Runtime layout

The repository has three main executable surfaces:

- **Authoritative server** — owns game-state simulation and multiplayer state.
- **Native terminal client** — runs the game in a terminal using the native renderer.
- **Browser client** — compiles the client to WebAssembly and connects to the same server over WebSocket.

The repository also includes the map and asset formats, actor-appearance compiler inputs and generated tables, glyph manifests and atlases, and standalone contract tests used by those runtime paths.

## Build

The top-level `Makefile` wraps the supported build commands:

```sh
make server
make terminal
make web
make test
```

The browser build uses Emscripten 4.0.21. The Linux server build needs PulseAudio development headers. The macOS terminal build uses Homebrew V8.

On Linux without the vendored V8 monolith, the terminal client can link the V8 bundled with Node.js instead: `apt-get install libnode-dev`, then `make terminal V8_FROM=system` (or `V8_FROM=auto`). The client needs a PulseAudio server at startup. `scripts/terminal_capture/capture.py` records a headless tmux session to a GIF.

## Run

Start the server:

```sh
make server
make run-server
```

For the browser client, build and serve it in another terminal:

```sh
make web
make serve-web
```

Then open `http://127.0.0.1:8765/` and connect to `ws://127.0.0.1:8080`.

For the native terminal client:

```sh
make terminal
make run-terminal
```

The repository's GitHub Actions workflow is configured to run the standalone checks, build the server on Linux, build the terminal client on macOS, and build the WebAssembly client on Linux.

## Source and licensing

See [docs/ATTRIBUTION.md](docs/ATTRIBUTION.md) for upstream and Y9-2 source identities, asset provenance, and licensing information. The original Asciicker source license is preserved under `docs/licenses/`.

Historical development records are kept separately in [docs/FAILURE_LOG.md](docs/FAILURE_LOG.md).

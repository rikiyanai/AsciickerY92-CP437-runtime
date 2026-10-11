# Attribution and provenance

## Upstream

This project is derived from [msokalski/asciicker](https://github.com/msokalski/asciicker). No authorship is claimed for upstream Asciicker source or assets. The original source license is preserved at `docs/licenses/asciicker-source-LICENSE`.

## Y9-2 runtime sources

The standalone repository combines the full C++ game runtime with later Y9-2 actor-appearance work from `rikiyanai/asciicker-Y9-2`.

The full game/server/browser runtime base is traced to commit `e7cca2c840e8344da16e8df62cfb214f5a1a4b4e` from June 1, 2026. That source includes the native game, authoritative server, browser build, gameplay assets, and the server-owned placeable-block work present in that branch.

The normalized REXPaint cell contract and actor-appearance runtime were integrated from the same Y9-2 lineage. The runtime cutover is traced to `c4b1b5a510e30a05d0f81cdc73deae25d9ffa4c3`, with the associated compiler state traced to `78eab2d18c92d359e65674e0405a06be44403283`.

The repository contains the selected normalized source-layer data, compiler inputs, generated actor-appearance tables, glyph manifests and atlases, runtime bindings, and tests needed by that integration. These source identities describe the imported lineage; they do not imply that the current repository remains byte-identical to those historical commits.

## Wallace, Gromit, and rocket assets

The dedicated Wallace and Gromit sprite sheets came from the Y9-2 pipeline source state at commit `f9ca59759fd46828e6cc320428ec7e6132dd4648`:

- `assets/sprites/2026-08-12-030327-wallace.xp` — SHA-256 `0e2bd7823d3aab79007df8a1c6c58150b5bb3c7718a75ce0ae9df27e88adbc3a`
- `assets/sprites/2026-08-12-030327-gromit.xp` — SHA-256 `e2e2a4212fb57c70ffc615c4c8539e336f7a9bc08b88e6c3648f37ec2a9b5bb5`

The original `player-0000.xp` and `wolfie-0000.xp` assets remain separate source identities rather than aliases for Wallace or Gromit.

The rocket source is `assets/meshes/source/toy_rocket.glb`, SHA-256 `6990ca861b55d4afd5073aa1cc09b018eb6617324a618c52215ca40ded271263`. Its converted runtime mesh is `assets/meshes/toy_rocket_ship.akm`, SHA-256 `ceb99d7aa06a00a9555f7bef8f6158e1c8857059215571a270ea376e2d714d7f`. Conversion provenance is recorded in `assets/meshes/toy_rocket_ship.provenance.json`.

## Other third-party material

Third-party code, fonts, and assets retain their original notices. Repository-level source lists, license copies, and additional provenance records are under `docs/licenses/` and `docs/upstream/`.

# Attribution and provenance

## Standalone product identity

This repository combines two requirements without importing the later FL-4512
rendering-refactor repository wholesale:

1. The complete Block Feature candidate runtime from private source repository
   rikiyanai/asciicker-Y9-2 at
   e7cca2c840e8344da16e8df62cfb214f5a1a4b4e (2026-06-01). That identity
   contains the native game, server, browser build, gameplay assets, and the
   server-owned placeable-block lineage introduced by
   2be07b19c59aba2a0a9cffdb312ca2d5bdf56e3f.
2. The final normalized-XP cell-contract and actor-appearance bundle owners from
   the same private source lineage. The runtime cutover is identified by
   c4b1b5a510e30a05d0f81cdc73deae25d9ffa4c3 and its compiler-cutover
   receipt by 78eab2d18c92d359e65674e0405a06be44403283.

The integration deliberately selects the normalized contract, compiler,
generated tables, assets, web binding, and tests required by item 2 on top of
item 1. It does not claim that the July source tree as a whole is the historical
candidate. A later audit identity,
c890933505b3e418746177fcd1755d1833e81f12, was used only to trace the final
ownership and verify that the selected bundle artifacts were current.

A read-only recovery checkpoint labelled
`2026-06-01T0408+0000__e7cca2c840__candidate-asciicker-cpp-runtime__dirty`
was corroborating evidence for the candidate deployment. Because it is a dirty,
non-quiesced copy without Git metadata, it was not treated as the clean source
identity.

## Normalized appearance contract

The checked-in semantic maps under
docs/research/ascii/semantic_maps/upstream_xp_cell_contract/, the generated
actor-visual table, reachability data, compiled glyph manifests, atlas pages,
and normalized XP bundles form one runtime contract. The compiler and tests
bind those outputs to the frozen upstream cell decisions rather than to an
independent parser snapshot.

The accepted gameplay recording uses the armored player composite (helmet,
armor, and sword) specifically because it exposes more of that integrated layer
stack than a nude or single-layer sprite.

## Wallace, Gromit, and rocket scene

The playable Wallace and companion Gromit sheets are the exact approved assets
from `asciicker-pipeline-v3` commit
`f9ca59759fd46828e6cc320428ec7e6132dd4648`:

- `sprites/2026-08-12-030327-wallace.xp`, SHA-256
  `0e2bd7823d3aab79007df8a1c6c58150b5bb3c7718a75ce0ae9df27e88adbc3a`;
- `sprites/2026-08-12-030327-gromit.xp`, SHA-256
  `e2e2a4212fb57c70ffc615c4c8539e336f7a9bc08b88e6c3648f37ec2a9b5bb5`.

The timestamped files are the runtime source identities for dedicated Wallace
and Gromit catalog profiles. The historical normalized `player-0000.xp` and
`wolfie-0000.xp` assets retain their original bytes and contracts; neither is
an alias for these characters. The untracked June duplicate names and distinct
repaired `adhoc/` variants were not imported.

The rocket source is `assets/meshes/source/toy_rocket.glb`, SHA-256
`6990ca861b55d4afd5073aa1cc09b018eb6617324a618c52215ca40ded271263`.
Its runtime conversion is `assets/meshes/toy_rocket_ship.akm`, SHA-256
`ceb99d7aa06a00a9555f7bef8f6158e1c8857059215571a270ea376e2d714d7f`.
The corrected conversion preserves the GLB's separate orange body and gray
glass materials. The map transform rotates its long axis upright and applies
the runtime's 16:1 vertical-height compensation.
The checked-in conversion scripts preserve the GLB-to-AKM production path.
Exact source, converter, and output identities are bound by
`assets/meshes/toy_rocket_ship.provenance.json`.

## License

The historical source license is preserved at
docs/licenses/asciicker-source-LICENSE. Third-party code and assets retain their
original notices; repository-level copies of package licenses, source lists, and
provenance documents are centralized under docs/licenses/ and docs/upstream/.
No authorship is claimed for upstream Asciicker source or assets.

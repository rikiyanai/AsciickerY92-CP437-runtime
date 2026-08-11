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

## License

The historical source license is preserved at
docs/licenses/asciicker-source-LICENSE. Third-party code and assets retain their
original notices; repository-level copies of package licenses, source lists, and
provenance documents are centralized under docs/licenses/ and docs/upstream/.
No authorship is claimed for upstream Asciicker source or assets.

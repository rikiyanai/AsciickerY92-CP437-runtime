# Attribution and provenance

## Historical runtime owner

Private source repository: `rikiyanai/asciicker-Y9-2`.

- Selected base: `0bdb614c77e9b06ee47af7b4ff1d584ada4793a1`.
- First source-changing FL-4512 commit: `873374a1e30bda2c7854192a6466c4fc48e677d4`.
- Divergent deployment evidence: `e7cca2c840e8344da16e8df62cfb214f5a1a4b4e`.
- Historical/deployed merge base: `661ccd385cc1de36bdf9246c777e2ed20e118fd1`.

The exact historical `engine/sprite.cpp` and `engine/sprite.h` are retained
under `historical/engine/` for review. The standalone `src/xp_runtime.cpp`
transplants only their documented gzip-compressed REXPaint layer format into a
small, dependency-bounded C++20 adapter. It does not import current renderer
code.

## Normalized-XP snapshot

The snapshot is copied from the verified standalone source-layer contract
package extracted from pipeline-v3 commit
`7fdecabf44175d25d3793335dee4d38e8b089a81` and Y9-2 commit
`242ecba44f76ed1120dadf06653fd6de47017b7f`.

`data/normalized-xp/SNAPSHOT.json` records every bundled sprite and contract
artifact SHA-256. It contains 115 XP paths. The snapshot is immutable input;
the runtime exposes no writer.

The upstream ledger README is preserved as
`docs/historical-upstream-contract-ledger.md`, outside the runtime data tree,
with an explicit warning that its parent-repository regeneration commands are
historical rather than standalone commands.

The source repository's license text at the selected base is preserved at
`docs/licenses/asciicker-source-LICENSE`.

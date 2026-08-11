# Failure Log

## P0C-06 / FL-4512 · 2026-08-11 — private repository created

- The historical base, first FL-4512 source commit, divergent deployment, and
  merge-base identities were recorded.
- No source was initially copied because a clean minimal transplant had not
  been proved.

## P0C-06 / FL-4512 · 2026-08-12 — minimal historical XP adapter implemented

- Selected the historical gzip/REXPaint loader contract rather than copying the
  whole game or the interleaved FL-4512 range.
- Added a C++20 terminal runtime that parses and browses the frozen 115-file
  normalized-XP corpus without mutation authority.
- Added clean-build, full-corpus, snapshot-hash, and no-write verification plus
  a real terminal recording.
- The adapter is runnable and verified. It is not a bootable/full-game claim,
  and user acceptance remains separate.

## P0C-06 / FL-4512 · 2026-08-12 — acceptance re-audit rejected the adapter substitution

- Intended product: a standalone runnable extraction of the selected historical
  Asciicker C++ runtime, with the later normalized-XP contract carried forward
  only as a selected subsystem.
- Observed result: `build.sh` compiles only `src/xp_runtime.cpp`. The exact
  historical `engine/sprite.cpp` and `engine/sprite.h` are preserved but never
  compiled or connected; the game loop, renderer, input, world, gameplay, and
  other runtime owners are absent.
- The four tests and sanitizer run prove only the replacement parser, snapshot
  hashes, and no-write behavior. The deleted GIF likewise showed that proxy.
- Highest supported stage: **Implemented and Executed XP-loader proxy only**.
  The historical runtime extraction is not Implemented, Verified, or Accepted.

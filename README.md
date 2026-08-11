# Historical Asciicker Runtime

Private standalone repository for P0C-06: the historical C++ runtime immediately
before the FL-4512 rendering-pipeline refactor, plus a selected frozen
normalized-XP contract snapshot.

## Selected identities

- Base candidate: `0bdb614c77e9b06ee47af7b4ff1d584ada4793a1`.
- First source-changing FL-4512 runtime commit:
  `873374a1e30bda2c7854192a6466c4fc48e677d4`.
- Divergent deployed checkpoint: `e7cca2c840e8344da16e8df62cfb214f5a1a4b4e`.
- Merge base of the historical candidate and divergent checkpoint:
  `661ccd385cc1de36bdf9246c777e2ed20e118fd1`.

## Current state

Repository ownership is established, but no runtime source is copied yet. The
base candidate still requires a clean historical build and a minimal adapter
for the later normalized-XP contract. The 267-commit interleaved range must not
be blindly cherry-picked.

The eventual product excludes the current FL-4512 renderer, research-paper
pipeline, agent/process infrastructure, and unrelated current-repository code.
This private scaffold is not a runnable-runtime claim.

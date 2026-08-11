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

- Intended product: the full runnable Asciicker repository at the Block Feature
  / `candidate-asciicker.rikiworld.com` state, with the normalized-XP layer
  contract and appearance-bundle refactor integrated into that full runtime.
- Observed result: `build.sh` compiles only `src/xp_runtime.cpp`. The exact
  historical `engine/sprite.cpp` and `engine/sprite.h` are preserved but never
  compiled or connected; the game loop, renderer, input, world, gameplay, and
  other runtime owners are absent.
- The four tests and sanitizer run prove only the replacement parser, snapshot
  hashes, and no-write behavior. The deleted GIF likewise showed that proxy.
- Highest supported stage: **Implemented and Executed XP-loader proxy only**.
  The historical runtime extraction is not Implemented, Verified, or Accepted.
- The rejected `.tape` recipe was deleted because it could only recreate the
  adapter proxy recording; no recapture is valid before the runtime exists.

## P0C-06 · 2026-08-12 — original full-product requirement restored to the record

- This is not a new requirement. The intended standalone product was always the
  full Block Feature candidate runtime plus the integrated normalized-XP
  layer-contract/bundle refactor.
- The audit's `0bdb614...` pre-FL-4512 base and "minimal adapter/transplant"
  framing was an unauthorized narrowing. It is retained above only as failed
  attempt history and is revoked as the product boundary.
- Direct recovery evidence exists for the full deployment: the read-only
  candidate checkpoint identifies source `e7cca2c840e8344da16e8df62cfb214f5a1a4b4e`,
  10,282 regular files, 15 symlinks, a runnable Linux server, a WebAssembly
  browser bundle, and the placeable-block lineage. The captured tree is dirty
  and has no `.git`, so it is recovery evidence rather than a clean source base.
- The later bundle-refactor audit identifies committed source
  `c890933505b3e418746177fcd1755d1833e81f12` with a complete static
  normalized-XP contract, compiler/runtime ownership cutover, 192 reachable
  bindings, and 115 XP / 573 layers. Its exact-HEAD headed runtime gate failed,
  so integration and visual acceptance remain open.
- Highest supported stage is unchanged: this repository is an Executed proxy.
  The always-required full standalone product is not Implemented, Connected,
  Executed, Verified, or Accepted here.

## P0C-06 · 2026-08-12 — direct cutover-patch replay failed before mutation

- Product question answered before extraction: this repository must provide the
  complete native/server/web Block Feature runtime from `e7cca2c840...`, with
  the later frozen normalized-XP contract and bundle-based actor-appearance
  ownership integrated and visibly reachable in that runtime. It is not a
  loader, parser, corpus browser, or current FL-4512 renderer checkout.
- A temporary 287 MiB product tree was mechanically extracted from committed
  `e7cca2c840...` owners: engine, game/server/web/platform/network source,
  editor/build surfaces, runtime assets, third-party dependencies, and build
  scripts. The source checkout and this standalone repository were not changed.
- The first integration hypothesis attempted to replay the exact
  `c4b1b5a5` runtime-cutover delta onto that tree. `git apply` rejected the
  patch atomically: the divergent later lineage expected intermediate
  `layer_roles.json`, compiler helpers/tests absent at `e7cca2c`, and different
  generated-table/compiler contexts.
- This falsifies a linear cherry-pick or raw patch replay. No partial patch is
  accepted. The successor must identify the bundle/profile seam already present
  in the Block Feature runtime, then transplant only final source-owned contract
  artifacts and code that can compile against that historical product.

## P0C-06 · 2026-08-12 — unsupported compiler check flag was discarded

- The first baseline compiler probe invoked
  `scripts/compile_actor_visual_profiles.py --check`. The historical script does
  not define that flag and exited 2 with argparse usage; it did not establish a
  compiler result or change the temporary tree.
- The source-owned read-only check is
  `scripts/check_actor_visual_table_coverage.py`, which passed the unmodified
  Block Feature base at 192 rows. A later direct compiler run, if needed, must
  be treated as a generating action and verified by before/after hashes rather
  than relabelled as a check mode.

## P0C-06 · 2026-08-12 — clean Block Feature terminal target failed at link

- The unmodified `e7cca2c840...` product tree compiled the macOS terminal
  target's objects, then failed at link. `makefile_game_term_mac` omits source
  units that committed callers already require: glyph-average/manifest/plane
  and material-glyph/sidecar implementations. The linker reported unresolved
  symbols including `AverageGlyphForId`, `glyph_manifest_load_and_verify`,
  `glyph_plane_alloc`, and `material_glyph_plane_alloc`.
- This is a clean-source build failure, not a normalized-contract result. It
  prevents treating commit identity alone as a runnable candidate. The dirty
  deployed checkpoint contains distinct makefiles and existing build artifacts;
  the successor must compare those actual deployment owners and carry only a
  source-supported build correction.

## P0C-06 · 2026-08-12 — first terminal execution was not a recordable proof

- The source-supported link correction was isolated and proved in the
  temporary transplant: add the six glyph/material implementation units that
  are already present in the candidate tree, plus the later PURE_TERM no-op
  sidecar writer required by that non-GL target. The corrected target linked
  `.run/game_term` successfully.
- Executing that binary initialized V8 and CoreAudio, loaded the runtime
  palette/menu assets and extended-glyph fixture, entered the alternate screen,
  and rendered the actual game menu. This establishes a live full-runtime
  execution path for the candidate base; it does not yet establish the later
  normalized-XP integration.
- The first PTY probe inherited `TERM=dumb` and emitted unbounded repeated ANSI
  redraws (over 100 million output tokens reported by the command runner) until
  interrupted. That capture is operationally invalid and must never become a
  GIF. The successor must bound terminal geometry/frame capture and record the
  real native or browser acceptance surface only after normalized-XP cutover.

## P0C-06 · 2026-08-12 — divergent cutover replay required explicit reject handling

- Replaying the `c4b1b5a5` cutover with reject isolation applied the runtime
  structure/telemetry hunks cleanly but rejected three divergent generated or
  compiler contexts: `engine/actor_visual_profile_table.generated.h`,
  `scripts/compile_actor_visual_profiles.py`, and one optional glyph-admission
  test constant. No rejected hunk was treated as integrated.
- The generated table, compiler/checker, reachability dumper, profile bindings,
  server identity, upstream-contract test, and frozen 115-XP / 573-layer source
  contract were then overlaid as exact files from the final compiler receipt
  `78eab2d1`. The three `.rej` diagnostics were moved recoverably to
  `~/.Trash/codex-p0c06-transplant-rejects-20260812/` and are not product files.
- The remaining unknown is executable compatibility between those exact final
  artifacts and the successfully executed Block Feature runtime. Static
  coverage, compiler reproducibility, full target builds, and a second live
  runtime execution must answer it before this integration can be called
  Connected or Executed.

## P0C-06 · 2026-08-12 — first exact compiler replay found an omitted authority input

- Static coverage passed at 192 rows, the server reachability dump reported its
  artifact current, and the upstream-contract unit test passed.
- The first mutating compiler replay then failed closed before writing output:
  `docs/research/ascii/semantic_maps/family_topology_contracts.json` was absent
  from the initial extraction. That file is a hashed compiler authority input,
  not optional documentation, so omitting it made the transplant incomplete.
- Before/after SHA-256 values for the generated table, provenance, and compiler
  cutover receipt were identical; the failed attempt did not partially rewrite
  the contract. The successor must enumerate and extract every compiler input
  named by the final receipt/compiler, then rerun the same hash comparison.

## P0C-06 · 2026-08-12 — full compiler replay was not byte-reproducible

- After adding the two omitted hash-bound authority inputs, the full compiler
  completed successfully across the frozen 6,807,104-cell contract.
- It did **not** reproduce the receipt-owned artifact bytes. The generated table
  changed from `3dfef7e9...` to `87927ce6...`; the compiler-cutover receipt
  changed from `f6f19562...` to `89049254...`. Generated provenance remained
  byte-identical at `ab703849...`.
- Therefore a successful compiler exit is not yet proof of the final cutover in
  this transplant. The likely discriminators are a required final source input
  not yet pinned or an environment/path-dependent generated field. The
  successor must diff the regenerated table/receipt against exact `78eab2d1`,
  identify the changed fields, and either restore a missing owner or record a
  justified deterministic normalization. No build result may be called final
  while this discrepancy is unexplained.

## P0C-06 · 2026-08-12 — compiler discrepancy traced to the glyph identity package

- The generated-table diff contained exactly three changed constants: glyph
  manifest SHA-256, atlas LUT SHA-256, and atlas page-chain SHA-256. The
  candidate's older `material.additive.v1` glyph package was therefore the
  stale owner; actor rows and provenance were already identical.
- Overlaying the exact 31-file `material.additive.v1` atlas package and manifest
  from `78eab2d1`, then rerunning the full compiler, reproduced all three
  receipt-owned hashes exactly: table `3dfef7e9...`, provenance `ab703849...`,
  and cutover receipt `f6f19562...`.
- This closes the compiler-reproducibility failure. It does not by itself prove
  C++ compatibility or live visual behavior; affected native/server/web builds
  and a live runtime still remain required.

## P0C-06 · 2026-08-12 — first integrated web build failed during site staging

- With pinned Emscripten 4.0.21, the web build passed the glyph-manifest check,
  192-row actor-table coverage, diagnostic-isolation check, audio worklet build,
  and main wasm/data/js/html compilation.
- It then failed in the site-staging step because `docs/player-guide.md` was not
  included in the initial product extraction. The build intentionally requires
  that guide for the deployed browser site, so the wasm compile alone is not a
  successful web build.
- This is another extraction-boundary omission, not an Emscripten or normalized
  runtime compile failure. The successor must restore the exact candidate-owned
  player guide (and enumerate any other staging inputs) before rerunning the web
  build to a final zero exit.

## P0C-06 · 2026-08-12 — player-guide restoration hit a full filesystem

- Restoring the exact candidate-owned `docs/player-guide.md` immediately after
  the failed web build was blocked by `No space left on device`; no partial
  guide was accepted. The first attempt to append this entry was likewise
  blocked and was retried only after scoped cleanup.
- Disk measurement identified 3.6 GiB in the isolated transplant's reproducible
  `.o_game_term`, `.o_server`, `.d_game_term`, and `.d_server` build
  intermediates. Only those four temporary build trees were deleted, freeing
  approximately 3.7 GiB. Source, linked binaries, web outputs, repositories,
  and user-owned work were untouched.
- The successor must now restore the exact guide and rerun site staging/web
  build. Native/server rebuilds remain reproducible but their intermediate
  caches were intentionally discarded.

## P0C-06 · 2026-08-12 — transplant web manifest lacked Git identity

- The second Emscripten build completed and staged the browser bundle, including
  `index.wasm`, `index.data`, the full material atlas ladder, fonts, player
  guide, and watchdog slot manifest.
- During manifest generation, three `fatal: not a git repository` diagnostics
  were emitted because the isolated transplant intentionally has no `.git`.
  The script exited zero and recorded artifact hashes, but left `source_ref`,
  `git_head`, and `runtime_root` blank. That temporary manifest is not valid
  provenance for publication.
- The product must first be installed into the actual private repository, then
  its slot manifest regenerated and checked for nonblank repository identity.
  The web binary itself is built; repository-connected staging is still open.

## P0C-06 · 2026-08-12 — first live browser navigation was refused

- The first browser navigation to the local built site at
  `http://127.0.0.1:8765/` returned `ERR_CONNECTION_REFUSED` even though the
  detached-server launch command had returned PID 47066.
- No page or visual proof was obtained. The launch receipt is therefore
  insufficient; the successor must inspect the server PID/log, establish a
  successful HTTP response independently, then navigate only after that
  acceptance precondition is observed.

## P0C-06 · 2026-08-12 — full DOM snapshot was an invalid proof transport

- A persistent foreground server was then established with PID 69085, a log at
  `/tmp/p0c06-web-server.log`, a listening socket on `127.0.0.1:8765`, and an
  independent HTTP 200 response for the 348,077-byte `index.html`.
- Browser reload succeeded far enough to produce page state, but requesting the
  full DOM snapshot attempted to return roughly 65 KiB of inline binary image
  data and was blocked by the context-bloat guard. That snapshot is not usable
  evidence and no inline bytes will be retained.
- The successor must use bounded page evaluation, console diagnostics, and a
  saved/cropped screenshot or browser screenshot channel instead of dumping the
  full canvas-bearing DOM. The page itself need not be reloaded unless current
  state proves stale.

## P0C-06 · 2026-08-12 — first browser connection interaction over-returned canvas bytes

- The actual authoritative server was started on `127.0.0.1:8080`; it loaded
  the candidate map, initialized eight NPCs and nine ordinary world items,
  seeded both Block Feature placed-block variants, reported appearance contract
  version 3, and entered its 30 Hz authoritative loop.
- Browser controls were uniquely resolved, filled with player
  `armored-audit` and server `127.0.0.1:8080`, and PLAY was activated. The
  browser-control response then attempted to return roughly 238 KiB of inline
  canvas bytes and was blocked before its structured state reached the audit.
- The interaction may have succeeded, but that blocked response is not proof.
  The successor must inspect the bounded recovered preview and query current
  state without another click or full canvas-bearing response. No GIF may be
  recorded from this attempt.

## P0C-06 · 2026-08-12 — live join stalled on a stale web wire layout

- Follow-up evidence shows the socket itself opened: browser logs report
  `ws ready!`, TCP is established, and the browser sent JOIN_V2 contract version
  3. However, the server authoritative state remains at zero players and no
  join response arrived.
- Root cause is source-level and exact: candidate `web/game_web.html` constructs
  a 358-byte JOIN_V2 ending after the 31-byte name. The integrated
  `STRUCT_REQ_JOIN_V2` is 488 bytes because the final glyph identity contract
  adds two 65-byte fields, `lut_hash` and `page_atlas_chain_hash`. The server
  accepts only `sizeof(STRUCT_REQ_JOIN_V2)`, so it never enters the JOIN_V2
  handler for the stale 358-byte request.
- This proves the first normalized integration omitted a required web protocol
  owner. The successor must port the exact final request layout and accepted
  response layout/identity handling, rebuild web, and obtain an authoritative
  player entry before any visual/GIF claim.

## P0C-06 · 2026-08-12 — rebuilt-page reload again over-returned canvas bytes

- The rebuilt site contains the corrected 488-byte JOIN_V2 request, the
  464-byte accepted-response threshold, and the seven-argument server contract
  setter. Its Emscripten build completed successfully and staged a 36 MiB WASM
  plus 23 MiB data bundle.
- Reloading the live browser page succeeded, but the browser-control response
  again attempted to return roughly 70 KiB of inline canvas data and was
  blocked. The recovered preview is diagnostic only and is not accepted as a
  product GIF or runtime proof.
- A separate bounded state query confirmed the rebuilt page is complete and
  reset at the real PLAY gate with the intended player/server fields. The next
  action must isolate the PLAY interaction from all returned page/canvas state,
  then independently inspect the server-owned authoritative player record.

## P0C-06 · 2026-08-12 — synthetic DOM click was not a valid PLAY action

- An attempt to isolate PLAY from the browser's automatic canvas return called
  `.click()` on `#play-btn` inside page evaluation. That surface does not expose
  a callable DOM `click` member, so the attempt raised `TypeError` and did not
  activate the runtime.
- No connection or gameplay claim is derived from this attempt. The successor
  must inspect the actual control type and invoke the supported browser-visible
  interaction path, then verify the result independently from server state.

## P0C-06 · 2026-08-12 — first held movement did not reach the wearables

- The supported browser PLAY interaction did activate the rebuilt runtime;
  independent evidence now shows `GAME RUNNING (multiplayer id=0)`, a matching
  488-byte JOIN_V2 server diagnostic, and one authoritative player. The click
  call itself again over-returned canvas bytes, so only those independent
  results close the join failure.
- A first 1.2-second `D` movement action also over-returned canvas bytes and
  produced only about 0.1 units of horizontal displacement while the server Z
  value changed materially. It did not bring the player within the six-unit
  pickup radius of helmet/armour and is not an acceptable recording take.
- The successor must determine the real camera-relative movement direction
  from server positions, use bounded input intervals, and confirm proximity to
  definitions 410/411 before ordinary numbered pickup input.

## P0C-06 · 2026-08-12 — assumed browser keyboard surface was unavailable

- The next input attempt assumed a Playwright-style
  `tab.playwright.keyboard.down/up` surface. This browser binding does not
  expose `keyboard`, so it failed immediately before sending any movement.
- Authoritative position was unchanged and no recording claim is attached to
  the attempt. The successor must use only an input method actually exposed by
  the selected browser binding, then verify the resulting server position.

## P0C-06 · 2026-08-12 — inventory view was not a judgeable GIF surface

- Ordinary numbered pickup succeeded for the map-authored helmet 410, armour
  411, and sword 409; the server now reports all three as equipped in slots
  301, 306, and 303 on the live player.
- The first proposed recording setup opened the real inventory, but the
  captured panel is mostly blank grey at the normal README presentation size
  and its bottom labels are not legible enough to prove the loadout visually.
  That setup is rejected and will not be published as a GIF.
- The successor must record the armored sprite itself at a tight, judgeable
  crop while it moves in the real Block Feature world, keeping the authored
  blocks visible and corroborating the three-layer server state separately.

## P0C-06 · 2026-08-12 — first armored-frame encoding used the wrong decoder

- Sixty real browser gameplay frames were captured while the three-piece
  loadout remained equipped and the player moved/rotated beside the Block
  Feature blocks. The browser returned JPEG-encoded frames even though the
  temporary filenames used a `.png` suffix.
- The first GIF encoding attempt therefore failed with `Invalid PNG
  signature`; it produced no GIF and is not a deliverable.
- The successor must explicitly decode the captured frames as MJPEG (or give
  them truthful extensions), then inspect the resulting animation before any
  README link is added.

## P0C-06 · 2026-08-12 — corrected encoder refused the failed output path

- The explicit-MJPEG successor did not start because the prior failed command
  had left its output pathname present and ffmpeg correctly refused to
  overwrite it interactively.
- No animation was changed or accepted. The successor uses a new versioned
  temporary output path so the failed artifact remains distinguishable until
  the valid result is inspected.

## P0C-06 · 2026-08-12 — first repository install omitted two selected tests

- The validated runtime/source/assets and accepted armored GIF copied into the
  real private repository, but the scripts transfer exited 23 because two
  selected glyph-topology tests were not present in the isolated transplant:
  `test_glyph_topology_gate_t1.py` and
  `test_glyph_topology_gate_t2_fixtures.py`.
- Existing files in that transfer were copied; the two absent tests were not
  silently represented as present. The product is not yet verified in the
  repository.
- The successor must restore those exact committed test owners from the final
  normalized-XP source identity (or explicitly remove them from the selected
  suite with justification), then enumerate and execute the actual suite.

## P0C-06 · 2026-08-12 — first installed Python suite failed at collection

- After restoring the two exact test files, pytest collected 17 cases but
  stopped on three import errors. The minimal scripts allowlist omitted
  `scripts/glyph_sidecar.py`, `scripts/glyph_skeleton.py`, and the Gate-T2
  oracle fixture required by those selected tests.
- No individual test result is counted from a collection-failed run. This is a
  repository packaging failure, not evidence against the runtime behavior.
- The successor must restore the exact final-source helper/fixture owners,
  rerun collection, and only then report executable test counts.

## P0C-06 · 2026-08-12 — second Python collection exposed a transitive helper omission

- Restoring the direct helpers reduced collection failures from three to two,
  but `glyph_skeleton.py` itself imports the committed morphology/font-chain
  owners that the minimal scripts allowlist still omitted.
- Pytest again stopped at collection; its 17 discovered cases are not counted
  as passes. The omitted dependency chain is explicit, not an assertion
  failure.
- The successor must restore `glyph_morphology_browser.py`,
  `generate_glyph_shape_catalog.py`, and `fl4482_font_chain.py` from the same
  final source identity before rerunning.

## P0C-06 · 2026-08-12 — first fully collected Python run had three real failures

- With the helper chain present, pytest collected and executed 25 cases:
  22 passed and 3 failed.
- One failure came from a stale candidate-era admission test constant expecting
  manifest `8da401...`; the integrated final glyph package and generated table
  correctly use `077de379...`, and the final-source version of that same test
  expects `077de379...`.
- Two topology-fixture failures were packaging gaps: the selected tests require
  final-source `assets/fonts/unifont-17.0.04.otf`, which was absent from the
  Block Feature candidate asset extraction. They did not report a topology
  mismatch.
- The successor must replace the stale test owner with its exact final version,
  restore the pinned font fixture, and rerun all 25 cases. The 22/25 result is
  retained as failure evidence, not reported as completion.

## P0C-06 · 2026-08-12 — first JavaScript test encoded a false static page requirement

- The 25-case Python suite passed after restoring the final test/font owners.
  The first JavaScript test then failed because it requires the literal
  `material.additive.v1.page0_rgba8.json` in `game_web.html`.
- That requirement is false for both the final source identity and the working
  runtime: the web client loads `atlas_of_atlases.json`, selects a page by cell
  size, and fetches the manifest-owned `page.url`. The final manifest currently
  points to the hash-bound page16 artifact. `build-web.sh` separately stages
  page0 only as a compatibility artifact.
- Adding a dead page0 string to runtime code would make the test green without
  proving the loader. The successor must correct the test to assert the dynamic
  manifest binding and verify every referenced page file/hash, then rerun all
  JavaScript tests.

## P0C-06 · 2026-08-12 — first corrected atlas test hashed the JSON envelope

- The dynamic-binding correction found the manifest-owned page file, but its
  first hash assertion compared `page_hash` with SHA-256 of the entire JSON
  file. The contract defines `page_hash` over the decoded `rgba8` byte array,
  which the existing Python compile-output test also enforces.
- The test therefore failed for an incorrect verifier implementation; no
  runtime or atlas artifact changed. The successor must hash the decoded RGBA8
  payload and also compare the page JSON's embedded hash before rerunning.

## P0C-06 · 2026-08-12 — bounded terminal launch produced an oversized capture

- The actual-repository terminal binary linked and entered the runtime, loading
  all six audio samples and admitting the normalized extended-glyph fixture.
  Redirecting its ANSI framebuffer for roughly one second nevertheless wrote a
  258,199,636-byte diagnostic log because the renderer is intentionally
  unthrottled when its display stream is redirected.
- The launch is valid execution evidence, but the capture is not a useful or
  retainable proof artifact. It was inspected only for bounded startup markers
  and then removed from `/tmp`; no terminal capture is published as a GIF.
- Runtime acceptance remains attached to the real browser/server gameplay path,
  while future terminal smoke checks must discard the framebuffer stream or
  use a byte-capped consumer.

## P0C-06 · 2026-08-12 — first actual-repository manifest used an empty root

- The full WebAssembly build succeeded, but its generated slot manifest encoded
  the repository-owned runtime root as an empty string. That is Node's literal
  relative-path result when both paths are equal, but it is ambiguous to a
  human or downstream consumer and fails the intended non-empty identity check.
- Artifact hashes, Git HEAD, and dirty state were present; this was a metadata
  serialization defect rather than a missing or failed runtime build.
- The relative-path formatter must serialize an equal path as `.`, then the
  manifest must be regenerated after the product commit so its source identity
  names the delivered commit rather than the repository's previous HEAD.

## P0C-06 · 2026-08-12 — candidate addon carried a broken external worktree link

- The pre-staging hygiene audit found
  `addons/io_asciicker/io_asciicker` as an absolute symlink into the source
  repository's `.claude/worktrees/fix-termpp-skin/` directory.
- The historical target no longer exists, so the link is both non-standalone
  and broken. The surrounding addon directory is already the importable
  `io_asciicker` package; no runtime or build owner resolves through this
  self-nested development link.
- The symlink must be moved recoverably to Trash rather than committed. The
  other eight asset symlinks are relative links to checked-in fixture meshes
  and remain valid.

## P0C-06 · 2026-08-12 — whole-import whitespace check is not clean

- `git diff --cached --check` reports extensive trailing whitespace and CRLF
  endings in the imported historical C/C++, JavaScript, addon, and asset-side
  text files. Because the previous repository contained only the rejected
  proxy, Git sees the complete upstream runtime as newly added and checks every
  historical line.
- Bulk-normalizing those files would destroy byte fidelity to the selected
  source identities and create an unrelated formatting rewrite. The result is
  therefore retained as an explicit limitation, not silently called clean.
- Authored integration surfaces (README, attribution, verification record,
  workflow, test requirements, standalone runner, failure-log additions, and
  the two focused JavaScript corrections) must pass their scoped whitespace
  check. Runtime correctness remains proven by the three builds and executable
  suites rather than by rewriting historical formatting.

## P0C-06 · 2026-08-12 — direct inspection of an unreferenced web GIF over-returned

- The staged-file inventory exposed an additional 19 MB
  `web/asciicker.gif` that is not referenced by source, the web build, or the
  README. A direct image inspection attempt tried to return the entire GIF and
  was blocked by the bounded-output guard.
- No judgment is based on the rejected payload. The guard produced a bounded
  still preview path, which must be inspected instead while source references
  and frame metadata decide whether the file is a runtime asset or stale media.
- If it is not required by the standalone runtime and does not prove the
  product, it must be moved recoverably out of the repository rather than
  retained merely because it existed in the candidate tree.
- Follow-up: a repository-wide reference search found zero consumers, and the
  bounded preview showed an older gameplay capture unrelated to the armored
  normalized-layer proof. The file was moved recoverably to Trash; the accepted
  armored gameplay recording is now the repository's only GIF.

## P0C-06 · 2026-08-12 — first tmux code-review delivery exceeded command length

- The first required code-review submission embedded a bounded 520-line staged
  diff plus evidence in a single `tmux set-buffer` shell command. The shell
  rejected it as `command too long` before any text reached the reviewer pane.
- No review result exists from that attempt. The successor must send a compact
  request containing the acceptance evidence and exact shared-checkout
  inspection commands, then verify that it left the pane input box.

## P0C-06 · 2026-08-12 — imported package docs violated the documentation boundary

- The root-level Markdown check passed, but a stricter staged-path audit found
  eight documentation or license files under `addons/`, `assets/`, and
  `engine/`. That violates the explicit repository rule that the root README
  is the sole documentation file outside `docs/`.
- None of the eight is a runtime/build input. All must be preserved under
  `docs/upstream/` or `docs/licenses/`; the two cJSON source comments that
  name its provenance file must be updated to the centralized path.
- This is an organization defect in the first full-runtime extraction, not
  permission to omit upstream license or provenance material.

## P0C-06 · 2026-08-12 — first CI documentation gate scanned ignored build state

- Independent code review executed the workflow's documentation-boundary
  `find` after local tests and found ignored `.pytest_cache/README.md` and
  the ignored `.web/player-guide.md` staging copy. The gate would therefore
  fail on generated workspace state even though neither path is tracked.
- The acceptance condition concerns repository content, so scanning every
  filesystem byproduct is the wrong owner. The workflow must query
  `git ls-files` for Markdown/licenses and forbidden router names, matching
  the successful staged-content privacy audit.

## P0C-06 · 2026-08-12 — imported Blender MCP addon embedded an API key fallback

- Independent review found `addons/blender_mcp_addon.py` assigning
  `RODIN_FREE_TRIAL_KEY` from an environment variable with a non-empty
  64-character literal fallback. The first secret scan missed it because the
  value does not use a provider prefix covered by that narrow regex.
- A trial credential is still a credential value, and the Blender-to-agent MCP
  addon is unrelated to the standalone native/server/web game. The entire file
  must be moved recoverably to Trash rather than replacing the key while
  retaining out-of-scope integration code.
- The workflow and local audit must additionally reject tracked source files
  containing non-empty long-literal fallbacks in environment lookups.

## P0C-06 · 2026-08-12 — imported editor tree contained a worker-task stub

- The expanded agent/process scan found `editor/asciiid_mcp.cpp`; its entire
  content is one comment directing the reader to a `Worker task description`
  for extraction scope.
- No makefile or runtime source references the file. It is agent-process
  residue, not an editor implementation, and must be moved recoverably to Trash.
- The tracked-content hygiene gate must reject worker-task and
  agent/transcript instruction phrases so future full-tree imports cannot hide
  process residue behind a source extension.

## P0C-06 · 2026-08-12 — first process-phrase gate matched its own failure record

- The new tracked-content phrase gate initially scanned
  `docs/FAILURE_LOG.md`, which necessarily records the rejected
  `Worker task description` text. Its first local execution therefore stopped
  before printing any success receipts.
- Durable failure evidence is allowed to name the condition it records. The
  gate must exclude only the failure log while continuing to scan every product,
  source, test, workflow, and ordinary documentation file.

## P0C-06 · 2026-08-12 — pytest glob did not execute script-style test mains

- Independent review invoked
  `scripts/test_fl4131_glyph_manifest_compile_outputs.py` directly and it
  failed: the candidate-era script expected manifest `8da401...` and an older
  compiler output shape, while the integrated final manifest is `077de379...`.
- The standalone runner's broad pytest glob imported that file but collected no
  tests from its `main()` path. The reported 25 pytest passes were real for
  the collected functions, but they did not cover every selected Python test
  script and were incorrectly presented as the entire Python verification
  surface.
- The exact final-source test owner must replace the stale version, every
  script-style test must be enumerated and executed directly, and counts must
  distinguish pytest cases from standalone script checks.

## P0C-06 · 2026-08-12 — glyph-admission script treated a missing runtime as success

- `scripts/test_fl4131_glyph_admission.py` described a runtime loader test but
  its abandoned harness always returned `None`; when the default
  `.run/game` was absent it printed `SKIP` and exited zero after checking
  only fixture filenames.
- A skipped runtime is not a pass. The abandoned harness text must be replaced
  by an honest, executable source-contract check for fixture presence, registry
  load/fail-closed wiring, and manifest validity. Actual runtime admission
  remains separate execution evidence from the built terminal client.

## P0C-06 · 2026-08-12 — full-tree audit found 18 more worker-task stubs

- After the first one-line editor stub was removed, the final staged-tree scan
  found 18 additional `.cpp` and `.h` files whose complete content was the same
  `Worker task description` extraction note.
- Each file is exactly one comment line; filename-reference checks found no
  include, makefile, source, or test owner for any of them. They are unused
  agent-process residue, not implementations of the named runtime modules.
- All 18 files must be moved recoverably to Trash. The final tracked-content
  gate must then return zero process-phrase paths across the product tree while
  retaining this failure record as the sole documented exception.

## P0C-06 · 2026-08-12 — first native code review found stale standalone owners

- The first normal-subagent code review passed at 7.17/10 but found four
  actionable defects; passing the numerical gate does not waive them.
- `README.md` and `docs/VERIFICATION.md` named Emscripten 4.0.17 while the
  enforced `.emscripten-version` and successful build use 4.0.21. A clean user
  following the README would hit the version gate before the browser build.
- The imported root `Makefile` advertised setup, status, launcher, V8, pipeline,
  Blender, MCP, web-E2E, and engine-test paths that are not present in the
  standalone tree. Those targets describe a larger development checkout, not
  this repository's runnable contract. The separate `clean.sh` is not empty,
  but the root help surface is still materially false.
- GitHub Actions rebuilds the authoritative server but not the terminal or web
  clients. Local builds and the real gameplay recording remain direct evidence,
  but clean-checkout automation must cover all three named build surfaces.
- The ignored local slot manifest still identifies the deleted proxy commit and
  reports a dirty tree. It must be regenerated only after the corrected commit
  exists. Tracked provenance also retains workstation-absolute Desktop paths;
  portable provenance must preserve the source identity without publishing the
  operator's local directory layout.

## P0C-06 · 2026-08-12 — replacement make help initially retained an unbuildable editor target

- A dry run of the first simplified root `Makefile` reached the historical
  editor makefile and failed because `.o_asciiid/vendor/imgui/imgui.o` has no
  rule in this standalone tree.
- The accepted product contract names the authoritative server, native terminal
  client, and browser client. It does not require the historical editor or SDL
  desktop development target. The convenience Makefile must advertise only the
  three directly verified client/server surfaces plus their checks and launch
  commands.

## P0C-06 · 2026-08-12 — first terminal CI job selected an unavailable Linux V8 owner

- Static inspection of the proposed Ubuntu terminal job found that
  `makefile_game_term` links `vendor/v8/v8/out.gn/x64.release/obj/libv8_monolith.a`,
  but `vendor/v8/` is intentionally not tracked in this standalone repository.
- The locally verified terminal target is `makefile_game_term_mac`, whose V8
  dependency is a Homebrew package. CI must therefore prove the server on
  Ubuntu, the terminal client on macOS with Homebrew V8, and the browser through
  the pinned Emscripten job; an Ubuntu terminal job would be a known failure.
- The README's terminal prerequisites also omitted V8 even though the verified
  macOS link uses it. The run contract must name `brew install v8` explicitly.

## P0C-06 · 2026-08-12 — authored diff check found a trailing blank line

- The scoped `git diff --cached --check` rejected the first `clean.sh` hardening
  patch for a new blank line at end of file. The historical full import retains
  upstream whitespace, but every newly authored standalone hunk must be clean.

## P0C-06 · 2026-08-12 — second native review found a self-matching CI gate

- The second normal-subagent review rejected the staged workflow because its
  process-residue `git grep` embeds the same complete forbidden phrases that it
  scans. The workflow therefore matches its own YAML and deterministically
  fails the contracts job even when the product tree is clean.
- The gate must continue scanning the workflow itself. Its source must split the
  forbidden literals into shell fragments and reconstruct the exact regex only
  at runtime, then the complete CI command block must execute locally.

## P0C-06 · 2026-08-12 — README still overstated Linux terminal readiness

- The post-review diff retained `On Linux, use makefile_game_term` even though
  that target requires an externally provisioned V8 monolith under `vendor/v8/`
  and the standalone repository intentionally does not vendor it.
- The README must identify macOS/Homebrew V8 as the verified terminal path and
  state the exact extra Linux prerequisite instead of presenting the Linux
  makefile as a clean-checkout command.

## P0C-06 · 2026-08-12 — legacy root build script remained a false entrypoint

- The post-review entrypoint scan found the historical root `build.sh`. It still
  invokes the editor and SDL desktop builds that the standalone convenience
  surface deliberately does not claim and that fail in this extracted tree.
- The README no longer references it, but a root executable named `build.sh` is
  still an implied runnable path. Preserve it under `docs/upstream/` as source
  history and leave the verified root `Makefile` as the sole build dispatcher.

## P0C-06 · 2026-08-12 — moved historical build script failed its scoped whitespace check

- After relocation, the authored-surface diff check exposed three upstream
  comment lines with trailing spaces in `docs/upstream/historical-build.sh`.
- Normalize only those three comment lines; do not sweep unrelated historical
  source whitespace into the integration commit.

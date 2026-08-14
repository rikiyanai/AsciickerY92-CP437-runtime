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

## P0C-06 · 2026-08-12 — first pushed web CI job lacked its server artifact owner

- GitHub Actions run `31536352775` built the WebAssembly client through final
  site staging, then failed while generating `.web/slot_manifest.json` because
  the isolated `web-build` job had not produced `.run/server`.
- Local web verification passed only because the authoritative server binary
  was already present from the separately executed server build. The workflow
  therefore did not reproduce the integrated web/server bundle from a clean
  checkout even though the local path did.
- The `web-build` job must install the server dependency and build the server
  before `build-web.sh`. Using the script's web-only missing-server override
  would weaken this full-runtime repository's bundle proof by emitting a null
  server hash, so it is not an acceptable correction here.

## P0C-06 · 2026-08-12 — wide gameplay GIF did not prove the appearance stack

- `docs/recordings/armored-block-feature-gameplay.gif` is a valid recording of
  the real browser client moving in the Block Feature world, but its 520-pixel
  wide view leaves the player too small to judge the base, armor, helmet, and
  sword layers individually.
- The animation shows neither the unequipped baseline nor equipment transitions,
  and it has no artifact-level test that checks decoded frames for those visual
  states. It therefore proves runtime/world movement, not the full normalized-XP
  appearance stack.
- `README.md` also overattributes separately observed pickup and authoritative
  slot evidence to that GIF. Server-owned slots 301, 306, and 303 remain valid
  runtime evidence, but they are not visible in the recording and must be stated
  separately.
- Preserve the wide animation as world proof. The successor must record one real
  browser/server session with an uncropped world view plus a nearest-neighbor
  player inset, explicitly showing the unequipped baseline, armor added, helmet
  added, a held sword if visibly rendered, and movement/turning across angles.
  A receipt and decoded all-frame semantic-state test must bind the new artifact
  to those claims without treating server slot state as a visible pixel fact.

## P0C-06 · 2026-08-12 — first successor join check queried a nonexistent wrapper

- The first new-session browser interaction uniquely resolved the live name,
  server, and PLAY controls and submitted them, but its immediate confirmation
  query assumed a `#login` wrapper that the actual page does not expose.
- The query failed before returning bounded state. The click may have joined the
  server, so it is not repeated. The successor must inspect the current canvas,
  runtime log, and server-owned player state independently before sending any
  further input or capturing a claimed baseline.

## P0C-06 · 2026-08-12 — live server and browser bundle identities diverged

- Independent console evidence from the first successor session showed the
  browser sending a 488-byte JOIN_V2 request and the still-running authoritative
  server rejecting it with `bundle_hash_mismatch`.
- The rejection is a valid fail-closed contract result, not a playable session
  or capture source. No screenshot from the rejected login is product evidence.
- The server process belongs to this checkout but predates the current built web
  bundle. Rebuild and restart that exact repository-owned server, then require a
  successful JOIN_V2 response before capturing the unequipped baseline.

## P0C-06 · 2026-08-12 — rebuild falsified the stale-server hypothesis

- The scoped server rebuild passed actor-table coverage and server-reachability
  gates without relinking: `.run/server` was already the exact binary recorded
  by the current web slot manifest (SHA-256 `d7fce0d1769...`).
- The checked-in/current appearance bundle is `43976fa43434...`, while the
  rejected Chrome client logged request bundle `9a96b4d5485e...`. This locates
  the divergence in Chrome's cached prior client artifacts, not in the running
  authoritative server.
- The successor must disable the cache for this local tab, reload the same
  current site, and verify the request identity and accepted join. A server
  restart alone would not correct the stale browser payload.

## P0C-06 · 2026-08-12 — uncached reload proved the staged web build was stale

- Chrome cache was disabled through the local tab's developer protocol and the
  site was reloaded, but the next JOIN_V2 request still carried bundle
  `9a96b4d5485e...` and was again rejected against current bundle
  `43976fa43434...`.
- That result falsifies browser HTTP cache as the remaining owner. The served
  `.web/index.wasm`/preload bundle itself was built before the current compiled
  appearance bundle even though the later slot manifest names the current Git
  head.
- Regenerate the complete web bundle with `build-web.sh`; do not edit the join
  hash, bypass the server rejection, or use either rejected session as proof.

## P0C-06 · 2026-08-12 — generic repository name hid the runnable contract

- `asciicker-historical-runtime` described provenance but did not identify the
  standalone product: the Asciicker Y9-2 Block Feature-era CP437 native/server/
  browser runtime with the normalized-XP bundle integration.
- Rename the private repository and local checkout to the exact product name
  `AsciickerY92-CP437-runtime`. The README must still state that the runtime is
  not CP437-only because admitted extended glyphs travel through the sidecar,
  compiled manifest, and atlas path.

## P0C-06 · 2026-08-12 — port 8765 was owned by the obsolete transplant

- After rebuilding the renamed checkout, an independent served-file hash still
  disagreed with its `.web/index.html`. `lsof` located HTTP PID 69085 at
  `/private/tmp/p0c06-transplant.Gz4n01`, while the authoritative game server's
  working directory correctly followed the renamed product checkout.
- This supersedes the narrower "staged web build was stale" diagnosis: the
  stale bundle was real, but port 8765 was serving it from the obsolete
  transplant rather than the rebuilt repository.
- Stop only that confirmed HTTP owner and relaunch port 8765 with the renamed
  checkout's absolute `.web` directory. Require the served and local HTML hashes
  to converge before another browser reload or join attempt.

## P0C-06 · 2026-08-12 — first renamed-checkout HTTP launch did not persist

- A background `nohup` launch wrote PID 80964 but the process exited before the
  first socket check and left an empty log. Port 8765 was closed; no browser
  request reached the renamed checkout.
- The successor must keep the HTTP server in an owned execution session, record
  its PID and log path, and prove both a listening socket and exact served/local
  HTML hash equality before reloading Chrome.

## P0C-06 · 2026-08-12 — Chrome rejected raw developer-protocol keyboard input

- The accepted live session captured an unequipped eight-frame baseline, then
  the first positioning attempt tried to hold `D` through raw developer-protocol
  key events so authoritative movement could be measured.
- This Chrome control surface explicitly rejects raw input injection and directed
  the caller to visible CUA keyboard actions. No key event was sent and the
  authoritative player position did not change.
- Use repeated supported keyboard presses against the focused real canvas,
  measuring the server snapshot after each bounded batch. Do not add a runtime
  teleport, verifier mutation, or reconstructed state to simplify the recording.

## P0C-06 · 2026-08-12 — completed keypresses were shorter than a game tick

- Ten supported Chrome `D` keypresses reached the focused canvas: the page input
  trace recorded matching `KeyD` down/character/up events. The authoritative
  snapshot nevertheless remained exactly `(-2.8, -73.6, 73.25)` because each
  completed press released before the game's held-key sampling observed it.
- No captured delivery frame is attributed to movement from that batch. The
  successor must use the production page's own `Keyb(DOWN/UP)` bridge with a
  bounded hold interval, then verify displacement from the authoritative
  snapshot. This preserves the real client input path without enabling a
  teleport or verifier-only mutation API.

## P0C-06 · 2026-08-12 — first production-bridge hold overshot the equipment

- A 700 ms `Keyb(DOWN, D)` hold proved the real production bridge reaches the
  authoritative server, moving the player from `(-2.8, -73.6, 73.25)` to
  `(18.195, -106.429, 57.25)`. It also overshot all equipment pickup radii;
  the pickup strip became empty.
- Those positioning frames are not included in the deliverable. Use the
  opposite `A` vector for a shorter bounded hold, and accept the correction only
  when the server-owned pickup strip actually contains definitions 409, 410,
  and 411 before any numbered pickup input.

## P0C-06 · 2026-08-12 — opposite hold also crossed past the pickup cluster

- The first correction held `A` for 550 ms and moved to
  `(-18.176, -58.624, 57.25)`, again leaving the pickup strip empty. The
  vector was correct but the interval ignored the runtime's acceleration and
  produced a second overshoot.
- Discard those positioning frames. Use shorter measured holds, decomposing the
  remaining displacement across strafe and forward axes, and gate every pickup
  on the authoritative strip rather than screen proximity alone.

## P0C-06 · 2026-08-12 — measured strafe climbed the placeable block

- Short `D`/`W` probes established the two movement vectors, but the final
  330 ms strafe crossed the nearby placed-block collision volume. The server Z
  rose to `259.476` and settled at the block top near `97.431`; only block
  definitions 420/421 remained in pickup range.
- This is real Block Feature collision behavior but not useful layer-transition
  staging. No airborne/block-top frame enters the deliverable. Use a short
  forward move followed by a short strafe to route off the block and toward the
  six ground equipment items, then gate on pickup-strip definition identities.

## P0C-06 · 2026-08-12 — numbered pickup cascaded across a reordering strip

- The player reached the equipment cluster and pressed visible pickup number 4
  for armor item 25095. The recorder later reported 24 pickup attempts as the
  strip reordered, and server truth contained both armor 411 and an unintended
  sword 409.
- Dropping the sword through the real inventory briefly produced the desired
  armor-only state, but it was picked again before the eight-frame phase ended.
  The saved attempted armor frames (temporary indices 8–15) are rejected and
  must not enter the GIF or receipt.
- Restart a clean browser session and use a single visible mouse click on the
  exact armor item in the real pickup strip. After each click, require one new
  owned item and an exact server-truth definition/slot set before capturing any
  phase frames.

## P0C-06 · 2026-08-12 — clean-session browser transaction timed out after reload

- The first clean-session restart exceeded the browser-control deadline while
  waiting for the page-debug channel after reload. The compound transaction may
  have reached login or PLAY, so no control is repeated and no resulting frame
  is accepted yet.
- Inspect the current tab visually and read the latest connection log before
  deciding whether to join, resume, or reload. Only an independently verified
  zero-entry server-truth state can start the replacement same-session capture.

## P0C-06 · 2026-08-12 — reset variables did not retarget the capture closure

- The clean session joined with zero equipment entries, but the first baseline
  capture call reused a helper closed over the rejected attempt's directory and
  frame index. The new temporary directory stayed empty, so its reported
  checkpoint is not paired with the intended clean-session pixels.
- Do not reuse that helper or any frame it appended to the rejected directory.
  Create a new distinctly named capture function closed over a new directory,
  recapture baseline, and verify its file count/range before positioning.

## P0C-06 · 2026-08-12 — exact pickup-strip click still crossed adjacent items

- The replacement session recaptured eight clean baseline frames and resolved
  armor item 25095 to its exact visible strip interval. One CUA click inside
  that interval nevertheless produced four pickup requests while the dense
  strip reordered, leaving server truth with sword 409, helmet 410, and armor
  411 together.
- No post-click frames were saved. The UI's dense-cluster request behavior means
  selecting by number or pixel is not sufficient when all three definitions are
  simultaneously in range.
- Clear the accidental loadout through the real inventory, then position at the
  outer edge of the six-unit radius so only one definition class is eligible:
  armor first, helmet second, sword last. Gate the strip's definition set before
  every click and the exact server-truth slot set before every capture phase.

## P0C-06 · 2026-08-12 — inventory navigation re-equipped the second sword

- Repeated `Y` input on the first inventory focus removed one sword from the
  authoritative weapon slot, but the four owned inventory records persisted.
  The attempted `ArrowDown`, `Y` sequence then focused the second sword and
  re-equipped slot 303, restoring the full server-truth set 409/410/411 instead
  of advancing directly to helmet removal.
- No frame from this inventory interaction is accepted. Clear the loadout by
  toggling the currently focused second sword off, advancing exactly once to
  helmet and exactly once to armor, and checking authoritative slot/definition
  state after every single toggle before returning to the world view.

## P0C-06 · 2026-08-12 — the cleanup session lost its browser tab

- With the panel genuinely open, an authoritative focus advance did remove
  helmet 410, but the runtime simultaneously restored weapon slot 303 from the
  remaining owned sword. Before the next single-step state query, the
  agent-created Chrome tab disappeared and its control channel reported no
  remaining tabs.
- The clean baseline saved from that disconnected session cannot support a
  same-session transition artifact and is rejected with the rest of that
  attempt. Open a fresh runtime tab, prove zero server entries, and recapture
  the baseline. This time, move to a definition-isolated pickup radius before
  the first item interaction so inventory cleanup is unnecessary.

## P0C-06 · 2026-08-12 — disconnected player retained the equipment corpus

- A fresh browser actor joined with an empty appearance loadout, but the
  authoritative item snapshot showed all six 409/410/411 equipment instances
  still owned by client 0 from the disconnected cleanup session. Moving within
  four world units of armor therefore produced an empty eligible pickup strip.
- The newly captured empty-loadout frames do not form a viable transition
  session and are rejected. Restart the local authoritative server to reset its
  seeded item corpus, reconnect one fresh actor, verify both zero loadout and
  unowned equipment, and only then begin the accepted same-session capture.

## P0C-06 · 2026-08-12 — direct route crossed the dense equipment radius

- After the authoritative reset, the accepted baseline was clean. The first
  route then passed through the center of the six equipment instances while
  combining forward and strafe movement. By the next checkpoint the client had
  sent eight pickup requests and server truth already contained slots
  303/301/306 for definitions 409/410/411.
- No post-baseline frame from that session is accepted. Reset once more and
  route around the cluster: move well south while still west, move east outside
  every equipment radius, then approach from the east until only armor 411 is
  eligible. Verify zero pickup attempts after each leg.

## P0C-06 · 2026-08-12 — route exposed a retained pointer contact

- The around-cluster route remained unequipped until it briefly entered sword
  range. Without any deliberate pickup command, the client's pickup-attempt
  counter rose from zero to one and server truth gained one item. This is
  consistent with the PLAY interaction leaving a pointer contact visible to
  the canvas after the overlay closed; the movement keys themselves do not map
  to pickup actions.
- Reject that session. On the next clean join, explicitly deliver mouse-button
  up and touch-cancel events through the production input bridge before the
  baseline, verify the attempt counter remains zero, and repeat the outside
  route with a zero-attempt gate at every leg.

## P0C-06 · 2026-08-12 — first armor capture outlasted weapon-drop grace

- The east approach correctly auto-picked armor 411 after both 417 weapons,
  and real inventory drops briefly produced exact armor-only server truth.
  Because both dropped weapons remained at the actor's position, their
  two-second repick grace expired before the first saved armor frame; all eight
  attempted frames already contained armor plus 417.
- Reject temporary frame indices 8–15. Drop both 417 instances again, move the
  armored actor immediately east/south beyond their pickup radius while grace
  is active, require exact armor-only truth at the destination, and capture the
  replacement phase there.

## P0C-06 · 2026-08-12 — first armored movement segment climbed a block

- The final loadout and twelve-frame turn were valid, with sprite angles 0
  through 7 and exact server definitions 411/410/409. The first forward-motion
  segment then intersected the placed-block collision volume; authoritative Z
  rose from 73.25 to 370.401 before falling.
- Reject temporary frame indices 60–67. Preserve the valid turn as angle proof,
  wait for the actor to land, move away from the block with a short bounded
  strafe, and accept replacement movement only when horizontal displacement is
  visible while Z remains at the ground/support height.

## P0C-06 · 2026-08-12 — first compositor expected one movement metadata file

- The first preview compositor correctly selected replacement frame indices
  68–75 but tried to load a nonexistent `movement.frames.json`. The accepted
  movement is intentionally split across `move_ground_active.frames.json` and
  `move_ground_settle.frames.json`, so the preview stopped before writing a GIF.
- No artifact was produced. Load metadata from every accepted capture-phase
  receipt, then map the combined movement label to those two exact ranges.

## P0C-06 · 2026-08-12 — full angle sweep included world-object occlusion

- Both real-runtime turn sweeps reached multiple sprite angles, but nearby
  roofs and trees fully hid the player in several frames. Those pixels are
  truthful world occlusion yet fail the layer-detail purpose of this artifact.
- The delivery excludes original turn indices 48–59 and open-field indices
  83–86. It uses only open-field indices 76–82 and 87–91, where the same-session
  player remains readable across sprite angles 0, 1, 2, 3, 4, 6, and 7. The GIF
  says “seven visible angles”; it does not claim the omitted angle 5.

## P0C-06 · 2026-08-12 — first final GIF coalesced identical frames

- The compositor submitted 52 accepted frames, but Pillow's GIF writer merged
  five consecutive pixel-identical frames even with optimization disabled. The
  decoded artifact therefore exposed only 47 frames, preventing an exact
  one-source-frame-to-one-decoded-frame semantic receipt.
- Overwrite that artifact with a tiny visible evidence-frame counter in the
  header. The counter prevents coalescing and lets the test bind every decoded
  frame to one recorded semantic state and source screenshot.

## P0C-06 · 2026-08-12 — first artifact nonblank assertion used RGBA alpha

- The exact hash, dimensions, frame count, durations, phase states, and motion
  assertions passed. The first standalone artifact test then failed its
  nonblank check because `ImageChops.difference` on two opaque RGBA images has
  an all-zero alpha difference, which makes `getbbox()` report no box even when
  the RGB channels differ.
- Compare RGB crops against RGB black references. This changes only the test's
  pixel predicate; the GIF and receipt remain unchanged.

## P0C-06 · 2026-08-12 — transition-difference assertion repeated RGBA mistake

- The corrected per-frame nonblank loop passed, but the representative
  base/armor/helmet/sword comparison still passed RGBA crops to the same
  alpha-sensitive `getbbox()` path. The second test run therefore failed only
  that transition-difference assertion.
- Convert both representative crops to RGB before comparison, matching the
  already-correct nonblank predicate.

## P0C-06 · 2026-08-12 — first final security command was rejected

- The combined final check attempted to create and remove a temporary tracked-
  path list. The execution safety layer rejected the command because it
  contained `rm -f`; none of the checks in that compound command ran.
- Repeat the checks without a temporary file or cleanup command, using direct
  Git/file-list pipelines and scoped repository searches.

## P0C-06 · 2026-08-12 — credential regex was parsed as an option

- The replacement diff, receipt, README-link, root-document, and filename
  checks ran, but the high-confidence credential pattern began with five
  hyphens and `rg` parsed it as an unsupported flag. The surrounding shell
  fallback also allowed the compound command to print a misleading final pass
  line.
- Rerun the content scan with all options before an explicit `--` separator,
  and treat its zero-match status independently from the already-passed checks.

## P0C-06 · 2026-08-12 — old GitHub slug check mistook redirect for duplicate

- The final rename check expected the old API path to fail. GitHub instead
  resolves the former repository slug through its automatic rename redirect,
  so the command exited nonzero after printing “old repository target still
  resolves.” This does not represent a second repository.
- Verify the resolved repository object's exact `name`, `html_url`, privacy,
  and default branch, plus the local origin and directory. Record the old slug
  only as a redirect alias; do not require it to return 404.

## P0C-07 · 2026-08-14 — Wallace/Gromit sand scene

- Intended outcome: the canonical C++ runtime opens on an entirely yellow sand
  map with playable Wallace, the converted rocket within twelve world units of
  the map-owned start, and one friendly Gromit that automatically follows the
  joining player.
- Observed mismatch: the canonical map retained mixed terrain, scenery, and
  hostile generators; server spawn ignored the map-owned start; all NPCs used
  nearest-player hostile AI; the approved timestamped XP and rocket assets were
  absent from this repository.
- Owners selected: `game_map_y8.a3d` owns terrain/start/rocket placement; the
  normalized appearance sources own Wallace and Gromit selection; server tick
  state owns companion relationship, follow intent, cleanup, and damage
  exclusion. Hard-coded fallback spawn remains only for maps without a start.
- Rejected findings: the untracked June XP duplicates and modified repaired
  `adhoc/` variants are not provenance-equivalent to the approved commit and
  remain outside this repository.
- Acceptance surface: exact asset hashes; parsed v4 map invariants; generated
  appearance table currentness; native server compile; standalone contract
  suite; user-reachable live viewport plus matching authoritative companion
  state from the same run.

### P0C-07 attempt 1 — rejected underwater baseline

- The first headed run rendered the authored yellow material blue-purple.
  Authoritative terrain Z was `0`, below the global water plane at `55`; the
  discrepancy was environmental tinting, not a material-ID failure.
- Replace height `0` with the A3D canonical export baseline `128` and rebuild
  both native and web products.

### P0C-07 attempt 2 — accepted live scene

- The rebuilt viewport visibly shows ochre-yellow sand, Wallace, Gromit, and
  the rocket. Spawn resolves from the v4 map at Z `128`.
- Gromit initially lost mount definition `950` because NPC presentation derives
  it from runtime mount state. Set the companion's catalog-owned runtime state
  to `MOUNT::WOLF`; same-run authority then reports mount `950`, disposition
  companion, owner player `0`, and a post-movement distance of `6.883`.
- Disconnect cleanup removes Gromit and idle reset reconstructs zero NPCs and
  zero items from the frozen map. The headed image and receipt are committed as
  the acceptance artifact; operator signoff remains the only stage above this
  verified result.

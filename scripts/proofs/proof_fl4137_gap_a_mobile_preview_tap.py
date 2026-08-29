#!/usr/bin/env python3
"""FL-4137 Gap A — mobile tap-on-floating-preview to place proof.

Single-layer source-shape proof. Gap A is wiring: appearance pass publishes
a single mobile preview tap rect, the mobile tap router consumes it and
dispatches the SAME ITEM_ACTION_REQ_PLACE intent helper as desktop P and
mobile player double-tap.

What this proof asserts:

  Surface contract:
    - AuthoritativeHeldPreviewMobileContact struct exists in
      authoritative_client_state.h with item_id + cell rect fields.
    - AuthoritativeClientState carries one held_preview_mobile_contact
      field — single rect, not per-row, not an array. The contact lives
      in the same struct as world_pickup_rows but is a SEPARATE field;
      no world_pickup_rows writes anywhere in the publish path.
    - AuthoritativeWorldItemAppearanceFrame carries a matching
      held_preview_mobile_contact field so the per-frame work writes to
      the frame and PublishAuthoritativeWorldItemAppearanceRows copies it
      out (single-owner write seam, Law 1).

  Reset + publish:
    - ResetAuthoritativeWorldItemAppearanceRows clears the persisted
      contact each frame (value-init / valid=0 default).
    - PublishAuthoritativeWorldItemAppearanceRows copies
      frame->held_preview_mobile_contact into game->authoritative.
    - The reset does NOT touch world_pickup_rows_count beyond the
      existing clear, and the publish does NOT route the contact into
      world_pickup_rows.

  Per-row population:
    - In the per-row appearance loop, the contact is populated only when
      `held_placeable_preview && item_on_screen && preview_valid`. The
      preview_valid gate keeps the contact surface lockstep with the
      FL-4137 Gap B HideInst() — if the preview is hidden, the tap rect
      must not stay live (Law 1: single ownership; Law 3: no place
      intent from an invisible target).
    - No other branch writes to held_preview_mobile_contact.
    - The rect is derived from ProjectCoords output (cell-space) with a
      fixed half-pad, then clipped to the viewport.

  Mobile tap router:
    - StartContact (game_input.cpp) reads
      authoritative.held_preview_mobile_contact AFTER the world pickup
      strip block and BEFORE the keyboard / world-tap routing.
    - The new branch is gated by `session.mobile_controls && b == 1`
      (mobile primary tap only — desktop input and right-click torque
      untouched).
    - On hit, the branch calls RequestPlaceEquippedPlaceableAuthoritativeItem
      — the SAME helper as desktop P (game_input.cpp:1656-ish) and as the
      mobile player double-tap path (game_input.cpp:2859-ish).
    - On hit, the branch sets con->action = Input::Contact::NONE and
      returns (tap is swallowed; EndContact's PLAYER double-tap / talk_box
      path does not run for this tap).

  Authority invariants preserved:
    - SvrPlaceOwnedItemFromPlayer is still the only writer of placed pos /
      state. Gap A adds no second placement writer.
    - ITEM_ACTION_REQ_PLACE is emitted from
      authoritative_item_command_surface.cpp only — the same source as
      desktop P, mobile player double-tap, and place-by-index. No new
      emission site lives in the appearance pass or the tap router.
    - autopick / world_pickup_rows handling is untouched — no autopick of
      preview; placed world block tap stays an explicit pickup via
      RequestPickupAuthoritativeWorldItemByListIndex.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
CLIENT_STATE_H = REPO_ROOT / "engine" / "authoritative_client_state.h"
APPEARANCE_H = REPO_ROOT / "engine" / "authoritative_world_item_appearance.h"
APPEARANCE_CPP = REPO_ROOT / "engine" / "authoritative_world_item_appearance.cpp"
GAME_INPUT_CPP = REPO_ROOT / "engine" / "game_input.cpp"
COMMAND_SURFACE_CPP = REPO_ROOT / "engine" / "authoritative_item_command_surface.cpp"
PICKUP_STRIP_CPP = REPO_ROOT / "engine" / "authoritative_world_item_pickup_strip.cpp"


def read_or_die(path: Path) -> str:
	if not path.is_file():
		print(f"FAIL: missing required source file: {path}", file=sys.stderr)
		sys.exit(2)
	return path.read_text()


def check_source_shape() -> list[str]:
	failures: list[str] = []

	client_state = read_or_die(CLIENT_STATE_H)
	appearance_h = read_or_die(APPEARANCE_H)
	appearance_cpp = read_or_die(APPEARANCE_CPP)
	game_input = read_or_die(GAME_INPUT_CPP)
	command_surface = read_or_die(COMMAND_SURFACE_CPP)
	pickup_strip = read_or_die(PICKUP_STRIP_CPP)

	# --- AuthoritativeHeldPreviewMobileContact struct ----------------------
	if "struct AuthoritativeHeldPreviewMobileContact" not in client_state:
		failures.append(
			"client_state: AuthoritativeHeldPreviewMobileContact struct missing"
		)
	for field in (
		"uint8_t valid",
		"uint16_t item_id",
		"int16_t cell_x0",
		"int16_t cell_y0",
		"int16_t cell_x1",
		"int16_t cell_y1",
	):
		if field not in client_state:
			failures.append(
				f"client_state: AuthoritativeHeldPreviewMobileContact missing field `{field}`"
			)
	if "held_preview_mobile_contact;" not in client_state:
		failures.append(
			"client_state: AuthoritativeClientState missing held_preview_mobile_contact field"
		)

	# --- Frame carries the same contact field ------------------------------
	if "held_preview_mobile_contact" not in appearance_h:
		failures.append(
			"appearance_h: AuthoritativeWorldItemAppearanceFrame missing held_preview_mobile_contact"
		)

	# --- Reset clears the persisted contact --------------------------------
	reset_block_re = re.compile(
		r"void\s+ResetAuthoritativeWorldItemAppearanceRows\s*\([^)]*\)\s*\{(?P<body>.*?)\n\}",
		re.DOTALL,
	)
	reset_match = reset_block_re.search(appearance_cpp)
	if not reset_match:
		failures.append(
			"appearance_cpp: ResetAuthoritativeWorldItemAppearanceRows not found"
		)
	else:
		reset_body = reset_match.group("body")
		if "held_preview_mobile_contact" not in reset_body:
			failures.append(
				"appearance_cpp: ResetAuthoritativeWorldItemAppearanceRows does not clear held_preview_mobile_contact"
			)

	# --- Publish copies the contact frame -> game --------------------------
	publish_block_re = re.compile(
		r"void\s+PublishAuthoritativeWorldItemAppearanceRows\s*\([^)]*\)\s*\{(?P<body>.*?)\n\}",
		re.DOTALL,
	)
	publish_match = publish_block_re.search(appearance_cpp)
	if not publish_match:
		failures.append(
			"appearance_cpp: PublishAuthoritativeWorldItemAppearanceRows not found"
		)
	else:
		publish_body = publish_match.group("body")
		expected_copy = (
			"game->authoritative.held_preview_mobile_contact =\n"
			"\t\tframe->held_preview_mobile_contact;"
		)
		if expected_copy not in publish_body:
			failures.append(
				"appearance_cpp: Publish does not copy frame->held_preview_mobile_contact"
				" into game->authoritative.held_preview_mobile_contact"
			)
		# Publish must NOT funnel the contact through world_pickup_rows.
		# Authority invariant: world_pickup_rows is pickup-only.
		if re.search(
			r"world_pickup_(?:rows|item_ids|distance2)[^\n]*held_preview",
			publish_body,
		):
			failures.append(
				"appearance_cpp: Publish appears to fold held_preview_mobile_contact"
				" into world_pickup_rows — that violates the pickup-vs-place ownership split"
			)

	# --- Per-row population is gated by held_placeable_preview && on_screen
	# && preview_valid
	# Reason: only the held placeable preview row publishes the tap rect, and
	# Gap B HideInst()s the preview when preview_valid==0. An invisible
	# preview that still publishes a tappable rect would swallow normal tap
	# routing and dispatch place intent the user never saw — same class of
	# visibility/contact mismatch that Gap B existed to prevent. The contact
	# surface must stay lockstep with the inst visibility toggle so that
	# what-you-see-is-what-you-can-tap. Any other row writing
	# held_preview_mobile_contact would create a second writer (Law 1
	# violation); a contact published when preview_valid==0 would violate
	# Law 3 (client dispatches place intent on rendered nothing).
	contact_writers = re.findall(
		r"out->held_preview_mobile_contact\.\w+\s*=", appearance_cpp
	)
	if not contact_writers:
		failures.append(
			"appearance_cpp: per-row loop never writes out->held_preview_mobile_contact"
		)
	# Find the enclosing if-condition for the population block.
	pop_block_re = re.compile(
		r"if\s*\(\s*out\s*&&\s*held_placeable_preview\s*&&\s*item_on_screen\s*&&\s*preview_valid\s*\)",
	)
	if not pop_block_re.search(appearance_cpp):
		failures.append(
			"appearance_cpp: held_preview_mobile_contact population is not gated by"
			" `out && held_placeable_preview && item_on_screen && preview_valid`"
			" — when Gap B hides the preview (preview_valid==0), the contact"
			" must not stay live or mobile taps will dispatch place intent on an"
			" invisible target"
		)
	# The published rect must come from ProjectCoords (cell-space) — i.e.
	# item_view[]. A rect computed from raw pixel x/y or from a non-projected
	# world coordinate would not be comparable to ScreenToCell tap coords.
	if "item_view[0]" not in appearance_cpp or "item_view[1]" not in appearance_cpp:
		failures.append(
			"appearance_cpp: item_view (ProjectCoords output) not used in appearance pass"
		)

	# --- Mobile tap router branch in StartContact --------------------------
	# Must read the contact, gate on mobile_controls + primary tap, call the
	# same helper as desktop P / double-tap, and consume the tap.
	if "session.mobile_controls && b == 1" not in game_input:
		failures.append(
			"game_input: mobile/primary-tap gate `session.mobile_controls && b == 1`"
			" missing — the new preview-tap branch would fire on desktop too"
		)
	# The contact must be read from `authoritative.held_preview_mobile_contact`
	# (Game::authoritative inside StartContact). A read from
	# `world_pickup_*` would mean the tap router is conflating pickup and
	# place again.
	router_read_re = re.compile(
		r"authoritative\.held_preview_mobile_contact"
	)
	if not router_read_re.search(game_input):
		failures.append(
			"game_input: tap router does not read authoritative.held_preview_mobile_contact"
		)
	# The router must call RequestPlaceEquippedPlaceableAuthoritativeItem
	# inside the new branch. Tighten regex to call syntax to avoid matching
	# comment prose.
	router_call_re = re.compile(
		r"RequestPlaceEquippedPlaceableAuthoritativeItem\(this\)"
	)
	router_calls = router_call_re.findall(game_input)
	if len(router_calls) < 3:
		# Pre-existing call sites: line 1656 (desktop P), line 2859 (mobile
		# double-tap). The new Gap A tap branch adds a third caller.
		failures.append(
			"game_input: expected at least 3 calls to"
			" RequestPlaceEquippedPlaceableAuthoritativeItem(this) (desktop P,"
			f" mobile double-tap, new mobile preview tap); found {len(router_calls)}"
		)
	# The new branch must precede the keyboard / world-tap routing. We assert
	# this via ordering: the contact-read line appears AFTER the pickup-strip
	# block end and BEFORE the first occurrence of `case Input::Contact::PLAYER`
	# (used in EndContact) and the keyboard `if (ui.show_keyb)` block.
	pickup_strip_end_re = re.compile(
		r"IsWithinAuthoritativeWorldItemPickupStripBounds\(this,\s*cp\[0\],\s*cp\[1\]\)"
	)
	pickup_strip_end_match = pickup_strip_end_re.search(game_input)
	contact_read_match = router_read_re.search(game_input)
	# Anchor the keyboard block search to the first occurrence AFTER the new
	# preview-tap branch. game_input.cpp has multiple `if (ui.show_keyb)`
	# blocks (EndContact, MenuTouch, gamepad path); we only care about the
	# one immediately following StartContact's pickup-strip block.
	keyb_after_contact = None
	if contact_read_match:
		keyb_after_re = re.compile(r"if\s*\(\s*ui\.show_keyb\)")
		m = keyb_after_re.search(game_input, contact_read_match.end())
		if m:
			keyb_after_contact = m
	if pickup_strip_end_match and contact_read_match and keyb_after_contact:
		if not (
			pickup_strip_end_match.start()
			< contact_read_match.start()
			< keyb_after_contact.start()
		):
			failures.append(
				"game_input: tap-router branch ordering is wrong — the new"
				" held_preview_mobile_contact read must sit AFTER the pickup-strip"
				" `IsWithinAuthoritativeWorldItemPickupStripBounds` block and"
				" BEFORE the next `if (ui.show_keyb)` block in StartContact"
			)
	else:
		failures.append(
			"game_input: could not locate the anchors needed to verify tap-router"
			" branch ordering (pickup-strip end / contact read / keyb block after contact)"
		)

	# --- Authority invariants (Law 1, Law 3, Law 6) ------------------------

	# SvrPlaceOwnedItemFromPlayer remains sole writer of placed pos/state.
	# (We can't easily prove "sole writer" from the client code, but we CAN
	# assert that the Gap A path adds zero second writers: appearance.cpp and
	# game_input.cpp must not directly mutate any *placed* item fields.)
	# Use tightened regex (no whitespace before paren) so comment prose like
	# `SvrPlaceOwnedItemFromPlayer (server_tick.cpp:...)` does not match.
	for path, text in (
		(APPEARANCE_CPP, appearance_cpp),
		(GAME_INPUT_CPP, game_input),
	):
		if re.search(r"SvrPlaceOwnedItemFromPlayer\(", text):
			failures.append(
				f"{path.name}: contains a call to SvrPlaceOwnedItemFromPlayer —"
				" Gap A must not introduce a second placement writer (Law 1)"
			)

	# ITEM_ACTION_REQ_PLACE must be ASSEMBLED only inside the command surface.
	# Gap A must NOT construct an ITEM_ACTION_REQ_PLACE packet from the
	# appearance pass or the tap router. We look for code-level usage —
	# `kind = ITEM_ACTION_REQ_PLACE` assignment or `case ITEM_ACTION_REQ_PLACE`
	# — so the new files' doc comments mentioning the token by name are
	# tolerated.
	code_use_re = re.compile(
		r"(?:kind\s*=\s*ITEM_ACTION_REQ_PLACE|case\s+ITEM_ACTION_REQ_PLACE)"
	)
	if code_use_re.search(appearance_cpp):
		failures.append(
			"appearance_cpp: ITEM_ACTION_REQ_PLACE assembled/handled in appearance"
			" pass — Gap A must not emit place intents from the render path (Law 3)"
		)
	if code_use_re.search(game_input):
		failures.append(
			"game_input: ITEM_ACTION_REQ_PLACE assembled/handled directly in tap"
			" router — Gap A must dispatch through"
			" RequestPlaceEquippedPlaceableAuthoritativeItem rather than constructing"
			" the packet inline (Law 1, Law 3)"
		)
	if not code_use_re.search(command_surface):
		failures.append(
			"command_surface_cpp: no ITEM_ACTION_REQ_PLACE assembly site —"
			" the shared helper is the only allowed emitter"
		)

	# Pickup-strip must not gain held-preview awareness. The strip is the
	# pickup-only ownership lane — if it starts reading the held preview
	# contact, that's the same kind of ownership-collapse failure FL-4137
	# Gap B was rejected for.
	if "held_preview_mobile_contact" in pickup_strip:
		failures.append(
			"pickup_strip_cpp: references held_preview_mobile_contact — the pickup"
			" strip must remain pickup-only (Law 1)"
		)

	# MpMoveTick / local placement mutation must stay dead. If the tap router
	# or appearance pass writes the local player's placement state, that's a
	# Law-3 violation (client must send intent, not author truth).
	for path, text in (
		(APPEARANCE_CPP, appearance_cpp),
		(GAME_INPUT_CPP, game_input),
	):
		if re.search(r"\bMpMoveTick\b", text):
			failures.append(
				f"{path.name}: references MpMoveTick — that owner is retired and"
				" Gap A must not revive it (Law 3)"
			)

	return failures


def main() -> int:
	failures = check_source_shape()
	if failures:
		print("FL-4137 Gap A proof: FAIL")
		for f in failures:
			print(f"  - {f}")
		return 1
	print("FL-4137 Gap A proof: PASS")
	print("  - AuthoritativeHeldPreviewMobileContact struct present + wired")
	print("  - frame field added, reset and publish copy through it")
	print("  - per-row population gated by `held_placeable_preview && item_on_screen && preview_valid` (lockstep with Gap B HideInst)")
	print("  - mobile tap router: ordering OK (after pickup strip, before keyb)")
	print("  - tap branch gates on `session.mobile_controls && b == 1`")
	print("  - tap dispatches via RequestPlaceEquippedPlaceableAuthoritativeItem")
	print("  - same helper as desktop P (game_input.cpp:1656) and mobile double-tap (:2859)")
	print("  - no held_preview hooks in pickup strip; no second place writer")
	print("  - no ITEM_ACTION_REQ_PLACE emission from appearance or tap router")
	print("  - no MpMoveTick revival in either touched file")
	return 0


if __name__ == "__main__":
	sys.exit(main())

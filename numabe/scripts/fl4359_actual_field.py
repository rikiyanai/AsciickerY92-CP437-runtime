"""Consumer for FL-4359's compositor final-paint field.

The compositor publishes one little-endian binary sidecar for each captured
frame.  The sidecar is a lossless transport rather than a
second cell renderer or an archive format.  Its bytes are twelve float32
values per actual shader pixel, in this order::

    fg.rgb, native_intensity, bg.rgb, effective_coverage,
    final.rgb, actor_alpha

``native_intensity`` is the intact native mask observation.  The paint pin is
``mix(bg.rgb, fg.rgb, effective_coverage)``; the native intensity must not be
used as a replacement for that effective coverage.  ``fg``/``bg`` and both
coverage fields are production-paint inputs.  ``final.rgb`` is the compositor
colour after an optional debug-view recolor and immediately before imageStore.
It is not a later viewport, tilt-shift, UI, or quantised image.  The composition
flags identify the debug stage, so a recolored viewport cannot be mislabeled as
unaltered production paint.

The producer places a ``final_paint_field`` object in each jitter frame trace.
Schema v2 retains that 48-byte float prefix and appends twelve uint32 words
for front/underlay source references and composition, making a 96-byte record.

This module reads that object and its one sidecar path.  It does not create a
bundle, write archive files, replay atlas samples, infer glyphs, or manufacture
a mode receipt.  A legacy cell-pair raster remains a diagnostic input
elsewhere.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Mapping

import numpy as np

from native_source_content_reader import (
    NativeSourceContentError,
    NativeSourceContentReader,
    validate_native_source_content_join,
)


ACTUAL_FIELD_SCHEMA = "fl4359.final_paint_field.v1"
TRACE_FIELD_SCHEMA = "fl4359.final_paint_field.v2"
TRACE_KEYS = ("front_sources", "under_sources", "composition")
ADMISSION_REASONS = {
    1: "admitted",
    2: "unclassified_refusal",
    3: "resolve_or_native_sample_invalid",
    4: "resolve_abi_invalid",
    5: "resolve_owner_unmapped",
    6: "resolve_colour_nonfinite",
    7: "captured_texel_paper_missing",
    8: "native_span_layout_refused",
    9: "view_not_perspective",
    10: "resolve_contract_disabled",
    11: "resolve_contract_abi_invalid",
    12: "resolve_contract_record_bytes_invalid",
    13: "cell_outside_resolve_contract",
    14: "native_sample_extent_mismatch",
}
RECORD_BYTES = 48
RECORD_FLOATS = 12
RAW_DTYPE = np.dtype("<f4")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SOURCE_GENERATION_SCHEMA = "fl4359.source_capture_generation.v2"
SOURCE_GENERATION_SKY_FLAG = np.uint32(0x80000000)
SOURCE_GENERATION_VALUE_MASK = np.uint32(0x7FFFFFFF)
FIELD_KEYS = (
    "fg_rgb",
    "native_intensity",
    "bg_rgb",
    "effective_coverage",
    "final_rgb",
    "actor_alpha",
)
RGB_KEYS = ("fg_rgb", "bg_rgb", "final_rgb")
COVERAGE_KEYS = ("native_intensity", "effective_coverage", "actor_alpha")
MODES = {
    "FF": ("flat", "flat"),
    "FC": ("flat", "continuous"),
    "CF": ("continuous", "flat"),
    "CC": ("continuous", "continuous"),
}


class ActualFieldError(ValueError):
    """Raised when a final-paint sidecar cannot be trusted."""


def sha256_file(path: Path) -> str:
    """Hash a sidecar without making a second full-size byte allocation."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ActualFieldError(f"{label} must be a non-empty string")
    return value.strip()


def _sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise ActualFieldError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _int_value(value: Any, label: str, *, positive: bool = False,
               nonnegative: bool = False) -> int:
    # JSON integer dimensions are the contract.  Integral floats are accepted
    # for receipts produced by a numeric host, but bools and fractions are not.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ActualFieldError(f"{label} must be integral")
    number = float(value)
    if not math.isfinite(number) or not number.is_integer():
        raise ActualFieldError(f"{label} must be integral")
    result = int(number)
    if positive and result <= 0:
        raise ActualFieldError(f"{label} must be positive")
    if nonnegative and result < 0:
        raise ActualFieldError(f"{label} must be non-negative")
    return result


def _pair(value: Any, label: str, *, positive: bool = False,
          nonnegative: bool = False) -> tuple[int, int]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ActualFieldError(f"{label} must be a two-element pair")
    return (
        _int_value(value[0], f"{label}[0]", positive=positive,
                   nonnegative=nonnegative),
        _int_value(value[1], f"{label}[1]", positive=positive,
                   nonnegative=nonnegative),
    )


def _unit_field(value: np.ndarray, label: str) -> None:
    if not np.isfinite(value).all():
        raise ActualFieldError(f"{label} contains non-finite values")
    if np.any(value < 0.0) or np.any(value > 1.0):
        raise ActualFieldError(f"{label} must be in [0, 1]")


def _resolve_sidecar(trace: Mapping[str, Any], capture_path: Path) -> tuple[dict[str, Any], Path]:
    field = trace.get("final_paint_field")
    if not isinstance(field, Mapping):
        raise ActualFieldError("frame trace has no final_paint_field object")
    metadata = dict(field)
    if "capture_sha256" in metadata:
        raise ActualFieldError(
            "final_paint_field must not contain capture_sha256; hash the JSONL in its post-run manifest")
    raw_path = _string(metadata.get("raw_path"), "final_paint_field.raw_path")
    path = Path(raw_path)
    if not path.is_absolute():
        path = capture_path.parent / path
    return metadata, path.resolve()


def _load_source_generation_sidecars(metadata: Mapping[str, Any], capture_path: Path,
                                      *, required: bool) -> dict[str, np.ndarray]:
    """Load exact front/underlay source-generation banks for one field."""
    value = metadata.get("source_capture_generation_sidecars")
    if value is None or value == {}:
        if required:
            raise ActualFieldError("perspective v2 field has no source-generation sidecars")
        return {}
    if not isinstance(value, Mapping) or value.get("schema") != SOURCE_GENERATION_SCHEMA:
        raise ActualFieldError("source-generation sidecar schema is missing or unsupported")
    # Sidecars are request-scoped witnesses. A valid bank from another deferred
    # request must never satisfy this field's source attribution.
    for key, metadata_key in (
        ("capture_frame_index", "frame_index"),
        ("capture_request_id", "request_id"),
        ("capture_sequence_frame", "sequence_frame"),
    ):
        if key not in value:
            raise ActualFieldError(f"source-generation envelope lacks {key}")
        if _int_value(value.get(key), f"source_generation_sidecars.{key}", nonnegative=True) != _int_value(
                metadata.get(metadata_key), f"final_paint_field.{metadata_key}", nonnegative=True):
            raise ActualFieldError(
                f"source-generation {key} disagrees with final_paint_field.{metadata_key}"
            )
    if _int_value(value.get("snapshot_frame_index"),
                  "source_generation_sidecars.snapshot_frame_index", nonnegative=True) != _int_value(
                      metadata.get("frame_index"), "final_paint_field.frame_index", nonnegative=True):
        raise ActualFieldError("source-generation snapshot was queued for a different frame")
    if value.get("snapshot_frame_join_valid") is not True:
        raise ActualFieldError("source-generation snapshot did not prove its enqueue-frame join")
    if value.get("snapshot_timeline") != "same_frame_post_image_store_before_next_frame_async_transfer":
        raise ActualFieldError("source-generation snapshot timeline is not same-frame")
    probe_size = _int_value(value.get("probe_size"), "source_generation_sidecars.probe_size", positive=True)
    layer_count = _int_value(value.get("layer_count"), "source_generation_sidecars.layer_count", positive=True)
    if layer_count != 18:
        raise ActualFieldError("source-generation layer_count must cover all 18 capture layers")
    capture_state = metadata.get("source_capture_state", {})
    if not isinstance(capture_state, Mapping):
        raise ActualFieldError("source_capture_state must be an object for source attribution")
    if _int_value(capture_state.get("probe_size"), "source_capture_state.probe_size", positive=True) != probe_size:
        raise ActualFieldError("source-generation probe_size disagrees with source_capture_state")
    state_bands = capture_state.get("capture_bands")
    sidecar_bands = value.get("capture_bands")
    if not isinstance(state_bands, list) or state_bands != sidecar_bands:
        raise ActualFieldError("source-generation capture_bands disagree with source_capture_state")
    if len(state_bands) != 3 or sorted(state_bands) != [0, 1, 2]:
        raise ActualFieldError("source-generation capture_bands are not a physical permutation")
    expected_bytes = probe_size * probe_size * layer_count * 4
    maps: dict[str, np.ndarray] = {}
    for bank in ("front", "underlay"):
        descriptor = value.get(bank)
        if not isinstance(descriptor, Mapping) or descriptor.get("status") != "valid":
            raise ActualFieldError(f"source-generation {bank} bank is unavailable")
        if descriptor.get("schema") != SOURCE_GENERATION_SCHEMA or descriptor.get("bank") != bank:
            raise ActualFieldError(f"source-generation {bank} descriptor disagrees with its bank")
        if descriptor.get("word_format") != "little_endian_u32":
            raise ActualFieldError(f"source-generation {bank} word format is unsupported")
        if descriptor.get("encoding") != {
            "zero": "missing",
            "low31": "captured_geometry_source_revision_w",
            "high_bit": "generated_sky_paint_generation",
        }:
            raise ActualFieldError(f"source-generation {bank} encoding is unsupported")
        raw_path = _string(descriptor.get("raw_path"), f"source_generation_sidecars.{bank}.raw_path")
        path = Path(raw_path)
        if not path.is_absolute():
            path = capture_path.parent / path
        path = path.resolve()
        if _int_value(descriptor.get("probe_size"), f"source_generation_sidecars.{bank}.probe_size", positive=True) != probe_size:
            raise ActualFieldError(f"source-generation {bank} probe size disagrees with envelope")
        if _int_value(descriptor.get("layer_count"), f"source_generation_sidecars.{bank}.layer_count", positive=True) != layer_count:
            raise ActualFieldError(f"source-generation {bank} layer count disagrees with envelope")
        if _int_value(descriptor.get("bytes"), f"source_generation_sidecars.{bank}.bytes", nonnegative=True) != expected_bytes:
            raise ActualFieldError(f"source-generation {bank} byte count disagrees with dimensions")
        if not path.is_file() or path.stat().st_size != expected_bytes:
            raise ActualFieldError(f"source-generation {bank} sidecar is missing, truncated, or oversized")
        digest = _sha(descriptor.get("raw_sha256"), f"source_generation_sidecars.{bank}.raw_sha256")
        if sha256_file(path) != digest:
            raise ActualFieldError(f"source-generation {bank} SHA-256 mismatch")
        maps[bank] = np.memmap(path, dtype="<u4", mode="r",
                               shape=(layer_count, probe_size, probe_size))
    return maps


def _validate_policy(value: Any) -> tuple[dict[str, Any], bool]:
    if value == "unavailable":
        return {"status": "unavailable"}, False
    if not isinstance(value, Mapping):
        raise ActualFieldError("final_paint_field.applied_policy must be an object or 'unavailable'")
    policy = dict(value)
    status = policy.get("status", "applied")
    if status == "unavailable":
        return {**policy, "status": "unavailable"}, False
    if status != "applied":
        raise ActualFieldError("final_paint_field.applied_policy.status must be applied or unavailable")
    mode = policy.get("mode")
    if mode not in MODES:
        raise ActualFieldError("applied presentation policy mode must be FF, FC, CF, or CC")
    fg_sampling, bg_sampling = MODES[mode]
    if policy.get("fg_sampling") != fg_sampling or policy.get("bg_sampling") != bg_sampling:
        raise ActualFieldError("applied policy sampling does not match its mode")
    # FL-4359: missing domain is historical v1, not evidence of texel BG.
    domain = policy.get("bg_domain")
    if domain is not None:
        expected = ("projected_texel" if policy.get("projection") == "perspective"
                    else "screen_cell" if bg_sampling == "flat" else "native_sample")
        if domain != expected or policy.get("projection") not in {"perspective", "orthographic"}:
            raise ActualFieldError("applied policy BG domain contradicts projection/sampling")
        transport = "depth_winning_splat" if domain == "projected_texel" else "product"
        if policy.get("bg_transport") != transport:
            raise ActualFieldError("applied policy BG transport contradicts domain")
    _string(policy.get("family"), "final_paint_field.applied_policy.family")
    _string(policy.get("owner"), "final_paint_field.applied_policy.owner")
    _string(policy.get("bg_owner"), "final_paint_field.applied_policy.bg_owner")
    _sha(policy.get("sha256"), "final_paint_field.applied_policy.sha256")
    return policy, True


def _eligibility(value: Any, label: str) -> bool:
    """Return source/atlas eligibility after checking producer identities.

    An available status is not a magic pass bit. Source availability carries
    the existing build identity and dirty-diff receipt; atlas availability
    carries the actual logical source paths and their hashes. If the producer
    cannot provide those facts it should say unavailable and the observation
    stays useful while remaining ineligible for a presentation comparison.
    """
    if value is None:
        return False
    if value == "unavailable":
        return False
    if not isinstance(value, Mapping):
        raise ActualFieldError(f"{label} must be an object or 'unavailable'")
    status = value.get("status")
    if status == "unavailable":
        return False
    if status == "available":
        if label.endswith(".source"):
            _string(value.get("build_id"), f"{label}.build_id")
            _sha(value.get("dirty_diff_sha256"), f"{label}.dirty_diff_sha256")
            return True
        sources = value.get("sources")
        if not isinstance(sources, list) or not sources:
            raise ActualFieldError(f"{label}.sources must be a non-empty list")
        seen: set[str] = set()
        for index, source in enumerate(sources):
            if not isinstance(source, Mapping):
                raise ActualFieldError(f"{label}.sources[{index}] must be an object")
            logical_path = _string(source.get("path"), f"{label}.sources[{index}].path")
            if logical_path in seen:
                raise ActualFieldError(f"{label}.sources contains duplicate paths")
            seen.add(logical_path)
            _sha(source.get("sha256"), f"{label}.sources[{index}].sha256")
        return True
    raise ActualFieldError(f"{label}.status must be available or unavailable")


@dataclass(frozen=True)
class ActualFieldFrame:
    """One validated native-resolution field and its producer metadata."""

    path: Path
    metadata: dict[str, Any]
    fields: Mapping[str, Any]
    field_size_px: tuple[int, int]
    logical_target_size: tuple[int, int]
    presentation_size: tuple[int, int]
    cell_px: tuple[int, int]
    grid_cells: tuple[int, int]
    capture_id: str
    actual_frame_index: int
    sequence_frame: int
    frame_id: int
    request_id: Any
    profile: str
    projection: str
    policy: dict[str, Any]
    source_eligible: bool
    atlas_eligible: bool
    composite_eligible: bool
    postprocess_trace: Any = None
    source_generation_maps: dict[str, np.ndarray] = field(default_factory=dict)
    native_source_reader: NativeSourceContentReader | None = field(
        default=None, repr=False, compare=False
    )

    def postprocess_sources(self, x: int, y: int) -> dict[str, Any]:
        if not 0 <= x < self.width or not 0 <= y < self.height:
            raise ActualFieldError("postprocess pixel outside native image")
        if self.postprocess_trace is None:
            raise ActualFieldError("postprocess operator sidecar unavailable")
        return self.postprocess_trace.source_contributors(x, y)

    def replay_postprocess_exact(
        self,
        *,
        chunk_rows: int = 32,
        store_contract: Mapping | None = None,
        runtime_identity: Mapping | None = None,
    ):
        """Replay post-stores only when the producer supplied a verified contract.

        Operator/source reads remain available through ``postprocess_sources``.
        A missing or unsupported store contract raises before any replay iterator
        is returned, so callers cannot silently substitute host RNE quantization.
        """
        if self.postprocess_trace is None:
            raise ActualFieldError("postprocess operator sidecar unavailable")
        from fl4359_postprocess_trace import PostprocessTraceError
        try:
            return self.postprocess_trace.replay_final_rgb(
                self.fields["final_rgb"],
                chunk_rows=chunk_rows,
                store_contract=store_contract,
                runtime_identity=runtime_identity,
            )
        except PostprocessTraceError as exc:
            # Keep the public field-reader error boundary stable while retaining
            # the exact failure reason from the operator reader.
            raise ActualFieldError(str(exc)) from exc

    @property
    def width(self) -> int:
        return self.field_size_px[0]

    @property
    def height(self) -> int:
        return self.field_size_px[1]

    @property
    def actual_field_observation(self) -> bool:
        return True

    def pixel_provenance(self, x: int, y: int) -> dict[str, Any]:
        """Join the paint owner's exact channel choices to the captured layer.

        This observes the compositor boundary. Later blur/presentation remains
        a separate observation, as declared by the frame metadata.
        """
        if not (0 <= x < self.width and 0 <= y < self.height):
            raise ActualFieldError("pixel outside final-paint field")
        if "front_sources" not in self.fields:
            raise ActualFieldError("historical v1 field has no per-pixel source references")
        capture = self.metadata.get("source_capture_state", {})
        extent = int(capture.get("probe_size", 0))
        generation_maps = self.source_generation_maps
        publications = capture.get("captured_publication_sha256_by_layer", [])

        def source(reference: int, bank: str) -> dict[str, Any]:
            result: dict[str, Any] = {"reference": reference, "bank": bank}
            if reference == 0:
                return {**result, "present": False}
            if self.projection == "ortho":
                index = reference - 1
                if index >= self.grid_cells[0] * self.grid_cells[1] * 4:
                    raise ActualFieldError("orthographic source outside product bank")
                return {**result, "present": True, "product_index": index,
                        "cell_index": index // 4, "quarter": index % 4}
            address = reference & 0x0fffffff
            if extent <= 0 or address == 0 or bank not in generation_maps:
                raise ActualFieldError("perspective source has no matching source-generation bank")
            index = address - 1
            layer, local = divmod(index, extent * extent)
            generation_map = generation_maps[bank]
            if layer >= generation_map.shape[0]:
                raise ActualFieldError("perspective source layer exceeds source-generation bank")
            token = int(generation_map[layer, local // extent, local % extent])
            if token == 0:
                raise ActualFieldError("perspective source has no exact captured generation")
            sky = bool(token & int(SOURCE_GENERATION_SKY_FLAG))
            generation = token & int(SOURCE_GENERATION_VALUE_MASK)
            if generation <= 0:
                raise ActualFieldError("perspective source has an invalid typed generation token")
            return {**result, "present": True, "layer": layer,
                    "texel": [local % extent, local // extent],
                    "capture_generation": generation,
                    "capture_generation_kind": "generated_sky" if sky else "captured_geometry",
                    "geometry_capture": not sky,
                    "logical_probe": (reference >> 28) & 3,
                    "publication": publications[layer] if layer < len(publications) else None}

        roles = ("glyph", "foreground", "background", "visibility")
        composition = self.fields["composition"][y, x]
        flags = int(composition[3])
        admission_code = flags & 15
        cell_x = x // self.cell_px[0]
        cell_y = y // self.cell_px[1]
        front_continuation = bool(flags & (1 << 15))
        under_continuation = bool(flags & (1 << 7))
        return {
            "pixel": [x, y], "frame_index": self.actual_frame_index,
            "front": {role: source(int(ref), "front") for role, ref in
                      zip(roles, self.fields["front_sources"][y, x])},
            "underlay": {role: source(int(ref), "underlay") for role, ref in
                         zip(roles, self.fields["under_sources"][y, x])},
            "glyph": int(composition[0]), "underlay_glyph": int(composition[1]),
            "underlay_intensity": float(composition[2].view(RAW_DTYPE)),
            "underlay_used": bool(flags & (1 << 8)),
            "admission_code": admission_code,
            "admission_reason": ADMISSION_REASONS.get(
                admission_code, "unknown_refusal"),
            "presentation_mode": (flags >> 4) & 3,
            "debug_view": (flags >> 10) & 15,
            "front_span": {
                "origin_cell": [
                    cell_x - 1 if front_continuation else cell_x, cell_y],
                "columns": 2 if flags & (1 << 14) else 1,
                "continuation": front_continuation,
            },
            "underlay_span": {
                "origin_cell": [
                    cell_x - 1 if under_continuation else cell_x, cell_y],
                "columns": 2 if flags & (1 << 6) else 1,
                "continuation": under_continuation,
            },
            "actor_row": (flags >> 16) - 1 if flags & (1 << 9) else None,
            "coverage": float(self.fields["effective_coverage"][y, x]),
            "final_rgb": self.fields["final_rgb"][y, x].tolist(),
            "capture_boundary": self.metadata["capture_boundary"],
        }

    def displayed_result_explanation(self, x: int, y: int) -> dict[str, Any]:
        """Join the captured source, channel owners, glyph, and final pixel.

        This is measurement history, not a rendering history buffer. References
        come from the compositor record, source facts from the immutable native
        sidecar, and final RGB from the same invocation immediately before
        imageStore. Missing source content stays explicit.
        """
        provenance = self.pixel_provenance(x, y)
        composition = self.fields["composition"][y, x]
        front_refs = [int(value) for value in self.fields["front_sources"][y, x]]
        under_refs = [int(value) for value in self.fields["under_sources"][y, x]]

        def facts(reference: int, bank: int) -> dict[str, Any]:
            if reference == 0:
                return {"status": "absent", "reference": 0, "bank": bank}
            if self.native_source_reader is None:
                return {
                    "status": "unavailable",
                    "reference": reference,
                    "bank": bank,
                    "reason": "native_source_content_sidecar_unavailable",
                }
            return self.native_source_reader.source_content(reference, bank)

        front_facts = {
            role: facts(reference, 0)
            for role, reference in zip(
                ("glyph", "flat_ink", "native_background", "visibility"),
                front_refs,
            )
        }
        under_facts = {
            role: facts(reference, 1)
            for role, reference in zip(
                ("glyph", "flat_ink", "native_background", "visibility"),
                under_refs,
            )
        }
        base_payload = front_facts["glyph"].get("payload")
        base_glyph = (
            int(base_payload[0]) & 0xFFF
            if isinstance(base_payload, (list, tuple)) and len(base_payload) == 4
            else None
        )
        flags = int(composition[3])
        admission_code = flags & 15
        return {
            "schema": "fl4359.displayed_result_explanation.v1",
            "pixel": [x, y],
            "frame_index": self.actual_frame_index,
            "capture": {
                "front": front_facts,
                "underlay": under_facts,
            },
            "native_visibility": {
                "front_source": front_refs[3],
                "underlay_source": under_refs[3],
                "admission_code": admission_code,
                "admission_reason": ADMISSION_REASONS.get(
                    admission_code, "unknown_refusal"),
            },
            "cell_election": {
                "glyph_source": front_refs[0],
                "base_captured_glyph": base_glyph,
                "displayed_glyph": int(composition[0]),
                "underlay_glyph": int(composition[1]),
            },
            "span_channel_authority": {
                "front_span": provenance["front_span"],
                "underlay_span": provenance["underlay_span"],
                "glyph_source": front_refs[0],
                "flat_ink_source": front_refs[1],
                "native_background_source": front_refs[2],
                "underlay_used": bool(flags & (1 << 8)),
                "background_and_deeper_layer_domain": "native_pixel",
                "valid": admission_code != 8,
                "refusal": (
                    None if admission_code != 8 else
                    "native_span_layout_refused"),
            },
            "native_paint": {
                "production_fg": self.fields["fg_rgb"][y, x].tolist(),
                "production_bg": self.fields["bg_rgb"][y, x].tolist(),
                "native_intensity": float(
                    self.fields["native_intensity"][y, x]),
                "effective_coverage": float(
                    self.fields["effective_coverage"][y, x]),
                "debug_view": (flags >> 10) & 15,
                "actual_compositor_rgb":
                    self.fields["final_rgb"][y, x].tolist(),
            },
            "postprocess": {
                "observed": self.postprocess_trace is not None,
                "capture_boundary": provenance["capture_boundary"],
            },
        }

    def compare_displayed_result(
        self, previous: "ActualFieldFrame", x: int, y: int,
    ) -> dict[str, Any]:
        """Name the first changed owner between two measured display records.

        This compares immutable observations after both frames completed. It
        does not retain, vote, or feed history back into rendering.
        """
        before = previous.displayed_result_explanation(x, y)
        after = self.displayed_result_explanation(x, y)
        stages = (
            ("capture_or_native_visibility", (
                before["capture"], before["native_visibility"],
            ), (
                after["capture"], after["native_visibility"],
            )),
            ("cell_election", before["cell_election"], after["cell_election"]),
            ("span_channel_authority", before["span_channel_authority"],
             after["span_channel_authority"]),
            ("production_paint", {
                key: before["native_paint"][key] for key in (
                    "production_fg", "production_bg", "native_intensity",
                    "effective_coverage")
            }, {
                key: after["native_paint"][key] for key in (
                    "production_fg", "production_bg", "native_intensity",
                    "effective_coverage")
            }),
            ("debug_view", before["native_paint"]["debug_view"],
             after["native_paint"]["debug_view"]),
            ("compositor_output", before["native_paint"]["actual_compositor_rgb"],
             after["native_paint"]["actual_compositor_rgb"]),
            ("postprocess_observation", before["postprocess"], after["postprocess"]),
        )
        changes = [
            {"stage": name, "before": old, "after": new}
            for name, old, new in stages if old != new
        ]
        return {
            "schema": "fl4359.displayed_result_change.v1",
            "pixel": [x, y],
            "previous_frame_index": previous.actual_frame_index,
            "frame_index": self.actual_frame_index,
            "changed": bool(changes),
            "first_divergence": changes[0]["stage"] if changes else "unchanged",
            "changes": changes,
        }

    @property
    def presentation_mode_eligible(self) -> bool:
        return bool(self.policy.get("status") == "applied"
                    and self.source_eligible and self.atlas_eligible
                    and self.composite_eligible)

    def as_dict(self) -> dict[str, Any]:
        """Expose fields at the seam used by the GIF compositor."""
        return {
            **self.fields,
            "frame_id": self.frame_id,
            "actual_frame_index": self.actual_frame_index,
            "sequence_frame": self.sequence_frame,
            "capture_id": self.capture_id,
            "field_size_px": self.field_size_px,
            "logical_target_size": self.logical_target_size,
            "presentation_size": self.presentation_size,
            "cell_px": self.cell_px,
            "grid_cells": self.grid_cells,
            "actual_field_observation": True,
            "presentation_mode_eligible": self.presentation_mode_eligible,
            "source_eligible": self.source_eligible,
            "atlas_eligible": self.atlas_eligible,
            "composite_eligible": self.composite_eligible,
            "metadata": self.metadata,
        }


    def source_content(self, reference: int, bank: int) -> dict[str, Any]:
        """Join a native reference to same-frame immutable source facts.

        Legacy raw fields stay readable through ``pixel_provenance``.  A
        canonical armed perspective frame cannot answer this attribution from
        sparse rows or a later frame, so missing native content is an error.
        """
        if self.native_source_reader is None:
            if self.projection == "perspective" and bool(
                self.metadata.get("native_source_content_requested", False)
            ):
                raise ActualFieldError(
                    "canonical perspective native source content is unavailable"
                )
            return {
                "status": "unavailable",
                "reference": int(reference),
                "bank": int(bank),
                "reason": "legacy_raw_field_has_no_native_source_attribution",
            }
        return self.native_source_reader.source_content(reference, bank)

    def sky_source_context(self) -> dict[str, Any]:
        """Return the typed-sky inputs frozen at this frame's paint seam."""
        if self.native_source_reader is None:
            raise ActualFieldError("native source content sky context unavailable")
        try:
            return self.native_source_reader.sky_context()
        except NativeSourceContentError as error:
            raise ActualFieldError(f"native source content sky context invalid: {error}") from error

class ActualFieldReader:
    """Read per-frame sidecars while enforcing one capture identity."""

    def __init__(self, capture_path: Path):
        self.capture_path = capture_path.resolve()
        self.capture_id: str | None = None
        self.profile: str | None = None
        self.projection: str | None = None
        self.field_size_px: tuple[int, int] | None = None
        self.logical_target_size: tuple[int, int] | None = None
        self.presentation_size: tuple[int, int] | None = None
        self.cell_px: tuple[int, int] | None = None
        self.identity: dict[str, Any] | None = None
        self._native_source_readers: list[NativeSourceContentReader] = []

    def _load_native_source_content(
        self, trace: Mapping[str, Any], metadata: Mapping[str, Any], *,
        projection: str, actual_index: int, sequence_frame: int,
        request_id: int,
    ) -> NativeSourceContentReader | None:
        native_value = trace.get("native_source_content")
        native = native_value if isinstance(native_value, Mapping) else None
        requested_value = metadata.get("native_source_content_requested", False)
        if not isinstance(requested_value, bool):
            raise ActualFieldError(
                "final_paint_field.native_source_content_requested must be boolean"
            )
        requested = requested_value
        if not requested:
            return None
        if projection != "perspective":
            raise ActualFieldError(
                "native_source_content_requested is only valid for perspective"
            )
        if native is None or native.get("status") != "valid":
            raise ActualFieldError(
                "canonical perspective native_source_content receipt is missing or invalid"
            )
        if native.get("input_schema") not in (None, TRACE_FIELD_SCHEMA):
            raise ActualFieldError("legacy NativeSourceCapture input schema is unsupported")
        capture = metadata.get("source_capture_state", {})
        capture_probe = (
            _int_value(capture.get("probe_size"), "source_capture_state.probe_size", positive=True)
            if isinstance(capture, Mapping) and "probe_size" in capture else 0
        )
        probe_size = _int_value(native.get("probe_size"), "native_source_content.probe_size", positive=True)
        if capture_probe > 0 and probe_size != capture_probe:
            raise ActualFieldError(
                "native_source_content probe_size disagrees with perspective probe receipt"
            )
        capture_layers = (
            _int_value(capture.get("layer_count"), "source_capture_state.layer_count", positive=True)
            if isinstance(capture, Mapping) and "layer_count" in capture else 0
        )
        native_layers = _int_value(native.get("layer_count"), "native_source_content.layer_count", positive=True)
        if capture_layers > 0 and native_layers != capture_layers:
            raise ActualFieldError(
                "native_source_content layer_count disagrees with perspective probe receipt"
            )
        try:
            validate_native_source_content_join(
                native,
                frame_index=actual_index,
                sequence_frame=sequence_frame,
                request_id=request_id,
                probe_size=probe_size,
            )
            raw_path = _string(native.get("raw_path"), "native_source_content.raw_path")
            path = Path(raw_path)
            if not path.is_absolute():
                path = self.capture_path.parent / path
            reader = NativeSourceContentReader(path.resolve(), native)
        except (NativeSourceContentError, OSError) as error:
            raise ActualFieldError(
                f"native_source_content receipt/sidecar invalid: {error}"
            ) from error
        self._native_source_readers.append(reader)
        return reader


    def _observed_profile(self, trace: Mapping[str, Any]) -> str:
        # FL-4359: reuse the canonical runner's strict runtime/profile contract.
        # A requested label alone never establishes actual atlas or grid identity.
        receipt_path = self.capture_path.parent / "receipt.json"
        if not receipt_path.is_file():
            return "unavailable"
        receipt = json.loads(receipt_path.read_text())
        profile_id = receipt.get("runner_presentation_profile_id")
        spec = receipt.get("runner_presentation_profile_spec")
        if not profile_id or not spec:
            return "unavailable"
        from argparse import Namespace
        from run_fl4359_headed_movement import _presentation_profile_receipt_contract
        gate = _presentation_profile_receipt_contract(
            {"frame_trace": dict(trace)},
            Namespace(presentation_profile=profile_id, presentation_profile_manifest=spec))
        if not gate["pass"]:
            raise ActualFieldError("recorded presentation profile mismatch: " + str(gate.get("failed_checks", gate.get("reason"))))
        return profile_id

    def read(self, trace: Mapping[str, Any], *, frame_id: int,
              sequence_index: int) -> ActualFieldFrame:
        metadata, path = _resolve_sidecar(trace, self.capture_path)
        trace_sources = metadata.get("schema") == TRACE_FIELD_SCHEMA
        if metadata.get("schema") not in (ACTUAL_FIELD_SCHEMA, TRACE_FIELD_SCHEMA):
            raise ActualFieldError(
                f"unsupported final_paint_field schema {metadata.get('schema')!r}")
        if not path.is_file():
            raise ActualFieldError(f"final-paint sidecar missing: {path}")

        if metadata.get("status") != "valid":
            raise ActualFieldError("final_paint_field.status must be valid")
        expected_frame_id = _int_value(frame_id, "JSONL frame_id", nonnegative=True)
        sequence_frame = _int_value(metadata.get("sequence_frame"),
                                    "final_paint_field.sequence_frame", nonnegative=True)
        if sequence_frame != expected_frame_id:
            raise ActualFieldError("final_paint_field.sequence_frame disagrees with JSONL frame_id")
        actual_index = _int_value(metadata.get("frame_index"),
                                  "final_paint_field.frame_index", nonnegative=True)
        if actual_index != _int_value(trace.get("frame_index"),
                                      "frame_trace.frame_index", nonnegative=True):
            raise ActualFieldError("final_paint_field.frame_index disagrees with frame_trace.frame_index")
        request_id = _int_value(metadata.get("request_id"),
                                "final_paint_field.request_id", nonnegative=True)
        if request_id != _int_value(trace.get("request_id"),
                                    "frame_trace.request_id", nonnegative=True):
            raise ActualFieldError("final_paint_field.request_id disagrees with frame trace")
        if "sequence_frame" in trace and trace["sequence_frame"] != sequence_frame:
            raise ActualFieldError("frame_trace.sequence_frame disagrees with JSONL frame_id")
        # sequence_index is a consumer ordinal, not an engine or capture frame.
        _int_value(sequence_index, "sequence_index", nonnegative=True)
        capture_id = _string(metadata.get("capture_id"), "final_paint_field.capture_id")
        recorder = trace.get("recorder_identity")
        if isinstance(recorder, Mapping):
            recorder_path = _string(recorder.get("capture_path"), "recorder_identity.capture_path")
            recorder_pid = _int_value(recorder.get("pid"), "recorder_identity.pid", positive=True)
            if capture_id != f"{recorder_path}#pid={recorder_pid}":
                raise ActualFieldError("final-paint capture_id disagrees with frame recorder_identity")
        kind = _int_value(metadata.get("projection_kind"), "projection_kind")
        if kind not in (0, 1):
            raise ActualFieldError("projection_kind must be VSB 0=ortho or 1=perspective")
        projection = "perspective" if kind == 1 else "ortho"
        if _int_value(metadata.get("projection_mode"), "projection_mode") != kind + 1:
            raise ActualFieldError("PixelCamera projection_mode disagrees with projection_kind")
        if _int_value(metadata.get("camera_projection_mode"), "camera_projection_mode") != 1 - kind:
            raise ActualFieldError("Camera3D projection mode disagrees with projection_kind")
        # Historical v16/v2 raw colours and masks remain readable when a legacy
        # field has no sidecars. Perspective source attribution fails closed
        # until a new capture supplies both exact banks.
        source_generation_maps = _load_source_generation_sidecars(
            metadata, self.capture_path, required=False)
        # FL-4359: admit the recorded label only after actual runtime validation.
        profile = self._observed_profile(trace)
        field_size = _pair(metadata.get("actual_image_size"), "actual_image_size", positive=True)
        logical_size = _pair(metadata.get("logical_target_size"), "logical_target_size", positive=True)
        presentation_size = _pair(metadata.get("presentation_size"), "presentation_size", positive=True)
        cell_px = _pair(metadata.get("cell_size"), "cell_size", positive=True)
        grid = _pair(metadata.get("semantic_ansi_size"), "semantic_ansi_size", positive=True)
        for key in ("cell_size", "semantic_ansi_size", "projection_mode", "camera_projection_mode"):
            if key in trace and trace[key] != metadata[key]:
                raise ActualFieldError(f"final-paint {key} disagrees with frame trace")
        if logical_size != (grid[0] * cell_px[0], grid[1] * cell_px[1]):
            raise ActualFieldError("logical_target_size must equal semantic_ansi_size times cell_size")
        if grid != tuple((presentation_size[i] + cell_px[i] - 1) // cell_px[i] for i in (0, 1)):
            raise ActualFieldError("semantic_ansi_size does not cover the presentation's partial edge cells")
        if any(field_size[i] > logical_size[i] for i in (0, 1)):
            raise ActualFieldError("actual_image_size cannot exceed logical_target_size")
        if projection == "perspective" and field_size != presentation_size:
            raise ActualFieldError("perspective actual_image_size must equal presentation_size")
        if projection == "ortho" and field_size not in (presentation_size, logical_size):
            raise ActualFieldError("ortho actual_image_size must identify presentation or logical target")
        for previous, current, label in (
            (self.capture_id, capture_id, "capture_id"),
            (self.profile, profile, "presentation_profile"),
            (self.projection, projection, "projection"),
            (self.field_size_px, field_size, "actual_image_size"),
            (self.logical_target_size, logical_size, "logical_target_size"),
            (self.presentation_size, presentation_size, "presentation_size"),
            (self.cell_px, cell_px, "cell_size"),
        ):
            if previous is not None and previous != current:
                raise ActualFieldError(f"final-paint sidecars mix {label} values")
        scalar_format = "float32_uint32" if trace_sources else "float32"
        if metadata.get("byte_order") != "little_endian" or metadata.get("scalar_format") != scalar_format:
            raise ActualFieldError("final-paint scalar encoding disagrees with its schema")
        layout = FIELD_KEYS + TRACE_KEYS if trace_sources else FIELD_KEYS
        if metadata.get("record_layout") != list(layout):
            raise ActualFieldError("final-paint record_layout does not match its ABI")
        if metadata.get("capture_boundary") != "pre_imageStore_after_compositor_actor_blend":
            raise ActualFieldError("unsupported final-paint capture_boundary")
        if metadata.get("post_imageStore_display_observed") is not False:
            raise ActualFieldError("pre-imageStore observer cannot claim post-imageStore display observation")
        if not isinstance(metadata.get("late_overlay_observed"), bool):
            raise ActualFieldError("late_overlay_observed must be boolean")
        provenance = metadata.get("provenance")
        if not isinstance(provenance, Mapping) or provenance.get("paint_owner") != "ascii_compositor_same_dispatch":
            raise ActualFieldError("same-dispatch compositor provenance is required")
        if provenance.get("cell_source_join") != "fl4431_final_cell_capture_same_frame_request":
            raise ActualFieldError("same-frame request cell source join is required")

        record_count = field_size[0] * field_size[1]
        if _int_value(metadata.get("record_count"), "record_count", positive=True) != record_count:
            raise ActualFieldError("record_count disagrees with actual_image_size")
        stride = 96 if trace_sources else RECORD_BYTES
        expected_bytes = record_count * stride
        byte_count = _int_value(metadata.get("bytes"), "final_paint_field.bytes", nonnegative=True)
        record_bytes = _int_value(metadata.get("record_bytes"),
                                  "final_paint_field.record_bytes", positive=True)
        if record_bytes != stride or byte_count != expected_bytes:
            raise ActualFieldError("final-paint sidecar byte count does not match field dimensions")
        if path.stat().st_size != expected_bytes:
            raise ActualFieldError("final-paint sidecar is missing, truncated, or has extra bytes")
        expected_sha = _sha(metadata.get("raw_sha256"), "final_paint_field.raw_sha256")
        if sha256_file(path) != expected_sha:
            raise ActualFieldError("final-paint sidecar SHA-256 mismatch")
        record_words = stride // 4
        raw = np.fromfile(path, dtype=RAW_DTYPE, count=record_count * record_words)
        if raw.size != record_count * record_words:
            raise ActualFieldError("final-paint sidecar decoded record count is short")
        records = raw.reshape((field_size[1], field_size[0], record_words))
        fields: Mapping[str, Any] = {
            "fg_rgb": records[..., 0:3],
            "native_intensity": records[..., 3],
            "bg_rgb": records[..., 4:7],
            "effective_coverage": records[..., 7],
            "final_rgb": records[..., 8:11],
            "actor_alpha": records[..., 11],
        }
        for key in RGB_KEYS + COVERAGE_KEYS:
            _unit_field(fields[key], key)
        if trace_sources:
            words = records.view(np.dtype("<u4"))
            for index, key in enumerate(TRACE_KEYS):
                fields[key] = words[..., 12 + index * 4:16 + index * 4]
            _unit_field(fields["composition"][..., 2].view(RAW_DTYPE), "underlay_intensity")

        policy, policy_shape_ok = _validate_policy(metadata.get("applied_policy"))
        composite_ok = metadata["late_overlay_observed"]
        source_ok = _eligibility(provenance.get("source"), "final_paint_field.source")
        atlas_ok = _eligibility(provenance.get("atlas"), "final_paint_field.atlas")
        identity = {"policy": policy, "provenance": dict(provenance)}
        if self.identity is not None and identity != self.identity:
            raise ActualFieldError("final-paint sidecars mix applied policy or provenance")

        self.identity = identity
        self.capture_id = capture_id
        self.profile = profile
        self.projection = projection
        self.field_size_px = field_size
        self.logical_target_size = logical_size
        self.presentation_size = presentation_size
        self.cell_px = cell_px
        metadata = {
            **metadata,
            "sha256": expected_sha,
            "bytes": byte_count,
            "record_bytes": record_bytes,
            "input_schema": metadata.get("schema"),
            "fields_materialization": "eager_v2",
            "field_size_px": list(field_size),
            "logical_target_size": list(logical_size),
            "presentation_size": list(presentation_size),
            "cell_px": list(cell_px),
            "grid_cells": list(grid),
            "policy_eligible": policy_shape_ok,
            "composite_eligible": composite_ok,
            "source_eligible": source_ok,
            "atlas_eligible": atlas_ok,
        }
        postprocess_trace = None
        postprocess_receipt = trace.get("postprocess_trace")
        if isinstance(postprocess_receipt, Mapping) and postprocess_receipt.get("status") != "not_requested":
            from fl4359_postprocess_trace import PostprocessTrace, PostprocessTraceError
            try:
                postprocess_trace = PostprocessTrace(
                    dict(postprocess_receipt), self.capture_path.parent,
                    frame_index=actual_index, request_id=metadata["request_id"],
                    sequence_frame=sequence_frame,
                )
            except PostprocessTraceError as exc:
                raise ActualFieldError(str(exc)) from exc
        native_source_reader = self._load_native_source_content(
            trace,
            metadata,
            projection=projection,
            actual_index=actual_index,
            sequence_frame=sequence_frame,
            request_id=request_id,
        )
        metadata["native_source_content_observed"] = native_source_reader is not None
        return ActualFieldFrame(
            path=path,
            metadata=metadata,
            fields=fields,
            field_size_px=field_size,
            logical_target_size=logical_size,
            presentation_size=presentation_size,
            cell_px=cell_px,
            grid_cells=grid,
            capture_id=capture_id,
            actual_frame_index=actual_index,
            sequence_frame=sequence_frame,
            frame_id=expected_frame_id,
            request_id=metadata["request_id"],
            profile=profile,
            projection=projection,
            policy=policy,
            source_eligible=source_ok,
            atlas_eligible=atlas_ok,
            composite_eligible=composite_ok,
            postprocess_trace=postprocess_trace,
            source_generation_maps=source_generation_maps,
            native_source_reader=native_source_reader,
        )


    def close(self) -> None:
        for reader in self._native_source_readers:
            reader.close()
        self._native_source_readers.clear()


def load_actual_field_frame(trace: Mapping[str, Any], capture_path: Path, *,
                            frame_id: int, sequence_index: int,
                            reader: ActualFieldReader | None = None) -> ActualFieldFrame:
    """Load one producer sidecar from its enclosing JSONL frame trace."""
    active_reader = reader if reader is not None else ActualFieldReader(capture_path)
    return active_reader.read(trace, frame_id=frame_id, sequence_index=sequence_index)


def actual_field_absent_status(trace: Mapping[str, Any]) -> dict[str, Any]:
    """Receipt shape for a legacy capture that has no sidecar."""
    return {
        "status": "absent",
        "schema": ACTUAL_FIELD_SCHEMA,
        "actual_field_observation": False,
        "presentation_mode_eligible": False,
        "legacy_pair_diagnostic_only": True,
        "reason": "frame trace has no final_paint_field sidecar",
    }


__all__ = [
    "ACTUAL_FIELD_SCHEMA",
    "ActualFieldError",
    "ActualFieldFrame",
    "ActualFieldReader",
    "actual_field_absent_status",
    "load_actual_field_frame",
    "sha256_file",
]

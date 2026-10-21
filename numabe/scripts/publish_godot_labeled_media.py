#!/usr/bin/env python3
"""FL-4359: build labeled viewing copies from manifests, never replace raw evidence."""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from fl4359_stitch_sweep_gif import _write_gif

from godot_proof_publication import (
    PublicationError, apply_finder_tags, deterministic_artifact_name,
    load_manifest, publish_artifacts, sha256_file, validate_manifest,
)


def resolution(value):
    if isinstance(value, list):
        return "+".join(resolution(item) for item in value)
    if isinstance(value, dict):
        return f"{value['width']}x{value['height']}"
    return str(value)


def _text(metadata, *keys, default="UNKNOWN"):
    """Read a presentation fact without consulting the current checkout.

    Capture manifests use a few historical spellings for proof and route
    identity.  The publisher may display an explicit UNKNOWN for an older
    diagnostic capture, but it never fills a missing value from git HEAD or
    another host-side source.
    """
    for key in keys:
        value = metadata.get(key)
        if value is not None and str(value).strip():
            return str(value)
    return default


def _mode(metadata):
    value = _text(metadata, "presentation_mode", "mode", "mode_name")
    return "FGONLY" if value.upper() in {"FGONLY", "F10FGONLY"} else value


def label_lines(metadata, role, index, count, size):
    proof = _text(metadata, "proof_name", "proof_id", "proof", "fl_id")
    route = _text(metadata, "route_id", "route", "route_key")
    projection = _text(metadata, "projection", "projection_name")
    view = _text(metadata, "f10_view_name", "view_name", "view")
    comparison = _text(metadata, "comparison_role", "before_after", default=str(role).upper())
    mode = _mode(metadata)
    source_commit = _text(metadata, "source_commit")
    tree = _text(metadata, "effective_tree_fingerprint", "tree_fingerprint")
    return [
        f"PROOF={proof} ROUTE={route} | FG={_text(metadata, 'foreground_mode')} | BG={_text(metadata, 'background_mode')} | MODE={mode} | {comparison} | frame {index + 1}/{count}",
        f"ARCH={_text(metadata, 'architecture')} | RUN={_text(metadata, 'run_id')} | VIEW={view} | F10={view} | PROJECTION={projection}",
        f"CAPTURE STATE={_text(metadata, 'capture_source_state')} | SRC={source_commit[:12]} | TREE={tree[:12]}",
        f"CELL={resolution(metadata.get('cell_resolution', 'UNKNOWN'))} | GLYPHSRC={resolution(metadata.get('glyph_source_resolution', 'UNKNOWN'))} | SOURCE IMG={size[0]}x{size[1]}",
        f"PROVENANCE={_text(metadata, 'provenance_status')} | DERIVED VIEWING COPY",
    ]


def label_frame(frame, lines):
    """Keep every source pixel at 1:1 below a separate, measured text band."""
    font = ImageFont.load_default(size=32 if frame.width >= 1600 else 14)
    measure = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    wrapped = []
    for line in lines:
        current = ""
        for word in line.split():
            candidate = f"{current} {word}".strip()
            if measure.textlength(candidate, font=font) > frame.width - 24:
                if current:
                    wrapped.append(current)
                current = word
                if measure.textlength(word, font=font) > frame.width - 24:
                    raise PublicationError("label token exceeds image width")
            else:
                current = candidate
        wrapped.append(current)
    line_height = font.getbbox("Ag")[3] + 10
    band = 20 + line_height * len(wrapped)
    result = Image.new("RGB", (frame.width, frame.height + band), (20, 20, 20))
    result.paste(frame.convert("RGB"), (0, band))
    draw = ImageDraw.Draw(result)
    for index, line in enumerate(wrapped):
        draw.text((12, 10 + index * line_height), line, font=font, fill=(255, 255, 255))
    return result, band


def _fit_label_width(frame, lines):
    """Pad only the right edge when a tiny diagnostic frame cannot fit a token."""
    font = ImageFont.load_default(size=32 if frame.width >= 1600 else 14)
    measure = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    widest = max((measure.textlength(token, font=font) for line in lines for token in line.split()), default=0)
    width = max(frame.width, int(widest) + 26)
    if width == frame.width:
        return frame
    padded = Image.new("RGB", (width, frame.height), (0, 0, 0))
    padded.paste(frame.convert("RGB"), (0, 0))
    return padded


def _capture_metadata(metadata):
    result = copy.deepcopy(metadata)
    for key in ("storage", "immutable_sources", "filename_note"):
        result.pop(key, None)
    return result


def publish_labeled_media(manifest_paths, output, *, tags=True, grid=False):
    output = Path(output).resolve()
    if output.exists() and any(output.iterdir()):
        raise PublicationError("presentation output must be new or empty; preserve previous receipts")
    inputs = []
    for path in manifest_paths:
        path = Path(path).resolve()
        errors = validate_manifest(path, allow_incomplete=True)
        if errors:
            raise PublicationError(f"invalid source publication {path}: {errors}")
        inputs.append((path, load_manifest(path)))
    output.mkdir(parents=True, exist_ok=True)
    records, receipts, native = [], [], {}
    for source_manifest, manifest in inputs:
        root = (source_manifest.parent / manifest.get("artifact_root", ".")).resolve()
        for artifact in manifest["artifacts"]:
            if not artifact["visual"]:
                continue
            source = root / artifact["path"]
            # FL-4895: an F9 MOV carries its title band from the encode
            # (scripts/recording/f9_finalize_bundle.py:encode_mov); a
            # second, re-encoded labeled copy is not made here.
            if source.suffix.lower() not in {".png", ".gif"}:
                raise PublicationError(f"unsupported labeled media: {source}")
            metadata = _capture_metadata(artifact["capture_metadata"])
            frames, durations, labels, bands = [], [], [], []
            with Image.open(source) as image:
                for index in range(artifact["frame_count"]):
                    image.seek(index)
                    lines = label_lines(metadata, artifact["role"], index, artifact["frame_count"], image.size)
                    frame, band = label_frame(image, lines)
                    frames.append(frame)
                    bands.append(band)
                    labels.append(lines)
                    durations.append(image.info.get("duration", 60))
            if len({frame.size for frame in frames}) != 1:
                raise PublicationError("label layout changed dimensions across frames")
            draft = {"role": artifact["role"], "frame_count": len(frames),
                     "extension": source.suffix.lower(),
                     "image_dimensions": {"width": frames[0].width, "height": frames[0].height}}
            target = output / deterministic_artifact_name(metadata, draft)
            if target.exists():
                raise PublicationError(f"presentation collision: {target.name}")
            if source.suffix.lower() == ".gif":
                _write_gif(frames, target, durations)
            else:
                frames[0].save(target)
            records.append({**draft, "path": target, "metadata": metadata})
            receipts.append({"output": target.name, "sha256": sha256_file(target),
                             "source_manifest": str(source_manifest),
                             "source_manifest_sha256": sha256_file(source_manifest),
                             "source": str(source), "source_sha256": artifact["sha256"],
                             "labels": labels, "label_band_heights": bands,
                             "durations_ms": durations, "source_scale": 1,
                             "gif_indexed_colour_reencoding": source.suffix.lower() == ".gif"})
            if artifact["role"] == "native" and grid:
                mode = metadata["foreground_mode"][0] + metadata["background_mode"][0]
                if mode in native:
                    raise PublicationError(f"duplicate grid mode: {mode}")
                native[mode] = (target, metadata)
            if sha256_file(source) != artifact["sha256"]:
                raise PublicationError(f"source bytes changed: {source}")
    if grid:
        if set(native) != {"FF", "FC", "CF", "CC"}:
            raise PublicationError("native 2x2 requires exactly FF, FC, CF, CC")
        panes = []
        for mode in ("FF", "FC", "CF", "CC"):
            path, metadata = native[mode]
            panes.append({**metadata, "pane_id": mode})
        metadata = {**panes[0], "run_id": "NATIVE-2X2", "foreground_mode": "MIXED",
                    "background_mode": "MIXED", "source_commit": "MIXED",
                    "capture_source_state": "MIXED", "effective_tree_fingerprint": "UNKNOWN",
                    "panes": panes}
        frames = []
        for index in range(5):
            images = []
            for mode in ("FF", "FC", "CF", "CC"):
                with Image.open(native[mode][0]) as image:
                    if image.n_frames != 5:
                        raise PublicationError("native grid requires five frames per pane")
                    image.seek(index)
                    images.append(image.convert("RGB"))
            if len({image.size for image in images}) != 1:
                raise PublicationError("native grid pane sizes differ")
            width, height = images[0].size
            combined = Image.new("RGB", (width * 2, height * 2))
            for pane, image in enumerate(images):
                combined.paste(image, ((pane % 2) * width, (pane // 2) * height))
            frames.append(combined)
        draft = {"role": "native-grid", "frame_count": 5, "extension": ".gif",
                 "image_dimensions": {"width": frames[0].width, "height": frames[0].height}}
        target = output / deterministic_artifact_name(metadata, draft)
        _write_gif(frames, target, 60)
        records.append({**draft, "path": target, "metadata": metadata})
        receipts.append({"output": target.name, "sha256": sha256_file(target),
                         "pane_order": ["FF", "FC", "CF", "CC"],
                         "sources": [str(native[mode][0]) for mode in ("FF", "FC", "CF", "CC")],
                         "source_scale": 1, "timing": "60ms viewing cadence; independent runs, not simultaneous captures"})
    receipt = output / "presentation_receipt.json"
    receipt.write_text(json.dumps({"schema": "godot-labeled-presentation.v1", "artifacts": receipts,
                                   "renderer_acceptance": "NOT_EVALUATED"}, indent=2) + "\n")
    metadata = _capture_metadata(inputs[0][1]["metadata"])
    metadata["storage"] = {"status": "TRACKED", "location": ".",
                           "receipt_path": receipt.name, "receipt_sha256": sha256_file(receipt),
                           "failure_log": "docs/FAILURE_LOG.md#" + metadata["fl_id"],
                           "godot_goal": "docs/plans/GODOT_GOAL.MD"}
    manifest_path = output / "godot_proof_publication.json"
    publish_artifacts(manifest_path, metadata, records, artifact_root=output)
    errors = validate_manifest(manifest_path, allow_incomplete=True)
    if errors:
        raise PublicationError(str(errors))
    if tags:
        apply_finder_tags(manifest_path)
    return manifest_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", action="append", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--native-grid", action="store_true")
    parser.add_argument("--no-tags", action="store_true")
    args = parser.parse_args()
    print(publish_labeled_media(args.manifest, args.output, tags=not args.no_tags, grid=args.native_grid))


if __name__ == "__main__":
    main()

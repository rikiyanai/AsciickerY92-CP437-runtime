#!/usr/bin/env python3
"""compile_glyph_manifest.py — FL-4131 manifest + atlas tooling.

Gates closed by this script:
  compile_glyph_manifest_check_wired  — --check validates a manifest against the schema
  rfc8785_sha256_helper_exists        — rfc8785_canonical() + sha256_manifest() are importable
  bake_extended_glyph_atlas_font      — --compile rasterizes the admitted Unicode scalars
                                        through a TTF font at every supported_cell_sizes
                                        entry and emits one page per size with a hash binding.

Usage:
  python3 scripts/compile_glyph_manifest.py --check [MANIFEST]
      Validate one manifest file (or all fixtures if omitted) against
      assets/glyphs/schema/glyph_manifest.schema.json.
      Exits 0 on success, 1 on any validation failure.

  python3 scripts/compile_glyph_manifest.py --hash MANIFEST
      Print the RFC8785 canonical JSON + SHA-256 hex digest of MANIFEST.

  python3 scripts/compile_glyph_manifest.py --compile MANIFEST --out OUT_DIR \\
        [--font PATH] [--sizes 4,6,8,...] [--write-pngs] [--write-manifest]
      Compile a manifest into atlas-of-atlases + per-size pages + LUT.
      The manifest must declare unicode_scalar for every entry it wants
      rasterized; entries without unicode_scalar are emitted as blank cells.
      --font defaults to assets/fonts/Roboto-Medium.ttf.
      --sizes overrides manifest.supported_cell_sizes (CSV of integers).
      --write-pngs also emits a sibling .png per page (helpful for debugging).
      --write-manifest updates MANIFEST in place with font_id/font_sha256/
       supported_cell_sizes so the manifest_hash flows through downstream
       generators (compile_actor_visual_profiles.py, ASCIIID, JoinV2).

RFC8785 canonicalization contract (gate rfc8785_sha256_helper_exists):
  The canonical form is RFC8785 (JCS — JSON Canonicalization Scheme):
    - UTF-8 encoded
    - No insignificant whitespace
    - Object keys sorted lexicographically by Unicode codepoint
    - Numbers serialized as IEEE 754 double without trailing zeros
  This implementation uses Python's built-in json module with sort_keys=True
  and separators=(',', ':'), which is correct for manifests that contain only
  integer/string/array/object types (no floats needing IEEE 754 edge-case handling).
  If floating-point UVs are added in Phase 1, replace with a proper RFC8785 library.
"""

import argparse
import hashlib
import json
import os
import sys

# ── Path resolution ──────────────────────────────────────────────────────────

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCHEMA_PATH = os.path.join(_REPO_ROOT, "assets", "glyphs", "schema", "glyph_manifest.schema.json")
_FIXTURES_DIR = os.path.join(_REPO_ROOT, "assets", "glyphs", "fixtures")

# ── RFC8785 canonical JSON + SHA-256 ─────────────────────────────────────────

def _normalize_numbers(obj):
    """Recursively convert whole-number floats to ints for canonical parity.

    Python json.dumps preserves float repr (1.0) while C's %.17g prints 1.
    To ensure hash parity, we normalize floats that are whole numbers to ints.
    """
    if isinstance(obj, float):
        if obj.is_integer():
            return int(obj)
        return obj
    if isinstance(obj, list):
        return [_normalize_numbers(v) for v in obj]
    if isinstance(obj, dict):
        return {k: _normalize_numbers(v) for k, v in obj.items()}
    return obj


def rfc8785_canonical(obj) -> bytes:
    """Return RFC8785 canonical JSON bytes for a JSON-compatible Python object.

    Contract (gate rfc8785_sha256_helper_exists):
      - UTF-8 encoded
      - No insignificant whitespace (separators=(',',':'))
      - Object keys sorted lexicographically by Unicode codepoint (sort_keys=True)
      - Sufficient for manifests with integer/string/bool/null/array/object types
      - Floating-point precision: Python's json module serializes floats using
        repr(), which is correct for normal values. Phase 1 must audit if UV
        floats are added.
    """
    return json.dumps(_normalize_numbers(obj), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sha256_manifest(obj) -> str:
    """Return lowercase hex SHA-256 of the RFC8785 canonical form of obj."""
    canonical = rfc8785_canonical(obj)
    return hashlib.sha256(canonical).hexdigest()


# ── Schema validation ─────────────────────────────────────────────────────────

def _load_schema():
    try:
        with open(_SCHEMA_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"[ERROR] Schema not found: {_SCHEMA_PATH}", file=sys.stderr)
        print("[ERROR] Run from repo root or ensure assets/glyphs/schema/ exists.", file=sys.stderr)
        sys.exit(1)


def _validate_manifest(manifest: dict, path: str, schema: dict) -> list:
    """Validate a loaded manifest dict against FL-4131 Phase 0 rules.

    Returns a list of error strings. Empty list = valid.

    Note: We implement the FL-4131-specific semantic rules directly rather than
    pulling in a full JSON Schema library dependency. The schema file is the
    canonical structural spec; this validator enforces the semantic rules that
    cannot be expressed purely in JSON Schema (sentinel rejection, CP437 boundary,
    admission set coherence, fallback_glyph_id coverage, etc.).
    """
    errors = []

    # ── Top-level required fields ──
    for field in ("manifest_version", "profile_kind", "content_pack_id", "fallback_glyph_id", "entries"):
        if field not in manifest:
            errors.append(f"missing required field: {field}")

    if errors:
        return errors

    # ── manifest_version ──
    if manifest["manifest_version"] != 1:
        errors.append(f"manifest_version must be 1, got {manifest['manifest_version']!r}")

    # ── profile_kind ──
    if manifest["profile_kind"] != "extended_glyph_v1":
        errors.append(f"profile_kind must be 'extended_glyph_v1', got {manifest['profile_kind']!r}")

    # ── content_pack_id ──
    cpid = manifest["content_pack_id"]
    if not isinstance(cpid, str) or not cpid or len(cpid) > 128:
        errors.append(f"content_pack_id must be a non-empty string <= 128 chars")

    # ── fallback_glyph_id sentinel check ──
    GLYPH_ID_NONE      = 0xFFFFFFFF
    GLYPH_ID_UNRESOLVED = 0xFFFFFFFE
    fallback = manifest["fallback_glyph_id"]
    if not isinstance(fallback, int) or fallback < 0:
        errors.append(f"fallback_glyph_id must be a non-negative integer")
    elif fallback == GLYPH_ID_NONE:
        errors.append(f"fallback_glyph_id must not be GLYPH_ID_NONE (0xFFFFFFFF)")
    elif fallback == GLYPH_ID_UNRESOLVED:
        errors.append(f"fallback_glyph_id must not be GLYPH_ID_UNRESOLVED (0xFFFFFFFE)")

    # ── entries ──
    entries = manifest["entries"]
    if not isinstance(entries, list) or len(entries) == 0:
        errors.append("entries must be a non-empty array")
        return errors

    entry_ids = set()
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            errors.append(f"entries[{i}] must be an object")
            continue
        for field in ("glyph_id", "label", "coverage_quadrants"):
            if field not in entry:
                errors.append(f"entries[{i}] missing required field: {field}")
        if "coverage_quadrants" in entry:
            cq = entry["coverage_quadrants"]
            if not isinstance(cq, int) or cq < 0 or cq > 65535:
                errors.append(f"entries[{i}].coverage_quadrants must be an integer 0-65535, got {cq!r}")
        if "glyph_id" not in entry:
            continue

        gid = entry["glyph_id"]
        if not isinstance(gid, int):
            errors.append(f"entries[{i}].glyph_id must be an integer")
            continue
        if gid <= 255:
            errors.append(f"entries[{i}].glyph_id={gid} is in CP437 range (0-255); CP437 glyphs must not appear in extended manifest entries")
        if gid == GLYPH_ID_NONE:
            errors.append(f"entries[{i}].glyph_id is GLYPH_ID_NONE sentinel (0xFFFFFFFF)")
        if gid == GLYPH_ID_UNRESOLVED:
            errors.append(f"entries[{i}].glyph_id is GLYPH_ID_UNRESOLVED sentinel (0xFFFFFFFE)")
        if gid in entry_ids:
            errors.append(f"entries[{i}].glyph_id={gid} is a duplicate")
        entry_ids.add(gid)

    # ── fallback_glyph_id must exist in entries ──
    if isinstance(fallback, int) and fallback not in (GLYPH_ID_NONE, GLYPH_ID_UNRESOLVED):
        if fallback not in entry_ids:
            errors.append(f"fallback_glyph_id={fallback} is not declared in entries")

    # ── admission_set coherence ──
    if "admission_set" in manifest:
        admission = manifest["admission_set"]
        if not isinstance(admission, list):
            errors.append("admission_set must be an array")
        else:
            for j, aid in enumerate(admission):
                if not isinstance(aid, int) or aid < 256:
                    errors.append(f"admission_set[{j}]={aid!r} must be an integer >= 256")
                elif aid == GLYPH_ID_NONE or aid == GLYPH_ID_UNRESOLVED:
                    errors.append(f"admission_set[{j}]={aid} is a sentinel and cannot be admitted")
        # fallback must be inside admission_set when admission_set exists
        if isinstance(fallback, int) and fallback not in (GLYPH_ID_NONE, GLYPH_ID_UNRESOLVED):
            if isinstance(admission, list) and fallback not in admission:
                errors.append(f"fallback_glyph_id={fallback} is not in admission_set; renderable fallback must be admitted")

    # ── Phase 1+ pipeline coherence ──
    # If the manifest declares atlas requirements (supported_cell_sizes / font),
    # the corresponding fields must all be present and consistent.
    has_sizes = "supported_cell_sizes" in manifest
    has_font_id = "font_id" in manifest
    has_font_sha = "font_sha256" in manifest
    if has_sizes or has_font_id or has_font_sha:
        if not has_sizes:
            errors.append("supported_cell_sizes is required when font_id/font_sha256 is declared")
        if not has_font_id:
            errors.append("font_id is required when supported_cell_sizes is declared")
        if not has_font_sha:
            errors.append("font_sha256 is required when supported_cell_sizes is declared")
        if has_sizes:
            sizes = manifest["supported_cell_sizes"]
            if not isinstance(sizes, list) or len(sizes) == 0:
                errors.append("supported_cell_sizes must be a non-empty array")
            else:
                seen = set()
                for k, sz in enumerate(sizes):
                    if not isinstance(sz, int) or sz < 1 or sz > 128:
                        errors.append(f"supported_cell_sizes[{k}]={sz!r} must be an integer in 1..128")
                    elif sz in seen:
                        errors.append(f"supported_cell_sizes[{k}]={sz} duplicated")
                    seen.add(sz)
        if has_font_sha:
            fs = manifest["font_sha256"]
            if not isinstance(fs, str) or len(fs) != 64 or any(c not in "0123456789abcdef" for c in fs):
                errors.append("font_sha256 must be 64 lowercase hex chars")

    return errors


# ── Phase 1+ pipeline-artifact integrity ──────────────────────────────────────

def _validate_pipeline_artifacts(manifest_path: str, manifest: dict) -> list:
    """When a manifest declares supported_cell_sizes, verify the on-disk AOA,
    LUT, and per-size pages exist and that each page_hash matches the actual
    RGBA8 byte digest. Returns list of error strings.

    Looks for artifacts under assets/glyphs/atlases/<content_pack_id>.*.json
    relative to repo root.
    """
    if "supported_cell_sizes" not in manifest:
        return []
    errors = []
    sizes = list(manifest.get("supported_cell_sizes") or [])
    content_pack_id = manifest["content_pack_id"]
    atlases_dir = os.path.join(_REPO_ROOT, "assets", "glyphs", "atlases")
    aoa_path = os.path.join(atlases_dir, f"{content_pack_id}.atlas_of_atlases.json")
    lut_path = os.path.join(atlases_dir, f"{content_pack_id}.lut_rgba8.json")
    if not os.path.isfile(aoa_path):
        errors.append(f"AOA missing: {os.path.relpath(aoa_path, _REPO_ROOT)} — run --compile to bake")
        return errors
    if not os.path.isfile(lut_path):
        errors.append(f"LUT missing: {os.path.relpath(lut_path, _REPO_ROOT)}")
    try:
        with open(aoa_path, "r", encoding="utf-8") as f:
            aoa = json.load(f)
    except json.JSONDecodeError as e:
        errors.append(f"AOA JSON parse error: {e}")
        return errors

    expected_hash = sha256_manifest(manifest)
    if aoa.get("manifest_hash") != expected_hash:
        errors.append(
            f"AOA manifest_hash {aoa.get('manifest_hash')!r} != current manifest hash {expected_hash!r}; re-run --compile"
        )
    if aoa.get("content_pack_id") != content_pack_id:
        errors.append(f"AOA content_pack_id {aoa.get('content_pack_id')!r} != manifest content_pack_id {content_pack_id!r}")
    if manifest.get("font_id") and aoa.get("font_id") != manifest["font_id"]:
        errors.append("AOA font_id does not echo manifest.font_id")
    if manifest.get("font_sha256") and aoa.get("font_sha256") != manifest["font_sha256"]:
        errors.append("AOA font_sha256 does not echo manifest.font_sha256")
    if aoa.get("fallback_glyph_id") != manifest["fallback_glyph_id"]:
        errors.append("AOA fallback_glyph_id does not echo manifest.fallback_glyph_id")
    if not aoa.get("lut_hash"):
        errors.append("AOA missing lut_hash (Phase 1+ requirement)")

    fallback = manifest.get("fallback_glyph_id")
    entry_ids = {int(e["glyph_id"]) for e in manifest.get("entries", []) if "glyph_id" in e}
    if fallback not in entry_ids:
        errors.append(f"fallback_glyph_id={fallback} is not present in entries (cannot be rendered)")

    pages_by_size = {int(p.get("cell_px", -1)): p for p in aoa.get("pages", []) if "cell_px" in p}
    missing_sizes = [sz for sz in sizes if sz not in pages_by_size]
    if missing_sizes:
        errors.append(f"AOA missing pages for declared sizes: {missing_sizes}")

    for sz in sizes:
        meta = pages_by_size.get(sz)
        if not meta:
            continue
        url = meta.get("url") or ""
        page_path = os.path.join(atlases_dir, url) if url else ""
        if not page_path or not os.path.isfile(page_path):
            errors.append(f"page{sz} file missing: {url}")
            continue
        try:
            with open(page_path, "r", encoding="utf-8") as f:
                page = json.load(f)
        except json.JSONDecodeError as e:
            errors.append(f"page{sz} JSON parse error: {e}")
            continue
        rgba8 = page.get("rgba8")
        if not isinstance(rgba8, list):
            errors.append(f"page{sz} rgba8 must be an array")
            continue
        actual = hashlib.sha256(bytes(rgba8)).hexdigest()
        if meta.get("page_hash") != actual:
            errors.append(f"page{sz} page_hash in AOA does not match rgba8 byte digest")
        if page.get("page_hash") and page.get("page_hash") != actual:
            errors.append(f"page{sz} embedded page_hash does not match its own rgba8 bytes")
        if meta.get("width_px") != page.get("width"):
            errors.append(f"page{sz} width mismatch: AOA {meta.get('width_px')} vs file {page.get('width')}")
        if meta.get("height_px") != page.get("height"):
            errors.append(f"page{sz} height mismatch: AOA {meta.get('height_px')} vs file {page.get('height')}")

    return errors


# ── Commands ─────────────────────────────────────────────────────────────────

def cmd_check(paths: list) -> int:
    """Validate manifest files. Returns exit code."""
    schema = _load_schema()
    if not paths:
        # FL-4131: `.glyph_profile.json` files are per-sprite sidecars
        # (engine/glyph_sidecar.h schema), not manifests. They live next to
        # their .xp asset and must be excluded from manifest validation.
        paths = [
            os.path.join(_FIXTURES_DIR, f)
            for f in sorted(os.listdir(_FIXTURES_DIR))
            if f.endswith(".json")
            and not f.startswith("_")
            and not f.endswith(".glyph_profile.json")
        ]
        if not paths:
            print("[WARN] No manifest files found in assets/glyphs/fixtures/")
            return 0

    ok = True
    for path in paths:
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            print(f"[FAIL] {path}: file not found")
            ok = False
            continue
        except json.JSONDecodeError as e:
            print(f"[FAIL] {path}: JSON parse error: {e}")
            ok = False
            continue

        # Skip non-manifest files (sidecars, examples, etc. that lack manifest_version)
        if "manifest_version" not in data:
            continue

        errors = _validate_manifest(data, path, schema)
        if not errors:
            errors = _validate_pipeline_artifacts(path, data)
        if errors:
            print(f"[FAIL] {path}:")
            for err in errors:
                print(f"  - {err}")
            ok = False
        else:
            digest = sha256_manifest(data)
            sizes_note = ""
            if "supported_cell_sizes" in data:
                sizes_note = f"  sizes={data['supported_cell_sizes']}"
            print(f"[OK]   {path}  sha256={digest[:16]}...{sizes_note}")

    return 0 if ok else 1


def cmd_hash(path: str) -> int:
    """Print RFC8785+SHA-256 of a manifest. Returns exit code."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        print(f"[ERROR] File not found: {path}", file=sys.stderr)
        return 1
    except json.JSONDecodeError as e:
        print(f"[ERROR] JSON parse error in {path}: {e}", file=sys.stderr)
        return 1
    digest = sha256_manifest(data)
    print(digest)
    return 0


DEFAULT_FONT_PATH = os.path.join(_REPO_ROOT, "assets", "fonts", "Roboto-Medium.ttf")
DEFAULT_CELL_SIZES = [4, 6, 8, 10, 12, 14, 16, 18, 20, 24, 28, 32, 36, 40]
ATLAS_COLS = 16  # cells per row in every page; layout shared across sizes


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _sha256_bytes(buf: bytes) -> str:
    return hashlib.sha256(buf).hexdigest()


def _font_candidates_for_manifest(manifest: dict) -> list[str]:
    candidates: list[str] = []
    compile_font = manifest.get("_compile_font")
    if isinstance(compile_font, str) and compile_font:
        candidates.append(compile_font)
    font_id = manifest.get("font_id")
    if isinstance(font_id, str) and font_id:
        for ext in (".ttf", ".otf", ".ttc"):
            candidates.append(os.path.join(_REPO_ROOT, "assets", "fonts", font_id + ext))
        for base in (
            "/System/Library/Fonts",
            "/System/Library/Fonts/Supplemental",
            "/Library/Fonts",
        ):
            for ext in (".ttf", ".otf", ".ttc"):
                candidates.append(os.path.join(base, font_id + ext))
    candidates.append(DEFAULT_FONT_PATH)
    seen = set()
    out = []
    for path in candidates:
        if path and path not in seen:
            seen.add(path)
            out.append(path)
    return out


def _find_font_by_sha256(wanted_sha256: str) -> str:
    for base in (
        os.path.join(_REPO_ROOT, "assets", "fonts"),
        "/System/Library/Fonts",
        "/System/Library/Fonts/Supplemental",
        "/Library/Fonts",
    ):
        if not os.path.isdir(base):
            continue
        for root, _dirs, files in os.walk(base):
            for name in files:
                if not name.lower().endswith((".ttf", ".otf", ".ttc")):
                    continue
                path = os.path.join(root, name)
                try:
                    if _sha256_file(path) == wanted_sha256:
                        return path
                except OSError:
                    continue
    return ""


def _resolve_compile_font(manifest: dict, explicit_font_path: str) -> str:
    if explicit_font_path:
        return explicit_font_path
    wanted_sha = manifest.get("font_sha256")
    if isinstance(wanted_sha, str) and len(wanted_sha) == 64:
        for path in _font_candidates_for_manifest(manifest):
            if os.path.isfile(path) and _sha256_file(path) == wanted_sha:
                return path
        found = _find_font_by_sha256(wanted_sha)
        if found:
            return found
    compile_font = manifest.get("_compile_font")
    if isinstance(compile_font, str) and compile_font:
        return compile_font
    return DEFAULT_FONT_PATH


def _bake_page(entries, cell_px, font_path):
    """Rasterize one atlas page at the requested cell_px.

    Returns (width_px, height_px, rgba8_int_list, page_hash_hex).

    Layout: ATLAS_COLS cells wide, ceil(len(entries)/ATLAS_COLS) cells tall.
    Each entry's unicode_scalar is rendered centered in its cell with the font
    sized to cell_px (slightly inset). Entries without unicode_scalar are left
    blank (the existing coverage_quadrants metadata is informational only at
    this layer; the engine still consults coverage[] for blending).
    """
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as e:
        raise RuntimeError(
            "PIL (Pillow) is required for --compile. Install with: python3 -m pip install pillow"
        ) from e

    rows = (len(entries) + ATLAS_COLS - 1) // ATLAS_COLS
    width_px = ATLAS_COLS * cell_px
    height_px = max(1, rows * cell_px)
    img = Image.new("RGBA", (width_px, height_px), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Pick a font size that fits the cell. PIL's ImageFont treats the size as
    # the EM-square height; we leave a 1px inset so descenders don't clip.
    font_size = max(2, cell_px - 1)
    try:
        font = ImageFont.truetype(font_path, font_size)
    except OSError as e:
        raise RuntimeError(f"Failed to load font {font_path}: {e}") from e

    for i, entry in enumerate(entries):
        u = entry.get("unicode_scalar")
        if u is None:
            continue
        try:
            ch = chr(int(u))
        except (TypeError, ValueError):
            continue
        cx = (i % ATLAS_COLS) * cell_px
        cy = (i // ATLAS_COLS) * cell_px
        bbox = draw.textbbox((0, 0), ch, font=font)
        gw = bbox[2] - bbox[0]
        gh = bbox[3] - bbox[1]
        tx = cx + (cell_px - gw) // 2 - bbox[0]
        ty = cy + (cell_px - gh) // 2 - bbox[1]
        draw.text((tx, ty), ch, font=font, fill=(255, 255, 255, 255))

    raw = img.tobytes()
    rgba8 = list(raw)
    page_hash = _sha256_bytes(raw)
    return width_px, height_px, rgba8, page_hash, img


def _build_lut(entries, max_gid):
    """Build the GlyphId -> (atlas_x_cell, atlas_y_cell, 0, 255) LUT.

    Layout is size-agnostic — atlas cell coordinates are the same for every
    page in the AOA.
    """
    lut_width = max_gid - 255
    lut = [0] * (lut_width * 4)
    for i in range(lut_width):
        lut[i * 4 + 2] = 0
        lut[i * 4 + 3] = 255
    glyph_index: dict[str, list[int]] = {}
    cell_px = 16  # glyph_index pixel rect uses cell_px=16 as the canonical unit
    for out_index, entry in enumerate(entries):
        glyph_id = int(entry["glyph_id"])
        atlas_x_cell = out_index % ATLAS_COLS
        atlas_y_cell = out_index // ATLAS_COLS
        atlas_x_px = atlas_x_cell * cell_px
        atlas_y_px = atlas_y_cell * cell_px
        glyph_index[str(glyph_id)] = [
            0,
            atlas_x_px,
            atlas_y_px,
            atlas_x_px + cell_px,
            atlas_y_px + cell_px,
        ]
        lut_index = glyph_id - 256
        lut[lut_index * 4 + 0] = atlas_x_cell
        lut[lut_index * 4 + 1] = atlas_y_cell
        lut[lut_index * 4 + 2] = 0
        lut[lut_index * 4 + 3] = 255
    return lut, lut_width, glyph_index


def cmd_compile(manifest_path: str, out_dir: str, font_path: str = "",
                sizes_csv: str = "", write_pngs: bool = False,
                write_manifest_back: bool = False) -> int:
    """Compile manifest into per-size atlas pages, LUT, and atlas-of-atlases."""
    if not out_dir:
        print("[ERROR] --compile requires --out OUT_DIR", file=sys.stderr)
        return 1
    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)
    except FileNotFoundError:
        print(f"[ERROR] File not found: {manifest_path}", file=sys.stderr)
        return 1
    except json.JSONDecodeError as e:
        print(f"[ERROR] JSON parse error in {manifest_path}: {e}", file=sys.stderr)
        return 1

    schema = _load_schema()
    errors = _validate_manifest(manifest, manifest_path, schema)
    if errors:
        print(f"[FAIL] {manifest_path}:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    explicit_font_path = bool(font_path)
    font_path = _resolve_compile_font(manifest, font_path)
    if not os.path.isfile(font_path):
        print(f"[ERROR] Font not found: {font_path}", file=sys.stderr)
        return 1
    font_sha256 = _sha256_file(font_path)
    font_id = os.path.splitext(os.path.basename(font_path))[0] if explicit_font_path else (
        manifest.get("font_id") or os.path.splitext(os.path.basename(font_path))[0]
    )
    style_id = manifest.get("style_id", "regular")

    if sizes_csv:
        try:
            cell_sizes = sorted({int(x) for x in sizes_csv.split(",") if x.strip()})
        except ValueError:
            print(f"[ERROR] --sizes must be CSV of integers, got {sizes_csv!r}", file=sys.stderr)
            return 1
    else:
        cell_sizes = list(manifest.get("supported_cell_sizes") or DEFAULT_CELL_SIZES)
    if not cell_sizes:
        print("[ERROR] supported_cell_sizes is empty", file=sys.stderr)
        return 1

    # Lock manifest font identity + sizes BEFORE hashing so the hash reflects
    # the artifacts the compiler is about to produce.
    manifest["font_id"] = font_id
    manifest["font_sha256"] = font_sha256
    manifest["style_id"] = style_id
    manifest["supported_cell_sizes"] = cell_sizes

    entries = sorted(manifest["entries"], key=lambda e: int(e["glyph_id"]))
    content_pack_id = manifest["content_pack_id"]
    manifest_hash = sha256_manifest(manifest)
    max_gid = max(int(e["glyph_id"]) for e in entries)

    lut, lut_width, glyph_index = _build_lut(entries, max_gid)
    lut_payload = {
        "format": "rgba8",
        "width": lut_width,
        "height": 1,
        "glyph_id_base": 256,
        "rgba8": lut,
    }
    lut_canonical = rfc8785_canonical(glyph_index)
    lut_hash = _sha256_bytes(lut_canonical)

    os.makedirs(out_dir, exist_ok=True)
    base = content_pack_id
    pages_meta = []
    page_paths = []
    for cell_px in cell_sizes:
        width_px, height_px, rgba8, page_hash, img = _bake_page(entries, cell_px, font_path)
        page_filename = f"{base}.page{cell_px}_rgba8.json"
        page_path = os.path.join(out_dir, page_filename)
        page_paths.append(page_path)
        page_payload = {
            "format": "rgba8",
            "width": width_px,
            "height": height_px,
            "cell_px": cell_px,
            "page_hash": page_hash,
            "rgba8": rgba8,
        }
        with open(page_path, "w", encoding="utf-8") as f:
            json.dump(page_payload, f, sort_keys=True, separators=(",", ":"))
            f.write("\n")
        if write_pngs:
            img.save(os.path.join(out_dir, f"{base}.page{cell_px}.png"))
        pages_meta.append({
            "page_id": f"page{cell_px}",
            "url": page_filename,
            "width_px": width_px,
            "height_px": height_px,
            "cell_px": cell_px,
            "page_hash": page_hash,
            "page_binding_kind": "atlas_rect",
            "format": "rgba8",
        })

    # Backward-compat alias: emit page0_rgba8.json pointing at the canonical
    # 16px page so existing single-page consumers (native terminal_gl_present,
    # web InitFl4131CompiledManifestTextures) still load until Phase 8/9 wire
    # multi-size selection.
    canonical_cell_px = 16 if 16 in cell_sizes else cell_sizes[len(cell_sizes) // 2]
    canonical_idx = cell_sizes.index(canonical_cell_px)
    canonical_page = pages_meta[canonical_idx]
    legacy_path = os.path.join(out_dir, f"{base}.page0_rgba8.json")
    with open(page_paths[canonical_idx], "r", encoding="utf-8") as src:
        legacy_payload = src.read()
    with open(legacy_path, "w", encoding="utf-8") as dst:
        dst.write(legacy_payload)

    aoa = {
        "aoa_version": 1,
        "content_pack_id": content_pack_id,
        "manifest_hash": manifest_hash,
        "fallback_glyph_id": manifest["fallback_glyph_id"],
        "font_id": font_id,
        "font_sha256": font_sha256,
        "style_id": style_id,
        "lut_hash": lut_hash,
        "pages": pages_meta,
        "glyph_index": glyph_index,
    }
    aoa_path = os.path.join(out_dir, f"{base}.atlas_of_atlases.json")
    lut_path = os.path.join(out_dir, f"{base}.lut_rgba8.json")
    for path, payload in ((aoa_path, aoa), (lut_path, lut_payload)):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, sort_keys=True, separators=(",", ":"))
            f.write("\n")

    if write_manifest_back:
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2, ensure_ascii=False)
            f.write("\n")

    print(f"[OK] compiled {manifest_path} -> {out_dir}")
    print(f"     manifest_hash = {manifest_hash}")
    print(f"     lut_hash      = {lut_hash}")
    print(f"     font_sha256   = {font_sha256}")
    print(f"     pages         = {len(pages_meta)} ({','.join(str(p['cell_px']) for p in pages_meta)})")
    return 0


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) > 1 and sys.argv[1].startswith("--"):
        parser2 = argparse.ArgumentParser(add_help=True)
        parser2.add_argument("--check", nargs="*", metavar="MANIFEST")
        parser2.add_argument("--hash", metavar="MANIFEST")
        parser2.add_argument("--compile", metavar="MANIFEST")
        parser2.add_argument("--out", metavar="OUT_DIR")
        parser2.add_argument("--font", metavar="PATH", default="")
        parser2.add_argument("--sizes", metavar="CSV", default="")
        parser2.add_argument("--write-pngs", action="store_true")
        parser2.add_argument("--write-manifest", action="store_true")
        args2 = parser2.parse_args()

        if args2.check is not None:
            sys.exit(cmd_check(args2.check))
        elif args2.hash:
            sys.exit(cmd_hash(args2.hash))
        elif args2.compile:
            out = args2.out or ""
            sys.exit(cmd_compile(
                args2.compile, out,
                font_path=args2.font,
                sizes_csv=args2.sizes,
                write_pngs=args2.write_pngs,
                write_manifest_back=args2.write_manifest,
            ))
        else:
            parser2.print_help()
            sys.exit(1)

    parser = argparse.ArgumentParser(
        description="FL-4131 glyph manifest tooling (Phase 0).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="command")

    check_p = sub.add_parser("--check", help="Validate manifest file(s)")
    check_p.add_argument("manifests", nargs="*", metavar="MANIFEST", help="Manifest files to validate (default: all fixtures)")

    hash_p = sub.add_parser("--hash", help="Print RFC8785+SHA-256 of a manifest")
    hash_p.add_argument("manifest", metavar="MANIFEST")

    compile_p = sub.add_parser("--compile", help="Bake manifest into per-size atlas pages + AOA + LUT")
    compile_p.add_argument("manifest", metavar="MANIFEST")
    compile_p.add_argument("--out", required=True, metavar="OUT_DIR")
    compile_p.add_argument("--font", metavar="PATH", default="")
    compile_p.add_argument("--sizes", metavar="CSV", default="")
    compile_p.add_argument("--write-pngs", action="store_true")
    compile_p.add_argument("--write-manifest", action="store_true")

    # Support argparse subcommand style AND flag style (--check FILE)
    # because the gate description says `--check` as a flag.
    args, unknown = parser.parse_known_args()

    if args.command == "--check":
        sys.exit(cmd_check(args.manifests))
    elif args.command == "--hash":
        sys.exit(cmd_hash(args.manifest))
    elif args.command == "--compile":
        sys.exit(cmd_compile(
            args.manifest, args.out,
            font_path=getattr(args, "font", ""),
            sizes_csv=getattr(args, "sizes", ""),
            write_pngs=getattr(args, "write_pngs", False),
            write_manifest_back=getattr(args, "write_manifest", False),
        ))
    else:
        # Try bare --check / --hash flags
        parser2 = argparse.ArgumentParser(add_help=False)
        parser2.add_argument("--check", nargs="*", metavar="MANIFEST")
        parser2.add_argument("--hash", metavar="MANIFEST")
        parser2.add_argument("--compile", metavar="MANIFEST")
        parser2.add_argument("--out", metavar="OUT_DIR")
        parser2.add_argument("--font", metavar="PATH", default="")
        parser2.add_argument("--sizes", metavar="CSV", default="")
        parser2.add_argument("--write-pngs", action="store_true")
        parser2.add_argument("--write-manifest", action="store_true")
        args2 = parser2.parse_args()

        if args2.check is not None:
            sys.exit(cmd_check(args2.check))
        elif args2.hash:
            sys.exit(cmd_hash(args2.hash))
        elif args2.compile:
            out = args2.out or ""
            sys.exit(cmd_compile(
                args2.compile, out,
                font_path=args2.font,
                sizes_csv=args2.sizes,
                write_pngs=args2.write_pngs,
                write_manifest_back=args2.write_manifest,
            ))
        else:
            parser.print_help()
            sys.exit(1)


if __name__ == "__main__":
    main()

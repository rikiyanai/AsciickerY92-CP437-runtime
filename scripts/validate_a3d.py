#!/usr/bin/env python3
"""
A3D File Format Validator

Validates the integrity of .a3d world files used by the Asciicker game engine.
Checks magic numbers, header structure, terrain patches, material tables, and world data.

Usage:
    python3 scripts/validate_a3d.py path/to/file.a3d [path/to/another.a3d ...]

Exit codes:
    0 - All files valid
    1 - One or more files invalid or validation error
"""

import argparse
import struct
import sys
from pathlib import Path
from typing import Tuple, Optional


# Format constants
MAGIC_NUMBER = b'AS3D'
EXPECTED_HEADER_SIZE = 16
PATCH_SIZE = 188  # 8 + 128 + 50 + 2 bytes
MATERIAL_TABLE_SIZE = 131072  # 256 materials × 4 shades × 16 cells × 8 bytes

# Sanity limits
MAX_PATCHES = 100000
MIN_FORMAT_VERSION = -1000
MAX_FORMAT_VERSION = -1
MAX_INSTANCES = 1000000


class ValidationError(Exception):
    """Raised when A3D file validation fails."""
    pass


def read_file_header(data: bytes) -> Tuple[int, int, int]:
    """
    Parse the 16-byte file header.

    Returns:
        Tuple of (header_size, num_patches, reserved)

    Raises:
        ValidationError: If header is invalid
    """
    if len(data) < 16:
        raise ValidationError(f"File too small for header: {len(data)} bytes (expected >= 16)")

    # Check magic number
    magic = data[0:4]
    if magic != MAGIC_NUMBER:
        magic_str = magic.decode('ascii', errors='replace')
        raise ValidationError(f"Invalid magic number: expected 'AS3D', got '{magic_str}'")

    # Parse header fields
    header_size, num_patches, reserved = struct.unpack('<III', data[4:16])

    if header_size != EXPECTED_HEADER_SIZE:
        raise ValidationError(f"Invalid header size: expected {EXPECTED_HEADER_SIZE}, got {header_size}")

    if num_patches < 0 or num_patches > MAX_PATCHES:
        raise ValidationError(f"Unreasonable patch count: {num_patches} (max {MAX_PATCHES})")

    return header_size, num_patches, reserved


def validate_terrain_section(data: bytes, offset: int, num_patches: int) -> int:
    """
    Validate the terrain patches section.

    Args:
        data: Full file data
        offset: Starting offset (should be 16)
        num_patches: Number of patches to expect

    Returns:
        Offset after terrain section

    Raises:
        ValidationError: If terrain section is invalid
    """
    terrain_size = num_patches * PATCH_SIZE
    expected_end = offset + terrain_size

    if len(data) < expected_end:
        raise ValidationError(
            f"File too small for terrain data: {len(data)} bytes "
            f"(expected >= {expected_end} for {num_patches} patches)"
        )

    return expected_end


def validate_material_table(data: bytes, offset: int) -> int:
    """
    Validate the material table section.

    Args:
        data: Full file data
        offset: Starting offset

    Returns:
        Offset after material table

    Raises:
        ValidationError: If material table is missing or incomplete
    """
    expected_end = offset + MATERIAL_TABLE_SIZE

    if len(data) < expected_end:
        raise ValidationError(
            f"File too small for material table: {len(data)} bytes "
            f"(expected >= {expected_end})"
        )

    return expected_end


def validate_world_header(data: bytes, offset: int) -> Tuple[Optional[int], int]:
    """
    Validate the world data header and determine format.

    Args:
        data: Full file data
        offset: Starting offset

    Returns:
        Tuple of (format_version or None, instance_count)

    Raises:
        ValidationError: If world header is invalid
    """
    if len(data) < offset + 4:
        raise ValidationError(f"File truncated: no world header at offset {offset}")

    first_int32 = struct.unpack('<i', data[offset:offset+4])[0]

    if first_int32 < 0:
        # New format: negative value is format version
        format_version = first_int32

        if format_version < MIN_FORMAT_VERSION or format_version > MAX_FORMAT_VERSION:
            raise ValidationError(
                f"Unreasonable format version: {format_version} "
                f"(expected {MIN_FORMAT_VERSION} to {MAX_FORMAT_VERSION})"
            )

        # Read instance count
        if len(data) < offset + 8:
            raise ValidationError(f"File truncated: no instance count at offset {offset+4}")

        instance_count = struct.unpack('<i', data[offset+4:offset+8])[0]

        if instance_count < 0 or instance_count > MAX_INSTANCES:
            raise ValidationError(
                f"Unreasonable instance count: {instance_count} "
                f"(expected 0 to {MAX_INSTANCES})"
            )

        return format_version, instance_count
    else:
        # Legacy format: non-negative value is instance count
        instance_count = first_int32

        if instance_count < 0 or instance_count > MAX_INSTANCES:
            raise ValidationError(
                f"Unreasonable instance count (legacy format): {instance_count} "
                f"(expected 0 to {MAX_INSTANCES})"
            )

        return None, instance_count


def validate_a3d_file(filepath: Path) -> dict:
    """
    Validate a single A3D file.

    Args:
        filepath: Path to .a3d file

    Returns:
        Dictionary with validation results

    Raises:
        ValidationError: If validation fails
    """
    if not filepath.exists():
        raise ValidationError(f"File not found: {filepath}")

    if not filepath.is_file():
        raise ValidationError(f"Not a regular file: {filepath}")

    # Read entire file
    try:
        data = filepath.read_bytes()
    except Exception as e:
        raise ValidationError(f"Failed to read file: {e}")

    # Validate sections in order
    header_size, num_patches, reserved = read_file_header(data)

    offset = header_size
    terrain_size = num_patches * PATCH_SIZE

    offset = validate_terrain_section(data, offset, num_patches)
    offset = validate_material_table(data, offset)

    format_version, instance_count = validate_world_header(data, offset)

    # Calculate section sizes
    results = {
        'file_size': len(data),
        'num_patches': num_patches,
        'terrain_size': terrain_size,
        'material_size': MATERIAL_TABLE_SIZE,
        'format_version': format_version,
        'instance_count': instance_count,
        'world_offset': offset
    }

    return results


def format_success_message(filepath: Path, results: dict) -> str:
    """Format a success message with file details."""
    lines = [f"OK: {filepath.name}"]

    lines.append(f"  Terrain: {results['num_patches']} patches ({results['terrain_size']} bytes)")
    lines.append(f"  Materials: {results['material_size']} bytes")

    if results['format_version'] is not None:
        version_str = f"format v{abs(results['format_version'])}"
    else:
        version_str = "legacy format"

    lines.append(f"  World: {version_str}, {results['instance_count']} instances")
    lines.append(f"  Total: {results['file_size']} bytes")

    return '\n'.join(lines)


def format_error_message(filepath: Path, error: Exception) -> str:
    """Format an error message."""
    lines = [f"FAIL: {filepath.name}"]
    lines.append(f"  Error: {error}")
    return '\n'.join(lines)


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description='Validate A3D world file integrity',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument(
        'files',
        nargs='+',
        type=Path,
        metavar='FILE',
        help='A3D file(s) to validate'
    )

    args = parser.parse_args()

    all_valid = True

    for filepath in args.files:
        try:
            results = validate_a3d_file(filepath)
            print(format_success_message(filepath, results))
        except ValidationError as e:
            print(format_error_message(filepath, e), file=sys.stderr)
            all_valid = False
        except Exception as e:
            print(
                format_error_message(filepath, f"Unexpected error: {e}"),
                file=sys.stderr
            )
            all_valid = False

        # Blank line between files (except last)
        if filepath != args.files[-1]:
            print()

    sys.exit(0 if all_valid else 1)


if __name__ == "__main__":
    main()

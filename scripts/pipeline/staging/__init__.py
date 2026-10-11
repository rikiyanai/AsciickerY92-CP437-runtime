"""
Staging directory for work-in-progress sprite assets.

DIRECTORY STRUCTURE:
    staging/
    |- inputs/     # Source PNG sheets (AI-generated or downloaded)
    |- xp/         # Generated .xp files (not yet published)
    |- debug/      # Debug visualization outputs
    |- renders/    # Intermediate Blender render outputs
    |- sheets/     # Sliced sprite sheets
    +- archive/    # Published sprite sources (.gitignored)

WORKFLOW:
    1. New assets start in staging/inputs/ or staging/renders/
    2. Pipeline generates .xp to staging/xp/
    3. Validation passes -> publish to assets/sprites/
    4. Source files move to staging/archive/ (preserves history)

STALENESS WARNING:
    Files in staging/ older than 7 days trigger warnings.
    This is informational only - no auto-deletion.

KEY EXPORTS:
    - STAGING_DIR:              Path to staging directory
    - STAGING_STRUCTURE:        Dict mapping subdirs to descriptions
    - ensure_staging_structure: Create staging directories if missing
    - check_staging_staleness:  Find files older than warn_days threshold
    - print_staleness_warnings: Format and print stale file warnings
"""

from pathlib import Path
from datetime import datetime
from typing import List, Dict

# Staging directory path
STAGING_DIR = Path(__file__).parent

# Directory structure with descriptions
STAGING_STRUCTURE = {
    "inputs": "Original input files (PNG, .blend)",
    "sheets": "Processed sprite sheets (before conversion)",
    "xp": "Final XP asset files",
    "debug": "Debug artifacts (label sheets, intermediate PNGs)",
    "renders": "Intermediate Blender render outputs",
    "archive": "Published sprite sources (gitignored)",
}


def ensure_staging_structure() -> None:
    """
    Create staging directories if not present.

    Creates each subdirectory in STAGING_STRUCTURE if it doesn't exist.
    Logs creation if newly created.

    Example:
        ensure_staging_structure()  # Creates: scripts/pipeline/staging/{inputs,sheets,xp,debug,renders,archive}
    """
    for subdir, description in STAGING_STRUCTURE.items():
        path = STAGING_DIR / subdir

        if not path.exists():
            path.mkdir(parents=True, exist_ok=True)
            print(f"Created staging directory: {path} ({description})")


def check_staging_staleness(
    staging_path: str = None,
    warn_days: int = 7
) -> List[Dict]:
    """
    Check for stale files in staging directory.

    Scans active working directories (inputs, xp, debug, renders, sheets)
    for files older than the warn_days threshold. The archive/ directory
    is intentionally excluded since archived files are expected to be old.

    Args:
        staging_path: Path to staging directory. Defaults to this module's parent.
        warn_days: Age threshold in days before warning (default: 7)

    Returns:
        List of dicts with 'path', 'age_days', 'type' for each stale file.
        Empty list if no stale files found.

    Example:
        stale = check_staging_staleness()
        # [{'path': 'staging/xp/old_sprite.xp', 'age_days': 14, 'type': 'xp'}]
    """
    if staging_path is None:
        staging_path = STAGING_DIR
    else:
        staging_path = Path(staging_path)

    stale_files = []
    now = datetime.now()

    # Only check active working dirs (skip archive/)
    check_dirs = ['inputs', 'xp', 'debug', 'renders', 'sheets']

    for subdir in check_dirs:
        subdir_path = staging_path / subdir
        if not subdir_path.exists():
            continue

        for filepath in subdir_path.rglob('*'):
            if filepath.is_file():
                mtime = datetime.fromtimestamp(filepath.stat().st_mtime)
                age_days = (now - mtime).days

                if age_days > warn_days:
                    stale_files.append({
                        'path': str(filepath),
                        'age_days': age_days,
                        'type': subdir
                    })

    return stale_files


def print_staleness_warnings(stale_files: List[Dict]) -> None:
    """
    Print formatted warnings for stale staging files.

    Output format follows the project's [PREFIX] convention for
    pipeline messages, making them easy to grep in logs.

    Args:
        stale_files: List of dicts from check_staging_staleness()

    Example output:
        [STAGING] Warning: 2 stale file(s) in staging/
          [xp] 14 days old: staging/xp/old_sprite.xp
          [inputs] 8 days old: staging/inputs/forgotten.png
    """
    if not stale_files:
        return

    print(f"[STAGING] Warning: {len(stale_files)} stale file(s) in staging/")
    for f in stale_files:
        print(f"  [{f['type']}] {f['age_days']} days old: {f['path']}")


__all__ = [
    "STAGING_DIR",
    "STAGING_STRUCTURE",
    "ensure_staging_structure",
    "check_staging_staleness",
    "print_staleness_warnings",
]

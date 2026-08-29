#!/usr/bin/env bash
# doc_lifecycle_stitch.sh — stitch a multiplayer doc into the archive, delete it,
# and rewrite all path references across the worktree to point at the archive anchor.
#
# Usage:
#   ./scripts/doc_lifecycle_stitch.sh <worktree-root> <doc-path-relative-to-worktree>
#
# Example:
#   ./scripts/doc_lifecycle_stitch.sh .worktrees/bug1-remote-pose docs/plans/2026-03-06-ralph-drift-proof-plan.md
#
# What it does:
#   1. Appends the full doc content to MULTIPLAYER_DOCS_ARCHIVE.md (matching existing format)
#   2. Updates the archive TOC
#   3. Deletes the original file
#   4. Greps the entire worktree for any remaining path references and rewrites them to archive anchors
#   5. Appends a doc_lifecycle_violation entry to docs/FAILURE_LOG.md

set -euo pipefail

if [ $# -lt 2 ]; then
    echo "Usage: $0 <worktree-root> <doc-path-relative-to-worktree>"
    echo "Example: $0 .worktrees/bug1-remote-pose docs/plans/2026-03-06-ralph-drift-proof-plan.md"
    exit 1
fi

WORKTREE="$1"
DOC_REL="$2"
DOC_ABS="${WORKTREE}/${DOC_REL}"

ARCHIVE="${WORKTREE}/docs/research/ascii/verification/archive/MULTIPLAYER_DOCS_ARCHIVE.md"
FAILURE_LOG="${WORKTREE}/docs/FAILURE_LOG.md"

# Validate inputs
if [ ! -f "$DOC_ABS" ]; then
    echo "ERROR: Doc not found: $DOC_ABS"
    exit 1
fi

if [ ! -f "$ARCHIVE" ]; then
    echo "ERROR: Archive not found: $ARCHIVE"
    exit 1
fi

if [ ! -f "$FAILURE_LOG" ]; then
    echo "ERROR: Failure log not found: $FAILURE_LOG"
    exit 1
fi

# Never stitch the canonical files themselves
CANON_SPEC="docs/plans/2026-03-22-multiplayer-canonical-spec.md"
CANON_FL="docs/FAILURE_LOG.md"
if [ "$DOC_REL" = "$CANON_SPEC" ] || [ "$DOC_REL" = "$CANON_FL" ]; then
    echo "ERROR: Cannot archive a canonical file: $DOC_REL"
    exit 1
fi

# Build the anchor ID (matches existing archive convention: path with / and . replaced by -)
ANCHOR_ID=$(echo "$DOC_REL" | sed 's/[\/\.]/-/g')

# Check if already archived
if grep -q "id=\"${ANCHOR_ID}\"" "$ARCHIVE" 2>/dev/null; then
    echo "SKIP: Already in archive (anchor ${ANCHOR_ID}). Proceeding to delete + repoint only."
    ALREADY_STITCHED=1
else
    ALREADY_STITCHED=0
fi

LAST_MOD=$(stat -f "%Sm" -t "%Y-%m-%d %H:%M:%S" "$DOC_ABS" 2>/dev/null || date +"%Y-%m-%d %H:%M:%S")
DOC_LINES=$(wc -l < "$DOC_ABS" | tr -d ' ')
TODAY=$(date +"%Y-%m-%d")

echo "=== doc_lifecycle_stitch ==="
echo "  Worktree:  $WORKTREE"
echo "  Doc:       $DOC_REL ($DOC_LINES lines)"
echo "  Anchor:    $ANCHOR_ID"
echo "  Archive:   $ARCHIVE"
echo ""

# --- Step 1: Stitch into archive ---
if [ "$ALREADY_STITCHED" -eq 0 ]; then
    echo "[1/4] Stitching into archive..."

    # Count existing entries for TOC numbering
    EXISTING_COUNT=$(grep -c '^<a id="' "$ARCHIVE" 2>/dev/null || echo 0)
    NEW_NUM=$((EXISTING_COUNT + 1))

    TOC_LINE="${NEW_NUM}. [${DOC_REL}](#${ANCHOR_ID}) (last modified: $(echo "$LAST_MOD" | cut -d' ' -f1,2 | cut -c1-16))"

    # Find the blank line + --- separator after the TOC entries and insert before it
    TOC_START=$(grep -n '## Table of Contents' "$ARCHIVE" | head -1 | cut -d: -f1)
    if [ -n "$TOC_START" ]; then
        # Find the first blank line after TOC_START that is followed by ---
        BLANK_BEFORE_SEP=$(tail -n +"$((TOC_START + 1))" "$ARCHIVE" | grep -n '^$' | while IFS=: read -r lnum _rest; do
            ABS=$((TOC_START + lnum))
            NEXT_LINE=$(sed -n "$((ABS + 1))p" "$ARCHIVE")
            if [ "$NEXT_LINE" = "---" ]; then
                echo "$ABS"
                break
            fi
        done)
        if [ -n "$BLANK_BEFORE_SEP" ]; then
            # Insert new TOC entry just before the blank line
            { head -n "$((BLANK_BEFORE_SEP - 1))" "$ARCHIVE"
              echo "$TOC_LINE"
              tail -n +"$BLANK_BEFORE_SEP" "$ARCHIVE"
            } > "${ARCHIVE}.tmp" && mv "${ARCHIVE}.tmp" "$ARCHIVE"
        fi
    fi

    # Append the full doc content at the end of archive
    cat >> "$ARCHIVE" <<STITCH_EOF

---

<a id="${ANCHOR_ID}"></a>

## ARCHIVED: \`${DOC_REL}\`
**Last modified:** ${LAST_MOD}
**Original path:** \`${DOC_REL}\`

$(cat "$DOC_ABS")
STITCH_EOF

    echo "  Appended ${DOC_LINES} lines + header to archive."
else
    echo "[1/4] Already stitched, skipping append."
fi

# --- Step 2: Delete the original ---
echo "[2/4] Deleting original: $DOC_ABS"
rm "$DOC_ABS"

# --- Step 3: Rewrite all path references in the worktree ---
echo "[3/4] Rewriting path references across worktree..."

# The archive pointer path (relative to docs/)
ARCHIVE_REL="research/ascii/verification/archive/MULTIPLAYER_DOCS_ARCHIVE.md"
ARCHIVE_POINTER="${ARCHIVE_REL}#${ANCHOR_ID}"

# Patterns to search for (the doc path can appear in various forms)
# We search .md, .js, .sh, .py, .json, .txt files
REF_COUNT=0

# Use grep to find files containing the doc path, then sed to replace
# Handle both the relative path and just the filename
DOC_BASENAME=$(basename "$DOC_REL")
DOC_DIRNAME=$(dirname "$DOC_REL")

# Search for references — exclude the archive itself, binary files, and .git
while IFS= read -r ref_file; do
    # Skip the archive itself
    if [ "$ref_file" = "$ARCHIVE" ]; then
        continue
    fi

    # Count replacements before
    BEFORE=$(grep -c "$DOC_REL" "$ref_file" 2>/dev/null || echo 0)
    BEFORE2=$(grep -c "$DOC_BASENAME" "$ref_file" 2>/dev/null || echo 0)

    # Replace full relative path references: docs/plans/foo.md -> archive#anchor
    if [ "$BEFORE" -gt 0 ]; then
        # Handle markdown link syntax: [text](path) -> [text (ARCHIVED)](archive#anchor)
        sed -i '' "s|\](${DOC_REL})|, ARCHIVED](${ARCHIVE_POINTER})|g" "$ref_file"
        # Handle bare path references
        sed -i '' "s|${DOC_REL}|${ARCHIVE_POINTER}|g" "$ref_file"
        REF_COUNT=$((REF_COUNT + BEFORE))
        echo "  Rewritten in: $ref_file ($BEFORE refs)"
    fi
done < <(grep -rl --include='*.md' --include='*.js' --include='*.sh' --include='*.py' --include='*.json' --include='*.txt' "$DOC_REL" "$WORKTREE" 2>/dev/null || true)

echo "  Total references rewritten: $REF_COUNT"

# --- Step 4: Append failure log entry ---
echo "[4/4] Appending doc_lifecycle_violation to failure log..."

# Find next FL number
LAST_FL=$(grep -o 'FL-[0-9]*' "$FAILURE_LOG" | sed 's/FL-//' | sort -n | tail -1)
NEXT_FL=$((LAST_FL + 1))

cat >> "$FAILURE_LOG" <<FL_EOF

### FL-$(printf '%03d' $NEXT_FL): Doc lifecycle violation — stale worksheet archived (${TODAY})

**Status:** RESOLVED
**Date Opened:** ${TODAY}
**Date Closed:** ${TODAY}
**Category:** doc_lifecycle_violation

**Description:**
Stale multiplayer worksheet \`${DOC_REL}\` (${DOC_LINES} lines, last modified ${LAST_MOD}) was still live after being superseded by the canonical spec. Stitched to archive and deleted by doc-lifecycle-enforcer.

**Actions taken:**
1. Full content stitched to \`${ARCHIVE_REL}#${ANCHOR_ID}\`
2. Original file deleted
3. ${REF_COUNT} path references across worktree rewritten to archive pointer
4. This failure-log entry appended

**Archive anchor:** \`${ANCHOR_ID}\`
FL_EOF

echo ""
echo "=== DONE ==="
echo "  Stitched:   ${DOC_REL} -> archive#${ANCHOR_ID}"
echo "  Deleted:    ${DOC_ABS}"
echo "  Refs fixed: ${REF_COUNT}"
echo "  FL entry:   FL-$(printf '%03d' $NEXT_FL)"

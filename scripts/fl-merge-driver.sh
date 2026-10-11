#!/usr/bin/env bash
# fl-merge-driver.sh — append-only union merge for docs/FAILURE_LOG.md
#
# Git merge driver for the canonical failure log. Since FL entries are
# append-only (new entries always go at the tail), this driver:
#   1. Keeps the shared prefix from the ancestor
#   2. Takes the longer divergent tail as the primary block
#   3. Appends the shorter tail's unique entries after it
#   4. Renumbers any FL-NNN collisions in the appended block
#
# Setup (once per clone):
#   git config merge.fl-append.name "Failure log append-only union merge"
#   git config merge.fl-append.driver "scripts/fl-merge-driver.sh %O %A %B"
#
# The .gitattributes entry (committed, travels with repo):
#   docs/FAILURE_LOG.md merge=fl-append

set -euo pipefail

ANCESTOR="$1"  # %O — common ancestor
OURS="$2"      # %A — current branch (merge target, result written here)
THEIRS="$3"    # %B — incoming branch

# Find the last line shared between ancestor and both sides.
# Since the file is append-only, the ancestor content should be a prefix of both.
ancestor_lines=$(wc -l < "$ANCESTOR")

# Extract tails (content added after the shared ancestor prefix)
ours_total=$(wc -l < "$OURS")
theirs_total=$(wc -l < "$THEIRS")

ours_added=$((ours_total - ancestor_lines))
theirs_added=$((theirs_total - ancestor_lines))

# If one side added nothing, the other side wins trivially
if [ "$ours_added" -le 0 ] && [ "$theirs_added" -le 0 ]; then
    # No changes on either side — ancestor is the result
    exit 0
fi

if [ "$theirs_added" -le 0 ]; then
    # Only our side added entries — already in $OURS, nothing to do
    exit 0
fi

if [ "$ours_added" -le 0 ]; then
    # Only their side added entries — take theirs
    cp "$THEIRS" "$OURS"
    exit 0
fi

# Both sides added entries. Figure out which block is primary (more entries)
# and which gets appended with renumbering.

# Extract FL numbers from headers (POSIX-compatible, no -P flag)
extract_fl_numbers() {
    sed -n 's/^##\{1,2\} FL-\([0-9][0-9]*\).*/\1/p' "$1"
}

# Find the highest FL number in a file
find_max_fl() {
    extract_fl_numbers "$1" | sort -n | tail -1
}

# Determine primary (bigger tail) and secondary (smaller tail, gets renumbered)
if [ "$ours_added" -ge "$theirs_added" ]; then
    primary_file="$OURS"
    secondary_file="$THEIRS"
    secondary_start=$((ancestor_lines + 1))
    secondary_added="$theirs_added"
else
    primary_file="$THEIRS"
    secondary_file="$OURS"
    secondary_start=$((ancestor_lines + 1))
    secondary_added="$ours_added"
    # Start with theirs as base since it's bigger
    head -n "$theirs_total" "$THEIRS" > "$OURS.tmp"
fi

# Find the max FL number in the primary file
max_fl=$(find_max_fl "$primary_file" 2>/dev/null || echo "0")

if [ "$max_fl" = "" ] || [ "$max_fl" = "0" ]; then
    # Fallback: can't parse FL numbers, bail to manual merge
    echo "fl-merge-driver: cannot determine FL numbering, falling back to manual merge" >&2
    exit 1
fi

# Extract the secondary tail
tail -n "$secondary_added" "$secondary_file" > "$OURS.secondary_tail"

# Renumber FL entries in the secondary tail
# Each FL-NNN in the secondary gets mapped to max_fl + offset
next_fl=$((max_fl + 1))

# Build sed commands for renumbering
# Find all FL numbers in the secondary tail and create a mapping
secondary_fls=$(extract_fl_numbers "$OURS.secondary_tail" | sort -nu || true)

sed_script=""
for old_fl in $secondary_fls; do
    # Replace FL-OLD with FL-NEW in headers and cross-references
    # Use [^0-9] boundary instead of \b for BSD sed compatibility
    new_fl="$next_fl"
    # Zero-padded form (FL-003 → FL-136)
    old_pad=$(printf '%03d' "$old_fl")
    new_pad=$(printf '%03d' "$new_fl")
    sed_script="${sed_script}s/FL-${old_pad}([^0-9])/FL-${new_pad}\1/g;"
    sed_script="${sed_script}s/FL-${old_pad}$/FL-${new_pad}/g;"
    # Bare form (FL-3 → FL-136)
    sed_script="${sed_script}s/FL-${old_fl}([^0-9])/FL-${new_fl}\1/g;"
    sed_script="${sed_script}s/FL-${old_fl}$/FL-${new_fl}/g;"
    next_fl=$((next_fl + 1))
done

if [ -n "$sed_script" ]; then
    sed -E "$sed_script" "$OURS.secondary_tail" > "$OURS.secondary_renumbered"
else
    cp "$OURS.secondary_tail" "$OURS.secondary_renumbered"
fi

# Build the merged result:
# primary file (ancestor prefix + primary tail) + blank line + renumbered secondary tail
if [ "$ours_added" -ge "$theirs_added" ]; then
    # OURS was primary — append renumbered secondary
    cat "$OURS" > "$OURS.merged"
    echo "" >> "$OURS.merged"
    cat "$OURS.secondary_renumbered" >> "$OURS.merged"
else
    # THEIRS was primary — we already started with theirs
    cat "$OURS.tmp" > "$OURS.merged"
    echo "" >> "$OURS.merged"
    cat "$OURS.secondary_renumbered" >> "$OURS.merged"
fi

# Write result back to OURS (git expects the result in %A)
mv "$OURS.merged" "$OURS"

# Cleanup temp files
rm -f "$OURS.tmp" "$OURS.secondary_tail" "$OURS.secondary_renumbered"

echo "fl-merge-driver: merged $(echo "$secondary_fls" | wc -w | tr -d ' ') entries from secondary branch (renumbered to FL-$((max_fl + 1))+)" >&2
exit 0

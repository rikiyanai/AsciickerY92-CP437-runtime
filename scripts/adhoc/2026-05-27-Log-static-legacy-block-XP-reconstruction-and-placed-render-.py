# Ad hoc script: Log static legacy block XP reconstruction and placed render failure investigation
# Created: 2026-05-27
# Canonical gap: legacy static prototype asset import lacks a dedicated verifier
# that proves source PNG cell-encoding, generated XP content, and runtime placed
# item render visibility agree.
#
# Investigation notes:
# - asciicker.com/yy/block.png is not a literal RGB sprite. The yy static
#   runtime decodes fmt1 cells as G=CP437 glyph, B low nibble=fg palette index,
#   B high nibble=bg palette index, and palette index 11 as transparent key.
# - The previous importer half-block-rasterized RGB pixels, producing the
#   flattened red/blue block artifact instead of the authored ASCII block.
# - The gameplay proof must separately prove visible placed rendering; server
#   placement state alone is not sufficient.

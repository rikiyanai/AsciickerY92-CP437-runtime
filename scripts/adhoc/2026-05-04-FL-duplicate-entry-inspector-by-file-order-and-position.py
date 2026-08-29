# Ad hoc script: FL duplicate entry inspector by file order and position
# Created: 2026-05-04
# Canonical gap: <describe what tool should own this>

#!/usr/bin/env python3
"""Inspect duplicate FL entry copies by file order and position."""
import re
import sys

path = sys.argv[1] if len(sys.argv) > 1 else 'docs/FAILURE_LOG.md'
fl_id = sys.argv[2] if len(sys.argv) > 2 else 'FL-2615'

with open(path) as f:
    content = f.read()

matches = list(re.finditer(rf'^### {fl_id}.*$', content, re.MULTILINE))
for i, m in enumerate(matches):
    end = content.find('\n### ', m.start() + 1)
    if end == -1:
        end = len(content)
    print(f'Copy {i+1} (pos {m.start()}): {m.group(0)}')

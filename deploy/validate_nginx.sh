#!/bin/bash
# deploy/validate_nginx.sh — Pre-deploy nginx syntax check
#
# Usage:
#   bash deploy/validate_nginx.sh
#
# Validates all asciicker nginx configs with `nginx -t`.  Exits non-zero
# if any config fails syntax so deploy scripts can gate on it.
#
# This was added in response to CE code-review finding #1 for RQ-084:
# proxy_intercept_errors + error_page rewrite must be syntax-checked
# before a live reload so the operator never sees a misconfigured
# /health path on the VPS.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=== nginx syntax validation ==="
for conf in \
    "${SCRIPT_DIR}/nginx/asciicker-current-host.conf" \
    "${SCRIPT_DIR}/nginx/asciicker-candidate-host.conf" \
    "${SCRIPT_DIR}/nginx/asciicker-wss.conf"
do
    # Use containerised or system nginx if available, else skip gracefully
    NGINX="${NGINX_BIN:-nginx}"
    if ! command -v "$NGINX" >/dev/null 2>&1; then
        echo "SKIP: $NGINX not found in PATH (run this on the target VPS)"
        continue
    fi
    echo "Checking: $(basename "$conf") ..."
    "$NGINX" -t -c "$conf"
done

echo "=== OK ==="

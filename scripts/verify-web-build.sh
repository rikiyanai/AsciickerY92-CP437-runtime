#!/bin/bash
# Auto-verifier for Asciicker web build
# Builds, serves, tests, and evaluates the web build
#
# Usage:
#   ./scripts/verify-web-build.sh              # Full build + verify
#   ./scripts/verify-web-build.sh --skip-build # Verify existing build
#   ./scripts/verify-web-build.sh --visual     # Include browser tests (needs playwright)
#
# Install playwright for visual tests:
#   npm install playwright
#   npx playwright install chromium

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
WEB_DIR="$PROJECT_DIR/.web"
PORT=8090
TIMEOUT=30
RESULTS_FILE="$PROJECT_DIR/.planning/verification-results.md"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

cleanup() {
    log_info "Cleaning up..."
    if [ ! -z "$SERVER_PID" ]; then
        kill $SERVER_PID 2>/dev/null || true
    fi
}
trap cleanup EXIT

# Parse arguments
SKIP_BUILD=false
VISUAL_TEST=false
while [[ "$#" -gt 0 ]]; do
    case $1 in
        --skip-build) SKIP_BUILD=true ;;
        --visual) VISUAL_TEST=true ;;
        --port) PORT="$2"; shift ;;
        -h|--help)
            echo "Usage: $0 [options]"
            echo "Options:"
            echo "  --skip-build  Skip the build step"
            echo "  --visual      Run visual tests (requires playwright)"
            echo "  --port PORT   Use specific port (default: 8090)"
            exit 0
            ;;
        *) log_error "Unknown option: $1"; exit 1 ;;
    esac
    shift
done

cd "$PROJECT_DIR"

echo "========================================"
echo "  Asciicker Web Build Verifier"
echo "========================================"
echo ""

TOTAL_CHECKS=0
PASSED_CHECKS=0
FAILED_CHECKS=()

record_check() {
    local name="$1"
    local result="$2"
    local details="$3"
    TOTAL_CHECKS=$((TOTAL_CHECKS + 1))
    if [ "$result" = "PASS" ]; then
        PASSED_CHECKS=$((PASSED_CHECKS + 1))
        log_info "CHECK: $name - ${GREEN}PASS${NC}"
    else
        FAILED_CHECKS+=("$name: $details")
        log_error "CHECK: $name - ${RED}FAIL${NC} ($details)"
    fi
}

# ============================================
# PHASE 1: BUILD
# ============================================
echo ""
log_info "=== PHASE 1: BUILD ==="

if [ "$SKIP_BUILD" = true ]; then
    log_warn "Skipping build (--skip-build)"
    if [ ! -f "$WEB_DIR/index.wasm" ]; then
        log_error "No existing build found at $WEB_DIR"
        exit 1
    fi
    record_check "Build exists" "PASS" ""
else
    log_info "Building web version..."
    BUILD_START=$(date +%s)

    if ./build-web.sh > /tmp/build-web.log 2>&1; then
        BUILD_END=$(date +%s)
        BUILD_TIME=$((BUILD_END - BUILD_START))
        record_check "Build compilation" "PASS" ""
        log_info "Build completed in ${BUILD_TIME}s"
    else
        record_check "Build compilation" "FAIL" "See /tmp/build-web.log"
        cat /tmp/build-web.log | tail -20
        exit 1
    fi
fi

# ============================================
# PHASE 2: STATIC CHECKS
# ============================================
echo ""
log_info "=== PHASE 2: STATIC CHECKS ==="

# Check required files exist
REQUIRED_FILES=("index.html" "index.js" "index.wasm" "index.data")
for file in "${REQUIRED_FILES[@]}"; do
    if [ -f "$WEB_DIR/$file" ]; then
        SIZE=$(du -h "$WEB_DIR/$file" | cut -f1)
        record_check "File: $file" "PASS" ""
        log_info "  $file: $SIZE"
    else
        record_check "File: $file" "FAIL" "Missing"
    fi
done

# Check WASM size (should be reasonable)
if [ -f "$WEB_DIR/index.wasm" ]; then
    WASM_SIZE=$(stat -f%z "$WEB_DIR/index.wasm" 2>/dev/null || stat -c%s "$WEB_DIR/index.wasm")
    if [ "$WASM_SIZE" -gt 100000 ]; then
        record_check "WASM size sanity" "PASS" ""
    else
        record_check "WASM size sanity" "FAIL" "Too small: $WASM_SIZE bytes"
    fi
fi

# Check for compile errors in build log
if [ -f "/tmp/build-web.log" ]; then
    # Use grep -c with explicit single-line handling
    ERROR_COUNT=$(grep -c "error:" /tmp/build-web.log 2>/dev/null | head -1 | tr -d '\n' || echo "0")
    WARN_COUNT=$(grep -c "warning:" /tmp/build-web.log 2>/dev/null | head -1 | tr -d '\n' || echo "0")
    # Ensure we have valid integers
    ERROR_COUNT=${ERROR_COUNT:-0}
    WARN_COUNT=${WARN_COUNT:-0}
    if [ "$ERROR_COUNT" = "0" ]; then
        record_check "No compile errors" "PASS" ""
    else
        record_check "No compile errors" "FAIL" "$ERROR_COUNT errors"
    fi
    log_info "Compile warnings: $WARN_COUNT"
fi

# ============================================
# PHASE 3: RUNTIME CHECKS
# ============================================
echo ""
log_info "=== PHASE 3: RUNTIME CHECKS ==="

# Kill any existing process on the port
EXISTING_PID=$(lsof -ti:$PORT 2>/dev/null || true)
if [ ! -z "$EXISTING_PID" ]; then
    log_warn "Killing existing process on port $PORT (PID: $EXISTING_PID)"
    kill $EXISTING_PID 2>/dev/null || true
    sleep 1
fi

# Start HTTP server
log_info "Starting HTTP server on port $PORT..."
cd "$WEB_DIR"
python3 -m http.server $PORT > /tmp/http-server.log 2>&1 &
SERVER_PID=$!
sleep 2

# Double-check server log for errors
if [ -f /tmp/http-server.log ]; then
    if grep -q "Address already in use" /tmp/http-server.log 2>/dev/null; then
        log_error "Port $PORT still in use. Try: --port XXXX"
        cat /tmp/http-server.log
    fi
fi

# Check server is running
if kill -0 $SERVER_PID 2>/dev/null; then
    record_check "HTTP server started" "PASS" ""
else
    record_check "HTTP server started" "FAIL" "Server died"
    exit 1
fi

# Test HTTP response
HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" "http://localhost:$PORT/index.html" 2>/dev/null || echo "000")
if [ "$HTTP_STATUS" = "200" ]; then
    record_check "HTTP 200 response" "PASS" ""
else
    record_check "HTTP 200 response" "FAIL" "Got $HTTP_STATUS"
fi

# Check HTML contains expected elements
HTML_CONTENT=$(curl -s "http://localhost:$PORT/index.html" 2>/dev/null)
if echo "$HTML_CONTENT" | grep -q "ASCIICKER"; then
    record_check "HTML contains ASCIICKER" "PASS" ""
else
    record_check "HTML contains ASCIICKER" "FAIL" "Title not found"
fi

if echo "$HTML_CONTENT" | grep -q "index.js"; then
    record_check "HTML references index.js" "PASS" ""
else
    record_check "HTML references index.js" "FAIL" "Script not found"
fi

# Check JS loads
JS_STATUS=$(curl -s -o /dev/null -w "%{http_code}" "http://localhost:$PORT/index.js" 2>/dev/null || echo "000")
if [ "$JS_STATUS" = "200" ]; then
    record_check "JS file accessible" "PASS" ""
else
    record_check "JS file accessible" "FAIL" "Got $JS_STATUS"
fi

# Check WASM loads
WASM_STATUS=$(curl -s -o /dev/null -w "%{http_code}" "http://localhost:$PORT/index.wasm" 2>/dev/null || echo "000")
if [ "$WASM_STATUS" = "200" ]; then
    record_check "WASM file accessible" "PASS" ""
else
    record_check "WASM file accessible" "FAIL" "Got $WASM_STATUS"
fi

# ============================================
# PHASE 4: BROWSER TESTS (if --visual)
# ============================================
if [ "$VISUAL_TEST" = true ]; then
    echo ""
    log_info "=== PHASE 4: VISUAL TESTS ==="

    log_warn "Visual test scripts removed (no screenshot-based testing). Use MP4 video path for human review."
fi

# ============================================
# PHASE 5: RESULTS
# ============================================
echo ""
echo "========================================"
echo "  VERIFICATION RESULTS"
echo "========================================"
echo ""

PASS_RATE=$((PASSED_CHECKS * 100 / TOTAL_CHECKS))

if [ ${#FAILED_CHECKS[@]} -eq 0 ]; then
    log_info "All checks passed! ($PASSED_CHECKS/$TOTAL_CHECKS)"
    OVERALL="PASS"
else
    log_error "Some checks failed ($PASSED_CHECKS/$TOTAL_CHECKS passed)"
    echo ""
    echo "Failed checks:"
    for fail in "${FAILED_CHECKS[@]}"; do
        echo "  - $fail"
    done
    OVERALL="FAIL"
fi

# Write results to file
mkdir -p "$(dirname "$RESULTS_FILE")"
cat > "$RESULTS_FILE" << EOF
# Verification Results

**Date:** $(date -Iseconds)
**Overall:** $OVERALL
**Pass Rate:** $PASS_RATE% ($PASSED_CHECKS/$TOTAL_CHECKS)

## Checks

| Check | Result |
|-------|--------|
EOF

# This would need the check names stored - simplified for now
echo "| Build & Static | $PASSED_CHECKS passed |" >> "$RESULTS_FILE"

if [ ${#FAILED_CHECKS[@]} -gt 0 ]; then
    echo "" >> "$RESULTS_FILE"
    echo "## Failed Checks" >> "$RESULTS_FILE"
    for fail in "${FAILED_CHECKS[@]}"; do
        echo "- $fail" >> "$RESULTS_FILE"
    done
fi

echo "" >> "$RESULTS_FILE"
echo "## Test Environment" >> "$RESULTS_FILE"
echo "- Port: $PORT" >> "$RESULTS_FILE"
echo "- Skip Build: $SKIP_BUILD" >> "$RESULTS_FILE"
echo "- Visual Tests: $VISUAL_TEST" >> "$RESULTS_FILE"

log_info "Results written to $RESULTS_FILE"

echo ""
if [ "$OVERALL" = "PASS" ]; then
    echo -e "${GREEN}BUILD INFRASTRUCTURE OK${NC}"
    echo ""
    echo -e "${YELLOW}NOTE: This only verifies build/serve infrastructure.${NC}"
    echo "To verify visually, test manually in browser at http://localhost:$PORT"
    echo ""
    exit 0
else
    echo -e "${RED}BUILD VERIFICATION FAILED${NC}"
    exit 1
fi

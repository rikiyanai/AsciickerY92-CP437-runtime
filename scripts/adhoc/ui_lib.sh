#!/usr/bin/env bash
# scripts/adhoc/ui_lib.sh — terminal UI primitives for shell scripts
#
# Original proposal: BFC-dbbc06c30259
# Source: claude session (codex history), unlinked
# Generalized: fixed leading whitespace from transcript extraction,
#              variable naming for better namespacing (_UI_R vs _R etc.)
#
# Usage: source scripts/adhoc/ui_lib.sh

# --- Color setup ---
_UI_R="" _UI_G="" _UI_Y="" _UI_B="" _UI_DIM="" _UI_BOLD="" _UI_RST=""
if [ "${CI:-}" != "1" ] && command -v tput &>/dev/null && [ "$(tput colors 2>/dev/null)" -ge 8 ]; then
    _UI_R=$(tput setaf 1)
    _UI_G=$(tput setaf 2)
    _UI_Y=$(tput setaf 3)
    _UI_B=$(tput setaf 4)
    _UI_DIM=$(tput dim)
    _UI_BOLD=$(tput bold)
    _UI_RST=$(tput sgr0)
fi

# --- Primitives ---
ui_header() { printf "\n${_UI_BOLD}══════════════════════════════════════${_UI_RST}\n  %s\n${_UI_BOLD}══════════════════════════════════════${_UI_RST}\n\n" "$1"; }
ui_step()   { printf "${_UI_BOLD}[%s/%s] %s${_UI_RST}\n" "$1" "$2" "$3"; }
ui_ok()     { printf "  ${_UI_G}✓${_UI_RST} %s\n" "$1"; }
ui_fail()   { printf "  ${_UI_R}✗${_UI_RST} %s\n" "$1"; }
ui_warn()   { printf "  ${_UI_Y}!${_UI_RST} %s\n" "$1"; }
ui_info()   { printf "  ${_UI_DIM}→${_UI_RST} %s\n" "$1"; }
ui_skip()   { printf "  ${_UI_DIM}─ %s (skipped — already done)${_UI_RST}\n" "$1"; }

ui_ask() {
    local prompt="$1" default="${2:-y}"
    if [ "${CI:-}" = "1" ]; then
        ui_info "$prompt → ${default} (CI mode)"
        [[ "$default" == "y" ]]
        return
    fi
    printf "  %s [%s]: " "$prompt" "$([ "$default" = "y" ] && echo "Y/n" || echo "y/N")"
    read -r reply
    reply="${reply:-$default}"
    [[ "$reply" =~ ^[Yy] ]]
}

# --- Spinner ---
_UI_SPIN_PID=""
_UI_SPIN_MSG=""
ui_spin_start() {
    local msg="$1"
    _UI_SPIN_MSG="$msg"
    if [ "${CI:-}" = "1" ]; then
        printf "  → %s\n" "$msg"
        return
    fi
    local frames=("⠸" "⠼" "⠴" "⠦" "⠧" "⠇" "⠏" "⠋" "⠙" "⠹")
    (
        i=0
        while true; do
            printf "\r  ${_UI_B}%s${_UI_RST} %s" "${frames[$((i % ${#frames[@]}))]}" "$_UI_SPIN_MSG"
            sleep 0.1
            ((i++))
        done
    ) &
    _UI_SPIN_PID=$!
}

ui_spin_stop() {
    local exit_code="${1:-0}"
    [ -z "$_UI_SPIN_PID" ] && return
    kill "$_UI_SPIN_PID" 2>/dev/null
    wait "$_UI_SPIN_PID" 2>/dev/null
    _UI_SPIN_PID=""
    printf "\r%-60s\r" " "
    if [ "$exit_code" -eq 0 ]; then
        ui_ok "$_UI_SPIN_MSG"
    else
        ui_fail "$_UI_SPIN_MSG (exit $exit_code)"
    fi
}

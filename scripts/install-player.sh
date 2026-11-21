#!/usr/bin/env bash
# Invoked by install-windows.ps1; all builds and data live in the user's Linux filesystem.
set -euo pipefail
user_phase=0
player_user=asciicker
source_dir=
source_ref=main
while (($#)); do
    case "$1" in
        --user-phase) user_phase=1; shift ;;
        --user) player_user="$2"; shift 2 ;;
        --source) source_dir="$2"; shift 2 ;;
        --ref) source_ref="$2"; shift 2 ;;
        *) printf 'Unknown installer option: %s\n' "$1" >&2; exit 2 ;;
    esac
done

if (( ! user_phase )); then
    [[ $EUID == 0 ]] || { echo 'System setup must run as root.' >&2; exit 1; }
    [[ "$player_user" =~ ^[a-z_][a-z0-9_-]*$ ]] || { echo 'Invalid Ubuntu username.' >&2; exit 1; }
    [[ $(uname -m) == x86_64 ]] || { echo 'This installer currently supports x64 Windows.' >&2; exit 1; }
    packages=(build-essential git ca-certificates pkg-config python3-venv python3-pip
              libnode-dev libsdl2-dev libpulse-dev libx11-dev libxinerama-dev libgl1-mesa-dev libgl1-mesa-dri)
    missing=()
    for package in "${packages[@]}"; do
        [[ $(dpkg-query -W -f='${db:Status-Status}' "$package" 2>/dev/null || true) == installed ]] || missing+=("$package")
    done
    if ((${#missing[@]})); then
        apt-get update -qq
        DEBIAN_FRONTEND=noninteractive apt-get install -y "${missing[@]}"
    fi
    if ! id "$player_user" >/dev/null 2>&1; then
        useradd --create-home --shell /bin/bash "$player_user"
    fi
    args=(--user-phase --ref "$source_ref")
    [[ -z "$source_dir" ]] || args+=(--source "$source_dir")
    exec runuser -u "$player_user" -- bash "$0" "${args[@]}"
fi

[[ $EUID != 0 ]] || { echo 'The player must be installed as a normal Ubuntu user.' >&2; exit 1; }
mkdir -p "$HOME/.local/share/asciicker" "$HOME/.local/bin"
exec 9>"$HOME/.local/share/asciicker/install.lock"
flock 9
if [[ -z "$source_dir" ]]; then
    source_dir="$HOME/.local/share/asciicker/source"
    if [[ ! -d "$source_dir/.git" ]]; then
        git clone --depth 1 --branch "$source_ref" https://github.com/rikiyanai/AsciickerY92-CP437-runtime.git "$source_dir"
    else
        git -C "$source_dir" diff --quiet && git -C "$source_dir" diff --cached --quiet || {
            echo 'The player source has local edits; leaving them intact. Resolve them before updating.' >&2; exit 1;
        }
        git -C "$source_dir" fetch origin "$source_ref"
        git -C "$source_dir" merge --ff-only FETCH_HEAD
    fi
fi
cd "$source_dir"
source_dir="$PWD"
[[ -f tools/player.py ]] || { echo 'The source directory does not contain the player installer.' >&2; exit 1; }
python3 -m venv .venv
.venv/bin/python -m pip install -q -r numabe/requirements-editor.txt
echo 'Building Classic and its local server (two compiler jobs)...'
make numabe-game NPROC=2 V8_FROM=system
make numabe-server NPROC=2
# The migrated workspace shares the runtime's sound bank.
mkdir -p numabe/assets/samples
for sound in forest footsteps jump attack hurt die; do
    [[ -f "numabe/assets/samples/$sound.ogg" ]] || cp "assets/samples/$sound.ogg" "numabe/assets/samples/$sound.ogg"
done
.venv/bin/python -X utf8 tools/numabe/run.py check
python3 tools/player.py --check
python3 - "$source_dir" <<'PY'
from pathlib import Path
import shlex
import sys
target = Path.home() / '.local/bin/asciicker'
entry = shlex.quote(str(Path(sys.argv[1]) / 'tools/player.py'))
target.write_text('#!/bin/sh\nexec python3 ' + entry + ' "$@"\n', encoding='utf-8')
target.chmod(0o755)
PY
echo 'Ready. Run asciicker to play, or asciicker launcher for the menu.'

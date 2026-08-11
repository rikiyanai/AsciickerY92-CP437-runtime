#!/bin/sh
set -eu

repo_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
build_dir="$repo_dir/build"
mkdir -p "$build_dir"

: "${CXX:=c++}"
"$CXX" -std=c++20 -O2 -Wall -Wextra -Wpedantic \
  "$repo_dir/src/xp_runtime.cpp" -lz -o "$build_dir/xp-runtime"
printf 'built %s\n' "$build_dir/xp-runtime"

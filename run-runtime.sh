#!/bin/sh
set -eu

repo_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
binary="$repo_dir/build/xp-runtime"
if [ ! -x "$binary" ]; then
  "$repo_dir/build.sh"
fi
exec "$binary" --root "$repo_dir" "$@"

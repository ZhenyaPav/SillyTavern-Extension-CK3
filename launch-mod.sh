#!/bin/sh
# Keep the caller's working directory: ./cream.sh and Steam arguments depend on it.
set -eu
bridge_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [ "$#" -eq 0 ]; then
    echo 'Usage: sh /path/to/launch-mod.sh [./cream.sh] <game command> [arguments...]' >&2
    exit 2
fi
exec python3 "$bridge_dir/companion/bridge.py" run "$@"

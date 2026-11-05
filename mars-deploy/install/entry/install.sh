#!/bin/sh
# MARS offline install (Linux x86_64, glibc 2.28+). No network access, no root needed.
set -e
ROOT="$(cd "$(dirname "$0")" && pwd)"
PY="$ROOT/runtime/python/bin/python3"
[ -x "$PY" ] || { mkdir -p "$ROOT/runtime"; tar -xzf "$ROOT/bundle/python.tar.gz" -C "$ROOT/runtime"; }
exec "$PY" -I "$ROOT/code/mars-deploy/install/install.py" "$@"

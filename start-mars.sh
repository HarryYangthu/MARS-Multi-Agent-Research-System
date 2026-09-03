#!/usr/bin/env bash
set -euo pipefail

MARS_LAUNCHER_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$MARS_LAUNCHER_ROOT/scripts/dev.sh" "$@"

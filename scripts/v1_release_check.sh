#!/usr/bin/env bash
# V1 release gate wrapper. This keeps the canonical release command stable even
# as scripts/acceptance.sh grows V1 coverage.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

bash scripts/acceptance.sh

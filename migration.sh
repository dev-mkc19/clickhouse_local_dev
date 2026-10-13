#!/bin/bash
# Thin wrapper: real logic lives in migration.py + tables.yaml
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
export TABLES_YAML="${TABLES_YAML:-$SCRIPT_DIR/tables.yaml}"

exec python3 "$SCRIPT_DIR/migration.py" "$@"

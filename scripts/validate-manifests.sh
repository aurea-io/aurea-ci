#!/usr/bin/env bash
set -euo pipefail
python3 "$(dirname "${BASH_SOURCE[0]}")/validate-manifests.py"
python3 "$(dirname "${BASH_SOURCE[0]}")/validate-architecture.py"

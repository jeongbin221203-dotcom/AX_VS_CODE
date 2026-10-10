#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"
exec python3 scripts/site.py --port 5091

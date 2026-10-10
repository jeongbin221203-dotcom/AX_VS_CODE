#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"
echo "Open http://127.0.0.1:5091 in your browser. Stop with Ctrl+C."
exec python3 -m http.server 5091 --bind 127.0.0.1

#!/usr/bin/env bash
# Jalankan Audio Vibe (bridge Spotify MPRIS) lalu buka browser.
set -euo pipefail

cd "$(dirname "$0")"

if ! python3 -c 'import gi; gi.require_version("Gio", "2.0")' >/dev/null 2>&1; then
    echo "PyGObject (python3-gi) belum terpasang." >&2
    echo "Ubuntu/Debian: sudo apt install python3-gi" >&2
    exit 1
fi

exec python3 server.py --open "$@"

#!/usr/bin/env sh
# KATALIS - jalankan dashboard.
# Mencoba beberapa interpreter dan memakai yang benar-benar bisa menjalankan Python.
set -e
cd "$(dirname "$0")"

for candidate in "./.venv/bin/python" "python3" "python"; do
  if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c "import sys" >/dev/null 2>&1; then
    exec "$candidate" app.py "$@"
  fi
done

cat >&2 <<'MSG'

  Python 3.10+ tidak ditemukan.

  Pasang dari python.org atau manajer paket sistem Anda, lalu jalankan lagi:
      python3 app.py

MSG
exit 1

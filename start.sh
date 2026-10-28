#!/usr/bin/env bash
# Linux/macOS için start.bat karşılığı: sanal ortam + paketler + sunucu.
set -e
cd "$(dirname "$0")"
PY=${PY:-python3}
[ -x .venv/bin/python ] || "$PY" -m venv .venv
[ -f .venv/kurulum_tamam.txt ] || { .venv/bin/python -m pip install --disable-pip-version-check -r requirements-lock.txt && echo ok > .venv/kurulum_tamam.txt; }
exec .venv/bin/python -m backend.run "$@"

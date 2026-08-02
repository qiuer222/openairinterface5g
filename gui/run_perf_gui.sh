#!/usr/bin/env bash
set -e
cd "$(dirname "$0")/.."
if [ -d .venv ]; then
  source .venv/bin/activate
fi
if [ "$XDG_SESSION_TYPE" = "wayland" ] && [ -z "$QT_QPA_PLATFORM" ]; then
  export QT_QPA_PLATFORM=wayland
fi
exec python3 -m gui.oai_perf_monitor "$@"

#!/usr/bin/env bash
set -e
cd "$(dirname "$0")/.."
if [ -d .venv ]; then
  source .venv/bin/activate
fi

ensure_xcb_runtime() {
  command -v ldd >/dev/null 2>&1 || return 0
  plugin=$(python3 - <<'PY' 2>/dev/null || true
from PyQt5.QtCore import QLibraryInfo
import os
print(os.path.join(QLibraryInfo.location(QLibraryInfo.PluginsPath), "platforms", "libqxcb.so"))
PY
)
  [ -n "$plugin" ] && [ -f "$plugin" ] || return 0
  ldd "$plugin" 2>/dev/null | grep -q "not found" || return 0

  if [ "$(id -u)" -eq 0 ]; then
    if ! command -v apt-get >/dev/null 2>&1; then
      echo "Qt xcb is missing shared libraries." >&2
      return 1
    fi
    DEBIAN_FRONTEND=noninteractive apt-get install -y libxcb-xinerama0 libxcb-cursor0
    if ldd "$plugin" 2>/dev/null | grep -q "not found"; then
      return 1
    fi
    return 0
  fi

  cache="${XDG_CACHE_HOME:-$HOME/.cache}/oai-gui-qt-xcb"
  libdir="$cache/lib"
  if [ -f "$libdir/libxcb-xinerama.so.0" ] && [ -f "$libdir/libxcb-cursor.so.0" ]; then
    if ! LD_LIBRARY_PATH="$libdir" ldd "$plugin" 2>/dev/null | grep -q "not found"; then
      export LD_LIBRARY_PATH="$libdir${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
      return 0
    fi
    rm -rf "$cache"
  fi

  if ! command -v apt-get >/dev/null 2>&1 || ! command -v dpkg-deb >/dev/null 2>&1; then
    echo "Qt xcb is missing shared libraries." >&2
    echo "Install them with: sudo apt install libxcb-xinerama0 libxcb-cursor0" >&2
    return 1
  fi

  tmp="$(mktemp -d)"
  trap 'rm -rf "$tmp"' EXIT
  echo "Downloading missing Qt xcb libraries..." >&2
  (cd "$tmp" && apt-get download libxcb-xinerama0 libxcb-cursor0 >/dev/null)
  mkdir -p "$tmp/root" "$libdir"
  for deb in "$tmp"/*.deb; do
    dpkg-deb -x "$deb" "$tmp/root"
  done
  multiarch="$(dpkg-architecture -qDEB_HOST_MULTIARCH 2>/dev/null || echo x86_64-linux-gnu)"
  src="$tmp/root/usr/lib/$multiarch"
  cp "$src"/libxcb-xinerama* "$libdir/"
  cp "$src"/libxcb-cursor* "$libdir/"
  trap - EXIT
  rm -rf "$tmp"

  if LD_LIBRARY_PATH="$libdir" ldd "$plugin" 2>/dev/null | grep -q "not found"; then
    return 1
  fi
  export LD_LIBRARY_PATH="$libdir${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
}

ensure_xcb_runtime

if [ -z "$XDG_RUNTIME_DIR" ] || [ ! -d "$XDG_RUNTIME_DIR" ]; then
  export XDG_RUNTIME_DIR="/tmp/runtime-$(id -u)"
  mkdir -p "$XDG_RUNTIME_DIR"
  chmod 700 "$XDG_RUNTIME_DIR"
fi

if [ "$XDG_SESSION_TYPE" = "wayland" ] && [ -z "$QT_QPA_PLATFORM" ]; then
  export QT_QPA_PLATFORM=wayland
fi
exec python3 -m gui.oai_gnb_monitor "$@"

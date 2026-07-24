#!/usr/bin/env bash

# Keep Obsidian's bundled Electron libraries ahead of host libraries while
# exposing the small set of libraries Chromium loads dynamically.
export LD_LIBRARY_PATH="@out@/libexec/obsidian:@dynamic-library-path@:/run/opengl-driver/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

wayland_socket=""
if [[ -n ${WAYLAND_DISPLAY:-} ]]; then
  if [[ $WAYLAND_DISPLAY = /* ]]; then
    wayland_socket="$WAYLAND_DISPLAY"
  else
    wayland_socket="${XDG_RUNTIME_DIR:-/run/user/$UID}/$WAYLAND_DISPLAY"
  fi
fi

# Prefer native Wayland for Niri and Fcitx5. OBSIDIAN_USE_X11=1 provides an
# explicit escape hatch, and a stale WAYLAND_DISPLAY falls back automatically.
if [[ ${OBSIDIAN_USE_X11:-0} != 1 && -n $wayland_socket && -S $wayland_socket ]]; then
  exec "@out@/libexec/obsidian/obsidian" \
    --ozone-platform=wayland \
    --enable-wayland-ime=true \
    --wayland-text-input-version=3 \
    "$@"
else
  exec "@out@/libexec/obsidian/obsidian" "$@"
fi

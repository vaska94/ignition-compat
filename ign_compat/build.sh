#!/usr/bin/env bash
# Build the Ignition compatibility layer.
# 32-bit, because Ign_win.exe is 32-bit and these load into its process.
set -euo pipefail
cd "$(dirname "$0")"

CC=${CC:-i686-w64-mingw32-gcc}
OUT=build
CFLAGS="-O2 -Wall -Wextra -Wno-unused-parameter -DWIN32_LEAN_AND_MEAN"
LDCOMMON="-static-libgcc -Wl,--enable-stdcall-fixup -Wl,--kill-at"
mkdir -p "$OUT"

echo "==> common"
$CC $CFLAGS -c -o "$OUT/ignlog.o"        src/common/ignlog.c
$CC $CFLAGS -c -o "$OUT/iathook.o"       src/common/iathook.c
$CC $CFLAGS -c -o "$OUT/detour.o"        src/common/detour.c
$CC $CFLAGS -c -o "$OUT/gametrace.o"     src/common/gametrace.c
$CC $CFLAGS -c -o "$OUT/d3d11_present.o" src/common/d3d11_present.c

build_dll () {
  local mod=$1; shift
  compgen -G "src/$mod/*.c" > /dev/null || { echo "    (skip $mod - no sources)"; return; }
  echo "==> $mod.dll"
  local def=""
  [ -f "src/$mod/$mod.def" ] && def="src/$mod/$mod.def"
  # shellcheck disable=SC2086
  $CC $CFLAGS -shared -o "$OUT/$mod.dll" src/$mod/*.c $def $LDCOMMON "$@"
}

build_dll dplayx "$OUT/ignlog.o"
build_dll ddraw  "$OUT/ignlog.o" "$OUT/iathook.o" "$OUT/detour.o" "$OUT/gametrace.o" "$OUT/d3d11_present.o" -ld3d11 -ldxgi -luuid -lole32 -lgdi32 -lwinmm
build_dll dsound "$OUT/ignlog.o" -lole32 -luuid
build_dll dinput "$OUT/ignlog.o" -lole32 -luuid

echo "==> tests"
$CC $CFLAGS -o "$OUT/detour_test.exe" tests/detour_test.c "$OUT/detour.o" -static-libgcc

echo "==> built:"
ls -la "$OUT"/*.dll 2>/dev/null || echo "   (none yet)"

#!/usr/bin/env bash
# Clean, reproducible firmware build -> firmware/dist/{dc32_display.uf2,.elf,.bin,.sha256,BUILDINFO.txt}
# Needs: cmake, ninja, gcc-arm-none-eabi, git, python3. Fetches pico-sdk 2.2.0 if PICO_SDK_PATH unset.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [ -z "${PICO_SDK_PATH:-}" ]; then
  PICO_SDK_PATH="$ROOT/.deps/pico-sdk"
  if [ ! -d "$PICO_SDK_PATH" ]; then
    git clone -q --depth 1 -b 2.2.0 https://github.com/raspberrypi/pico-sdk "$PICO_SDK_PATH"
    git -C "$PICO_SDK_PATH" submodule update --init --depth 1 lib/tinyusb
  fi
fi
export PICO_SDK_PATH
B="$ROOT/firmware/build"
rm -rf "$B"
cmake -S "$ROOT/firmware" -B "$B" -G Ninja -DCMAKE_BUILD_TYPE=Release >/dev/null
ninja -C "$B"
D="$ROOT/firmware/dist"
mkdir -p "$D"
cp "$B/dc32_display.uf2" "$B/dc32_display.elf" "$B/dc32_display.bin" "$D/"
(cd "$D" && sha256sum dc32_display.uf2 > dc32_display.sha256 && sha256sum dc32_display.bin dc32_display.elf >> dc32_display.sha256)
{
  echo "built: $(date -u +%FT%TZ)"
  echo "git: $(git -C "$ROOT" rev-parse HEAD 2>/dev/null || echo none)"
  echo "pico-sdk: $(git -C "$PICO_SDK_PATH" describe --tags 2>/dev/null || echo unknown)"
  echo "toolchain: $(arm-none-eabi-gcc --version | head -1)"
  (cd "$ROOT" && arm-none-eabi-size firmware/build/dc32_display.elf)
} > "$D/BUILDINFO.txt"
# static checks: native decoder/encoder round-trip tests
python3 "$ROOT/tests/test_protocol.py"
cat "$D/dc32_display.sha256"

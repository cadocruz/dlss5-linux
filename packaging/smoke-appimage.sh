#!/usr/bin/env bash
# Run the built file. This is the only test of the packaging itself: the suite
# tests the checkout, and the checkout is not what anybody downloads. A bundle
# can be green in all 250 of them and still not start.
#
#   bash packaging/smoke-appimage.sh ./dlss5-linux-1.9.0+linux0.2-x86_64.AppImage
set -euo pipefail

APP="${1:?usage: smoke-appimage.sh <file.AppImage>}"
APP="$(readlink -f "$APP")"
chmod +x "$APP"

# Current distributions ship fuse3, not the libfuse2 the AppImage runtime
# wants, and a CI container has no FUSE at all.
export APPIMAGE_EXTRACT_AND_RUN=1

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
GAME="$WORK/game/Binaries/Win64"
mkdir -p "$GAME"

# A minimal 64-bit PE, written in shell rather than with python3: the point of
# this file is that it carries its own interpreter, so the test of it should
# not need one on the machine. MZ, e_lfanew at 0x3C pointing at 0x40, the PE
# signature and a COFF Machine of 0x8664.
{
    printf 'MZ'
    printf '\0%.0s' {1..58}
    printf '\x40\x00\x00\x00'
    printf 'PE\x00\x00\x64\x86'
    printf '\0%.0s' {1..96}
} > "$GAME/Fixture-Win64-Shipping.exe"

say() { printf '\n== %s\n' "$*"; }

say "the command line answers"
"$APP" --cli --help > /dev/null

say "detection, with no Steam and no GPU"
"$APP" --cli check "$GAME"

say "diagnosis of a folder with nothing installed"
"$APP" --cli verify "$GAME"

say "the out-of-process route reports its state"
"$APP" --cli vklayer status > /dev/null

# The window. Qt modules are pruned from the bundle by name, and a list like
# that is only safe because something starts the real thing and finds out.
say "the window builds offscreen"
QT_QPA_PLATFORM=offscreen timeout 40 "$APP" > "$WORK/gui.log" 2>&1 &
gui=$!
sleep 12
if kill -0 "$gui" 2>/dev/null; then
    kill "$gui" 2>/dev/null || true
    wait "$gui" 2>/dev/null || true
    echo "   still up after 12s"
else
    wait "$gui" 2>/dev/null && rc=0 || rc=$?
    echo "   exited early (rc=${rc:-0})"
    cat "$WORK/gui.log"
    exit 1
fi

# What it must and must not carry.
say "the bundle's contents"
cd "$WORK"
"$APP" --appimage-extract > /dev/null
R=squashfs-root
test -x "$R"/usr/bin/7zz -o -x "$R"/usr/bin/7zzs || { echo "no bundled 7-Zip"; exit 1; }
test -f "$R/usr/app/vklayer-run"                 || { echo "no vklayer-run"; exit 1; }
if find "$R" \( -name 'libGL*.so*' -o -name 'libEGL*.so*' -o -name 'libvulkan*.so*' \
   -o -name 'libgbm*.so*' -o -name 'libdrm*.so*' \) | grep -q .; then
    echo "a driver library got swept in; those must come from the machine"; exit 1
fi
echo "   7-Zip, vklayer-run, and no driver libraries"
echo "   unpacked $(du -sh "$R" | cut -f1), file $(du -h "$APP" | cut -f1)"

printf '\nok\n'

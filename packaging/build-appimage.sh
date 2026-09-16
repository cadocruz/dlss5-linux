#!/usr/bin/env bash
# Build dlss5-linux as a single runnable file.
#
# What goes in, and why it is this and not PyInstaller:
#
#   usr/python   python-build-standalone. Its glibc floor is 2.17, so the
#                build host stops deciding who can run the result, and
#                sys.frozen stays False - core/prefs.py, core/log.py and
#                core/selfupdate.py all read it and all mean "the Windows exe"
#                by it. PyInstaller would also set LD_LIBRARY_PATH inside the
#                process, which every subprocess inherits: protontricks runs
#                on the host's python and would load this bundle's libcrypto.
#                Upstream already carries a scar from the same mechanism -
#                core/selfupdate.py's clean_env() strips _PYI_* for it.
#
#   usr/app      the dlss5-linux/ tree, copied. The import graph at runtime is
#                then exactly the one the tests test, with no hidden-import
#                audit owed on every upstream re-vendor - and core/gui.py and
#                three more modules import tkinter at the top, which a
#                bytecode scan would collect along with Tcl/Tk.
#
#   usr/bin/7zz  7-Zip, static. SteamOS ships none, and the OptiScaler
#                nightlies are .7z: without this the route that works best
#                under Proton is the one that cannot be installed there.
#
# Both downloads are pinned by sha256 in packaging/pins.lock.
#
#   bash packaging/build-appimage.sh            build
#   OUT=/tmp/x.AppImage bash packaging/...      somewhere else
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
WORK="${WORK:-$ROOT/build}"
CACHE="${CACHE:-$WORK/cache}"
APPDIR="$WORK/AppDir"
# Only used to drive the build; the interpreter that ships is the pinned one.
PYTHON="${PYTHON:-}"
if [ -z "$PYTHON" ]; then
    for c in python3 python3.12 python3.11 python; do
        if command -v "$c" >/dev/null 2>&1; then PYTHON="$c"; break; fi
    done
fi
if ! command -v "${PYTHON:-}" >/dev/null 2>&1; then
    echo "no python3 on PATH to build with; set PYTHON=/path/to/python3" >&2
    exit 1
fi

VERSION="$("$PYTHON" - <<'PY'
import pathlib, re
src = pathlib.Path("dlss5-linux/linuxport/features.py").read_text(encoding="utf8")
port = re.search(r'PORT_VERSION\s*=\s*"([^"]+)"', src).group(1)
core = pathlib.Path("dlss5-linux/core/update.py").read_text(encoding="utf8")
print(f"{re.search(r'VERSION\s*=\s*.([0-9][^\"\x27]*)', core).group(1)}+linux{port}")
PY
)"
OUT="${OUT:-$ROOT/dlss5-linux-${VERSION}-x86_64.AppImage}"
echo "==> version ${VERSION}"

cd "$ROOT"
rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr" "$CACHE"

# --- the two pinned downloads ---------------------------------------------------------
mapfile -t PINNED < <("$PYTHON" packaging/resolve_pins.py --fetch "$CACHE")
PY_TGZ="${PINNED[0]}"
SZ_TXZ="${PINNED[1]}"
echo "==> python  $(basename "$PY_TGZ")"
echo "==> 7-zip   $(basename "$SZ_TXZ")"

tar -xzf "$PY_TGZ" -C "$APPDIR/usr"          # unpacks as usr/python/
test -x "$APPDIR/usr/python/bin/python3"

mkdir -p "$APPDIR/usr/bin"
tar -xJf "$SZ_TXZ" -C "$APPDIR/usr/bin" 7zzs 2>/dev/null || tar -xJf "$SZ_TXZ" -C "$APPDIR/usr/bin" 7zz
chmod 755 "$APPDIR"/usr/bin/7zz* 2>/dev/null || true

# --- the third-party libraries --------------------------------------------------------
# Into the bundled interpreter's own site-packages, so AppRun needs no
# PYTHONPATH: -I keeps the user's environment out but still uses these.
SITE="$(echo "$APPDIR"/usr/python/lib/python3.*/site-packages)"
"$APPDIR/usr/python/bin/python3" -m pip install --quiet --no-compile \
    --target "$SITE" --upgrade -r requirements.txt

# Nothing installs anything at run time, and the bundle is read-only anyway.
rm -rf "$SITE"/pip "$SITE"/pip-*.dist-info "$SITE"/setuptools "$SITE"/setuptools-*.dist-info \
       "$SITE"/pkg_resources "$SITE"/_distutils_hack "$SITE"/distutils-precedence.pth

# Qt modules this application does not import. It uses QtCore, QtGui and
# QtWidgets; Essentials still carries a QML engine, the Quick scenegraph, the
# Designer libraries and the shader tools, and they are a third of what is
# left. Pruned by name, then proven by starting the real window offscreen -
# a list like this is only safe because something checks it.
QT="$SITE/PySide6/Qt"
for m in Qml QmlCompiler QmlLocalStorage QmlMeta QmlModels QmlWorkerScript QmlXmlListModel \
         Quick Quick3D QuickControls2 QuickDialogs2 QuickLayouts QuickParticles \
         QuickShapes QuickTemplates2 QuickTest QuickWidgets QuickTimeline \
         Designer DesignerComponents Help Test ShaderTools Sql \
         Multimedia MultimediaWidgets Bluetooth Nfc Positioning SerialPort \
         WebSockets WebChannel WebChannelQuick Charts DataVisualization Pdf PdfWidgets; do
    rm -f "$QT/lib/libQt6${m}.so"* "$SITE/PySide6/Qt${m}.abi3.so" \
          "$SITE/PySide6/Qt${m}.pyi" 2>/dev/null || true
    rm -rf "$QT/qml" 2>/dev/null || true
done
rm -rf "$QT/translations" "$SITE/PySide6/scripts" "$SITE/PySide6/glue" \
       "$SITE/PySide6/include" "$SITE/shiboken6/include" 2>/dev/null || true
find "$SITE" -name '*.pyi' -delete 2>/dev/null || true

# Plugins for things this is not. They are dlopen'd on demand and would never
# be opened, but each one carries its own NEEDED list - the SQL drivers alone
# ask the host for libmysqlclient, libpq, libodbc and libmimerapi, none of
# which anybody should have to install to run a DLSS installer. Removing the
# QtSql library and leaving its drivers behind, which is what this did first,
# is the worst of both.
rm -rf "$QT/plugins/sqldrivers" "$QT/plugins/egldeviceintegrations" \
       "$QT/plugins/platformthemes" "$QT/plugins/designer" \
       "$QT/plugins/qmltooling" 2>/dev/null || true
# Wayland: the CLIENT side stays, because people run Wayland. What goes is the
# compositor - this application is not one.
rm -f "$QT"/lib/libQt6WaylandCompositor.so* "$QT"/lib/libQt6WaylandEgl*.so* 2>/dev/null || true
rm -rf "$QT/plugins/wayland-graphics-integration-server" 2>/dev/null || true

# Qt 6.5+ wants libxcb-cursor at runtime and the wheels do not carry it. Beside
# the other Qt libraries, where the platform plugin's RUNPATH ($ORIGIN/../../lib)
# finds it - no environment variable, so nothing leaks to a Qt child.
for lib in libxcb-cursor.so.0 libxcb-xinerama.so.0; do
    src="$(ldconfig -p 2>/dev/null | awk -v l="$lib" '$1==l {print $NF; exit}')" || true
    if [ -n "${src:-}" ] && [ -f "$src" ]; then
        cp -L "$src" "$SITE/PySide6/Qt/lib/" && echo "==> bundled $lib"
    else
        echo "!!  $lib not on this machine; install libxcb-cursor0 for a complete build" >&2
    fi
done

# python-build-standalone ships libpython with its debug symbols: 209 MB of
# them, against 30 MB of library. They are not what a traceback is made of -
# that comes from the code objects, and it prints the same function names and
# line numbers either way, which was checked rather than assumed. They would
# only matter for a gdb backtrace through a C-level crash in the interpreter,
# which is not a thing this tool asks anybody to produce.
find "$APPDIR/usr/python" -name '*.so' -o -name '*.so.*' | while read -r so; do
    strip --strip-unneeded "$so" 2>/dev/null || true
done

# Never ours to ship: these must come from the machine's NVIDIA driver.
find "$APPDIR" \( -name 'libGL*.so*' -o -name 'libEGL*.so*' -o -name 'libvulkan*.so*' \
    -o -name 'libgbm*.so*' -o -name 'libdrm*.so*' \) -delete

# --- the application ------------------------------------------------------------------
mkdir -p "$APPDIR/usr/app"
cp -r dlss5-linux/. "$APPDIR/usr/app/"
rm -rf "$APPDIR/usr/app/__pycache__" "$APPDIR/usr/app"/*/__pycache__ "$APPDIR/usr/app/docs"
chmod 755 "$APPDIR/usr/app/vklayer-run"
# A CRLF checkout - what a Windows clone does unless .gitattributes says
# otherwise - turns the shebang into "#!/usr/bin/env bash\r" and the kernel
# answers "bad interpreter". Steam runs this in front of the game, so the
# failure would land on somebody who just pressed play. The first build here
# shipped exactly that, and nothing noticed, because the check was test -f.
if head -c 200 "$APPDIR/usr/app/vklayer-run" | grep -q $'\r'; then
    echo "vklayer-run has CRLF line endings; it will not run on Linux." >&2
    echo "Your checkout predates .gitattributes: rm it and git checkout -- it." >&2
    exit 1
fi
cp LICENSE "$APPDIR/usr/app/" 2>/dev/null || true
cp dlss5-linux/LICENSE.upstream "$APPDIR/usr/app/" 2>/dev/null || true

# Precompiled here, because the squashfs is read-only at runtime: without this
# every launch recompiles and silently fails to cache the result.
"$APPDIR/usr/python/bin/python3" -m compileall -q "$APPDIR/usr/app" >/dev/null || true

# --- desktop integration --------------------------------------------------------------
install -Dm755 packaging/AppRun              "$APPDIR/AppRun"
install -Dm644 packaging/dlss5-linux.desktop "$APPDIR/dlss5-linux.desktop"
install -Dm644 packaging/dlss5-linux.png     "$APPDIR/dlss5-linux.png"
install -Dm644 packaging/dlss5-linux.desktop "$APPDIR/usr/share/applications/dlss5-linux.desktop"
install -Dm644 packaging/dlss5-linux.png \
    "$APPDIR/usr/share/icons/hicolor/256x256/apps/dlss5-linux.png"
sed "s/@VERSION@/${VERSION}/" packaging/dlss5-linux.metainfo.xml \
    > "$APPDIR/usr/share/metainfo/io.github.pantsoftime.dlss5-linux.metainfo.xml" 2>/dev/null \
    || install -Dm644 packaging/dlss5-linux.metainfo.xml \
        "$APPDIR/usr/share/metainfo/io.github.pantsoftime.dlss5-linux.metainfo.xml"

# --- pack -----------------------------------------------------------------------------
TOOL="$CACHE/appimagetool-x86_64.AppImage"
if [ ! -x "$TOOL" ]; then
    curl -fsSL -o "$TOOL" \
      https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage
    chmod 755 "$TOOL"
fi

# --runtime-file with the static runtime: the default one needs libfuse2, which
# current distributions do not ship, and an AppImage that will not open is
# worse than no AppImage. --appimage-extract-and-run because the tool is itself
# an AppImage and CI containers have no FUSE either.
RUNTIME="$CACHE/runtime-x86_64"
if [ ! -f "$RUNTIME" ]; then
    curl -fsSL -o "$RUNTIME" \
      https://github.com/AppImage/type2-runtime/releases/download/continuous/runtime-x86_64
fi

UPDATE_INFO="gh-releases-zsync|${GH_OWNER:-pantsoftime}|${GH_REPO:-dlss5-linux}|latest|dlss5-linux-*-x86_64.AppImage"
APPIMAGE_EXTRACT_AND_RUN=1 ARCH=x86_64 "$TOOL" \
    --runtime-file "$RUNTIME" \
    --updateinformation "$UPDATE_INFO" \
    "$APPDIR" "$OUT"

echo
echo "==> $OUT"
ls -lh "$OUT" | awk '{print "    " $5}'
sha256sum "$OUT"

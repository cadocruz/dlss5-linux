r"""Files that ship inside the package and cannot be used from where they land.

An AppImage is mounted read-only at /tmp/.mount_XXXXXX, and that path is
different on every run. Anything the tool only reads is fine there. Two things
are not:

  vklayer-run   `launch-options --vklayer --apply` writes its path into Steam's
                localconfig.vdf, which outlives the mount by months. Left
                pointing inside the AppImage, the wrapper is gone by the next
                time Steam launches the game, and the launch simply fails.

  7zz           SteamOS and the other immutable distributions ship no 7z, and
                archive.py's advice when it finds none - apt/dnf/pacman - is
                advice you cannot take on a read-only root. Carrying a static
                build is the difference between the OptiScaler route working
                there and not.

So: the wrapper is copied out to a stable place under XDG on first use, and the
bundled 7z is offered to archive.py ahead of whatever the system has.

Running from a git checkout none of this applies, and every function here
answers with the path in the tree, unchanged.
"""
from __future__ import annotations

import os
import shutil
import stat
import tempfile
from pathlib import Path

from . import paths, vklayer

# Set by the AppImage runtime for the process it starts. Absent in a checkout.
APPDIR = os.environ.get("APPDIR")

# Beside the package, which inside the AppImage is $APPDIR/usr/app.
_TREE = Path(__file__).resolve().parents[1]

WRAPPER_NAME = "vklayer-run"


def packaged() -> bool:
    return bool(APPDIR) and _TREE.is_relative_to(APPDIR)


def _copy_out(src: Path, dst: Path, mode: int) -> Path:
    """Put src at dst, atomically, and only when the bytes differ.

    Replace by rename rather than writing in place: bash reads a running script
    from its descriptor as it goes, and a game session may be in the middle of
    the wrapper right now. os.replace swaps the directory entry and leaves the
    open file alone.
    """
    try:
        if dst.is_file() and dst.read_bytes() == src.read_bytes():
            return dst
        dst.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(dst.parent), prefix=f".{dst.name}.")
        os.close(fd)
        shutil.copyfile(src, tmp)
        os.chmod(tmp, mode)
        os.replace(tmp, dst)
    except OSError:
        return src              # better the read-only copy than no copy
    return dst


def wrapper() -> Path:
    r"""Where vklayer-run can be found by a path that will still be there.

    The basename stays exactly `vklayer-run`: vklayer.enabled_in matches
    `(^|[\s/])vklayer-run(\s|$)`, so any prefix on the name - dlss5-vklayer-run,
    say - makes a correctly wrapped game read as "layer off", while the
    substring checks elsewhere in that module still match. Silently.
    """
    src = _TREE / WRAPPER_NAME
    if not packaged() or not src.is_file():
        return src
    return _copy_out(src, paths.DATA / "bin" / WRAPPER_NAME,
                     stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH)


def seven_zip() -> Path | None:
    """The 7z carried in the package, if this build carries one."""
    if not APPDIR:
        return None
    for name in ("7zz", "7zzs", "7zr"):
        p = Path(APPDIR) / "usr" / "bin" / name
        if p.is_file() and os.access(p, os.X_OK):
            return p
    return None


def install() -> None:
    vklayer.WRAPPER = wrapper()

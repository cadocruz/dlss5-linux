r"""core.optiscaler's .7z fallback, without Windows' tar.exe.

Upstream unpacks a .7z with 7-Zip when the machine has it, and falls back to
%SystemRoot%\System32\tar.exe -- the bsdtar Windows has shipped since 1803.
That path does not exist here, so an OptiScaler fork that publishes a .7z
asset (archive_name() accepts .7z and .zip alike) ended in

    "... and this Windows has no tar.exe either (Windows 10 1803 and later
     ship one at C:\Windows\System32\tar.exe). Install 7-Zip ..."

which names a directory the person does not have and software they cannot
install. Linux has the same bsdtar under its own name, so the fallback is
kept and only the executables change: p7zip first (upstream's _seven_zip()
already finds 7z / 7za / 7zr on PATH), then bsdtar, then plain tar -- which
IS bsdtar on a few distributions and GNU tar, no .7z, on most. GNU tar fails
cleanly and its message ends up in the error below with the others.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from core import optiscaler as _opti

TARS = ("bsdtar", "tar")


def commands(archive: Path, dest: Path) -> list[list[str]]:
    """Every way this machine could open a .7z, best first."""
    out: list[list[str]] = []
    sz = _opti._seven_zip()
    if sz is not None:
        out.append([str(sz), "x", str(archive), f"-o{dest}", "-y"])
    for name in TARS:
        exe = shutil.which(name)
        if exe:
            out.append([exe, "-xf", str(archive), "-C", str(dest)])
    return out


def extract_7z(archive: Path, dest: Path) -> None:
    archive, dest = Path(archive), Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    cmds = commands(archive, dest)
    if not cmds:
        raise RuntimeError(
            f"{archive.name} is a .7z archive and nothing here opens one. "
            f"Install p7zip (7z) or libarchive (bsdtar) -- on Debian/Ubuntu "
            f"`apt install p7zip-full`, on Fedora `dnf install p7zip`, on "
            f"Arch `pacman -S p7zip` -- and run the install again.")
    tried: list[str] = []
    for cmd in cmds:
        try:
            r = subprocess.run(cmd, capture_output=True, text=True)
        except OSError as e:
            tried.append(f"{Path(cmd[0]).name}: {e}")
            continue
        if r.returncode == 0:
            return
        detail = (r.stderr or r.stdout).strip()[-200:] or f"exit {r.returncode}"
        tried.append(f"{Path(cmd[0]).name}: {detail}")
    raise RuntimeError(f"could not unpack {archive.name}:\n  "
                       + "\n  ".join(tried))


def install() -> None:
    _opti.extract_7z = extract_7z

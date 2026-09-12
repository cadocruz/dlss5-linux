"""Stage archives the person already holds under the installer's cache names.

net.download() returns a cached file without fetching, so anything placed
in the cache under the exact name the installer asks for is used as-is. That
saves the 165 MB nvngx_dlssnr pull and lets an install run offline.

The source is the components directory (paths.components_dir(): $DLSS5_COMPONENTS_DIR,
default ~/.local/share/dlss5-linux/components). Two kinds of file are picked up:

* archives whose name already IS a cache name (the installer's
  `<tag>-<asset>.zip` form) are copied as they are;
* the known bundles below are renamed to what the installer asks for;
* a zip containing nvngx_dlssnr.dll is repacked as dlssnr-<version>.zip, the
  name the installer looks for, with the version read from the zip's name.
"""
from __future__ import annotations

import re
import shutil
import zipfile
from pathlib import Path

from core import net
from . import paths

# (file name in the components dir, cache name the installer asks for)
RENAMES = (
    ("OptiScaler-DLSSNR-v0.1.2.zip", "v0.1.2-dIssnr-OptiScaler-DLSSNR-v0.1.2.zip"),
    ("OptiScaler-DLSSNR-v0.2.0.zip", "v0.2.0-dlssnr-OptiScaler-DLSSNR-v0.2.0.zip"),
    ("DLSS5-Feeder-0.12.0.zip", "v0.12.0-DLSS5-Feeder-0.12.0.zip"),
    ("DLSS5-Feeder-0.14.0-beta.5.zip", "v0.14.0-beta.5-DLSS5-Feeder-0.14.0-beta.5.zip"),
)
_DLSSNR_VERSION = re.compile(r"(\d{3}\.\d+\.\d+(?:[-.][A-Za-z0-9]+)*)")


def dlssnr_version_from_name(name: str) -> str:
    m = _DLSSNR_VERSION.search(Path(name).stem)
    return m.group(1) if m else "310.8.0"


def seed(log=print, source: Path | None = None) -> list[str]:
    """Stage everything usable from the components dir. Returns the cache names staged."""
    src = Path(source) if source else paths.components_dir()
    cache = net.cache_dir()
    cache.mkdir(parents=True, exist_ok=True)
    staged: list[str] = []
    if not src.is_dir():
        return staged
    renames = dict(RENAMES)
    for p in sorted(src.iterdir()):
        if not p.is_file():
            continue
        name = renames.get(p.name, p.name)
        if p.suffix.lower() in (".zip", ".7z", ".exe") and not _has_dlssnr(p):
            dest = cache / name
            if not dest.is_file():
                shutil.copy2(p, dest)
                log(f"  seeded {name}")
                staged.append(name)
        elif _has_dlssnr(p):
            ver = dlssnr_version_from_name(p.name)
            dest = cache / f"dlssnr-{ver}.zip"
            if not dest.is_file():
                with zipfile.ZipFile(p) as z:
                    member = next(n for n in z.namelist() if n.lower().endswith("nvngx_dlssnr.dll"))
                    with z.open(member) as fh, zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as out:
                        out.writestr("nvngx_dlssnr.dll", fh.read())
                log(f"  built  {dest.name} from {p.name}")
                staged.append(dest.name)
    return staged


def _has_dlssnr(p: Path) -> bool:
    if p.suffix.lower() != ".zip":
        return False
    try:
        with zipfile.ZipFile(p) as z:
            return any(n.lower().endswith("nvngx_dlssnr.dll") for n in z.namelist())
    except (OSError, zipfile.BadZipFile):
        return False

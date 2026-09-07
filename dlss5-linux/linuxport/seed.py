"""Stage archives we already hold under the installer's cache names.

net.download() returns a cached file without fetching, so anything placed
here under the exact name the installer asks for is used as-is. Saves the
165 MB nvngx_dlssnr pull and lets the fixture test run offline.
"""
from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

from core import net

WORK = Path(__file__).resolve().parents[2]            # DLSS5_work/
OLD_CACHE = Path.home() / ".cache" / "dlss5-proton"

# (cache name the installer asks for, source file)
COPIES = (
    ("ReShade_Setup_6.8.0_Addon.exe", OLD_CACHE / "ReShade_Setup_6.8.0_Addon.exe"),
    ("v0.1.2-dIssnr-OptiScaler-DLSSNR-v0.1.2.zip", WORK / "OptiScaler-DLSSNR-v0.1.2.zip"),
    ("v0.2.0-dlssnr-OptiScaler-DLSSNR-v0.2.0.zip", WORK / "OptiScaler-DLSSNR-v0.2.0.zip"),
    ("v0.12.0-DLSS5-Feeder-0.12.0.zip", WORK / "DLSS5-Feeder-0.12.0.zip"),
    ("v0.14.0-beta.5-DLSS5-Feeder-0.14.0-beta.5.zip", WORK / "DLSS5-Feeder-0.14.0-beta.5.zip"),
    # the default since 2026-09-06 (pins.DEFAULT_TAG); pins._local() stages it too
    ("OptiScaler-DLSSNR-y4my4m-nightly-20260906.zip",
     WORK / "OptiScaler_v10.0.0-pre1_20260906_y4my4m-nightly.zip"),
)
DLSSNR_BUNDLE = WORK / "DLSS 310.8.0 streamline 2.13 dll.zip"


def seed(log=print) -> None:
    cache = net.cache_dir()
    cache.mkdir(parents=True, exist_ok=True)
    for name, src in COPIES:
        dest = cache / name
        if dest.is_file():
            continue
        if src.is_file():
            shutil.copy2(src, dest)
            log(f"  seeded {name}")
    # The installer wants dlssnr-310.8.0.zip containing nvngx_dlssnr.dll.
    # extract_one() matches members by suffix, so inner path is irrelevant.
    dest = cache / "dlssnr-310.8.0.zip"
    if not dest.is_file() and DLSSNR_BUNDLE.is_file():
        with zipfile.ZipFile(DLSSNR_BUNDLE) as src:
            member = next(n for n in src.namelist() if n.lower().endswith("nvngx_dlssnr.dll"))
            with src.open(member) as fh, zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as out:
                out.writestr("nvngx_dlssnr.dll", fh.read())
        log(f"  built  dlssnr-310.8.0.zip from {DLSSNR_BUNDLE.name}")

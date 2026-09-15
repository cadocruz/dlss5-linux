"""XDG replacements for every place core derives a path from %LOCALAPPDATA%.

Upstream keeps cache, prefs, profiles, the library cache, the GitHub API
cache, its own log and the standalone-dlssnr log under
%LOCALAPPDATA%\\dlss5-autopilot. On Linux those belong under XDG_CACHE_HOME,
XDG_CONFIG_HOME and XDG_STATE_HOME. Patched at import so nothing downstream
changes.

Two of them (library.FILE, profiles.DIR) are computed from prefs.FILE at
import time, so setting prefs.FILE alone leaves them pointing at the old
place; they are set explicitly here. `tools/check_shims.py --windows` lists
the modules that name LOCALAPPDATA; any new one belongs in install().
"""
from __future__ import annotations

import os
from pathlib import Path

from core import diagnose, library, log, net, prefs, profiles, sources

# core.watch reads the running game's module list through the Windows process
# API and imports ctypes.wintypes at module level, so on Linux it does not
# import at all - diagnose/evidence.py calls it inside try/except for exactly
# that reason. Its RECORD constant still names LOCALAPPDATA, which here falls
# back to Path.home() and would put a dlss5-autopilot/ folder in the root of
# $HOME. Nothing reaches it while the import fails; this is here so that the
# day a Linux backend makes watch importable, the path is already right.
try:
    from core import watch
except Exception:                              # pragma: no cover - Linux: always
    watch = None

APP = "dlss5-linux"
CACHE = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / APP
CONFIG = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / APP
STATE = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / APP
DATA = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / APP

# Archives and add-ons the person keeps by hand (a nightly OptiScaler zip, a
# renodx build, the DLSS runtime bundle). Replaces the old DLSS5_work/ folder
# beside the tools, which existed on one machine only.
COMPONENTS_ENV = "DLSS5_COMPONENTS_DIR"


def components_dir() -> Path:
    env = os.environ.get(COMPONENTS_ENV, "").strip()
    return Path(env).expanduser() if env else DATA / "components"


def install() -> None:
    net.CACHE = CACHE / "cache"
    sources._API_CACHE = CACHE / "api-cache"
    prefs.FILE = CONFIG / "settings.json"
    profiles.DIR = CONFIG / "profiles"
    library.FILE = CONFIG / "library.json"
    log.DIR = STATE
    if watch is not None:
        watch.RECORD = STATE / "sightings.json"
    # STANDALONE_LOG lives on diagnose.model since upstream 1.9.0 split diagnose
    # into a package, and its __init__ deliberately does NOT re-export it: a copy
    # on the package would be a value that looks right and is not the one the
    # code reads. Patch where it lives, or the log silently keeps the Windows path.
    getattr(diagnose, "model", diagnose).STANDALONE_LOG = STATE / "standalone-dlssnr.log"
    for d in (net.CACHE, CONFIG, STATE):
        d.mkdir(parents=True, exist_ok=True)

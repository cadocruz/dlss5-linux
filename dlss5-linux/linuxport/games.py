"""Linux backend for core.games: Steam roots without the Windows registry.

Everything else in core.games -- the libraryfolders.vdf walk, appmanifest name
lookup, the Game dataclass -- is plain filesystem code and runs unchanged.
Only _steam_root() reads HKCU/HKLM, so only it is replaced.
"""
from __future__ import annotations

import os
from pathlib import Path

from core import games as _games

_CANDIDATES = (
    "~/.steam/steam",
    "~/.local/share/Steam",
    "~/.var/app/com.valvesoftware.Steam/.local/share/Steam",   # Flatpak
    "~/snap/steam/common/.local/share/Steam",                  # Snap
)


def steam_root() -> Path | None:
    env = os.environ.get("STEAM_ROOT")
    if env and Path(env).is_dir():
        return Path(env)
    for c in _CANDIDATES:
        p = Path(c).expanduser()
        if (p / "steamapps").is_dir():
            return p.resolve()
    return None


def install() -> None:
    """Point core.games at this backend. Call once, before scanning."""
    _games._steam_root = steam_root
    install_scan()


# Steam tooling shows up as "games": Proton builds, SteamVR, The Lab. Our own
# scanner (dlss5_proton.SKIP_EXE_RE) already drops them; mirror that here.
_TOOLS = ("proton", "steamvr", "the lab", "steamworks common", "steam linux runtime",
          "sniper", "soldier", "vive console")
_orig_scan = _games.scan_steam


def scan_steam() -> list:
    return [g for g in _orig_scan()
            if not any(g.name.lower().startswith(t) or t in g.name.lower() for t in _TOOLS)]


def install_scan() -> None:
    _games.scan_steam = scan_steam


def scan_heroic() -> list:
    """Read Heroic's Linux records, whose config is under XDG_CONFIG_HOME."""
    from . import heroic
    out = []
    seen: set[Path] = set()
    for root in heroic._roots():
        for key, item in heroic._records(root):
            loc = item.get("install_path") or item.get("path")
            if not loc:
                continue
            folder = Path(loc).expanduser()
            try:
                folder = folder.resolve()
                if not folder.is_dir() or folder in seen:
                    continue
            except OSError:
                continue
            seen.add(folder)
            name = item.get("title") or item.get("app_name") or folder.name
            out.append(_games.Game(name=name, folder=folder, source="Heroic"))
    return out


# --- remembered manual folders + detection of installs we did not record ----
from core import installer as _inst, optiscaler as _opti, prefs as _prefs  # noqa: E402
from pathlib import Path as _P  # noqa: E402

# A manually chosen game folder is remembered by its exact path. Older runs
# could also remember a library root (for example ~/Games/bin, ~/Games/Heroic
# or ~/Games/Steam); scan_all() would then recurse into it, pick the first EXE
# it found, and show the library itself as a game in the GUI. Keep these paths
# usable for an explicit --target, but never present them as game entries.
_LIBRARY_FOLDER_NAMES = {
    "bin", "heroic", "steam", "steamlibrary", "steamapps", "common",
    "compatdata", "epic games", "gog games", "games", "library",
}
_LIBRARY_MARKERS = ("steamapps", "libraryfolders.vdf", "compatdata")


def _is_library_folder(folder: Path) -> bool:
    low = folder.name.strip().lower()
    if low in _LIBRARY_FOLDER_NAMES:
        return True
    try:
        return any((folder / marker).exists() for marker in _LIBRARY_MARKERS)
    except OSError:
        return False


def remember_folder(folder) -> None:
    _prefs.add_install(str(_P(folder).resolve()))


def forget_folder(folder) -> None:
    _prefs.drop_install(str(_P(folder).resolve()))


def scan_all() -> list:
    """Steam games plus every folder ever chosen by hand, deduped."""
    out = scan_steam() + scan_heroic()
    seen = {g.folder.resolve() for g in out}
    for f in _prefs.installs():
        p = _P(f)
        if (p.is_dir() and not _is_library_folder(p)
                and p.resolve() not in seen):
            out.append(_games.Game(name=p.name, folder=p, source="Manual"))
            seen.add(p.resolve())
    return out


def install_state(g) -> tuple[str, str]:
    """('installed'|'foreign'|'', detail) -- what is sitting beside the exe.

    'installed' = a manifest from this tool or the retired proton-tool. 'foreign' =
    ReShade / OptiScaler / nvngx_dlssnr present with no record: someone put
    it there by hand. install() backs those up rather than clobbering them,
    but the list should say so before anyone gets that far.
    """
    root = g.install_dir
    man = _inst._previous_manifest(root)
    if man:
        return "installed", man.get("path") or "?"
    found = []
    for name in _inst.RESHADE_PROXIES:
        p = root / name
        if p.is_file() and _inst._is_reshade(p):
            found.append(f"reshade as {name}")
            break
    if _opti.find_existing(root):
        found.append("optiscaler")
    if (root / "nvngx_dlssnr.dll").is_file():
        found.append("nvngx_dlssnr")
    addons = [p.name for p in root.glob("*.addon64")]
    if addons:
        found.append(", ".join(addons[:2]))
    return ("foreign", " + ".join(found)) if found else ("", "")

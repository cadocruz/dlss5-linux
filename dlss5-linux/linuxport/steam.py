"""Steam on Linux: roots, libraries, installed games, prefixes, launch options.

Lifted from proton-tool/dlss5_proton.py, where it was exercised for a week
on a real library, so that linuxport/ no longer imports the old tool. Pure
filesystem and text: no Steam API, no network. Valve's KeyValues files
(libraryfolders.vdf, appmanifest_*.acf, localconfig.vdf) are parsed with a
small tokenizer rather than a dependency.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

_VDF_TOKEN = re.compile(r'"((?:[^"\\]|\\.)*)"|([{}])')

# Where a prefix may live besides compatdata: hand-made Proton prefixes and umu's.
# Resolved at call time: Path.home() at import would freeze the home of whoever
# imported the module (a test, a different user under sudo).
EXTRA_PREFIX_DIRS = ("proton-prefixes", "Games/umu")


def extra_prefix_roots() -> list[Path]:
    return [Path.home() / d for d in EXTRA_PREFIX_DIRS]

# Names a game's loader picks up; an override left behind for one of these
# whose DLL is gone is noise in the launch options and is dropped.
LOADER_NAMES = ["dxgi.dll", "d3d11.dll", "d3d12.dll", "dinput8.dll",
                "d3d9.dll", "ddraw.dll", "opengl32.dll", "winmm.dll", "version.dll"]

_CANDIDATE_ROOTS = (
    "~/.steam/steam",
    "~/.local/share/Steam",
    "~/.steam/root",
    "~/.var/app/com.valvesoftware.Steam/.local/share/Steam",   # Flatpak
    "~/.var/app/com.valvesoftware.Steam/data/Steam",
    "~/snap/steam/common/.local/share/Steam",                  # Snap
)


@dataclass
class SteamGame:
    appid: str
    name: str
    install_dir: Path
    library: Path            # the steamapps/ folder holding the manifest
    prefix: Path | None = None


def parse_vdf(text: str) -> dict:
    """Valve's KeyValues text format into nested dicts (values are strings)."""
    tokens = []
    for match in _VDF_TOKEN.finditer(text):
        string, brace = match.groups()
        tokens.append(("s", string.replace(r"\"", '"')) if string is not None else ("b", brace))

    def build(index: int) -> tuple[dict, int]:
        result: dict = {}
        while index < len(tokens):
            kind, value = tokens[index]
            if kind == "b" and value == "}":
                return result, index + 1
            if kind != "s":
                index += 1
                continue
            key = value
            index += 1
            if index >= len(tokens):
                break
            next_kind, next_value = tokens[index]
            if next_kind == "b" and next_value == "{":
                child, index = build(index + 1)
                result[key] = child
            else:
                result[key] = next_value
                index += 1
        return result, index

    return build(0)[0]


def steam_roots() -> list[Path]:
    """Every Steam installation root that exists, $STEAM_ROOT first, deduplicated."""
    seen: set[Path] = set()
    roots: list[Path] = []
    env = os.environ.get("STEAM_ROOT")
    candidates = ([Path(env)] if env else []) + [Path(c).expanduser() for c in _CANDIDATE_ROOTS]
    for candidate in candidates:
        if not (candidate / "steamapps").is_dir():
            continue
        real = candidate.resolve()
        if real not in seen:
            seen.add(real)
            roots.append(real)
    return roots


def steam_libraries() -> list[Path]:
    """Every steamapps/ folder that currently exists, across all library folders."""
    libraries: list[Path] = []
    seen: set[Path] = set()

    def add(path: Path) -> None:
        steamapps = path / "steamapps"
        if not steamapps.is_dir():
            return
        real = steamapps.resolve()
        if real not in seen:
            seen.add(real)
            libraries.append(real)

    for root in steam_roots():
        add(root)
        for vdf in (root / "steamapps" / "libraryfolders.vdf", root / "config" / "libraryfolders.vdf"):
            if not vdf.is_file():
                continue
            try:
                data = parse_vdf(vdf.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue
            folders = data.get("libraryfolders", data.get("LibraryFolders", {}))
            for value in folders.values():
                path = value.get("path") if isinstance(value, dict) else value
                if isinstance(path, str) and path:
                    add(Path(path))
    return libraries


def installed_games() -> dict[str, SteamGame]:
    """appid -> game for everything installed across all libraries."""
    games: dict[str, SteamGame] = {}
    for steamapps in steam_libraries():
        for manifest in sorted(steamapps.glob("appmanifest_*.acf")):
            try:
                data = parse_vdf(manifest.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue
            state = data.get("AppState", {})
            appid = state.get("appid")
            install_dir = state.get("installdir")
            if not appid or not install_dir:
                continue
            path = steamapps / "common" / install_dir
            if not path.is_dir():
                continue
            games[appid] = SteamGame(appid=appid, name=state.get("name") or install_dir,
                                     install_dir=path, library=steamapps)
    return games


def find_prefix(appid: str) -> Path | None:
    """The Wine prefix for an appid across compatdata and custom roots.

    Steam creates a stub compatdata directory for games it has never run, so a
    populated prefix elsewhere always wins over the first match.
    """
    roots = [steamapps / "compatdata" for steamapps in steam_libraries()]
    roots.extend(extra_prefix_roots())
    fallback: Path | None = None
    for root in roots:
        candidate = root / appid / "pfx"
        if not candidate.is_dir():
            continue
        if (candidate / "drive_c" / "windows" / "system32").is_dir():
            return candidate
        if fallback is None:
            fallback = candidate
    return fallback


def prefix_from_path(path: Path) -> Path | None:
    """The prefix a game path lives inside, or beside (~/Games/Foo/{pfx,game})."""
    p = Path(path)
    try:
        resolved = p.resolve()
    except OSError:
        resolved = p
    for parent in resolved.parents:
        if parent.name == "drive_c" and parent.parent != parent:
            return parent.parent
    for parent in [resolved, *resolved.parents][:4]:
        for name in ("pfx", "prefix", "wineprefix"):
            candidate = parent / name
            if (candidate / "drive_c" / "windows" / "system32").is_dir():
                return candidate
    return None


def localconfigs() -> list[Path]:
    out: list[Path] = []
    for root in steam_roots():
        out += sorted((root / "userdata").glob("*/config/localconfig.vdf"))
    return out


def launch_options_for(appid: str) -> str | None:
    """The current Steam launch options for an appid, unescaped, or None."""
    for config in localconfigs():
        try:
            text = config.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        stack: list[str] = []
        for raw in text.splitlines():
            line = raw.strip()
            key_only = re.fullmatch(r'"([^"]+)"', line)
            if key_only:
                stack.append(key_only.group(1))
            elif line == "}" and stack:
                stack.pop()
            elif line.startswith('"LaunchOptions"') and stack and stack[-1] == appid:
                value = re.fullmatch(r'"LaunchOptions"\s+"(.*)"', line)
                if value:
                    return value.group(1).replace('\\"', '"').replace("\\\\", "\\")
    return None


def build_launch_options(appid: str | None, folder: Path, loader: str,
                         extra_overrides: list[str] | None = None) -> str:
    """Merge the loader override into the game's existing options.

    Returns the string as it is pasted into Steam (plain quotes). Overrides
    for loader names whose DLL is no longer beside the game are dropped as
    leftovers of an earlier attempt.
    """
    override = f"{Path(loader).stem}=n,b"
    wanted = [override, *(extra_overrides or [])]
    existing = (launch_options_for(appid) if appid else None) or ""

    if not existing.strip():
        return 'WINEDLLOVERRIDES="' + ";".join(wanted) + '" %command%'

    match = re.search(r'WINEDLLOVERRIDES="([^"]*)"', existing)
    if match:
        stale = {Path(name).stem for name in LOADER_NAMES
                 if name != loader and not (Path(folder) / name).is_file()}
        parts = [part for part in match.group(1).split(";")
                 if part and part.split("=")[0] not in stale]
        for want in wanted:
            key = want.split("=")[0]
            if not any(part.split("=")[0] == key for part in parts):
                parts.append(want)
        if not parts:
            return (existing[:match.start()] + existing[match.end():]).replace("  ", " ")
        replacement = 'WINEDLLOVERRIDES="' + ";".join(parts) + '"'
        return existing[:match.start()] + replacement + existing[match.end():]

    joined = 'WINEDLLOVERRIDES="' + ";".join(wanted) + '"'
    if "%command%" in existing:
        return existing.replace("%command%", f"{joined} %command%", 1)
    return f"{existing} {joined} %command%"


# --- processes ----------------------------------------------------------------------------------
def _ancestor_pids() -> set[int]:
    """This process and everything that spawned it (Linux /proc)."""
    pids, pid = set(), os.getpid()
    while pid > 1 and pid not in pids:
        pids.add(pid)
        try:
            status = Path(f"/proc/{pid}/status").read_text()
        except OSError:
            break
        match = re.search(r"^PPid:\s*(\d+)", status, re.MULTILINE)
        if not match:
            break
        pid = int(match.group(1))
    return pids


def process_running(exe_name: str) -> bool:
    """Is a process running this executable? Confirmed against argv[0], with
    this tool's own ancestry filtered out (the game path is one of our arguments)."""
    import subprocess
    result = subprocess.run(["pgrep", "-f", re.escape(exe_name)], capture_output=True, text=True)
    if result.returncode != 0:
        return False
    skip = _ancestor_pids()
    for line in result.stdout.split():
        try:
            pid = int(line)
        except ValueError:
            continue
        if pid in skip:
            continue
        try:
            argv0 = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")[0]
        except OSError:
            continue
        if argv0.decode("utf-8", "replace").lower().endswith(exe_name.lower()):
            return True
    return False

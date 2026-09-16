"""Proton layer: everything Windows never needed.

Prefix discovery, Steam appid mapping, localconfig.vdf and WINEDLLOVERRIDES
live in linuxport/steam.py (lifted from the old proton-tool); this module
adapts core.games.Game to them and adds what Proton itself needs: the NGX
bridge check, the on-screen indicator, PROTON_DLSS_UPGRADE, XIVLauncher-RB.
"""
from __future__ import annotations

import os
from pathlib import Path

from core import games
from . import steam as pt

_APPIDS: dict[Path, str] | None = None


def _index() -> dict[Path, str]:
    global _APPIDS
    if _APPIDS is None:
        _APPIDS = {g.install_dir.resolve(): appid for appid, g in pt.installed_games().items()}
    return _APPIDS


def appid_for(g: games.Game) -> str | None:
    """Steam appid whose install_dir contains this game's folder, if any."""
    idx = _index()
    for probe in (g.folder.resolve(), *g.folder.resolve().parents):
        if probe in idx:
            return idx[probe]
    return None


def prefix_for(g: games.Game) -> Path | None:
    appid = appid_for(g)
    if appid:
        return pt.find_prefix(appid)
    return pt.prefix_from_path(g.exe or g.folder)


def system32(g: games.Game) -> Path | None:
    p = prefix_for(g)
    s = p / "drive_c/windows/system32" if p else None
    return s if s and s.is_dir() else None


def ngx_bridge_present(g: games.Game) -> bool:
    """Proton copies the driver's NGX bridge into the prefix on first run."""
    s = system32(g)
    return bool(s and (s / "_nvngx.dll").is_file())


def proton_version(g: games.Game) -> str | None:
    """The Proton build that last ran this prefix, from Steam's own record.

    Steam writes `compatdata/<appid>/version` ("9.0-4" style, or the tool's
    name for GE/CachyOS builds) when Proton sets the prefix up; a prefix made
    by another launcher has no such file, and then there is no answer.
    """
    p = prefix_for(g)
    if not p:
        return None
    for cand in (p.parent / "version", p / "version"):
        try:
            text = cand.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            continue
        if text:
            return text.splitlines()[0][:60]
    return None


def current_launch_options(g: games.Game) -> str | None:
    appid = appid_for(g)
    if appid:
        return pt.launch_options_for(appid)
    from . import heroic
    return heroic.current_line(g)


def nvapi_decision(g: games.Game):
    """Predict Proton's NVAPI policy without executing the Proton script."""
    from . import nvapi
    return nvapi.decision(g)


def force_nvapi_present(options: str | None) -> bool:
    import re as _re
    return bool(options) and bool(_re.search(r"(?:^|\s)PROTON_FORCE_NVAPI=1(?:\s|$)", options))


def with_force_nvapi(line: str, on: bool = True) -> str:
    import re as _re
    line = _re.sub(r"\s*PROTON_FORCE_NVAPI=\S+", "", line).strip()
    return f"PROTON_FORCE_NVAPI=1 {line}" if on else line


def override_entries(g: games.Game, proxy: str, route: str | None = None) -> list[str]:
    """Every WINEDLLOVERRIDES entry this game needs, proxy first.

    * the proxy itself -- Proton marking a name native does not make the game
      folder outrank system32, so the override is always required;
    * d3d12 + d3d12core for a D3D11 game on OptiScaler: its dx11on12 bridge
      must get vkd3d-proton's d3d12, and Proton only injects those overrides
      on Steam's `run` verb. umu launchers (XIVLauncher-RB, Lutris) use
      `runinprefix` and hand the bridge Wine's builtin d3d12, which cannot
      take DXVK's adapter: `D3D12CreateDevice failed: 80004002`. Harmless on
      Steam, so it is added whenever the bridge is in play;
    * d3dcompiler_47 when a native copy sits beside the exe (the tool places
      one for ReShade on prefixes it could not run protontricks in).
    """
    if proxy == "(vulkan layer)":
        # No proxy DLL at all: ReShade is an implicit layer in the prefix.
        # The override that matters is the loader's, when the LunarG one
        # replaced Wine's builtin (linuxport.vulkan.ensure_native_loader).
        from . import vulkan as _lv
        entries = ["vulkan-1=n,b"] if _lv.native_loader_present(prefix_for(g)) else []
    else:
        entries = [f"{Path(proxy).stem}=n,b"]
    if (route in (None, "optiscaler")) and (g.api or "").upper() == "DX11":
        entries += ["d3d12=n,b", "d3d12core=n,b"]
    exe_dir = g.exe.parent if g.exe else g.folder
    if (exe_dir / "d3dcompiler_47.dll").is_file():
        entries.append("d3dcompiler_47=n")
    return entries


def launch_options(g: games.Game, proxy: str, indicator: bool | None = None,
                   route: str | None = None) -> str:
    """The exact string to paste into Steam (or export, for non-Steam).

    indicator=None keeps whatever the current options already say."""
    import re as _re
    line = pt.build_launch_options(appid_for(g), g.install_dir, proxy)
    entries = ";".join(override_entries(g, proxy, route))
    if entries:
        line, n = _re.subn(r'WINEDLLOVERRIDES="[^"]*"', f'WINEDLLOVERRIDES="{entries}"', line)
        if not n:
            line = f'WINEDLLOVERRIDES="{entries}" {line}'
    else:
        # A Vulkan-layer install with Wine's own loader needs no override at all.
        line = _re.sub(r'\s*WINEDLLOVERRIDES="[^"]*"\s*', " ", line).strip()
    if indicator is None:
        indicator = indicator_present(current_launch_options(g))
    line = with_indicator(line, indicator)
    if nvapi_decision(g).state == "required":
        line = with_force_nvapi(line)
    return line


def override_present(options: str | None, proxy: str, entries: list[str] | None = None) -> bool:
    """Every required entry present? (proxy only, unless `entries` is given)."""
    if not options:
        return False
    need = entries or [f"{Path(proxy).stem}=n"]
    return all(e.split("=")[0] + "=n" in options for e in need)


def missing_overrides(g: games.Game, proxy: str, route: str | None = None) -> list[str]:
    cur = launcher_overrides(g) if is_xlcore(g) else current_launch_options(g)
    return [e for e in override_entries(g, proxy, route) if not override_present(cur, proxy, [e])]


def running(g: games.Game) -> bool:
    """Is the game's executable running? False when it cannot be told.

    process_running shells out to pgrep; a box without procps (or a test
    run on another OS) must read as "not running", not as a crash inside
    'did it work?'."""
    if not g.exe:
        return False
    try:
        return pt.process_running(g.exe.name)
    except OSError:
        return False


# --- non-Steam games: the prefix is not derivable, so it can be told to us ----
# Steam games: compatdata/<appid>/pfx. Lutris/Heroic games installed INSIDE a
# prefix: walk up to drive_c/. Everything else (Cyberpunk on the GOG drive with
# its prefix at ~/.wine) has no path from the folder to the prefix at all, so
# it is remembered per folder in prefs, with $WINEPREFIX as a last fallback.
from core import prefs as _prefs  # noqa: E402

_PREFS_KEY = "prefixes"


def set_prefix(g: games.Game, prefix: Path | None) -> None:
    d = dict(_prefs.get(_PREFS_KEY) or {})
    key = str(g.folder.resolve())
    if prefix and (Path(prefix) / "drive_c").is_dir():
        d[key] = str(Path(prefix).expanduser().resolve())
    else:
        d.pop(key, None)
    _prefs.set_(_PREFS_KEY, d)


def remembered_prefix(g: games.Game) -> Path | None:
    p = (_prefs.get(_PREFS_KEY) or {}).get(str(g.folder.resolve()))
    return Path(p) if p and (Path(p) / "drive_c").is_dir() else None


_prefix_for_steam = prefix_for


# --- XIVLauncher-RB (Final Fantasy XIV outside Steam) --------------------------
# The RB fork of XIVLauncher.Core runs the game through umu with Proton; its
# prefix and config live under ~/.xlcore (a symlink into XDG data). Overrides
# go into launcher.ini's WineDLLOverrides (regex-validated: no dxgi/d3d11/d3d9
# entries allowed there), not into Steam.
XLCORE = Path.home() / ".xlcore"


def xlcore_prefix() -> Path | None:
    p = XLCORE / "protonprefix" / "pfx"
    return p if (p / "drive_c").is_dir() else None


def is_xlcore(g: games.Game) -> bool:
    x = xlcore_prefix()
    return bool(x and g.exe and g.exe.name.lower() == "ffxiv_dx11.exe" and not appid_for(g))


def launcher_overrides(g: games.Game) -> str | None:
    """The WineDLLOverrides line XIVLauncher-RB currently carries."""
    ini = XLCORE / "launcher.ini"
    try:
        for line in ini.read_text(encoding="utf8").splitlines():
            if line.startswith("WineDLLOverrides="):
                return line.split("=", 1)[1]
    except OSError:
        pass
    return None


def _pgrep(*args: str) -> bool:
    """True when pgrep finds a match; False when it does not or cannot run."""
    import subprocess
    try:
        return subprocess.run(["pgrep", *args], capture_output=True).returncode == 0
    except OSError:
        return False


def launcher_running() -> bool:
    return _pgrep("-f", "XIVLauncher[.]Core")


def set_launcher_overrides(g: games.Game, entries: list[str]) -> Path:
    """Write WineDLLOverrides into ~/.xlcore/launcher.ini (launcher must be closed)."""
    import re as _re, shutil, time
    if not is_xlcore(g):
        raise RuntimeError("not an XIVLauncher-RB game")
    if launcher_running():
        raise RuntimeError("XIVLauncher is running: close it first, it rewrites launcher.ini on exit")
    bad = [e for e in entries if e.split("=")[0] in ("dxgi", "d3d11", "d3d9", "d3d10core", "msquic", "mscoree")]
    if bad:
        raise RuntimeError(f"XIVLauncher-RB refuses these in its override field: {', '.join(bad)}")
    ini = XLCORE / "launcher.ini"
    text = ini.read_text(encoding="utf8")
    value = ";".join(entries)
    new, n = _re.subn(r"(?m)^WineDLLOverrides=.*$", f"WineDLLOverrides={value}", text)
    if not n:
        new = text.rstrip("\n") + f"\nWineDLLOverrides={value}\n"
    backup = ini.with_name(f"launcher.ini.bak-{time.strftime('%Y%m%d-%H%M%S')}")
    shutil.copy2(ini, backup)
    ini.write_text(new, encoding="utf8")
    return backup


def prefix_for(g: games.Game) -> Path | None:  # noqa: F811 -- override, by design
    p = remembered_prefix(g) or _prefix_for_steam(g)
    if p:
        return p
    if g.exe and g.exe.name.lower() == "ffxiv_dx11.exe" and xlcore_prefix():
        return xlcore_prefix()
    env = os.environ.get("WINEPREFIX")
    return Path(env) if env and (Path(env) / "drive_c").is_dir() else None


def launch_help(g: games.Game, proxy: str, indicator: bool | None = None,
                route: str | None = None) -> list[str]:
    """Lines to show for the launch configuration, Steam or otherwise."""
    line = launch_options(g, proxy, indicator, route)
    entries = override_entries(g, proxy, route)
    joined = ";".join(entries)
    if appid_for(g):
        d = nvapi_decision(g)
        status = ("required: PROTON_FORCE_NVAPI=1 added automatically"
                  if d.state == "required" else
                  "not required by this Proton build" if d.state == "not_required" else
                  "unknown: Proton build could not be inspected")
        return ["steam > properties > launch options:", f"  {line}", f"  NVAPI detection: {status}"]
    if is_xlcore(g):
        return [
            "xivlauncher-rb runs this game through umu, so the override goes into its own config:",
            f"  settings > wine tab > dll overrides:      {joined}",
            f"  (or launcher.ini: WineDLLOverrides={joined} -- 'apply' below writes it while the launcher is closed)",
            "  d3d12 + d3d12core are required there: umu's runinprefix skips proton's own vkd3d overrides",
        ]
    from . import heroic
    if heroic.is_game(g):
        env = _environment_tokens(line)
        return [
            "heroic > game settings > environment variables:",
            *[f"  key {key} value {value}" for key, value in env.items()],
            "  (the 'apply overrides' action writes these values to Heroic's game config while Heroic is closed)",
        ]
    grid = "   ".join(f"key {e.split('=')[0]} value {e.split('=', 1)[1]}" for e in entries)
    return [
        "this game is not in your steam library, so the override goes where it launches from:",
        f"  lutris / heroic, environment variables:   WINEDLLOVERRIDES={joined}",
        f"  lutris 'dll overrides' grid instead:       {grid}   (NOT the whole string as one key)",
        f"  shell / umu:                               export WINEDLLOVERRIDES=\"{joined}\"",
        f"  or add it to steam as a non-steam game:    {line}",
    ]


# --- on-screen DLSS indicator ------------------------------------------------
# Proton owns this. PROTON_DLSS_INDICATOR=1 becomes compat flag "dlsshud",
# which sets DXVK_NVAPI_SET_NGX_DEBUG_OPTIONS="DLSSIndicator=1024,DLSSGIndicator=2,"
# and dxvk-nvapi writes it into NGXCore in the prefix registry at every launch.
# Absent the flag, Proton defaults the same variable to ...=0, so a value set
# by hand in system.reg is overwritten before the game starts -- which is why
# the manual `reg add` did nothing. Do not touch the registry; set the env.
INDICATOR_ENV = "PROTON_DLSS_INDICATOR=1"
# For a Proton build without the flag, this is what it expands to:
INDICATOR_RAW = 'DXVK_NVAPI_SET_NGX_DEBUG_OPTIONS="DLSSIndicator=1024,DLSSGIndicator=2,"'


def with_indicator(line: str, on: bool = True) -> str:
    """Add or remove the indicator variable in a launch-options string."""
    import re as _re
    line = _re.sub(r"\s*PROTON_DLSS_INDICATOR=\S+", "", line).strip()
    return f"{INDICATOR_ENV} {line}" if on else line


def indicator_present(options: str | None) -> bool:
    import re as _re
    return bool(options) and bool(_re.search(r"PROTON_DLSS_INDICATOR=([1-9]|true|yes|on)", options))


# --- the DLSS runtime Proton ships ---------------------------------------------------------------
# PROTON_DLSS_UPGRADE=1 makes Proton copy its bundled nvngx_dlss / dlssd / dlssg
# (310.9 in Proton 11) into system32/umu/ at every launch, ahead of the game's
# own. It is the Linux answer to "upgrade the game's DLSS" - the old tool did it
# by swapping files in the game folder, which Proton then overrode anyway.
DLSS_UPGRADE_ENV = "PROTON_DLSS_UPGRADE=1"


def with_dlss_upgrade(line: str, on: bool = True) -> str:
    import re as _re
    line = _re.sub(r"\s*PROTON_DLSS_UPGRADE=\S+", "", line).strip()
    return f"{DLSS_UPGRADE_ENV} {line}" if on else line


def dlss_upgrade_present(options: str | None) -> bool:
    import re as _re
    return bool(options) and bool(_re.search(r"PROTON_DLSS_UPGRADE=([1-9]|true|yes|on)", options))


# --- installed proxy + writing launch options ---------------------------------
def installed_proxy(g: games.Game) -> str | None:
    """The proxy recorded by whichever tool set this game up, else None."""
    from core import installer as _inst
    man = _inst._previous_manifest(g.install_dir) or {}
    return man.get("proxy") or None


def steam_running() -> bool:
    return _pgrep("-x", "steam")


def set_launch_options(g: games.Game, line: str) -> Path:
    """Write LaunchOptions for this game's appid into localconfig.vdf.

    Steam holds that file and rewrites it on exit, so this refuses while Steam
    runs -- an edit made now would be silently lost. Backs the file up first.
    The on-disk form escapes quotes as \\" ; `line` is the plain form.
    """
    import re as _re, shutil, time
    appid = appid_for(g)
    if not appid:
        raise RuntimeError("not a Steam game -- set the variables in its launcher instead")
    if steam_running():
        raise RuntimeError("Steam is running: close it first, or the edit is overwritten on exit")
    escaped = line.replace("\\", "\\\\").replace('"', '\\"')
    # Every root, not just the first: a native Steam and a Flatpak one can sit
    # side by side, and the appid may be in either. steam_roots() is also
    # allowed to be empty (Steam somewhere this tool does not look), which
    # indexing [0] turned into an IndexError out of a worker thread.
    configs = pt.localconfigs()
    for cfg in configs:
        try:
            text = cfg.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        # the app block: "<appid>"\n{ ... } inside "apps"
        m = _re.search(r'(\n(\t+)"' + appid + r'"\n\2\{\n)(.*?)(\n\2\})', text, _re.S)
        if not m:
            continue
        head, indent, body, tail = m.group(1), m.group(2), m.group(3), m.group(4)
        lo = _re.search(r'^\t+"LaunchOptions"\t+"(.*)"$', body, _re.M)
        if lo:
            body = body[:lo.start(1)] + escaped + body[lo.end(1):]
        else:
            body = body + f'\n{indent}\t"LaunchOptions"\t\t"{escaped}"'
        # Sub-second + counter: four games applied in one second must not
        # share one backup name, or later writes overwrite the earlier backups.
        stamp = time.strftime("%Y%m%d-%H%M%S") + f"-{int(time.time() * 1000) % 1000:03d}"
        backup = cfg.with_name(f"localconfig.vdf.bak-{stamp}")
        n = 0
        while backup.exists():
            n += 1; backup = cfg.with_name(f"localconfig.vdf.bak-{stamp}-{n}")
        shutil.copy2(cfg, backup)
        cfg.write_text(text[:m.start()] + head + body + tail + text[m.end():], encoding="utf-8")
        return backup
    if not configs:
        raise RuntimeError("no localconfig.vdf found under any Steam root - "
                           "set STEAM_ROOT, or paste the line into Steam by hand")
    raise RuntimeError(f"appid {appid} not found in any localconfig.vdf "
                       f"({len(configs)} read)")


def _environment_tokens(line: str) -> dict[str, str]:
    """Extract launch environment assignments, preserving quoted values."""
    import shlex
    try:
        tokens = shlex.split(line)
    except ValueError:
        tokens = line.split()
    supported = {"WINEDLLOVERRIDES", "PROTON_FORCE_NVAPI", "PROTON_DLSS_INDICATOR",
                 "PROTON_DLSS_UPGRADE"}
    values: dict[str, str] = {}
    for token in tokens:
        if "=" not in token:
            continue
        key, value = token.split("=", 1)
        if key in supported:
            values[key] = value
    return values


def apply_launch_options(g: games.Game, line: str) -> Path | None:
    """Apply generated launch settings to Steam, Heroic, or XIVLauncher."""
    if is_xlcore(g):
        return set_launcher_overrides(g, override_entries(g, installed_proxy(g) or "dxgi.dll"))
    if appid_for(g):
        return set_launch_options(g, line)
    from . import heroic
    if heroic.is_game(g):
        values = _environment_tokens(line)
        if not values:
            raise RuntimeError("no supported environment variables were generated")
        return heroic.set_environment(g, values)
    raise RuntimeError("this game is not managed by Steam or Heroic")

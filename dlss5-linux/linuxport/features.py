"""The upstream 1.7.2 - 1.8.0 window features, without the window.

Upstream grew these inside core/gui.py (Tk): the library remembered between
launches, the driver and conflict notes per route, the ray-reconstruction
swap, the overlay key, "aim for N fps", "what other people found", "share
the result" and the bug report with its two questions. The Qt window here
cannot import that file, so the logic is lifted into plain functions that
both dlss5_gui_linux.py and dlss5_linux.py call. Nothing here touches Qt,
so all of it is unit-tested.

Every function takes the core objects (games.Game, dlss.Support,
diagnose.Report) and returns text or small data; writing to disk happens
only in apply_tune() and set_overlay_key(), both mirrors of upstream.
"""
from __future__ import annotations

import webbrowser
from dataclasses import dataclass
from urllib.parse import quote

from core import (anticheat, autotune, community, diagnose, dlss, feedcfg, gpu, installer,
                  library, log, optiscaler, prefs, reshade_ini, update)
from . import openxr as lopenxr, proton

# The port's own version, and the only one it may call its own: update.VERSION
# belongs to DLSS5-Autopilot, and a release named after it would be claiming
# somebody else's.
#
# The numbering restarts here. 0.1 through 0.3 were checkout revisions - there
# was no tag, no release, and nothing anybody could have downloaded - so 0.0.1
# is the first version that names something a person can actually hold.
PORT_VERSION = "0.0.1"

# The library cache is gated on the version that wrote it. The Linux layer
# changes what a game is read as (pe shims, policy), so a cache written by an
# older port must not survive a port update either: the tag carries both.
LIBRARY_VERSION = f"{update.VERSION}+linux{PORT_VERSION}"

# What a shared result carries beyond upstream's record. `os` lets the
# aggregate tell a Linux 610-series driver from a Windows 616 one; without it
# every Proton report would count against a driver number that never
# existed on Windows.
RESULT_OS = "linux"


# --- route notes -------------------------------------------------------------------------
def route_notes(game, path: str, support, driver: str | None = None) -> tuple[list[str], list[str]]:
    """(blurb lines, warning lines) for the install page, in upstream's order:
    the route blurb, its known conflicts, the game's quirks, the driver."""
    blurb = [dlss.BLURB.get(path, "")]
    warns: list[str] = []
    # CONFLICTS entries became (kind, line) in upstream 1.9.0. gui.py splits them
    # and so must we: an "ingame" line is a setting to change inside the game,
    # not a warning about this folder, and a "folder" line only earns a warning
    # when another NGX hook is actually sitting there. Flattening both into
    # warnings - what this did before 1.9.0 - warns about nothing on a clean folder.
    foreign: list[str] = []
    try:
        if game is not None and getattr(game, "install_dir", None):
            foreign = installer.other_ngx_hooks(game.install_dir, path)
    except Exception:
        foreign = []
    for kind, line in getattr(dlss, "CONFLICTS", {}).get(path, ()):
        if kind == "folder" and foreign:
            warns.append(f"{', '.join(foreign[:3])} in this folder - {line}")
        else:
            blurb.append(line)
    try:
        warns += list(dlss.quirks(game.exe if game else None, game.api if game else ""))
    except Exception:
        pass
    try:
        # linux_gpu.driver_at_least makes this silent on the 610 branch.
        w = dlss.driver_warning(path, driver if driver is not None else gpu.driver_version())
    except Exception:
        w = None
    if w:
        warns.append(w)
    return blurb, warns


def has_ray_reconstruction(support) -> bool:
    """Does the game ship nvngx_dlssd.dll? Read from detect()'s evidence, as upstream does."""
    if support is None:
        return False
    return any(str(e).lower().endswith("nvngx_dlssd.dll") for e in (getattr(support, "evidence", None) or []))


def swap_warning(name: str) -> list[str]:
    return [l for l in anticheat.swap_message(name).splitlines()]


def vr_reason() -> str:
    return lopenxr.REASON


# --- overlay key -----------------------------------------------------------------------------
def overlay_key_names() -> list[str]:
    return list(reshade_ini.OVERLAY_KEYS)


def current_overlay_key() -> str:
    """The entry of OVERLAY_KEYS the settings currently name."""
    try:
        vk = int(prefs.get("overlay_key") or 0)
    except (TypeError, ValueError):
        vk = 0
    for name, code in reshade_ini.OVERLAY_KEYS.items():
        if code == vk:
            return name
    return overlay_key_names()[0]


def set_overlay_key(name: str) -> int:
    """Remember the key; 0 (route default) writes nothing at install."""
    vk = reshade_ini.OVERLAY_KEYS.get(name, 0)
    vk = 0 if "default" in name else vk
    prefs.set_("overlay_key", vk)
    return vk


# --- library cache ------------------------------------------------------------------------------
def library_load(sm):
    """(games, rows, changed) from last time, or None. Never raises."""
    try:
        return library.load(LIBRARY_VERSION, sm)
    except Exception:
        return None


def library_save(all_games: list, rows: dict, sm) -> None:
    library.save(all_games, rows, LIBRARY_VERSION, sm)


def library_forget() -> None:
    try:
        library.forget()
    except Exception:
        pass


def scan_on_start() -> bool:
    v = prefs.get("scan_on_start")
    return True if v is None else bool(v)


def set_scan_on_start(on: bool) -> None:
    prefs.set_("scan_on_start", bool(on))


# --- community ---------------------------------------------------------------------------------------
def community_lines(game, route: str) -> list[str]:
    """What other people found in this game. Downloads (cached); never raises."""
    try:
        entry = community.for_game(community.fetch(), game)
        return community.advice(entry, route, gpu.driver_version() or "")
    except Exception:
        return []


def share_record(game, route: str, rep, manifest: dict | None = None) -> dict:
    """Upstream's record plus the two Linux facts the aggregate needs."""
    man = manifest if manifest is not None else (diagnose._manifest(game.install_dir) or {})
    worked = str(getattr(rep, "verdict", "")).startswith("Working")
    try:
        name, sm = gpu.detect()
    except Exception:
        name, sm = "unknown", None
    rec = community.record(
        game, route or str(man.get("path") or ""), "worked" if worked else "failed",
        api=str(man.get("api") or getattr(game, "api", "") or ""),
        build=str(man.get("opti_build") or ""),
        gpu_sm=sm, gpu_name=name or "", driver=gpu.driver_version() or "",
        version=LIBRARY_VERSION)
    rec["os"] = RESULT_OS
    pv = proton.proton_version(game)
    if pv:
        rec["proton"] = pv
    return rec


def share_url(game, route: str, rep, manifest: dict | None = None) -> str:
    rec = share_record(game, route, rep, manifest)
    note = str(getattr(rep, "verdict", "") or "")
    return community.issue_url(rec, note)


# --- aim for a frame rate ------------------------------------------------------------------------------
@dataclass
class Tune:
    measured: autotune.Measured
    suggestion: autotune.Suggestion | None


def work_applies(game, route: str) -> bool:
    """Is there a resolution dial on this route for this game? OptiScaler
    always; the feeder only on the 64-bit D3D11 path (its own log says
    'settled D3D11 work resolution'); nowhere else."""
    if route == dlss.OPTI:
        return True
    if route == dlss.FEEDER:
        return (game.api or "").upper() == "DX11" and (game.bitness or 64) == 64
    return False


def autotune_after(game, route: str, slider_value: int, rep, target_fps: float | None) -> Tune | None:
    """After 'did it work?': what the session cost and what to run next.

    Mirrors gui._autotune: the resolution the session ran at is read from
    the config the add-on read, not from the slider; the measurement is
    remembered before the suggestion so the second session already solves.
    """
    if game is None or not target_fps or not getattr(rep, "ran", False):
        return None
    if not work_applies(game, route):
        return None
    d = game.install_dir
    try:
        feed_txt = diagnose._last_run(diagnose._tail(d / diagnose.FEED_LOG, 100_000))
        opti_p = diagnose._opti_log(d)
        opti_txt = diagnose._last_run(diagnose._tail(opti_p, 100_000)) if opti_p else ""
        ran_at = autotune.ran_at(d, route, slider_value)
        m = autotune.measure(feed_txt, opti_txt, route, ran_at)
    except Exception:
        return None
    if m is None:
        return None
    autotune.remember(d, m)
    sug = autotune.suggest(autotune.history(d), target_fps, m.resolution, route, m)
    return Tune(m, sug)


def apply_tune(game, route: str, resolution: int, log_fn=None) -> None:
    """Write the suggested work area into the config already on disk.
    Both add-ons read it at start-up, so it applies to the next run."""
    log_fn = log_fn or (lambda *_: None)
    d = game.install_dir
    if route == dlss.OPTI:
        optiscaler.enable_nr(d, log=log_fn, settings={"WorkingScale": round(resolution / 100.0, 3)})
    else:
        feedcfg.write(d, {"work_resolution": resolution})
        log_fn(f"      dlss5-feed.cfg: work_resolution={resolution}%")


# --- bug report -------------------------------------------------------------------------------------------
REPORT_QUESTIONS = (
    ("started", "did the game start?", ("yes", "no", "it closed itself")),
    ("happened", "what happened, in your own words?", None),
)


def proton_section(game) -> str:
    """The lines a Linux report needs that the Windows body cannot know."""
    pfx = proton.prefix_for(game)
    lines = ["**Proton**",
             f"- prefix: {'found' if pfx else 'not found'}",
             f"- ngx bridge in system32: {'yes' if pfx and proton.ngx_bridge_present(game) else 'no'}",
             f"- proton: {proton.proton_version(game) or '-'}"]
    proxy = proton.installed_proxy(game)
    if proxy:
        route = (diagnose._manifest(game.install_dir) or {}).get("path")
        missing = proton.missing_overrides(game, proxy, route)
        lines.append(f"- WINEDLLOVERRIDES: {'complete' if not missing else 'missing ' + ';'.join(missing)}")
    return "\n".join(lines) + "\n\n"


def report_url(game, route: str, last_diag, answers: dict | None,
               crash=None) -> tuple[str, str, bool]:
    """(url, body, body_on_clipboard): a pre-filled upstream issue.

    The Windows body comes from diagnose.issue_body; the Proton section is
    prepended. Above GitHub's URL limit the body has to travel by clipboard
    and the URL only says so - the caller copies it.
    """
    try:
        name, sm = gpu.detect()
    except Exception:
        name, sm = "unknown", None
    drv = gpu.driver_version() or "?"
    title = "[linux] " + ("not working: " if last_diag is not None and not str(
        getattr(last_diag, "verdict", "")).startswith("Working") else "bug: ")
    if game is not None:
        title += game.name
    body = diagnose.issue_body(
        LIBRARY_VERSION, name, sm, drv, game, route or "-", last_diag,
        log.tail(60, 6000), log.path(), game.install_dir if game else None,
        last_error=log.last_error(), answers=answers, crash=crash)
    if game is not None:
        try:
            body = proton_section(game) + body
        except Exception:
            pass
    base = f"https://github.com/{update.REPO}/issues/new?title={quote(title)}&body="
    url = base + quote(body)
    if len(url) > 7800:
        return base + quote("(the details are on your clipboard - paste them here)"), body, True
    return url, body, False


def open_url(url: str) -> bool:
    try:
        return bool(webbrowser.open(url))
    except Exception:
        return False


# --- install-page defaults -----------------------------------------------------------------------------------
def opti_builds() -> list[tuple[str, str]]:
    """(key, label) for the OptiScaler build dropdown, upstream's three lines
    with the Proton finding on the one that matters here."""
    out = []
    for key, text in optiscaler.BUILDS.items():
        tail = "  <-  the line that works under Proton" if key == optiscaler.FORK else ""
        out.append((key, f"{key or 'dagherbou'} - {text}{tail}"))
    return out


def default_opti_build() -> str:
    return optiscaler.FORK


def dlssd_choices(catalog: dict) -> list[str]:
    return [e["label"] for e in (catalog or {}).get("dlssd", [])]


def manifest_route(game) -> str:
    return str((installer._previous_manifest(game.install_dir) or {}).get("path") or "")

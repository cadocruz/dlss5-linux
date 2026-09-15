"""linuxport.userdata: per-user game data folders, read out of the Proton prefix."""
from __future__ import annotations

from pathlib import Path

import pytest

from core.diagnose import model
from linuxport import paths, userdata, vulkan


def make_prefix(tmp_path: Path, *users: str) -> Path:
    pfx = tmp_path / "pfx"
    for u in users or ("steamuser",):
        for sub in userdata.USER_SUBDIRS:
            (pfx / "drive_c" / "users" / u / sub).mkdir(parents=True, exist_ok=True)
    return pfx


# --- the roots themselves ---------------------------------------------------------------
def test_every_windows_root_has_a_place_in_the_prefix(tmp_path):
    got = userdata.prefix_roots(make_prefix(tmp_path))
    user = tmp_path / "pfx" / "drive_c" / "users" / "steamuser"
    assert got == [user / s for s in userdata.USER_SUBDIRS]
    # the three %-variables upstream reads, and its two home folders
    names = {p.name for p in got}
    assert {"Local", "LocalLow", "Roaming", "My Games", "Saved Games"} == names


def test_public_is_skipped_and_a_login_name_is_not(tmp_path):
    """Steam prefixes say steamuser; Lutris, Heroic and umu use the login name."""
    pfx = make_prefix(tmp_path, "Public", "ricardo")
    got = userdata.prefix_roots(pfx)
    users = pfx / "drive_c" / "users"
    assert got and all("Public" not in p.parts for p in got)
    assert all(p.is_relative_to(users / "ricardo") for p in got)


def test_a_prefix_that_is_not_there_is_not_an_error(tmp_path):
    assert userdata.prefix_roots(tmp_path / "nope") == []


# --- what it returns when there is no prefix -----------------------------------------------
def test_without_a_prefix_it_returns_upstreams_list_not_nothing(monkeypatch):
    """Returning [] would make _game_ran look nowhere at all."""
    monkeypatch.setattr(vulkan, "prefix", lambda: None)
    assert userdata.roots() == userdata._orig()


def test_a_prefix_lookup_that_raises_falls_back_rather_than_propagating(monkeypatch):
    """prefix_for reads the Steam library; a game outside it is ordinary, not a fault."""
    def boom():
        raise OSError("no steam here")
    monkeypatch.setattr(vulkan, "prefix", boom)
    assert userdata.roots() == userdata._orig()


def test_the_prefix_comes_first_and_upstream_still_follows(tmp_path, monkeypatch):
    pfx = make_prefix(tmp_path)
    monkeypatch.setattr(vulkan, "prefix", lambda: pfx)
    got = userdata.roots()
    assert got[:len(userdata.USER_SUBDIRS)] == userdata.prefix_roots(pfx)
    assert got[len(userdata.USER_SUBDIRS):] == userdata._orig()


# --- the patch point --------------------------------------------------------------------
def test_it_is_patched_where_the_caller_reads_it():
    """diagnose/__init__ lists _user_data_roots in PATCHED, so it is not on the
    package: evidence.py reads model._user_data_roots, and that is what moved."""
    assert model._user_data_roots is userdata.roots
    from core import diagnose
    assert not hasattr(diagnose, "_user_data_roots")


# --- watch.RECORD -----------------------------------------------------------------------
def test_watch_record_would_not_land_in_the_home_root():
    """core.watch does not import on Linux, so this is pre-emptive: if some day it
    does, RECORD must already be under XDG and not ~/dlss5-autopilot/."""
    if paths.watch is None:
        pytest.skip("core.watch does not import here, which is the Linux case")
    assert Path(paths.watch.RECORD) == paths.STATE / "sightings.json"


# --- end to end: the question the shim exists to answer -------------------------------------
def test_a_save_written_inside_the_prefix_proves_the_game_ran(tmp_path, monkeypatch):
    """Without the shim `_game_ran` cannot see into the prefix at all, and the
    report says "has not been started since the install" to somebody who played."""
    from core.diagnose import evidence

    install_dir = tmp_path / "steamapps" / "common" / "Fixture" / "Binaries" / "Win64"
    install_dir.mkdir(parents=True)
    exe = "Fixture-Win64-Shipping.exe"

    # The game's own per-user folder, where upstream looks first. Its name comes
    # from the exe stem with the Unreal suffix cut off - see _user_data_names.
    pfx = make_prefix(tmp_path)
    saved = (pfx / "drive_c" / "users" / "steamuser" / "AppData" / "Local"
             / "Fixture" / "Saved" / "Logs")
    saved.mkdir(parents=True)
    played = saved / "Fixture.log"
    played.write_text("a session", encoding="utf8")

    installed_at = played.stat().st_mtime - 3600      # the file is newer than the install

    monkeypatch.setattr(vulkan, "prefix", lambda: None)
    assert evidence._game_ran(install_dir, exe, installed_at, set()) == ("", 0.0)

    monkeypatch.setattr(vulkan, "prefix", lambda: pfx)
    what, when = evidence._game_ran(install_dir, exe, installed_at, set())
    assert what == "Saved/Logs/Fixture.log" and when == played.stat().st_mtime

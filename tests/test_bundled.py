"""linuxport.bundled: the two files that cannot be used from inside the mount."""
from __future__ import annotations

import os
import re
import stat
from pathlib import Path

import pytest

from linuxport import archive, bundled, paths, vklayer


@pytest.fixture
def appdir(tmp_path, monkeypatch):
    """An AppDir shaped like the built one, with the package under usr/app."""
    root = tmp_path / "mount"
    tree = root / "usr" / "app" / "linuxport"
    tree.mkdir(parents=True)
    (root / "usr" / "bin").mkdir(parents=True)
    (root / "usr" / "app" / bundled.WRAPPER_NAME).write_text("#!/bin/bash\necho one\n")
    monkeypatch.setattr(bundled, "APPDIR", str(root))
    monkeypatch.setattr(bundled, "_TREE", root / "usr" / "app")
    monkeypatch.setattr(paths, "DATA", tmp_path / "xdg-data")
    return root


# --- the name, which is load-bearing ------------------------------------------------------
def test_the_wrapper_keeps_the_name_the_detector_matches(appdir):
    """vklayer.enabled_in matches (^|[\\s/])vklayer-run(\\s|$). A prefixed name -
    dlss5-vklayer-run - is preceded by "-", so a correctly wrapped game reads as
    "layer off", while the substring checks elsewhere in vklayer.py still match.
    The failure is silent, which is why the name is asserted rather than assumed."""
    got = bundled.wrapper()
    assert got.name == "vklayer-run"
    assert vklayer.enabled_in(f"{got} %command%")
    assert not vklayer.enabled_in(f"{got.parent}/dlss5-vklayer-run %command%")


# --- where it goes ------------------------------------------------------------------------
def test_it_is_copied_out_of_the_mount(appdir):
    got = bundled.wrapper()
    assert got == paths.DATA / "bin" / "vklayer-run"
    assert not got.is_relative_to(appdir)
    assert got.read_text() == "#!/bin/bash\necho one\n"


def test_it_is_executable(appdir):
    got = bundled.wrapper()
    assert os.access(got, os.X_OK)
    assert stat.S_IMODE(got.stat().st_mode) & stat.S_IXUSR


def test_from_a_checkout_it_is_the_file_in_the_tree(tmp_path, monkeypatch):
    monkeypatch.setattr(bundled, "APPDIR", None)
    got = bundled.wrapper()
    assert got == bundled._TREE / "vklayer-run"


# --- upgrades -----------------------------------------------------------------------------
def test_a_changed_wrapper_replaces_the_old_copy(appdir):
    first = bundled.wrapper()
    assert first.read_text().endswith("one\n")
    (appdir / "usr" / "app" / bundled.WRAPPER_NAME).write_text("#!/bin/bash\necho two\n")
    assert bundled.wrapper().read_text().endswith("two\n")


def test_an_unchanged_wrapper_is_left_alone(appdir):
    """Replaced by rename, and only when the bytes differ: bash reads a running
    script from its descriptor, and a game session may be inside it right now."""
    first = bundled.wrapper()
    before = first.stat().st_mtime_ns
    assert bundled.wrapper().stat().st_mtime_ns == before


def test_an_unwritable_destination_falls_back_to_the_copy_in_the_mount(appdir, monkeypatch):
    def refuse(*a, **k):
        raise OSError("read-only")
    monkeypatch.setattr(bundled.os, "replace", refuse)
    got = bundled.wrapper()
    assert got.is_relative_to(appdir)          # worse, but not nothing


# --- the 7z ------------------------------------------------------------------------------
def test_a_bundled_7z_is_offered_before_the_system_one(appdir, tmp_path):
    exe = appdir / "usr" / "bin" / "7zz"
    exe.write_text("#!/bin/sh\n")
    exe.chmod(0o755)
    assert bundled.seven_zip() == exe
    first = archive.commands(tmp_path / "a.7z", tmp_path / "out")[0]
    assert first[0] == str(exe) and first[1] == "x"


def test_a_non_executable_7z_is_not_offered(appdir):
    (appdir / "usr" / "bin" / "7zz").write_text("#!/bin/sh\n")
    assert bundled.seven_zip() is None


def test_from_a_checkout_there_is_no_bundled_7z(monkeypatch):
    monkeypatch.setattr(bundled, "APPDIR", None)
    assert bundled.seven_zip() is None


# --- the patch point ----------------------------------------------------------------------
def test_install_points_vklayer_at_the_stable_copy(appdir):
    bundled.install()
    try:
        assert vklayer.WRAPPER == paths.DATA / "bin" / "vklayer-run"
        # what with_layer would write into localconfig.vdf, which outlives the mount
        line = vklayer.with_layer("%command%", on=True)
        assert str(vklayer.WRAPPER) in line and "/mount/" not in line
    finally:
        bundled.install()

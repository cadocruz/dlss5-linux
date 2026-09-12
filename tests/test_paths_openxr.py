"""linuxport.paths and linuxport.openxr: the two shims that exist only to
keep core off Windows-shaped locations and APIs."""
from __future__ import annotations

from pathlib import Path

import pytest

from core import diagnose, installer, library, log, net, openxr, profiles, sources
from linuxport import openxr as lxr, paths


def test_every_localappdata_path_is_under_xdg():
    assert Path(net.CACHE) == paths.CACHE / "cache"
    assert Path(sources._API_CACHE) == paths.CACHE / "api-cache"
    assert Path(profiles.DIR) == paths.CONFIG / "profiles"
    assert Path(library.FILE) == paths.CONFIG / "library.json"
    assert Path(log.DIR) == paths.STATE
    assert Path(diagnose.STANDALONE_LOG) == paths.STATE / "standalone-dlssnr.log"
    # prefs.FILE is redirected per test by conftest; the module default is XDG too
    assert paths.CONFIG.name == paths.APP and paths.CACHE.name == paths.APP


def test_paths_honour_the_xdg_variables(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "k"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    import importlib
    fresh = importlib.reload(paths)
    try:
        assert fresh.CACHE == tmp_path / "c" / paths.APP
        assert fresh.CONFIG == tmp_path / "k" / paths.APP
        assert fresh.STATE == tmp_path / "s" / paths.APP
    finally:
        monkeypatch.undo()
        importlib.reload(paths)
        paths.install()


def test_no_core_path_points_at_a_dlss5_autopilot_folder():
    for p in (net.CACHE, sources._API_CACHE, profiles.DIR, library.FILE, log.DIR, diagnose.STANDALONE_LOG):
        assert "dlss5-autopilot" not in str(p), p


# --- openxr ------------------------------------------------------------------------------
def test_openxr_reads_answer_not_registered():
    assert openxr.existing_registration() is None
    assert openxr.registrations() == []
    assert openxr.unregister() is False


def test_openxr_install_refuses_with_a_reason(tmp_path):
    with pytest.raises(RuntimeError, match="not available under Proton"):
        openxr.install_layer(tmp_path / "ReShade_Setup.exe")
    assert lxr.REASON.startswith("VR through OpenXR")


def test_a_vr_preview_does_not_crash_and_carries_the_upstream_warning(game):
    """installer.preview reaches openxr when Options.vr is set (64-bit only)."""
    opt = installer.Options(path="native", native_dlss=True, vr=True)
    pv = installer.preview(game, opt)
    assert any("OpenXR" in line for line in pv.outside), pv.outside
    assert any("headset" in w for w in pv.warnings), pv.warnings

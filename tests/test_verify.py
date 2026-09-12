"""linuxport.verify: the Proton checks layered over upstream's diagnosis.

Upstream's `diagnose.analyse` is stubbed: what it finds is its own business.
These tests cover what the Linux layer adds -- override presence, prefix and
NGX bridge, and the per-route log readers -- against fixture logs.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from conftest import FIXTURES
from core import diagnose, installer
from linuxport import proton, verify

LOGS = FIXTURES / "logs"


@pytest.fixture
def quiet_upstream(monkeypatch):
    """analyse() returns an empty report for the route the manifest names;
    the process check never shells out."""
    def analyse(root: Path):
        rep = diagnose.Report()
        rep.route = (diagnose._manifest(root) or {}).get("path", "")
        return rep
    monkeypatch.setattr(diagnose, "analyse", analyse)
    monkeypatch.setattr(proton, "running", lambda g: False)


def _manifest(root: Path, route: str, proxy: str = "dxgi.dll") -> None:
    (root / installer.MANIFEST).write_text(json.dumps({"path": route, "proxy": proxy, "files": []}),
                                           encoding="utf-8")
    old = time.time() - 3600
    os.utime(root / installer.MANIFEST, (old, old))   # logs written afterwards count as fresh


def _log(root: Path, name: str, text: str) -> Path:
    p = root / name
    p.write_text(text, encoding="utf-8")
    return p


def rows(out, title):
    return [(lvl, detail) for lvl, t, detail in out if t == title]


def row(out, title):
    r = rows(out, title)
    assert len(r) == 1, (title, r)
    return r[0]


# --- overrides, prefix, bridge ------------------------------------------------------------
def test_non_steam_game_without_overrides_is_told_the_exact_string(fake_home, game, quiet_upstream):
    _manifest(game.install_dir, "native")
    out = verify.run(game)
    lvl, detail = row(out, "WINEDLLOVERRIDES")
    assert lvl == "BAD" and "missing" in detail and 'WINEDLLOVERRIDES="dxgi=n,b"' in detail
    assert row(out, "Proton prefix") == ("WARN", "not found")
    assert not rows(out, "NGX bridge")


def test_complete_overrides_prefix_and_bridge_are_ok(steam_game, quiet_upstream, monkeypatch):
    _manifest(steam_game.install_dir, "native")
    monkeypatch.setattr(proton, "current_launch_options", lambda g: 'WINEDLLOVERRIDES="dxgi=n,b" %command%')
    out = verify.run(steam_game)
    assert row(out, "WINEDLLOVERRIDES") == ("OK", "dxgi=n,b present")
    assert row(out, "Proton prefix")[0] == "OK"
    assert row(out, "NGX bridge") == ("OK", "_nvngx.dll in prefix system32")


def test_partial_overrides_name_the_missing_bridge_entries(fake_home, game, quiet_upstream, monkeypatch):
    game.api = "DX11"
    _manifest(game.install_dir, "optiscaler", proxy="winmm.dll")
    monkeypatch.setattr(proton, "current_launch_options", lambda g: 'WINEDLLOVERRIDES="winmm=n,b" %command%')
    lvl, detail = row(verify.run(game), "WINEDLLOVERRIDES")
    assert lvl == "BAD" and "incomplete" in detail
    assert "d3d12=n,b;d3d12core=n,b" in detail and "80004002" in detail


def test_no_bridge_in_the_prefix_is_bad(steam, steam_game, quiet_upstream):
    (steam["pfx"] / "drive_c" / "windows" / "system32" / "_nvngx.dll").unlink()
    _manifest(steam_game.install_dir, "native")
    lvl, detail = row(verify.run(steam_game), "NGX bridge")
    assert lvl == "BAD" and "never run under Proton" in detail


def test_vulkan_layer_installs_skip_the_override_check(fake_home, game, quiet_upstream):
    _manifest(game.install_dir, "native", proxy="(vulkan layer)")
    assert not rows(verify.run(game), "WINEDLLOVERRIDES")


def test_running_game_is_a_warning(fake_home, game, quiet_upstream, monkeypatch):
    _manifest(game.install_dir, "native")
    monkeypatch.setattr(proton, "running", lambda g: True)
    assert row(verify.run(game), "process")[0] == "WARN"


def test_upstream_findings_are_deduplicated_and_verdict_kept(fake_home, game, monkeypatch):
    def analyse(root):
        rep = diagnose.Report()
        rep.route = "native"
        rep.verdict = "Working."
        rep.add("ok", "NR", "device 1")
        rep.add("ok", "NR", "device 1")      # upstream re-registers per device
        rep.add("warn", "NR", "device 2")
        return rep
    monkeypatch.setattr(diagnose, "analyse", analyse)
    monkeypatch.setattr(proton, "running", lambda g: False)
    out = verify.run(game)
    assert out[0] == ("INFO", "route", "native")
    assert rows(out, "NR") == [("OK", "device 1"), ("WARN", "device 2")]
    assert row(out, "verdict") == ("INFO", "Working.")


def test_no_manifest_reports_no_route(fake_home, game, quiet_upstream):
    assert verify.run(game)[0] == ("INFO", "route", "(no manifest)")


# --- OptiScaler log reader ----------------------------------------------------------------
def test_good_optiscaler_log(fake_home, game, quiet_upstream):
    root = game.install_dir
    _manifest(root, "optiscaler", proxy="winmm.dll")
    _log(root, "OptiScaler.log", (LOGS / "OptiScaler.good.log").read_text(encoding="utf-8"))
    out = verify.run(game)
    assert row(out, "dlss capability") == ("OK", "dlss: true")
    assert not rows(out, "device lost")
    assert row(out, "NR dispatching") == ("OK", "2 build(s), latest 3840x2160")
    assert row(out, "NR forwarder") == ("OK", "loaded")
    assert row(out, "DLSS runtime")[1].startswith("v310.9.0")
    assert row(out, "NR cost")[1].startswith("6.9 / 7.4 / 9.1 ms")
    assert row(out, "D3D12 bridge")[0] == "OK"
    assert row(out, "OptiScaler errors") == ("OK", "none")      # queryNvapi is benign
    assert not rows(out, "OptiScaler.log")                      # written after the install


def test_optiscaler_log_older_than_the_install(fake_home, game, quiet_upstream):
    root = game.install_dir
    _manifest(root, "optiscaler")
    p = _log(root, "OptiScaler.log", "[I] nothing\n")
    ancient = time.time() - 7200
    os.utime(p, (ancient, ancient))
    assert row(verify.run(game), "OptiScaler.log") == ("WARN", "older than the install -- launch once")


def test_xess_fallback_device_lost_and_bad_bridge(fake_home, game, quiet_upstream):
    root = game.install_dir
    _manifest(root, "optiscaler", proxy="winmm.dll")
    text = (LOGS / "OptiScaler.good.log").read_text(encoding="utf-8")
    text = text.replace("dlss: true", "dlss: false")
    text = text.replace("D3D12 interop device created on adapter NVIDIA GeForce RTX 5090",
                        "GetD3D12DeviceFromD3D11 D3D12CreateDevice failed: 80004002")
    text += "[E] VK_ERROR_DEVICE_LOST\n[E] VK_ERROR_DEVICE_LOST\n[2026] [E] something real broke\n"
    _log(root, "OptiScaler.log", text)
    out = verify.run(game)
    lvl, detail = row(out, "dlss capability")
    assert lvl == "WARN" and "XeSS" in detail
    assert row(out, "device lost") == ("BAD", "VK_ERROR_DEVICE_LOST x2 -- GPU fault; see FINDINGS.md (the v0.2.x deadlock)")
    lvl, detail = row(out, "D3D12 bridge")
    assert lvl == "BAD" and "d3d12=n,b;d3d12core=n,b" in detail
    lvl, detail = row(out, "OptiScaler errors")
    assert lvl == "WARN" and detail.startswith("1: ")


def test_optiscaler_without_dispatch_or_forwarder(fake_home, game, quiet_upstream):
    root = game.install_dir
    _manifest(root, "optiscaler")
    _log(root, "OptiScaler.log", "[I] Upscaler support - fsr4: false, dlss: true\n")
    out = verify.run(game)
    assert row(out, "NR dispatching")[0] == "WARN"
    assert row(out, "NR forwarder") == ("WARN", "not loaded")
    assert not rows(out, "DLSS runtime") and not rows(out, "NR cost")


def test_multipass_with_several_features_alive_is_flagged(fake_home, game, quiet_upstream):
    root = game.install_dir
    _manifest(root, "optiscaler")
    text = (LOGS / "OptiScaler.good.log").read_text(encoding="utf-8") + "[I] CreateFeature HandleId: 2\n"
    _log(root, "OptiScaler.log", text)
    _log(root, "OptiScaler.ini", "[DlssNr]\nEnabled=true\nPasses=2\n")
    lvl, detail = row(verify.run(game), "multipass")
    assert lvl == "WARN" and "Passes=2 with 2 DLSS features" in detail
    _log(root, "OptiScaler.ini", "[DlssNr]\nEnabled=true\nPasses=1\n")
    assert not rows(verify.run(game), "multipass")


# --- ReShade log reader (native / feeder) ---------------------------------------------------
def test_good_reshade_log_on_the_native_route(fake_home, game, quiet_upstream):
    root = game.install_dir
    _manifest(root, "native")
    _log(root, "ReShade.log", (LOGS / "ReShade.good.log").read_text(encoding="utf-8"))
    out = verify.run(game)
    assert row(out, "NR feature") == ("OK", "2 creation(s), latest 3840x2160")
    assert row(out, "Streamline")[0] == "WARN"
    assert row(out, "DLAA")[0] == "INFO"
    assert row(out, "NR runtime") == ("OK", "NR runtime" and "DLSSNR 310.8.0 initialised")
    assert row(out, "ReShade errors")[0] == "OK"                # EvaluateFeature_C is baseline
    assert not rows(out, "motion vectors")


def test_reshade_log_with_a_real_error_and_no_feature(fake_home, game, quiet_upstream):
    root = game.install_dir
    _manifest(root, "native")
    _log(root, "ReShade.log", "| INFO  | init\n| ERROR | Failed to load add-on: bad image\n")
    out = verify.run(game)
    assert row(out, "NR feature")[0] == "WARN"
    lvl, detail = row(out, "ReShade errors")
    assert lvl == "WARN" and detail.startswith("1: ")


def test_feeder_motion_vectors_and_d3d12_create_fault(fake_home, game, quiet_upstream):
    root = game.install_dir
    _manifest(root, "feeder")
    _log(root, "ReShade.log", "| INFO  | feature 1 created (DLAA) for NR input 1920x1080\n")
    _log(root, "dlss5-feed.log", (LOGS / "dlss5-feed.bad.log").read_text(encoding="utf-8"))
    out = verify.run(game)                       # game.api is DX12
    lvl, detail = row(out, "motion vectors")
    assert lvl == "WARN" and "12%" in detail and "VORT" in detail
    assert row(out, "feeder on D3D12")[0] == "BAD"


def test_feeder_on_d3d11_with_healthy_vectors(fake_home, game, quiet_upstream):
    game.api = "DX11"
    root = game.install_dir
    _manifest(root, "feeder")
    _log(root, "dlss5-feed.log", "[feed] MV probe 64x64 sample: 87% non-zero\n[feed] CreateFeature raised once\n")
    out = verify.run(game)
    assert row(out, "motion vectors") == ("OK", "87% of the probe non-zero")
    assert not rows(out, "feeder on D3D12")     # the create fault is a D3D12 problem

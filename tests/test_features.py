"""linuxport.features: the upstream window features, headless."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from conftest import make_game
from core import diagnose, dlss, installer, optiscaler, prefs, reshade_ini
from linuxport import features, linux_gpu, proton


# --- route notes ----------------------------------------------------------------------------
def test_route_notes_start_with_the_blurb_and_carry_conflicts(game, monkeypatch):
    monkeypatch.setattr(linux_gpu, "_smi", lambda: ("NVIDIA GeForce RTX 5090", "610.57.04", "12.0"))
    sup = dlss.detect(game.install_dir, game.folder, game.api, game.bitness, sm=120)
    for path in sup.options:
        blurb, warns = features.route_notes(game, path, sup)
        assert blurb[0] == dlss.BLURB[path]
        for _kind, line in getattr(dlss, "CONFLICTS", {}).get(path, ()):
            # Nothing foreign in the fixture folder, so every conflict line stays
            # informational: "ingame" always, "folder" until something is there.
            assert line in blurb, (path, line)
        # nothing about the driver at all: not "older than DLSS 5", and not
        # the 616.64 evaluate fault either (both come from dlss.driver_warning)
        assert not any("driver" in w.lower() for w in warns), (path, warns)



def test_a_folder_conflict_warns_only_when_another_ngx_hook_is_there(game, monkeypatch):
    """The 1.9.0 split: same line, warning or not depending on the folder."""
    monkeypatch.setattr(installer, "other_ngx_hooks", lambda root, path="": [])
    blurb, warns = features.route_notes(game, dlss.NATIVE, None)
    assert not warns and any("two NGX hooks" in b for b in blurb)

    monkeypatch.setattr(installer, "other_ngx_hooks", lambda root, path="": ["dlssg_to_fsr3.dll"])
    blurb, warns = features.route_notes(game, dlss.NATIVE, None)
    assert any("dlssg_to_fsr3.dll" in w and "two NGX hooks" in w for w in warns)

def test_route_notes_include_a_driver_warning_when_the_gate_says_so(game):
    """A pre-DLSS-5 driver number is the one case the Linux gate lets through."""
    _, warns = features.route_notes(game, dlss.FEEDER, None, driver="470.1")
    assert any("older than DLSS 5" in w for w in warns)


def test_has_ray_reconstruction_reads_the_evidence():
    assert not features.has_ray_reconstruction(None)
    assert not features.has_ray_reconstruction(SimpleNamespace(evidence=["nvngx_dlss.dll"]))
    assert features.has_ray_reconstruction(SimpleNamespace(evidence=["Plugins/x/nvngx_dlssd.dll"]))


def test_swap_warning_and_vr_reason_are_text():
    assert any("nvngx_dlssd.dll" in l for l in features.swap_warning("nvngx_dlssd.dll"))
    assert "Proton" in features.vr_reason()


# --- overlay key ----------------------------------------------------------------------------------
def test_overlay_key_round_trip():
    names = features.overlay_key_names()
    assert names[0] == "route default" and "Insert" in names
    assert features.current_overlay_key() == "route default"
    assert features.set_overlay_key("F10") == reshade_ini.OVERLAY_KEYS["F10"]
    assert features.current_overlay_key() == "F10"
    assert reshade_ini.overlay_key_name("Insert") == "F10"
    assert features.set_overlay_key("route default") == 0
    assert prefs.get("overlay_key") == 0
    assert reshade_ini.overlay_key_name("Insert") == "Insert"


# --- library cache --------------------------------------------------------------------------------
def test_library_round_trip_is_gated_on_the_port_version(tmp_path, monkeypatch):
    from core import library
    monkeypatch.setattr(library, "FILE", tmp_path / "library.json")
    assert features.library_load(120) is None
    g = make_game(tmp_path / "Game A")
    rows = {(str(g.folder), str(g.exe)): [True, "", "native", installer.STABLE, "", ""]}
    features.library_save([g], rows, 120)
    data = json.loads((tmp_path / "library.json").read_text(encoding="utf-8"))
    assert data["app_version"] == features.LIBRARY_VERSION and "+linux" in data["app_version"]
    got = features.library_load(120)
    assert got is not None
    gs, rows_back, changed = got
    assert [x.name for x in gs] == ["Game A"] and changed == []
    assert list(rows_back.values())[0][2] == "native"
    monkeypatch.setattr(features, "LIBRARY_VERSION", "other")
    assert features.library_load(120) is None


def test_scan_on_start_defaults_to_on():
    assert features.scan_on_start() is True
    features.set_scan_on_start(False)
    assert features.scan_on_start() is False


# --- community + share --------------------------------------------------------------------------------
def test_community_lines_never_raise(game, monkeypatch):
    from core import community
    monkeypatch.setattr(community, "fetch", lambda force=False: (_ for _ in ()).throw(RuntimeError("offline")))
    assert features.community_lines(game, "native") == []
    monkeypatch.setattr(community, "fetch", lambda force=False: {"games": {game.exe.name.lower(): {
        "routes": {"native": {"worked": 6, "failed": 1}}, "drivers": {}}}})
    lines = features.community_lines(game, "native")
    assert lines and "7 reports" in lines[0]


def test_share_record_carries_os_and_proton(steam, steam_game, monkeypatch):
    monkeypatch.setattr(linux_gpu, "_smi", lambda: ("NVIDIA GeForce RTX 5090", "610.57.04", "12.0"))
    (steam["pfx"].parent / "version").write_text("GE-Proton11-3\n", encoding="utf-8")
    rep = SimpleNamespace(verdict="Working. 23 creations", ran=True)
    rec = features.share_record(steam_game, "native", rep, manifest={"path": "native", "opti_build": ""})
    assert rec["os"] == "linux" and rec["proton"] == "GE-Proton11-3"
    assert rec["result"] == "worked" and rec["driver"] == "610.57.04" and rec["sm"] == "sm_120"
    assert rec["tool"] == features.LIBRARY_VERSION
    url = features.share_url(steam_game, "native", rep, manifest={"path": "native"})
    assert url.startswith("https://github.com/Kizzuwatnaa/DLSS5-Autopilot/issues/new?labels=result")
    assert "%22os%22%3A%22linux%22" in url                 # the record block carries it
    failed = features.share_record(steam_game, "native", SimpleNamespace(verdict="Nothing ran"), manifest={})
    assert failed["result"] == "failed"


def test_proton_version_is_none_without_steams_record(fake_home, game):
    assert proton.proton_version(game) is None


# --- aim for fps --------------------------------------------------------------------------------------
@pytest.mark.parametrize("route,api,bits,expected", [
    (dlss.OPTI, "DX12", 64, True), (dlss.OPTI, "DX11", 32, True),
    (dlss.FEEDER, "DX11", 64, True), (dlss.FEEDER, "DX12", 64, False), (dlss.FEEDER, "DX11", 32, False),
    (dlss.NATIVE, "DX12", 64, False),
])
def test_work_applies(tmp_path, route, api, bits, expected):
    g = make_game(tmp_path / "G", api=api, bitness=bits)
    assert features.work_applies(g, route) is expected


def _feeder_session(game, res: int, fps: float) -> None:
    d = game.install_dir
    (d / "dlss5-feed.cfg").write_text(f"enabled=1\nwork_resolution={res}\n", encoding="utf-8")
    (d / diagnose.FEED_LOG).write_text(
        f"[feed] starting\n[feed] 3600 frames: feed CPU 1.2 ms/frame ... {fps} fps\n", encoding="utf-8")


def test_autotune_after_one_session_gives_a_bounded_step(tmp_path):
    g = make_game(tmp_path / "Dreamfall", api="DX11", bitness=64)
    _feeder_session(g, 100, 47)
    rep = SimpleNamespace(ran=True, verdict="Working.")
    tune = features.autotune_after(g, dlss.FEEDER, 85, rep, 60)
    assert tune is not None
    assert tune.measured.resolution == 100 and tune.measured.fps == 47      # from the cfg, not the slider
    assert tune.suggestion is not None and tune.suggestion.resolution < 100
    assert any("47 fps" in ln for ln in tune.suggestion.lines)


def test_autotune_after_two_sessions_solves_exactly(tmp_path):
    g = make_game(tmp_path / "Dreamfall", api="DX11", bitness=64)
    rep = SimpleNamespace(ran=True)
    _feeder_session(g, 100, 47)
    features.autotune_after(g, dlss.FEEDER, 100, rep, 60)
    _feeder_session(g, 70, 70)
    tune = features.autotune_after(g, dlss.FEEDER, 70, rep, 60)
    assert tune.suggestion is not None and tune.suggestion.exact
    assert 70 <= tune.suggestion.resolution <= 100


def test_autotune_is_silent_without_a_target_a_run_or_a_dial(tmp_path):
    g = make_game(tmp_path / "G", api="DX11", bitness=64)
    _feeder_session(g, 100, 47)
    assert features.autotune_after(g, dlss.FEEDER, 100, SimpleNamespace(ran=True), None) is None
    assert features.autotune_after(g, dlss.FEEDER, 100, SimpleNamespace(ran=False), 60) is None
    assert features.autotune_after(g, dlss.NATIVE, 100, SimpleNamespace(ran=True), 60) is None


def test_apply_tune_writes_the_feeder_config(tmp_path):
    g = make_game(tmp_path / "G", api="DX11", bitness=64)
    _feeder_session(g, 100, 47)
    logs: list[str] = []
    features.apply_tune(g, dlss.FEEDER, 77, logs.append)
    text = (g.install_dir / "dlss5-feed.cfg").read_text(encoding="utf-8")
    assert "work_resolution=77" in text and "enabled=1" in text
    assert any("77" in l for l in logs)


def test_apply_tune_writes_optiscaler_working_scale(tmp_path):
    g = make_game(tmp_path / "G")
    (g.install_dir / optiscaler.INI).write_text("[DlssNr]\nEnabled=true\nWorkingScale=1.0\n", encoding="utf-8")
    features.apply_tune(g, dlss.OPTI, 75)
    text = (g.install_dir / optiscaler.INI).read_text(encoding="utf-8")
    assert "WorkingScale=0.75" in text


# --- bug report -----------------------------------------------------------------------------------------
def test_report_url_prefixes_linux_and_carries_the_proton_section(steam, steam_game, monkeypatch):
    monkeypatch.setattr(linux_gpu, "_smi", lambda: ("NVIDIA GeForce RTX 5090", "610.57.04", "12.0"))
    answers = {"started": "no", "happened": "black screen after the logos"}
    url, body, clip = features.report_url(steam_game, "native", None, answers)
    assert url.startswith("https://github.com/Kizzuwatnaa/DLSS5-Autopilot/issues/new?title=%5Blinux%5D%20bug")
    assert body.startswith("**Proton**") and "- prefix: found" in body and "- ngx bridge in system32: yes" in body
    assert "black screen after the logos" in body
    assert clip is False


def test_report_url_falls_back_to_the_clipboard_when_too_long(fake_home, game, monkeypatch):
    monkeypatch.setattr(diagnose, "issue_body", lambda *a, **k: "x" * 9000)
    url, body, clip = features.report_url(game, "native", SimpleNamespace(verdict="Nothing ran", ran=False), None)
    assert clip is True and "clipboard" in url and len(body) > 9000
    assert "not%20working" in url and len(url) < 400


# --- install-page helpers ---------------------------------------------------------------------------------
def test_opti_builds_mark_the_proton_line_and_default_to_it():
    keys = [k for k, _ in features.opti_builds()]
    assert keys == list(optiscaler.BUILDS) and features.default_opti_build() == optiscaler.FORK
    assert any("works under Proton" in t for k, t in features.opti_builds() if k == optiscaler.FORK)


def test_dlssd_choices_and_manifest_route(game):
    assert features.dlssd_choices({}) == [] and features.dlssd_choices({"dlssd": [{"label": "310.9.0"}]}) == ["310.9.0"]
    assert features.manifest_route(game) == ""
    (game.install_dir / installer.MANIFEST).write_text(json.dumps({"path": "feeder"}), encoding="utf-8")
    assert features.manifest_route(game) == "feeder"

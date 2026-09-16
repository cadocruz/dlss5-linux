"""The shims phase 1 will touch: gpu, games, pe, state, pins.

These pin down the behaviour that has to survive the re-vendor of core/.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import FIXTURES, make_game
from core import dlss, games as core_games, gpu, installer, net, optiscaler, pe, sources
from linuxport import games as lgames, linux_gpu, pe as lpe, pins, state


# --- gpu ---------------------------------------------------------------------------------
def test_detect_reads_compute_capability_from_nvidia_smi(monkeypatch):
    monkeypatch.setattr(linux_gpu, "_smi", lambda: ("NVIDIA GeForce RTX 5090", "610.57.04", "12.0"))
    assert gpu.detect() == ("NVIDIA GeForce RTX 5090", 120)
    assert gpu.driver_version() == "610.57.04"


def test_detect_falls_back_to_the_card_name_when_compute_cap_is_odd(monkeypatch):
    monkeypatch.setattr(linux_gpu, "_smi", lambda: ("NVIDIA GeForce RTX 4080", "580.0", "[N/A]"))
    assert gpu.detect() == ("NVIDIA GeForce RTX 4080", 89)


def test_detect_without_nvidia_smi(monkeypatch):
    monkeypatch.setattr(linux_gpu, "_smi", lambda: None)
    assert gpu.detect() == (None, None)
    assert gpu.driver_version() is None


@pytest.mark.parametrize("cap,sm", [("7.5", 75), ("8.6", 86), ("8.9", 89), ("12.0", 120)])
def test_compute_capability_maps_to_the_upstream_sm_numbers(monkeypatch, cap, sm):
    monkeypatch.setattr(linux_gpu, "_smi", lambda: ("NVIDIA GPU", "610.0", cap))
    assert gpu.detect()[1] == sm


def test_driver_gate_accepts_the_linux_610_branch(monkeypatch):
    monkeypatch.setattr(linux_gpu, "_smi", lambda: ("NVIDIA GeForce RTX 5090", "610.57.04", "12.0"))
    assert gpu.driver_at_least("616.56") is True
    monkeypatch.setattr(linux_gpu, "_smi", lambda: ("NVIDIA GeForce RTX 3080", "470.256.02", "8.6"))
    assert gpu.driver_at_least("616.56") is False


def test_driver_gate_takes_the_version_as_an_argument(monkeypatch):
    """dlss.driver_warning (upstream 1.8.0) passes the version it already read."""
    monkeypatch.setattr(linux_gpu, "_smi", lambda: None)
    assert gpu.driver_at_least("616.56", "610.57.04") is True
    assert gpu.driver_at_least("616.56", "616.64") is True
    assert gpu.driver_at_least("616.56", "470.1") is False
    assert gpu.driver_at_least("616.56", None) is None
    assert gpu.driver_at_least("616.56", "garbage") is None


def test_the_windows_evaluate_fault_gate_says_no_on_the_610_branch():
    """DRIVER_FAULT_MIN asks "is this driver faulty?"; a blanket True there
    printed the 616.64 crash warning on every route and pinned renodx to 4.55."""
    assert gpu.driver_at_least(sources.DRIVER_FAULT_MIN, "610.57.04") is False
    assert gpu.driver_at_least("616.65", "610.57.04") is False
    assert gpu.driver_at_least("616.56", "610.57.04") is True     # ...but new enough for DLSS 5
    # below the 610 branch there is nothing to say about Linux, and
    # upstream's own comparison decides
    assert gpu.driver_at_least(sources.DRIVER_FAULT_MIN, "470.1") is False
    assert gpu.driver_at_least("470.0", "470.1") is True


def test_driver_warning_is_silent_on_a_linux_driver(monkeypatch):
    """The upstream per-route driver notes must not fire on the 610 branch."""
    monkeypatch.setattr(linux_gpu, "_smi", lambda: ("NVIDIA GeForce RTX 5090", "610.57.04", "12.0"))
    for route in ("native", "feeder", "optiscaler", "upstream", "bridge", "renodx"):
        assert dlss.driver_warning(route, "610.57.04") is None, route
    # and the pin that goes with it: no renodx clamp on this branch
    assert gpu.driver_at_least(sources.DRIVER_FAULT_MIN) is False


def test_driver_warning_still_fires_on_a_pre_dlss5_driver(monkeypatch):
    monkeypatch.setattr(linux_gpu, "_smi", lambda: ("NVIDIA GeForce RTX 3080", "470.256.02", "8.6"))
    warn = dlss.driver_warning("feeder", "470.256.02")
    assert warn and "older than DLSS 5" in warn


# --- games -------------------------------------------------------------------------------
def test_steam_root_prefers_the_env_var(fake_home, tmp_path, monkeypatch):
    custom = tmp_path / "custom-steam"
    (custom / "steamapps").mkdir(parents=True)
    monkeypatch.setenv("STEAM_ROOT", str(custom))
    assert lgames.steam_root() == custom


def test_steam_root_finds_native_and_flatpak_layouts(fake_home):
    assert lgames.steam_root() is None
    flat = fake_home / ".var/app/com.valvesoftware.Steam/.local/share/Steam"
    (flat / "steamapps").mkdir(parents=True)
    assert lgames.steam_root() == flat.resolve()
    native = fake_home / ".steam" / "steam"
    (native / "steamapps").mkdir(parents=True)
    assert lgames.steam_root() == native.resolve()   # listed first


def test_scan_steam_drops_valve_tooling(monkeypatch, tmp_path):
    names = ["Proton 9.0 (Beta)", "SteamVR", "Steam Linux Runtime 3.0 (sniper)", "Stellar Blade", "The Lab"]
    fake = [core_games.Game(name=n, folder=tmp_path / n) for n in names]
    monkeypatch.setattr(lgames, "_orig_scan", lambda: fake)
    assert [g.name for g in core_games.scan_steam()] == ["Stellar Blade"]


def test_scan_all_adds_remembered_folders_once(monkeypatch, tmp_path):
    steam_game = make_game(tmp_path / "Steam Game")
    manual = tmp_path / "Manual Game"
    manual.mkdir()
    monkeypatch.setattr(lgames, "_orig_scan", lambda: [steam_game])
    lgames.remember_folder(manual)
    lgames.remember_folder(steam_game.folder)          # already in Steam: not duplicated
    lgames.remember_folder(tmp_path / "gone")          # no longer on disk: skipped
    out = core_games.scan_steam() and lgames.scan_all()
    assert [(g.name, g.source) for g in out] == [("Steam Game", "Manual"), ("Manual Game", "Manual")]
    lgames.forget_folder(manual)
    assert [g.name for g in lgames.scan_all()] == ["Steam Game"]


def test_scan_all_does_not_list_remembered_library_roots_as_games(monkeypatch, tmp_path):
    real = tmp_path / "APlagueTale"
    real.mkdir()
    library = tmp_path / "Heroic"
    library.mkdir()
    (library / "APlagueTale" / "bin").mkdir(parents=True)
    steam = tmp_path / "Steam"
    (steam / "steamapps").mkdir(parents=True)
    bin_folder = tmp_path / "bin"
    bin_folder.mkdir()
    monkeypatch.setattr(lgames, "_orig_scan", lambda: [])
    for folder in (real, library, steam, bin_folder):
        lgames.remember_folder(folder)
    assert [g.name for g in lgames.scan_all()] == ["APlagueTale"]


def test_install_state_empty_installed_and_foreign(game, monkeypatch):
    assert lgames.install_state(game) == ("", "")
    d = game.install_dir
    (d / "nvngx_dlssnr.dll").write_bytes(b"MZ")
    (d / "renodx-dlss5.addon64").write_bytes(b"MZ")
    (d / "dxgi.dll").write_bytes(b"MZ")
    monkeypatch.setattr(installer, "_is_reshade", lambda p: p.name == "dxgi.dll")
    kind, detail = lgames.install_state(game)
    assert kind == "foreign"
    assert detail == "reshade as dxgi.dll + nvngx_dlssnr + renodx-dlss5.addon64"
    (d / installer.MANIFEST).write_text(json.dumps({"path": "feeder", "files": []}), encoding="utf-8")
    assert lgames.install_state(game) == ("installed", "feeder")


# --- pe ------------------------------------------------------------------------------------
def test_unity_player_beside_the_exe_means_d3d11(tmp_path):
    g = make_game(tmp_path / "UnityGame", "Game.exe")
    (g.folder / "UnityPlayer.dll").write_bytes(b"MZ")
    api, why = pe.detect_api(g.exe)
    assert api == "DX11" and "Unity" in why


def test_unity_data_folder_is_enough_for_old_unity(tmp_path):
    g = make_game(tmp_path / "OldUnity", "Game.exe")
    (g.folder / "Game_Data").mkdir()
    assert pe.detect_api(g.exe)[0] == "DX11"


def test_d3d12_needle_promotes_an_unknown_renderer(tmp_path, monkeypatch):
    g = make_game(tmp_path / "REGame", "re.exe")
    g.exe.write_bytes(b"MZ" + b"\0" * 62 + b"...D3D12CreateDevice...")
    monkeypatch.setattr(lpe, "_orig_detect_api", lambda p: ("Unknown", "no renderer import"))
    api, why = pe.detect_api(g.exe)
    assert api == "DX12" and "dynamically" in why


def test_the_needle_is_found_across_a_read_boundary(tmp_path, monkeypatch):
    """_contains reads in 1 MiB blocks. A signature landing on the seam was
    invisible, and an RE Engine title came back DX11 depending on its size."""
    g = make_game(tmp_path / "BigGame", "big.exe")
    block = 1 << 20
    needle = b"d3d12createdevice"
    body = bytearray(block * 2)
    body[block - 5:block - 5 + len(needle)] = needle          # straddles the seam
    g.exe.write_bytes(b"MZ" + bytes(62) + bytes(body))
    assert lpe._contains(g.exe, needle)
    monkeypatch.setattr(lpe, "_orig_detect_api", lambda p: ("Unknown", "no renderer import"))
    assert pe.detect_api(g.exe)[0] == "DX12"


def test_a_confident_upstream_answer_is_left_alone(tmp_path, monkeypatch):
    g = make_game(tmp_path / "DxGame", "dx.exe")
    g.exe.write_bytes(b"MZ" + b"\0" * 62 + b"D3D12CreateDevice")
    monkeypatch.setattr(lpe, "_orig_detect_api", lambda p: ("DX11", "imports d3d11.dll"))
    assert pe.detect_api(g.exe) == ("DX11", "imports d3d11.dll")


def test_a_d3d9_verdict_is_only_second_guessed_for_a_64bit_exe(tmp_path, monkeypatch):
    g = make_game(tmp_path / "OldGame", "old.exe")
    g.exe.write_bytes(b"MZ" + b"\0" * 62 + b"D3D12CreateDevice")
    monkeypatch.setattr(lpe, "_orig_detect_api", lambda p: ("DX9", "imports d3d9.dll"))
    monkeypatch.setattr(pe, "exe_bitness", lambda p: 32)
    assert pe.detect_api(g.exe) == ("DX9", "imports d3d9.dll")
    monkeypatch.setattr(pe, "exe_bitness", lambda p: 64)
    assert pe.detect_api(g.exe)[0] == "DX12"


def test_an_unreadable_exe_is_not_promoted_from_d3d9(tmp_path, monkeypatch):
    g = make_game(tmp_path / "Broken", "b.exe")
    g.exe.write_bytes(b"MZ" + b"\0" * 62 + b"D3D12CreateDevice")
    monkeypatch.setattr(lpe, "_orig_detect_api", lambda p: ("DX9", "imports d3d9.dll"))

    def boom(p):
        raise pe.PEError("truncated")
    monkeypatch.setattr(pe, "exe_bitness", boom)
    assert pe.detect_api(g.exe)[0] == "DX9"


def test_agility_walks_up_to_the_unreal_plugin_folder(tmp_path):
    root = tmp_path / "SteamLibrary" / "steamapps" / "common" / "StellarBlade" / "SB"
    exe_dir = root / "Binaries" / "Win64"
    exe_dir.mkdir(parents=True)
    dlssg = root / "Plugins" / "Runtime" / "Nvidia" / "DLSS" / "Binaries" / "ThirdParty" / "Win64" / "nvngx_dlssg.dll"
    dlssg.parent.mkdir(parents=True)
    dlssg.write_bytes(b"MZ")
    assert pe._has_d3d12_agility_sdk(exe_dir) is True


def test_agility_walk_up_stops_at_the_games_own_tree(tmp_path):
    publisher = tmp_path / "Games" / "Publisher"
    ff = publisher / "Final Fantasy XIV" / "game"
    ff.mkdir(parents=True)
    other = publisher / "Other Title"
    other.mkdir()
    (other / "nvngx_dlssg.dll").write_bytes(b"MZ")
    assert pe._has_d3d12_agility_sdk(ff) is False


def test_agility_walk_up_stops_at_steamapps(tmp_path):
    common = tmp_path / "steamapps" / "common"
    exe_dir = common / "GameA" / "bin"
    exe_dir.mkdir(parents=True)
    (common / "GameB").mkdir()
    (common / "GameB" / "nvngx_dlssg.dll").write_bytes(b"MZ")
    assert pe._has_d3d12_agility_sdk(exe_dir) is False


# --- state ---------------------------------------------------------------------------------
def _write_state(root: Path) -> None:
    text = (FIXTURES / "_dlss5_proton_state.json").read_text(encoding="utf-8")
    (root / state.OUR_STATE).write_text(text.replace("__ROOT__", str(root).replace("\\", "/")), encoding="utf-8")


def test_proton_state_becomes_an_upstream_manifest(game):
    root = game.install_dir
    _write_state(root)
    man = installer._previous_manifest(root)
    assert man["path"] == "native" and man["proxy"] == "winmm.dll" and man["complete"]
    assert man["exe"] == "Fixture-Win64-Shipping.exe"
    assert man["files"] == ["winmm.dll", "renodx-dlss5++.addon64", "nvngx_dlss.dll", "nvngx_dlssnr.dll"]
    assert man["components"] == {"optiscaler": None, "feeder": "0.12.0"}
    assert man["_source"] == state.OUR_STATE


def test_ours_recognises_files_the_proton_tool_wrote(game):
    root = game.install_dir
    assert dlss._ours(root, "nvngx_dlss.dll") is False
    _write_state(root)
    assert dlss._ours(root, "nvngx_dlss.dll") is True
    assert dlss._ours(root, "nvngx_dlssg.dll") is False
    # a folder below the install dir still finds the record above it
    sub = root / "Plugins"
    sub.mkdir()
    assert dlss._ours(sub, "nvngx_dlss.dll") is True


def test_diagnose_manifest_falls_back_to_the_proton_state(game):
    from core import diagnose
    assert diagnose._manifest(game.install_dir) == {}
    _write_state(game.install_dir)
    assert diagnose._manifest(game.install_dir)["proxy"] == "winmm.dll"


def test_an_unreadable_state_file_is_ignored(game):
    (game.install_dir / state.OUR_STATE).write_text("{not json", encoding="utf-8")
    assert installer._previous_manifest(game.install_dir) is None
    assert dlss._ours(game.install_dir, "nvngx_dlss.dll") is False


# --- pins ------------------------------------------------------------------------------------
@pytest.fixture
def pin_env(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    cache.mkdir()
    monkeypatch.setattr(net, "cache_dir", lambda: cache)
    upstream_calls: list[str] = []

    def fake_upstream(build: str = ""):
        upstream_calls.append(build)
        return (f"v9.9.9-{build or 'dagherbou'}", f"https://example/{build or 'dagherbou'}.zip")
    monkeypatch.setattr(pins, "_orig_resolve", fake_upstream)
    monkeypatch.setattr(pins, "default_zip", lambda: tmp_path / "missing-nightly.zip")
    calls: list[str] = []

    def fake_json(url: str) -> dict:
        calls.append(url)
        tag = url.rsplit("/", 1)[1]
        return {"tag_name": tag, "assets": [{"name": "x.7z", "browser_download_url": "https://example/x.7z"},
                                            {"name": f"OptiScaler-{tag}.zip",
                                             "browser_download_url": f"https://example/{tag}.zip"}]}
    monkeypatch.setattr(sources, "_json", fake_json)
    yield {"cache": cache, "calls": calls, "upstream": upstream_calls}
    pins.set_optiscaler("default")


def test_latest_defers_to_upstream_resolve_for_the_requested_build(pin_env):
    pins.set_optiscaler("latest")
    assert optiscaler.resolve() == ("v9.9.9-dagherbou", "https://example/dagherbou.zip")
    assert optiscaler.resolve("wilsjo2") == ("v9.9.9-wilsjo2", "https://example/wilsjo2.zip")
    assert pin_env["upstream"] == ["", "wilsjo2"]


def test_fallback_and_explicit_tags_pick_the_zip_asset(pin_env):
    pins.set_optiscaler("fallback")
    assert optiscaler.resolve() == (pins.FALLBACK_TAG, f"https://example/{pins.FALLBACK_TAG}.zip")
    pins.set_optiscaler("v0.2.0-patch1")
    assert optiscaler.resolve() == ("v0.2.0-patch1", "https://example/v0.2.0-patch1.zip")
    assert pin_env["calls"][-1].endswith("/tags/v0.2.0-patch1")


def test_default_without_the_local_nightly_uses_the_y4my4my4m_line_upstream(pin_env):
    pins.set_optiscaler("default")
    assert optiscaler.resolve() == ("v9.9.9-y4my4my4m", "https://example/y4my4my4m.zip")
    assert pin_env["upstream"] == [optiscaler.FORK]
    assert pin_env["calls"] == []            # no Dagherbou tag lookup any more


def test_default_honours_a_build_named_by_the_install(pin_env):
    pins.set_optiscaler("default")
    assert optiscaler.resolve("wilsjo2")[0] == "v9.9.9-wilsjo2"


def test_default_with_the_local_nightly_stages_it_into_the_cache(pin_env, tmp_path, monkeypatch):
    nightly = tmp_path / "nightly.zip"
    nightly.write_bytes(b"PK\x05\x06" + b"\0" * 18)
    monkeypatch.setattr(pins, "default_zip", lambda: nightly)
    pins.set_optiscaler("default")
    tag, url = optiscaler.resolve()
    staged = pin_env["cache"] / f"OptiScaler-DLSSNR-{pins.DEFAULT_TAG}.zip"
    assert tag == pins.DEFAULT_TAG and url == f"file://{staged}" and staged.is_file()
    # an explicitly named fork still wins over the local file
    assert optiscaler.resolve("wilsjo2")[0] == "v9.9.9-wilsjo2"


def test_a_local_archive_path_is_staged_under_its_hash(pin_env, tmp_path):
    local = tmp_path / "mine.zip"
    local.write_bytes(b"PK\x05\x06" + b"\0" * 18)
    pins.set_optiscaler(str(local))
    tag, url = optiscaler.resolve()
    assert tag == f"local-{net.sha256(local)[:8]}"
    assert (pin_env["cache"] / f"OptiScaler-DLSSNR-{tag}.zip").is_file()


def test_a_release_without_a_zip_asset_is_an_error(pin_env, monkeypatch):
    monkeypatch.setattr(sources, "_json", lambda url: {"tag_name": "v1", "assets": [{"name": "x.7z", "browser_download_url": "u"}]})
    pins.set_optiscaler("v1")
    with pytest.raises(RuntimeError, match="no .zip asset"):
        optiscaler.resolve()

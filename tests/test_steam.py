"""linuxport.steam (lifted from proton-tool), linuxport.seed, state.migrate, the dlls replacement."""
from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

import pytest

from conftest import FIXTURES
from core import installer, net
from linuxport import paths, pins, proton, seed, state
from linuxport import steam as lsteam


# --- vdf --------------------------------------------------------------------------------------
def test_parse_vdf_nested_and_escaped():
    text = '"AppState"\n{\n\t"appid"\t\t"123"\n\t"name"\t\t"Say \\"hi\\""\n\t"UserConfig"\n\t{\n\t\t"language"\t\t"english"\n\t}\n}\n'
    data = lsteam.parse_vdf(text)
    assert data == {"AppState": {"appid": "123", "name": 'Say "hi"', "UserConfig": {"language": "english"}}}
    assert lsteam.parse_vdf("") == {}


# --- roots, libraries, games ---------------------------------------------------------------------
def test_steam_roots_env_first_then_candidates_deduplicated(fake_home, tmp_path, monkeypatch):
    assert lsteam.steam_roots() == []
    native = fake_home / ".steam" / "steam"
    (native / "steamapps").mkdir(parents=True)
    flat = fake_home / ".var/app/com.valvesoftware.Steam/.local/share/Steam"
    (flat / "steamapps").mkdir(parents=True)
    custom = tmp_path / "custom"
    (custom / "steamapps").mkdir(parents=True)
    monkeypatch.setenv("STEAM_ROOT", str(custom))
    roots = lsteam.steam_roots()
    assert roots[0] == custom.resolve() and native.resolve() in roots and flat.resolve() in roots
    assert len(roots) == len(set(roots))


def test_libraries_follow_libraryfolders_and_games_come_from_manifests(steam_game, steam, tmp_path):
    extra = tmp_path / "Library2"
    (extra / "steamapps" / "common" / "Second").mkdir(parents=True)
    (extra / "steamapps" / "appmanifest_456.acf").write_text(
        '"AppState"\n{\n\t"appid"\t\t"456"\n\t"name"\t\t"Second Game"\n\t"installdir"\t\t"Second"\n}\n', encoding="utf-8")
    (extra / "steamapps" / "appmanifest_789.acf").write_text(
        '"AppState"\n{\n\t"appid"\t\t"789"\n\t"name"\t\t"Not Installed"\n\t"installdir"\t\t"Missing"\n}\n', encoding="utf-8")
    vdf = steam["steamapps"] / "libraryfolders.vdf"
    esc = lambda p: str(p).replace("\\", "\\\\")
    vdf.write_text('"libraryfolders"\n{\n'
                   f'\t"0"\n\t{{\n\t\t"path"\t\t"{esc(steam["root"])}"\n\t}}\n'
                   f'\t"1"\n\t{{\n\t\t"path"\t\t"{esc(extra)}"\n\t}}\n'
                   '}\n', encoding="utf-8")
    libs = lsteam.steam_libraries()
    assert steam["steamapps"].resolve() in libs and (extra / "steamapps").resolve() in libs
    games = lsteam.installed_games()
    assert set(games) == {"123", "456"}                              # 789 has no folder
    assert games["123"].name == "Fixture Game" and games["456"].install_dir.name == "Second"
    assert games["456"].library == (extra / "steamapps").resolve()


def test_find_prefix_prefers_a_populated_one_over_a_stub(steam):
    assert lsteam.find_prefix("123") == steam["pfx"]
    stub = steam["steamapps"] / "compatdata" / "456" / "pfx"
    stub.mkdir(parents=True)
    assert lsteam.find_prefix("456") == stub                          # the stub, as the only candidate
    populated = Path.home() / "proton-prefixes" / "456" / "pfx"
    (populated / "drive_c" / "windows" / "system32").mkdir(parents=True)
    assert lsteam.find_prefix("456") == populated                     # populated elsewhere wins
    assert lsteam.find_prefix("999") is None


def test_prefix_from_path_inside_or_beside(tmp_path):
    inside = tmp_path / "wine" / "drive_c" / "Games" / "G" / "g.exe"
    inside.parent.mkdir(parents=True); inside.write_bytes(b"MZ")
    assert lsteam.prefix_from_path(inside) == (tmp_path / "wine").resolve()
    beside = tmp_path / "Foo" / "game" / "g.exe"
    beside.parent.mkdir(parents=True); beside.write_bytes(b"MZ")
    (tmp_path / "Foo" / "pfx" / "drive_c" / "windows" / "system32").mkdir(parents=True)
    assert lsteam.prefix_from_path(beside) == (tmp_path / "Foo" / "pfx")
    assert lsteam.prefix_from_path(tmp_path / "nowhere" / "x.exe") is None


# --- launch options -------------------------------------------------------------------------------
def test_launch_options_for_reads_only_that_appid(steam):
    assert lsteam.launch_options_for("123") == "PROTON_LOG=1 %command%"
    assert lsteam.launch_options_for("456") is None
    assert lsteam.launch_options_for("999") is None


def test_build_launch_options_merges_and_drops_stale_loader_overrides(steam, steam_game, monkeypatch):
    folder = steam_game.install_dir
    assert lsteam.build_launch_options(None, folder, "dxgi.dll") == 'WINEDLLOVERRIDES="dxgi=n,b" %command%'
    assert lsteam.build_launch_options("123", folder, "dxgi.dll") == 'PROTON_LOG=1 WINEDLLOVERRIDES="dxgi=n,b" %command%'
    monkeypatch.setattr(lsteam, "launch_options_for",
                        lambda a: 'X=1 WINEDLLOVERRIDES="winmm=n,b;d3d11=n,b;foo=b" %command%')
    (folder / "d3d11.dll").write_bytes(b"MZ")               # still on disk: kept; winmm is gone: dropped
    line = lsteam.build_launch_options("123", folder, "dxgi.dll", ["d3d12=n,b"])
    assert line == 'X=1 WINEDLLOVERRIDES="d3d11=n,b;foo=b;dxgi=n,b;d3d12=n,b" %command%'
    monkeypatch.setattr(lsteam, "launch_options_for", lambda a: "PROTON_LOG=1")
    assert lsteam.build_launch_options("123", folder, "dxgi.dll") == 'PROTON_LOG=1 WINEDLLOVERRIDES="dxgi=n,b" %command%'


def test_proton_layer_uses_the_steam_module(steam_game):
    assert proton.pt is lsteam
    assert proton.appid_for(steam_game) == "123"
    assert proton.current_launch_options(steam_game) == "PROTON_LOG=1 %command%"


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="/proc")
def test_ancestor_pids_include_self():
    import os
    assert os.getpid() in lsteam._ancestor_pids()


# --- PROTON_DLSS_UPGRADE ------------------------------------------------------------------------------
def test_dlss_upgrade_toggle_and_detection():
    base = 'WINEDLLOVERRIDES="dxgi=n,b" %command%'
    on = proton.with_dlss_upgrade(base, True)
    assert on == f"PROTON_DLSS_UPGRADE=1 {base}" and proton.dlss_upgrade_present(on)
    assert proton.with_dlss_upgrade(on, True) == on
    assert proton.with_dlss_upgrade(on, False) == base and not proton.dlss_upgrade_present(base)
    assert not proton.dlss_upgrade_present(None) and not proton.dlss_upgrade_present("PROTON_DLSS_UPGRADE=0 x")


def test_vulkan_layer_line_without_a_native_loader_has_no_override(steam_game):
    line = proton.launch_options(steam_game, "(vulkan layer)", indicator=False, route="feeder")
    assert "WINEDLLOVERRIDES" not in line and line == "PROTON_LOG=1 %command%"


# --- components dir + seed ------------------------------------------------------------------------------
def test_components_dir_defaults_to_xdg_data_and_honours_the_env(monkeypatch, tmp_path):
    monkeypatch.delenv(paths.COMPONENTS_ENV, raising=False)
    assert paths.components_dir() == paths.DATA / "components"
    monkeypatch.setenv(paths.COMPONENTS_ENV, str(tmp_path / "mine"))
    assert paths.components_dir() == tmp_path / "mine"
    assert pins.default_zip() == tmp_path / "mine" / pins.DEFAULT_ZIP_NAME
    assert "DLSS5_work" not in str(pins.default_zip())


def test_seed_stages_renames_and_repacks(monkeypatch, tmp_path):
    cache = tmp_path / "cache"
    monkeypatch.setattr(net, "cache_dir", lambda: cache)
    src = tmp_path / "components"
    src.mkdir()
    (src / "DLSS5-Feeder-0.12.0.zip").write_bytes(b"PK\x05\x06" + b"\0" * 18)          # renamed
    (src / "v9-Whatever.zip").write_bytes(b"PK\x05\x06" + b"\0" * 18)                  # already a cache name
    with zipfile.ZipFile(src / "DLSS 310.8.0 streamline 2.13 dll.zip", "w") as z:     # repacked
        z.writestr("some/dir/nvngx_dlssnr.dll", b"MZ dlssnr")
        z.writestr("some/dir/other.dll", b"MZ")
    (src / "notes.txt").write_text("ignored", encoding="utf-8")
    logs: list[str] = []
    staged = seed.seed(logs.append, source=src)
    assert set(staged) == {"v0.12.0-DLSS5-Feeder-0.12.0.zip", "v9-Whatever.zip", "dlssnr-310.8.0.zip"}
    with zipfile.ZipFile(cache / "dlssnr-310.8.0.zip") as z:
        assert z.namelist() == ["nvngx_dlssnr.dll"] and z.read("nvngx_dlssnr.dll") == b"MZ dlssnr"
    assert seed.seed(logs.append, source=src) == []                                   # idempotent
    assert seed.seed(logs.append, source=tmp_path / "absent") == []
    assert seed.dlssnr_version_from_name("dlssnr 310.8.0-RTX40.zip") == "310.8.0-RTX40"


# --- state.migrate ----------------------------------------------------------------------------------------
def _old_record(root: Path) -> None:
    text = (FIXTURES / "_dlss5_proton_state.json").read_text(encoding="utf-8")
    (root / state.OUR_STATE).write_text(text.replace("__ROOT__", str(root).replace("\\", "/")), encoding="utf-8")


def test_migrate_writes_the_upstream_manifest_once(game):
    root = game.install_dir
    assert not state.needs_migration(root) and state.migrate(root) is None
    _old_record(root)
    assert state.needs_migration(root)
    logs: list[str] = []
    written = state.migrate(root, logs.append)
    assert written == root / installer.MANIFEST
    man = json.loads(written.read_text(encoding="utf-8"))
    assert man["path"] == "native" and man["proxy"] == "winmm.dll" and man["migrated_from"] == state.OUR_STATE
    assert "_source" not in man and "winmm.dll" in man["files"]
    assert not (root / state.OUR_STATE).exists() and (root / (state.OUR_STATE + state.MIGRATED_SUFFIX)).is_file()
    assert not state.needs_migration(root) and state.migrate(root) is None
    # the upstream reader sees a normal install now
    assert installer._previous_manifest(root)["proxy"] == "winmm.dll"
    assert any("written from" in l for l in logs)


def test_migrate_leaves_a_folder_with_a_manifest_alone(game):
    root = game.install_dir
    (root / installer.MANIFEST).write_text(json.dumps({"path": "feeder", "files": []}), encoding="utf-8")
    _old_record(root)
    assert not state.needs_migration(root) and state.migrate(root) is None
    assert (root / state.OUR_STATE).is_file()

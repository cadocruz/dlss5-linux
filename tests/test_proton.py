"""linuxport.proton: the layer Windows never needed.

Override strings, the on-screen indicator, Steam's localconfig.vdf, prefix
discovery and the XIVLauncher-RB special case. Every test runs against
temporary files; Steam and the launcher are never touched.
"""
from __future__ import annotations

import pytest

from conftest import make_game
from linuxport import proton, steam as pt


# --- override entries ------------------------------------------------------------
def test_dx12_native_only_needs_the_proxy(game):
    assert proton.override_entries(game, "dxgi.dll", "native") == ["dxgi=n,b"]


def test_proxy_stem_is_used_whatever_the_name(game):
    assert proton.override_entries(game, "winmm.dll", "native")[0] == "winmm=n,b"


def test_dx11_optiscaler_adds_the_vkd3d_bridge_overrides(game):
    game.api = "DX11"
    assert proton.override_entries(game, "winmm.dll", "optiscaler") == [
        "winmm=n,b", "d3d12=n,b", "d3d12core=n,b"]


def test_dx11_with_unknown_route_also_adds_the_bridge(game):
    game.api = "DX11"
    assert "d3d12=n,b" in proton.override_entries(game, "dxgi.dll", None)


def test_dx11_on_a_reshade_route_does_not_add_the_bridge(game):
    game.api = "DX11"
    assert proton.override_entries(game, "dxgi.dll", "native") == ["dxgi=n,b"]


def test_app_local_d3dcompiler_gets_its_own_override(game):
    (game.exe.parent / "d3dcompiler_47.dll").write_bytes(b"MZ")
    assert proton.override_entries(game, "dxgi.dll", "native")[-1] == "d3dcompiler_47=n"


# --- indicator --------------------------------------------------------------------
LINE = 'WINEDLLOVERRIDES="dxgi=n,b" %command%'


def test_with_indicator_prepends_once():
    on = proton.with_indicator(LINE, True)
    assert on == f"PROTON_DLSS_INDICATOR=1 {LINE}"
    assert proton.with_indicator(on, True) == on


def test_with_indicator_off_removes_any_value():
    assert proton.with_indicator(f"PROTON_DLSS_INDICATOR=1 {LINE}", False) == LINE
    assert proton.with_indicator(f"X=1 PROTON_DLSS_INDICATOR=true {LINE}", False) == f"X=1 {LINE}"


@pytest.mark.parametrize("options,expected", [
    (None, False),
    ("", False),
    (LINE, False),
    ("PROTON_DLSS_INDICATOR=0 " + LINE, False),
    ("PROTON_DLSS_INDICATOR=1 " + LINE, True),
    ("PROTON_DLSS_INDICATOR=true " + LINE, True),
    (LINE + " PROTON_DLSS_INDICATOR=on", True),
])
def test_indicator_present(options, expected):
    assert proton.indicator_present(options) is expected


# --- presence of overrides ----------------------------------------------------------
def test_override_present_checks_every_required_entry():
    need = ["winmm=n,b", "d3d12=n,b", "d3d12core=n,b"]
    assert proton.override_present('WINEDLLOVERRIDES="winmm=n,b;d3d12=n,b;d3d12core=n,b"', "winmm.dll", need)
    assert not proton.override_present('WINEDLLOVERRIDES="winmm=n,b"', "winmm.dll", need)
    assert proton.override_present('WINEDLLOVERRIDES="winmm=n,b"', "winmm.dll")
    assert not proton.override_present(None, "winmm.dll")


def test_missing_overrides_for_a_non_steam_game_lists_everything(fake_home, game):
    game.api = "DX11"
    assert proton.missing_overrides(game, "winmm.dll", "optiscaler") == [
        "winmm=n,b", "d3d12=n,b", "d3d12core=n,b"]


# --- launch options string ------------------------------------------------------------
def test_launch_options_for_a_non_steam_game_is_the_bare_override(fake_home, game):
    line = proton.launch_options(game, "dxgi.dll", indicator=False, route="native")
    assert line == 'WINEDLLOVERRIDES="dxgi=n,b" %command%'


def test_launch_options_carries_every_entry_and_the_indicator(fake_home, game):
    game.api = "DX11"
    line = proton.launch_options(game, "winmm.dll", indicator=True, route="optiscaler")
    assert line.startswith("PROTON_DLSS_INDICATOR=1 ")
    assert 'WINEDLLOVERRIDES="winmm=n,b;d3d12=n,b;d3d12core=n,b"' in line


def test_launch_options_merges_into_the_steam_options_already_set(steam_game):
    line = proton.launch_options(steam_game, "dxgi.dll", indicator=False, route="native")
    assert "PROTON_LOG=1" in line and 'WINEDLLOVERRIDES="dxgi=n,b"' in line and "%command%" in line


# --- Steam: appid, prefix, localconfig.vdf ----------------------------------------------
def test_appid_for_a_game_inside_a_library(steam_game):
    assert proton.appid_for(steam_game) == "123"


def test_appid_for_a_folder_outside_steam_is_none(steam, game):
    assert proton.appid_for(game) is None


def test_prefix_for_a_steam_game_is_compatdata(steam, steam_game):
    assert proton.prefix_for(steam_game) == steam["pfx"]
    assert proton.ngx_bridge_present(steam_game)
    assert proton.system32(steam_game) == steam["pfx"] / "drive_c" / "windows" / "system32"


def test_current_launch_options_are_read_unescaped(steam_game):
    assert proton.current_launch_options(steam_game) == "PROTON_LOG=1 %command%"


def test_set_launch_options_replaces_existing_and_escapes_quotes(steam, steam_game, monkeypatch):
    monkeypatch.setattr(proton, "steam_running", lambda: False)
    line = 'PROTON_DLSS_INDICATOR=1 WINEDLLOVERRIDES="dxgi=n,b" PROTON_LOG=1 %command%'
    backup = proton.set_launch_options(steam_game, line)
    text = steam["localconfig"].read_text(encoding="utf-8")
    assert '"LaunchOptions"\t\t"PROTON_DLSS_INDICATOR=1 WINEDLLOVERRIDES=\\"dxgi=n,b\\" PROTON_LOG=1 %command%"' in text
    assert text.count('"LaunchOptions"') == 1
    assert backup.is_file() and backup.name.startswith("localconfig.vdf.bak-")
    # round trip through the reader used everywhere else
    assert pt.launch_options_for("123") == line


def test_set_launch_options_adds_the_key_when_the_app_has_none(steam, monkeypatch):
    monkeypatch.setattr(proton, "steam_running", lambda: False)
    monkeypatch.setattr(proton, "appid_for", lambda g: "456")
    g = make_game(steam["steamapps"] / "common" / "Other", source="Steam")
    proton.set_launch_options(g, 'WINEDLLOVERRIDES="dxgi=n,b" %command%')
    assert pt.launch_options_for("456") == 'WINEDLLOVERRIDES="dxgi=n,b" %command%'
    # the other app is untouched
    assert pt.launch_options_for("123") == "PROTON_LOG=1 %command%"


def test_set_launch_options_refuses_while_steam_runs(steam_game, monkeypatch):
    monkeypatch.setattr(proton, "steam_running", lambda: True)
    with pytest.raises(RuntimeError, match="Steam is running"):
        proton.set_launch_options(steam_game, LINE)


def test_set_launch_options_refuses_a_non_steam_game(fake_home, game, monkeypatch):
    monkeypatch.setattr(proton, "steam_running", lambda: False)
    with pytest.raises(RuntimeError, match="not a Steam game"):
        proton.set_launch_options(game, LINE)


def test_set_launch_options_reports_an_appid_missing_from_the_file(steam, monkeypatch):
    monkeypatch.setattr(proton, "steam_running", lambda: False)
    monkeypatch.setattr(proton, "appid_for", lambda g: "999")
    g = make_game(steam["steamapps"] / "common" / "Ghost", source="Steam")
    with pytest.raises(RuntimeError, match="999"):
        proton.set_launch_options(g, LINE)


def test_set_launch_options_without_a_steam_root_is_a_message_not_an_indexerror(
        fake_home, game, monkeypatch):
    """Steam somewhere this tool does not look. steam_roots()[0] raised
    IndexError out of a worker thread; there is an answer to give instead."""
    monkeypatch.setattr(proton, "steam_running", lambda: False)
    monkeypatch.setattr(proton, "appid_for", lambda g: "123")
    assert pt.steam_roots() == []
    with pytest.raises(RuntimeError, match="no localconfig.vdf"):
        proton.set_launch_options(game, LINE)


def test_set_launch_options_finds_the_appid_in_a_second_steam_root(steam, fake_home, monkeypatch):
    """A native Steam and a Flatpak one side by side: only the first root was
    ever read, so a game in the other never got its launch options."""
    monkeypatch.setattr(proton, "steam_running", lambda: False)
    monkeypatch.setattr(proton, "appid_for", lambda g: "777")
    flat = fake_home / ".var/app/com.valvesoftware.Steam/.local/share/Steam"
    (flat / "steamapps").mkdir(parents=True)
    cfg = flat / "userdata" / "2002" / "config"
    cfg.mkdir(parents=True)
    (cfg / "localconfig.vdf").write_text(
        steam["localconfig"].read_text(encoding="utf-8").replace('"123"', '"777"'), encoding="utf-8")
    assert len(pt.steam_roots()) == 2 and len(pt.localconfigs()) == 2

    g = make_game(steam["steamapps"] / "common" / "Elsewhere", source="Steam")
    backup = proton.set_launch_options(g, LINE)
    assert backup.parent == cfg
    assert pt.launch_options_for("777") == LINE
    assert pt.launch_options_for("123") == "PROTON_LOG=1 %command%"     # untouched


# --- non-Steam prefixes ----------------------------------------------------------------
def test_remembered_prefix_wins_and_is_validated(fake_home, game, tmp_path):
    good = tmp_path / "pfx"
    (good / "drive_c").mkdir(parents=True)
    proton.set_prefix(game, good)
    assert proton.remembered_prefix(game) == good.resolve()
    assert proton.prefix_for(game) == good.resolve()
    proton.set_prefix(game, tmp_path / "not-a-prefix")     # invalid: forgets it
    assert proton.remembered_prefix(game) is None


def test_prefix_from_a_drive_c_ancestor(fake_home, tmp_path):
    pfx = tmp_path / "wine"
    g = make_game(pfx / "drive_c" / "Program Files" / "Game", "game.exe")
    assert proton.prefix_for(g) == pfx.resolve()


def test_prefix_falls_back_to_wineprefix_env(fake_home, game, tmp_path, monkeypatch):
    env = tmp_path / "envpfx"
    (env / "drive_c").mkdir(parents=True)
    monkeypatch.setenv("WINEPREFIX", str(env))
    assert proton.prefix_for(game) == env
    monkeypatch.setenv("WINEPREFIX", str(tmp_path / "missing"))
    assert proton.prefix_for(game) is None


# --- XIVLauncher-RB ---------------------------------------------------------------------
@pytest.fixture
def xlcore(fake_home):
    x = fake_home / ".xlcore"
    (x / "protonprefix" / "pfx" / "drive_c").mkdir(parents=True)
    (x / "launcher.ini").write_text("Foo=1\nWineDLLOverrides=winmm=n,b\nBar=2\n", encoding="utf-8")
    return x


def test_is_xlcore_only_for_ffxiv_outside_steam(xlcore, tmp_path):
    ff = make_game(tmp_path / "ffxiv" / "game", "ffxiv_dx11.exe", api="DX11")
    other = make_game(tmp_path / "other", "other.exe")
    assert proton.is_xlcore(ff)
    assert not proton.is_xlcore(other)
    assert proton.prefix_for(ff) == xlcore / "protonprefix" / "pfx"
    assert proton.launcher_overrides(ff) == "winmm=n,b"


def test_set_launcher_overrides_writes_backup_and_refuses_forbidden_dlls(xlcore, tmp_path, monkeypatch):
    ff = make_game(tmp_path / "ffxiv" / "game", "ffxiv_dx11.exe", api="DX11")
    monkeypatch.setattr(proton, "launcher_running", lambda: False)
    backup = proton.set_launcher_overrides(ff, ["winmm=n,b", "d3d12=n,b", "d3d12core=n,b"])
    text = (xlcore / "launcher.ini").read_text(encoding="utf-8")
    assert "WineDLLOverrides=winmm=n,b;d3d12=n,b;d3d12core=n,b" in text
    assert text.startswith("Foo=1\n") and text.rstrip().endswith("Bar=2")
    assert backup.is_file()
    with pytest.raises(RuntimeError, match="refuses"):
        proton.set_launcher_overrides(ff, ["dxgi=n,b"])
    monkeypatch.setattr(proton, "launcher_running", lambda: True)
    with pytest.raises(RuntimeError, match="running"):
        proton.set_launcher_overrides(ff, ["winmm=n,b"])


def test_set_launcher_overrides_rejects_a_non_xlcore_game(fake_home, game):
    with pytest.raises(RuntimeError, match="not an XIVLauncher"):
        proton.set_launcher_overrides(game, ["winmm=n,b"])


# --- launch help text -------------------------------------------------------------------
def test_launch_help_steam_branch(steam_game):
    lines = proton.launch_help(steam_game, "dxgi.dll", indicator=False, route="native")
    assert lines[0].startswith("steam >")
    assert 'WINEDLLOVERRIDES="dxgi=n,b"' in lines[1]


def test_launch_help_xlcore_branch(xlcore, tmp_path):
    ff = make_game(tmp_path / "ffxiv" / "game", "ffxiv_dx11.exe", api="DX11")
    lines = proton.launch_help(ff, "winmm.dll", indicator=False, route="optiscaler")
    assert "xivlauncher-rb" in lines[0]
    assert any("winmm=n,b;d3d12=n,b;d3d12core=n,b" in l for l in lines)


def test_launch_help_non_steam_branch_shows_lutris_grid_form(fake_home, game):
    game.api = "DX11"
    lines = proton.launch_help(game, "winmm.dll", indicator=False, route="optiscaler")
    assert "not in your steam library" in lines[0]
    assert any("key winmm value n,b" in l for l in lines)
    assert any("export WINEDLLOVERRIDES=" in l for l in lines)


# --- installed proxy from a manifest --------------------------------------------------------
def test_installed_proxy_reads_the_old_proton_tool_state(game):
    from conftest import FIXTURES
    text = (FIXTURES / "_dlss5_proton_state.json").read_text(encoding="utf-8")
    (game.install_dir / "_dlss5_proton_state.json").write_text(
        text.replace("__ROOT__", str(game.install_dir).replace("\\", "/")), encoding="utf-8")
    assert proton.installed_proxy(game) == "winmm.dll"


def test_installed_proxy_is_none_without_a_record(game):
    assert proton.installed_proxy(game) is None

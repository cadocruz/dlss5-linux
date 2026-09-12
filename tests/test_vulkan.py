"""linuxport.vulkan: ReShade as a Vulkan layer inside a Proton prefix."""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from core import diagnose, dxvk, installer, vulkan
from linuxport import dxvk as ldxvk, proton, vulkan as lv


@pytest.fixture(autouse=True)
def clear_ctx():
    lv._ctx["game"] = lv._ctx["dir"] = None
    yield
    lv._ctx["game"] = lv._ctx["dir"] = None


@pytest.fixture
def setup_exe(tmp_path) -> Path:
    """A fake ReShade_Setup.exe: the zip the real one has appended."""
    p = tmp_path / "ReShade_Setup_6.8.0_Addon.exe"
    with zipfile.ZipFile(p, "w") as z:
        for bits in ("64", "32"):
            z.writestr(f"ReShade{bits}.dll", b"MZ" + b"\0" * 64)
            z.writestr(f"ReShade{bits}.json", json.dumps({"file_format_version": "1.0.0", "layer": {
                "name": "VK_LAYER_reshade", "library_path": f".\\ReShade{bits}.dll", "api_version": "1.3.0"}}))
    return p


@pytest.fixture
def regs(monkeypatch):
    """Capture every .reg the module would import; no wine is run."""
    seen: list[tuple[str, str]] = []
    monkeypatch.setattr(lv, "run_reg", lambda text, pfx, log=None, label="layer": seen.append((label, text)) or True)
    monkeypatch.setattr(lv, "ensure_native_loader", lambda pfx, x64, also32, log=None: False)
    monkeypatch.setenv(lv.NATIVE_LOADER_ENV, "0")
    return seen


def _write_reg(pfx: Path, manifests: list[tuple[str, int]], hive: str = "user") -> None:
    lines = ["WINE REGISTRY Version 2", "", f"[{lv.LAYER_KEY.replace(chr(92), chr(92) * 2)}] 1700000000", "#time=1d9"]
    for name, val in manifests:
        lines.append(f'"{name.replace(chr(92), chr(92) * 2)}"=dword:{val:08x}')
    (pfx / f"{hive}.reg").write_text("\n".join(lines) + "\n", encoding="utf-8")


# --- paths --------------------------------------------------------------------------------
def test_win_and_unix_views_round_trip(steam):
    pfx = steam["pfx"]
    inside = pfx / "drive_c" / "ProgramData" / "ReShade" / "ReShade64.json"
    assert lv.to_win(inside, pfx) == r"C:\ProgramData\ReShade\ReShade64.json"
    assert lv.to_unix(r"C:\ProgramData\ReShade\ReShade64.json", pfx) == inside
    outside = Path("/games/x/y.reg")
    assert lv.to_win(outside, pfx).startswith("Z:") and lv.to_unix("Z:\\games\\x\\y.reg", pfx) == outside


def test_layer_dir_follows_the_prefix(steam, steam_game, game):
    lv.use_game(steam_game)
    assert lv.layer_dir() == steam["pfx"] / "drive_c" / "ProgramData" / "ReShade"
    assert lv.is_ours(lv.layer_dir() / "ReShade64.json")
    lv.use_game(game)                      # no prefix
    assert "no-prefix" in str(lv.layer_dir())


# --- registry text ------------------------------------------------------------------------------
def test_parse_wine_registry_file():
    text = ('WINE REGISTRY Version 2\n\n[Software\\\\Khronos\\\\Vulkan\\\\ImplicitLayers] 1700000000\n#time=1d9\n'
            '"C:\\\\ProgramData\\\\ReShade\\\\ReShade64.json"=dword:00000000\n'
            '"C:\\\\Other\\\\layer.json"=dword:00000001\n\n[Software\\\\Wine] 1\n"Version"="win10"\n')
    got = lv.parse_reg_file(text, (lv.LAYER_KEY,))
    assert got == [(r"C:\ProgramData\ReShade\ReShade64.json", 0), (r"C:\Other\layer.json", 1)]
    assert lv.parse_reg_file(text, (lv.LAYER_KEY32,)) == []


def test_reg_add_text_covers_both_hives_and_the_32bit_node():
    text = lv.reg_add_text(r"C:\ProgramData\ReShade\ReShade64.json", r"C:\ProgramData\ReShade\ReShade32.json", True)
    assert text.startswith("Windows Registry Editor Version 5.00\n")
    assert f"[HKEY_CURRENT_USER\\{lv.LAYER_KEY}]" in text and f"[HKEY_LOCAL_MACHINE\\{lv.LAYER_KEY}]" in text
    assert f"[HKEY_LOCAL_MACHINE\\{lv.LAYER_KEY32}]" in text
    assert text.count(r'"C:\\ProgramData\\ReShade\\ReShade64.json"=dword:00000000') == 2   # HKCU + HKLM
    assert text.count(r'"C:\\ProgramData\\ReShade\\ReShade32.json"=dword:00000000') == 2   # HKCU + Wow6432Node
    assert '"vulkan-1"="native"' in text
    only64 = lv.reg_add_text(r"C:\ProgramData\ReShade\ReShade64.json", None, False)
    assert "Wow6432Node" not in only64 and "vulkan-1" not in only64


def test_reg_remove_text_deletes_only_our_values():
    text = lv.reg_remove_text(r"C:\ProgramData\ReShade\ReShade64.json", None, True)
    assert r'"C:\\ProgramData\\ReShade\\ReShade64.json"=-' in text and '"vulkan-1"=-' in text
    assert "[-HKEY" not in text                           # never the whole key


# --- reading registrations -------------------------------------------------------------------------
def test_registrations_read_user_and_system_reg(steam, steam_game):
    pfx = steam["pfx"]
    d = pfx / "drive_c" / "ProgramData" / "ReShade"
    d.mkdir(parents=True)
    (d / "ReShade64.json").write_text(json.dumps({"layer": {"library_path": ".\\ReShade64.dll"}}), encoding="utf-8")
    (d / "ReShade32.json").write_text(json.dumps({"layer": {"library_path": ".\\ReShade32.dll"}}), encoding="utf-8")
    _write_reg(pfx, [(r"C:\ProgramData\ReShade\ReShade64.json", 0), (r"C:\ProgramData\ReShade\gone.json", 0)], "user")
    _write_reg(pfx, [(r"C:\ProgramData\ReShade\ReShade32.json", 1)], "system")
    lv.use_game(steam_game)
    regs = dict(vulkan.registrations())
    assert regs == {d / "ReShade64.json": 0, d / "ReShade32.json": 1}     # gone.json: no file
    assert vulkan.existing_registration() == d / "ReShade64.json"
    assert vulkan.registered_for(True) == d / "ReShade64.json"
    assert vulkan.registered_for(False) is None                             # 32-bit one is disabled


def test_no_prefix_means_no_registrations(fake_home, game):
    lv.use_game(game)
    assert vulkan.registrations() == [] and vulkan.existing_registration() is None


# --- the runner ---------------------------------------------------------------------------------------
def test_pick_runner_prefers_env_then_protontricks_then_proton_then_system(steam, monkeypatch, tmp_path):
    pfx = steam["pfx"]
    monkeypatch.setenv(lv.WINE_ENV, "/opt/wine/bin/wine")
    r = lv.pick_runner(pfx, "123")
    assert r.kind == "env" and r.wine == ["/opt/wine/bin/wine"] and r.env["WINEPREFIX"] == str(pfx)
    monkeypatch.delenv(lv.WINE_ENV)

    monkeypatch.setattr(lv.shutil, "which", lambda n: "/usr/bin/protontricks" if n == "protontricks" else None)
    r = lv.pick_runner(pfx, "123")
    assert r.kind == "protontricks" and r.shell_join and r.env["STEAM_COMPAT_DATA_PATH"] == str(pfx.parent)
    cmd, wait = lv.regedit_argv(r, r"C:\ProgramData\ReShade\x.reg", "123")
    assert cmd == ["protontricks", "--no-bwrap", "-c", 'wine regedit /S "C:\\ProgramData\\ReShade\\x.reg"', "123"]
    assert wait[-2:] == ["wineserver -w", "123"]
    assert lv.pick_runner(pfx, None) is None                          # no appid: protontricks is useless, nothing else here

    monkeypatch.setattr(lv.shutil, "which", lambda n: None)
    proton_dir = tmp_path / "Proton 9.0 (Beta)" / "files"
    (proton_dir / "bin").mkdir(parents=True)
    (proton_dir / "bin" / "wine64").write_bytes(b"#!")
    (pfx.parent / "config_info").write_text(f"9.0-4\n{proton_dir}/share/fonts/\n", encoding="utf-8")
    r = lv.pick_runner(pfx, "123")
    assert r.kind == "proton" and r.wine == [str(proton_dir / "bin" / "wine64")]
    assert r.env["WINEDLLPATH"].startswith(str(proton_dir / "lib64" / "wine"))
    cmd, wait = lv.regedit_argv(r, r"C:\x.reg", "123")
    assert cmd == [str(proton_dir / "bin" / "wine64"), "regedit", "/S", r"C:\x.reg"]
    assert wait == [str(proton_dir / "bin" / "wineserver"), "-w"]

    (pfx.parent / "config_info").unlink()
    monkeypatch.setattr(lv.shutil, "which", lambda n: "/usr/bin/wine" if n == "wine" else None)
    r = lv.pick_runner(pfx, "123")
    assert r.kind == "system" and "different Wine" in r.note
    monkeypatch.setattr(lv.shutil, "which", lambda n: None)
    assert lv.pick_runner(pfx, "123") is None


def test_run_reg_without_any_runner_is_a_clear_error(steam, steam_game, monkeypatch):
    monkeypatch.setattr(lv.shutil, "which", lambda n: None)
    monkeypatch.delenv(lv.WINE_ENV, raising=False)
    lv.use_game(steam_game)
    with pytest.raises(RuntimeError, match="protontricks"):
        lv.run_reg("x", steam["pfx"])


# --- install / unregister ------------------------------------------------------------------------------------
def test_install_layer_places_files_fixes_the_32bit_name_and_registers(steam, steam_game, setup_exe, regs):
    lv.use_game(steam_game)
    logs: list[str] = []
    manifest, fresh = vulkan.install_layer(setup_exe, logs.append, also32=True)
    d = steam["pfx"] / "drive_c" / "ProgramData" / "ReShade"
    assert fresh and manifest == d / "ReShade64.json"
    assert (d / "ReShade64.dll").is_file() and (d / "ReShade32.dll").is_file()
    assert vulkan.layer_name(d / "ReShade64.json") == vulkan.LAYER_NAME
    assert vulkan.layer_name(d / "ReShade32.json") == vulkan.LAYER_NAME32          # upstream 1.8.0 fix, reused
    label, text = regs[-1]
    assert label == "add"
    assert r'"C:\\ProgramData\\ReShade\\ReShade64.json"=dword:00000000' in text
    assert r'"C:\\ProgramData\\ReShade\\ReShade32.json"=dword:00000000' in text and "Wow6432Node" in text
    assert "vulkan-1" not in text                     # native loader disabled in this test
    assert any("registered VK_LAYER_reshade and VK_LAYER_reshade32" in l for l in logs)


def test_install_layer_reuses_a_foreign_registration(steam, steam_game, setup_exe, regs):
    pfx = steam["pfx"]
    other = pfx / "drive_c" / "Program Files" / "ReShade" / "ReShade64.json"
    other.parent.mkdir(parents=True)
    other.write_text(json.dumps({"layer": {"library_path": ".\\ReShade64.dll"}}), encoding="utf-8")
    _write_reg(pfx, [(r"C:\Program Files\ReShade\ReShade64.json", 0)], "user")
    lv.use_game(steam_game)
    manifest, fresh = vulkan.install_layer(setup_exe, None, also32=False)
    assert manifest == other and fresh is False and regs == []


def test_install_layer_without_a_prefix_says_what_to_do(fake_home, game, setup_exe, regs):
    lv.use_game(game)
    with pytest.raises(RuntimeError, match="run it once under Proton"):
        vulkan.install_layer(setup_exe)


def test_unregister_removes_only_ours_and_restores_the_loader(steam, steam_game, setup_exe, regs, monkeypatch):
    pfx = steam["pfx"]
    lv.use_game(steam_game)
    vulkan.install_layer(setup_exe, None, also32=True)
    _write_reg(pfx, [(r"C:\ProgramData\ReShade\ReShade64.json", 0), (r"C:\ProgramData\ReShade\ReShade32.json", 0),
                     (r"C:\Program Files\other\ReShade64.json", 0)], "user")
    sys32 = pfx / "drive_c" / "windows" / "system32"
    (sys32 / "vulkan-1.dll").write_bytes(b"MZ LunarG loader")
    (sys32 / f"vulkan-1.dll{lv.LOADER_BACKUP}").write_bytes(b"MZ Wine builtin DLL")
    assert lv.native_loader_present(pfx)
    assert vulkan.unregister() is True
    label, text = regs[-1]
    assert label == "remove"
    assert r'"C:\\ProgramData\\ReShade\\ReShade64.json"=-' in text and r'"C:\\ProgramData\\ReShade\\ReShade32.json"=-' in text
    assert "other" not in text and '"vulkan-1"=-' in text
    assert b"Wine builtin" in (sys32 / "vulkan-1.dll").read_bytes() and not (sys32 / f"vulkan-1.dll{lv.LOADER_BACKUP}").exists()
    assert not lv.native_loader_present(pfx)


def test_unregister_with_nothing_of_ours_is_false(steam, steam_game, regs):
    lv.use_game(steam_game)
    assert vulkan.unregister() is False and regs == []


# --- context wrappers + shims in place --------------------------------------------------------------------------
def test_installer_and_diagnose_wrappers_set_the_prefix_context(game):
    installer.preview(game, installer.Options(path="feeder"))
    assert lv._ctx["game"] is game
    other = game.install_dir.parent / "Other"
    other.mkdir()
    diagnose.analyse(other)
    assert lv._ctx["game"] is None and lv._ctx["dir"] == other
    diagnose.analyse(game.install_dir)                 # a dir that matches the game keeps the game
    assert lv._ctx["dir"] == other or lv._ctx["dir"] == game.install_dir


def test_core_vulkan_points_at_the_prefix_backend():
    assert vulkan.install_layer is lv.install_layer and vulkan.unregister is lv.unregister
    assert vulkan.registrations is lv.registrations and vulkan.layer_dir is lv.layer_dir


# --- dxvk + overrides --------------------------------------------------------------------------------------------
def test_dxvk_is_a_note_under_proton(tmp_path):
    logs: list[str] = []
    assert dxvk.install(tmp_path, True, logs.append, api="DX9") == ("proton", [])
    assert dxvk.resolve() == ("proton", "")
    assert logs and "Proton already renders DX9" in logs[0]
    assert dxvk.install is ldxvk.install


def test_vulkan_layer_installs_need_no_proxy_override_unless_the_loader_was_swapped(steam, steam_game):
    assert proton.override_entries(steam_game, "(vulkan layer)", "feeder") == []
    sys32 = steam["pfx"] / "drive_c" / "windows" / "system32"
    (sys32 / "vulkan-1.dll").write_bytes(b"MZ LunarG loader")
    assert proton.override_entries(steam_game, "(vulkan layer)", "feeder") == ["vulkan-1=n,b"]
    (sys32 / "vulkan-1.dll").write_bytes(b"MZ Wine builtin DLL")
    assert proton.override_entries(steam_game, "(vulkan layer)", "feeder") == []

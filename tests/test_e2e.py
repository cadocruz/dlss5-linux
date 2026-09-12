"""End to end: a whole install, its record, its removal -- and the CLI itself.

The tests below run the real path. installer.install() writes real files
into a real folder, the manifest is the one the tool would ship, uninstall
is upstream's, and the Vulkan layer goes through linuxport/vulkan.py.

Two things are replaced, and only two:

* the resolvers that ask GitHub or reshade.me what the newest release is
  (sources.resolve_reshade and friends), and
* net.download(), which hands back a synthesized archive under the name the
  installer asked for instead of fetching one.

net.download is also the ONLY door to the network in the install path, so
patching it is what makes these offline; `offline.asked` records every name
that went through it, and a test can say what should have been fetched.

The unit tests elsewhere check the pieces. These check that the pieces still
add up to an install a person could undo.
"""
from __future__ import annotations

import json
import os
import struct
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from conftest import ENGINE, make_game
from core import diagnose, games as core_games, installer, net, prefs, sources
from linuxport import linux_gpu, proton, verify, vulkan as lv
from linuxport import steam as lsteam


# --- the offline harness ----------------------------------------------------------------------
class Offline:
    def __init__(self, cache: Path):
        self.cache = cache
        self.asked: list[str] = []

    def download(self, url: str, name: str, progress=None, force: bool = False, attempts: int = 4) -> Path:
        dest = self.cache / name
        self.asked.append(name)
        if dest.is_file() and not force:
            return dest
        low = name.lower()
        if "reshade_setup" in low:
            # The real installer exe has a zip appended; net.extract_one reads it.
            self._zip(dest, {"ReShade64.dll": b"MZ ReShade64", "ReShade32.dll": b"MZ ReShade32",
                             "ReShade64.json": json.dumps({"file_format_version": "1.0.0", "layer": {
                                 "name": "VK_LAYER_reshade", "library_path": ".\\ReShade64.dll",
                                 "api_version": "1.3.0"}}),
                             "ReShade32.json": json.dumps({"file_format_version": "1.0.0", "layer": {
                                 "name": "VK_LAYER_reshade", "library_path": ".\\ReShade32.dll",
                                 "api_version": "1.3.0"}})})
        elif low.endswith(".addon64") or low.endswith(".addon32"):
            dest.write_bytes(b"MZ add-on")
        elif low.endswith(".zip"):
            self._zip(dest, {"nvngx_dlssnr.dll": b"MZ dlssnr", "nvngx_dlss.dll": b"MZ dlss",
                             "nvngx_dlssg.dll": b"MZ dlssg", "renodx-dlss5.addon64": b"MZ add-on",
                             "Shaders/Fixture.fx": b"// fx", "Textures/Fixture.png": b"PNG"})
        else:
            dest.write_bytes(b"MZ")
        return dest

    @staticmethod
    def _zip(dest: Path, members: dict) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(dest, "w") as z:
            for name, data in members.items():
                z.writestr(name, data)


def _entry(fam: str, label: str, key: tuple) -> dict:
    return {"tag": f"{fam}-{label}", "label": label, "size": 1024, "key": key,
            "url": f"https://example.invalid/{fam}-{label}.zip"}


# What the rhi mirror publishes, in the shape sources.rhi_catalog() returns.
CATALOG = {
    "dlssnr": [_entry("dlssnr", "310.8.0", (310, 8, 0)),
               _entry("dlssnr", "310.8.0-RTX40", (310, 8, 0)),
               _entry("dlssnr", "310.8.SF-v2", (310, 8, 2))],
    "renodx": [_entry("renodx-dlss5", "4.70", (4, 70)),
               _entry("renodx-dlss5", "4.55", (4, 55))],
    "renodx_sf": [_entry("renodx-dlss-SF", "1.0", (1, 0))],
    "dlss": [_entry("dlss", "310.9.0", (310, 9, 0))],
    "dlssg": [_entry("dlssg", "310.9.0", (310, 9, 0))],
}

# The feeder release, with the loose assets the installer asks for by name.
FEEDER_RELEASE = {
    "tag_name": "v0.14.0",
    "assets": [{"name": n, "browser_download_url": f"https://example.invalid/{n}"}
               for n in ("dlss5-feed.addon64", "dlss5-feed.addon32",
                         "dlss5-feed-host64.exe", "DLSS5_Feed.fx")],
}


def canned_json(url: str):
    """Every release API this can reach, answered from the table above.

    An API this does not know about is a failure: a new resolver in a future
    upstream would otherwise quietly start fetching during the tests.
    """
    low = url.lower()
    if "dlss5-feeder" in low:
        return [FEEDER_RELEASE] if low.rstrip("/").endswith("releases") else FEEDER_RELEASE
    raise AssertionError(f"an unstubbed release API was called: {url}")


@pytest.fixture
def offline(tmp_path, monkeypatch) -> Offline:
    cache = tmp_path / "download-cache"
    cache.mkdir()
    off = Offline(cache)
    monkeypatch.setattr(net, "cache_dir", lambda: cache)
    monkeypatch.setattr(net, "download", off.download)
    monkeypatch.setattr(net, "fetch_text", lambda url, **k: b"// fixture header\n")
    monkeypatch.setattr(sources, "resolve_reshade",
                        lambda: ("6.5.2", "https://example.invalid/ReShade_Setup_6.5.2_Addon.exe"))
    monkeypatch.setattr(sources, "rhi_catalog", lambda force=False: CATALOG)
    monkeypatch.setattr(sources, "_json", canned_json)
    monkeypatch.setattr(sources, "cached_json", canned_json)
    monkeypatch.setattr(sources, "last_fallback", None)
    monkeypatch.setattr(linux_gpu, "_smi", lambda: ("NVIDIA GeForce RTX 5090", "610.57.04", "12.0"))
    return off


class FakeWine:
    """`wine regedit /S`, in thirty lines.

    linuxport.vulkan writes a .reg and hands it to wine; unregister() then
    reads the result back out of the prefix's user.reg. A stub that only
    records the text would let an install "register" a layer that unregister
    can never find, which is precisely the bug this file is here to catch --
    so the fake applies the file to user.reg / system.reg in Wine's own
    format instead.
    """

    def __init__(self):
        self.applied: list[tuple[str, str]] = []

    def run_reg(self, text: str, pfx: Path, log=None, label: str = "layer") -> bool:
        self.applied.append((label, text))
        hives: dict[str, dict[str, dict[str, str]]] = {"user.reg": {}, "system.reg": {}}
        for fname in hives:
            for key, values in self._read(pfx / fname).items():
                hives[fname][key] = values
        key = fname = None
        for raw in text.splitlines():
            line = raw.strip()
            if line.startswith("[") and line.endswith("]"):
                hive, _, key = line[1:-1].partition("\\")
                fname = "user.reg" if hive == "HKEY_CURRENT_USER" else "system.reg"
                hives[fname].setdefault(key, {})
                continue
            if not line or key is None or "=" not in line:
                continue
            name, _, value = line.partition("=")
            if value == "-":
                hives[fname][key].pop(name, None)
            else:
                hives[fname][key][name] = value
        for fname, keys in hives.items():
            out = ["WINE REGISTRY Version 2", ""]
            for k, values in keys.items():
                out += [f"[{k.replace(chr(92), chr(92) * 2)}] 1700000000", "#time=1d9"]
                out += [f"{n}={v}" for n, v in values.items()]
                out.append("")
            (pfx / fname).write_text("\n".join(out), encoding="utf-8")
        return True

    @staticmethod
    def _read(path: Path) -> dict[str, dict[str, str]]:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return {}
        keys: dict[str, dict[str, str]] = {}
        key = None
        for raw in text.splitlines():
            line = raw.strip()
            if line.startswith("[") and "]" in line:
                key = line[1:line.index("]")].replace("\\\\", "\\")
                keys.setdefault(key, {})
            elif key and "=" in line and not line.startswith("#"):
                name, _, value = line.partition("=")
                keys[key][name] = value
        return keys


@pytest.fixture
def regs(monkeypatch) -> list[tuple[str, str]]:
    """Every .reg the Vulkan layer imports, applied to the prefix for real."""
    wine = FakeWine()
    monkeypatch.setattr(lv, "run_reg", wine.run_reg)
    monkeypatch.setattr(lv, "ensure_native_loader", lambda pfx, x64, also32, log=None: False)
    monkeypatch.setenv(lv.NATIVE_LOADER_ENV, "0")
    return wine.applied


@pytest.fixture(autouse=True)
def clear_prefix_ctx():
    lv._ctx["game"] = lv._ctx["dir"] = None
    yield
    lv._ctx["game"] = lv._ctx["dir"] = None


def tree(root: Path) -> dict[str, int]:
    """Every file under root, relative path -> size."""
    return {str(p.relative_to(root)).replace("\\", "/"): p.stat().st_size
            for p in sorted(root.rglob("*")) if p.is_file()}


def dlss_game(folder: Path, api: str = "DX12") -> core_games.Game:
    """A game that ships its own DLSS, so every route is on the table."""
    g = make_game(folder, api=api, exe_sub="Binaries/Win64")
    (g.install_dir / "nvngx_dlss.dll").write_bytes(b"MZ game dlss")
    return g


# --- install -> record -> uninstall ------------------------------------------------------------
def test_a_native_install_writes_its_record_and_uninstall_gives_the_folder_back(tmp_path, offline):
    g = dlss_game(tmp_path / "Fixture Game")
    root = g.install_dir
    before = tree(g.folder)

    rep = installer.install(g, installer.Options(path="native", native_dlss=True))

    assert (root / "dxgi.dll").read_bytes() == b"MZ ReShade64"
    assert (root / "nvngx_dlssnr.dll").is_file() and (root / "ReShade.ini").is_file()
    assert "dxgi.dll" in rep.written and "nvngx_dlssnr.dll" in rep.written
    # the game's own DLSS is never touched on this route
    assert (root / "nvngx_dlss.dll").read_bytes() == b"MZ game dlss"

    man = installer._previous_manifest(root)
    assert man and man["path"] == "native" and man["proxy"] == "dxgi.dll"
    assert set(rep.written) <= set(man["files"]) | {"dxgi.dll"}
    assert diagnose.analyse(root).route == "native"

    removed = installer.uninstall(g)
    assert removed
    assert tree(g.folder) == before, "uninstall left something behind"
    assert installer._previous_manifest(root) is None


def test_a_file_the_install_overwrites_comes_back_on_uninstall(tmp_path, offline):
    """The game's own nvngx_dlssnr.dll is replaced by the build we install,
    and uninstall has to give the original back byte for byte."""
    g = dlss_game(tmp_path / "Fixture Game")
    root = g.install_dir
    (root / "nvngx_dlssnr.dll").write_bytes(b"MZ the game's own dlssnr")
    before = tree(g.folder)

    installer.install(g, installer.Options(path="native", native_dlss=True))
    assert (root / "nvngx_dlssnr.dll").read_bytes() == b"MZ dlssnr"      # ours now
    assert any(p.name.startswith("nvngx_dlssnr.dll") and p.name != "nvngx_dlssnr.dll"
               for p in root.iterdir()), "no backup of the file we replaced"

    installer.uninstall(g)
    assert (root / "nvngx_dlssnr.dll").read_bytes() == b"MZ the game's own dlssnr"
    assert tree(g.folder) == before


def test_a_foreign_injector_under_our_proxy_name_is_refused(tmp_path, offline):
    """Someone else's dxgi.dll is not ours to move: the install stops and the
    folder is exactly as it was."""
    g = dlss_game(tmp_path / "Fixture Game")
    (g.install_dir / "dxgi.dll").write_bytes(b"MZ Special K or DXVK")
    before = tree(g.folder)
    with pytest.raises(installer.InstallError, match="not ReShade"):
        installer.install(g, installer.Options(path="native", native_dlss=True))
    assert tree(g.folder) == before


def test_the_feeder_route_installs_and_comes_out_again(tmp_path, offline):
    """A second route, because they write different files and the uninstall
    reads the manifest, not a fixed list."""
    g = make_game(tmp_path / "No DLSS Here", api="DX12", exe_sub="Binaries/Win64")
    before = tree(g.folder)
    rep = installer.install(g, installer.Options(path="feeder", native_dlss=False))
    assert rep.written and installer._previous_manifest(g.install_dir)["path"] == "feeder"
    installer.uninstall(g)
    assert tree(g.folder) == before


def test_nothing_reached_the_network_and_the_reshade_setup_was_asked_for_once(tmp_path, offline):
    g = dlss_game(tmp_path / "Fixture Game")
    installer.install(g, installer.Options(path="native", native_dlss=True))
    assert offline.asked.count("ReShade_Setup_6.5.2_Addon.exe") == 1
    assert all(not n.startswith("http") for n in offline.asked)


# --- the Steam side: launch options round trip --------------------------------------------------
def test_install_then_launch_options_then_verify_on_a_steam_game(steam, steam_game, offline, monkeypatch):
    g = steam_game
    g.api, g.bitness = "DX12", 64
    (g.install_dir / "nvngx_dlss.dll").write_bytes(b"MZ game dlss")
    monkeypatch.setattr(proton, "steam_running", lambda: False)

    installer.install(g, installer.Options(path="native", native_dlss=True))
    assert proton.appid_for(g) == "123"

    line = proton.launch_options(g, "dxgi.dll", indicator=True, route="native")
    assert 'WINEDLLOVERRIDES="dxgi=n,b"' in line and "PROTON_DLSS_INDICATOR=1" in line
    assert proton.missing_overrides(g, "dxgi.dll", "native") == ["dxgi=n,b"]

    backup = proton.set_launch_options(g, line)
    assert backup.is_file()
    assert lsteam.launch_options_for("123") == line
    assert proton.missing_overrides(g, "dxgi.dll", "native") == []

    rows = verify.run(g)
    by_title = {t: (lvl, detail) for lvl, t, detail in rows}
    assert by_title["route"][1] == "native"
    assert by_title["WINEDLLOVERRIDES"] == ("OK", "dxgi=n,b present")     # what we just wrote
    assert by_title["Proton prefix"][0] == "OK" and by_title["NGX bridge"][0] == "OK"

    installer.uninstall(g)
    assert installer._previous_manifest(g.install_dir) is None


# --- the Vulkan layer, which lives in the prefix -------------------------------------------------
def test_a_vulkan_game_registers_the_layer_in_its_prefix_and_hands_it_back(steam, steam_game, offline, regs):
    g = steam_game
    g.api, g.bitness = "Vulkan", 64
    root = g.install_dir
    layer_dir = steam["pfx"] / "drive_c" / "ProgramData" / "ReShade"

    installer.install(g, installer.Options(path="feeder", native_dlss=False))

    assert not (root / "dxgi.dll").exists(), "a Vulkan game must not get a proxy DLL"
    assert (layer_dir / "ReShade64.dll").is_file() and (layer_dir / "ReShade64.json").is_file()
    assert str(root) in prefs.vulkan_games()
    assert regs and regs[-1][0] == "add"
    assert r"C:\\ProgramData\\ReShade\\ReShade64.json" in regs[-1][1]

    installer.uninstall(g)
    assert str(root) not in prefs.vulkan_games()
    assert regs[-1][0] == "remove", "the prefix kept our layer registration"


def test_a_vulkan_game_in_another_prefix_does_not_keep_this_one_registered(
        steam, steam_game, offline, regs, monkeypatch):
    """Upstream counts Vulkan installs for the whole Windows account, because
    there is one registration. Here each prefix has its own, and the count
    that decides has to be per prefix -- or the last game in prefix A leaves
    A registered for good because B, in its own prefix, is still installed."""
    other_root = steam["steamapps"] / "common" / "Second" / "Binaries" / "Win64"
    other_root.mkdir(parents=True)
    (steam["steamapps"] / "appmanifest_456.acf").write_text(
        '"AppState"\n{\n\t"appid"\t\t"456"\n\t"name"\t\t"Second Game"\n\t"installdir"\t\t"Second"\n}\n',
        encoding="utf-8")
    other_pfx = steam["steamapps"] / "compatdata" / "456" / "pfx"
    (other_pfx / "drive_c" / "windows" / "system32").mkdir(parents=True)
    prefs.add_vulkan_game(other_root)                 # a Vulkan install in the OTHER prefix
    monkeypatch.setattr(proton, "_APPIDS", None)

    g = steam_game
    g.api, g.bitness = "Vulkan", 64
    installer.install(g, installer.Options(path="feeder", native_dlss=False))
    assert {str(g.install_dir), str(other_root)} <= set(prefs.vulkan_games())

    installer.uninstall(g)
    assert regs[-1][0] == "remove"
    assert str(other_root) in prefs.vulkan_games(), "the other prefix's record was dropped too"


def test_the_other_prefix_is_left_alone_when_two_games_share_one(steam, steam_game, offline, regs):
    """The other side of the same rule: two games in ONE prefix, and the
    first uninstall must keep the registration the second one still needs."""
    sibling = steam["common"] / "Extras" / "Win64"
    sibling.mkdir(parents=True)
    prefs.add_vulkan_game(sibling)                    # same appid, same prefix

    g = steam_game
    g.api, g.bitness = "Vulkan", 64
    installer.install(g, installer.Options(path="feeder", native_dlss=False))
    before = len(regs)
    installer.uninstall(g)
    assert [label for label, _ in regs[before:]] == [], "unregistered a layer another game uses"


# --- the command line, as a person would run it ---------------------------------------------------
def _pe(path: Path) -> None:
    """A minimal but real 64-bit PE, so pe.exe_bitness answers."""
    head = bytearray(0x40)
    head[0:2] = b"MZ"
    struct.pack_into("<I", head, 0x3C, 0x40)
    path.write_bytes(bytes(head) + b"PE\0\0" + struct.pack("<H", 0x8664) + b"\0" * 96)


def _cli(tmp_path: Path, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env.update(HOME=str(home), USERPROFILE=str(home),
               XDG_CACHE_HOME=str(tmp_path / "xdg-cache"),
               XDG_CONFIG_HOME=str(tmp_path / "xdg-config"),
               XDG_STATE_HOME=str(tmp_path / "xdg-state"))
    env.pop("STEAM_ROOT", None)
    env.pop("WINEPREFIX", None)
    return subprocess.run([sys.executable, str(ENGINE / "dlss5_linux.py"), *args],
                          capture_output=True, text=True, env=env, timeout=180)


@pytest.fixture
def cli_game(tmp_path) -> Path:
    root = tmp_path / "CLI Game" / "Binaries" / "Win64"
    root.mkdir(parents=True)
    _pe(root / "Fixture-Win64-Shipping.exe")
    (root / "nvngx_dlss.dll").write_bytes(b"MZ")
    return root


def test_the_cli_recommends_a_route_for_a_folder(tmp_path, cli_game):
    r = _cli(tmp_path, "check", str(cli_game))
    assert r.returncode == 0, r.stderr
    assert "Traceback" not in r.stderr
    assert "recommend " in r.stdout and "routes:" in r.stdout and "plan " in r.stdout


def test_the_cli_names_the_optiscaler_line_it_would_fetch(tmp_path, cli_game):
    """--opti-build reaches plan(), which is the only place the preview says
    which OptiScaler is meant -- it used to be install-only."""
    plain = _cli(tmp_path, "check", str(cli_game))
    fork = _cli(tmp_path, "check", str(cli_game), "--opti-build", "y4my4my4m")
    assert fork.returncode == 0, fork.stderr
    assert "y4m" in fork.stdout.lower() and fork.stdout != plain.stdout


def test_the_cli_prints_the_override_line_for_a_non_steam_game(tmp_path, cli_game):
    r = _cli(tmp_path, "launch-options", str(cli_game), "--proxy", "dxgi.dll")
    assert r.returncode == 0, r.stderr
    assert "WINEDLLOVERRIDES=dxgi=n,b" in r.stdout


def test_the_cli_verifies_a_folder_with_nothing_installed(tmp_path, cli_game):
    r = _cli(tmp_path, "verify", str(cli_game))
    assert r.returncode == 0, r.stderr
    assert "route" in r.stdout and "no manifest" in r.stdout


def test_the_cli_says_there_is_nothing_to_migrate(tmp_path, cli_game):
    r = _cli(tmp_path, "migrate", str(cli_game))
    assert r.returncode == 0, r.stderr
    assert "nothing to migrate" in r.stdout


def test_the_cli_does_not_traceback_on_something_that_is_not_an_executable(tmp_path):
    junk = tmp_path / "Not A Game"
    junk.mkdir()
    (junk / "readme.txt").write_text("hello", encoding="utf-8")
    fake = junk / "launcher.exe"
    fake.write_text("#!/bin/sh\necho hi\n", encoding="utf-8")
    r = _cli(tmp_path, "check", str(fake))
    assert "Traceback" not in r.stderr, r.stderr
    assert "BLOCKED" in r.stdout or "not a windows executable" in r.stdout.lower()

"""Test harness for the Linux layer.

Everything here is pure Python against temporary directories: no Steam, no
GPU, no network, no Qt. The XDG variables are pointed at a scratch directory
*before* linuxport is imported, because linuxport.paths reads them at import
time and would otherwise write settings into the real ~/.config.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "dlss5-linux"
FIXTURES = Path(__file__).resolve().parent / "fixtures"

_SCRATCH = Path(tempfile.mkdtemp(prefix="dlss5-tests-"))
for var, sub in (("XDG_CACHE_HOME", "cache"), ("XDG_CONFIG_HOME", "config"), ("XDG_STATE_HOME", "state")):
    os.environ[var] = str(_SCRATCH / sub)

sys.path.insert(0, str(ENGINE))
sys.path.insert(0, str(ROOT / "tools"))

import linuxport  # noqa: E402

linuxport.activate()

from core import games as core_games  # noqa: E402
from core import prefs as core_prefs  # noqa: E402
from linuxport import proton  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_prefs(tmp_path, monkeypatch):
    """Every test gets its own settings.json and a cold appid cache."""
    monkeypatch.setattr(core_prefs, "FILE", tmp_path / "settings.json")
    monkeypatch.setattr(proton, "_APPIDS", None)
    yield


@pytest.fixture
def fake_home(tmp_path, monkeypatch) -> Path:
    """A HOME with nothing in it; Path.home() and $HOME both point here."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(proton, "XLCORE", home / ".xlcore")
    monkeypatch.delenv("WINEPREFIX", raising=False)
    monkeypatch.delenv("STEAM_ROOT", raising=False)
    return home


def make_game(folder: Path, exe_name: str = "Fixture-Win64-Shipping.exe", *,
              api: str = "DX12", bitness: int = 64, source: str = "Manual",
              exe_sub: str = "") -> core_games.Game:
    exe_dir = folder / exe_sub if exe_sub else folder
    exe_dir.mkdir(parents=True, exist_ok=True)
    exe = exe_dir / exe_name
    if not exe.exists():
        exe.write_bytes(b"MZ" + b"\0" * 62)
    g = core_games.Game(name=folder.name, folder=folder, exe=exe, source=source)
    g.api, g.api_why, g.bitness = api, "fixture", bitness
    return g


@pytest.fixture
def game(tmp_path) -> core_games.Game:
    return make_game(tmp_path / "Fixture Game", exe_sub="Binaries/Win64")


@pytest.fixture
def steam(fake_home) -> dict:
    """A Steam root under the fake HOME with one installed game (appid 123)
    that has a populated Proton prefix, and a second appid (456) with none.

    Returns the paths the tests need.
    """
    root = fake_home / ".steam" / "steam"
    steamapps = root / "steamapps"
    common = steamapps / "common" / "Fixture Game"
    (common / "Binaries" / "Win64").mkdir(parents=True)
    (steamapps / "libraryfolders.vdf").write_text(
        '"libraryfolders"\n{\n\t"0"\n\t{\n\t\t"path"\t\t"%s"\n\t}\n}\n' % str(root).replace("\\", "\\\\"),
        encoding="utf-8")
    (steamapps / "appmanifest_123.acf").write_text(
        '"AppState"\n{\n\t"appid"\t\t"123"\n\t"name"\t\t"Fixture Game"\n'
        '\t"installdir"\t\t"Fixture Game"\n}\n', encoding="utf-8")
    pfx = steamapps / "compatdata" / "123" / "pfx"
    (pfx / "drive_c" / "windows" / "system32").mkdir(parents=True)
    (pfx / "drive_c" / "windows" / "system32" / "_nvngx.dll").write_bytes(b"MZ")
    cfg = root / "userdata" / "1001" / "config"
    cfg.mkdir(parents=True)
    (cfg / "localconfig.vdf").write_text((FIXTURES / "localconfig.vdf").read_text(encoding="utf-8"),
                                         encoding="utf-8")
    return {"root": root, "steamapps": steamapps, "common": common, "pfx": pfx,
            "localconfig": cfg / "localconfig.vdf"}


@pytest.fixture
def steam_game(steam) -> core_games.Game:
    return make_game(steam["common"], exe_sub="Binaries/Win64", source="Steam")

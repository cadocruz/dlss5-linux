"""linuxport.tuning: OptiScaler.ini edits the Proton findings ask for."""
from __future__ import annotations

from pathlib import Path

import pytest

from core import optiscaler
from linuxport import tuning

INI = optiscaler.INI


@pytest.fixture
def exe_dir(tmp_path) -> Path:
    return tmp_path


@pytest.fixture
def loader(exe_dir, monkeypatch):
    """An installed OptiScaler binary; `set(tokens)` decides what it knows."""
    p = exe_dir / "winmm.dll"
    monkeypatch.setattr(optiscaler, "find_existing", lambda d, ignore="": [p] if p.is_file() else [])

    def set_tokens(*tokens: str, wide: bool = False) -> Path:
        body = b"".join((t.encode("utf-16-le") if wide else t.encode()) + b"\0" for t in tokens)
        p.write_bytes(b"MZ" + b"\0" * 62 + body)
        return p
    return set_tokens


def read(exe_dir: Path) -> str:
    return (exe_dir / INI).read_text(encoding="utf-8")


def section(text: str, name: str) -> dict[str, str]:
    out, inside = {}, False
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("["):
            inside = s.lower() == f"[{name.lower()}]"
        elif inside and "=" in s and not s.startswith((";", "#")):
            k, v = s.split("=", 1)
            out[k.strip()] = v.strip()
    return out


# --- loader_has --------------------------------------------------------------------------
def test_loader_has_without_a_loader_is_unknown(exe_dir, loader):
    assert tuning.loader_has(exe_dir, "dlss_12") is None


def test_loader_has_reads_ascii_and_utf16(exe_dir, loader):
    loader("Dx11Upscaler", "fsr22_12")
    assert tuning.loader_has(exe_dir, "dlss_12") is False
    loader("dlss_12")
    assert tuning.loader_has(exe_dir, "dlss_12") is True
    loader("dlss_12", wide=True)
    assert tuning.loader_has(exe_dir, "dlss_12") is True


# --- bridged upscaler -------------------------------------------------------------------------
def test_bridged_upscaler_prefers_dlss_12_when_the_build_has_it(exe_dir, loader):
    loader("dlss_12")
    logs: list[str] = []
    optiscaler.set_dx11_bridged_upscaler(exe_dir, logs.append)     # patched by tuning.install()
    ups = section(read(exe_dir), "Upscalers")
    assert ups == {"Dx11Upscaler": "dlss_12", "Dx12Upscaler": "dlss"}
    assert any("dlss_12" in l for l in logs)


def test_bridged_upscaler_falls_back_to_upstream_fsr_without_the_token(exe_dir, loader):
    loader("fsr22_12")
    logs: list[str] = []
    optiscaler.set_dx11_bridged_upscaler(exe_dir, logs.append)
    assert section(read(exe_dir), "Upscalers")["Dx11Upscaler"] == "fsr22_12"
    assert any("no dlss_12" in l for l in logs)


def test_bridged_upscaler_without_a_loader_uses_upstream(exe_dir, loader):
    optiscaler.set_dx11_bridged_upscaler(exe_dir)
    assert section(read(exe_dir), "Upscalers")["Dx11Upscaler"] == "fsr22_12"


# --- apply -------------------------------------------------------------------------------------
def test_apply_turns_logging_on_and_keeps_the_rest(exe_dir):
    (exe_dir / INI).write_text("; user comment\n[Upscalers]\nDx12Upscaler=xess\n\n[Log]\nLogToFile=auto\nLogLevel=4\n",
                               encoding="utf-8")
    notes = tuning.apply(exe_dir)
    text = read(exe_dir)
    assert text.startswith("; user comment\n")
    assert section(text, "Upscalers") == {"Dx12Upscaler": "xess"}
    assert section(text, "Log") == {"LogToFile": "true", "LogLevel": "2"}
    assert text.count("[Log]") == 1
    assert notes == []


def test_apply_creates_the_ini_when_missing(exe_dir):
    tuning.apply(exe_dir)
    assert section(read(exe_dir), "Log")["LogToFile"] == "true"


def test_apply_is_idempotent(exe_dir):
    tuning.apply(exe_dir)
    once = read(exe_dir)
    tuning.apply(exe_dir)
    assert read(exe_dir) == once


def test_ffxiv_overlay_with_a_dlss_12_build(exe_dir, loader):
    loader("dlss_12")
    (exe_dir / "ffxiv_dx11.exe").write_bytes(b"MZ")
    logs: list[str] = []
    notes = tuning.apply(exe_dir, logs.append)
    text = read(exe_dir)
    assert section(text, "Upscalers") == {"Dx11Upscaler": "dlss_12", "Dx12Upscaler": "dlss"}
    assert section(text, "DRS") == {"DrsMinOverrideEnabled": "true", "DrsMaxOverrideEnabled": "true"}
    assert set(section(text, "QualityOverrides").values()) == {"1.300000"}
    assert section(text, "Dx11withDx12") == {"UseDelayedInit": "true"}
    assert section(text, "Hooks") == {"HookOriginalNvngxOnly": "true"}
    assert section(text, "InitFlags") == {"DepthInverted": "true"}
    assert notes == tuning.NOTES["ffxiv_dx11.exe"] and notes
    assert any("ffxiv_dx11.exe keys" in l for l in logs)


def test_ffxiv_overlay_downgrades_the_bridge_upscaler_without_the_token(exe_dir, loader):
    loader("fsr22_12")
    (exe_dir / "ffxiv_dx11.exe").write_bytes(b"MZ")
    tuning.apply(exe_dir)
    assert section(read(exe_dir), "Upscalers")["Dx11Upscaler"] == "fsr22_12"
    # the module-level table is not mutated by the downgrade
    assert tuning.OVERLAYS["ffxiv_dx11.exe"]["Upscalers"]["Dx11Upscaler"] == "dlss_12"


def test_other_games_get_logging_only(exe_dir):
    (exe_dir / "Other-Win64-Shipping.exe").write_bytes(b"MZ")
    assert tuning.apply(exe_dir) == []
    assert "DRS" not in read(exe_dir)


def test_game_notes():
    assert tuning.game_notes("FFXIV_DX11.EXE") == tuning.NOTES["ffxiv_dx11.exe"]
    assert tuning.game_notes("other.exe") == []
    assert tuning.game_notes(None) == []


# --- the install wrapper ---------------------------------------------------------------------------
def test_optiscaler_install_is_followed_by_tuning(exe_dir, monkeypatch):
    seen: dict = {}

    def fake_install(d, *args, **kwargs):
        seen["dir"], seen["kwargs"] = d, kwargs
        return ["winmm.dll", INI]
    monkeypatch.setattr(tuning, "_orig_install", fake_install)
    logs: list[str] = []
    written = optiscaler.install(exe_dir, proxy="winmm.dll", log=logs.append)   # patched by tuning.install()
    assert written == ["winmm.dll", INI]
    assert seen["dir"] == exe_dir and seen["kwargs"]["proxy"] == "winmm.dll"
    assert section(read(exe_dir), "Log")["LogToFile"] == "true"
    assert any("LogToFile=true" in l for l in logs)

"""OptiScaler.ini tuning the Proton findings ask for, applied after install.

Three things, all idempotent and section-scoped (OptiScaler.ini repeats key
names across sections, so a flat search-and-replace would hit the wrong one):

1. Logging on. Upstream leaves LogToFile at auto (off); every diagnosis this
   tool does reads OptiScaler.log, so it has to exist.
2. The D3D11 bridge upscaler. The neural model is D3D12-only, so a D3D11 game
   needs one of the `*_12` upscalers. Upstream hard-codes fsr22_12 because its
   OptiScaler could not run DLSS on the bridge; the y4my4m line can
   (`dlss_12`), which keeps the game's DLSS quality mode and looks better. The
   loader is checked for the token before it is written -- an ini key the
   binary does not know is silently ignored, which is how a v0.1.2 ini once
   "had" ManualInputPolling.
3. Per-game overlays. Final Fantasy XIV's dynamic resolution fights the bridge
   unless OptiScaler takes the DRS bounds and every quality ratio is one value
   (the RenoDX Discord thread's tested ini, reproduced under Proton 2026-09-06).
"""
from __future__ import annotations

from pathlib import Path

from core import optiscaler as _opti

LOGGING = {"Log": {"LogToFile": "true", "LogLevel": "2"}}

# exe name (lower-case) -> {section: {key: value}}
OVERLAYS: dict[str, dict[str, dict[str, str]]] = {
    "ffxiv_dx11.exe": {
        "Upscalers": {"Dx11Upscaler": "dlss_12", "Dx12Upscaler": "dlss"},
        "DRS": {"DrsMinOverrideEnabled": "true", "DrsMaxOverrideEnabled": "true"},
        "QualityOverrides": {k: "1.300000" for k in (
            "QualityRatioDLAA", "QualityRatioUltraQuality", "QualityRatioQuality",
            "QualityRatioBalanced", "QualityRatioPerformance", "QualityRatioUltraPerformance")},
        "Dx11withDx12": {"UseDelayedInit": "true"},
        "Hooks": {"HookOriginalNvngxOnly": "true"},
        "InitFlags": {"DepthInverted": "true"},
    },
}

# What the user has to know per game, beyond the ini. Shown by the GUI's
# "game notes" and printed after a CLI install.
NOTES: dict[str, list[str]] = {
    "ffxiv_dx11.exe": [
        "in-game: DLSS on, Borderless window, Frame Rate Threshold = Always enabled",
        "do not change resolution, display mode or AO with DLSS w/Dx12 active -- it crashes",
        "open the overlay (Insert) only after your character is in the world",
        "XIVLauncher-RB: Wine tab > DLL overrides needs winmm=n,b;d3d12=n,b;d3d12core=n,b "
        "(Proton skips its own d3d12 wiring on umu's runinprefix path)",
        "multipass (Passes>1) flashes: the game runs two DLSS features per frame and the "
        "fork's pass gate is per command list -- leave Passes=1 until upstream fixes it",
        "frame generation: XeFG via OptiFG is what works on Windows; untested here",
    ],
}

_orig_bridged = _opti.set_dx11_bridged_upscaler
_orig_install = _opti.install


def _loaders(exe_dir: Path) -> list[Path]:
    try:
        return list(_opti.find_existing(exe_dir))
    except Exception:
        return []


def loader_has(exe_dir: Path, token: str) -> bool | None:
    """Does the installed OptiScaler binary know this ini value? None = no loader found."""
    paths = _loaders(exe_dir)
    if not paths:
        return None
    needle, wide = token.encode(), token.encode("utf-16-le")
    for p in paths:
        try:
            data = p.read_bytes()
        except OSError:
            continue
        if needle in data or wide in data:
            return True
    return False


def _write(exe_dir: Path, sections: dict[str, dict[str, str]], log) -> None:
    p = exe_dir / _opti.INI
    try:
        text = p.read_text(encoding="utf8", errors="replace") if p.is_file() else ""
        for section, values in sections.items():
            text = _opti._ini_set(text, section, values)
        p.write_text(text, encoding="utf8")
    except OSError:
        log(f"      could not write {_opti.INI}")


def bridged_upscaler(exe_dir: Path, log=None) -> None:
    """Replacement for core.optiscaler.set_dx11_bridged_upscaler."""
    log = log or (lambda *_: None)
    has = loader_has(exe_dir, "dlss_12")
    if has:
        _write(exe_dir, {"Upscalers": {"Dx11Upscaler": "dlss_12", "Dx12Upscaler": "dlss"}}, log)
        log("      OptiScaler.ini: [Upscalers] Dx11Upscaler=dlss_12 (DLSS on the D3D12 bridge; "
            "this build carries the neural pass on it)")
        return
    if has is False:
        log("      this OptiScaler build has no dlss_12 -- FSR 2.2 on the bridge carries the model")
    _orig_bridged(exe_dir, log)


def apply(exe_dir: Path, log=None) -> list[str]:
    """Logging + the per-game overlay for whatever exe sits in exe_dir. Returns notes."""
    log = log or (lambda *_: None)
    _write(exe_dir, LOGGING, log)
    log("      OptiScaler.ini: LogToFile=true, LogLevel=2 (diagnosis reads the log)")
    notes: list[str] = []
    try:
        exes = {p.name.lower() for p in exe_dir.iterdir() if p.suffix.lower() == ".exe"}
    except OSError:
        exes = set()
    for exe, sections in OVERLAYS.items():
        if exe not in exes:
            continue
        sections = {s: dict(v) for s, v in sections.items()}
        if "Upscalers" in sections and loader_has(exe_dir, "dlss_12") is False:
            sections["Upscalers"]["Dx11Upscaler"] = "fsr22_12"
        _write(exe_dir, sections, log)
        keys = sum(len(v) for v in sections.values())
        log(f"      OptiScaler.ini: {keys} {exe} keys ({', '.join(sections)})")
        notes = NOTES.get(exe, [])
        for n in notes:
            log(f"      note: {n}")
    return notes


def game_notes(exe_name: str | None) -> list[str]:
    return NOTES.get((exe_name or "").lower(), [])


def _install_wrapped(exe_dir: Path, *args, **kwargs):
    written = _orig_install(exe_dir, *args, **kwargs)
    apply(Path(exe_dir), kwargs.get("log"))
    return written


def install() -> None:
    _opti.set_dx11_bridged_upscaler = bridged_upscaler
    _opti.install = _install_wrapped

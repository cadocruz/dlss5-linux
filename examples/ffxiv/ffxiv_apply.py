#!/usr/bin/env python3
"""Put DLSS-NR (OptiScaler, y4my4m nightly) on FINAL FANTASY XIV under XIVLauncher-RB + Proton.

Run with the game AND the launcher closed. Steps, each idempotent:
  1. dlss5_proton.py install <exe> --loader winmm.dll --prefix <xlcore protonprefix/pfx>
     (FFXIV crashes with any proxy but winmm/winhttp; game's DLSS stays ON)
  2. merge OptiScaler.ffxiv-keys.ini over the installed OptiScaler.ini
     (dlss_12 bridge, DRS overrides, quality ratios 1.3, delayed init, logging)
  3. optionally `dlls install` -> DLSS 310.9.0 SR into game/ (--dlls)
  4. WineDLLOverrides=winmm=n,b in ~/.xlcore/launcher.ini (RB regex-validated key;
     Proton appends its own overrides after it, so this survives)
Undo: ffxiv_apply.py --undo  (restore + dlls restore + clear the override).
"""
from __future__ import annotations
import argparse, configparser, os, re, subprocess, sys
from pathlib import Path

WORK = Path(__file__).resolve().parents[1]
TOOL = WORK / "proton-tool" / "dlss5_proton.py"
KEYS = Path(__file__).resolve().parent / "OptiScaler.ffxiv-keys.ini"
XLCORE = Path.home() / ".xlcore"
LAUNCHER_INI = XLCORE / "launcher.ini"
PREFIX = XLCORE / "protonprefix" / "pfx"


def game_dir() -> Path:
    for line in LAUNCHER_INI.read_text(encoding="utf8").splitlines():
        if line.startswith("GamePath="):
            return Path(line.split("=", 1)[1].strip()) / "game"
    sys.exit("GamePath not found in launcher.ini")


def running() -> list[str]:
    out = subprocess.run(["pgrep", "-af", r"XIVLauncher[.]Core|ffxiv_dx11[.]exe"],
                         capture_output=True, text=True).stdout.strip()
    return [l for l in out.splitlines() if l]


def tool(*args: str) -> None:
    cmd = [sys.executable, str(TOOL), *args]
    print("  $", " ".join(f'"{a}"' if " " in a else a for a in cmd[2:]))
    r = subprocess.run(cmd)
    if r.returncode != 0:
        sys.exit(f"tool failed ({r.returncode})")


def merge_keys(ini: Path) -> None:
    """Overlay [section] key=value pairs from KEYS onto ini, preserving everything else."""
    text = ini.read_text(encoding="utf8", errors="replace")
    overlay = configparser.ConfigParser(interpolation=None, strict=False)
    overlay.optionxform = str
    overlay.read_string(KEYS.read_text(encoding="utf8"))
    lines = text.splitlines()
    for section in overlay.sections():
        for key, value in overlay[section].items():
            pat = re.compile(rf"^\s*{re.escape(key)}\s*=", re.I)
            # find the section, then the key inside it
            start = next((i for i, l in enumerate(lines) if l.strip().lower() == f"[{section.lower()}]"), None)
            if start is None:
                lines += ["", f"[{section}]", f"{key}={value}"]
                continue
            end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("[")), len(lines))
            hit = next((i for i in range(start + 1, end) if pat.match(lines[i])), None)
            if hit is None:
                lines.insert(end, f"{key}={value}")
            else:
                lines[hit] = f"{key}={value}"
    ini.write_text("\n".join(lines) + "\n", encoding="utf8")
    print(f"  merged {sum(len(overlay[s]) for s in overlay.sections())} keys into {ini.name}")


def set_override(value: str) -> None:
    text = LAUNCHER_INI.read_text(encoding="utf8")
    new = re.sub(r"(?m)^WineDLLOverrides=.*$", f"WineDLLOverrides={value}", text)
    if new == text and "WineDLLOverrides=" not in text:
        new = text.rstrip("\n") + f"\nWineDLLOverrides={value}\n"
    LAUNCHER_INI.write_text(new, encoding="utf8")
    print(f"  launcher.ini: WineDLLOverrides={value!r}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dlls", action="store_true", help="also swap the game's nvngx_dlss.dll to the newest archive (310.9.0)")
    ap.add_argument("--undo", action="store_true")
    ap.add_argument("--optiscaler-zip", help="use a specific OptiScaler archive instead of the tool's default")
    a = ap.parse_args()
    if procs := running():
        print("close these first:\n  " + "\n  ".join(p[:100] for p in procs)); return 1
    g = game_dir(); exe = g / "ffxiv_dx11.exe"
    if not exe.is_file():
        sys.exit(f"{exe} not found")
    if not (PREFIX / "drive_c").is_dir():
        sys.exit(f"prefix {PREFIX} has no drive_c -- launch the game vanilla once first")
    print(f"game: {g}\nprefix: {PREFIX}")
    if a.undo:
        tool("dlls", str(exe), "--prefix", str(PREFIX), "restore", "-y")
        tool("restore", str(exe), "--prefix", str(PREFIX), "-y")
        set_override("")
        return 0
    extra = ["--optiscaler-zip", a.optiscaler_zip] if a.optiscaler_zip else []
    tool("install", str(exe), "--prefix", str(PREFIX), "--loader", "winmm.dll", "-y", *extra)
    merge_keys(g / "OptiScaler.ini")
    if a.dlls:
        tool("dlls", str(exe), "--prefix", str(PREFIX), "install", "-y")
    set_override("winmm=n,b;d3dcompiler_47=n;d3d12=n,b;d3d12core=n,b")
    log = g / "OptiScaler.log"
    if log.is_file():
        log.rename(g / "OptiScaler.log.previous")
    print("\nIn-game before testing: Graphics > DLSS on, Borderless window, Frame Rate Threshold = Always enabled.\n"
          "Do not change resolution/display mode/AO in-game with DLSS w/Dx12 active (crashes). Insert opens the overlay;\n"
          "open it only after your character is in the world.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

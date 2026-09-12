"""What Linux recorded when the game closed: Proton's log and coredumpctl.

Upstream 1.8.0 reads the Windows Application Error event for the game's
executable (core/wincrash.py) and settles "whose crash is it" from the
faulting module. There is no event log here, but two things record the same
fact:

* Proton's own log, ~/steam-<appid>.log, written when PROTON_LOG=1 is in the
  launch options (the tool suggests it next to the indicator). Wine prints
  "Unhandled exception: <kind> in <bits>-bit code" and a backtrace whose
  first frame names the module: `=>0 0x... EntryPoint+0x... in reshade64`.
* systemd's coredumpctl, when the crash killed the wine process outright:
  the executable is wine's preloader, the signal is the code, no module.

Both are read only for events after the install, the rule every log reader
in the diagnosis follows. The result is core.wincrash.Crash, so upstream's
ownership rules (Crash.ours / ambiguous) apply unchanged; only the sentences
differ, because "Windows recorded" would be a lie here.
"""
from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from core.wincrash import Crash

EXCEPTION = re.compile(r"Unhandled exception: (.+?) in (\d+)-bit code \(0x([0-9a-fA-F]+)\)")
FRAME0 = re.compile(r"=>0\s+0x[0-9a-fA-F]+\s+(?:\S+\s+)?in\s+([\w.\-+]+)", re.IGNORECASE)
PROTON_LOG = "steam-{appid}.log"


def parse_proton_log(text: str) -> tuple[str, str, str] | None:
    """(exception kind, faulting module, address) of the LAST fault in a Proton log."""
    hits = list(EXCEPTION.finditer(text))
    if not hits:
        return None
    m = hits[-1]
    tail = text[m.end():m.end() + 20_000]
    f = FRAME0.search(tail)
    module = f.group(1).lower() if f else ""
    if module.endswith(".so"):
        module = module.rsplit("/", 1)[-1]
    return m.group(1).strip(), module, m.group(3)


def proton_logs(home: Path, since: float) -> list[Path]:
    """Proton logs written after `since`, newest first."""
    try:
        logs = [p for p in home.glob("steam-*.log") if p.stat().st_mtime > since]
    except OSError:
        return []
    return sorted(logs, key=lambda p: p.stat().st_mtime, reverse=True)


def from_proton_log(exe_name: str, since: float = 0.0, home: Path | None = None) -> Crash | None:
    home = home or Path.home()
    needle = exe_name.lower()
    for log in proton_logs(home, since):
        try:
            text = log.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if needle not in text.lower():
            continue
        parsed = parse_proton_log(text)
        if not parsed:
            continue
        kind, module, addr = parsed
        when = datetime.fromtimestamp(log.stat().st_mtime, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        return Crash(when=when, exe=exe_name, module=module or exe_name.lower(), code=f"{kind} at 0x{addr}",
                     provider=f"proton log ({log.name})")
    return None


def _coredumpctl_json() -> list[dict]:
    try:
        r = subprocess.run(["coredumpctl", "list", "--json=short", "--no-pager"],
                           capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return []
    if r.returncode != 0 or not r.stdout.strip():
        return []
    try:
        data = json.loads(r.stdout)
    except ValueError:
        return []
    return data if isinstance(data, list) else []


def from_coredumpctl(exe_name: str, since: float = 0.0, rows: list[dict] | None = None) -> Crash | None:
    rows = _coredumpctl_json() if rows is None else rows
    stem = Path(exe_name).stem.lower()[:15]      # COMM is the exe name cut to 15 chars
    best = None
    for row in rows:
        t = row.get("time") or 0
        secs = t / 1_000_000 if t > 10_000_000_000 else t
        if secs <= since:
            continue
        exe = str(row.get("exe") or "").lower()
        comm = str(row.get("comm") or "").lower()
        wine = exe.endswith(("wine-preloader", "wine64-preloader")) or "/wine" in exe
        if not (stem in exe or stem in comm or (wine and not comm)):
            continue
        if best is None or secs > best[0]:
            best = (secs, row)
    if best is None:
        return None
    secs, row = best
    sig = row.get("sig")
    when = datetime.fromtimestamp(secs, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    return Crash(when=when, exe=exe_name, module="", code=f"signal {sig}" if sig else "", provider="coredumpctl")


def last_crash(exe_name: str, since: float = 0.0, within_days: int = 14) -> Crash | None:
    """Replacement for core.wincrash.last_crash: Proton's log first, then coredumpctl."""
    exe = (exe_name or "").strip()
    if not exe:
        return None
    return from_proton_log(exe, since) or from_coredumpctl(exe, since)


def describe(c: Crash | None, proxy: str = "", written: tuple[str, ...] = ()) -> tuple[str, str] | None:
    """(title, detail) in Linux words; the ownership rules are upstream's."""
    if c is None:
        return None
    where = f"{c.when} UTC" if c.when else "an unrecorded time"
    src = c.provider or "the system"
    if not c.module:
        return (f"{src} recorded {c.exe} dying ({c.code or 'no signal recorded'}) at {where}.",
                "The wine process was killed before anything wrote a log, so the module is unknown. "
                "Add PROTON_LOG=1 to the launch options: Proton then writes ~/steam-<appid>.log "
                "with the faulting module, and this report can say whose crash it was.")
    if c.module == c.exe.lower():
        return (f"{src} recorded {c.exe} faulting in its own code ({c.code}).",
                f"The fault is inside the game, not in anything this tool loaded, at {where}. "
                "Uninstall and start the game once to tell the two apart.")
    if c.ambiguous(proxy):
        return (f"{src} recorded {c.exe} faulting in {c.module} ({c.code}).",
                f"That is the name this install writes beside the game ({proxy}) and also a Wine builtin; "
                f"the log names the module, not which copy, at {where}. Uninstall and start the game once.")
    if c.ours(written):
        return (f"{src} recorded {c.exe} faulting in {c.module} ({c.code}).",
                f"That module is part of this install, at {where}. Press 'report a bug' so the module, "
                "the exception and the route go in together.")
    return (f"{src} recorded {c.exe} faulting in {c.module} ({c.code}).",
            f"Neither the game nor anything this tool installs, at {where} - a Wine builtin, the driver, "
            "or another mod. If it persists, that module is the one to chase.")


def install() -> None:
    from core import wincrash as _wc
    _wc.last_crash = last_crash

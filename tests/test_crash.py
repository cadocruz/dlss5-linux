"""linuxport.crash: Proton's log and coredumpctl standing in for the Windows event log."""
from __future__ import annotations

import time

from core import wincrash
from linuxport import crash

PROTON_LOG = """\
======================
Proton: 1725900000 proton-cachyos-11
SteamGameId: 123
Command: ['/games/Fixture Game/Binaries/Win64/Fixture-Win64-Shipping.exe']
======================
0104:fixme:ver:GetCurrentPackageId
wine: Unhandled page fault on read access to 0000000000000010 at address 00006FFFFE1234 (thread 0104), starting debugger...
Unhandled exception: page fault on read access to 0x0000000000000010 in 64-bit code (0x00006ffffe1234).
Register dump:
 rip:00006ffffe1234 rsp:000000000011f000
Backtrace:
=>0 0x00006ffffe1234 EntryPoint+0x1234() in reshade64 (0x000000000011f000)
  1 0x00006ffffe5678 in dxgi (+0x5678) (0x000000000011f100)
  2 0x0000000140001000 in fixture-win64-shipping (+0x1000)
"""


def test_parse_proton_log_takes_the_last_fault_and_its_first_frame():
    kind, module, addr = crash.parse_proton_log(PROTON_LOG)
    assert kind == "page fault on read access to 0x0000000000000010"
    assert module == "reshade64" and addr == "00006ffffe1234"
    assert crash.parse_proton_log("nothing here") is None
    two = PROTON_LOG + PROTON_LOG.replace("in reshade64", "in ntdll").replace("0x00006ffffe1234", "0x0000000000000001")
    assert crash.parse_proton_log(two)[1] == "ntdll"


def test_from_proton_log_only_after_the_install_and_only_this_game(fake_home):
    log = fake_home / "steam-123.log"
    log.write_text(PROTON_LOG, encoding="utf-8")
    assert crash.from_proton_log("Fixture-Win64-Shipping.exe", since=time.time() + 60) is None   # older than the install
    c = crash.from_proton_log("Fixture-Win64-Shipping.exe", since=0)
    assert c is not None and c.module == "reshade64" and c.provider.startswith("proton log")
    assert c.code.startswith("page fault") and "0x00006ffffe1234" in c.code
    assert crash.from_proton_log("Other.exe", since=0) is None
    other = fake_home / "steam-456.log"
    other.write_text("Command: ['/g/Other.exe']\nno fault\n", encoding="utf-8")
    assert crash.from_proton_log("Other.exe", since=0) is None


def test_from_coredumpctl_matches_the_comm_or_wine_preloader():
    now = int(time.time())
    rows = [
        {"time": (now - 100) * 1_000_000, "pid": 1, "sig": 11, "exe": "/usr/bin/wine64-preloader"},
        {"time": (now - 50) * 1_000_000, "pid": 2, "sig": 6, "exe": "/usr/bin/firefox", "comm": "firefox"},
        {"time": (now - 10) * 1_000_000, "pid": 3, "sig": 11, "exe": "/usr/bin/wine64-preloader",
         "comm": "Fixture-Win64-S"},
    ]
    c = crash.from_coredumpctl("Fixture-Win64-Shipping.exe", since=now - 1000, rows=rows)
    assert c is not None and c.code == "signal 11" and c.module == "" and c.provider == "coredumpctl"
    assert crash.from_coredumpctl("Fixture-Win64-Shipping.exe", since=now - 5, rows=rows) is None
    assert crash.from_coredumpctl("Other.exe", since=now - 1000, rows=rows).code == "signal 11"   # anonymous preloader
    assert crash.from_coredumpctl("x.exe", since=0, rows=[]) is None


def test_last_crash_is_the_wincrash_replacement(fake_home, monkeypatch):
    monkeypatch.setattr(crash, "_coredumpctl_json", lambda: [])
    assert wincrash.last_crash is crash.last_crash
    assert wincrash.last_crash("Fixture-Win64-Shipping.exe") is None
    (fake_home / "steam-123.log").write_text(PROTON_LOG, encoding="utf-8")
    assert wincrash.last_crash("Fixture-Win64-Shipping.exe").module == "reshade64"
    assert wincrash.last_crash("") is None


def test_describe_uses_upstreams_ownership_rules_with_linux_words():
    c = wincrash.Crash(when="2026-09-10T20:00:00", exe="Game.exe", module="reshade64", code="page fault", provider="proton log")
    title, detail = crash.describe(c, "dxgi.dll", ("dxgi.dll",))
    assert "proton log recorded Game.exe faulting in reshade64" in title and "report a bug" in detail
    own = wincrash.Crash("", "Game.exe", "game.exe", "page fault", "proton log")
    assert "its own code" in crash.describe(own)[0]
    amb = wincrash.Crash("", "Game.exe", "dxgi", "page fault", "proton log")
    assert "also a Wine builtin" in crash.describe(amb, "dxgi")[1]
    foreign = wincrash.Crash("", "Game.exe", "ntdll", "page fault", "proton log")
    assert "Neither the game" in crash.describe(foreign, "dxgi.dll")[1]
    unknown = wincrash.Crash("", "Game.exe", "", "signal 11", "coredumpctl")
    assert "PROTON_LOG=1" in crash.describe(unknown)[1]
    assert crash.describe(None) is None

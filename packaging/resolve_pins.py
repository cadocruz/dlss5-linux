#!/usr/bin/env python3
"""Resolve the two binaries the AppImage carries, and lock them.

The build fetches a CPython from python-build-standalone and a static 7-Zip.
Both are pinned by sha256 in packaging/pins.lock, and the lock is authority:
if a download does not match it, the build stops. Nothing here rewrites a
recorded hash - upstream re-vendors several times a day and the packaging is
meant to be the part that does not move.

The lock is written on the first run, with the hashes of what was actually
downloaded, and is meant to be committed.

    python3 packaging/resolve_pins.py --print      what the lock says
    python3 packaging/resolve_pins.py --fetch DIR  download, verify, report paths
    python3 packaging/resolve_pins.py --relock     re-resolve and rewrite (deliberate)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOCK = HERE / "pins.lock"

# CPython to carry. 3.12 because that is what the suite runs on; the README
# floor of 3.11 is a floor, not a target.
PYTHON_SERIES = "3.12"
PBS_REPO = "astral-sh/python-build-standalone"
# install_only, not install_only_stripped: stripped drops the symbols a
# traceback needs, and a bug report with no traceback is the thing this
# project spends most of its effort avoiding.
PBS_PATTERN = re.compile(
    rf"^cpython-{re.escape(PYTHON_SERIES)}\.(\d+)\+\d+-x86_64-unknown-linux-gnu-install_only\.tar\.gz$")

# 7-Zip's own static Linux build. SteamOS and the other read-only roots ship no
# 7z, and the OptiScaler nightlies are .7z.
SEVENZIP_URL = "https://github.com/ip7z/7zip/releases/download/26.03/7z2603-linux-x64.tar.xz"


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "dlss5-linux-packaging"})
    with urllib.request.urlopen(req, timeout=180) as r:
        return r.read()


def newest_pbs() -> tuple[str, str]:
    """(asset name, download url) for the newest 3.12 linux build."""
    rel = json.loads(_get(f"https://api.github.com/repos/{PBS_REPO}/releases/latest"))
    best: tuple[int, str, str] | None = None
    for a in rel.get("assets", []):
        m = PBS_PATTERN.match(a["name"])
        if m and (best is None or int(m.group(1)) > best[0]):
            best = (int(m.group(1)), a["name"], a["browser_download_url"])
    if best is None:
        raise SystemExit(f"no {PYTHON_SERIES} linux install_only asset in {rel.get('tag_name')}")
    return best[1], best[2]


def load() -> dict:
    return json.loads(LOCK.read_text(encoding="utf8")) if LOCK.is_file() else {}


def fetch(name: str, url: str, into: Path, lock: dict, relock: bool) -> Path:
    into.mkdir(parents=True, exist_ok=True)
    dst = into / Path(url).name
    if not dst.is_file():
        dst.write_bytes(_get(url))
    got = hashlib.sha256(dst.read_bytes()).hexdigest()
    want = (lock.get(name) or {}).get("sha256")
    if want and got != want and not relock:
        raise SystemExit(
            f"{name}: sha256 mismatch\n"
            f"  locked   {want}\n"
            f"  download {got}\n"
            f"  url      {url}\n"
            f"Refusing. If the change is intended, run with --relock and commit "
            f"the new pins.lock as its own change, so it is reviewed on its own.")
    lock[name] = {"url": url, "sha256": got}
    return dst


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fetch", metavar="DIR")
    ap.add_argument("--print", action="store_true", dest="show")
    ap.add_argument("--relock", action="store_true")
    args = ap.parse_args()

    lock = load()
    if args.show:
        if not lock:
            print("no pins.lock yet - the first build writes one")
            return 1
        for k, v in sorted(lock.items()):
            print(f"{k}\n  {v['url']}\n  sha256 {v['sha256']}")
        return 0

    if not args.fetch:
        ap.error("one of --fetch or --print")

    into = Path(args.fetch)
    if "python" in lock and not args.relock:
        py_url = lock["python"]["url"]
    else:
        _, py_url = newest_pbs()
    py = fetch("python", py_url, into, lock, args.relock)
    sz = fetch("7zip", SEVENZIP_URL, into, lock, args.relock)

    before = LOCK.read_text(encoding="utf8") if LOCK.is_file() else ""
    text = json.dumps(lock, indent=1, sort_keys=True) + "\n"
    if text != before:
        LOCK.write_text(text, encoding="utf8")
        print(f"pins.lock {'rewritten' if before else 'written'} - commit it", file=sys.stderr)
    print(py)
    print(sz)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Re-vendor core/ from Kizzuwatnaa/DLSS5-Autopilot, safely.

The upstream author ships several times a day. core/ here is a byte-for-byte
copy of one upstream commit (recorded in UPSTREAM), and every Linux change
is a monkey-patch in linuxport/. Pulling a new core is therefore mechanical,
but two things must hold before the copy lands:

  1. every core symbol linuxport touches still exists (tools/check_shims.py);
  2. the drift is visible: which modules changed, appeared or vanished.

    tools/sync_upstream.py                    check the newest tag, change nothing
    tools/sync_upstream.py --tag v1.8.0       check one tag
    tools/sync_upstream.py --from ../DLSS5-Autopilot   use a local clone instead of fetching
    tools/sync_upstream.py --apply            replace core/ and rewrite UPSTREAM
    tools/sync_upstream.py --apply --force    apply even with missing symbols

Only git is used for the fetch (a shallow clone into the XDG cache). Exit 0
on success, 2 when symbols are missing and --force was not given.
"""
from __future__ import annotations

import argparse
import filecmp
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_shims  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "dlss5-linux"
CORE = ENGINE / "core"
UPSTREAM_FILE = ENGINE / "UPSTREAM"
REPO = "Kizzuwatnaa/DLSS5-Autopilot"
REMOTE = f"https://github.com/{REPO}.git"
CACHE = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "dlss5-linux" / "upstream"


def git(*args: str, cwd: Path | None = None) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                          text=True, encoding="utf-8", errors="replace").stdout.strip()


def current_pin() -> str | None:
    """The short commit recorded in UPSTREAM, if any."""
    try:
        m = re.search(r"^commit\s+([0-9a-f]{7,40})", UPSTREAM_FILE.read_text(encoding="utf-8"), re.M)
    except OSError:
        return None
    return m.group(1) if m else None


def newest_tag() -> str:
    out = git("ls-remote", "--tags", "--refs", REMOTE)
    tags = [line.split("refs/tags/")[1] for line in out.splitlines() if "refs/tags/" in line]

    def key(t: str) -> tuple:
        return tuple(int(n) for n in re.findall(r"\d+", t)) or (0,)
    if not tags:
        sys.exit("no tags found upstream")
    return max(tags, key=key)


def clone(tag: str) -> Path:
    """Shallow clone of one tag into the cache; reused when already there."""
    dest = CACHE / tag
    if (dest / ".git").is_dir():
        return dest
    CACHE.mkdir(parents=True, exist_ok=True)
    print(f"fetching {REPO} {tag} ...")
    git("clone", "--quiet", "--depth", "1", "--branch", tag, REMOTE, str(dest))
    return dest


def describe(repo: Path, ref: str) -> tuple[str, str, str]:
    """(short hash, ISO date, version from core/update.py) at ref."""
    short = git("rev-parse", "--short", ref, cwd=repo)
    day = git("log", "-1", "--format=%as", ref, cwd=repo)
    update_py = git("show", f"{ref}:core/update.py", cwd=repo)
    m = re.search(r'^VERSION\s*=\s*"([^"]+)"', update_py, re.M)
    return short, day, m.group(1) if m else "?"


def export_core(repo: Path, ref: str, into: Path) -> Path:
    """core/ at ref, extracted with git archive so the working tree is irrelevant."""
    into.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(["git", "archive", ref, "core"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["tar", "-x", "-C", str(into)], input=proc.stdout, check=True)
    return into / "core"


def drift(old: Path, new: Path) -> tuple[list[str], list[str], list[str]]:
    """(changed, added, removed) module names, ignoring caches."""
    def names(d: Path) -> set[str]:
        return {p.name for p in d.glob("*.py")}
    o, n = names(old), names(new)
    changed = sorted(f for f in o & n if not filecmp.cmp(old / f, new / f, shallow=False))
    return changed, sorted(n - o), sorted(o - n)


def line_delta(old: Path, new: Path, name: str) -> str:
    try:
        a = len((old / name).read_text(encoding="utf-8", errors="replace").splitlines())
        b = len((new / name).read_text(encoding="utf-8", errors="replace").splitlines())
    except OSError:
        return ""
    return f"{b - a:+d}"


def write_upstream(short: str, tag: str, day: str, previous: str | None, prev_version: str | None) -> None:
    lines = [
        REPO,
        f"commit {short} ({tag}, {day})",
        "license MIT (see LICENSE.upstream)",
        "core/ vendored unmodified; all Linux changes live in linuxport/",
        f"synced {date.today().isoformat()} by tools/sync_upstream.py",
    ]
    if previous:
        lines.append(f"previous: {previous}" + (f" ({prev_version})" if prev_version else ""))
    UPSTREAM_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def previous_version_label() -> str | None:
    try:
        m = re.search(r"^commit\s+\S+\s+\(([^,)]+)", UPSTREAM_FILE.read_text(encoding="utf-8"), re.M)
    except OSError:
        return None
    return m.group(1) if m else None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tag", help="upstream tag or ref (default: newest tag)")
    ap.add_argument("--from", dest="local", type=Path, help="local clone of the upstream repo")
    ap.add_argument("--apply", action="store_true", help="replace core/ and rewrite UPSTREAM")
    ap.add_argument("--force", action="store_true", help="apply even when shim symbols are missing")
    args = ap.parse_args()

    if args.local:
        repo = args.local.resolve()
        if not (repo / ".git").exists():
            sys.exit(f"not a git repository: {repo}")
        ref = args.tag or git("describe", "--tags", "--abbrev=0", cwd=repo)
    else:
        ref = args.tag or newest_tag()
        repo = clone(ref)

    short, day, version = describe(repo, ref)
    pin = current_pin()
    print(f"upstream : {REPO} {ref} = {short} ({day}), core VERSION {version}")
    print(f"pinned   : {pin or '(none)'} {previous_version_label() or ''}".rstrip())
    same_commit = bool(pin) and (short.startswith(pin[:7]) or pin.startswith(short))
    if same_commit:
        print("core/ is already at this commit; nothing to do")
        return 0

    with tempfile.TemporaryDirectory(prefix="dlss5-core-") as tmp:
        candidate = export_core(repo, ref, Path(tmp))
        changed, added, removed = drift(CORE, candidate)

        print(f"\ndrift    : {len(changed)} changed, {len(added)} new, {len(removed)} removed")
        for f in changed:
            print(f"  ~ {f:<18} {line_delta(CORE, candidate, f)} lines")
        for f in added:
            print(f"  + {f}")
        for f in removed:
            print(f"  - {f}")

        print()
        res = check_shims.check(candidate)
        check_shims.report(res, candidate)
        hard_missing = [m for m in res.missing if not m.optional]

        if added:
            print("\nnew modules -- check each for Windows-only imports (tools/check_shims.py --windows):")
            for f in added:
                top, inner = check_shims.windows_imports(candidate / f)
                print(f"  {f:<18} {check_shims.portability(candidate / f):<13} {', '.join(top + inner)}")

        if not args.apply:
            print("\ndry run; pass --apply to replace core/")
            return 2 if hard_missing else 0

        if hard_missing and not args.force:
            print("\nNOT applied: shim symbols missing. Fix linuxport/ first or pass --force.")
            return 2

        backup = CORE.with_name(f"core.prev-{pin or 'unknown'}")
        if backup.exists():
            shutil.rmtree(backup)
        CORE.rename(backup)
        shutil.copytree(candidate, CORE)
        shutil.rmtree(backup)
        write_upstream(short, ref, day, pin, previous_version_label())
        print(f"\napplied: core/ is now {short} ({ref}); UPSTREAM rewritten")
        print("next   : make check   (shim symbols + headless smoke), then the five-game scan in PORT_PLAN.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())

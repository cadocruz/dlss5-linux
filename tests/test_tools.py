"""tools/: the phase-0 sync tooling, against synthetic trees and a tiny git repo."""
from __future__ import annotations

import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

import check_shims
import sync_upstream


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text), encoding="utf-8")
    return path


@pytest.fixture
def core(tmp_path) -> Path:
    write(tmp_path / "core" / "foo.py", """
        import os
        from pathlib import Path as P
        BAZ = 1
        A, B = 1, 2
        def bar(): pass
        class Thing: pass
        try:
            import winreg
            HAVE_REG = True
        except ImportError:
            HAVE_REG = False
        if os.name == "nt":
            def only_nt(): pass
        """)
    write(tmp_path / "core" / "empty.py", "")
    return tmp_path / "core"


# --- check_shims: symbol resolution --------------------------------------------------------
def test_top_level_names_cover_defs_assignments_imports_and_guarded_blocks(core):
    names = check_shims.top_level_names(core / "foo.py")
    assert {"os", "P", "BAZ", "A", "B", "bar", "Thing", "HAVE_REG", "only_nt"} <= names
    assert "winreg" not in names          # only the top-level import list, not nested ones


def test_check_finds_missing_symbols_and_hasattr_guards(core, tmp_path):
    shim = write(tmp_path / "shim.py", """
        from core import foo as _f, nothere as _n
        from core.foo import bar, gone
        _orig = _f.bar
        _f.BAZ = 2
        _f.missing = lambda: None
        if hasattr(_f, "optional_thing"):
            _f.optional_thing = 1
        _n.anything
        """)
    res = check_shims.check(core, [shim])
    assert res.checked_modules == {"foo", "nothere"}
    hard = {(m.module, m.name) for m in res.missing if not m.optional}
    soft = {(m.module, m.name) for m in res.missing if m.optional}
    assert hard == {("foo", "gone"), ("foo", "missing"), ("nothere", "anything")}
    assert soft == {("foo", "optional_thing")}
    resolved = {(u.module, u.name) for u in res.uses} - hard - soft
    assert {("foo", "bar"), ("foo", "BAZ")} <= resolved


def test_check_reports_each_use_once_with_a_location(core, tmp_path):
    shim = write(tmp_path / "shim.py", "from core import foo\nfoo.bar()\nfoo.bar()\nfoo.nope\n")
    res = check_shims.check(core, [shim])
    assert [u.name for u in res.uses] == ["bar", "nope"]
    assert res.missing[0].where.endswith("shim.py:4")


def test_check_ignores_files_that_do_not_exist(core, tmp_path):
    assert check_shims.check(core, [tmp_path / "absent.py"]).uses == []


def test_main_exit_codes(core, tmp_path, monkeypatch, capsys):
    good = write(tmp_path / "good.py", "from core import foo\nfoo.bar\n")
    bad = write(tmp_path / "bad.py", "from core import foo\nfoo.nope\n")
    monkeypatch.setattr(check_shims, "LINUX_FILES", [good])
    monkeypatch.setattr("sys.argv", ["check_shims", "--core", str(core)])
    assert check_shims.main() == 0
    assert "all symbols resolve" in capsys.readouterr().out
    monkeypatch.setattr(check_shims, "LINUX_FILES", [good, bad])
    assert check_shims.main() == 1
    assert "MISSING  core.foo.nope" in capsys.readouterr().out


# --- check_shims: portability -----------------------------------------------------------------
def test_portability_classes(tmp_path):
    cases = {
        "windows-only": "import winreg\n",
        "tk-ui": "import tkinter as tk\n",
        "tk-ui-inner": "def f():\n    from tkinter import ttk\n",
        "needs-shim-import": "def f():\n    import winreg\n",
        "needs-shim-string": 'CMD = ["powershell", "-c", "x"]\n',
        "needs-shim-windll": "import ctypes\nK = ctypes.windll.kernel32\n",
        "needs-shim-env": 'import os\nP = os.environ.get("LOCALAPPDATA")\n',
        "portable": "import os\nimport subprocess\n",
    }
    got = {}
    for name, src in cases.items():
        got[name] = check_shims.portability(write(tmp_path / f"{name}.py", src))
    assert got == {
        "windows-only": "windows-only", "tk-ui": "tk-ui", "tk-ui-inner": "tk-ui",
        "needs-shim-import": "needs-shim", "needs-shim-string": "needs-shim",
        "needs-shim-windll": "needs-shim", "needs-shim-env": "needs-shim", "portable": "portable",
    }


def test_windows_imports_detail(tmp_path):
    p = write(tmp_path / "m.py", 'import winreg\ndef f():\n    import msvcrt\n    run("reg.exe")\n')
    top, inner = check_shims.windows_imports(p)
    assert top == ["winreg"] and inner == ['"reg.exe"', "msvcrt"]


# --- sync_upstream: drift and UPSTREAM file ----------------------------------------------------
def test_drift_and_line_delta(tmp_path):
    old, new = tmp_path / "old", tmp_path / "new"
    write(old / "same.py", "x = 1\n")
    write(new / "same.py", "x = 1\n")
    write(old / "changed.py", "a = 1\n")
    write(new / "changed.py", "a = 1\nb = 2\nc = 3\n")
    write(old / "removed.py", "")
    write(new / "added.py", "")
    (old / "__pycache__").mkdir()
    assert sync_upstream.drift(old, new) == (["changed.py"], ["added.py"], ["removed.py"])
    assert sync_upstream.line_delta(old, new, "changed.py") == "+2"
    assert sync_upstream.line_delta(old, new, "absent.py") == ""


def test_upstream_file_round_trip(tmp_path, monkeypatch):
    f = tmp_path / "UPSTREAM"
    monkeypatch.setattr(sync_upstream, "UPSTREAM_FILE", f)
    assert sync_upstream.current_pin() is None
    sync_upstream.write_upstream("99377ba", "v1.8.0", "2026-09-10", "8225fff", "v1.7.1")
    text = f.read_text(encoding="utf-8")
    assert text.startswith("Kizzuwatnaa/DLSS5-Autopilot\ncommit 99377ba (v1.8.0, 2026-09-10)\n")
    assert "core/ vendored unmodified" in text and "previous: 8225fff (v1.7.1)" in text
    assert sync_upstream.current_pin() == "99377ba"
    assert sync_upstream.previous_version_label() == "v1.8.0"


# --- sync_upstream: git-backed pieces ----------------------------------------------------------------
needs_git = pytest.mark.skipif(shutil.which("git") is None or shutil.which("tar") is None,
                               reason="git and tar are required")


@pytest.fixture
def upstream_repo(tmp_path) -> Path:
    repo = tmp_path / "upstream"
    write(repo / "core" / "update.py", 'VERSION = "1.2.3"\n')
    write(repo / "core" / "gpu.py", "def detect(): return None, None\n")
    write(repo / "README.md", "hi\n")
    env = ["-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"]
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", *env, "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", *env, "-C", str(repo), "commit", "-q", "-m", "first"], check=True)
    subprocess.run(["git", "-C", str(repo), "tag", "v1.2.3"], check=True)
    return repo


@needs_git
def test_describe_reads_hash_date_and_version(upstream_repo):
    short, day, version = sync_upstream.describe(upstream_repo, "v1.2.3")
    assert len(short) >= 7 and len(day) == 10 and version == "1.2.3"


@needs_git
def test_export_core_extracts_only_core(upstream_repo, tmp_path):
    out = sync_upstream.export_core(upstream_repo, "v1.2.3", tmp_path / "x")
    assert sorted(p.name for p in out.iterdir()) == ["gpu.py", "update.py"]
    assert not (tmp_path / "x" / "README.md").exists()

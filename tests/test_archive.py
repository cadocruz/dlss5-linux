"""linuxport.archive: the .7z fallback that does not reach for C:\\Windows."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from core import optiscaler
from linuxport import archive


@pytest.fixture
def nothing_installed(monkeypatch):
    monkeypatch.setattr(optiscaler, "_seven_zip", lambda: None)
    monkeypatch.setattr(archive.shutil, "which", lambda name: None)


def test_the_shim_is_the_one_core_calls():
    assert optiscaler.extract_7z is archive.extract_7z


def test_seven_zip_comes_first_then_bsdtar_then_tar(tmp_path, monkeypatch):
    monkeypatch.setattr(optiscaler, "_seven_zip", lambda: Path("/usr/bin/7z"))
    monkeypatch.setattr(archive.shutil, "which",
                        lambda name: {"bsdtar": "/usr/bin/bsdtar", "tar": "/bin/tar"}.get(name))
    cmds = archive.commands(tmp_path / "a.7z", tmp_path / "out")
    assert [Path(c[0]).name for c in cmds] == ["7z", "bsdtar", "tar"]
    assert cmds[0][1] == "x" and cmds[1][1:3] == ["-xf", str(tmp_path / "a.7z")]


def test_with_no_extractor_the_message_names_linux_packages(tmp_path, nothing_installed):
    archive_path = tmp_path / "OptiScaler.7z"
    archive_path.write_bytes(b"7z\xbc\xaf")
    with pytest.raises(RuntimeError) as e:
        archive.extract_7z(archive_path, tmp_path / "out")
    said = str(e.value)
    assert "p7zip" in said and "bsdtar" in said
    assert "tar.exe" not in said and "Windows" not in said


def test_the_first_extractor_that_works_wins(tmp_path, monkeypatch):
    dest = tmp_path / "out"
    calls: list[str] = []

    def run(cmd, capture_output=False, text=False):
        calls.append(Path(cmd[0]).name)
        ok = Path(cmd[0]).name == "bsdtar"
        return subprocess.CompletedProcess(cmd, 0 if ok else 2, "", "not an archive")

    monkeypatch.setattr(optiscaler, "_seven_zip", lambda: Path("/usr/bin/7z"))
    monkeypatch.setattr(archive.shutil, "which",
                        lambda name: {"bsdtar": "/usr/bin/bsdtar", "tar": "/bin/tar"}.get(name))
    monkeypatch.setattr(archive.subprocess, "run", run)
    archive.extract_7z(tmp_path / "a.7z", dest)
    assert calls == ["7z", "bsdtar"], "kept going after one that worked"
    assert dest.is_dir()


def test_every_failure_is_reported_together(tmp_path, monkeypatch):
    def run(cmd, capture_output=False, text=False):
        return subprocess.CompletedProcess(cmd, 2, "", f"{Path(cmd[0]).name} said no")

    monkeypatch.setattr(optiscaler, "_seven_zip", lambda: None)
    monkeypatch.setattr(archive.shutil, "which",
                        lambda name: {"bsdtar": "/usr/bin/bsdtar", "tar": "/bin/tar"}.get(name))
    monkeypatch.setattr(archive.subprocess, "run", run)
    with pytest.raises(RuntimeError) as e:
        archive.extract_7z(tmp_path / "a.7z", tmp_path / "out")
    said = str(e.value)
    assert "bsdtar said no" in said and "tar said no" in said


def test_an_extractor_that_cannot_be_started_is_not_the_end(tmp_path, monkeypatch):
    def run(cmd, capture_output=False, text=False):
        if Path(cmd[0]).name == "7z":
            raise OSError("Exec format error")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(optiscaler, "_seven_zip", lambda: Path("/usr/bin/7z"))
    monkeypatch.setattr(archive.shutil, "which", lambda name: "/usr/bin/bsdtar" if name == "bsdtar" else None)
    monkeypatch.setattr(archive.subprocess, "run", run)
    archive.extract_7z(tmp_path / "a.7z", tmp_path / "out")      # bsdtar picked it up

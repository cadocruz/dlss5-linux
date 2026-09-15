"""verify._runtime_rows: is the runtime on disk still the one the install wrote?

Only possible off Windows because linuxport.pe.file_version reads the version
out of the file. See tests/test_pe_version.py for what backs that reader.
"""
from __future__ import annotations

from pathlib import Path

from core import installer as inst
from linuxport import verify

from test_pe_version import rsrc_with, version_words, write_pe


def runtime(root: Path, name: str, parts: tuple[int, int, int, int]) -> Path:
    ms, ls = version_words(*parts)
    return write_pe(root / name, [(b".rsrc", rsrc_with(ms, ls))])


def test_a_runtime_that_still_matches_the_record_is_ok(tmp_path):
    runtime(tmp_path, inst.DLSS, (310, 9, 1, 0))
    rows = verify._runtime_rows(tmp_path, {"components": {"dlss": "310.9.1 (NVIDIA SDK)"}})
    assert rows == [("OK", "dlss runtime", "310.9.1 on disk, as recorded")]


def test_a_runtime_put_back_by_a_launcher_is_a_warning(tmp_path):
    """The case the row exists for: the file check restored the game's own copy."""
    runtime(tmp_path, inst.DLSS, (310, 2, 1, 0))
    rows = verify._runtime_rows(tmp_path, {"components": {"dlss": "310.9.1 (NVIDIA SDK)"}})
    assert len(rows) == 1
    level, title, detail = rows[0]
    assert level == "WARN" and title == "dlss runtime"
    assert "310.2.1 on disk" in detail and "310.9.1 (NVIDIA SDK)" in detail


def test_the_label_and_the_file_are_compared_as_numbers(tmp_path):
    """"310.9.1 (NVIDIA SDK)" against "310.9.1.0" is not a change. Comparing the
    strings printed an arrow between a build and itself - installer._ver exists
    for that, and this uses the same one."""
    runtime(tmp_path, inst.DLSS, (310, 9, 1, 0))
    rows = verify._runtime_rows(tmp_path, {"components": {"dlss": "310.9.1.0 (NVIDIA SDK)"}})
    assert rows[0][0] == "OK"


def test_all_three_runtimes_are_read(tmp_path):
    runtime(tmp_path, inst.DLSS, (310, 9, 1, 0))
    runtime(tmp_path, inst.DLSSD, (310, 9, 1, 0))
    runtime(tmp_path, inst.DLSSG, (310, 9, 0, 0))
    rows = verify._runtime_rows(tmp_path, {"components": {
        "dlss": "310.9.1 (NVIDIA SDK)",
        "dlssd": "310.9.1 (NVIDIA SDK)",
        "dlssg": "310.9.1 (NVIDIA SDK)",
    }})
    assert [r[1] for r in rows] == ["dlss runtime", "ray reconstruction", "frame generation"]
    assert [r[0] for r in rows] == ["OK", "OK", "WARN"]


# --- what must stay quiet -----------------------------------------------------------------
def test_nothing_is_said_about_a_runtime_the_record_does_not_mention(tmp_path):
    """A route that never swapped one has no opinion about the file beside it."""
    runtime(tmp_path, inst.DLSS, (310, 9, 1, 0))
    assert verify._runtime_rows(tmp_path, {"components": {}}) == []
    assert verify._runtime_rows(tmp_path, {}) == []


def test_nothing_is_said_when_the_file_is_gone(tmp_path):
    assert verify._runtime_rows(tmp_path, {"components": {"dlss": "310.9.1 (NVIDIA SDK)"}}) == []


def test_a_file_with_no_version_resource_is_not_a_mismatch(tmp_path):
    """"" is not a different version, it is no answer - and saying "replaced"
    on no evidence is exactly the kind of guess this project keeps out."""
    write_pe(tmp_path / inst.DLSS, [(b".rsrc", bytes(256))])
    assert verify._runtime_rows(tmp_path, {"components": {"dlss": "310.9.1 (NVIDIA SDK)"}}) == []

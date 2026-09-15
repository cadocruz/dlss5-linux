"""linuxport.pe.file_version: the version stamped in a DLL, read without Windows.

The fixtures are synthesised, not copied from a real binary: a checked-in DLL
would be a component in the repository, which this project does not take.

Be clear about what they prove. They are a section table and a block dropped
inside .rsrc - enough to exercise a bounded scan, its edges and its refusals,
which is what the reader under test does. They are NOT a valid resource tree,
and Windows' own version API reads none of them: it wants RT_VERSION
directories and the VS_VERSIONINFO wrapper, which the scan deliberately does
not require.

What proves the reader agrees with the API is real binaries, and that was done
separately: on Windows, over 500 files from System32, core.pe.file_version
(which asks the API) and this one returned the same string for every one - 490
with a version, 10 with none, no disagreement.
"""
from __future__ import annotations

import struct
from pathlib import Path

from core import pe as core_pe
from linuxport import pe as lpe

FIXED_INFO_SIG = 0xFEEF04BD


def fixed_info(ms: int, ls: int) -> bytes:
    """A VS_FIXEDFILEINFO block: signature, struct version 1.0, then the two
    halves of the file version and the same again for the product version."""
    return struct.pack("<IIIIII", FIXED_INFO_SIG, 0x00010000, ms, ls, ms, ls)


def version_words(a: int, b: int, c: int, d: int) -> tuple[int, int]:
    return (a << 16) | b, (c << 16) | d


def write_pe(path: Path, sections: list[tuple[bytes, bytes]]) -> Path:
    """A PE with the given (section name, raw bytes) sections and nothing else.

    Real enough for a reader that walks the section table: MZ stub, e_lfanew,
    PE signature, a COFF header naming the section count and an optional header
    size, then the table and the raw data it points at.
    """
    opt_size = 240
    head = bytearray(0x40)
    head[0:2] = b"MZ"
    struct.pack_into("<I", head, 0x3C, 0x40)

    coff = struct.pack("<HHIIIHH", 0x8664, len(sections), 0, 0, 0, opt_size, 0x2022)
    table_at = 0x40 + 4 + 20 + opt_size
    data_at = table_at + 40 * len(sections)

    table, data, cursor = b"", b"", data_at
    for name, raw in sections:
        table += (name.ljust(8, bytes(1))
                  + struct.pack("<II", len(raw), cursor)      # VirtualSize, VirtualAddress
                  + struct.pack("<II", len(raw), cursor)      # SizeOfRawData, PointerToRawData
                  + bytes(16))
        data += raw
        cursor += len(raw)

    path.write_bytes(bytes(head) + b"PE" + bytes(2) + coff + bytes(opt_size) + table + data)
    return path


def rsrc_with(ms: int, ls: int) -> bytes:
    """A resource section with the block somewhere in the middle of it."""
    return bytes(64) + fixed_info(ms, ls) + bytes(128)


# --- the number itself --------------------------------------------------------------------
def test_it_reads_the_four_parts(tmp_path):
    ms, ls = version_words(310, 8, 0, 1)
    write_pe(tmp_path / "a.dll", [(b".rsrc", rsrc_with(ms, ls))])
    assert lpe.file_version(tmp_path / "a.dll") == "310.8.0.1"


def test_a_trailing_zero_is_dropped(tmp_path):
    """NVIDIA writes 310.8.0 as 310.8.0.0 and nobody calls it that."""
    ms, ls = version_words(310, 8, 0, 0)
    write_pe(tmp_path / "b.dll", [(b".rsrc", rsrc_with(ms, ls))])
    assert lpe.file_version(tmp_path / "b.dll") == "310.8.0"


def test_only_one_trailing_zero_goes(tmp_path):
    """Three parts is the floor: 310.0.0 is not 310."""
    ms, ls = version_words(310, 0, 0, 0)
    write_pe(tmp_path / "c.dll", [(b".rsrc", rsrc_with(ms, ls))])
    assert lpe.file_version(tmp_path / "c.dll") == "310.0.0"


def test_a_zero_in_the_middle_stays(tmp_path):
    ms, ls = version_words(2, 0, 9, 4)
    write_pe(tmp_path / "d.dll", [(b".rsrc", rsrc_with(ms, ls))])
    assert lpe.file_version(tmp_path / "d.dll") == "2.0.9.4"


# --- what must NOT answer -----------------------------------------------------------------
def test_the_search_stops_at_the_resource_section(tmp_path):
    """The same eight bytes in code or in an embedded file must not answer.
    Bounding the scan to .rsrc is the whole reason this is safe to do at all."""
    ms, ls = version_words(9, 9, 9, 9)
    write_pe(tmp_path / "e.dll", [(b".text", rsrc_with(ms, ls))])
    assert lpe.file_version(tmp_path / "e.dll") == ""


def test_the_right_section_still_answers_when_another_carries_the_bytes(tmp_path):
    decoy = version_words(9, 9, 9, 9)
    real = version_words(310, 9, 1, 0)
    write_pe(tmp_path / "f.dll", [(b".text", rsrc_with(*decoy)),
                                  (b".rsrc", rsrc_with(*real))])
    assert lpe.file_version(tmp_path / "f.dll") == "310.9.1"


def test_a_dll_with_no_version_resource_is_not_an_error(tmp_path):
    """Plenty carry none. "" is the answer, not an exception."""
    write_pe(tmp_path / "g.dll", [(b".rsrc", bytes(256))])
    assert lpe.file_version(tmp_path / "g.dll") == ""


def test_a_file_that_is_not_a_pe_is_not_an_error(tmp_path):
    (tmp_path / "h.dll").write_bytes(b"not an executable at all")
    assert lpe.file_version(tmp_path / "h.dll") == ""


def test_a_file_that_is_not_there_is_not_an_error(tmp_path):
    assert lpe.file_version(tmp_path / "missing.dll") == ""


def test_a_truncated_pe_is_not_an_error(tmp_path):
    full = write_pe(tmp_path / "i.dll", [(b".rsrc", rsrc_with(*version_words(1, 2, 3, 4)))])
    (tmp_path / "j.dll").write_bytes(full.read_bytes()[:0x50])
    assert lpe.file_version(tmp_path / "j.dll") == ""


def test_a_section_header_claiming_an_absurd_size_is_refused(tmp_path):
    """A malformed header must not turn into an arbitrary allocation."""
    path = write_pe(tmp_path / "k.dll", [(b".rsrc", rsrc_with(*version_words(1, 2, 3, 4)))])
    raw = bytearray(path.read_bytes())
    table_at = 0x40 + 4 + 20 + 240
    struct.pack_into("<I", raw, table_at + 16, lpe._RSRC_MAX + 1)
    path.write_bytes(bytes(raw))
    assert lpe.file_version(path) == ""


# --- the patch point ----------------------------------------------------------------------
def test_it_is_installed_over_the_windows_only_original():
    """core.pe.file_version returns "" on anything but Windows by design."""
    assert core_pe.file_version is lpe.file_version

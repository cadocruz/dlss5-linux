"""Widen core.pe's DX12 promotion to engine plugin folders.

Upstream only checks the exe's own folder for an Agility SDK or DLSS-G/RR.
Unreal keeps nvngx_dlssg.dll / nvngx_dlssd.dll under
Plugins/Runtime/Nvidia/**, so Stellar Blade came back "DX11". Under Proton
every DXGI game is vkd3d/dxvk anyway; what matters is the route list, and
native/upstream/optiscaler need the DX12 label.
"""
from __future__ import annotations

import struct
from pathlib import Path

from core import dlss, pe as _pe

_orig = _pe._has_d3d12_agility_sdk


# Directory names that say "still inside one game's tree". The walk-up stops
# the moment neither the current name nor the parent's children look like
# that, so a non-Steam layout such as Games/Publisher/Game/game/ never climbs
# into the publisher folder and finds ANOTHER game's DLSS-G (which is exactly
# how Final Fantasy XIV came back "DX12" on 2026-09-07).
_TREE = {"binaries", "engine", "content", "game", "boot", "bin", "win64", "x64", "retail"}


def _inside_game_tree(d: Path) -> bool:
    if d.name.lower() in _TREE:
        return True
    try:
        return any(c.is_dir() and c.name.lower() in _TREE for c in d.iterdir())
    except OSError:
        return False


def _has_d3d12_agility_sdk(folder: Path) -> bool:
    if _orig(folder):
        return True
    # Walk up to the game root (bounded) and look where engines keep DLSS.
    root = folder
    for _ in range(4):
        if (root / "steamapps").is_dir() or root.parent == root:
            break
        found = dlss.find_dlss_files(root, names=("nvngx_dlssg.dll", "nvngx_dlssd.dll"))
        if found:
            return True
        if not _inside_game_tree(root.parent) or (root.parent / "steamapps").is_dir():
            break
        root = root.parent
    return False


def install() -> None:
    _pe._has_d3d12_agility_sdk = _has_d3d12_agility_sdk
    _pe.file_version = file_version
    install_api()


# --- engines that load their renderer dynamically -----------------------------
# Unity imports only opengl32.dll statically and picks D3D11 at run time via
# UnityPlayer.dll; RE Engine never stores the literal "d3d12.dll". Both came
# out of our own tool (dlss5_proton.looks_like_d3d11 / looks_like_d3d12).
_orig_detect_api = _pe.detect_api
_D3D12_NEEDLES = (b"d3d12core.dll", b"d3d12createdevice", b"d3d12sdkversion", b"agilitysdk")


def _contains(path: Path, needle: bytes) -> bool:
    """Is this byte string anywhere in the file? Read in blocks, with the
    tail of each one carried into the next: a needle straddling a block
    boundary is invisible otherwise, and "d3d12createdevice" landing across
    a 1 MiB edge would silently label an RE Engine title DX11."""
    keep = len(needle) - 1
    try:
        with path.open("rb") as fh:
            carry = b""
            while chunk := fh.read(1 << 20):
                window = carry + chunk.lower()
                if needle in window:
                    return True
                carry = window[-keep:] if keep else b""
    except OSError:
        pass
    return False


# Executables whose static import table says one thing and whose renderer does
# another. Each entry is a measured fact, not a guess: the feeder logged a
# "same-device D3D12 session" for Remake, and vkd3d-proton created its swapchain.
_KNOWN_API = {
    "ff7remake_.exe": ("DX12", "UE4 title that imports d3d11.dll statically but renders D3D12 (feeder and vkd3d-proton logs)"),
}


def _is_64bit(path: Path) -> bool:
    try:
        return _pe.exe_bitness(path) == 64
    except Exception:          # PEError or an unreadable file: not evidence of anything
        return False



def detect_api(path: Path) -> tuple[str, str]:
    known = _KNOWN_API.get(path.name.lower())
    if known:
        return known
    # Unity first: its player embeds D3D12 symbols too, so the needle scan
    # below would mislabel every Unity title as DX12. Upstream 1.7.2 handles
    # UnityPlayer.dll only when opengl32.dll is imported; old Unity has no
    # UnityPlayer.dll and only a *_Data folder, so this stays.
    if (path.parent / "UnityPlayer.dll").is_file() or (path.parent / (path.stem + "_Data")).is_dir():
        return "DX11", "Unity (UnityPlayer.dll / *_Data beside the exe): D3D11 at run time"
    api, why = _orig_detect_api(path)
    # Upstream 1.7.3 reads the exe's own strings for a 64-bit d3d9 importer and
    # deliberately leaves a 32-bit one alone: every 32-bit game reaching a
    # d3d9 verdict so far really was DirectX 9. The same rule applies here.
    weak = api in ("OpenGL", "Unknown", "?") or (api == "DX9" and _is_64bit(path))
    if weak:
        for n in _D3D12_NEEDLES:
            if _contains(path, n):
                return "DX12", f"{n.decode()!r} in the executable (renderer loaded dynamically)"
    return api, why


def install_api() -> None:
    _pe.detect_api = detect_api


# --- the version stamped in a DLL ---------------------------------------------
# Upstream reads it through Windows' version API and returns "" anywhere else
# (core/pe.py: `if os.name != "nt": return ""`), so on Linux the four
# "was -> now" lines in installer.py print only the build they just wrote, and
# the point of printing both - noticing that a launcher quietly put the old
# runtime back - is gone.
#
# The number is not in the API though, it is in the file. VS_FIXEDFILEINFO
# lives in the resource section, and core/pe.py already knows what it looks
# like: it checks the same signature after Windows hands it the block.
#
# Finding it without walking the resource tree is what optiscaler.is_optiscaler
# does one module over, for the same reason ("Version resources are UTF-16, so
# looking for the name in that encoding finds it without a full resource
# walk"). Bounding the search to .rsrc is what keeps it honest: the same eight
# bytes appearing in code or in an embedded file cannot answer.

# The block's first two DWORDs are fixed: the signature 0xFEEF04BD, then a
# struct version that has been 1.0 since the format was defined. Matching both
# is what makes scanning safe - a four-byte signature alone could fall anywhere.
_FIXED_INFO = bytes([0xBD, 0x04, 0xEF, 0xFE, 0x00, 0x00, 0x01, 0x00])

# A resource section is normally well under a megabyte. The cap is here so a
# malformed header cannot ask for an arbitrary allocation; a real .rsrc that
# large has worse problems than a missing version string.
_RSRC_MAX = 64 << 20


def _rsrc(path: Path) -> bytes:
    """The raw bytes of the .rsrc section, or b"".

    Read on its own rather than with the whole file. These are game DLLs -
    pulling eighty megabytes into memory to find eight bytes is the kind of
    thing that makes scanning a library feel broken.
    """
    with path.open("rb") as fh:
        if fh.read(2) != b"MZ":
            return b""
        fh.seek(0x3C)
        raw = fh.read(4)
        if len(raw) < 4:
            return b""
        pe_at = struct.unpack("<I", raw)[0]
        fh.seek(pe_at)
        if fh.read(4) != b"PE" + bytes(2):
            return b""
        coff = fh.read(20)
        if len(coff) < 20:
            return b""
        sections = struct.unpack_from("<H", coff, 2)[0]
        opt_size = struct.unpack_from("<H", coff, 16)[0]
        fh.seek(pe_at + 24 + opt_size)
        for _ in range(sections):
            row = fh.read(40)
            if len(row) < 40:
                return b""
            if row[:8].rstrip(bytes(1)) != b".rsrc":
                continue
            size, at = struct.unpack_from("<II", row, 16)
            if not size or size > _RSRC_MAX:
                return b""
            fh.seek(at)
            return fh.read(size)
    return b""


def file_version(path) -> str:
    """"310.8.0" - the version stamped in a DLL, or "" if it has none.

    The same string upstream's Windows path returns, trailing zero included:
    NVIDIA writes 310.8.0 as 310.8.0.0 and nobody calls it that. "" for
    anything unreadable, because no version is not an error - plenty of DLLs
    carry no version resource at all.
    """
    try:
        blob = _rsrc(Path(path))
        at = blob.find(_FIXED_INFO)
        if at < 0:
            return ""
        ms, ls = struct.unpack_from("<II", blob, at + 8)
        parts = [ms >> 16, ms & 0xFFFF, ls >> 16, ls & 0xFFFF]
        while len(parts) > 3 and parts[-1] == 0:
            parts.pop()
        return ".".join(str(n) for n in parts)
    except (OSError, struct.error, ValueError):
        return ""

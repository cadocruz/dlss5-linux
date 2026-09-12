"""Widen core.pe's DX12 promotion to engine plugin folders.

Upstream only checks the exe's own folder for an Agility SDK or DLSS-G/RR.
Unreal keeps nvngx_dlssg.dll / nvngx_dlssd.dll under
Plugins/Runtime/Nvidia/**, so Stellar Blade came back "DX11". Under Proton
every DXGI game is vkd3d/dxvk anyway; what matters is the route list, and
native/upstream/optiscaler need the DX12 label.
"""
from __future__ import annotations

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

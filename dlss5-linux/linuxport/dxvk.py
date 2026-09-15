"""core.dxvk under Proton: there is nothing to place.

Upstream drops DXVK's d3d9/d3d11/dxgi DLLs beside a game so that a D3D title
renders on Vulkan and ReShade can reach it as a Vulkan layer instead of a
proxy DLL (games that close themselves when D3D11 is hooked; every DirectX 9
title). Under Proton the game already renders through Proton's own DXVK, and
a second DXVK in the game folder would need overrides of its own and could
downgrade the one Proton ships. So the route keeps its meaning -- "ReShade
goes in as the Vulkan layer" -- and the file step becomes a note.
"""
from __future__ import annotations

from pathlib import Path

from core import dxvk as _dxvk

VERSION = "proton"


def resolve() -> tuple[str, str]:
    return VERSION, ""


def install(exe_dir: Path, x64: bool, log=None, api: str = "DX11") -> tuple[str, list[str]]:
    log = log or (lambda *_: None)
    log(f"      DXVK: Proton already renders {api} on Vulkan through its own DXVK - nothing placed beside the game")
    return VERSION, []


def install_shim() -> None:
    _dxvk.resolve = resolve
    _dxvk.install = install

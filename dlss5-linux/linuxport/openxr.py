"""core.openxr under Proton: nothing to register, say so clearly.

Upstream 1.7.2 registers ReShade's OpenXR layer for VR games through
HKCU\\Software\\Khronos\\OpenXR\\1\\ApiLayers\\Implicit. Under Proton the
OpenXR runtime and its layers are the host's (Monado, SteamVR's Linux
runtime), not the prefix's registry, so the Windows mechanism does nothing
and `import winreg` fails outright.

The installer only reaches openxr when Options.vr is set, and the diagnosis
only when a manifest says vr. Both are made safe: the read-only questions
answer "not registered", and the write refuses with one sentence instead of
a ModuleNotFoundError from inside the install.
"""
from __future__ import annotations

from pathlib import Path

from core import openxr as _xr

REASON = ("VR through OpenXR is not available under Proton: ReShade's OpenXR "
          "layer would have to be registered with the host runtime, not the "
          "prefix registry. Install without the VR option.")


def registrations() -> list[tuple[Path, int]]:
    return []


def existing_registration() -> Path | None:
    return None


def install_layer(setup_exe: Path, log=None) -> tuple[Path, bool]:
    raise RuntimeError(REASON)


def unregister() -> bool:
    return False


def install() -> None:
    _xr.registrations = registrations
    _xr.existing_registration = existing_registration
    _xr.install_layer = install_layer
    _xr.unregister = unregister

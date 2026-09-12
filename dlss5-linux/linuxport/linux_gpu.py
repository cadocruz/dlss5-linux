"""Linux backend for core.gpu: nvidia-smi instead of the Windows registry.

core.gpu.detect() reads DriverDesc out of HKLM on Windows and returns
(name, sm) where sm is the CUDA compute capability as an int (120 = RTX 50,
89 = RTX 40, 86 = RTX 30, 75 = RTX 20). Everything downstream -- the route
steer in dlss.detect(), gpu.order_dlssnr(), the tier notes -- keys off that
int, so the shape must be preserved exactly.
"""
from __future__ import annotations

import shutil
import subprocess

from core import gpu as _gpu

_QUERY = ["--query-gpu=name,driver_version,compute_cap", "--format=csv,noheader"]


def _smi() -> tuple[str, str, str] | None:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        out = subprocess.run([exe, *_QUERY], capture_output=True, text=True,
                             timeout=10, check=False).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    if not out:
        return None
    parts = [p.strip() for p in out.splitlines()[0].split(",")]
    return (parts + ["", "", ""])[:3]  # type: ignore[return-value]


def detect() -> tuple[str | None, int | None]:
    row = _smi()
    if not row:
        return None, None
    name, _driver, cap = row
    sm = None
    if cap:
        try:
            major, minor = cap.split(".")
            sm = int(major) * 10 + int(minor)
        except ValueError:
            sm = _gpu.sm_for_name(name)
    return name or None, sm


def driver_version() -> str | None:
    row = _smi()
    return row[1] or None if row else None


def install() -> None:
    """Point core.gpu at this backend. Call once, before anything uses gpu."""
    install_gates()
    _gpu.detect = detect
    _gpu.driver_version = driver_version


# --- driver version gates ------------------------------------------------------
# Upstream compares against Windows release numbers, and asks this gate two
# questions that mean opposite things:
#
#   driver_at_least("616.56")  "new enough to have the neural runtime?"
#   driver_at_least("616.64")  "old enough to be free of the evaluate fault?"
#                              (sources.DRIVER_FAULT_MIN -- True means faulty)
#
# Linux numbering is a different series: the 610.x branch here runs the
# neural pass in every game tried, and the runtime comes from the archive
# beside the tools when the driver store lacks it. So the first question is
# answered yes on any Linux driver from the 610 branch up, rather than
# warning "update to 616.56" against a number that will never be.
#
# The second one has to be answered NO. A blanket True made dlss.driver_warning
# print "the 4.6/4.7 renodx-dlss5 builds fault inside the driver's NGX runtime
# on 616.64 and newer" on every route, told people to roll back to a Windows
# driver they cannot install, and pinned renodx-dlss5 to 4.55 in
# installer.py / components.py for a fault this branch does not have.
#
# Since upstream 1.8.0 the gate takes the version as an optional second
# argument (dlss.driver_warning passes it); the shim accepts the same.
_orig_at_least = _gpu.driver_at_least if hasattr(_gpu, "driver_at_least") else None
LINUX_KNOWN_GOOD_MAJOR = 610


def _parts(version) -> tuple[int, ...] | None:
    try:
        return tuple(int(x) for x in str(version).split("."))
    except (TypeError, ValueError):
        return None


def _fault_min() -> tuple[int, ...]:
    """sources.DRIVER_FAULT_MIN as a tuple -- imported late, core.sources is heavy."""
    from core import sources
    return _parts(sources.DRIVER_FAULT_MIN) or (616, 64)


def driver_at_least(want: str, have: str | None = None) -> bool | None:
    have = have or _gpu.driver_version()
    try:
        major = int(str(have).split(".")[0])
    except (TypeError, ValueError):
        return None
    if major >= LINUX_KNOWN_GOOD_MAJOR:
        wanted = _parts(want)
        # Anything below the Windows fault threshold: yes, this branch has it.
        # The threshold itself and above: no -- that is a Windows release
        # number and the fault behind it was never seen here.
        return True if wanted is None else wanted < _fault_min()
    return _orig_at_least(want, have) if _orig_at_least else None


def install_gates() -> None:
    if _orig_at_least:
        _gpu.driver_at_least = driver_at_least

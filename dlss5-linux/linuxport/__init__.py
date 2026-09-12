"""Linux backends for the upstream DLSS5-Autopilot core. Call activate() once."""
from . import (archive, crash, dxvk, linux_gpu, games, openxr, paths, pe, policy,
               state, pins, tuning, vulkan)


def activate() -> None:
    paths.install()
    linux_gpu.install()
    games.install()
    pe.install()
    policy.install()
    state.install()
    pins.install()
    archive.install()
    tuning.install()
    openxr.install()
    vulkan.install()
    dxvk.install_shim()
    crash.install()

"""Version selection for OptiScaler -- a DEFAULT, not a clamp.

Upstream's optiscaler.resolve(build) returns the newest release of one of
three lines: Dagherbou's (build ""), y4my4my4m's fork (optiscaler.FORK) or
wilsjo2's (optiscaler.PRESR). Under Proton the y4my4my4m line is the one
that works (FINDINGS.md), so the default here is:

    the local nightly zip (default_zip()) when it sits in the components dir
    (paths.components_dir()), else upstream's own resolver for the y4my4my4m fork.

Every install can pick another:

    set_optiscaler("default")        as above
    set_optiscaler("latest")         upstream's resolver for whatever build the
                                     install asked for (Options.opti_build)
    set_optiscaler("fallback")       Dagherbou v0.1.2 (FALLBACK_TAG) -- loses the
                                     NVAPI race under Proton; kept for bisecting
    set_optiscaler("v0.2.0-patch1")  any Dagherbou release tag
    set_optiscaler("/path/to.zip")   a local archive (staged into the cache)

A tag is resolved through sources._json (cached, rate-limit tolerant), so
testing a fresh upstream build is one flag, not a code change. The `build`
argument the installer passes (Options.opti_build) is honoured by "default"
when it names a fork explicitly, and by "latest" always.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from core import net, optiscaler as _opti, sources

from . import paths as _paths

DEFAULT_TAG = "y4my4m-nightly-20260906"               # label recorded in the manifest
DEFAULT_ZIP_NAME = "OptiScaler_v10.0.0-pre1_20260906_y4my4m-nightly.zip"
DEFAULT_BUILD = _opti.FORK                             # upstream's key for the y4my4my4m line
FALLBACK_TAG = "v0.1.2-dIssnr"         # Dagherbou v0.1.2; capital-I typo is upstream's
RELEASES = "https://api.github.com/repos/Dagherbou/OptiScaler_DLSSNR/releases"

_choice = "default"
_orig_resolve = _opti.resolve


def default_zip() -> Path:
    """The local nightly, looked up when asked for: components_dir() reads
    $DLSS5_COMPONENTS_DIR, and freezing it at import froze whatever the
    environment happened to say while linuxport was being imported."""
    return _paths.components_dir() / DEFAULT_ZIP_NAME


def set_optiscaler(choice: str) -> None:
    global _choice
    _choice = choice or "default"


def _by_tag(tag: str) -> tuple[str, str]:
    rel = sources._json(f"{RELEASES}/tags/{tag}")
    for a in rel.get("assets", []):
        if a["name"].lower().endswith(".zip"):
            return rel.get("tag_name", tag), a["browser_download_url"]
    raise RuntimeError(f"OptiScaler release {tag} has no .zip asset.")


def _local(p: Path, tag: str) -> tuple[str, str]:
    # The installer asks net.download() for f"OptiScaler-DLSSNR-{tag}.zip";
    # put the file there under that name so it is returned without a fetch.
    net.cache_dir().mkdir(parents=True, exist_ok=True)
    dest = net.cache_dir() / f"OptiScaler-DLSSNR-{tag}.zip"
    if not dest.is_file() or dest.stat().st_size != p.stat().st_size:
        shutil.copy2(p, dest)
    return tag, f"file://{dest}"


def resolve(build: str = "") -> tuple[str, str]:
    """Replacement for core.optiscaler.resolve; same signature and return."""
    c = _choice
    if c == "latest":
        return _orig_resolve(build)
    if c == "fallback":
        return _by_tag(FALLBACK_TAG)
    if c == "default":
        local = default_zip()
        if local.is_file() and not build:
            return _local(local, DEFAULT_TAG)
        # No local nightly (or a fork was named): upstream's resolver for the
        # y4my4my4m line, the one that works under Proton; a named build wins.
        return _orig_resolve(build or DEFAULT_BUILD)
    p = Path(c).expanduser()
    if p.is_file():
        return _local(p, f"local-{net.sha256(p)[:8]}")
    return _by_tag(c)


def install() -> None:
    _opti.resolve = resolve

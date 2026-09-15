"""Read enough of a log for upstream's diagnosis to see a whole session.

core.diagnose reads the last 400 KB of OptiScaler.log (and of dlss5-feed.log)
by default. OptiScaler writes a fresh log per launch, so the tail was meant to
be "the session", but a long session outgrows it: FF7 Rebirth wrote 490 KB in
four minutes, and the window then starts after the two "DLSS-NR running at"
lines. What is left can still hold "DLSS-NR did not run: it is switched off" --
the player toggling the model off in the overlay -- which upstream counts as a
failure, so a session that ran neural rendering for minutes was reported as
"OptiScaler loaded, but the model refused or failed."

Only the default window is raised. Callers that pass their own limit keep it:
upstream uses those to read the last launch of logs that accumulate across
launches (ReShade.log), and widening them would pull older runs into the
verdict. 8 MB matches linuxport.verify's own reader.
"""
from __future__ import annotations

from pathlib import Path

from core import diagnose as _diagnose

LIMIT = 8_000_000
_orig_tail = _diagnose._tail


def _tail(path: Path, limit: int = LIMIT) -> str:
    return _orig_tail(path, limit)


def install() -> None:
    _diagnose._tail = _tail

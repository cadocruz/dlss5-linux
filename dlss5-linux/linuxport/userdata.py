r"""Where the engines keep per-user game data, when the game runs under Proton.

Upstream's `_user_data_roots` answers that with %LOCALAPPDATA%, the same
plus "Low", %APPDATA%, and two folders under the Windows home. `_game_ran`
(diagnose/evidence.py) looks there FIRST, and says why: those folders "are
small, they are named after this game, and they are where most engines
actually write". On Linux none of those variables exist and neither
Windows home folder does, so the list comes back nearly empty and the
question `_game_ran` answers - did the game actually run after the install
- falls back to scanning the game's own folder, which upstream's own
docstring warns about: "a Steam update writes into a game folder too".

Under Proton the same folders exist, one level down from the prefix's
drive_c/users. Steam's prefixes name that user `steamuser`; Lutris, Heroic
and umu use the login name, so every user folder counts except Public.

Two things this deliberately does NOT do:

  It never returns an empty list. With no prefix to be had - a game
  outside Steam whose prefix could not be derived - it returns exactly
  what upstream would have, which on Linux is two folders that do not
  exist. A missing directory costs one failed scandir in the loop
  (evidence.py, `except OSError: continue`) and nothing else.

  It never guesses a prefix by globbing compatdata. `_game_ran` runs on a
  budget (_RAN_ENTRIES 4000, _RAN_PER_DIR 400, _RAN_SECONDS 1.0) and every
  root multiplies by the game's candidate names and the 15 entries of
  _RAN_LOOK. Another game's save folder under a wrongly guessed prefix
  would not just cost time - it would answer "the game ran" about a game
  that did not.

Prefix roots go first because the budget is spent in order, and they are
the ones that exist.

`_user_data_roots` is one of the three names diagnose/__init__ lists in
PATCHED and therefore does not exist on the package: it is read off
`model` by its caller and patched on `model` here, which is the whole
reason upstream keeps that list.
"""
from __future__ import annotations

from pathlib import Path

from core.diagnose import model

from . import vulkan

# Relative to one user folder inside the prefix. AppData/Local covers what
# %LOCALAPPDATA% named, LocalLow what the "Low" suffix named, Roaming what
# %APPDATA% named; the last two are upstream's Windows home folders.
USER_SUBDIRS = (
    "AppData/Local",
    "AppData/LocalLow",
    "AppData/Roaming",
    "Documents/My Games",
    "Saved Games",
)

# Wine creates it in every prefix and no engine writes a game's data there.
SKIP_USERS = {"public"}

_orig = model._user_data_roots


def prefix_roots(pfx: Path | None) -> list[Path]:
    """The per-user data folders inside one Proton/Wine prefix."""
    if pfx is None:
        return []
    out: list[Path] = []
    try:
        users = sorted((Path(pfx) / "drive_c" / "users").iterdir())
    except OSError:
        return []
    for u in users:
        if u.name.lower() in SKIP_USERS:
            continue
        try:
            if not u.is_dir():
                continue
        except OSError:
            continue
        out += [u / sub for sub in USER_SUBDIRS]
    return out


def roots() -> list[Path]:
    """The prefix's folders, then upstream's own - never nothing."""
    try:
        pfx = vulkan.prefix()
    except Exception:
        # prefix_for reads the Steam library and the prefs; a game outside
        # Steam with no remembered prefix is the ordinary case, not a fault.
        pfx = None
    return prefix_roots(pfx) + _orig()


def install() -> None:
    model._user_data_roots = roots

"""Small, conservative backend for Heroic's per-game configuration.

Heroic stores environment variables in ``GamesConfig/<id>.json``.  The file
is JSON (despite the historical ``enviromentOptions`` spelling), so updating
it here is safer than trying to edit a launch command string.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path


def _roots() -> list[Path]:
    roots: list[Path] = []
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        roots.append(Path(xdg) / "heroic")
    roots.append(Path.home() / ".config" / "heroic")
    roots.append(Path.home() / ".var/app/com.heroicgameslauncher.hgl/config/heroic")
    out: list[Path] = []
    for root in roots:
        root = root.expanduser()
        if root not in out and root.is_dir():
            out.append(root)
    return out


def _records(root: Path):
    for rel in ("legendaryConfig/legendary/installed.json",
                "gog_store/installed.json", "nile_config/nile/installed.json"):
        path = root / rel
        try:
            data = json.loads(path.read_text(encoding="utf8"))
        except (OSError, json.JSONDecodeError):
            continue
        items = data.items() if isinstance(data, dict) else enumerate(data)
        for key, item in items:
            if isinstance(item, dict):
                yield str(key), item


def record_for(game) -> tuple[Path, str, dict] | None:
    """Return ``(config root, Heroic id, installed record)`` for a game."""
    target = game.install_dir.resolve()
    for root in _roots():
        for key, item in _records(root):
            loc = item.get("install_path") or item.get("path")
            if not loc:
                continue
            try:
                installed = Path(loc).expanduser().resolve()
                if target == installed or target.is_relative_to(installed) or installed.is_relative_to(target):
                    game_id = item.get("app_name") or key
                    return root, str(game_id), item
            except (OSError, ValueError):
                continue
    return None


def config_for(game) -> tuple[Path, dict, dict] | None:
    found = record_for(game)
    if not found:
        return None
    root, game_id, _ = found
    path = root / "GamesConfig" / f"{game_id}.json"
    try:
        outer = json.loads(path.read_text(encoding="utf8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(outer, dict):
        return None
    obj = outer.get(game_id)
    if not isinstance(obj, dict):
        # Some Heroic versions use a single object without the outer id.
        obj = outer
    return path, outer, obj


def is_game(game) -> bool:
    return config_for(game) is not None


def proton_script_for(game) -> Path | None:
    found = config_for(game)
    if not found:
        return None
    _, _, obj = found
    value = obj.get("wineVersion", {}).get("bin") if isinstance(obj.get("wineVersion"), dict) else None
    path = Path(value).expanduser() if value else None
    return path if path and path.is_file() else None


def environment(game) -> dict[str, str]:
    found = config_for(game)
    if not found:
        return {}
    options = found[2].get("enviromentOptions", [])
    return {str(x.get("key")): str(x.get("value", "")) for x in options
            if isinstance(x, dict) and x.get("key")}


def current_line(game) -> str | None:
    env = environment(game)
    return " ".join(f'{k}="{v}"' if any(c in v for c in " ;") else f"{k}={v}"
                    for k, v in env.items()) or None


def running() -> bool:
    try:
        return subprocess.run(["pgrep", "-f", r"(^|/)heroic([ .]|$)"],
                              capture_output=True).returncode == 0
    except OSError:
        return False


def set_environment(game, values: dict[str, str]) -> Path | None:
    """Merge environment variables into Heroic's game config and return backup."""
    found = config_for(game)
    if not found:
        raise RuntimeError("Heroic game configuration was not found")
    if running():
        raise RuntimeError("Heroic is running: close it before changing the game environment")
    path, outer, obj = found
    options = obj.get("enviromentOptions")
    if not isinstance(options, list):
        options = []
        obj["enviromentOptions"] = options
    changed = False
    for key, value in values.items():
        existing = next((x for x in options if isinstance(x, dict) and x.get("key") == key), None)
        if existing is None:
            options.append({"key": key, "value": str(value)})
            changed = True
        elif str(existing.get("value", "")) != str(value):
            existing["value"] = str(value)
            changed = True
    if not changed:
        return None
    stamp = time.strftime("%Y%m%d-%H%M%S") + f"-{int(time.time() * 1000) % 1000:03d}"
    backup = path.with_name(f"{path.name}.bak-{stamp}")
    shutil.copy2(path, backup)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf8") as handle:
            json.dump(outer, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        os.replace(temp, path)
    finally:
        try:
            os.unlink(temp)
        except FileNotFoundError:
            pass
    return backup

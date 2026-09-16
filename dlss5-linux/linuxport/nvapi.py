"""Static, non-invasive detection of Proton's game-specific NVAPI policy."""
from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Decision:
    state: str  # required, not_required, unknown
    reason: str
    appid: str | None = None
    proton: Path | None = None
    source: str = ""


def _string_list(node: ast.AST) -> set[str]:
    if not isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return set()
    return {x.value for x in node.elts if isinstance(x, ast.Constant) and isinstance(x.value, str)}


def _adds_disablenvapi(body: list[ast.stmt]) -> bool:
    for node in body:
        if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Call):
            continue
        call = node.value
        if (isinstance(call.func, ast.Attribute) and call.func.attr == "add"
                and isinstance(call.func.value, ast.Name) and call.func.value.id == "ret"
                and len(call.args) == 1 and isinstance(call.args[0], ast.Constant)
                and call.args[0].value == "disablenvapi"):
            return True
    return False


def disabled_appids(script: Path) -> set[str]:
    """Extract only unconditional appid lists that add ``disablenvapi``."""
    try:
        tree = ast.parse(script.read_text(encoding="utf8", errors="replace"))
    except (OSError, SyntaxError):
        return set()
    found: set[str] = set()
    for fn in (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
               and n.name == "default_compat_config"):
        for node in ast.walk(fn):
            if not isinstance(node, ast.If) or not _adds_disablenvapi(node.body):
                continue
            test = node.test
            if (isinstance(test, ast.Compare) and isinstance(test.left, ast.Name)
                    and test.left.id == "appid" and len(test.ops) == 1
                    and isinstance(test.ops[0], ast.In) and len(test.comparators) == 1):
                found.update(_string_list(test.comparators[0]))
    return found


def _proton_from_config_info(prefix: Path | None) -> Path | None:
    if not prefix:
        return None
    try:
        text = (prefix.parent / "config_info").read_text(encoding="utf8", errors="replace")
    except OSError:
        return None
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("/") and "/files/" in line:
            root = Path(line.split("/files/", 1)[0])
            script = root / "proton"
            if script.is_file():
                return script
    return None


def decision(game, appid: str | None = None, prefix: Path | None = None) -> Decision:
    from . import heroic, proton
    appid = appid or proton.appid_for(game)
    script = heroic.proton_script_for(game) if not appid else _proton_from_config_info(prefix or proton.prefix_for(game))
    if not appid:
        return Decision("unknown", "no Steam AppID was resolved for this game", proton=script)
    if not script:
        return Decision("unknown", "the Proton build used by this prefix could not be identified", appid=appid)
    disabled = disabled_appids(script)
    if appid in disabled:
        return Decision("required", f"Proton disables NVAPI for AppID {appid}", appid, script, "Proton default_compat_config")
    return Decision("not_required", f"Proton does not list AppID {appid} in its NVAPI-disabled policy", appid, script, "Proton default_compat_config")

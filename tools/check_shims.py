#!/usr/bin/env python3
"""Prove that every core symbol the Linux layer touches still exists.

linuxport/ never edits core/; it monkey-patches it (`_gpu.detect = detect`)
and reads private helpers (`installer._previous_manifest`). Both break
silently on a re-vendor: the assignment creates a new attribute nobody calls,
the read raises AttributeError at import. This script finds every
`<core alias>.<name>` the Linux files use and checks, by parsing the source
with `ast`, that `core/<module>.py` defines `<name>` at top level. Nothing is
imported, so it runs on any machine -- Windows without PySide6 included --
and against a candidate core/ that is not installed yet.

    tools/check_shims.py                 current core/
    tools/check_shims.py --core PATH     a candidate core/ (used by sync_upstream)
    tools/check_shims.py --windows       also list Windows-only imports per module

Exit 0 when every symbol resolves, 1 when any is missing.
"""
from __future__ import annotations

import argparse
import ast
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "dlss5-linux"
CORE = ENGINE / "core"
LINUX_FILES = sorted((ENGINE / "linuxport").glob("*.py")) + [
    ENGINE / "dlss5_linux.py",
    ENGINE / "dlss5_gui_linux.py",
]

# Imports that only resolve on Windows. A core module that imports one of
# these at top level cannot be imported on Linux at all; one that imports
# them inside a function needs a shim for that function.
WINDOWS_MODULES = {"winreg", "msvcrt", "_winapi", "winsound", "ctypes.wintypes"}

# Windows reached without an import: a shell tool or an environment variable
# named in a string. Each is reported with the text that matched.
WINDOWS_STRINGS = ("powershell", "LOCALAPPDATA", "PROGRAMFILES", "ProgramFiles",
                   "HKLM", "HKCU", "HKEY_", "reg.exe", "tar.exe", "bsdtar", "cmd.exe")

# The upstream window is Tk; the Linux window is Qt. A core module that
# imports tkinter is a UI piece to re-create, not to shim.
TK_MODULES = {"tkinter", "tkinter.ttk", "tkinter.font"}


@dataclass
class Use:
    module: str
    name: str
    where: str            # "file:line"
    optional: bool = False  # guarded by hasattr(alias, "name")


@dataclass
class Result:
    uses: list[Use] = field(default_factory=list)
    missing: list[Use] = field(default_factory=list)
    checked_modules: set[str] = field(default_factory=set)


def _core_aliases(tree: ast.Module) -> dict[str, str]:
    """alias -> core module name, from `from core import x as y` / `import core.x`."""
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "core":
            for a in node.names:
                aliases[a.asname or a.name] = a.name
        elif isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("core."):
            # `from core.installer import X` -> X is a direct symbol use
            pass
        elif isinstance(node, ast.Import):
            for a in node.names:
                if a.name.startswith("core."):
                    aliases[a.asname or a.name] = a.name.split(".", 1)[1]
    return aliases


def _direct_uses(tree: ast.Module, where: str) -> list[Use]:
    """`from core.installer import Options` counts as a use of installer.Options."""
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("core."):
            mod = node.module.split(".", 1)[1]
            for a in node.names:
                out.append(Use(mod, a.name, f"{where}:{node.lineno}"))
    return out


def _hasattr_guards(tree: ast.Module, aliases: dict[str, str]) -> set[tuple[str, str]]:
    guarded = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "hasattr" and len(node.args) == 2
                and isinstance(node.args[0], ast.Name) and node.args[0].id in aliases
                and isinstance(node.args[1], ast.Constant) and isinstance(node.args[1].value, str)):
            guarded.add((aliases[node.args[0].id], node.args[1].value))
    return guarded


def dunder_all(module_path: Path) -> set[str]:
    """The names a module lists in __all__, when it is a plain literal."""
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    out: set[str] = set()
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets):
            continue
        if isinstance(node.value, (ast.List, ast.Tuple)):
            for e in node.value.elts:
                if isinstance(e, ast.Constant) and isinstance(e.value, str):
                    out.add(e.value)
    return out


def literal_strings(module_path: Path, name: str) -> set[str]:
    """A module-level `NAME = ("a", "b")` of plain strings."""
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    out: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == name for t in node.targets):
            if isinstance(node.value, (ast.List, ast.Tuple)):
                for e in node.value.elts:
                    if isinstance(e, ast.Constant) and isinstance(e.value, str):
                        out.add(e.value)
    return out


def package_names(pkg_dir: Path) -> set[str]:
    """What `diagnose.<name>` reaches when core/diagnose is a package.

    core/diagnose was one module until upstream 1.9.0. Its __init__ now
    re-exports every submodule's __all__ onto the package - except the names
    listed in PATCHED, which stay only on the submodule that owns them,
    because a copy would be a value that looks right and is not the one the
    code reads. Mirroring that subtraction here is the point: it is what makes
    this check catch a shim that patches the package when it must patch the
    submodule.
    """
    init = pkg_dir / "__init__.py"
    exported: set[str] = set()
    for sub in sorted(pkg_dir.glob("*.py")):
        if sub.name != "__init__.py":
            exported |= dunder_all(sub)
    return top_level_names(init) | (exported - literal_strings(init, "PATCHED"))


def module_names(core_dir: Path, module: str) -> set[str] | None:
    """Top-level names of core/<module>.py, or of core/<module>/ as a package."""
    mp = core_dir / f"{module}.py"
    if mp.is_file():
        return top_level_names(mp)
    pkg = core_dir / module
    if (pkg / "__init__.py").is_file():
        return package_names(pkg)
    return None


def uses_in(path: Path) -> list[Use]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    aliases = _core_aliases(tree)
    guards = _hasattr_guards(tree, aliases)
    try:
        rel = str(path.relative_to(ROOT))
    except ValueError:            # a file outside the repository (tests, ad-hoc checks)
        rel = str(path)
    out = _direct_uses(tree, rel)
    seen: set[tuple[str, str]] = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                and node.value.id in aliases):
            key = (aliases[node.value.id], node.attr)
            if key in seen:
                continue
            seen.add(key)
            out.append(Use(key[0], key[1], f"{rel}:{node.lineno}", optional=key in guards))
    # ast.walk is breadth-first; report in source order instead.
    out.sort(key=lambda u: int(u.where.rsplit(":", 1)[1]))
    return out


def top_level_names(module_path: Path) -> set[str]:
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                for n in ast.walk(t):
                    if isinstance(n, ast.Name):
                        names.add(n.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                names.add(a.asname or a.name)
        elif isinstance(node, ast.Import):
            for a in node.names:
                names.add((a.asname or a.name).split(".")[0])
        elif isinstance(node, (ast.If, ast.Try)):
            # `try: import x except: x = None` and `if sys.platform...:` blocks
            for sub in ast.walk(node):
                if isinstance(sub, (ast.FunctionDef, ast.ClassDef)):
                    names.add(sub.name)
                elif isinstance(sub, ast.Assign):
                    for t in sub.targets:
                        if isinstance(t, ast.Name):
                            names.add(t.id)
    return names


def windows_imports(module_path: Path) -> tuple[list[str], list[str]]:
    """(top-level, inside functions or strings) Windows-only reach of one core module.

    Top-level means the module cannot be imported on Linux at all. Inner means
    a function needs a shim: a Windows import inside it, `ctypes.windll`, a
    Windows shell tool or environment variable named in a string, or Tk.
    """
    src = module_path.read_text(encoding="utf-8")
    tree = ast.parse(src, filename=str(module_path))
    top: list[str] = []
    inner: list[str] = []
    for node in tree.body:
        names = _import_names(node)
        top += [n for n in names if n in WINDOWS_MODULES]
        top += [f"tk:{n}" for n in names if n in TK_MODULES]
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for sub in ast.walk(node):
                names = _import_names(sub)
                inner += [n for n in names if n in WINDOWS_MODULES]
                inner += [f"tk:{n}" for n in names if n in TK_MODULES]
    # ctypes.windll is the other Win32 entry point, and it is an attribute, not an import
    if "windll" in src or "WinDLL" in src:
        inner.append("ctypes.windll")
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            inner += [f'"{s}"' for s in WINDOWS_STRINGS if s in node.value]
    return sorted(set(top)), sorted(set(inner))


def portability(module_path: Path) -> str:
    """One word for a module: windows-only | tk-ui | needs-shim | portable."""
    top, inner = windows_imports(module_path)
    if any(not t.startswith("tk:") for t in top):
        return "windows-only"
    if any(t.startswith("tk:") for t in top + inner):
        return "tk-ui"
    return "needs-shim" if inner else "portable"


def _import_names(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Import):
        return [a.name for a in node.names]
    if isinstance(node, ast.ImportFrom) and node.module:
        return [node.module] + [f"{node.module}.{a.name}" for a in node.names]
    return []


def check(core_dir: Path, linux_files: list[Path] | None = None) -> Result:
    """Resolve every core symbol the given Linux files use against core_dir.

    linux_files defaults to the module-level LINUX_FILES at call time, so a
    caller (or a test) may point the check at other files.
    """
    res = Result()
    cache: dict[str, set[str] | None] = {}
    for f in (LINUX_FILES if linux_files is None else linux_files):
        if not f.is_file():
            continue
        for use in uses_in(f):
            res.uses.append(use)
            if use.module not in cache:
                cache[use.module] = module_names(core_dir, use.module)
                res.checked_modules.add(use.module)
            names = cache[use.module]
            if names is None or use.name not in names:
                res.missing.append(use)
    return res


def report(res: Result, core_dir: Path) -> None:
    print(f"core: {core_dir}")
    print(f"{len(res.uses)} symbol uses across {len(res.checked_modules)} core modules")
    if not res.missing:
        print("all symbols resolve")
        return
    hard = [m for m in res.missing if not m.optional]
    soft = [m for m in res.missing if m.optional]
    for m in hard:
        print(f"  MISSING  core.{m.module}.{m.name:<28} used at {m.where}")
    for m in soft:
        print(f"  optional core.{m.module}.{m.name:<28} used at {m.where} (hasattr-guarded)")


def report_windows(core_dir: Path) -> None:
    print("\nWindows reach in core/ (windows-only = cannot import on Linux; "
          "needs-shim = a function to replace; tk-ui = window code to re-create in Qt):")
    # core/diagnose is a package since 1.9.0, so one level down counts too.
    for mp in sorted(list(core_dir.glob("*.py")) + list(core_dir.glob("*/*.py"))):
        top, inner = windows_imports(mp)
        if top or inner:
            label = mp.name if mp.parent == core_dir else f"{mp.parent.name}/{mp.name}"
            print(f"  {label:<18} {portability(mp):<13} {', '.join(top + inner)}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--core", type=Path, default=CORE, help="core/ directory to check against")
    ap.add_argument("--windows", action="store_true", help="also list Windows-only imports per module")
    args = ap.parse_args()
    if not args.core.is_dir():
        sys.exit(f"not a directory: {args.core}")
    res = check(args.core)
    report(res, args.core)
    if args.windows:
        report_windows(args.core)
    return 1 if any(not m.optional for m in res.missing) else 0


if __name__ == "__main__":
    sys.exit(main())

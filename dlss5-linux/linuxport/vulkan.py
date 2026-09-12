r"""core.vulkan under Proton: the layer lives in the prefix, the registry is Wine's.

Upstream registers ReShade as an implicit Vulkan layer for the Windows user:
files under %LOCALAPPDATA%, a DWORD under HKCU\Software\Khronos\Vulkan\
ImplicitLayers written with winreg. Under Proton every one of those is per
prefix, and there is no winreg:

* the files go to  <prefix>/drive_c/ProgramData/ReShade/  (C:\ProgramData\ReShade),
  where LeShade has been putting them for two years;
* the registry is read from the prefix's user.reg / system.reg (plain text)
  and written with `wine regedit /S file.reg` **inside that prefix** -- through
  protontricks when the game has an appid, else the Proton build that owns the
  prefix (found from compatdata/<appid>/config_info), else the system wine
  with a warning, since a different Wine touching a Proton prefix may update it;
* Wine's own vulkan-1.dll (winevulkan) hands Vulkan straight to the host
  loader and does not read Windows layer registrations. LeShade's measured
  fix is the LunarG Windows loader as a native vulkan-1.dll in system32 plus
  a `vulkan-1=native` override; winevulkan registers itself as the ICD that
  loader then finds. That step is here, on by default, and it is the one
  part of this file the author of the port has NOT run: DLSS5_VULKAN_NATIVE_LOADER=0
  turns it off.

core.vulkan's read-only helpers (manifest_x64, layer_name, name_clash, the
32-bit layer name fix) are pure and are reused; the module-level functions
that touch winreg or %LOCALAPPDATA% are replaced. They have no game argument,
so the prefix comes from a context set by wrappers around installer.install /
preview / uninstall and diagnose.analyse.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

from core import diagnose as _diag, installer as _inst, net, prefs as _prefs, vulkan as _v
from . import paths, proton

WIN_DIR = r"C:\ProgramData\ReShade"
LAYER_KEY = r"Software\Khronos\Vulkan\ImplicitLayers"
LAYER_KEY32 = r"Software\Wow6432Node\Khronos\Vulkan\ImplicitLayers"
OVERRIDES_KEY = r"Software\Wine\DllOverrides"
NATIVE_LOADER_ENV = "DLSS5_VULKAN_NATIVE_LOADER"
WINE_ENV = "DLSS5_WINE"
# LeShade's pin. The x64 and x86 loaders sit under x64/ and x86/ in the archive.
VULKANRT_URL = "https://sdk.lunarg.com/sdk/download/1.4.341.0/windows/VulkanRT-X64-1.4.341.0-Components.zip"
LOADER_BACKUP = ".dlss5-linux-backup"
NO_PREFIX = ("no Proton/Wine prefix is known for this game - run it once under Proton "
             "(Steam creates compatdata/<appid>/pfx), or set the prefix with '--prefix' / [ set prefix ]")

_ctx: dict = {"game": None, "dir": None}


# --- which prefix ------------------------------------------------------------------------------
def use_game(g) -> None:
    _ctx["game"], _ctx["dir"] = g, getattr(g, "install_dir", None)


def use_dir(d) -> None:
    _ctx["game"], _ctx["dir"] = None, Path(d) if d else None


def current_game():
    g = _ctx["game"]
    if g is not None:
        return g
    d = _ctx["dir"]
    if d is None:
        return None
    return SimpleNamespace(folder=Path(d), exe=None, install_dir=Path(d), name=Path(d).name)


def prefix() -> Path | None:
    g = current_game()
    return proton.prefix_for(g) if g is not None else None


def appid() -> str | None:
    g = current_game()
    return proton.appid_for(g) if g is not None else None


# --- paths, both views ---------------------------------------------------------------------------
def to_win(p: Path, pfx: Path) -> str:
    """A prefix path as the Windows side sees it: drive_c -> C:\\, anything else Z:\\."""
    p = Path(p)
    try:
        rel = p.resolve().relative_to((pfx / "drive_c").resolve())
        return "C:\\" + str(rel).replace("/", "\\")
    except (ValueError, OSError):
        return "Z:" + str(p).replace("/", "\\")


def to_unix(win: str, pfx: Path) -> Path:
    s = win.replace("\\", "/")
    if len(s) >= 2 and s[1] == ":":
        drive, rest = s[0].upper(), s[2:].lstrip("/")
        if drive == "C":
            return pfx / "drive_c" / rest
        if drive == "Z":
            return Path("/" + rest)
        return pfx / f"dosdevices/{drive.lower()}:" / rest
    return Path(s)


def layer_dir() -> Path:
    p = prefix()
    return (p / "drive_c" / "ProgramData" / "ReShade") if p else (paths.STATE / "no-prefix" / "reshade-vulkan")


def is_ours(path: Path) -> bool:
    try:
        return Path(path).resolve().parent == layer_dir().resolve()
    except OSError:
        return False


# --- Wine's registry files, read --------------------------------------------------------------------
_SECTION = re.compile(r"^\[(.+?)\](?:\s+\d+)?\s*$")
_DWORD = re.compile(r'^"(.+)"=dword:([0-9a-fA-F]{8})\s*$')


def _unescape(s: str) -> str:
    return s.replace("\\\\", "\\")


def parse_reg_file(text: str, wanted_keys: tuple[str, ...]) -> list[tuple[str, int]]:
    """(value name, dword) for every DWORD under any of `wanted_keys`."""
    want = {k.lower() for k in wanted_keys}
    out: list[tuple[str, int]] = []
    inside = False
    for raw in text.splitlines():
        line = raw.strip()
        m = _SECTION.match(line)
        if m:
            inside = _unescape(m.group(1)).lower() in want
            continue
        if not inside or line.startswith("#") or line.startswith(";"):
            continue
        v = _DWORD.match(line)
        if v:
            out.append((_unescape(v.group(1)), int(v.group(2), 16)))
    return out


def registrations() -> list[tuple[Path, int]]:
    """Every ReShade layer manifest registered in the prefix, with its value (0 = active)."""
    pfx = prefix()
    if not pfx:
        return []
    out: list[tuple[Path, int]] = []
    for fname, keys in (("user.reg", (LAYER_KEY,)), ("system.reg", (LAYER_KEY, LAYER_KEY32))):
        try:
            text = (pfx / fname).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for name, val in parse_reg_file(text, keys):
            if "reshade" in name.lower() and name.lower().endswith(".json"):
                p = to_unix(name, pfx)
                if p.is_file():
                    out.append((p, val))
    return out


def existing_registration() -> Path | None:
    for p, val in registrations():
        if val == 0:
            return p
    return None


def registered_for(x64: bool) -> Path | None:
    for p, val in registrations():
        if val != 0:
            continue
        arch = _v.manifest_x64(p)
        if arch is None or arch is x64:
            return p
    return None


# --- Wine's registry, written -------------------------------------------------------------------------
def _q(win_path: str) -> str:
    return '"' + win_path.replace("\\", "\\\\") + '"'


def reg_add_text(m64: str | None, m32: str | None, native_loader: bool) -> str:
    """A regedit file registering the manifests (Windows paths) as active implicit layers.

    HKCU carries both (that is where the 1.8.0 name fix matters: the key is not
    split per architecture); HKLM gets the 64-bit one and Wow6432Node the 32-bit
    one, the way ReShade's own installer does it when elevated."""
    lines = ["Windows Registry Editor Version 5.00", ""]
    hkcu = [f"{_q(m)}=dword:00000000" for m in (m64, m32) if m]
    if hkcu:
        lines += [f"[HKEY_CURRENT_USER\\{LAYER_KEY}]", *hkcu, ""]
    if m64:
        lines += [f"[HKEY_LOCAL_MACHINE\\{LAYER_KEY}]", f"{_q(m64)}=dword:00000000", ""]
    if m32:
        lines += [f"[HKEY_LOCAL_MACHINE\\{LAYER_KEY32}]", f"{_q(m32)}=dword:00000000", ""]
    if native_loader:
        lines += [f"[HKEY_CURRENT_USER\\{OVERRIDES_KEY}]", '"vulkan-1"="native"', ""]
    return "\n".join(lines) + "\n"


def reg_remove_text(m64: str | None, m32: str | None, native_loader: bool) -> str:
    """Delete only our values; the keys stay for whatever else lives there."""
    lines = ["Windows Registry Editor Version 5.00", ""]
    ours = [f"{_q(m)}=-" for m in (m64, m32) if m]
    if ours:
        lines += [f"[HKEY_CURRENT_USER\\{LAYER_KEY}]", *ours, ""]
    if m64:
        lines += [f"[HKEY_LOCAL_MACHINE\\{LAYER_KEY}]", f"{_q(m64)}=-", ""]
    if m32:
        lines += [f"[HKEY_LOCAL_MACHINE\\{LAYER_KEY32}]", f"{_q(m32)}=-", ""]
    if native_loader:
        lines += [f"[HKEY_CURRENT_USER\\{OVERRIDES_KEY}]", '"vulkan-1"=-', ""]
    return "\n".join(lines) + "\n"


@dataclass
class WineRunner:
    kind: str                      # env | protontricks | proton | system
    wine: list[str]                # argv prefix that runs `wine ...` in the prefix
    wineserver: list[str]          # argv that waits for the prefix's wineserver
    env: dict = field(default_factory=dict)
    note: str = ""
    shell_join: bool = False       # protontricks -c takes one shell string


def proton_wine(pfx: Path) -> Path | None:
    """The wine binary of the Proton build that owns this prefix, from config_info."""
    info = pfx.parent / "config_info"
    try:
        lines = info.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    for line in lines[1:]:
        s = line.strip().replace("\\", "/")      # a Windows test box writes backslashes
        if "/files/" in s:
            root = Path(s.split("/files/", 1)[0]) / "files"
        elif "/dist/" in s:
            root = Path(s.split("/dist/", 1)[0]) / "dist"
        else:
            continue
        for name in ("wine64", "wine"):
            cand = root / "bin" / name
            if cand.is_file():
                return cand
    return None


def pick_runner(pfx: Path, app: str | None) -> WineRunner | None:
    base_env = {"WINEPREFIX": str(pfx), "WINEDLLOVERRIDES": "mscoree,mshtml=", "WINEDEBUG": "-all"}
    forced = os.environ.get(WINE_ENV, "").strip()
    if forced:
        ws = str(Path(forced).with_name("wineserver"))
        return WineRunner("env", [forced], [ws if Path(ws).is_file() else "wineserver", "-w"], base_env,
                          f"{WINE_ENV} names the wine to use")
    if app:
        pt = None
        if shutil.which("protontricks"):
            pt = ["protontricks"]
        elif shutil.which("flatpak") and _flatpak_has("com.github.Matoking.protontricks"):
            pt = ["flatpak", "run", "com.github.Matoking.protontricks"]
        if pt:
            return WineRunner("protontricks", [*pt, "--no-bwrap", "-c"], [*pt, "--no-bwrap", "-c"],
                              {"STEAM_COMPAT_DATA_PATH": str(pfx.parent)},
                              "through protontricks, in the Proton build Steam uses for this game",
                              shell_join=True)
    pw = proton_wine(pfx)
    if pw:
        files = pw.parent.parent
        env = dict(base_env)
        env["PATH"] = f"{files / 'bin'}:{os.environ.get('PATH', '')}"
        env["LD_LIBRARY_PATH"] = ":".join(str(files / d) for d in ("lib64", "lib")) + (
            ":" + os.environ["LD_LIBRARY_PATH"] if os.environ.get("LD_LIBRARY_PATH") else "")
        env["WINEDLLPATH"] = ":".join(str(files / d / "wine") for d in ("lib64", "lib"))
        return WineRunner("proton", [str(pw)], [str(pw.with_name("wineserver")), "-w"], env,
                          f"the Proton build that owns the prefix ({files.parent.name})")
    if shutil.which("wine"):
        return WineRunner("system", ["wine"], ["wineserver", "-w"], base_env,
                          "the system wine - a different Wine version may update a Proton prefix")
    return None


def _flatpak_has(app_id: str) -> bool:
    try:
        return subprocess.run(["flatpak", "info", app_id], capture_output=True).returncode == 0
    except OSError:
        return False


def regedit_argv(runner: WineRunner, reg_win_path: str, app: str | None) -> tuple[list[str], list[str]]:
    """(regedit command, wineserver-wait command) for this runner."""
    if runner.shell_join:
        return ([*runner.wine, f'wine regedit /S "{reg_win_path}"', app or ""],
                [*runner.wineserver, "wineserver -w", app or ""])
    return [*runner.wine, "regedit", "/S", reg_win_path], list(runner.wineserver)


def run_reg(text: str, pfx: Path, log=None, label: str = "layer") -> bool:
    """Write a .reg into the prefix and import it with wine regedit there."""
    log = log or (lambda *_: None)
    app = appid()
    runner = pick_runner(pfx, app)
    if runner is None:
        raise RuntimeError("no way to run regedit in this prefix: install protontricks, "
                           f"or point {WINE_ENV} at a wine binary")
    reg = pfx / "drive_c" / "ProgramData" / "ReShade" / f"dlss5-linux-{label}.reg"
    reg.parent.mkdir(parents=True, exist_ok=True)
    reg.write_text(text, encoding="utf-8")
    cmd, wait = regedit_argv(runner, to_win(reg, pfx), app)
    env = {**os.environ, **runner.env}
    log(f"      regedit via {runner.note}")
    for argv in (cmd, wait):
        try:
            r = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=120)
        except (OSError, subprocess.SubprocessError) as e:
            raise RuntimeError(f"{argv[0]} failed to start: {e}") from e
        if r.returncode != 0 and argv is cmd:
            raise RuntimeError(f"regedit failed ({r.returncode}): {(r.stderr or r.stdout).strip()[-300:]}")
    return True


# --- the native loader (LeShade's finding) ------------------------------------------------------------------
def native_loader_wanted() -> bool:
    return os.environ.get(NATIVE_LOADER_ENV, "1").strip() not in ("0", "no", "false", "off")


def native_loader_present(pfx: Path | None) -> bool:
    """A vulkan-1.dll in system32 that is not Wine's own builtin."""
    if not pfx:
        return False
    p = pfx / "drive_c" / "windows" / "system32" / "vulkan-1.dll"
    try:
        data = p.read_bytes()
    except OSError:
        return False
    return b"Wine builtin DLL" not in data and b"Wine placeholder DLL" not in data


def ensure_native_loader(pfx: Path, x64: bool, also32: bool, log=None) -> bool:
    """Place the LunarG loader as vulkan-1.dll in system32 (and syswow64 for 32-bit).

    Wine's builtin is kept beside it with LOADER_BACKUP so unregister() can put
    it back. Returns True when the loader is in place. Failure is a warning,
    not an error: the layer is still registered and the caller says why it may
    not load."""
    log = log or (lambda *_: None)
    if native_loader_present(pfx):
        log("      native vulkan-1.dll already in system32")
        return True
    try:
        archive = net.download(VULKANRT_URL, "VulkanRT-Components.zip")
    except Exception as e:
        log(f"      !! could not fetch the LunarG loader ({e}); winevulkan may ignore the layer")
        return False
    targets = [("x64", pfx / "drive_c" / "windows" / "system32")] if x64 or also32 else []
    if also32 or not x64:
        targets.append(("x86", pfx / "drive_c" / "windows" / "syswow64"))
    for arch, sysdir in targets:
        if not sysdir.is_dir():
            continue
        dest = sysdir / "vulkan-1.dll"
        backup = sysdir / f"vulkan-1.dll{LOADER_BACKUP}"
        if dest.is_file() and not backup.exists():
            shutil.copy2(dest, backup)
        try:
            net.extract_one(archive, f"{arch}/vulkan-1.dll", dest)
        except Exception as e:
            log(f"      !! no {arch}/vulkan-1.dll in the LunarG archive ({e})")
            return False
        log(f"      {dest.relative_to(pfx)} <- LunarG loader ({arch})")
    return True


def restore_builtin_loader(pfx: Path, log=None) -> None:
    log = log or (lambda *_: None)
    for sub in ("system32", "syswow64"):
        d = pfx / "drive_c" / "windows" / sub
        backup = d / f"vulkan-1.dll{LOADER_BACKUP}"
        if backup.is_file():
            shutil.move(str(backup), str(d / "vulkan-1.dll"))
            log(f"      {sub}/vulkan-1.dll: Wine's builtin restored")


# --- the two operations the installer calls ----------------------------------------------------------------
def install_layer(setup_exe: Path, log=None, also32: bool = False) -> tuple[Path, bool]:
    log = log or (lambda *_: None)
    pfx = prefix()
    if not pfx:
        raise RuntimeError(NO_PREFIX)
    x64 = not also32

    stale = _v.name_clash() if also32 else None
    if stale is not None:
        log("      the 32-bit layer registered here shares the 64-bit layer's name, "
            "which is why it never loaded - rewriting it")
    found = registered_for(x64=x64)
    if found is not None and not is_ours(found) and stale is None:
        log(f"      a ReShade Vulkan layer is already registered in this prefix ({found}); reusing it")
        return found, False
    if found is None and existing_registration() is not None:
        log("      a ReShade Vulkan layer is registered, but not one this game's architecture can load - adding ours")

    d = layer_dir()
    d.mkdir(parents=True, exist_ok=True)
    m64 = _v._place(setup_exe, d, _v.DLL, _v.MANIFEST)
    m32 = _v._place(setup_exe, d, _v.DLL32, _v.MANIFEST32) if also32 else None
    native = native_loader_wanted() and ensure_native_loader(pfx, x64, also32, log)
    if native_loader_wanted() and not native:
        log("      !! the layer is registered but Wine's own vulkan-1.dll does not load Windows layers; "
            f"retry with network, or set {NATIVE_LOADER_ENV}=0 to stop trying")
    run_reg(reg_add_text(to_win(m64, pfx), to_win(m32, pfx) if m32 else None, native), pfx, log, "add")
    log(f"      registered {_v.LAYER_NAME}" + (f" and {_v.LAYER_NAME32}" if also32 else "") + " in the prefix")
    log(f"      {m64}")
    if native:
        log("      launch options need vulkan-1=n,b as well - 'launch options' has the line")
    return m64, True


def unregister() -> bool:
    """Remove only what we registered in the prefix. True when something was."""
    pfx = prefix()
    if not pfx:
        return False
    d = layer_dir()
    mine = {p for p, _ in registrations() if is_ours(p)}
    if not mine:
        return False
    m64 = to_win(d / _v.MANIFEST, pfx) if (d / _v.MANIFEST) in mine else None
    m32 = to_win(d / _v.MANIFEST32, pfx) if (d / _v.MANIFEST32) in mine else None
    native = native_loader_present(pfx)
    run_reg(reg_remove_text(m64, m32, native), pfx, None, "remove")
    if native:
        restore_builtin_loader(pfx)
    return True


# --- the registration is per prefix, not per user ----------------------------------------------------------
# Upstream keeps one list of Vulkan installs (prefs.vulkan_games) because on
# Windows there is one registration for the whole account: whoever uninstalls
# last is the one that removes it. Under Proton the manifest, the registry key
# and the native loader all live inside <prefix>, and two games in two prefixes
# share none of them. Counted together, uninstalling the only game in prefix A
# printed "kept the Vulkan layer: 1 other Vulkan install(s) still use it" and
# left prefix A registered - with LunarG's loader still in its system32 - for
# good.
#
# The list is still global (it is upstream's file); only the count that
# installer.uninstall() reads is narrowed to the games sharing THIS prefix,
# which the uninstall wrapper below has already set as the context.
_orig_drop_vulkan_game = _prefs.drop_vulkan_game


def _prefix_of(install_dir) -> Path | None:
    d = Path(install_dir)
    g = SimpleNamespace(folder=d, exe=None, install_dir=d, name=d.name)
    try:
        return proton.prefix_for(g)
    except OSError:
        return None


def _same(a: Path | None, b: Path | None) -> bool:
    if a is None or b is None:
        return False
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return Path(a) == Path(b)


def drop_vulkan_game(install_dir) -> list[str]:
    """Forget this game; return only the ones left in the same prefix."""
    remaining = _orig_drop_vulkan_game(install_dir)
    mine = prefix()
    if mine is None:            # no prefix known: keep upstream's caution
        return remaining
    return [other for other in remaining if _same(_prefix_of(other), mine)]


# --- context wrappers -------------------------------------------------------------------------------------------
_orig_install, _orig_preview, _orig_uninstall = _inst.install, _inst.preview, _inst.uninstall
_orig_analyse = _diag.analyse


def _install(g, *a, **k):
    use_game(g)
    return _orig_install(g, *a, **k)


def _preview(g, *a, **k):
    use_game(g)
    return _orig_preview(g, *a, **k)


def _uninstall(g, *a, **k):
    use_game(g)
    return _orig_uninstall(g, *a, **k)


def _analyse(install_dir, *a, **k):
    if _ctx["game"] is None or Path(getattr(_ctx["game"], "install_dir", "")) != Path(install_dir):
        use_dir(install_dir)
    return _orig_analyse(install_dir, *a, **k)


def install() -> None:
    _v.layer_dir = layer_dir
    _v.is_ours = is_ours
    _v.registrations = registrations
    _v.existing_registration = existing_registration
    _v.registered_for = registered_for
    _v.install_layer = install_layer
    _v.unregister = unregister
    _prefs.drop_vulkan_game = drop_vulkan_game
    _inst.install = _install
    _inst.preview = _preview
    _inst.uninstall = _uninstall
    _diag.analyse = _analyse


def reg_label(pfx: Path) -> str:
    return hashlib.sha1(str(pfx).encode()).hexdigest()[:8]

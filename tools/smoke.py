#!/usr/bin/env python3
"""Headless smoke: does the Linux layer still load and drive the core?

Each step prints OK / FAIL / SKIP and the run exits non-zero on any FAIL.
Nothing touches Steam, the network or a real game: a fixture folder in a
temporary directory stands in for the game.

    tools/smoke.py            imports, activate(), detection, plan, preview
    tools/smoke.py --gui      also builds the PySide6 window offscreen and walks its pages

Runs on Linux; on another OS the Linux-only steps are skipped rather than
failed, so the import and AST checks can still be exercised there.
"""
from __future__ import annotations

import argparse
import importlib
import os
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "dlss5-linux"
sys.path.insert(0, str(ENGINE))
sys.path.insert(0, str(ROOT / "tools"))

IS_LINUX = sys.platform.startswith("linux")
RESULTS: list[tuple[str, str, str]] = []

# Modules whose import needs a display toolkit or Windows and are not part of
# the Linux layer. Everything else in core/ must import cleanly.
SKIP_IMPORT = {"gui", "compareui", "remixui", "reportui", "icon_png", "video", "wincrash", "openxr"}


FORCE_ALL = False  # --all: run the Linux-only steps on another OS anyway


def step(name: str, fn, *, linux_only: bool = False) -> None:
    if linux_only and not IS_LINUX and not FORCE_ALL:
        RESULTS.append(("SKIP", name, f"not Linux ({sys.platform}); --all to force"))
        _report_last()
        return
    try:
        detail = fn() or ""
        RESULTS.append(("OK", name, str(detail)[:120]))
    except SkipStep as e:
        RESULTS.append(("SKIP", name, str(e)[:120]))
    except Exception as e:  # noqa: BLE001 -- a smoke reports, it does not raise
        RESULTS.append(("FAIL", name, f"{type(e).__name__}: {e}"[:200]))
        if os.environ.get("SMOKE_TRACE"):
            traceback.print_exc()
    _report_last()


def _report_last() -> None:
    """Print each step as it lands, flushed: an abort at interpreter exit
    (a QThread destroyed while running) must not swallow the results."""
    status, name, detail = RESULTS[-1]
    print(f"[{status:<4}] {name:<30}  {detail}", flush=True)


class SkipStep(Exception):
    pass


# --- steps ---------------------------------------------------------------------
def s_check_shims():
    import check_shims
    res = check_shims.check(ENGINE / "core")
    hard = [m for m in res.missing if not m.optional]
    if hard:
        raise AssertionError("missing: " + ", ".join(f"{m.module}.{m.name}" for m in hard))
    return f"{len(res.uses)} symbol uses resolve"


def s_import_core():
    failed = []
    for p in sorted((ENGINE / "core").glob("*.py")):
        mod = p.stem
        if mod.startswith("_") or mod in SKIP_IMPORT:
            continue
        try:
            importlib.import_module(f"core.{mod}")
        except Exception as e:  # noqa: BLE001
            failed.append(f"{mod}: {type(e).__name__}: {e}")
    if failed:
        raise AssertionError("; ".join(failed)[:300])
    return "all non-UI core modules import"


def s_activate():
    import linuxport
    linuxport.activate()
    from core import (games, gpu, dlss, pe, optiscaler, installer, diagnose, openxr, prefs,
                      vulkan, dxvk, wincrash)
    from linuxport import (archive as larch, linux_gpu, games as lgames, pins, policy, state,
                           pe as lpe, openxr as lxr, vulkan as lv, dxvk as ldxvk, crash as lcrash)
    checks = {
        "vulkan.install_layer": vulkan.install_layer is lv.install_layer,
        "vulkan.registrations": vulkan.registrations is lv.registrations,
        "prefs.drop_vulkan_game (per prefix)": prefs.drop_vulkan_game is lv.drop_vulkan_game,
        "optiscaler.extract_7z": optiscaler.extract_7z is larch.extract_7z,
        "installer.install (prefix context)": installer.install is lv._install,
        "diagnose.analyse (prefix context)": diagnose.analyse is lv._analyse,
        "dxvk.install": dxvk.install is ldxvk.install,
        "wincrash.last_crash": wincrash.last_crash is lcrash.last_crash,
        "gpu.detect": gpu.detect is linux_gpu.detect,
        "gpu.driver_version": gpu.driver_version is linux_gpu.driver_version,
        "gpu.driver_at_least": gpu.driver_at_least is linux_gpu.driver_at_least,
        "openxr.existing_registration": openxr.existing_registration is lxr.existing_registration,
        "openxr.install_layer": openxr.install_layer is lxr.install_layer,
        "games._steam_root": games._steam_root is lgames.steam_root,
        "games.scan_steam": games.scan_steam is lgames.scan_steam,
        "dlss.detect": dlss.detect is policy.detect,
        "dlss._ours": dlss._ours is state._ours,
        "installer._previous_manifest": installer._previous_manifest is state._previous_manifest,
        "diagnose._manifest": diagnose._manifest is state._diag_manifest,
        "optiscaler.resolve": optiscaler.resolve is pins.resolve,
        "pe.detect_api": pe.detect_api is lpe.detect_api,
    }
    bad = [k for k, v in checks.items() if not v]
    if bad:
        raise AssertionError("patch not applied: " + ", ".join(bad))
    return f"{len(checks)} patches in place"


def s_paths():
    from core import diagnose, library, log, net, prefs, profiles, sources
    from linuxport import paths
    expected = {
        "net.CACHE": (net.CACHE, paths.CACHE / "cache"),
        "sources._API_CACHE": (sources._API_CACHE, paths.CACHE / "api-cache"),
        "prefs.FILE": (prefs.FILE, paths.CONFIG / "settings.json"),
        "profiles.DIR": (profiles.DIR, paths.CONFIG / "profiles"),
        "library.FILE": (library.FILE, paths.CONFIG / "library.json"),
        "log.DIR": (log.DIR, paths.STATE),
        "diagnose.model.STANDALONE_LOG": (diagnose.model.STANDALONE_LOG,
                                         paths.STATE / "standalone-dlssnr.log"),
    }
    wrong = [k for k, (got, want) in expected.items() if Path(got) != want]
    if wrong:
        raise AssertionError("not redirected: " + ", ".join(wrong))
    return f"{len(expected)} paths under {paths.CACHE.parent}"


def s_gpu():
    from core import gpu
    name, sm = gpu.detect()
    if name is None:
        raise SkipStep("no nvidia-smi on this machine")
    return f"{name} sm={sm} driver={gpu.driver_version()}"


def s_scan():
    from linuxport import games as lgames
    gs = lgames.scan_all()
    return f"{len(gs)} game(s) (Steam + remembered folders)"


def _fixture(tmp: Path):
    """A fake game: a folder with an .exe name the core can reason about."""
    from core import games
    folder = tmp / "Fixture Game"
    (folder / "Binaries" / "Win64").mkdir(parents=True)
    exe = folder / "Binaries" / "Win64" / "Fixture-Win64-Shipping.exe"
    # Minimal MZ header so pe.* returns "unknown" instead of raising.
    exe.write_bytes(b"MZ" + b"\0" * 62)
    g = games.Game(name="Fixture Game", folder=folder, exe=exe, source="Manual")
    g.bitness = 64
    g.api, g.api_why = "DX12", "fixture"
    return g


def s_detect_and_plan():
    from core import dlss, installer
    with tempfile.TemporaryDirectory(prefix="dlss5-smoke-") as tmp:
        g = _fixture(Path(tmp))
        s = dlss.detect(g.install_dir, g.folder, g.api, g.bitness, sm=120)
        assert s.recommended in s.options, (s.recommended, s.options)
        opt = installer.Options(path=s.recommended, native_dlss=s.native_dlss, upscaler=s.upscaler)
        plan = installer.plan(g, opt)
        assert plan, "empty plan"
        pv = installer.preview(g, opt)
        lines = installer.preview_lines(pv)
        assert lines, "empty preview"
        return f"recommended={s.recommended} plan={len(plan)} steps preview={len(lines)} lines"


def s_proton_layer():
    from linuxport import proton
    with tempfile.TemporaryDirectory(prefix="dlss5-smoke-") as tmp:
        g = _fixture(Path(tmp))
        entries = proton.override_entries(g, "dxgi.dll", "native")
        assert entries and entries[0] == "dxgi=n,b", entries
        d3d11 = _fixture(Path(tmp) / "b")
        d3d11.api = "DX11"
        entries11 = proton.override_entries(d3d11, "winmm.dll", "optiscaler")
        assert "d3d12=n,b" in entries11 and "d3d12core=n,b" in entries11, entries11
        line = proton.with_indicator('WINEDLLOVERRIDES="dxgi=n,b" %command%', True)
        assert line.startswith("PROTON_DLSS_INDICATOR=1 "), line
        assert proton.indicator_present(line) and not proton.indicator_present(proton.with_indicator(line, False))
        return "override entries, indicator toggling"


def s_gui():
    try:
        from PySide6 import QtWidgets
    except ImportError as e:
        raise SkipStep(f"PySide6 not installed: {e}")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import dlss5_gui_linux as gui
    from core import sources
    from linuxport import features
    # No network in a smoke: the catalog and the community feed answer at once.
    sources.rhi_catalog = lambda force=False: {}
    features.community_lines = lambda g, route: []
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    w = gui.App()
    for page in (0, 1, 2):
        w._show(page)
        app.processEvents()
    from core import dlss
    with tempfile.TemporaryDirectory(prefix="dlss5-smoke-") as tmp:
        g = _fixture(Path(tmp))
        w.game = g
        w.support = dlss.detect(g.install_dir, g.folder, g.api, g.bitness, sm=120)
        w.all_games = [g]
        w._show(3)
        w._enter_install()
        app.processEvents()
        routes = []
        for i in range(w.cb_route.count()):
            w.cb_route.setCurrentIndex(i)
            app.processEvents()
            opts = w._opts()
            assert opts.path == w.cb_route.itemData(i), (opts.path, w.cb_route.itemData(i))
            assert opts.vr is False, "VR must stay off under Proton"
            routes.append(opts.path)
        assert routes, "GUI offered no route"
        # The 1.7.2-1.8.0 rows: OptiScaler line, ray reconstruction, overlay key, aim-for-fps.
        assert w.cb_optibuild.currentData() == w.cb_optibuild.itemData(w.cb_optibuild.currentIndex())
        assert w._opts().opti_build == w.cb_optibuild.currentData()
        w.sp_aim.setValue(60)
        assert w.sp_aim.value() == 60
        assert w.cb_overlaykey.count() > 3 and w.cb_dlssd.itemText(0).startswith("keep")
        assert not w.ck_vr.isEnabled()
        w._diagnose()                       # no logs: must not raise, share stays disabled
        assert not w.buttons["share the result"].isEnabled()
        dlg = gui.ReportDialog(w, "Fixture Game")
        assert not dlg.ok.isEnabled()
        dlg.started.button(0).setChecked(True); dlg.happened.setPlainText("nothing happened")
        app.processEvents()
        assert dlg.ok.isEnabled()
        # The catalog and community workers may still be downloading; closing
        # must wait for them (closeEvent does) or Qt aborts the process.
        w.close()
        alive = [t for t in w._threads if t.isRunning()]
        assert not alive, f"{len(alive)} worker(s) survived closeEvent"
        app.processEvents()
    return f"three pages shown, _opts() for {len(routes)} route(s): {', '.join(routes)}; new rows present; workers joined"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--gui", action="store_true", help="also build the PySide6 window offscreen")
    ap.add_argument("--all", action="store_true",
                    help="run the Linux-only steps on this OS too (the layer is pure Python; "
                         "only nvidia-smi, Steam and Proton are absent)")
    args = ap.parse_args()
    global FORCE_ALL
    FORCE_ALL = args.all

    step("shim symbols (AST)", s_check_shims)
    step("import core modules", s_import_core, linux_only=True)
    step("linuxport.activate()", s_activate, linux_only=True)
    step("XDG paths", s_paths, linux_only=True)
    step("gpu.detect via nvidia-smi", s_gpu, linux_only=True)
    step("scan_all()", s_scan, linux_only=True)
    step("dlss.detect + plan + preview", s_detect_and_plan, linux_only=True)
    step("proton overrides + indicator", s_proton_layer, linux_only=True)
    if args.gui:
        step("PySide6 window offscreen", s_gui, linux_only=True)

    fails = sum(1 for s, _, _ in RESULTS if s == "FAIL")
    print(f"\n{len(RESULTS)} steps, {fails} failed", flush=True)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())

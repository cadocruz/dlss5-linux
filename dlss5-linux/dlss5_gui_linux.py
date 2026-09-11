#!/usr/bin/env python3
"""DLSS 5 on Proton -- PySide6 port of DLSS5-Autopilot's three-page wizard.

Same engine as dlss5_linux.py (upstream core/ + linuxport/ shims). The look
follows AESTHETIC.md: near-black, one amber accent, lowercase, bracketed links.
"""
from __future__ import annotations

import sys
import traceback
from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt

sys.path.insert(0, str(Path(__file__).resolve().parent))
import linuxport  # noqa: E402
linuxport.activate()
from core import components, diagnose, dlss, feedcfg, games, gpu, installer, optiscaler, pe, reshade_ini, sources  # noqa: E402
from linuxport import games as lgames, pins, proton, tuning, verify as lverify, vklayer  # noqa: E402

# --- palette (AESTHETIC.md) -------------------------------------------------
BG, RAIL, PANEL, FIELD = "#0b0c0e", "#08090a", "#0e1013", "#121519"
LINE, EDGE, TXT, BODY, DIM, FAINT = "#1c1f24", "#2a2e35", "#e6e8ea", "#b9bcc2", "#5c6069", "#454952"
AMBER, GREEN, RUST, RED = "#d8a657", "#6f9f6f", "#b07a3c", "#c96a5a"
MONO = "Cascadia Mono, Consolas, DejaVu Sans Mono, monospace"

STYLE = f"""
QMainWindow, QWidget {{ background: {BG}; color: {BODY}; font-size: 10pt; }}
QFrame#rail {{ background: {RAIL}; border-right: 1px solid {LINE}; }}
QFrame#card {{ background: {PANEL}; border: 1px solid {LINE}; }}
QFrame#line {{ background: {LINE}; max-height: 1px; min-height: 1px; }}
QLabel {{ background: transparent; }}
QLabel#h1 {{ color: {TXT}; font-size: 15pt; }}
QLabel#dim {{ color: {DIM}; font-size: 9pt; }}
QLabel#faint {{ color: {FAINT}; font-size: 8pt; }}
QLabel#rust {{ color: {RUST}; }}
QLabel#amber {{ color: {AMBER}; }}
QLabel#link {{ color: {DIM}; font-size: 8pt; }}
QLabel#link:hover {{ color: {AMBER}; }}
QPushButton {{ background: {BG}; color: {BODY}; border: 1px solid {EDGE}; padding: 6px 14px; }}
QPushButton:hover {{ background: {FIELD}; }}
QPushButton:disabled {{ color: {FAINT}; border-color: {LINE}; }}
QPushButton[accent="true"] {{ background: {AMBER}; color: {BG}; font-weight: bold; border: 0; padding: 8px 22px; }}
QPushButton[accent="true"]:hover {{ background: #e8bd7a; }}
QPushButton[accent="true"]:disabled {{ background: {FIELD}; color: {FAINT}; }}
QComboBox, QLineEdit {{ background: {FIELD}; color: {TXT}; border: 1px solid {LINE}; padding: 4px 8px; }}
QComboBox QAbstractItemView {{ background: {PANEL}; color: {BODY}; selection-background-color: {AMBER}; selection-color: {BG}; }}
QRadioButton, QCheckBox {{ color: {TXT}; background: transparent; }}
QTreeWidget {{ background: {PANEL}; color: {BODY}; border: 1px solid {LINE}; alternate-background-color: {PANEL}; }}
QTreeWidget::item {{ height: 26px; }}
QTreeWidget::item:selected {{ background: {AMBER}; color: {BG}; }}
QHeaderView::section {{ background: {BG}; color: {DIM}; border: 0; border-bottom: 1px solid {LINE}; padding: 4px; font-size: 9pt; }}
QPlainTextEdit {{ background: {PANEL}; color: {BODY}; border: 1px solid {LINE}; font-family: {MONO}; font-size: 9pt; }}
QProgressBar {{ background: {FIELD}; border: 0; max-height: 4px; }}
QProgressBar::chunk {{ background: {AMBER}; }}
QSlider::groove:horizontal {{ background: #7a5a2c; height: 4px; }}
QSlider::handle:horizontal {{ background: {AMBER}; width: 12px; margin: -5px 0; }}
QSlider::handle:horizontal:hover {{ background: #f0b25a; }}
QScrollBar:vertical {{ background: {BG}; width: 8px; }} QScrollBar::handle:vertical {{ background: {EDGE}; }}
"""

STEPS = (("architecture", "what to install for"), ("game", "pick from your library"), ("install", "settings and go"))
OUTLOOK = {installer.STABLE: "reliable", installer.BETA: "beta", installer.EXPERIMENTAL: "experimental"}
MARK = {"ok": ("[ok]  ", GREEN), "warn": ("[!!]  ", RUST), "bad": ("[fail]", RED), "info": ("[--]  ", BODY)}
OPTI_CHOICES = ("default - y4my4m nightly 2026-09-06: proven here on ff7 rebirth, 007, horizon, ffxiv",
                "fallback - dagherbou v0.1.2, the last pre-nightly build that ran under proton",
                "latest - whatever dagherbou publishes now (v0.2.x deadlocked here)",
                "pin a release tag...", "use a local .zip...")


# --- worker -----------------------------------------------------------------
class Worker(QtCore.QThread):
    step = QtCore.Signal(int, int, str)
    prog = QtCore.Signal(int, str)
    line = QtCore.Signal(str, str)
    done = QtCore.Signal(object)
    fail = QtCore.Signal(str)

    def __init__(self, fn, *args, parent=None):
        super().__init__(parent)
        self.fn, self.args = fn, args

    def run(self) -> None:
        try:
            self.done.emit(self.fn(*self.args))
        except Exception:
            self.fail.emit(traceback.format_exc())


def card(parent=None) -> tuple[QtWidgets.QFrame, QtWidgets.QVBoxLayout]:
    c = QtWidgets.QFrame(parent); c.setObjectName("card")
    lay = QtWidgets.QVBoxLayout(c); lay.setContentsMargins(16, 14, 16, 14); lay.setSpacing(6)
    return c, lay


def label(text: str, kind: str = "") -> QtWidgets.QLabel:
    w = QtWidgets.QLabel(text)
    if kind:
        w.setObjectName(kind)
    w.setWordWrap(True)
    return w


def hline() -> QtWidgets.QFrame:
    f = QtWidgets.QFrame(); f.setObjectName("line"); return f


# --- main window --------------------------------------------------------------
class App(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("dlss5 linux")
        self.resize(1060, 830)
        self.setStyleSheet(STYLE)
        self.step = 1
        self.arch = 64
        self.all_games: list[games.Game] = []
        self.game: games.Game | None = None
        self.support: dlss.Support | None = None
        self.route_fit: dict[str, tuple[bool, str]] = {}
        self.route = dlss.NATIVE
        self.catalog: dict = {}
        self.feeder_tags: list[str] = []
        self.renodx_local: Path | None = None
        self.worker: Worker | None = None
        self.gpu_name, self.sm = gpu.detect()
        self._build()
        self._show(1)

    # ---------------------------------------------------------------- frame
    def _build(self) -> None:
        root = QtWidgets.QWidget(); self.setCentralWidget(root)
        h = QtWidgets.QHBoxLayout(root); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(0)

        rail = QtWidgets.QFrame(); rail.setObjectName("rail"); rail.setFixedWidth(236)
        rl = QtWidgets.QVBoxLayout(rail); rl.setContentsMargins(20, 24, 20, 18); rl.setSpacing(0)
        brand = QtWidgets.QLabel(f'<span style="color:{AMBER}">dlss5</span><br><span style="color:{DIM}">linux</span>')
        brand.setFont(QtGui.QFont("Cascadia Mono", 16)); brand.setTextFormat(Qt.RichText)
        rl.addWidget(brand); rl.addSpacing(22)
        self.rail_rows = []
        for i, (title, sub) in enumerate(STEPS, start=1):
            row = QtWidgets.QFrame(); row.setCursor(Qt.PointingHandCursor)
            rh = QtWidgets.QHBoxLayout(row); rh.setContentsMargins(0, 9, 12, 9); rh.setSpacing(10)
            marker = QtWidgets.QFrame(); marker.setFixedWidth(2)
            mark = QtWidgets.QLabel("[ ]"); t1 = QtWidgets.QLabel(title); t2 = QtWidgets.QLabel(sub); t2.setObjectName("faint")
            box = QtWidgets.QVBoxLayout(); box.setSpacing(0); box.addWidget(t1); box.addWidget(t2)
            rh.addWidget(marker); rh.addSpacing(16); rh.addWidget(mark); rh.addLayout(box, 1)
            row.mousePressEvent = (lambda e, n=i: self._show(n) if n < self.step or (n == 2 and self.all_games) else None)
            rl.addWidget(row)
            self.rail_rows.append(dict(n=i, row=row, marker=marker, mark=mark, t1=t1, t2=t2))
        rl.addStretch(1)
        self.gpulbl = label(f"{self.gpu_name or 'no nvidia gpu'}\n{gpu.label(self.sm)} · driver {gpu.driver_version() or '?'}", "faint")
        self.protonlbl = label("", "faint")
        rl.addWidget(self.gpulbl); rl.addSpacing(6); rl.addWidget(self.protonlbl); rl.addSpacing(6)
        rl.addWidget(label("port of dlss5-autopilot v1.7.1", "faint")); rl.addSpacing(4)
        for text, fn in (("[ set prefix ]", self._ask_prefix), ("[ open log folder ]", self._open_logs), ("[ how it works ]", self._how)):
            l = label(text, "link"); l.setCursor(Qt.PointingHandCursor); l.mousePressEvent = lambda e, f=fn: f()
            rl.addWidget(l); rl.addSpacing(2)
        h.addWidget(rail)

        right = QtWidgets.QWidget(); rv = QtWidgets.QVBoxLayout(right); rv.setContentsMargins(28, 20, 28, 14); rv.setSpacing(0)
        self.stack = QtWidgets.QStackedWidget()
        self.stack.addWidget(self._page_arch()); self.stack.addWidget(self._page_games()); self.stack.addWidget(self._page_install())
        rv.addWidget(self.stack, 1); rv.addSpacing(10); rv.addWidget(hline()); rv.addSpacing(14)
        bar = QtWidgets.QHBoxLayout()
        self.btn_back = QtWidgets.QPushButton("back"); self.btn_back.clicked.connect(lambda: self._show(self.step - 1))
        self.btn_next = QtWidgets.QPushButton("scan games"); self.btn_next.setProperty("accent", True); self.btn_next.clicked.connect(self._next)
        bar.addWidget(self.btn_back); bar.addStretch(1); bar.addWidget(self.btn_next)
        rv.addLayout(bar)
        h.addWidget(right, 1)

    def _paint_rail(self) -> None:
        for e in self.rail_rows:
            active, done = e["n"] == self.step, e["n"] < self.step
            bg = PANEL if active else RAIL
            e["row"].setStyleSheet(f"background:{bg};")
            e["marker"].setStyleSheet(f"background:{AMBER if active else bg};")
            e["mark"].setText("[x]" if done else ("[>]" if active else "[ ]"))
            e["mark"].setStyleSheet(f"color:{GREEN if done else (AMBER if active else FAINT)};")
            e["t1"].setStyleSheet(f"color:{TXT if active else (BODY if done else DIM)};")

    def _show(self, step: int) -> None:
        if step < 1 or step > 3:
            return
        self.step = step
        self.stack.setCurrentIndex(step - 1)
        self._paint_rail()
        self.btn_back.setEnabled(step > 1)
        self.btn_next.setText(("scan games", "continue", "INSTALL")[step - 1])
        self.btn_next.setEnabled(step != 2 or self.game is not None)

    def _next(self) -> None:
        if self.worker and self.worker.isRunning():
            return
        if self.step == 1:
            self._show(2)
            if not self.all_games:
                self._scan()
            else:
                self._fill()
        elif self.step == 2:
            if self.game:
                self._show(3); self._enter_install()
        else:
            self._install()

    # ---------------------------------------------------------------- page 1
    def _page_arch(self) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget(); v = QtWidgets.QVBoxLayout(w); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(12)
        v.addWidget(label("what are you installing for?", "h1"))
        v.addWidget(label("not sure if a game is 32- or 64-bit? leave it on 64-bit - almost everything is.", "dim"))
        c, cl = card(); rb = QtWidgets.QHBoxLayout(); rb.setSpacing(24)
        self.rb64 = QtWidgets.QRadioButton("64-bit  (recommended)"); self.rb32 = QtWidgets.QRadioButton("32-bit")
        self.rb64.setChecked(True); rb.addWidget(self.rb64); rb.addWidget(self.rb32); rb.addStretch(1); cl.addLayout(rb); v.addWidget(c)
        c2, cl2 = card()
        cl2.addWidget(label("!! before you get your hopes up", "rust"))
        cl2.addWidget(label(
            "dlss5 works reliably on 64-bit games with their own dlss, run through proton. optiscaler (the nightly "
            "build this tool pins) hooks the game's dlss, reports 'dlss: true' and runs neural rendering on it - "
            "directx 12 directly, directx 11 through its d3d12 bridge (final fantasy xiv). the native route is the "
            "alternative that leaves the game's dlss and frame generation untouched. games with no dlss: directx 11 "
            "goes through the feeder (always dlaa, dreamfall works); directx 12 without dlss has no working route "
            "under proton yet - the feeder's create faults in vkd3d. 32-bit, opengl and directx 9 often fail. "
            "anti-cheat games: don't.", "dim"))
        v.addWidget(c2); v.addStretch(1)
        return w

    # ---------------------------------------------------------------- page 2
    def _page_games(self) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget(); v = QtWidgets.QVBoxLayout(w); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(10)
        top = QtWidgets.QHBoxLayout(); top.addWidget(label("pick a game", "h1")); top.addStretch(1)
        self.only_installed = QtWidgets.QCheckBox("installed only"); self.only_installed.toggled.connect(self._fill)
        for text, fn in (("choose folder", self._pick_folder), ("rescan", self._scan), ("uninstall", self._uninstall)):
            b = QtWidgets.QPushButton(text); b.clicked.connect(fn); top.addWidget(b)
            if text == "uninstall": self.btn_rm2 = b; b.setEnabled(False)
        top.addWidget(self.only_installed); v.addLayout(top)
        srow = QtWidgets.QHBoxLayout(); srow.addWidget(label("search", "dim"))
        self.search = QtWidgets.QLineEdit(); self.search.textChanged.connect(self._fill); srow.addWidget(self.search, 1); v.addLayout(srow)
        self.scanlbl = label("", "dim"); v.addWidget(self.scanlbl)
        self.tree = QtWidgets.QTreeWidget(); self.tree.setRootIsDecorated(False); self.tree.setAlternatingRowColors(False)
        self.tree.setHeaderLabels(["  game", "source", "arch", "api", "route", "outlook", "status"])
        for i, wd in enumerate((250, 76, 62, 80, 74, 96, 92)): self.tree.setColumnWidth(i, wd)
        self.tree.itemSelectionChanged.connect(self._on_pick); self.tree.itemDoubleClicked.connect(lambda *_: self._next())
        v.addWidget(self.tree, 1)
        c, cl = card(); self.detail = label("select a game for details", "dim"); self.detail.setFont(QtGui.QFont("Cascadia Mono", 9)); cl.addWidget(self.detail); v.addWidget(c)
        return w

    def _scan(self) -> None:
        self.scanlbl.setText("scanning your steam library...")
        def work():
            out = []
            for g in lgames.scan_all():
                exes = pe.find_game_exes(g.folder)
                if not exes: continue
                g.exe, g.candidates = exes[0], exes
                try:
                    g.bitness = pe.exe_bitness(g.exe); g.api, g.api_why = pe.detect_api(g.exe)
                except Exception as e:
                    g.error = str(e)[:80]
                out.append(g)
            return out
        self._run(work, self._scanned)

    def _scanned(self, gs) -> None:
        self.all_games = sorted(gs, key=lambda g: g.name.lower()); self._fill()

    def _fill(self) -> None:
        self.tree.clear(); q = self.search.text().lower(); n = 0
        for i, g in enumerate(self.all_games):
            if q and q not in g.name.lower(): continue
            if self.arch_filter() and g.bitness and g.bitness != self.arch_filter(): continue
            ok, why = installer.check_supported(g)
            state, sdetail = lgames.install_state(g)
            installed = state == "installed"
            if self.only_installed.isChecked() and not state: continue
            if ok:
                sup = dlss.detect(g.install_dir, g.folder, g.api, g.bitness, sm=self.sm)
                level, _ = installer.reliability(g, sup.recommended, "" if sup.native_dlss else sup.upscaler)
                route, outlook = sup.recommended, OUTLOOK.get(level, level)
            else:
                route, outlook, level = "-", "unsupported", installer.EXPERIMENTAL
            status = "installed" if installed else (f"found: {sdetail}" if state == "foreign" else "")
            it = QtWidgets.QTreeWidgetItem(["  " + g.name, g.source.lower(), g.bit_label, g.api, route, outlook, status])
            it.setData(0, Qt.UserRole, i)
            colour = None
            if not ok: colour = RED
            elif installed: colour = GREEN
            elif state == "foreign": colour = AMBER
            elif level == installer.EXPERIMENTAL: colour = RUST
            if colour:
                for c in range(7): it.setForeground(c, QtGui.QColor(colour))
            self.tree.addTopLevelItem(it); n += 1
        self.scanlbl.setText(f"{n} game(s)" + (f" of {len(self.all_games)}" if n != len(self.all_games) else ""))

    def arch_filter(self) -> int | None:
        return 32 if self.rb32.isChecked() else None

    def _on_pick(self) -> None:
        items = self.tree.selectedItems()
        if not items:
            return
        g = self.all_games[items[0].data(0, Qt.UserRole)]; self.game = g
        ok, why = installer.check_supported(g)
        lines = [f"exe    {g.exe.relative_to(g.folder) if g.exe else '?'}", f"arch   {g.bit_label}  api {g.api}  ({g.api_why})"]
        if ok:
            self.support = sup = dlss.detect(g.install_dir, g.folder, g.api, g.bitness, sm=self.sm)
            level, why_rel = installer.reliability(g, sup.recommended, "" if sup.native_dlss else sup.upscaler)
            lines.append(f"route  {dlss.LABELS[sup.recommended]}  [{level}]")
            lines.append(f"dlss   {'yes: ' + ', '.join(sup.evidence[:2]) if sup.native_dlss else 'none' + (' (ships ' + dlss.UPSCALER_NAMES[sup.upscaler] + ')' if sup.upscaler else '')}")
            lines.append(f"proton {proton.prefix_for(g) or 'no prefix yet - run the game once under proton'}")
            lines.append(f"note   {why_rel}")
        else:
            lines.append(f"BLOCK  {why}")
        self.detail.setText("\n".join(lines))
        self.btn_next.setEnabled(ok); self.btn_rm2.setEnabled(bool(installer._previous_manifest(g.install_dir)))

    def _pick_folder(self) -> None:
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "game folder")
        if not d: return
        g = games.Game(name=Path(d).name, folder=Path(d)); exes = pe.find_game_exes(g.folder)
        if not exes:
            QtWidgets.QMessageBox.warning(self, "no exe", "no game executable found in that folder"); return
        g.exe, g.candidates = exes[0], exes; g.bitness = pe.exe_bitness(g.exe); g.api, g.api_why = pe.detect_api(g.exe)
        lgames.remember_folder(g.folder)
        if proton.prefix_for(g) is None:
            self._ask_prefix(g)
        self.all_games.insert(0, g); self._fill(); self.tree.setCurrentItem(self.tree.topLevelItem(0))

    def _ask_prefix(self, g=None) -> None:
        g = g or self.game
        if not g: return
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "wine prefix for this game (the folder holding drive_c) - cancel to skip", str(Path.home()))
        if d:
            proton.set_prefix(g, Path(d))
            if self.game is g:
                self._enter_install() if self.step == 3 else self._on_pick()

    # ---------------------------------------------------------------- page 3
    def _page_install(self) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget(); v = QtWidgets.QVBoxLayout(w); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(10)
        self.gamelbl = label("", "h1"); v.addWidget(self.gamelbl)
        c, cl = card(); grid = QtWidgets.QGridLayout(); grid.setHorizontalSpacing(14); grid.setVerticalSpacing(5); cl.addLayout(grid)
        self.rows: dict[str, tuple[QtWidgets.QWidget, QtWidgets.QWidget, QtWidgets.QWidget | None]] = {}

        def row(r, key, text, widget, hint=None, amber=False):
            l = label(text, "amber" if amber else "dim"); grid.addWidget(l, r, 0); grid.addWidget(widget, r, 1)
            if hint is not None: grid.addWidget(hint, r, 2)
            self.rows[key] = (l, widget, hint)

        self.cb_exe = QtWidgets.QComboBox(); row(0, "exe", "target exe", self.cb_exe, amber=True)
        self.cb_route = QtWidgets.QComboBox(); self.cb_route.currentIndexChanged.connect(self._on_route); row(1, "route", "loads as", self.cb_route)
        self.routelbl = label("", "dim"); grid.addWidget(self.routelbl, 2, 0, 1, 3)
        self.cb_prov = QtWidgets.QComboBox(); self.cb_prov.addItems([v[0] for v in reshade_ini.PROVIDERS.values()]); row(3, "prov", "motion vectors", self.cb_prov)
        self.cb_proxy = QtWidgets.QComboBox(); self.cb_proxy.addItems(["auto - pick a free name"] + list(optiscaler.PROXY_NAMES)); row(4, "proxy", "loads as", self.cb_proxy)
        self.cb_rproxy = QtWidgets.QComboBox(); self.cb_rproxy.addItems(["auto - from the graphics api"] + list(installer.RESHADE_PROXIES)); row(5, "rproxy", "reshade loads as", self.cb_rproxy)
        ar = QtWidgets.QHBoxLayout(); self.cb_renodx = QtWidgets.QComboBox(); self.cb_renodx.addItem("loading..."); ar.addWidget(self.cb_renodx, 1)
        b = QtWidgets.QPushButton("use my file"); b.clicked.connect(self._pick_renodx); ar.addWidget(b); arw = QtWidgets.QWidget(); arw.setLayout(ar); row(6, "renodx", "dlss 5 add-on", arw)
        self.cb_dlssnr = QtWidgets.QComboBox(); self.cb_dlssnr.addItem("auto - match my gpu"); row(7, "dlssnr", "nvngx_dlssnr", self.cb_dlssnr)
        dr = QtWidgets.QHBoxLayout(); self.cb_dlss = QtWidgets.QComboBox(); self.cb_dlss.addItem("loading..."); dr.addWidget(self.cb_dlss, 1)
        self.keep_dlss = QtWidgets.QCheckBox("keep the game's own"); self.keep_dlss.setChecked(True); dr.addWidget(self.keep_dlss); drw = QtWidgets.QWidget(); drw.setLayout(dr); row(8, "dlss", "nvngx_dlss", drw)
        self.cb_preset = QtWidgets.QComboBox(); self.cb_preset.addItems(list(feedcfg.PRESETS.values())); row(9, "preset", "dlss preset", self.cb_preset)
        self.cb_hdr = QtWidgets.QComboBox(); self.cb_hdr.addItems(list(feedcfg.HDR.values())); self.dlaalbl = label("", "faint"); row(10, "hdr", "hdr", self.cb_hdr, self.dlaalbl)
        self.cb_nrpreset = QtWidgets.QComboBox(); self.cb_nrpreset.addItems([f"{v}" + ("  -  the author's default" if k == 0 else "") for k, v in optiscaler.NR_PRESETS.items()]); row(11, "nrpreset", "model preset", self.cb_nrpreset)
        self.cb_nrstyle = QtWidgets.QComboBox(); self.cb_nrstyle.addItems(list(optiscaler.NR_STYLES.values())); self.nrhint = label("the rest is on the overlay (Insert)", "faint"); row(12, "nrstyle", "style", self.cb_nrstyle, self.nrhint)
        sw = QtWidgets.QHBoxLayout(); self.sc_work = QtWidgets.QSlider(Qt.Horizontal); self.sc_work.setRange(50, 100); self.sc_work.setValue(100); self.sc_work.valueChanged.connect(self._on_workres)
        self.workhint = label("", "dim"); sw.addWidget(self.sc_work, 2); sw.addWidget(self.workhint, 3); sww = QtWidgets.QWidget(); sww.setLayout(sw); row(13, "work", "work resolution", sww)
        self.cb_feederver = QtWidgets.QComboBox(); self.cb_feederver.addItems(["stable - newest release", "newest pre-release"]); row(14, "feederver", "feeder build", self.cb_feederver)
        self.cb_opti = QtWidgets.QComboBox(); self.cb_opti.addItems(OPTI_CHOICES); self.cb_opti.currentIndexChanged.connect(self._on_opti); self.opti_custom = ""; row(15, "opti", "optiscaler build", self.cb_opti)
        self.overridelbl = label("", "dim"); grid.addWidget(self.overridelbl, 16, 0, 1, 3)
        self.ck_indicator = QtWidgets.QCheckBox("show the dlss indicator on screen (PROTON_DLSS_INDICATOR=1: version, preset, resolution, frame-gen state)")
        grid.addWidget(self.ck_indicator, 18, 0, 1, 3)
        self.reswarn = label("!! set your screen resolution BEFORE turning neural rendering on - a resolution change with the feed live is the crash we see most", "rust"); grid.addWidget(self.reswarn, 17, 0, 1, 3)
        grid.setColumnStretch(1, 1); v.addWidget(c)

        self.pb = QtWidgets.QProgressBar(); self.pb.setRange(0, 100); self.pb.setTextVisible(False); v.addWidget(self.pb)
        self.pblbl = label("", "faint"); v.addWidget(self.pblbl)
        self.log = QtWidgets.QPlainTextEdit(); self.log.setReadOnly(True); v.addWidget(self.log, 1)
        act = QtWidgets.QHBoxLayout()
        for text, fn in (("did it work?", self._diagnose), ("uninstall", self._uninstall), ("check versions", self._check_components),
                         ("open folder", self._open_folder), ("what will happen?", self._preview), ("launch options", self._launch_options),
                         ("apply overrides", self._apply_launch_options), ("upgrade game dlss", self._upgrade_dlss), ("game notes", self._game_notes),
                         ("vk layer", self._vklayer)):
            b = QtWidgets.QPushButton(text); b.clicked.connect(fn); act.addWidget(b)
        act.addStretch(1); v.addLayout(act)
        return w

    def _enter_install(self) -> None:
        g = self.game; self.gamelbl.setText(g.name); self.log.clear()
        self.cb_exe.clear(); self.cb_exe.addItems([str(c.relative_to(g.folder)) for c in (g.candidates or [g.exe])])
        pfx = proton.prefix_for(g); bridge = proton.ngx_bridge_present(g)
        self.protonlbl.setText(f"proton: {'prefix ok' if pfx else 'no prefix'} · {'ngx bridge ok' if bridge else 'no ngx bridge'}")
        self.route_fit = {o: dlss.fit(o, g.api, self.support.native_dlss, self.sm, self.support.upscaler) for o in self.support.options}
        self.cb_route.blockSignals(True); self.cb_route.clear()
        for o in self.support.options:
            usable, note = self.route_fit[o]
            tail = "  <-  recommended for this game and card" if o == self.support.recommended else ""
            if not usable: tail = f"  (not for this pc: {note})"
            self.cb_route.addItem(dlss.LABELS[o] + tail, o)
        self.cb_route.blockSignals(False); self.cb_route.setCurrentIndex(self.support.options.index(self.support.recommended))
        self._apply_route(self.support.recommended)
        self._log(f"> {g.name}: {g.api} x{g.bitness}, {'dlss found' if self.support.native_dlss else 'no dlss'}", "head")
        self._log(f"  {self.support.reason}")
        if not self.catalog:
            self._run(sources.rhi_catalog, self._catalog_loaded)

    def _catalog_loaded(self, cat) -> None:
        self.catalog = cat or {}
        self._fill_addon_list(self.route == dlss.RENODX)
        self.cb_dlssnr.clear(); self.cb_dlssnr.addItem("auto - match my gpu")
        self.cb_dlssnr.addItems([e["label"] for e in gpu.order_dlssnr(self.catalog.get("dlssnr", []), self.sm)])
        self.cb_dlss.clear(); self.cb_dlss.addItem("auto - newest"); self.cb_dlss.addItems([e["label"] for e in self.catalog.get("dlss", [])])

    def _fill_addon_list(self, sf: bool) -> None:
        fam = "renodx_sf" if sf else "renodx"
        self.cb_renodx.clear(); self.cb_renodx.addItem("auto - newest from the catalog")
        for e in self.catalog.get(fam, []): self.cb_renodx.addItem(e["label"])
        work = Path(__file__).resolve().parents[1]
        for p in sorted(work.glob("*.addon64")):
            self.cb_renodx.addItem(f"[local] {p.name}", str(p))
        if self.renodx_local: self.cb_renodx.setCurrentText(f"[local] {self.renodx_local.name}")

    def _pick_renodx(self) -> None:
        f, _ = QtWidgets.QFileDialog.getOpenFileName(self, "renodx add-on", str(Path(__file__).resolve().parents[1]), "*.addon64")
        if f:
            self.renodx_local = Path(f); self.cb_renodx.addItem(f"[local] {Path(f).name}", f); self.cb_renodx.setCurrentIndex(self.cb_renodx.count() - 1)

    def _on_route(self, i: int) -> None:
        if i >= 0: self._apply_route(self.cb_route.itemData(i))

    def _apply_route(self, path: str) -> None:
        self.route = path
        usable, note = self.route_fit.get(path, (True, ""))
        text = dlss.BLURB[path]
        if not usable: text = f"NOT FOR THIS PC - {note}.\n{text}"
        elif note: text = f"{text}\n({note})"
        self.routelbl.setText(text); self.routelbl.setObjectName("rust" if not usable else "dim"); self.routelbl.setStyleSheet("")
        feeder, opti = path == dlss.FEEDER, path == dlss.OPTI
        self._fill_addon_list(path == dlss.RENODX)
        vis = {"prov": feeder, "proxy": opti, "rproxy": not (feeder or opti), "preset": not opti, "hdr": not opti,
               "nrpreset": opti, "nrstyle": opti, "feederver": feeder, "opti": opti, "renodx": path != dlss.UPSTREAM, "work": opti or feeder}
        for key, on in vis.items():
            for wdg in self.rows[key]:
                if wdg is not None: wdg.setVisible(on)
        self.reswarn.setVisible(feeder)
        self.dlaalbl.setText("the feeder path is always dlaa" if feeder else "the game's own dlss quality mode applies")
        self.sc_work.setRange(optiscaler.NR_SCALE_MIN if opti else 50, 100)
        self.sc_work.setValue(optiscaler.NR_SCALE_DEFAULT if opti else 100); self._on_workres()
        proxy = (self.cb_proxy.currentText().split(" ")[0] if opti and self.cb_proxy.currentIndex() else None) or \
                (optiscaler.suggest_proxy(self.game.install_dir) if opti else installer._proxy_name(self.game.api, ""))
        cur = proton.current_launch_options(self.game)
        self.ck_indicator.setChecked(proton.indicator_present(cur))
        need = proton.override_entries(self.game, proxy, path); missing = proton.missing_overrides(self.game, proxy, path)
        xl = proton.is_xlcore(self.game)
        where = "xivlauncher-rb's dll overrides" if xl else ("steam launch options" if proton.appid_for(self.game) else "the launcher's environment (lutris/heroic/umu)")
        if not missing:
            self.overridelbl.setText(f"{where} already carry {';'.join(need)}"); self.overridelbl.setObjectName("dim")
        else:
            extra = " (d3d12 + d3d12core: the d3d11 bridge needs vkd3d-proton's d3d12, and umu launchers skip proton's own wiring)" if any(m.startswith("d3d12") for m in missing) else ""
            self.overridelbl.setText(f"!! {where} need {';'.join(missing)} - without it nothing loads{extra}. 'launch options' below gives the exact line; 'apply overrides' writes it.")
            self.overridelbl.setObjectName("rust")
        self.overridelbl.setStyleSheet("")
        if self.game:
            level, why = installer.reliability(self.game, path, "" if self.support.native_dlss else self.support.upscaler)
            self._log(f"> route: {dlss.LABELS[path]}  [{level}]", "head"); self._log(f"  {why}")
            hint = vklayer.suggestion(self.game, self.support.native_dlss)
            if hint: self._log(f"  vk layer: {hint}")

    def _on_workres(self) -> None:
        v = self.sc_work.value(); opti = self.route == dlss.OPTI
        if opti:
            cost = round((v / 100) ** 2 * 100)
            self.workhint.setText("100% - full size; the pass costs about half your fps" if v == 100 else f"{v}% - about {cost}% of the full-size cost; the frame stays full detail")
        else:
            self.workhint.setText("100% - full quality" if v == 100 else (f"{v}% - a little faster" if v >= 75 else f"{v}% - faster, softer"))

    def _on_opti(self, i: int) -> None:
        if i == 3:
            t, ok = QtWidgets.QInputDialog.getText(self, "optiscaler release tag", "tag (e.g. v0.2.0-patch1):"); self.opti_custom = t.strip() if ok else ""
        elif i == 4:
            f, _ = QtWidgets.QFileDialog.getOpenFileName(self, "optiscaler zip", str(Path(__file__).resolve().parents[1]), "*.zip"); self.opti_custom = f
        else:
            self.opti_custom = ""

    def _opts(self) -> installer.Options:
        s = self.support
        val = self.cb_renodx.currentText(); local = self.cb_renodx.currentData() if val.startswith("[local]") else None
        clean = lambda t: None if (not t or t.startswith(("loading", "auto"))) else t
        feed, nr = {}, {}
        if self.route == dlss.FEEDER and self.sc_work.value() != 100: feed["work_resolution"] = self.sc_work.value()
        if self.cb_preset.currentIndex() > 0: feed["preset"] = list(feedcfg.PRESETS)[self.cb_preset.currentIndex()]
        if self.cb_hdr.currentIndex() > 0: feed["hdr"] = list(feedcfg.HDR)[self.cb_hdr.currentIndex()]
        if self.route == dlss.OPTI:
            nr["WorkingScale"] = round(self.sc_work.value() / 100, 2)
            if self.cb_nrpreset.currentIndex() > 0: nr["Preset"] = list(optiscaler.NR_PRESETS)[self.cb_nrpreset.currentIndex()]
            if self.cb_nrstyle.currentIndex() > 0: nr["Style"] = list(optiscaler.NR_STYLES)[self.cb_nrstyle.currentIndex()]
        choice = ("default", "fallback", "latest", self.opti_custom or "default", self.opti_custom or "default")[self.cb_opti.currentIndex()]
        pins.set_optiscaler(choice)
        return installer.Options(
            provider=list(reshade_ini.PROVIDERS)[self.cb_prov.currentIndex()], renodx=None if local else clean(val),
            renodx_local=Path(local) if local else None, dlssnr=clean(self.cb_dlssnr.currentText()), dlss=clean(self.cb_dlss.currentText()),
            keep_game_dlss=self.keep_dlss.isChecked(), feed=feed, nr=nr, feeder_prerelease=self.cb_feederver.currentIndex() == 1,
            path=self.route, native_dlss=s.native_dlss, upscaler=s.upscaler,
            opti_proxy="" if self.cb_proxy.currentIndex() == 0 else self.cb_proxy.currentText(),
            reshade_proxy="" if self.cb_rproxy.currentIndex() == 0 else self.cb_rproxy.currentText())

    # ---------------------------------------------------------------- actions
    def _log(self, text: str, tag: str = "") -> None:
        colour = {"ok": GREEN, "warn": RUST, "err": RED, "head": AMBER}.get(tag)
        esc = text.replace("&", "&amp;").replace("<", "&lt;")
        style = "white-space:pre" + (f";color:{colour}" if colour else "")
        self.log.appendHtml(f'<span style="{style}">{esc}</span>')

    def _run(self, fn, on_done, *args) -> None:
        self.worker = Worker(fn, *args); self.worker.done.connect(on_done)
        self.worker.fail.connect(lambda tb: self._log("!! " + tb.strip().splitlines()[-1], "err")); self.worker.start()

    def _preview(self) -> None:
        try:
            pv = installer.preview(self.game, self._opts())
        except Exception as e:
            self._log(f"!! preview failed: {e}", "err"); return
        self._log(""); self._log("=== what will happen ===", "head")
        for line in installer.preview_lines(pv):
            self._log(f"   {line}", "err" if line.startswith("cannot") else ("warn" if line.startswith("warning") else ""))
        self._log("   nothing is downloaded or written by this preview")

    def _install(self) -> None:
        g = self.game
        if proton.running(g):
            self._log(f"!! {g.exe.name} is running - close it first", "err"); return
        pv = installer.preview(g, self._opts())
        if pv.blockers:
            for b in pv.blockers: self._log(f"!! {b}", "err")
            return
        self._log(""); self._log(f"=== {g.name} ===", "head"); self.btn_next.setEnabled(False); self.pb.setValue(0)
        from linuxport import seed; seed.seed(log=lambda m: None)
        opt = self._opts()
        w = Worker(lambda: installer.install(g, opt, on_step=lambda i, n, name: w.step.emit(i, n, name),
                                              on_prog=lambda p, m: w.prog.emit(p, m), on_log=lambda t: w.line.emit(t, "")))
        w.step.connect(lambda i, n, name: (self._log(f"[{i+1}/{n}] {name}"), self.pb.setValue(int((i) * 100 / max(n, 1)))))
        w.prog.connect(lambda p, m: self.pblbl.setText(m)); w.line.connect(lambda t, tag: self._log("   " + t.rstrip(), tag))
        w.done.connect(self._installed); w.fail.connect(lambda tb: (self._log("!! " + tb.strip().splitlines()[-1], "err"), self.btn_next.setEnabled(True)))
        self.worker = w; w.start()

    def _installed(self, rep) -> None:
        self.pb.setValue(100); self.pblbl.setText(""); self.btn_next.setEnabled(True)
        self._log(f"> wrote {len(rep.written)} file(s)", "ok")
        for wn in rep.warnings: self._log(f"!! {wn}", "warn")
        for n in rep.notes: self._log(f"-- {n}")
        self._launch_options(); self._game_notes(); self._fill()

    def _proxy(self) -> str:
        g = self.game; opt = self._opts()
        if self.route == dlss.OPTI:
            return opt.opti_proxy or proton.installed_proxy(g) or optiscaler.suggest_proxy(g.install_dir)
        return installer._proxy_name(g.api, opt.reshade_proxy)

    def _vklayer(self) -> None:
        g = self.game; self._log(""); self._log("=== vk layer (out-of-process route) ===", "head")
        m = vklayer.manifest()
        self._log(f"  layer   : {'installed: ' + str(m) if m else 'not installed -- ' + vklayer.UPSTREAM + ' (examples/vklayer)'}", "" if m else "warn")
        self._log(f"  helper  : {'running' if vklayer.helper_running() else 'stopped -- dlssnr-helper start before the game'}",
                  "ok" if vklayer.helper_running() else "warn")
        digest, label = vklayer.runtime(); self._log(f"  runtime : {label}")
        hint = vklayer.suggestion(g, self.support.native_dlss) if self.support else None
        if hint: self._log(f"  fit     : {hint}")
        cur = proton.current_launch_options(g)
        self._log(f"  token   : {'present' if vklayer.enabled_in(cur) else 'absent'} -- launch options for this route:")
        self._log(f"     {vklayer.launch_line(g, True, self.ck_indicator.isChecked())}")
        self._log("  (the cli writes it while steam is closed: dlss5_linux.py launch-options <game> --vklayer --apply)")
        st = vklayer.shm_status()
        if st: self._log(f"  live    : frames={st.get('layer_frames', '0')} model_up={st.get('model_up', '0')} hdr_detected={st.get('hdr_detected', '0')}")
        self._log("  controls:")
        for c in vklayer.CONTROLS: self._log(f"   - {c}")

    def _launch_options(self) -> None:
        g = self.game; proxy = self._proxy()
        missing = proton.missing_overrides(g, proxy, self.route)
        need = proton.launch_options(g, proxy, self.ck_indicator.isChecked(), self.route)
        self._log(""); self._log("=== launch options ===", "head")
        self._log(f"   {'already set' if not missing else '!! SET THIS - required, or nothing loads: ' + ';'.join(missing)}", "ok" if not missing else "warn")
        for line in proton.launch_help(g, proxy, self.ck_indicator.isChecked(), self.route): self._log(f"   {line}")
        QtWidgets.QApplication.clipboard().setText(need); self._log("   (copied to the clipboard)")

    def _apply_launch_options(self) -> None:
        g = self.game; proxy = self._proxy()
        xl = proton.is_xlcore(g)
        self._log(""); self._log("=== apply overrides ===", "head")
        try:
            if xl:
                entries = proton.override_entries(g, proxy, self.route)
                backup = proton.set_launcher_overrides(g, entries); line = ";".join(entries); where = "xivlauncher-rb launcher.ini"
            else:
                line = proton.launch_options(g, proxy, self.ck_indicator.isChecked(), self.route)
                backup = proton.set_launch_options(g, line); where = "localconfig.vdf"
        except RuntimeError as e:
            self._log(f"!! not applied: {e}", "warn"); return
        self._log(f"> written to {where} (backup: {backup.name})", "ok"); self._log(f"   {line}")
        self._apply_route(self.route)

    def _upgrade_dlss(self) -> None:
        """Swap the game's own DLSS SR/RR/FG runtimes for the newest archive beside the tools."""
        import subprocess
        g = self.game
        if proton.running(g):
            self._log(f"!! {g.exe.name} is running - close it first", "err"); return
        tool = Path(__file__).resolve().parents[1] / "proton-tool" / "dlss5_proton.py"
        cmd = [sys.executable, str(tool), "dlls", str(g.exe or g.folder)]
        pfx = proton.prefix_for(g)
        if pfx and not proton.appid_for(g): cmd += ["--prefix", str(pfx)]
        self._log(""); self._log("=== upgrade the game's dlss runtimes ===", "head")
        self._log("   sr / rr / fg from the newest archive next to the tools; streamline is left alone (undo: dlls restore)")
        def work():
            return subprocess.run(cmd + ["install", "-y"], capture_output=True, text=True)
        def done(r):
            for line in (r.stdout + r.stderr).splitlines():
                if line.strip(): self._log("   " + line.rstrip()[:160], "warn" if "WARN" in line or "ERROR" in line else "")
            self._log("> done" if r.returncode == 0 else f"!! exit {r.returncode}", "ok" if r.returncode == 0 else "err")
        self._run(work, done)

    def _game_notes(self) -> None:
        g = self.game
        notes = tuning.game_notes(g.exe.name if g and g.exe else None)
        self._log(""); self._log("=== notes ===", "head")
        if not notes:
            self._log("   nothing game-specific on file. general: set the screen resolution before enabling neural rendering; "
                      "open the optiscaler overlay with insert; 'did it work?' reads the logs.")
        for n in notes: self._log(f"   - {n}")

    def _diagnose(self) -> None:
        g = self.game; self._log(""); self._log("=== did it work? ===", "head")
        for lvl, title, detail in lverify.run(g):
            if title == "verdict":
                self._log(f"> {detail}", "ok" if detail.startswith("Working") else "head"); continue
            mark, colour = MARK.get(lvl.lower(), ("[--]  ", BODY))
            self._log(f"{mark} {title}", {"ok": "ok", "warn": "warn", "bad": "err"}.get(lvl.lower(), ""))
            if detail: self._log(f"        {detail}")

    def _check_components(self) -> None:
        g = self.game
        if not installer._previous_manifest(g.install_dir):
            self._log("> nothing is installed in this folder yet", "warn"); return
        self._log(""); self._log("=== component versions ===", "head"); self._log("  asking each source for its current version...")
        self._run(components.check, self._show_components, g.install_dir)

    def _show_components(self, items) -> None:
        for it in items:
            if it.outdated: self._log(f"[!!]   {it.name}: {it.installed} -> {it.latest}", "warn")
            elif not it.latest: self._log(f"[--]   {it.name}: {it.installed}")
            else: self._log(f"[ok]   {it.name}: {it.installed}", "ok")
        self._log(f"> {components.summary(items)}")

    def _uninstall(self) -> None:
        g = self.game
        if not g: return
        if QtWidgets.QMessageBox.question(self, "uninstall", f"remove everything this tool wrote for {g.name}?") != QtWidgets.QMessageBox.Yes: return
        self._log(""); self._log("=== uninstalling ===", "head")
        self._run(lambda: installer.uninstall(g, on_log=lambda t: None), lambda rm: (self._log(f"> removed {len(rm)} item(s)", "ok"), self._fill()))

    def _open_folder(self) -> None:
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(self.game.install_dir)))

    def _open_logs(self) -> None:
        from linuxport import paths; QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(paths.STATE)))

    def _how(self) -> None:
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(Path(__file__).resolve().parent / "PORT_PLAN.md")))


def main() -> int:
    app = QtWidgets.QApplication(sys.argv); app.setStyle("Fusion")
    w = App(); w.show(); return app.exec()


if __name__ == "__main__":
    sys.exit(main())

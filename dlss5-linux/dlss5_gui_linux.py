#!/usr/bin/env python3
"""DLSS 5 on Proton -- PySide6 port of DLSS5-Autopilot's three-page wizard.

Same engine as dlss5_linux.py (upstream core/ + linuxport/ shims). The look
follows AESTHETIC.md: near-black, one amber accent, lowercase, bracketed links.
The window logic that upstream keeps in core/gui.py (library cache, driver
notes, ray-reconstruction swap, overlay key, aim-for-fps, community notes,
sharing and the bug report) lives in linuxport/features.py, headless.
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
from core import components, diagnose, dlss, feedcfg, games, gpu, installer, optiscaler, pe, reshade_ini, sources, update  # noqa: E402
from linuxport import features, games as lgames, paths, pins, proton, state as lstate, tuning, verify as lverify, vklayer  # noqa: E402


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
QComboBox, QLineEdit, QSpinBox, QPlainTextEdit#answer {{ background: {FIELD}; color: {TXT}; border: 1px solid {LINE}; padding: 4px 8px; }}
QComboBox:disabled, QSpinBox:disabled {{ color: {FAINT}; }}
QComboBox QAbstractItemView {{ background: {PANEL}; color: {BODY}; selection-background-color: {AMBER}; selection-color: {BG}; }}
QRadioButton, QCheckBox {{ color: {TXT}; background: transparent; }}
QCheckBox:disabled {{ color: {FAINT}; }}
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
QDialog {{ background: {BG}; }}
"""

STEPS = (("architecture", "what to install for"), ("game", "pick from your library"), ("install", "settings and go"))
OUTLOOK = {installer.STABLE: "reliable", installer.BETA: "beta", installer.EXPERIMENTAL: "experimental"}
MARK = {"ok": ("[ok]  ", GREEN), "warn": ("[!!]  ", RUST), "bad": ("[fail]", RED), "info": ("[--]  ", BODY)}
# How pins.py picks the OptiScaler archive; the LINE (dagherbou / y4my4my4m / wilsjo2) is the row below it.
OPTI_CHOICES = ("default - the local y4my4m nightly when present, else the newest release of the line below",
                "latest - the newest release of the line below, from upstream's resolver",
                "fallback - dagherbou v0.1.2, for bisecting only (lost the nvapi race here)",
                "pin a dagherbou release tag...", "use a local .zip...")
OPTI_CHOICE_KEYS = ("default", "latest", "fallback")
DLSSD_KEEP = "keep the game's own"


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


# --- the two questions a bug report cannot be read without (upstream reportui, in Qt) ---
class ReportDialog(QtWidgets.QDialog):
    def __init__(self, parent, game_name: str = ""):
        super().__init__(parent)
        self.setWindowTitle("report a bug"); self.setModal(True); self.answers: dict | None = None
        v = QtWidgets.QVBoxLayout(self); v.setSpacing(10)
        v.addWidget(label(f"two things the tool cannot know about {game_name or 'this game'}", "h1"))
        key, question, choices = features.REPORT_QUESTIONS[0]
        v.addWidget(label(question, "dim")); self.started = QtWidgets.QButtonGroup(self); row = QtWidgets.QHBoxLayout()
        for i, c in enumerate(choices):
            rb = QtWidgets.QRadioButton(c); self.started.addButton(rb, i); row.addWidget(rb)
        row.addStretch(1); v.addLayout(row)
        v.addWidget(label(features.REPORT_QUESTIONS[1][1], "dim"))
        self.happened = QtWidgets.QPlainTextEdit(); self.happened.setObjectName("answer"); self.happened.setFixedHeight(110); v.addWidget(self.happened)
        v.addWidget(label("the machine, the route, the folder and the logs are filled in for you. nothing is sent until you post it in the browser.", "faint"))
        bar = QtWidgets.QHBoxLayout(); bar.addStretch(1)
        cancel = QtWidgets.QPushButton("cancel"); cancel.clicked.connect(self.reject); bar.addWidget(cancel)
        self.ok = QtWidgets.QPushButton("open the report"); self.ok.setProperty("accent", True); self.ok.setEnabled(False); self.ok.clicked.connect(self._accept); bar.addWidget(self.ok)
        v.addLayout(bar)
        self.started.idToggled.connect(lambda *_: self._check()); self.happened.textChanged.connect(self._check)

    def _check(self) -> None:
        self.ok.setEnabled(self.started.checkedId() >= 0 and bool(self.happened.toPlainText().strip()))

    def _accept(self) -> None:
        choices = features.REPORT_QUESTIONS[0][2]
        self.answers = {"started": choices[self.started.checkedId()], "happened": self.happened.toPlainText().strip()}
        self.accept()


# --- main window --------------------------------------------------------------
class App(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("dlss5 linux")
        self.resize(1060, 900)
        self.setStyleSheet(STYLE)
        self.step = 1
        self.arch = 64
        self.all_games: list[games.Game] = []
        self._rows: dict[tuple[str, str], list] = {}
        self.game: games.Game | None = None
        self.support: dlss.Support | None = None
        self.route_fit: dict[str, tuple[bool, str]] = {}
        self.route = dlss.NATIVE
        self.catalog: dict = {}
        self.feeder_tags: list[str] = []
        self.renodx_local: Path | None = None
        self.worker: Worker | None = None
        self.side: Worker | None = None
        self._threads: set[Worker] = set()      # every live worker, joined on close
        self._last_diag = None
        self._tune = None
        self._noted: set[tuple[str, str]] = set()
        self.gpu_name, self.sm = gpu.detect()
        self._build()
        # The library is where the tool opens when there is one from last time
        # (upstream 1.8.0): no disks walked, the architecture filter one click away.
        if features.scan_on_start() and self._load_cached():
            self._show(2); self._fill()
        else:
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
            row.mousePressEvent = (lambda e, n=i: self._show(n) if n < self.step or n == 1 or (n == 2 and self.all_games) else None)
            rl.addWidget(row)
            self.rail_rows.append(dict(n=i, row=row, marker=marker, mark=mark, t1=t1, t2=t2))
        rl.addStretch(1)
        self.gpulbl = label(f"{self.gpu_name or 'no nvidia gpu'}\n{gpu.label(self.sm)} · driver {gpu.driver_version() or '?'}", "faint")
        self.protonlbl = label("", "faint")
        rl.addWidget(self.gpulbl); rl.addSpacing(6); rl.addWidget(self.protonlbl); rl.addSpacing(6)
        rl.addWidget(label(f"port of dlss5-autopilot v{update.VERSION} · linux {features.PORT_VERSION}", "faint")); rl.addSpacing(4)
        for text, fn in (("[ set prefix ]", self._ask_prefix), ("[ report a bug ]", self._report_bug),
                         ("[ open log folder ]", self._open_logs), ("[ how it works ]", self._how)):
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
        self.btn_next.setText(("scan games" if not self.all_games else "to the library", "continue", "INSTALL")[step - 1])
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
            "dlss5 works reliably on 64-bit games with their own dlss, run through proton. optiscaler (the y4my4my4m "
            "line this tool defaults to) hooks the game's dlss, reports 'dlss: true' and runs neural rendering on it - "
            "directx 12 directly, directx 11 through its d3d12 bridge (final fantasy xiv). the native route is the "
            "alternative that leaves the game's dlss and frame generation untouched. games with no dlss: directx 11 "
            "goes through the feeder (always dlaa, dreamfall works); directx 12 without dlss goes through the "
            "dlss5vklayer route instead (the feeder's create faults in vkd3d; the layer runs the model beside the "
            "game, nothing in the folder - ff7 remake works). 32-bit, opengl and directx 9 often fail. "
            "anti-cheat games: don't.", "dim"))
        v.addWidget(c2); v.addStretch(1)
        return w

    # ---------------------------------------------------------------- page 2
    def _page_games(self) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget(); v = QtWidgets.QVBoxLayout(w); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(10)
        top = QtWidgets.QHBoxLayout(); top.addWidget(label("pick a game", "h1")); top.addStretch(1)
        self.only_installed = QtWidgets.QCheckBox("installed only"); self.only_installed.toggled.connect(self._fill)
        self.ck_scanstart = QtWidgets.QCheckBox("scan library at start"); self.ck_scanstart.setChecked(features.scan_on_start())
        self.ck_scanstart.setToolTip("open on the library from last time, without walking the disks; 'rescan' always does the full walk")
        self.ck_scanstart.toggled.connect(features.set_scan_on_start)
        for text, fn in (("choose folder", self._pick_folder), ("rescan", self._scan), ("uninstall", self._uninstall)):
            b = QtWidgets.QPushButton(text); b.clicked.connect(fn); top.addWidget(b)
            if text == "uninstall": self.btn_rm2 = b; b.setEnabled(False)
        top.addWidget(self.only_installed); top.addWidget(self.ck_scanstart); v.addLayout(top)
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

    @staticmethod
    def _inspect(g: games.Game) -> games.Game:
        exes = pe.find_game_exes(g.folder)
        if not exes:
            return g
        g.exe, g.candidates = exes[0], exes
        try:
            g.bitness = pe.exe_bitness(g.exe); g.api, g.api_why = pe.detect_api(g.exe)
        except Exception as e:
            g.error = str(e)[:80]
        return g

    def _row_for(self, g: games.Game) -> list:
        """The compatibility row the list shows: [ok, why, route, level, state, detail].
        Walks the game folder, so it is computed on the worker and cached."""
        ok, why = installer.check_supported(g)
        state, sdetail = lgames.install_state(g)
        if ok:
            sup = dlss.detect(g.install_dir, g.folder, g.api, g.bitness, sm=self.sm)
            level, _ = installer.reliability(g, sup.recommended, "" if sup.native_dlss else sup.upscaler)
            return [True, why, sup.recommended, level, state, sdetail]
        return [False, why, "-", installer.EXPERIMENTAL, state, sdetail]

    @staticmethod
    def _key(g: games.Game) -> tuple[str, str]:
        return (str(g.folder), str(g.exe))

    def _scan(self) -> None:
        self.scanlbl.setText("scanning your steam library...")
        def work():
            out, rows = [], {}
            for g in lgames.scan_all():
                self._inspect(g)
                if not g.exe: continue
                out.append(g); rows[self._key(g)] = self._row_for(g)
            features.library_save(out, rows, self.sm)
            return out, rows
        self._run(work, self._scanned)

    def _scanned(self, got) -> None:
        gs, rows = got
        self.all_games = sorted(gs, key=lambda g: g.name.lower()); self._rows = dict(rows); self._fill()

    def _load_cached(self) -> bool:
        got = features.library_load(self.sm)
        if not got:
            return False
        gs, rows, changed = got
        self.all_games = sorted(gs, key=lambda g: g.name.lower())
        self._rows = {tuple(k) if not isinstance(k, tuple) else k: list(v) for k, v in rows.items() if v}
        if changed:
            # Their folder or exe moved on: read them again off the UI thread, then save.
            def work():
                fresh = {}
                for g in changed:
                    self._inspect(g)
                    if g.exe: fresh[self._key(g)] = self._row_for(g)
                return fresh
            def done(fresh):
                self._rows.update(fresh); features.library_save(self.all_games, self._rows, self.sm); self._fill()
            self._side_run(work, done)
        return True

    def _fill(self) -> None:
        self.tree.clear(); q = self.search.text().lower(); n = 0
        for i, g in enumerate(self.all_games):
            if q and q not in g.name.lower(): continue
            if self.arch_filter() and g.bitness and g.bitness != self.arch_filter(): continue
            row = self._rows.get(self._key(g))
            if row is None:
                row = self._rows[self._key(g)] = self._row_for(g)
            ok, why, route, level, state, sdetail = row
            installed = state == "installed"
            if self.only_installed.isChecked() and not state: continue
            outlook = OUTLOOK.get(level, level) if ok else "unsupported"
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
        g = self._inspect(games.Game(name=Path(d).name, folder=Path(d)))
        if not g.exe:
            QtWidgets.QMessageBox.warning(self, "no exe", "no game executable found in that folder"); return
        lgames.remember_folder(g.folder)
        if proton.prefix_for(g) is None:
            self._ask_prefix(g)
        self.all_games.insert(0, g); self._fill(); self.tree.setCurrentItem(self.tree.topLevelItem(0))
        features.library_save(self.all_games, self._rows, self.sm)

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
        scroll = QtWidgets.QScrollArea(); scroll.setWidgetResizable(True); scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        c, cl = card(); grid = QtWidgets.QGridLayout(); grid.setHorizontalSpacing(14); grid.setVerticalSpacing(5); cl.addLayout(grid)
        self.rows: dict[str, tuple[QtWidgets.QWidget, QtWidgets.QWidget, QtWidgets.QWidget | None]] = {}

        def row(r, key, text, widget, hint=None, amber=False):
            l = label(text, "amber" if amber else "dim"); grid.addWidget(l, r, 0); grid.addWidget(widget, r, 1)
            if hint is not None: grid.addWidget(hint, r, 2)
            self.rows[key] = (l, widget, hint)

        self.cb_exe = QtWidgets.QComboBox(); row(0, "exe", "target exe", self.cb_exe, amber=True)
        self.cb_route = QtWidgets.QComboBox(); self.cb_route.currentIndexChanged.connect(self._on_route); row(1, "route", "route", self.cb_route)
        self.routelbl = label("", "dim"); grid.addWidget(self.routelbl, 2, 0, 1, 3)
        self.cb_prov = QtWidgets.QComboBox(); self.cb_prov.addItems([v[0] for v in reshade_ini.PROVIDERS.values()]); row(3, "prov", "motion vectors", self.cb_prov)
        self.cb_proxy = QtWidgets.QComboBox(); self.cb_proxy.addItems(["auto - pick a free name"] + list(optiscaler.PROXY_NAMES)); row(4, "proxy", "loads as", self.cb_proxy)
        self.cb_rproxy = QtWidgets.QComboBox(); self.cb_rproxy.addItems(["auto - from the graphics api"] + list(installer.RESHADE_PROXIES)); row(5, "rproxy", "reshade loads as", self.cb_rproxy)
        ar = QtWidgets.QHBoxLayout(); self.cb_renodx = QtWidgets.QComboBox(); self.cb_renodx.addItem("loading..."); ar.addWidget(self.cb_renodx, 1)
        b = QtWidgets.QPushButton("use my file"); b.clicked.connect(self._pick_renodx); ar.addWidget(b); arw = QtWidgets.QWidget(); arw.setLayout(ar); row(6, "renodx", "dlss 5 add-on", arw)
        self.cb_dlssnr = QtWidgets.QComboBox(); self.cb_dlssnr.addItem("auto - match my gpu"); row(7, "dlssnr", "nvngx_dlssnr", self.cb_dlssnr)
        dr = QtWidgets.QHBoxLayout(); self.cb_dlss = QtWidgets.QComboBox(); self.cb_dlss.addItem("loading..."); dr.addWidget(self.cb_dlss, 1)
        self.keep_dlss = QtWidgets.QCheckBox("keep the game's own"); self.keep_dlss.setChecked(True); self.keep_dlss.toggled.connect(self._on_keep_dlss)
        dr.addWidget(self.keep_dlss); drw = QtWidgets.QWidget(); drw.setLayout(dr); row(8, "dlss", "nvngx_dlss", drw)
        self.cb_dlssd = QtWidgets.QComboBox(); self.cb_dlssd.addItem(DLSSD_KEEP); self.cb_dlssd.currentIndexChanged.connect(self._on_dlssd)
        self.dlssdhint = label("", "faint"); row(9, "dlssd", "ray reconstruction", self.cb_dlssd, self.dlssdhint)
        self.cb_preset = QtWidgets.QComboBox(); self.cb_preset.addItems(list(feedcfg.PRESETS.values())); row(10, "preset", "dlss preset", self.cb_preset)
        self.cb_hdr = QtWidgets.QComboBox(); self.cb_hdr.addItems(list(feedcfg.HDR.values())); self.dlaalbl = label("", "faint"); row(11, "hdr", "hdr", self.cb_hdr, self.dlaalbl)
        self.cb_nrpreset = QtWidgets.QComboBox(); self.cb_nrpreset.addItems([f"{v}" + ("  -  the author's default" if k == 0 else "") for k, v in optiscaler.NR_PRESETS.items()]); row(12, "nrpreset", "model preset", self.cb_nrpreset)
        self.cb_nrstyle = QtWidgets.QComboBox(); self.cb_nrstyle.addItems(list(optiscaler.NR_STYLES.values())); self.nrhint = label("the rest is on the overlay", "faint"); row(13, "nrstyle", "style", self.cb_nrstyle, self.nrhint)
        sw = QtWidgets.QHBoxLayout(); self.sc_work = QtWidgets.QSlider(Qt.Horizontal); self.sc_work.setRange(50, 100); self.sc_work.setValue(100); self.sc_work.valueChanged.connect(self._on_workres)
        self.workhint = label("", "dim"); sw.addWidget(self.sc_work, 2); sw.addWidget(self.workhint, 3); sww = QtWidgets.QWidget(); sww.setLayout(sw); row(14, "work", "work resolution", sww)
        aim = QtWidgets.QHBoxLayout(); self.sp_aim = QtWidgets.QSpinBox(); self.sp_aim.setRange(0, 500); self.sp_aim.setSpecialValueText("off"); self.sp_aim.setSuffix(" fps")
        self.sp_aim.setToolTip("play, then press 'did it work?': the session's cost is read from the logs and the work area that meets this target is worked out")
        self.btn_tune = QtWidgets.QPushButton("apply the change"); self.btn_tune.setEnabled(False); self.btn_tune.clicked.connect(self._apply_tune)
        aim.addWidget(self.sp_aim); aim.addWidget(self.btn_tune); aim.addStretch(1); aimw = QtWidgets.QWidget(); aimw.setLayout(aim)
        self.aimhint = label("two sessions at different work areas solve it exactly; one gives a bounded step", "faint"); row(15, "aim", "aim for", aimw, self.aimhint)
        self.cb_feederver = QtWidgets.QComboBox(); self.cb_feederver.addItems(["stable - newest release", "newest pre-release"]); row(16, "feederver", "feeder build", self.cb_feederver)
        self.cb_optibuild = QtWidgets.QComboBox()
        for key, text in features.opti_builds(): self.cb_optibuild.addItem(text, key)
        self.cb_optibuild.setCurrentIndex(max(0, [k for k, _ in features.opti_builds()].index(features.default_opti_build())))
        row(17, "optibuild", "optiscaler line", self.cb_optibuild)
        self.cb_opti = QtWidgets.QComboBox(); self.cb_opti.addItems(OPTI_CHOICES); self.cb_opti.currentIndexChanged.connect(self._on_opti); self.opti_custom = ""; row(18, "opti", "optiscaler archive", self.cb_opti)
        self.cb_overlaykey = QtWidgets.QComboBox(); self.cb_overlaykey.addItems(features.overlay_key_names()); self.cb_overlaykey.setCurrentText(features.current_overlay_key())
        self.cb_overlaykey.currentTextChanged.connect(self._on_overlaykey)
        self.overlaykeyhint = label("route default: reshade opens on Home, optiscaler on Insert", "faint"); row(19, "overlaykey", "overlay key", self.cb_overlaykey, self.overlaykeyhint)
        self.ck_vr = QtWidgets.QCheckBox("vr headset (openxr) - not available under proton"); self.ck_vr.setEnabled(False); self.ck_vr.setToolTip(features.vr_reason())
        grid.addWidget(self.ck_vr, 20, 0, 1, 3); self.rows["vr"] = (self.ck_vr, self.ck_vr, None)
        self.overridelbl = label("", "dim"); grid.addWidget(self.overridelbl, 21, 0, 1, 3)
        self.reswarn = label("!! set your screen resolution BEFORE turning neural rendering on - a resolution change with the feed live is the crash we see most", "rust"); grid.addWidget(self.reswarn, 22, 0, 1, 3)
        self.ck_indicator = QtWidgets.QCheckBox("show the dlss indicator on screen (PROTON_DLSS_INDICATOR=1: version, preset, resolution, frame-gen state)")
        grid.addWidget(self.ck_indicator, 23, 0, 1, 3)
        grid.setColumnStretch(1, 1); scroll.setWidget(c); v.addWidget(scroll, 2)

        self.pb = QtWidgets.QProgressBar(); self.pb.setRange(0, 100); self.pb.setTextVisible(False); v.addWidget(self.pb)
        self.pblbl = label("", "faint"); v.addWidget(self.pblbl)
        self.log = QtWidgets.QPlainTextEdit(); self.log.setReadOnly(True); v.addWidget(self.log, 3)
        act = QtWidgets.QHBoxLayout(); self.buttons: dict[str, QtWidgets.QPushButton] = {}
        for text, fn in (("did it work?", self._diagnose), ("share the result", self._share_result), ("uninstall", self._uninstall),
                         ("check versions", self._check_components), ("open folder", self._open_folder), ("what will happen?", self._preview),
                         ("launch options", self._launch_options), ("apply overrides", self._apply_launch_options),
                         ("upgrade game dlss", self._upgrade_dlss), ("game notes", self._game_notes),
                         ("vk layer", self._vklayer)):
            b = QtWidgets.QPushButton(text); b.clicked.connect(fn); act.addWidget(b); self.buttons[text] = b
        self.buttons["share the result"].setEnabled(False)
        self.buttons["share the result"].setToolTip("press 'did it work?' first - the outcome comes from the diagnosis, not from a question")

        act.addStretch(1); v.addLayout(act)
        return w

    def _enter_install(self) -> None:
        g = self.game; self.gamelbl.setText(g.name); self.log.clear(); self._last_diag = None; self._tune = None
        self.buttons["share the result"].setEnabled(False); self.btn_tune.setEnabled(False); self.btn_tune.setText("apply the change")
        self.cb_exe.clear(); self.cb_exe.addItems([str(c.relative_to(g.folder)) for c in (g.candidates or [g.exe])])
        pfx = proton.prefix_for(g); bridge = proton.ngx_bridge_present(g)
        pv = proton.proton_version(g)
        self.protonlbl.setText(f"proton: {'prefix ok' if pfx else 'no prefix'} · {'ngx bridge ok' if bridge else 'no ngx bridge'}" + (f" · {pv}" if pv else ""))
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
        if lstate.needs_migration(g.install_dir):
            # An install made by the retired proton-tool: give it the upstream record once.
            written = lstate.migrate(g.install_dir, lambda t: self._log(t))
            if written:
                self._log("> this folder was set up by the old proton-tool; its record is now an upstream manifest "
                          "(uninstall, versions and 'did it work?' understand it)", "ok")
                self._rows.pop(self._key(g), None)
        if not self.catalog:
            self._run(sources.rhi_catalog, self._catalog_loaded)

    def _catalog_loaded(self, cat) -> None:
        self.catalog = cat or {}
        self._fill_addon_list(self.route == dlss.RENODX)
        self.cb_dlssnr.clear(); self.cb_dlssnr.addItem("auto - match my gpu")
        self.cb_dlssnr.addItems([e["label"] for e in gpu.order_dlssnr(self.catalog.get("dlssnr", []), self.sm)])
        self.cb_dlss.clear(); self.cb_dlss.addItem("auto - newest"); self.cb_dlss.addItems([e["label"] for e in self.catalog.get("dlss", [])])
        dd = features.dlssd_choices(self.catalog)
        self.cb_dlssd.blockSignals(True); self.cb_dlssd.clear(); self.cb_dlssd.addItem(DLSSD_KEEP); self.cb_dlssd.addItems(dd); self.cb_dlssd.blockSignals(False)
        self.cb_dlssd.setEnabled(bool(dd))
        if not dd: self.dlssdhint.setText("no build could be listed - the game keeps its own")

    def _fill_addon_list(self, sf: bool) -> None:
        fam = "renodx_sf" if sf else "renodx"
        self.cb_renodx.clear(); self.cb_renodx.addItem("auto - newest from the catalog")
        for e in self.catalog.get(fam, []): self.cb_renodx.addItem(e["label"])
        comp = paths.components_dir()
        if comp.is_dir():
            for p in sorted(comp.glob("*.addon64")):
                self.cb_renodx.addItem(f"[local] {p.name}", str(p))
        if self.renodx_local: self.cb_renodx.setCurrentText(f"[local] {self.renodx_local.name}")

    def _pick_renodx(self) -> None:
        f, _ = QtWidgets.QFileDialog.getOpenFileName(self, "renodx add-on", str(paths.components_dir()), "*.addon64")
        if f:
            self.renodx_local = Path(f); self.cb_renodx.addItem(f"[local] {Path(f).name}", f); self.cb_renodx.setCurrentIndex(self.cb_renodx.count() - 1)

    def _on_route(self, i: int) -> None:
        if i >= 0: self._apply_route(self.cb_route.itemData(i))

    def _apply_route(self, path: str) -> None:
        self.route = path
        usable, note = self.route_fit.get(path, (True, ""))
        blurb, warns = features.route_notes(self.game, path, self.support)
        text = blurb[0]
        if not usable: text = f"NOT FOR THIS PC - {note}.\n{text}"
        elif note: text = f"{text}\n({note})"
        for wl in warns: text += f"\n  !  {wl}"
        self.routelbl.setText(text); self.routelbl.setObjectName("rust" if not usable else "dim"); self.routelbl.setStyleSheet("")
        feeder, opti, remix = path == dlss.FEEDER, path == dlss.OPTI, path == dlss.REMIX
        x64 = (self.game.bitness or 64) == 64
        self._fill_addon_list(path == dlss.RENODX)
        has_rr = features.has_ray_reconstruction(self.support) and not (opti or remix)
        vis = {"prov": feeder, "proxy": opti, "rproxy": not (feeder or opti), "preset": not opti, "hdr": not opti,
               "nrpreset": opti, "nrstyle": opti, "feederver": feeder, "opti": opti, "optibuild": opti,
               "renodx": path != dlss.UPSTREAM, "work": opti or feeder, "aim": features.work_applies(self.game, path),
               "dlssd": has_rr, "overlaykey": not remix, "vr": not opti and not remix and x64}
        for key, on in vis.items():
            for wdg in self.rows[key]:
                if wdg is not None: wdg.setVisible(on)
        if not has_rr: self.cb_dlssd.setCurrentIndex(0)
        elif self.cb_dlssd.isEnabled(): self.dlssdhint.setText("this game ships one; if you swap it, the original is backed up and comes back on uninstall")
        self.nrhint.setText(f"the rest is on the overlay ({reshade_ini.overlay_key_name(optiscaler.OVERLAY_KEY)})")
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
            hint = vklayer.suggestion(self.game, self.support.native_dlss)
            if hint: self._log(f"  vk layer: {hint}")
            for wl in warns: self._log(f"  !  {wl}", "warn")
            self._community_note()

    def _community_note(self) -> None:
        """What other people found in this game - off the UI thread, once per game and route."""
        g, route = self.game, self.route
        key = (str(g.install_dir), route)
        if key in self._noted:
            return
        self._noted.add(key)
        def done(lines):
            if lines and self.game is g:
                self._log(""); self._log("=== what other people found ===", "head")
                for ln in lines: self._log(f"> {ln}")
        self._side_run(lambda: features.community_lines(g, route), done)


    def _on_workres(self) -> None:
        v = self.sc_work.value(); opti = self.route == dlss.OPTI
        if opti:
            cost = round((v / 100) ** 2 * 100)
            self.workhint.setText("100% - full size; the pass costs about half your fps" if v == 100 else f"{v}% - about {cost}% of the full-size cost; the frame stays full detail")
        else:
            self.workhint.setText("100% - full quality" if v == 100 else (f"{v}% - a little faster" if v >= 75 else f"{v}% - faster, softer"))

    def _on_opti(self, i: int) -> None:
        if i == 3:
            t, ok = QtWidgets.QInputDialog.getText(self, "optiscaler release tag", "dagherbou tag (e.g. v0.2.0-patch1):"); self.opti_custom = t.strip() if ok else ""
        elif i == 4:
            f, _ = QtWidgets.QFileDialog.getOpenFileName(self, "optiscaler zip", str(paths.components_dir()), "*.zip"); self.opti_custom = f
        else:
            self.opti_custom = ""

    def _on_dlssd(self, i: int) -> None:
        if i > 0:
            self._log("")
            for line in features.swap_warning("nvngx_dlssd.dll"): self._log(f"!! {line}" if line.strip() else "", "warn")

    def _on_keep_dlss(self, on: bool) -> None:
        if not on and self.game is not None and (self.game.install_dir / "nvngx_dlss.dll").is_file():
            self._log("")
            for line in features.swap_warning("nvngx_dlss.dll"): self._log(f"!! {line}" if line.strip() else "", "warn")

    def _on_overlaykey(self, name: str) -> None:
        vk = features.set_overlay_key(name)
        if vk:
            self._log(f"> the overlay will open on {name} - press INSTALL to write it into this game")
        if hasattr(self, "nrhint"):
            self.nrhint.setText(f"the rest is on the overlay ({reshade_ini.overlay_key_name(optiscaler.OVERLAY_KEY)})")

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
        i = self.cb_opti.currentIndex()
        pins.set_optiscaler(OPTI_CHOICE_KEYS[i] if i < 3 else (self.opti_custom or "default"))
        dlssd = self.cb_dlssd.currentText() if self.cb_dlssd.currentIndex() > 0 and self.cb_dlssd.isVisible() else ""
        return installer.Options(
            provider=list(reshade_ini.PROVIDERS)[self.cb_prov.currentIndex()], renodx=None if local else clean(val),
            renodx_local=Path(local) if local else None, dlssnr=clean(self.cb_dlssnr.currentText()), dlss=clean(self.cb_dlss.currentText()),
            dlssd=dlssd, keep_game_dlss=self.keep_dlss.isChecked(), feed=feed, nr=nr, feeder_prerelease=self.cb_feederver.currentIndex() == 1,
            path=self.route, native_dlss=s.native_dlss, upscaler=s.upscaler,
            opti_proxy="" if self.cb_proxy.currentIndex() == 0 else self.cb_proxy.currentText(),
            opti_build=self.cb_optibuild.currentData() or "",
            reshade_proxy="" if self.cb_rproxy.currentIndex() == 0 else self.cb_rproxy.currentText(),
            vr=False)

    # ---------------------------------------------------------------- actions
    def _log(self, text: str, tag: str = "") -> None:
        colour = {"ok": GREEN, "warn": RUST, "err": RED, "head": AMBER}.get(tag)
        esc = text.replace("&", "&amp;").replace("<", "&lt;")
        style = "white-space:pre" + (f";color:{colour}" if colour else "")
        self.log.appendHtml(f'<span style="{style}">{esc}</span>')

    def _track(self, w: Worker) -> Worker:
        """Remember a worker until it finishes. self.worker / self.side hold
        only the newest one; a route change that starts a second community
        fetch would otherwise orphan the first, and a QThread destroyed while
        running aborts the whole process at exit."""
        self._threads.add(w)
        w.finished.connect(lambda: self._threads.discard(w))
        return w

    def _run(self, fn, on_done, *args) -> None:
        self.worker = self._track(Worker(fn, *args)); self.worker.done.connect(on_done)
        self.worker.fail.connect(lambda tb: self._log("!! " + tb.strip().splitlines()[-1], "err")); self.worker.start()

    def _side_run(self, fn, on_done) -> None:
        """A second worker for things that must not block INSTALL: the community note, the library recheck."""
        w = self._track(Worker(fn)); w.done.connect(on_done); w.fail.connect(lambda tb: None)
        self.side = w; w.start()

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
        # pins._choice is module state read by the worker when it resolves the
        # archive; changing the combo mid-install would swap the package under it.
        self.cb_opti.setEnabled(False); self.cb_optibuild.setEnabled(False)
        from linuxport import seed; seed.seed(log=lambda m: None)
        opt = self._opts()
        w = Worker(lambda: installer.install(g, opt, on_step=lambda i, n, name: w.step.emit(i, n, name),
                                              on_prog=lambda p, m: w.prog.emit(p, m), on_log=lambda t: w.line.emit(t, "")))
        w.step.connect(lambda i, n, name: (self._log(f"[{i+1}/{n}] {name}"), self.pb.setValue(int((i) * 100 / max(n, 1)))))
        w.prog.connect(lambda p, m: self.pblbl.setText(m)); w.line.connect(lambda t, tag: self._log("   " + t.rstrip(), tag))
        w.done.connect(self._installed)
        w.fail.connect(lambda tb: (self._log("!! " + tb.strip().splitlines()[-1], "err"), self.btn_next.setEnabled(True),
                                   self.cb_opti.setEnabled(True), self.cb_optibuild.setEnabled(True)))
        self.worker = self._track(w); w.start()

    def _installed(self, rep) -> None:
        self.pb.setValue(100); self.pblbl.setText(""); self.btn_next.setEnabled(True)
        self.cb_opti.setEnabled(True); self.cb_optibuild.setEnabled(True)
        self._log(f"> wrote {len(rep.written)} file(s)", "ok")
        for wn in rep.warnings: self._log(f"!! {wn}", "warn")
        for n in rep.notes: self._log(f"-- {n}")
        self._rows.pop(self._key(self.game), None)
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
        """The game's DLSS runtimes, Proton's way: PROTON_DLSS_UPGRADE=1 in the launch options.

        Proton copies its bundled nvngx_dlss / dlssd / dlssg into system32/umu/ at
        every launch, ahead of the game's own. A specific build for this game is
        the nvngx_dlss / ray reconstruction dropdowns above (NVIDIA's catalog)."""
        g = self.game; proxy = self._proxy()
        cur = proton.current_launch_options(g)
        on = not proton.dlss_upgrade_present(cur)
        line = proton.with_dlss_upgrade(proton.launch_options(g, proxy, self.ck_indicator.isChecked(), self.route), on)
        self._log(""); self._log("=== the game's dlss runtimes ===", "head")
        self._log(f"   PROTON_DLSS_UPGRADE {'ON' if on else 'OFF'}: {'proton puts its bundled sr/rr/fg (310.9) ahead of the game s own at every launch' if on else 'the game runs its own dlss files'}")
        self._log(f"   {line}")
        QtWidgets.QApplication.clipboard().setText(line); self._log("   (copied to the clipboard; 'apply overrides' writes the current line into steam)")
        self._log("   a specific build for this game instead: pick it in 'nvngx_dlss' / 'ray reconstruction' above and press INSTALL")

    def _game_notes(self) -> None:
        g = self.game
        notes = tuning.game_notes(g.exe.name if g and g.exe else None)
        self._log(""); self._log("=== notes ===", "head")
        if not notes:
            self._log(f"   nothing game-specific on file. general: set the screen resolution before enabling neural rendering; "
                      f"open the overlay with {reshade_ini.overlay_key_name(optiscaler.OVERLAY_KEY if self.route == dlss.OPTI else 'Home')}; "
                      "'did it work?' reads the logs.")
        for n in notes: self._log(f"   - {n}")

    def _diagnose(self) -> None:
        g = self.game; self._log(""); self._log("=== did it work? ===", "head")
        rep = diagnose.analyse(g.install_dir); self._last_diag = rep
        for lvl, title, detail in lverify.run(g, rep):
            if title == "verdict":
                self._log(f"> {detail}", "ok" if detail.startswith("Working") else "head"); continue
            mark, colour = MARK.get(lvl.lower(), ("[--]  ", BODY))
            self._log(f"{mark} {title}", {"ok": "ok", "warn": "warn", "bad": "err"}.get(lvl.lower(), ""))
            if detail: self._log(f"        {detail}")
        self.buttons["share the result"].setEnabled(bool(rep.ran))
        self._autotune(rep)

    def _autotune(self, rep) -> None:
        self._tune = None; self.btn_tune.setEnabled(False); self.btn_tune.setText("apply the change")
        target = self.sp_aim.value() or None
        tune = features.autotune_after(self.game, self.route, self.sc_work.value(), rep, target)
        if tune is None or tune.suggestion is None:
            return
        self._log(""); self._log(f"=== aiming for {target} fps ===", "head")
        for ln in tune.suggestion.lines: self._log(f"> {ln}")
        if tune.suggestion.resolution != tune.measured.resolution:
            self._tune = tune.suggestion
            self.btn_tune.setEnabled(True); self.btn_tune.setText(f"set the work area to {tune.suggestion.resolution}%")

    def _apply_tune(self) -> None:
        sug = self._tune
        if sug is None or self.game is None:
            return
        self.sc_work.setValue(sug.resolution); self._on_workres()
        try:
            features.apply_tune(self.game, self.route, sug.resolution, log_fn=lambda t: self._log(t))
        except Exception as e:
            self._log(f"[fail] could not write the setting ({e}) - press INSTALL instead, it writes the same value.", "err"); return
        self._log(f"> set to {sug.resolution}%. it is read when the game starts, so it applies to the next run - play again and press 'did it work?'.", "ok")
        self._tune = None; self.btn_tune.setEnabled(False); self.btn_tune.setText("apply the change")

    def _share_result(self) -> None:
        rep, g = self._last_diag, self.game
        if rep is None or g is None:
            return
        self._log("")
        self._log("> a browser window opens with the result in it - nothing is sent unless you post it. it carries the game's "
                  "name and executable, the route and build, the graphics api, your card and driver, the proton build, this "
                  "tool's version, whether it worked, and the one-line verdict. no paths, no user name, nothing else.", "head")
        if not features.open_url(features.share_url(g, self.route, rep)):
            self._log("[fail] could not open the browser", "err")

    def _report_bug(self) -> None:
        g = self.game
        dlg = ReportDialog(self, g.name if g else "")
        if dlg.exec() != QtWidgets.QDialog.Accepted or dlg.answers is None:
            return
        url, body, on_clipboard = features.report_url(g, self.route if g else "-", self._last_diag, dlg.answers)
        if on_clipboard:
            QtWidgets.QApplication.clipboard().setText(body)
        if not features.open_url(url):
            QtWidgets.QApplication.clipboard().setText(body)
            QtWidgets.QMessageBox.information(self, "report", "details copied to the clipboard - paste them into a new issue on github.")

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
        def done(rm):
            self._log(f"> removed {len(rm)} item(s)", "ok"); self._rows.pop(self._key(g), None); self._fill()
        self._run(lambda: installer.uninstall(g, on_log=lambda t: None), done)

    def closeEvent(self, event) -> None:
        """Let the workers finish before Qt tears them down.

        A QThread destroyed while running aborts the whole process ("QThread:
        Destroyed while thread is still running", exit 134) - seen when the
        window was closed with the community fetch still in flight. Waiting a
        few seconds per worker is the honest fix; a download that has not
        answered by then is abandoned with the process, which is what closing
        means anyway.
        """
        for t in list(self._threads):
            if t.isRunning():
                t.wait(3000)
            if t.isRunning():
                # A download that has not answered in three seconds is not
                # going to be missed; terminating at exit beats an abort.
                t.terminate(); t.wait(1000)
        super().closeEvent(event)

    def _open_folder(self) -> None:
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(self.game.install_dir)))

    def _open_logs(self) -> None:
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(paths.STATE)))

    def _how(self) -> None:
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(Path(__file__).resolve().parent / "PORT_PLAN.md")))


def main() -> int:
    app = QtWidgets.QApplication(sys.argv); app.setStyle("Fusion")
    w = App(); w.show(); return app.exec()


if __name__ == "__main__":
    sys.exit(main())

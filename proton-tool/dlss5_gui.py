#!/usr/bin/env python3
"""Qt front-end for dlss5_proton.

Deliberately a thin shell: every action builds the same argparse namespace the
CLI does and calls straight into the library, so there is exactly one
implementation of install/restore/verify and the GUI cannot drift from it.
"""

from __future__ import annotations

import argparse
import contextlib
import sys
import traceback
from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dlss5_proton as core  # noqa: E402

core._COLOR = False  # ANSI escapes would render as literal junk in a QTextEdit

MODE_MINIMAL, MODE_FULL, MODE_FEEDER = "minimal", "full", "feeder"


# ---------------------------------------------------------------------------
# Worker plumbing
# ---------------------------------------------------------------------------


class _SignalStream:
    """File-like object that forwards complete lines to a Qt signal."""

    def __init__(self, emit):
        self._emit = emit
        self._buffer = ""

    def write(self, text: str) -> int:
        self._buffer += text
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            self._emit(line)
        return len(text)

    def flush(self) -> None:
        if self._buffer:
            self._emit(self._buffer)
            self._buffer = ""


class Worker(QtCore.QThread):
    line = QtCore.Signal(str)
    finished_ok = QtCore.Signal(bool, str)

    def __init__(self, fn, *args, parent=None):
        super().__init__(parent)
        self._fn, self._args = fn, args

    def run(self) -> None:
        stream = _SignalStream(self.line.emit)
        try:
            with contextlib.redirect_stdout(stream):
                code = self._fn(*self._args)
            stream.flush()
            self.finished_ok.emit(code in (0, None), "")
        except core.Fail as error:
            stream.flush()
            self.finished_ok.emit(False, str(error))
        except Exception:
            stream.flush()
            self.finished_ok.emit(False, traceback.format_exc(limit=4))


# ---------------------------------------------------------------------------
# Small widgets
# ---------------------------------------------------------------------------


class PathPicker(QtWidgets.QWidget):
    """Line edit plus a Browse button."""

    def __init__(self, placeholder: str, mode: str = "dir", parent=None):
        super().__init__(parent)
        self.mode = mode
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.edit = QtWidgets.QLineEdit(placeholderText=placeholder)
        button = QtWidgets.QPushButton("Browse…")
        button.setFixedWidth(90)
        button.clicked.connect(self._browse)
        layout.addWidget(self.edit)
        layout.addWidget(button)

    def _browse(self) -> None:
        start = self.edit.text() or str(Path.home())
        if self.mode == "dir":
            chosen = QtWidgets.QFileDialog.getExistingDirectory(self, "Select folder", start)
        else:
            chosen, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Select file", start)
        if chosen:
            self.edit.setText(chosen)

    def text(self) -> str:
        return self.edit.text().strip()

    def setText(self, value: str) -> None:
        self.edit.setText(value)


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"dlss5-proton {core.VERSION}")
        self.resize(1080, 780)
        self.worker: Worker | None = None

        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        root = QtWidgets.QHBoxLayout(central)

        left = QtWidgets.QVBoxLayout()
        left.addWidget(self._build_target_box())
        left.addWidget(self._build_mode_box())
        left.addWidget(self._build_options_box())
        left.addWidget(self._build_action_box())
        left.addStretch(1)
        panel = QtWidgets.QWidget()
        panel.setLayout(left)
        panel.setFixedWidth(430)
        root.addWidget(panel)

        right = QtWidgets.QVBoxLayout()
        right.addWidget(QtWidgets.QLabel("<b>Status</b>"))
        self.results = QtWidgets.QTreeWidget()
        self.results.setHeaderLabels(["", "Check", "Detail"])
        self.results.setRootIsDecorated(False)
        header = self.results.header()
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.results.setColumnWidth(0, 46)
        self.results.setMaximumHeight(280)
        right.addWidget(self.results)
        right.addWidget(QtWidgets.QLabel("<b>Output</b>"))
        self.output = QtWidgets.QPlainTextEdit(readOnly=True)
        self.output.setFont(QtGui.QFontDatabase.systemFont(
            QtGui.QFontDatabase.SystemFont.FixedFont))
        right.addWidget(self.output, 1)
        root.addLayout(right, 1)

        self.status = self.statusBar()
        self._refresh_games()
        self._mode_changed()

    # --- construction ------------------------------------------------------

    def _build_target_box(self) -> QtWidgets.QGroupBox:
        box = QtWidgets.QGroupBox("Game")
        form = QtWidgets.QVBoxLayout(box)

        self.radio_steam = QtWidgets.QRadioButton("Steam library")
        self.radio_path = QtWidgets.QRadioButton("Folder or .exe")
        self.radio_steam.setChecked(True)
        self.radio_steam.toggled.connect(self._target_changed)
        form.addWidget(self.radio_steam)

        self.games = QtWidgets.QComboBox()
        self.games.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(self.games, 1)
        rescan = QtWidgets.QPushButton("Rescan")
        rescan.setFixedWidth(80)
        rescan.clicked.connect(self._refresh_games)
        row.addWidget(rescan)
        form.addLayout(row)

        self.only_dlss = QtWidgets.QCheckBox("Only games with a DLSS runtime")
        self.only_dlss.stateChanged.connect(self._refresh_games)
        form.addWidget(self.only_dlss)

        form.addWidget(self.radio_path)
        self.custom_path = PathPicker("/path/to/game (folder or .exe)")
        form.addWidget(self.custom_path)

        form.addWidget(QtWidgets.QLabel("Wine prefix (optional — auto-detected)"))
        self.prefix = PathPicker("…/compatdata/<appid>/pfx")
        form.addWidget(self.prefix)
        return box

    def _build_mode_box(self) -> QtWidgets.QGroupBox:
        box = QtWidgets.QGroupBox("Mode")
        layout = QtWidgets.QVBoxLayout(box)
        self.mode_optiscaler = QtWidgets.QRadioButton(
            "OptiScaler — recommended (no ReShade)")
        self.mode_optiscaler.setToolTip(
            "Runs the NR model right after the game's own upscaler, before the "
            "UI. Ships its own nvngx caller shim, which is what makes it work "
            "under Proton. Leave the game's DLSS ON.")
        self.mode_minimal = QtWidgets.QRadioButton(
            "Minimal — ReShade + RenoDX add-on")
        self.mode_full = QtWidgets.QRadioButton(
            "Advanced — also replace the game's DLSS/Streamline set")
        self.mode_feeder = QtWidgets.QRadioButton(
            "Feeder — games with NO DLSS (D3D11/Unity)")
        self.mode_optiscaler.setChecked(True)
        for widget in (self.mode_optiscaler, self.mode_minimal,
                       self.mode_full, self.mode_feeder):
            widget.toggled.connect(self._mode_changed)
            layout.addWidget(widget)
        return box

    def mode(self) -> str:
        if self.mode_optiscaler.isChecked():
            return "optiscaler"
        if self.mode_full.isChecked():
            return "full"
        if self.mode_feeder.isChecked():
            return "feeder"
        return "minimal"

    def _build_options_box(self) -> QtWidgets.QGroupBox:
        box = QtWidgets.QGroupBox("Options")
        form = QtWidgets.QFormLayout(box)

        self.loader = QtWidgets.QComboBox()
        self.loader.addItems(core.LOADER_NAMES)
        form.addRow("ReShade proxy", self.loader)

        self.dlss_dest = QtWidgets.QComboBox()
        self.dlss_dest.addItems(["auto", "game", "system32", "both"])
        form.addRow("DLSS destination", self.dlss_dest)

        self.mv_provider = PathPicker("motion-vector provider (.fx or folder)")
        form.addRow("MV provider", self.mv_provider)

        self.zip_path = PathPicker("DLSS 5 ZIP (auto-detected)", mode="file")
        form.addRow("DLSS ZIP", self.zip_path)

        self.addon_path = PathPicker("renodx add-on (auto-detected)", mode="file")
        form.addRow("Add-on", self.addon_path)

        # Both upstreams ship several builds a day and a build that fixes one
        # game can break another, so each install can be pinned independently
        # of the tool's default. Empty = use the pinned version.
        self.optiscaler_zip = PathPicker(
            f"OptiScaler archive (default: {core.OPTISCALER_VERSION})", mode="file")
        form.addRow("OptiScaler build", self.optiscaler_zip)

        self.feeder_zip = PathPicker(
            f"DLSS5-Feeder archive (default: {core.FEEDER_VERSION})", mode="file")
        form.addRow("Feeder build", self.feeder_zip)

        self.skip_d3dc = QtWidgets.QCheckBox("Skip d3dcompiler_47 install")
        form.addRow("", self.skip_d3dc)
        return box

    def _build_action_box(self) -> QtWidgets.QGroupBox:
        box = QtWidgets.QGroupBox("Actions")
        grid = QtWidgets.QGridLayout(box)
        self.buttons: dict[str, QtWidgets.QPushButton] = {}
        actions = [
            ("Check", self.on_check, "Dry run — changes nothing"),
            ("Install", self.on_install, "Back up and install"),
            ("Verify", self.on_verify, "Read the logs: did it actually work?"),
            ("Restore", self.on_restore, "Undo, verified by hash"),
            ("Copy launch options", self.on_launch_options, "Copy to clipboard"),
            ("Open game folder", self.on_open_folder, ""),
        ]
        for index, (label, slot, tip) in enumerate(actions):
            button = QtWidgets.QPushButton(label)
            button.setToolTip(tip)
            button.clicked.connect(slot)
            grid.addWidget(button, index // 2, index % 2)
            self.buttons[label] = button
        self.buttons["Install"].setStyleSheet("font-weight: bold;")
        return box

    # --- state -------------------------------------------------------------

    def _refresh_games(self) -> None:
        self.games.clear()
        try:
            games = core.installed_games()
        except Exception as error:
            self._append(f"[ERROR] could not read Steam libraries: {error}")
            return
        rows = []
        for game in games.values():
            if self.only_dlss.isChecked():
                has = next(game.install_dir.rglob("nvngx_dlss.dll"), None)
                if has is None:
                    continue
            rows.append((game.name, game.appid))
        for name, appid in sorted(rows, key=lambda r: r[0].lower()):
            self.games.addItem(f"{name}  ({appid})", appid)
        self.status.showMessage(f"{self.games.count()} games", 4000)

    def _target_changed(self) -> None:
        steam = self.radio_steam.isChecked()
        self.games.setEnabled(steam)
        self.only_dlss.setEnabled(steam)
        self.custom_path.setEnabled(not steam)

    def _mode_changed(self) -> None:
        mode = self.mode()
        feeder = mode == "feeder"
        optiscaler = mode == "optiscaler"
        self.mv_provider.setEnabled(feeder)
        self.optiscaler_zip.setEnabled(optiscaler)
        self.feeder_zip.setEnabled(feeder)
        # OptiScaler drives the model itself, so no RenoDX add-on is involved.
        self.addon_path.setEnabled(not optiscaler)
        self.dlss_dest.setEnabled(not (feeder or optiscaler))
        if feeder or optiscaler:
            self.dlss_dest.setCurrentText("game")

        # The two engines accept different proxy DLL names.
        names = (core.OPTISCALER_PROXY_NAMES if optiscaler else core.LOADER_NAMES)
        current = self.loader.currentText()
        self.loader.blockSignals(True)
        self.loader.clear()
        self.loader.addItems(names)
        self.loader.setCurrentText(current if current in names else "dxgi.dll")
        self.loader.blockSignals(False)

    def target(self) -> str:
        if self.radio_steam.isChecked():
            appid = self.games.currentData()
            if not appid:
                raise core.Fail("No game selected")
            return str(appid)
        path = self.custom_path.text()
        if not path:
            raise core.Fail("No path chosen")
        return path

    def namespace(self, **extra) -> argparse.Namespace:
        args = argparse.Namespace(
            target=self.target(),
            prefix=self.prefix.text() or None,
            zip=self.zip_path.text() or None,
            addon=self.addon_path.text() or None,
            mode=self.mode(),
            full=self.mode_full.isChecked(),
            feeder=self.mode_feeder.isChecked(),
            mv_provider=self.mv_provider.text() or None,
            optiscaler_zip=self.optiscaler_zip.text() or None,
            feeder_zip=self.feeder_zip.text() or None,
            loader=self.loader.currentText(),
            dlss_dest=self.dlss_dest.currentText(),
            skip_d3dcompiler=self.skip_d3dc.isChecked(),
            yes=True,
            force=False,
        )
        for key, value in extra.items():
            setattr(args, key, value)
        return args

    # --- running -----------------------------------------------------------

    def _append(self, line: str) -> None:
        self.output.appendPlainText(line)
        bar = self.output.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _run(self, fn, *args, label: str = "") -> None:
        if self.worker and self.worker.isRunning():
            self.status.showMessage("Busy…", 3000)
            return
        for button in self.buttons.values():
            button.setEnabled(False)
        self.output.clear()
        self._append(f"$ {label}\n")
        self.status.showMessage(f"{label}…")
        self.worker = Worker(fn, *args)
        self.worker.line.connect(self._append)
        self.worker.finished_ok.connect(lambda ok, err: self._done(ok, err, label))
        self.worker.start()

    def _done(self, ok: bool, error: str, label: str) -> None:
        for button in self.buttons.values():
            button.setEnabled(True)
        if error:
            self._append("\n" + error)
        self.status.showMessage(f"{label}: {'OK' if ok else 'failed'}", 8000)
        if label in ("Install", "Restore"):
            self.on_verify(silent=True)

    # --- actions -----------------------------------------------------------

    def _guard(self, fn):
        try:
            fn()
        except core.Fail as error:
            QtWidgets.QMessageBox.warning(self, "dlss5-proton", str(error))

    def on_check(self) -> None:
        self._guard(lambda: self._run(core.do_check, self.namespace(), label="Check"))

    def on_install(self) -> None:
        def go():
            args = self.namespace()
            mode = {"optiscaler": f"OptiScaler {core.OPTISCALER_VERSION}",
                    "feeder": "Feeder", "full": "Advanced",
                    "minimal": "Minimal"}[args.mode]
            answer = QtWidgets.QMessageBox.question(
                self, "Confirm install",
                f"Install into:\n{args.target}\n\nMode: {mode}\n"
                f"Loader: {args.loader}\n\n"
                "This is an experimental, unofficial injection. Every replaced "
                "file is backed up and Restore undoes it. Continue?")
            if answer == QtWidgets.QMessageBox.StandardButton.Yes:
                self._run(core.do_install, args, label="Install")
        self._guard(go)

    def on_restore(self) -> None:
        def go():
            args = self.namespace()
            answer = QtWidgets.QMessageBox.question(
                self, "Confirm restore",
                f"Restore original files for:\n{args.target}?")
            if answer == QtWidgets.QMessageBox.StandardButton.Yes:
                self._run(core.do_restore, args, label="Restore")
        self._guard(go)

    def on_verify(self, silent: bool = False) -> None:
        def go():
            args = self.namespace()
            target = core.resolve_target(
                args.target, Path(args.prefix) if args.prefix else None)
            checks = core.verify_install(target)
            self.results.clear()
            palette = {True: QtGui.QColor("#2e7d32"),
                       False: QtGui.QColor("#c62828"),
                       None: QtGui.QColor("#ef6c00")}
            glyph = {True: "PASS", False: "FAIL", None: "?"}
            for check in checks:
                item = QtWidgets.QTreeWidgetItem(
                    [glyph[check.ok], check.label, check.detail])
                item.setForeground(0, palette[check.ok])
                item.setToolTip(2, check.detail)
                self.results.addTopLevelItem(item)
            failed = sum(1 for c in checks if c.ok is False)
            self.status.showMessage(
                "All checks passed" if not failed else f"{failed} check(s) failed",
                8000)
            if not silent:
                self._append("\n".join(
                    f"[{c.mark}] {c.label}: {c.detail}" for c in checks))
        self._guard(go)

    def on_launch_options(self) -> None:
        def go():
            args = self.namespace()
            target = core.resolve_target(
                args.target, Path(args.prefix) if args.prefix else None)
            text = core.build_launch_options(target, args.loader, args.dlss_dest)
            QtWidgets.QApplication.clipboard().setText(text)
            self._append(text)
            self.status.showMessage("Launch options copied to clipboard", 6000)
        self._guard(go)

    def on_open_folder(self) -> None:
        def go():
            args = self.namespace()
            target = core.resolve_target(
                args.target, Path(args.prefix) if args.prefix else None)
            QtGui.QDesktopServices.openUrl(
                QtCore.QUrl.fromLocalFile(str(target.folder)))
        self._guard(go)


def main() -> int:
    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName("dlss5-proton")
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())

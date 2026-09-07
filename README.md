# dlss5-linux

DLSS 5 neural rendering for games running under Proton on Linux: a small
engine, a CLI and a PySide6 GUI that pick the right injection route for a
game, install it, write the Proton launch configuration, and read the logs
back to tell you whether it worked.

The engine is a Linux port of [DLSS5-Autopilot](https://github.com/Kizzuwatnaa/DLSS5-Autopilot)
(Windows, MIT): its `core/` is vendored unmodified and everything Linux-specific
lives in `linuxport/`, so upstream updates drop in. Alongside it sits the older
standalone tool (`proton-tool/dlss5_proton.py`) that the port still calls for
Proton-specific work: prefix discovery, Steam launch options, and swapping a
game's own DLSS runtimes.

Tested on an RTX 5090 with proton-cachyos 11 and GE-Proton 11 (Sept 2026).
Everything here is experimental community tooling around a leaked NVIDIA
runtime; nothing is official, and none of the binaries are redistributed
by this repository.

## What works (measured, not assumed)

| game | api | route | result |
|---|---|---|---|
| FINAL FANTASY VII REBIRTH | D3D12 + DLSS | OptiScaler (nightly) | DLSS 310.9 + NR, HDR, ~5-8 ms/frame at 4K |
| 007 First Light | D3D12 + DLSS | OptiScaler (nightly) | works; the build that fixed the v0.2.x deadlock |
| Horizon Forbidden West | D3D12 + DLSS | OptiScaler as winmm.dll | works |
| Stellar Blade | D3D12 + DLSS | native (ReShade + RenoDX) or OptiScaler | both work; native keeps the game's FG |
| FINAL FANTASY XIV | D3D11 + DLSS | OptiScaler dx11on12 bridge | works via XIVLauncher-RB + umu, see findings |
| Dreamfall Chapters | D3D11, no DLSS | feeder (private D3D12 device) | works, DLAA |
| FINAL FANTASY VII REMAKE | D3D12, no DLSS | feeder (same-device) | does not work: create faults in vkd3d-proton |
| Cyberpunk 2077 (GOG, RED4ext) | D3D12 + DLSS | OptiScaler | livelock; incompatible with the mod stack |

`FINDINGS.md` has the why for each row and the dead ends, so nobody repeats them.

## Install

Requirements: Python 3.11+, `PySide6` for the GUI, `7z` (p7zip) for the
OptiScaler nightly archives, Steam or a Proton launcher, an NVIDIA driver that
carries `nvngx_dlssnr.dll` (or the runtime archive next to the tools).

```bash
git clone https://github.com/pantsoftime/dlss5-linux
cd dlss5-linux/dlss5-linux
python3 dlss5_linux.py --scan                 # every Steam game with its route
python3 dlss5_linux.py recommend "Stellar Blade"
python3 dlss5_linux.py install "Stellar Blade" --indicator
python3 dlss5_linux.py launch-options "Stellar Blade" --apply   # Steam closed
python3 dlss5_linux.py verify "Stellar Blade"                    # after one launch
python3 dlss5_gui_linux.py                    # the same, as a three-page wizard
```

Non-Steam games take a folder or `.exe` (`--prefix` for the Wine prefix if it
cannot be derived). Final Fantasy XIV through XIVLauncher-RB is detected and
its `launcher.ini` overrides are written by `launch-options --apply`.

### Builds and pins

* `--optiscaler default` is the y4my4my4m fork nightly of 2026-09-06 (build
  7b7220bb). Upstream's `nightly` tag rolls daily, so the pinned hash only
  matches that day's asset: keep the archive next to the tools, or point
  `--optiscaler` at a local `.zip`/`.7z`. `fallback` is Dagherbou v0.1.2,
  `latest` whatever Dagherbou publishes (v0.2.x deadlocked under Proton here).
* `dlls <game> install` swaps the game's own DLSS SR/RR/FG runtimes for the
  newest archive beside the tools (310.9.0 at the time of writing); Streamline
  is left alone on purpose. `dlls <game> restore` undoes it.
* The feeder pin is DLSS5-Feeder 0.14.0-beta.5 (Dreamfall Chapters, D3D11, VORT
  vectors, classic RenoDX add-on); v0.12.0 is kept as the previous known-good.
* `scan_updates.py` lists what moved upstream across every project this
  depends on. Run it before moving a pin; test a new build on one game first.

## Layout

```
dlss5-linux/            the port: core/ (vendored upstream), linuxport/, CLI, GUI
proton-tool/            the standalone tool the port delegates Proton work to
mv-providers/vort/      VORT motion-vector shader (MIT) for the feeder route
examples/ffxiv/         one-shot FFXIV setup under XIVLauncher-RB + the ini keys
scan_updates.py         upstream release watcher
FINDINGS.md             what was learned, per route and per failure
```

## Credits

DLSS5-Autopilot (Kizzuwatnaa), OptiScaler and the DLSS-NR forks (Dagherbou,
y4my4my4m), DLSS5-Feeder (jlrouzies-fr), RenoDX (clshortfuse), the RHI build
catalogue (RankFTW), vort_Shaders (vortigern11), dxvk-nvapi and vkd3d-proton.

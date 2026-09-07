# dlss5-linux — porting DLSS5-Autopilot to Proton

**Status (2026-09-05): CLI complete and verified; PySide6 GUI ported and
smoke-tested headlessly against this library.**

    ./dlss5_gui_linux.py                 the three-page wizard (see AESTHETIC.md, docs/gui_page*.png)

    ./dlss5_linux.py recommend|install|uninstall|verify|launch-options <game>
    ./dlss5_linux.py --scan
    <game> = Steam appid, exact name, unambiguous substring, folder, or .exe

Install flags: `--route`, `--addon FILE`, `--dlssnr LABEL`, `--dlss LABEL`,
`--optiscaler default|latest|<tag>|<zip>`, `--feeder-tag`, `--proxy`,
`--no-keep-game-dlss`, `--force-route`, `--dry-run`, `-y`.

Upstream: [Kizzuwatnaa/DLSS5-Autopilot](https://github.com/Kizzuwatnaa/DLSS5-Autopilot)
v1.6.1, MIT. `core/` is vendored **unmodified** (see `UPSTREAM`); every Linux
change is a monkey-patch in `linuxport/`, applied once by `linuxport.activate()`.
That keeps upstream diffs trivially mergeable — the author ships several times a
day and we want to keep pulling.

## What we found

- **All 28 `core` modules import on Linux unchanged.** Windows is confined to
  six places: `games._steam_root()` (registry), `gpu.detect()`/`driver_version()`
  (registry), `vulkan.py` (layer registry), `emulators.py` (registry/ProgramFiles),
  five `%LOCALAPPDATA%` paths, and Win32 DPI/titlebar calls in `gui.py`.
- `pe.py`, `dlss.py`, `installer.py`, `diagnose.py`, `reshade_ini.py`,
  `sources.py`, `feedcfg.py`, `optiscaler.py` are **pure** and already encode
  things we learned by hand: proxy-name conflict avoidance, the Unreal
  plugin-folder DLSS layout, competing-NGX-hook detection, RE-Engine Agility
  SDK promotion.
- Their `native` route plan is **file-for-file** what we installed by hand on
  Stellar Blade. Their `feeder` plan matches Dreamfall.
- Build catalog is **RankFTW/rhi-repo** (85 releases: every dlss/dlssg/dlssd/
  streamline/renodx-dlss5/renodx-dlss-SF/dlssnr variant, incl. RTX40 + SF
  builds). Replaces our hand-curated archives in `DLSS5_work/`.
- Tests: `test_reshade_ini.py` passes on Linux. `test_all.py` / `test_install.py`
  hardcode the author's Windows fixture path — not a portability signal.

## linuxport/ — what each shim does and why

| shim | replaces | reason |
|---|---|---|
| `linux_gpu.py` | `gpu.detect`, `gpu.driver_version` | `nvidia-smi --query-gpu=name,driver_version,compute_cap` → `(name, sm)`; 5090 = sm 120. Everything downstream keys off that int. |
| `games.py` | `games._steam_root`, `games.scan_steam` | Linux Steam roots (native, Flatpak, Snap, `$STEAM_ROOT`); filters Proton/SteamVR tooling. Library walk + appmanifest names are upstream's, unchanged. |
| `paths.py` | `net.CACHE`, `prefs.FILE`, `log.DIR`, `diagnose.STANDALONE_LOG` | XDG. |
| `pe.py` | `pe._has_d3d12_agility_sdk`, `pe.detect_api` | Widens DX12 promotion to engine plugin folders (Stellar Blade came back DX11); Unity via `UnityPlayer.dll` **or `*_Data/`** (old Unity has no UnityPlayer); RE-Engine byte needles. From our tool. |
| `policy.py` | `dlss.detect` | **Recommend `native` over `optiscaler`** when both are offered. Upstream steers every RTX card to OptiScaler; under Proton its NVAPI check runs before `nvapi64.dll` loads → `dlss: false` → silent XeSS (Stellar Blade, FF7R). OptiScaler stays listed (wins on Horizon). |
| `state.py` | `dlss._ours` | Recognises `_dlss5_proton_state.json` so a feeder install's `nvngx_dlss.dll` isn't read as native DLSS (Dreamfall was being steered to `native`). |

Two rules for shims: never edit `core/`; never name a package `platform/`
(shadows stdlib — cost 10 minutes).

## Verified on this box

```
GPU: RTX 5090 sm=120, driver 610.57.04
44 games with an exe: 22 native, ~20 feeder, 2 remix, 2 bridge, 1 optiscaler
007 / FF7R / Horizon FW / Stellar Blade  DX12 x64 dlss=y -> native
Dreamfall Chapters                        DX11 x64 dlss=n -> feeder
Split Fiction (DX12, FSR/XeSS, no DLSS)  -> optiscaler   (correct: native not offered)
Hellblade 1, Valkyrie Elysium (DX11+DLSS) -> bridge
HL2 RTX, Portal Prelude RTX               -> remix
```

## Plan

### 1. Install route -- DONE
Proven end-to-end on a fixture (real SB exe + Unreal plugin-folder DLSS):
wrote ReShade 6.8.0 (`dxgi.dll` byte-identical to what our tool installed on
live Stellar Blade), the perf-fix add-on, `nvngx_dlssnr` 310.8.0, `ReShade.ini`
and a manifest; `diagnose.analyse()` read it; `uninstall` returned the folder
to exactly its original files. `linuxport/seed.py` stages our archives under
the installer's cache names, so this ran offline. Route validation refuses a
route the game does not offer; a running game is refused. Original notes:
- ~~`Options.renodx_local` ← our `--addon`~~ done (`--addon`, `--dlssnr`, `--dlss`, `--feeder-tag`) (perf-fix / classic `++`);
  `Options.feeder_tag` ← `--feeder-zip`; OptiScaler pin ← their `optiscaler.resolve()`
  must be overridable (they fetch **latest**, which is the v0.2.x that deadlocks
  here). Done: `linuxport/pins.py` -- a **default**, not a clamp (v0.1.2 until 2026-09-06, now the local y4my4m nightly zip, v0.1.2 as fallback);
  `--optiscaler latest|<tag>|<zip>` picks anything else per install.
- Their `_proxy_name()` defaults to `dxgi.dll`; keep our Horizon lesson
  (`winmm` when a mod owns `dxgi`) — their `optiscaler.suggest_proxy()` already
  does this for OptiScaler; extend to ReShade.

### 2. Proton layer -- DONE (`linuxport/proton.py`)
Imported from `dlss5_proton.py` rather than re-ported (one source of truth
while both tools coexist); the module only adapts `games.Game` <-> appid.
Covered: prefix discovery, NGX-bridge check, `localconfig.vdf` read,
`WINEDLLOVERRIDES` generation (`launch-options`, printed after every install),
running-game refusal. Still ours only: the `dlls` swap, `--dlss-dest`.
- prefix discovery (`find_prefix`, stub-prefix avoidance), `system32` NGX bridge check
- **`WINEDLLOVERRIDES` generation + Steam `localconfig.vdf` read/unescape**
  (`launch_options_for`, `build_launch_options`) — the #1 silent failure
- `PROTON_DLSS_UPGRADE` awareness (it supplies 310.9.0 via `system32/umu/`)
- `dlls` subcommand (SR/RR/FG swap with its own state file)
- `--optiscaler-zip` / `--feeder-zip` per-game pins

### 3. Verify -- DONE (`linuxport/verify.py`)
Upstream findings first, then: `WINEDLLOVERRIDES` present, prefix + NGX
bridge, running, and per-route log readers. Verified live: Horizon "Working."
+ `dlss: true`; Rebirth "Working." + **`dlss: false` XeSS-fallback warning**;
007 `dlss: true` (not affected); Stellar Blade `native`, 23 NR creations.
Two manifest readers had to be patched (`installer._previous_manifest` AND
`diagnose._manifest`) -- they do not share one. Readers added:
`VK_ERROR_DEVICE_LOST`, `queryNvapi Failed` → `dlss: false` (XeSS fallback
warning), `Upscaler support` line, `DLSS-NR running at WxH`, `LogToFile=auto`
trap, log-mtime-vs-install check.

### 4. GUI -- DONE (`dlss5_gui_linux.py`, 576 lines, PySide6 6.11)
Ported their structure onto PySide6 (Tk is installed now, but their `gui.py`
is Tk layout with Win32 calls and our stack is already Qt). Three pages --
architecture -> pick a game -> install -- with the rail, step markers, cards,
route combo labelled by `dlss.fit()`, reality blurb, per-route row visibility
(`_apply_route` logic reproduced exactly), the amber work-resolution dial,
"what will happen?", "did it work?" (= `linuxport.verify`), "check versions",
"launch options" (copies to clipboard). Widget->`Options` mapping mirrors
upstream's `_opts()`. **Dropped on purpose:** video/YouTube/webcam card, RTX
Remix card and route, self-update banner, Win32 titlebar/DPI. **Added:** an
"optiscaler build" row (default / latest / tag / local zip -> `pins`), a
`proton:` line in the rail, and a `!!` WINEDLLOVERRIDES row on the install
page. Look documented in `AESTHETIC.md`; renders in `docs/gui_page2.png` and
`docs/gui_page3.png`. Headless smoke (`QT_QPA_PLATFORM=offscreen`) drives all
three pages, every route's `_opts()`, preview, diagnose and launch-options.
Not yet exercised on a real display -- run it.

### 5. Ongoing
- `scan_updates.py` already watches all eight route repos + rhi-repo's parent.
- Pull upstream weekly: `git -C ../autopilot-upstream pull`, diff `core/`,
  re-vendor, re-run `--scan` and the five-game check above.

## Known gaps

- **Native route writes `nvngx_dlss.dll` beside the exe** when the game keeps
  its own in a plugin folder (`present()` checks `install_dir` only). Inert
  under Proton (`PROTON_DLSS_UPGRADE` governs SR), but a divergence from our
  hand-verified layout; needs a `find_dlss_files`-aware `keep_game_dlss`.
- **Installs made by `dlss5_proton.py` with files outside `install_dir`**
  (our plugin-folder `nvngx_dlssnr.dll` copy) cannot be expressed in the
  synthesized manifest, so their `uninstall` leaves that copy. Use our
  `restore` for those; installs made by this tool are fully reversible.
- `find()` resolves appid / exact name / unambiguous substring; ambiguous
  substrings list candidates and exit.
- No Vulkan-layer route yet (`vulkan.py` is registry-based; on Proton ReShade's
  Vulkan layer is a different mechanism entirely).
- `emulators.py`, `remix*.py`, `video.py` untouched — Windows-specific or out of scope.
- ~~Non-Steam games~~ done: `--prefix PATH` / `[ set prefix ]` / `$WINEPREFIX`,
  remembered per folder in `~/.config/dlss5-linux/settings.json`; chosen
  folders are remembered too (`prefs.installs()`) and listed as `manual` by
  `scan_all()`; launch help gives the Lutris env form, the DLL-overrides-grid
  gotcha (key `winmm`, value `n,b`) and the non-Steam-game form.
- **Foreign installs are detected**: `install_state()` reports `installed:
  <route>` (either tool's manifest) or `found: reshade as dxgi.dll / optiscaler
  / nvngx_dlssnr / *.addon64` for hand-placed files with no record. First run
  surfaced two we never recorded: Dragon Quest XI and Final Fantasy XVI.
- **AnWave-DLSS** (SimonMacer, "DLSS Global Override Mode") is Windows-only:
  it rewrites the driver's NGX DriverStore copies so every game gets the
  newest SR/FG/RR, toggles the on-screen DLSS indicator (registry), and
  firewalls `nvngx_update.exe`. Linux equivalents: per-game only --
  `PROTON_DLSS_UPGRADE=1` (Proton copies its bundled DLLs into
  `system32/umu/`), `DXVK_NVAPI_DRS_*` env vars, and our `dlls` swap. Done:
  the **DLSS indicator** -- NOT via the registry (Proton/dxvk-nvapi overwrite
  `NGXCore\\ShowDlssIndicator` every launch from
  `DXVK_NVAPI_SET_NGX_DEBUG_OPTIONS`); `PROTON_DLSS_INDICATOR=1` is the switch,
  wired as `--indicator` and a GUI checkbox, plus `--apply` / *apply to
  steam* which writes `localconfig.vdf` (backed up, refuses while Steam runs;
  proven on a copy). `launch-options` now defaults to the recorded proxy
  instead of `dxgi.dll`. **Verified on screen** (Stellar
  Blade: `Preset: K / DLSS v310.9.0 (nvapp_override) HDR 5120x1440`), and it
  immediately showed the game had fallen back to its shipped 310.1.0/preset J
  when `PROTON_DLSS_UPGRADE` was dropped. Re-adding it plus
  `DXVK_NVAPI_DRS_NGX_DLSS_SR_OVERRIDE_RENDER_PRESET_SELECTION=RENDER_PRESET_K`
  **coexists with the RenoDX add-on** -- the upstream "Nvidia Override" warning
  is about the Windows NVIDIA App, not Proton's DLL copy. Registry attempt documented so it
  is not repeated. Original note: via the prefix registry
  (`HKLM\SOFTWARE\NVIDIA Corporation\Global\NGXCore\ShowDlssIndicator=1024`)
  -- untested under Proton, but `_nvngx.dll` is NVIDIA's own NGX core running
  in Wine and reads HKLM, and it would have shown the XeSS-vs-DLSS problem on
  screen instantly. A "global" mode = writing `PROTON_DLSS_UPGRADE=1` into
  every Steam game's launch options (Steam must be closed to edit
  `localconfig.vdf`).

### 2026-09-07 -- upstream sync + findings fold-in

- `core/` re-vendored at DLSS5-Autopilot **v1.7.1** (8225fff; was v1.6.1
  23f96c6). Every symbol the shims wrap still exists; upstream's diagnose now
  spots the feeder create crash by itself. New upstream Options fields (`dxvk`,
  `fg`, `mfg`, `remix_swap`) are passed through untouched.
- `linuxport/policy.py`: OptiScaler (nightly) is the recommendation for any
  game with DLSS; D3D11 + DLSS gets the bridge reasoning; feeder on D3D12 is
  flagged as faulting under Proton.
- `linuxport/tuning.py` (new): LogToFile on after every OptiScaler install,
  `Dx11Upscaler=dlss_12` when the loader has the token (else fsr22_12),
  per-game ini overlays + notes (FFXIV).
- `linuxport/proton.py`: `override_entries()` (proxy + d3d12/d3d12core for
  the D3D11 bridge + d3dcompiler_47 when app-local), XIVLauncher-RB
  detection (`~/.xlcore`), `set_launcher_overrides()`.
- `linuxport/pe.py`: the DX12 walk-up stops at the game's own tree (a
  non-Steam publisher folder made FFXIV look DX12 after our own payload).
- `linuxport/verify.py`: D3D12 bridge check, DLSS runtime version, NR cost
  min/median/max, multipass-vs-multi-feature warning, feeder MV probe and
  D3D12 create fault, launcher.ini override check.
- CLI: `dlls <game> [status|install|restore]`, `launch-options --apply` on
  XIVLauncher-RB games, per-game notes after install. GUI: fallback build
  choice, "apply overrides", "upgrade game dlss", "game notes".
- Untested live: a fresh install through the port's own path (tuning hooks),
  the GUI on a display, feeder 0.14.0-beta.5 on Dreamfall.

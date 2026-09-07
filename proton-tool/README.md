# dlss5-proton

Linux/Proton installer for the RenoDX **DLSS Neural Rendering** ReShade add-on —
the counterpart to the Windows "DLSS5 Easy Installer" in
`../DLSS5-Easy-Installer-v1.0.1-public-safe/`.

Same shape as the Windows tool: resolve the game, run a compatibility scan, back
up every file it touches, install ReShade + the add-on + the DLSS runtime, and
restore in one command. The work that's specific to here is Proton prefix
discovery, `WINEDLLOVERRIDES`, and knowing that on this machine the DLSS runtime
often lives in the prefix's `system32` rather than beside the game .exe.

No NVIDIA binaries are bundled. You supply the DLSS ZIP.

---

## How the pieces stack up

```
Steam  →  Proton (CachyOS)  →  Wine prefix
                                 │
                                 ├─ dxgi.dll          ← ReShade 6.8.0 (add-on build)
                                 │    └─ renodx-dlss5++.addon64   ← the actual mod
                                 │
                                 ├─ game .exe → D3D12 → VKD3D-Proton → Vulkan
                                 │
                                 └─ NGX: nvngx.dll → _nvngx.dll → libnvidia-ngx.so
                                          ↑ from /usr/lib/nvidia/wine/
                                    nvngx_dlss.dll / nvngx_dlssnr.dll ← game folder
```

The add-on is a normal ReShade add-on, so the only injection trick needed is
getting Wine to load *our* `dxgi.dll` instead of its built-in one. That is what
`WINEDLLOVERRIDES="dxgi=n,b"` does (`n` = native first, `b` = built-in fallback).

---

## Requirements

| Requirement | Your machine | Status |
|---|---|---|
| RTX 50-series GPU | RTX 5090 | ok |
| D3D12 game already using NGX DLSS | many, see `scan` | ok |
| ReShade add-on build | downloaded + hash-pinned by the tool | ok |
| `7z`, `curl`, `python3` | present | ok |
| `protontricks` (for `d3dcompiler_47`) | present | ok |
| NVIDIA driver | **610.57.04** | see below |

**Read this before you spend an evening on it.** The add-on's own metadata
declares **Windows driver 615.00** as its minimum. You are on the Linux
**610.57.04** branch. NVIDIA's Linux and Windows branch numbers are not directly
comparable, so the tool cannot check this for you and only warns — but if the
add-on loads, shows up in the ReShade overlay, and then never initialises or sits
on `WAITING FOR GAME DLSS` forever, an under-spec driver branch is the first
thing to suspect, ahead of anything game-specific.

The second unknown is `nvngx_dlssnr.dll` itself. Ordinary DLSS DLL swapping is
well-proven under Proton, but *neural rendering* is a newer NGX feature, and
whether the Linux NGX path exposes it on your driver is exactly what this
experiment finds out. If it fails, it should fail by doing nothing rather than by
breaking the game — and `restore` puts everything back byte-for-byte.

---

## Install the tool

```bash
cd /path/to/dlss5-linux/proton-tool && chmod +x dlss5_proton.py
```

Optionally put it on your PATH:

```bash
ln -sf /path/to/dlss5-linux/proton-tool/dlss5_proton.py ~/.local/bin/dlss5-proton
```

It finds the add-on and your DLSS ZIP automatically by looking next to itself and
in `../` (it picks the newest ZIP that actually contains `nvngx_dlssnr.dll`).
Override with `--addon` / `--zip`.

---

## Workflow

### 1. See what you've got

```bash
python3 dlss5_proton.py scan --dlss-only
```

Lists every installed Steam game, whether a Proton prefix exists, and where its
`nvngx_dlss.dll` lives (`game` / `prefix` / `-`). Games showing `-` are not
candidates.

### 2. Dry-run the compatibility scan

```bash
python3 dlss5_proton.py check "Resident Evil Requiem"
```

Target can be an appid, a game-name substring, a folder, or a `.exe`. This
changes nothing on disk. It reports the detected rendering executable, the
prefix, D3D12 evidence, the existing DLSS runtime, and where the new files would
go.

### 3. Install

```bash
python3 dlss5_proton.py install "Resident Evil Requiem"
```

Defaults are the conservative ones: **minimal** mode (just the add-on +
`nvngx_dlssnr.dll`, leaving the game's DLSS stack alone), loader `dxgi.dll`, and
`--dlss-dest auto`.

Useful flags:

| Flag | Use it when |
|---|---|
| `--full` | Minimal mode loads but can't hook. Replaces the whole DLSS/Streamline set. Higher crash risk — try minimal first. |
| `--dlss-dest both` | The overlay says `WAITING FOR GAME DLSS`. Also writes into the prefix's `system32`. |
| `--loader d3d12.dll` | The game ships its own `dxgi.dll`. |
| `--skip-d3dcompiler` | You don't want the tool running protontricks. |
| `-y` | No prompts. |

### 3b. Games with no DLSS at all — `--feeder`

The base add-on can only hook a DLSS pass the game already runs. **DLSS5-Feeder**
([upstream](https://github.com/jlrouzies-fr/DLSS5-Feeder)) removes that limit: it
synthesises a DLAA contract from ReShade's depth buffer plus *estimated* motion
vectors and drives neural rendering through a private D3D12 device. That makes
**D3D11 games — most Unity titles — viable**, and games that have never heard of
DLSS.

```bash
python3 dlss5_proton.py install "Some Unity Game" --feeder --mv-provider ~/Downloads/ReshadeMotionEstimation
```

Feeder mode changes the install in four ways:

| | normal | `--feeder` |
|---|---|---|
| Requires game DLSS | yes (hard fail without it) | no |
| Installs | add-on + `nvngx_dlssnr.dll` | plus **`nvngx_dlss.dll`** |
| API | D3D12 only | **D3D11 or D3D12** |
| Extra files | — | `dlss5-feed.addon64`, `DLSS5_Feed.fx`, `dlss5-feed.cfg` |

The feeder artifacts are downloaded from the upstream GitHub release, pinned to
`v0.12.0` and **verified against a hard-coded SHA-256** — same handling as
ReShade. A re-tagged release fails the check rather than installing silently.
Use `--feeder-zip` to test a newer build on one game without moving the pin;
upstream is beta-heavy, and there is currently no stable release above `v0.12.0`.

**You must supply a motion-vector provider.** The feeder reads the community
`texMotionVectors` texture and bundles no third-party shader code, so neither
does this tool. Use
[ReshadeMotionEstimation](https://github.com/JakobPCoder/ReshadeMotionEstimation)
(CC BY-NC 4.0) or `qUINT_motionvectors`, and point `--mv-provider` at the folder
or `.fx`. If you already have MartysMods Launchpad in that game's
`reshade-shaders`, the scan detects it and you can skip the flag. Without a
provider the image is sharp when still and smears when you move.

Turning it on in-game (order matters): **Home** → enable your motion-vector
technique → enable **DLSS 5 Feed** *below it* → enable neural rendering in the
**DLSS 5 Neural Rendering** panel. Turn the game's MSAA/SSAA off.

Feeder-specific gotchas:

- **Incompatible with NVIDIA Smooth Motion and OptiScaler.** Don't run either.
- **DLAA only** — no upscaling performance win yet.
- **64-bit only here.** Upstream supports 32-bit via a helper process and Vulkan
  via a layer; this tool doesn't install those paths.
- Diagnostics live in `dlss5-feed.log` next to the exe. `mode=1` in
  `dlss5-feed.cfg` is a transport test that runs no NGX at all — the fastest way
  to tell "the round trip is broken" from "NGX is unhappy".

### 3c. Games that aren't in your Steam library

Pass the folder or the `.exe` instead of an appid — everything else is identical:

```bash
python3 dlss5_proton.py install ~/Games/MyGame/drive_c/Games/MyUnityGame --feeder --mv-provider ./mv-providers/ReshadeMotionEstimation
```

Three things adapt automatically:

- **Prefix discovery.** If the game lives inside a prefix (`<prefix>/drive_c/...`,
  the usual Lutris/Heroic/umu layout), the prefix is derived by walking up to the
  `drive_c` ancestor. A sibling `pfx/` or `prefix/` directory is also detected.
  `--prefix` still overrides.
- **`d3dcompiler_47`.** protontricks needs an appid, so for non-Steam games the
  tool copies a native `d3dcompiler_47.dll` from a prefix that already has one
  and places it beside the executable, adding `d3dcompiler_47=n` to the printed
  overrides. This matters in feeder mode, where `.fx` effects must compile. The
  same fallback kicks in when protontricks fails on a Steam game.
- **Launch instructions.** It prints the environment block for Lutris/Heroic/umu
  rather than a Steam launch-options string, plus the Add-a-Non-Steam-Game route
  if you'd rather reuse Proton.

### 4. Set the Steam launch options

The installer prints the exact string, with your existing variables preserved.
Paste it into **Properties → General → Launch Options**. For RE Requiem it comes
out as:

```
PROTON_ENABLE_WAYLAND=1 PROTON_ENABLE_HDR=1 ENABLE_HDR_WSI=1 PROTON_DLSS_UPGRADE=1 WINEDLLOVERRIDES="dxgi=n,b" %command% /WineDetectionEnabled:False
```

You can re-print it at any time:

```bash
python3 dlss5_proton.py launch-options "Resident Evil Requiem"
```

### 5. In-game

1. Enable **DirectX 12** and **DLSS** in the game's own graphics settings.
2. Start at **1920×1080, SDR** for the first attempt.
3. Press **Home** for the ReShade overlay.
4. **Add-ons → DLSS Neural Rendering → enable.**

### 6. Undo

```bash
python3 dlss5_proton.py restore "Resident Evil Requiem"
```

Restores every file to its exact pre-install bytes (verified by SHA-256), deletes
files that didn't exist before, and keeps the backup folder. `status <game>` shows
what's installed and flags anything changed since.

---

## Updating the game's own DLSS runtime — `dlls`

Games ship whatever DLSS runtime they were built against and almost never update
it. Horizon Forbidden West still carries 3.5.10, from before the transformer
model. This matters for us directly: in OptiScaler mode neural rendering runs
**after** the game's own upscaler, so the upscaler's version sets the quality of
the image NR is handed.

```bash
python3 dlss5_proton.py dlls 2420110 status     # what's there vs what's available
python3 dlss5_proton.py dlls 2420110 install    # swap in the newest
python3 dlss5_proton.py dlls 2420110 restore    # put the game's own back
```

```
Horizon Forbidden West™ Complete Edition (2420110)
------------------------------------------------------------
  Super Resolution     3.5.10.0     nvngx_dlss.dll
                       310.9.0.0    available
  Frame Generation     3.5.10.0     nvngx_dlssg.dll
                       310.9.0.0    available
```

Sources are the archives sitting next to the tool; `--from <zip-or-folder>` picks
a specific one. Nested ZIPs are handled — the 310.9.0 drop is a ZIP of three ZIPs.
Nothing is extracted to disk: members are streamed straight out of the archive and
only the DLL actually being installed is written, to a temp dir.

`--only sr,rr,fg` limits which components move. Downgrades are refused unless you
pass `--force`.

This keeps **its own state file** (`_dlss5_proton_dlls.json`) separate from the
mod's. The two are on different schedules — `restore` + `install` runs every time
OptiScaler ships a build, and that must not silently revert a DLL swap or restore
a swapped DLL as if it were the game's original.

Three things it deliberately won't touch:

- **`nvngx_dlssnr.dll`** — that belongs to the neural-rendering mod, which already
  places the newest build and tracks it in its own state.
- **Files the mod owns.** In feeder mode the mod installs its own `nvngx_dlss.dll`;
  the swap skips any path recorded in the mod's state file.
- **The prefix's `system32`.** Proton rewrites `nvngx_*.dll` there on every launch,
  so a swap would silently disappear.

It also only replaces DLLs the game already has. Dropping a runtime into a game
that never shipped one achieves nothing — the game asks NGX for the features it
was built to use.

### Streamline

`--streamline` additionally swaps `sl.*.dll`. It's off by default and should stay
that way unless you're chasing something specific: `sl.interposer` is loaded by
the game's own code and its plugins are version-matched against it, so swapping it
works in some titles and breaks others. If a game stops launching after that, run
`dlls <game> restore`.

---

## Proton-specific gotchas

**`PROTON_DLSS_UPGRADE=1` is narrower than it looks.** In proton-cachyos
(`protonfixes/upscalers.py`) it manages exactly three filenames —
`nvngx_dlss.dll`, `nvngx_dlssd.dll`, `nvngx_dlssg.dll` — and installs them to
`pfx/drive_c/windows/system32/umu/`. It never touches `nvngx_dlssnr.dll` and never
writes into the game folder.

So for **minimal mode it is harmless — leave it on.** It only matters if you use
`--full` together with `--dlss-dest system32` or `both`, where your copies of those
three names can be shadowed by Proton's `umu/` copies. In that combination the tool
strips the flag from the launch options it prints; use the printed string.

**HDR first.** Your launch options generally carry `DXVK_HDR=1 PROTON_ENABLE_HDR=1
ENABLE_HDR_WSI=1`. HDR + swapchain-upgrading add-ons is a known source of black
screens. Test in SDR at 1080p, then turn HDR back on.

**Unreal games hide their DLSS runtime.** Stellar Blade keeps it in
`SB/Plugins/Runtime/Nvidia/DLSS/Binaries/ThirdParty/Win64/`, not next to
`SB-Win64-Shipping.exe`. `--dlss-dest auto` (the default) writes to both the exe
folder and that plugin folder, because NGX looks in the latter.

**`d3dcompiler_47`.** ReShade needs the real one to compile `.fx` effects; Wine's
stub isn't enough. The installer runs `protontricks <appid> d3dcompiler_47` for
you. The RenoDX add-on is compiled C++ and doesn't strictly need it, so a
protontricks failure is a warning, not a fatal.

**Two prefix locations.** You have both `steamapps/compatdata/<appid>/pfx` and
`~/proton-prefixes/<appid>/`. The tool prefers compatdata and falls back to the
custom root; pass `--prefix` to force one.

**Wayland.** `PROTON_ENABLE_WAYLAND=1` is fine with ReShade, but if the overlay
won't take keyboard input, that's the first thing to flip off.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| No overlay on **Home** | ReShade not loaded | Confirm the `WINEDLLOVERRIDES` launch option is actually saved; check `dxgi.dll` sits beside the exe the game really runs |
| Overlay opens, no **Add-ons** tab | Non-add-on ReShade build | `restore`, then reinstall — the tool only ever fetches the add-on build |
| `WAITING FOR GAME DLSS` | Add-on can't see a DLSS output | Turn DLSS on in-game; then `--dlss-dest both`; then `--full` |
| Add-on listed but greyed out | NGX never initialised | Most likely the driver-branch gap above |
| Black screen on launch | Swapchain/HDR conflict | Disable HDR, 1080p SDR windowed |
| Game won't start at all | Loader conflict | `restore`, retry with `--loader d3d12.dll` |
| `game already has installer state` | Previous install present | `restore` first |

Proton logs: add `PROTON_LOG=1` to the launch options and read
`$HOME/steam-<appid>.log`. ReShade writes `ReShade.log` beside the loader — that's
the file that says whether the add-on was found and loaded.

---

## Good first candidates in your library

Ranked by likelihood of working, from `scan`:

1. **Resident Evil Requiem** (`3764200`) — RE Engine, D3D12, ships the full
   Streamline set in the game folder, and it's one of the two games the Windows
   installer has an explicit profile for. Best first test.
2. **PRAGMATA** (`3357650`) — same engine and layout.
3. **Stellar Blade** (`3489700`) — UE5, exercises the plugin-folder path.
4. **Clair Obscur: Expedition 33** (`1903340`), **Split Fiction** (`2001120`),
   **Keeper** (`3043580`) — UE5 D3D12.

Skip anything with anti-cheat, and skip D3D11-only titles — the add-on supports
neither.

---

## What the tool will not do

- It won't add DLSS to a game that doesn't already have it.
- It won't touch a non-ReShade `dxgi.dll` — that's a hard stop, not a warning.
- It won't install a second ReShade if one is already there under another name.
- It won't redistribute NVIDIA binaries; the DLSS ZIP is yours and stays local.

Anti-cheat: injected rendering DLLs can trigger bans. Offline and single-player
only.

---

## GUI

```bash
python3 dlss5_gui.py
```

A PySide6 front-end over the same library — every button builds the identical
argparse namespace the CLI does and calls straight into `dlss5_proton`, so there
is one implementation of install/restore/verify and the GUI cannot drift from it.

- **Game** — pick from your Steam library (optionally filtered to titles that
  actually ship a DLSS runtime), or browse to any folder/`.exe` for non-Steam
  games. The Wine prefix is auto-detected; override it if you need to.
- **Mode** — Minimal / Advanced / Feeder. Selecting Feeder enables the
  motion-vector provider picker and pins the DLSS destination to `game`.
- **Options** — ReShade proxy name, DLSS destination, and optional overrides for
  the DLSS ZIP and add-on paths.
- **Actions** — Check (dry run), Install, Verify, Restore, copy launch options to
  the clipboard, open the game folder.

Long operations run on a worker thread with output streamed live, so the window
never freezes. Verify runs automatically after Install and Restore.

### Verify

The most useful part, and available on the CLI too:

```bash
python3 dlss5_proton.py verify <game>
```

It reads `ReShade.log` and `dlss5-feed.log` and answers "did it actually work?"
in the order that matters, because every failure in this project was diagnosed
this way:

| Check | Catches |
|---|---|
| Installed / Files intact | missing or externally-modified files (configs the app rewrites are excluded) |
| Launch option | a missing `WINEDLLOVERRIDES` — corroborated against the log, since Steam only flushes launch options on exit |
| ReShade.log stale | **the mistake that cost two runs** — a "clean" result that was really ReShade never loading |
| ReShade loaded | which DLL it came from, and its version |
| Effects compile | missing `ReShade.fxh`-style include failures |
| Add-ons registered | the add-on build vs the plain build |
| NR runtime / resources | whether NGX initialised, at what resolution and format |
| NR evaluating | `count=1` then silence — the FF7 Rebirth failure |
| Dimension skips | the guide/output mismatch that `--full` fixes |
| Feeder feature / frames | `feature ready … DLAA`, frames delivered, missing MV provider |

---

## OptiScaler mode (default)

`--mode optiscaler` is now the default and **does not use ReShade at all**. It
installs [OptiScaler_DLSSNR](https://github.com/Dagherbou/OptiScaler_DLSSNR),
which runs the neural-rendering model immediately after the game's own upscaler,
on the same command list, before the UI is drawn.

```bash
python3 dlss5_proton.py install <game>
```

Why it supersedes the ReShade path on Linux:

- **It ships its own nvngx caller shim** (`nvngx.dll_dlssnr.dll`). The model
  checks that it was loaded by `nvngx.dll` at a Windows driver-store path that
  doesn't exist under Proton — this sidesteps that entirely.
- **It fixes bindless engines.** RenoDX's v4.x add-on captures and restores full
  D3D12 host state around the pass and crashes on bindless renderers; OptiScaler
  `v0.1.1.5-dlssnr` fixes exactly that class (007, RE Requiem, PRAGMATA, MHW,
  Dragon's Dogma 2).
- **It survives feature and swapchain rebuilds**, which killed every ReShade-based
  attempt — including FF7 Rebirth's supposed 1440p ceiling, which turned out not
  to be a resolution limit at all.
- **It auto-detects the colour space** from the game's DLSS buffer rather than
  assuming, which is what produced wrong colours on the native RenoDX path.

Requirements: RTX 50-series, and a game with **its own DLSS on DirectX 12** —
leave the game's DLSS **on**, since OptiScaler reads the depth and motion vectors
it already produces. D3D11 works via OptiScaler's D3D11-on-D3D12 bridge.

In-game: **Insert** opens the overlay (not Home), enable *Neural Rendering* under
DLSS Neural Rendering, Page Up/Down for stats.

The archive is hash-pinned and cached in `DLSS5_work/`. Proxy names available in
this mode: `dxgi`, `winmm`, `version`, `dbghelp`, `d3d12`, `wininet`, `winhttp`.

`verify` reads `OptiScaler.log`: forwarder loaded, NR dispatch count and
resolutions, colour path, and error count — ignoring messages OptiScaler merely
forwards from the game's Streamline library (the OTA updater can never spawn
`nvngx_update.exe` under Wine) and the harmless `queryNvapi` GPU-identification
failure.

### Pinning a game to a specific build

Both upstreams ship several times a day, and a build that fixes one title can
break another. `--optiscaler-zip` and `--feeder-zip` pin one game each, without
moving the tool's default:

```bash
python3 dlss5_proton.py install 3768760 --loader dxgi.dll \
    --optiscaler-zip ../OptiScaler-DLSSNR-v0.1.2.zip

python3 dlss5_proton.py install "Some Unity Game" --feeder \
    --feeder-zip ../DLSS5-Feeder-0.14.0-beta.2.zip
```

The archive is used as given — its hash is not the pinned one by definition — and
the filename is recorded in the game's state file, so `status` shows which build
it's actually on. Both are exposed in the GUI as **OptiScaler build** and
**Feeder build**, each enabled only in its own mode; leaving them empty uses the
pinned version.

Keep spare archives in `DLSS5_work/`. `scan_updates.py` reports what's new
upstream without changing anything.

#### Known-bad builds

Upstream ships several times a day, and a build that fixes one title can hang
another. `--optiscaler-zip` pins one game without moving the global default:

```bash
python3 dlss5_proton.py install 3768760 --loader dxgi.dll \
    --optiscaler-zip ../OptiScaler-DLSSNR-v0.1.2.zip
```

The archive is used as given — its hash is not the pinned one by definition — and
the filename is recorded in the game's state file so `status` shows which build
it's actually on.

Known: **Dagherbou v0.2.0 and v0.2.0-patch1 hang 007 First Light and Stellar
Blade** during initial load, before the first loading screen. Black screen, cursor
and overlays still composited, process alive, presents stopped, no error in
`OptiScaler.log`.

**Default since 2026-09-06: the y4my4my4m fork's nightly of that day (build
7b7220bb)**, kept in `DLSS5_work/` as
`OptiScaler_v10.0.0-pre1_20260906_y4my4m-nightly.7z` (`.7z` archives are accepted
now, through the system `7z` binary). It passed FF7 Rebirth, 007 and Horizon with
HDR on, and its `Upscaler support ... dlss: true` removes the nvapi timing gate
that pushed v0.1.2 onto XeSS in some titles. Upstream's `nightly` tag rolls
daily, so the pinned hash only matches that day's asset — keep the archive.
Dagherbou v0.1.2 (`OptiScaler-DLSSNR-v0.1.2.zip`) stays as the fallback:

```bash
python3 dlss5_proton.py restore 3768760
python3 dlss5_proton.py install 3768760 --loader dxgi.dll \
    --optiscaler-zip ../OptiScaler-DLSSNR-v0.1.2.zip
```

The older ReShade-based paths remain as `--mode minimal|full|feeder`.

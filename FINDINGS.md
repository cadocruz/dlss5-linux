# Findings: DLSS 5 neural rendering under Proton

Everything below was measured on one machine (RTX 5090, NVIDIA 610.57,
proton-cachyos 11 / GE-Proton 11, CachyOS) between late August and early
September 2026. Dates matter: the projects involved ship several times a day.

## The build that matters: OptiScaler, y4my4my4m nightly

* **Dagherbou/OptiScaler_DLSSNR v0.2.0 and v0.2.0-patch1 deadlock under
  Proton** on some titles (007 First Light, Stellar Blade): black screen with
  the overlay drawn, every thread parked, no GPU fault; with an HDR10 swapchain
  they also lose the device. v0.1.2 did not deadlock but lost a race: its GPU
  capability check ran before the game loaded `nvapi64.dll`, `dlss: false`
  stuck for the session, and XeSS silently replaced the game's DLSS/DLAA.
* **y4my4my4m/OptiScaler_DLSSNR_Multipass_MFG nightly 2026-09-06 (7b7220bb)**
  fixed both: the deadlocking title runs, and `Upscaler support ... dlss: true`
  on every game tried. Its changelog names the mechanism: the overlay is drawn
  on the game's queue under vkd3d-proton, and Vulkan device creation no longer
  enumerates adapters through DXVK's DXGI (which re-entered its own hook).
  This is the default the tools pin.
* The `nightly` tag rolls daily. A newer nightly is a candidate to test on one
  game, not an upgrade.

## D3D11 games with DLSS (Final Fantasy XIV)

* The ReShade-based routes need a D3D12 device; OptiScaler bridges the game to
  D3D12 (`Dx11Upscaler=dlss_12`, a token only the nightly line carries; older
  builds only offer `fsr22_12`/`xess_12` on the bridge) and runs the model there.
* **The bridge needs vkd3d-proton's `d3d12`/`d3d12core`.** Proton only injects
  those overrides on Steam's `run` verb. Launchers that use umu's
  `runinprefix` (XIVLauncher-RB, Lutris) hand the bridge Wine's builtin d3d12,
  which cannot take DXVK's adapter: `WithDx12::GetD3D12DeviceFromD3D11
  D3D12CreateDevice failed: 80004002`, every w/Dx12 upscaler falls back to
  FSR 2.2. Fix: `d3d12=n,b;d3d12core=n,b` in the launcher's DLL overrides.
* FFXIV specifics (from the RenoDX Discord community, reproduced under Proton):
  OptiScaler as `winmm.dll` (the game crashes with other proxies),
  `DrsMinOverrideEnabled=true`, `DrsMaxOverrideEnabled=true`, every
  `QualityRatio*` set to one value, `UseDelayedInit=true`; in-game DLSS on,
  Borderless, Frame Rate Threshold "Always enabled". Any in-game resolution or
  display-mode change with the bridge active crashes.
* **Multipass flashes in FFXIV.** The game keeps two DLSS features alive and
  both get an NR dispatch every frame; the fork decides whether an extra pass
  has been submitted by command-list pointer when the swapchain is not
  wrapped, so the feature whose list built pass 2 never passes the check and
  runs one pass while the other runs two. Output alternates per dispatch.
  Single-feature games are unaffected. Needs an upstream fix (present count on
  the bridge path); no config works around it.

## Games without DLSS: the feeder route

* **D3D11 (Dreamfall Chapters): works.** The feeder builds its DLAA contract
  on a private D3D12 device.
* **D3D12 (FF7 Remake Intergrade): does not work.** The feeder creates DLSS on
  the game's own device through ReShade's wrapper, and that create faults
  inside vkd3d-proton on most launches (11 of 15) with a garbage object
  pointer reached through the DLSS snippet's NVAPI cubin calls
  (`NvAPI_D3D12_GetCuda*: Invalid pointer`, then an access violation in
  `d3d12core.dll`). The four successes were all in the game's HDR mode; no
  configuration variable predicted it (add-on build, snippet version, DLSS
  override mode, swapchain re-initialisation were all bisected and ruled out).
  Hiding the cubin entrypoints via `DXVK_NVAPI_DISABLE_ENTRYPOINTS` makes NGX
  report DLSS unavailable, so that path cannot be avoided. When the create did
  succeed, no add-on build drew a visible NR frame on the R10G10B10A2 PQ
  backbuffer, and feeder 0.14.0-beta.4's own feed came out black there where
  0.12.0's was visible.
* ReshadeMotionEstimation does not compile on ReShade's D3D12 backend
  (`error X3020: cannot sample from texture that is also used as render target`);
  VORT (`DLSS5_MV_PROVIDER=2`) does and feeds real vectors.
* `PROTON_DLSS_UPGRADE=1` and the feeder: the game-local `nvngx_dlss.dll`
  must stay (with no NVAPI use in the game, the driver-store copy is not found
  on its own), and the classic RenoDX add-on tolerates the second copy the
  override loads.

## RenoDX add-on builds

| build | native route | feeder route |
|---|---|---|
| classic `++` 0.2026.827.2036 (391 KB) | works | creates/evaluates; black on a PQ backbuffer |
| v4.x 0.2026.828.517 (1.7 MB, = rhi-repo 4.55) | hangs/crashes in CreateFeature | not viable |
| perf-fix 4.1.5 0.2026.828.2110 (575 KB) | works, HDR-aware (transfer strength ~0.5 fixes tints) | double-detours core + snippet; create faults |
| rhi-repo 4.70 (1.7 MB) | untested | has a PQ BT.2020 colour bridge; black under the feeder |
| ShortFuse 2.6 MB (renodx-dlss SF 0.5x) | never hooks under Proton | - |

`NVSDK_NGX_D3D12_EvaluateFeature_C` is not exported by any NGX core on Linux;
the add-ons' hook miss on it is baseline, not a fault.

## Proton and DLSS knobs

* `PROTON_DLSS_INDICATOR=1` is the only way to get the on-screen DLSS readout:
  dxvk-nvapi rewrites `NGXCore\ShowDlssIndicator` at every launch, so a manual
  registry value is clobbered.
* `PROTON_DLSS_UPGRADE=1` writes DLSS 310.9 SR/RR/FG into
  `system32/umu/` and redirects the game's loads there; it coexists with
  OptiScaler and the add-ons. Swapping the game's own files (`dlls install`)
  does the same without the variable.
* `DXVK_NVAPI_DRS_NGX_DLSS_SR_OVERRIDE_RENDER_PRESET_SELECTION=RENDER_PRESET_K`
  selects the DLSS preset.
* ReShade and OptiScaler must not both be loaded in the same process.

## Cyberpunk 2077 (GOG, RED4ext + CET + redscript)

OptiScaler livelocks the game thread on the mod stack; no DLSS feature is
created. Not a Proton problem as such; parked.

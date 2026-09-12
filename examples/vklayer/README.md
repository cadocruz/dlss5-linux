# The out-of-process route: DLSS5VKLayer under Proton

[bmitch87/DLSS5VKLayer](https://github.com/bmitch87/DLSS5VKLayer) (AGPL-3.0)
is a Linux Vulkan implicit layer plus a Windows helper. The layer captures each
presented frame; the helper, running under a Proton runner of its own, runs the
neural-rendering model on it and hands it back. Nothing goes into the game
folder, and the game keeps presenting its own frames if the helper is absent.

Use it for what the in-process routes cannot reach:

* **D3D12 games without DLSS** (FF7 Remake here): the feeder's same-device
  create faults under vkd3d-proton; this route never touches the game's device.
* **native Linux Vulkan games**: no other route enters those.
* anything else that presents through Vulkan (DXVK D3D9/10/11, vkd3d-proton
  D3D12) when you want nothing written beside the game.

A game with its own DLSS is still better on OptiScaler: real motion vectors,
real depth, before the UI, integrated with the upscaler. This route is
post-present (the HUD gets the model too), uses optical-flow vectors and zero
depth, and does not upscale.

Measured 2026-09-10/12: RTX 5090, NVIDIA 615.71, CachyOS, proton-cachyos-slr
as the helper's runner, DLSS5VKLayer 0.2.6-2 then 0.3.0-1, FF7 Remake at
5120x1440. Upstream ships most days; 0.3.0-1 carries a fix worth taking on any
version older than it (a 1x1 probe swapchain built a model and hung the GPU
with Xid 109 on the first submit, taking the game with it).

## Install (user mode, no root)

```bash
# 1. the release tarball (works on Arch/CachyOS since 0.2.5-2)
tar -xzf dlssnr-*-linux-x86_64.tar.gz && cd dlssnr-*-linux-x86_64
./install.sh --user
#    -> ~/.local/lib/dlssnr, ~/.local/bin/dlssnr-{helper,gui,shmctl},
#       ~/.local/share/vulkan/implicit_layer.d/VK_LAYER_NV_dlssnr.{x86_64,i686}.json

# 2. the installer also writes ~/.config/environment.d/dlssnr.conf, a session-wide
#    VK_INSTANCE_LAYERS pin that exists only to order the layer against NVIDIA
#    Smooth Motion. Without Smooth Motion it lists the layer for every Vulkan app
#    at your next login; remove it. The layer stays gated on its launch token.
rm ~/.config/environment.d/dlssnr.conf

# 3. init picks a runner from compatibilitytools.d (user dirs first, then
#    /usr/share/steam/compatibilitytools.d), then import the model runtime
dlssnr-helper init
dlssnr-helper runners
dlssnr-helper import-binaries /path/to/dir-with-nvngx_dlssnr.dll   # nvngx.dll optional; nvapi64.dll not needed under Proton
dlssnr-helper doctor

# 4. start the helper BEFORE the game (first start builds its own Proton prefix)
dlssnr-helper start
dlssnr-helper status
```

Pick the runtime by hash, not by name: `sha256sum nvngx_dlssnr.dll`.
`e16bcf15...` is NVIDIA's signed 310.8 for RTX 50; `e67dee20...` is the
ShortFuse cross-generation build for RTX 20/30/40; `8270b350...` is a widely
copied patched build that works but is not the one to give a helper.
`python3 dlss5_linux.py vklayer status` names the one the helper has.

## Enable it for a game

Steam launch options, and nothing else:

```text
VKLayer_DLSS5=1 %command%
```

The port writes it while Steam is closed (`launch-options <game> --vklayer
--apply`) or prints it while Steam is open. If the game previously had an
in-process route, remove that payload first (`dlss5_proton.py restore
<appid>` or `uninstall`); a leftover `WINEDLLOVERRIDES` in the options is
harmless once the files are gone. `PROTON_LOG=1 PROTON_DEBUG_DIR=$HOME` keeps
the layer's own lines in `~/steam-<appid>.log`, which `verify --vklayer` reads.

For a native Linux game: `VKLayer_DLSS5=1 ./game`, same token.

## Controls

There is no in-game overlay, because nothing runs inside the game. Settings
take effect on the next frame:

* `dlssnr-gui`: every setting as a row (style, intensity, local tone, local
  structure, skin structure, sharpness, detail and colour amounts, highlight
  guard, transfer mode, passes with per-pass overrides, working scale and
  downscaler, motion quality, HDR mode, white point), profiles, a split-screen
  compare, a frame hold that freezes the model's input, and debug views.
  Closing the GUI stops the helper.
* `dlssnr-shmctl /tmp/dlssnr-$UID/shm.bin settings | set <key> <value> | toggle enabled`
* a toggle hotkey exists (`DLSSNR_TOGGLE_KEY=F10`, or the GUI's "Toggle key")
  but **leave it unbound for now**: binding one arms an evdev rescan that
  re-opens every `/dev/input` node once a second on the present thread. 122 ms
  of stall per second measured on this machine with 16 nodes, whether or not
  neural rendering is on (upstream issue #12). The default is none; the
  setting persists in `~/.config/dlssnr/config.ini` as `set_toggle_key`.

## Check a run

```bash
python3 dlss5_linux.py verify "<game>" --vklayer
```

reads the helper log (`~/.local/state/dlssnr/helper.log`), the live counters
in shared memory, and the layer's lines in the Proton log: feature create
result and size, frames through the model, transport, HDR state, and the
per-frame rebuild described below.

## Caveats (0.3.0-1, measured here)

1. **HDR10 swapchains: play in SDR for now.** The layer's `Prepare()` compares
   the raw PQ transfer flag against a value it stores normalised to zero
   whenever the proxy is 8-bit, so on a PQ10 swapchain it rebuilds its whole
   composition every frame. `verify --vklayer` counts it.
2. **The float16 HDR proxy does not engage on this runtime**: the helper only
   tries HDR when `GetFeatureRequirements` reports it, and that query returns
   `0xbad00005`, so the model is created SDR and sees PQ code values as an
   8-bit picture.
3. **No zero-copy under a Wine runner.** dma-buf needs the fd-based
   external-memory extensions on the Wine-side device and winevulkan does not
   expose them; frames cross shared memory. `ptrace_scope` does not matter on
   that path.
4. **The present thread waits for the helper in a spin/poll loop**, so GPU
   utilisation has a ceiling: no fence or semaphore takes part, the CPU does
   the waiting, and the queue drains while it waits. Upstream issue #13
   measures ~8.7 ms of wait against ~2.0 ms of actual helper GPU work at 4K.
   This is the route's headline cost on a fast card.
5. The "core" NGX init answering `0xbad00002` and the `[param-miss]
   DLSSNR.*Subrect*` lines in the helper log are expected.
6. The helper must be running before the game starts, and it is stopped by
   closing the GUI or by `dlssnr-helper stop`.

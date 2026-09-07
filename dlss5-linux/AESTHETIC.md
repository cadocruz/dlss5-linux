# DLSS5-Autopilot — aesthetic notes

Captured from `autopilot-upstream/core/gui.py` (v1.6.1) so the PySide6 port
keeps the look, and so later additions stay in character.

## Palette (hex, verbatim)

| name | hex | used for |
|---|---|---|
| `BG` | `#0b0c0e` | window ground |
| `RAIL` | `#08090a` | left rail — *darker* than the ground |
| `PANEL` | `#0e1013` | cards, log, tree |
| `FIELD` | `#121519` | inputs, hover |
| `LINE` | `#1c1f24` | 1 px separators, card outline |
| `EDGE` | `#2a2e35` | button borders |
| `TXT` | `#e6e8ea` | bright text (H1, active) |
| `BODY` | `#b9bcc2` | normal text |
| `DIM` | `#5c6069` | labels |
| `FAINT` | `#454952` | decoration, inactive markers |
| `AMBER` | `#d8a657` | **the one accent**: brand, active step, INSTALL, selection, progress |
| `GREEN` | `#6f9f6f` | ok / done / installed |
| `RUST` | `#b07a3c` | warnings, "shaky" |
| `RED` | `#c96a5a` | errors, unsupported |
| slider trough / hot | `#7a5a2c` / `#f0b25a` | the work-resolution dial — deliberately unmissable |
| banner | `#1a1509` | update banner ground, amber text |

Near-black, one warm accent, no blue anywhere. Contrast comes from *value*
steps (RAIL < BG < PANEL < FIELD), not from colour.

## Type

- UI font: system default at 10 px; H1 15 px `TXT`; labels 9 px `DIM`;
  fine print 8 px `FAINT`. Bold only on INSTALL and `[ update now ]`.
- **Brand is monospace**: `Cascadia Mono` → `Consolas` → `Courier New`, 16 px,
  `dlss5` in AMBER over `autopilot` in DIM, stacked, top of the rail.
- **Everything is lowercase** — headings, labels, buttons, log lines
  ("what are you installing for?", "pick a game", "did it work?", "loads as").
  The only shouted word is the accent button: **INSTALL**.

## Layout

- Window 1060×830, `clam` ttk theme, all widgets flat.
- **Left rail, 236 px**, `RAIL` ground, 1 px `LINE` on its right edge.
  Brand at top (24 px pad), then the three steps, spacer, then GPU line,
  version, and four `[ bracketed ]` links (open log / report a bug /
  suggest a feature / how it works) in 8 px DIM.
- **Step rows** in the rail: 2 px left marker (AMBER when active), a
  `[ ]` / `[>]` / `[x]` mark (FAINT / AMBER / GREEN), title (DIM → TXT when
  active, BODY when done), 8 px subtitle in FAINT. Active row's ground
  becomes PANEL.
- **Right side**: optional amber-on-`#1a1509` update banner at top; body
  with 28 px side padding; a 1 px `LINE` above a **bottom bar** holding
  `back` and the primary button (`scan games` → `continue` → `INSTALL`).
- **Cards**: `PANEL` ground, 1 px `LINE` outline, inner pad (16, 14). Every
  section on the install page is a card. A card title row can carry a small
  AMBER `new` tag.
- Form rows: label column left in DIM, control fills right; 5 px vertical
  rhythm; a third column for inline hints in DIM 8–9 px.

## Voice and marks

- Warnings open with `!!` in RUST ("!! before you get your hopes up",
  "!! set your screen resolution BEFORE turning neural rendering on").
- Links and secondary actions are `[ bracketed ]`; buttons are plain words.
- Log headers are `=== like this ===` in AMBER; lines start with `> `.
- Result marks are fixed-width: `[ok]  ` GREEN, `[!!]  ` RUST, `[fail]` RED,
  `[--]  ` plain. Component checks: `[ok]`, `[!!] name: old -> new`, `[--]`.
- Tree row tags: installed GREEN, unsupported RED, shaky RUST, stale AMBER;
  selection is AMBER ground.
- Route combo entries read `label  <-  recommended for this game and card`
  or `label  (not for this pc: reason)`.
- Reality text under the route: the route BLURB, prefixed
  `NOT FOR THIS PC - reason.` in RUST when it does not fit.
- Progress bar: AMBER on FIELD, 4 px thick, a DIM 8 px caption below.

## What we keep, drop, and add in the Linux port

Keep: everything above. Drop: the video/webcam/YouTube card, RTX Remix
card and route, `[ update now ]` self-update, Win32 titlebar/DPI calls.
Add (in the same voice): a `proton` line in the rail under the GPU line
("prefix ok · ngx bridge ok"), and a `WINEDLLOVERRIDES` row on the install
page rendered like a warning when it is missing — that is the #1 silent
failure on this platform and deserves the `!!`.

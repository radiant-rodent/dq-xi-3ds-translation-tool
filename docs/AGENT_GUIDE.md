# Agent guide: testing the DQ XI translation in Azahar

How an agent (or a person) builds a test version of the translation, boots it in the Azahar
emulator, drives the game, takes screenshots and debugs crashes - without touching the
user's desktop. Everything here runs from a shell.

## Ground rules

- **Never drive the emulator window** (no simulated keys/mouse, no focusing, no clipboard,
  no UI Automation). The window is opened *without activation* and is controlled only through
  Azahar's RPC server (memory read/write + screenshots) and input movies. The user keeps
  working while tests run.
- **Never minimize** the test emulator (the renderer stops). It may sit behind other windows.
- **Do not run `azahar.exe --help`**: it opens a dialog box on the user's screen.
- **Never use the user's own Azahar/Citra install or profile.** Tests use the portable copy in
  `_emutest/azahar` with its own `user/` folder.
- Test builds contain a **test-only input hook** (`dq11tool/testhook.py`). Release builds
  never do (`ExportOptions.test_input_hook` defaults to `False`).
- `_emutest/` is gitignored (emulator, saves, screenshots). The scripts live in `emutest/`.

## Setup (once per machine)

```
python emutest/setup_azahar.py            # copies C:\azahar -> _emutest/azahar, portable user/ folder
```

The test profile (`emutest/az.py: SETTINGS`, applied on every start):

| setting | value | why |
|---|---|---|
| graphics_api | 2 (Vulkan) | fast |
| resolution_factor | 1 | native 400x240 + 320x240, screenshots are 1:1 |
| async_shader_compilation | false | **required**: with async shaders, movie replays are not deterministic |
| frame_limit | 0 | unlimited speed (~10-20x real time) |
| is_new_3ds | false | Old 3DS: the stricter memory budget (a real Old 3DS must work) |
| enable_rpc_server | true | memory access, screenshots, clock |
| output_type | 1 | no audio |
| init_clock | 1 | fixed clock |

## Test builds

```
python emutest/build_test.py              # ~12 s
```

Exports the current project database as an Azahar mod into
`_emutest/azahar/user/load/mods/0004000000199200/` (LayeredFS `romfs/` + decompressed
`exefs/code.bin`) and boots the **original Japanese ROM** (`DQXI.3ds`) with it. No 4 GB image
is written. `build_test.build(**opts)` accepts any `ExportOptions` field (for experiments,
e.g. `menu_buffer_factor=2.0`, `expand_only=...`, `test_probes=("null_alloc",)`).

## Driving the game: `emutest/azctl.py`

One emulator session stays running between commands (its pid is in `_emutest/runs/ctl/session.json`):

```
python emutest/azctl.py start --save user_citra --boot 1   # boot, Continue, slot 1, skip greeting
python emutest/azctl.py press X                               # exact 2-frame press
python emutest/azctl.py press DOWN DOWN A
python emutest/azctl.py walk L 1.5                            # circle pad, game seconds
python emutest/azctl.py mash A 10                             # dialogue
python emutest/azctl.py shot name                             # -> _emutest/runs/ctl/name.png (400x480, both screens)
python emutest/azctl.py seq "press X" "shot a" "press A" "shot b"   # one process; sheet in seq.png
python emutest/azctl.py status
python emutest/azctl.py stop
```

Every command prints the emulated time and a crash line if the log shows one.
`python emutest/nav.py X Y` walks the hero to a point picked on a screenshot's bottom-screen
map (closed loop; greedy - give waypoints around obstacles).
`python emutest/mapstitch.py "R 4" "U 3" ...` walks and stitches the scrolling bottom-screen
map into `_emutest/runs/ctl/map.png` (find shops, doors, exits).

### How input works (test builds only)

`testhook.py` patches the SDK's `nn::hid::PadReader::Read` (end at `0x11AD3C`, called once per
frame) to OR a RAM "mailbox" at `0x839F00` (unused tail of .bss) into the pad state:

| offset | meaning |
|---|---|
| +0 | held buttons (OR) |
| +4 | one-shot trigger bits |
| +8 / +12 | circle pad x,y (s16) / bit 0 = override |
| +16 | read counter (alive check; 30/s in game) |
| +20 / +24 | pulse buttons / remaining frames - the hook releases them itself |

Use pulses for presses: holding through RPC lasts several frames at 12x speed and triggers key repeat.
Buttons: `A B SELECT START RIGHT LEFT UP DOWN R L X Y` (HID bit order). Movement in the field
is camera-relative; the D-pad rotates the camera.

### Clock

`Azahar.game_time()` = tick of the latest HID pad update (HID shared memory at `0x10002000`,
u64 at +0, 268111856 ticks/s). It is exact emulated time and also the clock of input movies.
Poll it every ~20 ms: at 12x speed, 0.25 s real is 3 s of game.

### Movies (deterministic replays)

`az.Movie` writes `.ctm` files: per HID update (234/s) one pad+circle entry (type 0) and one
touch entry (type 1). `Movie().spec("6:0.15:A,12:0.15:A")` = press A at 6 s and 12 s of game time.
Replays are deterministic only with `async_shader_compilation=false`. The live hook works
during movie playback too (its bits are ORed in).

## Saves ("save states")

Game saves (not emulator states) are the portable way to start at a scene: an emulator state
also contains the *old* translation and code in RAM. Saves live in `_emutest/saves/<name>/`
(`00000001/Data0..2, mini_*, system` + `00000001.metadata`) and are copied into the test
profile's sdmc by `--save NAME` (`az.install_save`). `az.export_save(name)` keeps the current one.

Save format: every file ends with `u32 0x5E` + CRC32 of everything before those 8 bytes
(`zlib.crc32(data[:-8])`), so saves can be edited. In a slot file (`Data0..2`), `0x6580` holds
the location block (u32 kind, position x,y,z floats at `0x658C`, facing vector, map ids).

Available saves: `user_citra` (copied from the user's Citra: slot 1 = Cobblestone church, Lv 3)
and `cobblestone_start` (the original first-battle test save).

## Crashes

- `Azahar.crashed()` scans the tail of `_emutest/azahar/user/log/azahar_log.txt` for guest
  faults (`unmapped Read32/Write32 ... at PC 0x...`, aborts). Lines with `ReadBlock` come from
  RPC reads and are ignored. A crash loop grows the log by MB/s - read only its tail.
- Disassemble with `python emutest/armdis.py 0xADDR [N]` (capstone; the JP `.code` is
  extracted to `_emutest/code_jp.bin`).
- Debug probes: `testhook.PROBES` - small code patches in the reserved tail of .text
  (`0x724C00..`) that record registers/stack at `0x839A00` for RPC reads.
- Known code: general heap alloc `0x55E928` (free list, sentinel at heap+0x14; a NULL `next`
  = corrupted heap, loops at `0x55E968/0x55EA48`), small-block allocator `0x6F1C9C`.
- Bisect tools: `emutest/bisect_layouts.py` (which layout's buffer growth breaks the game),
  `emutest/bisect_panes.py PACK MEMBER` (which pane).

## Memory: the crash family and the UI heap

The game carves the linear heap into fixed heaps at boot (`0x102EF8`): main 44 MB, then
10.6 MB, 3 MB, 1.75 MB, 256 KB, 160 KB, then two small-block allocators (5 block pools + a
fallback heap each). Allocator 2 (object `0x7F50F0`, alloc `0x6F1C9C`, fallback heap object
`0x7F5144`, 0x23F880 bytes) serves the UI layouts. Every text box allocates a vertex buffer of
~312 bytes per character of its *current string length* (`0x53F344`; the game re-allocates
when a longer string is set, so file buffers do not limit runtime text).

**Bug in the game**: when the fallback heap is full, `0x6F1D08 addeq r0, r4, #0x54` returns
the address of the heap object itself instead of NULL. The next layout object is built on
the heap's bookkeeping; the game then hangs in the allocator (`unmapped Read32 @ 0x0000000C/10
at PC 0x0055E968/0x0055EA48`) or jumps to garbage ("Undefined Instruction", PC = a vtable).
English strings are longer, so the translation needs ~470 KB more of this heap at its peak
(2.31 MB of 2.36 MB, Japanese: 1.84 MB) - the item details screens (bag, shops) ran it out.

**Fix** (`dq11tool/memfix.py`, `ExportOptions.ui_heap_extra`, default 1 MB): take 1 MB from
allocator 1's fallback heap (peak ~350 KB of 1.95 MB in every scenario) and give it to the UI
heap. Measured after the fix: UI heap peak 2.36 MB of 3.4 MB (1 MB free, twice the Japanese
game's margin), allocator 1's heap 550 KB free, main heap unchanged.

Measuring (test builds): probes in `testhook.PROBES` - `heap_track` + `heap_live_alloc` +
`heap_live_free` (= `testhook.HEAP_LIVE`) keep live bytes and the high-water mark of every heap;
`oom` counts fallback failures and profiles fallback allocations by caller.

```
python -c "import sys; sys.path.insert(0,'emutest'); import build_test; from dq11tool import testhook; build_test.build(test_probes=testhook.HEAP_LIVE + ('oom',))"
python emutest/memreport.py LABEL            # size / live / peak / headroom of every heap
python emutest/profrun.py LABEL out.json     # which callers allocate from the UI heap
```

Always re-measure after changes that make UI text longer or add layouts. Keep the UI heap
headroom at or above the Japanese game's (~500 KB) in every scenario you can reach.

## Layout text buffers

`textBufBytes` in a txt1 pane is only the initial buffer: the game grows it when it sets a
longer string at runtime (item names, descriptions, monster names showed in full with no
file growth at all). What limits English is the *visible* room: pane and window frame widths.
Number/symbol panes are never grown (`bflyt.numeric_text`).

## Walking the hero (no UI driving)

Walk at real speed: `python emutest/azctl.py start --save user_citra --speed 100 --boot 1`
(at unlimited speed every stick input overshoots).

- `emutest/walkto.py`: the hero's world position is the per-frame copy at `0x7F3110` (x, y, z
  floats; writing it does nothing, the game rewrites it every frame - the real position is
  not found yet, so no teleporting). `goto_world(s, (x, z))` walks closed loop on it.
- The **bottom map is not north-up** in general (it follows the camera); do not convert map
  pixels to world coordinates without calibrating. Indoors the small room map does not scroll.
- Movement is camera-relative and the camera turns while walking: never hold one stick
  direction for long. `jevnav.Nav` re-estimates the stick->world rotation after every step.
- L+R resets the camera behind the hero.
- `emutest/jevnav.py` + `emutest/jevplay.py SCENARIO`: routing by **Jev** (OpenCode's free
  "System One" decision model, `https://opencode.ai/inference/v1/systemone`, model
  `jev-1.13-free`, text only, ~1 s per decision). Each step it gets position, target, the
  distance after each of 8 directions, blocked directions and the move history. Sliding along
  a wall counts as blocked. The free tier has a daily quota (429 FreeUsageLimitError with a
  Retry-After of hours): then the code picks the best unblocked direction itself.
- `emutest/vision.py`: **Muse** (`muse-spark-1.3-contributor`, OpenCode Go, Responses API,
  `reasoning.effort = minimal`) locates things on a screenshot (~15 s/call): use it only at
  key points. Keys come from the translation tool (`mt.get_key`), never print them.
- Talking needs the hero right in front of the NPC and facing them; "Talk" in the X menu with
  nobody in front starts party chat instead.

## GDB stub (watchpoints)

`az.Azahar(..., settings=az.GDB)` enables Azahar's GDB stub (port 24689; the emulator waits
for a debugger before the first instruction). `emutest/gdb.py` is a minimal client
(break/watchpoints, registers, memory); `emutest/watchrun.py SPEC "SEQ BEFORE" "SEQ TRIGGER"`
boots, plays, sets a watchpoint and prints registers and code pointers on the stack.
SPEC: `ADDR:SIZE:KIND`, `pane:NAME:OFFSET` (every layout pane of that name; name at pane+0x80,
translate y at +0x20) or `u16:TEXT` (every UTF-16 copy of a text, halts first).
Breakpoints re-trigger on the same instruction (the stub does not step over them); prefer
write watchpoints. `emutest/layoutdump.py PACK MEMBER` prints the pane tree of a layout.

## Message window limits (found 2026-10-13)

- A message string shows **at most 3 lines** in the window. A 4th line scrolls the first out
  of view, text after a `{CR}` on the 3rd line is never shown (blank window). No Japanese
  dialogue uses more than 3 lines; `{CR}` is a pause, not a page break.
- The window copies every message into a 256-unit buffer (`0x1EFC8C`, window object +0xC7C):
  longer messages are cut and the window stays blank (gamefont.MESSAGE_MAX_UNITS = 255).
- Visible line width is **321 px** (text starts at x 39, 10 px inside the frame;
  gamefont.MESSAGE_WINDOW). The layout's text buffer size (textBufBytes) is not a limit.
- A 4-line window was tried (export `talk_lines=4`, layout moves): the game decides the
  scrolling itself (not from the pane height), not solved yet.

## Names and labels in runtime panes

- Monster names (gamefont.NAME_FIELDS): lines break at 112 px (gamefont.NAME_WRAP) but may be
  up to 148 px: every name pane is narrowed per pane to its widest name (user's choice
  2026-10-13: official names, one squeeze per pane). Panes widened for that: 3D target label
  90 -> 130 px (no frame, floats over the monster; squeeze 0.88), 2D 120 px; enemy lists
  150 -> 185 px (the count column is one digit since codefix removed "匹"; 3D squeeze 0.81).
  `python emutest/squeezes.py` prints every RUNTIME_FIT pane's factor and any OVERFLOW
  (still too wide at 0.75: 2D enemy list 6 px, item details 35 px - pre-existing).
- Battle commands (2026-10-13): official Attack / Defend / Abilities / Items / Flee; the right
  column is narrowed at runtime (RUNTIME_FIT win_command_*, 3D 54 px, 2D 46 px).
- First battle: `python emutest/battlego.py LABEL ["Long name"]` (cobblestone_start
  save; optional test name for the slime to see long labels).
- export.RUNTIME_FIT: panes filled at runtime from a table get their glyphs narrowed
  (font size x) so the longest translated name fits (>= MIN_RUNTIME_SQUEEZE); the 2D target
  label is also widened. Layout labels wider than their pane are narrowed the same way
  (ExportOptions.min_label_squeeze); gamefont.LABEL_SQUEEZE lets labels be 1/0.8 of the pane.
- `python -m dq11tool.pcnames [--dialogue|--labels] [--apply]` puts the exact official PC
  text back where it now fits (backup of the DB first). The official text is the target; hand
  edits were crash workarounds and are replaced too.

## Cobblestone item shop (found 2026-10-13)

- `python emutest/shopwalk.py` boots user_citra, walks church -> village -> shop door
  (about (-9.6, -5.7) on map b011_001a) -> the left side of the counter facing the shopkeeper,
  and leaves the session running (press A to talk). About 1 minute at `--speed 100`, no vision.
- The current map name is a string at 0x7CF304 (e.g. `b011_001a`); its files are
  `romfs/level3d/b011_*.pack` (CLevel3DBin bxon) and `romfs/script/b011*.pack` (Squirrel cnut).
- At user_citra's story point (evening after the Tor, Gemma following) the shopkeeper only
  chats. Save `user_shop` (the next morning, from the user) has the working shop.
- **Shop crash test:** `python emutest/shoptest.py LABEL` (save user_shop) walks there, checks
  that the greeting appeared (else INVALID - D-pad presses would walk the hero away), opens Buy
  (every item + details), Sell, Quit; screenshots in `_emutest/runs/shoptest/LABEL/`, heap table
  if the build has the probes. Result 2026-10-13: `ui_heap_extra=0` crashes when the shop menu
  opens (PC 0x55EA48, UI heap full); the current build passes, UI heap peak 2.63 MB of 3.41 MB.
  Proof image: `docs/shop_crash_before_after.png`.
- The hero name in the save: UTF-16 at Data0+0x2 (slot header) and Data0+0xC46C (hero record),
  then `roomscan.fix_crc`. user_citra is renamed to "Hero" (original in
  `_emutest/saves/user_citra.before-rename`).
- Position: 0x7F3110 and the world matrix are per-frame copies; no writable master position was
  found (float/double scans of 0x700000-0x900000, 0x08000000-0x10000000 and 0x30000000-0x38000000;
  `emutest/posdiff.py`, `emutest/postest.py`). Teleporting is not available; use scripted routes.
- The Azahar test window can cover the top rows of the top screen while you watch it; the
  screenshots (framebuffer) are what counts. Menus checked 2026-10-13 (`emutest/menutour.py`).
- Paging test 2026-10-13: 5 lines with `{CR}` after each line -> lines 1-3 show, the next A
  press skips lines 4-5 and goes to the next message. One message string = one page.

## Long NPC lines: extra message boxes (`{PAGE}`, 2026-10-13)

- NPC talk (CharacterIEData, `param/region/rNNN.pack:npc_ie.bxon`) is an array of 28-byte line
  records per talk (layout in `dq11tool/ietalk.py`). `{PAGE}` in a translation (a control code in the
  exported string, U+001F) makes the export split that record into consecutive records - the game
  shows them as consecutive boxes with its normal scroll, like a Japanese NPC with more lines.
  Verified in game (shopkeeper greeting split in two, shop still works afterwards).
- `gamefont.splittable(sid)` = used only as CharacterIEData lines; for those `wrap()` uses
  `metrics.wrap_pages` (3 lines and <= 255 units per page, page ends after sentences) and
  `fits()` checks every page. `python -m dq11tool.pcnames --dialogue --apply` restored 2936
  more official lines this way (2938 split, max 154 units per page).
- The same split works for every CharacterIEData table (npc_ie, book_ie, script `ie.bxon`) and
  for party chat (TalkPartyContent2: each utterance is an array of strings = consecutive boxes,
  `ietalk.split_party`; verified in game with Gemma). The marker is U+001F (no game string uses
  it; U+000C does occur in party-chat placeholders).
- Not splittable: cutscene text (TextBlock, cs_text.bxon) - the cutscene script calls each
  line by the id in +0x0C, so an extra record would never be shown. Church/shop messages are
  single UI strings. ~410 official lines there stay condensed (`python emutest/longlines.py`).
- Official lines may omit the hero name `{v:1}` (`validate(..., official=True)`, used for
  origin 'pc'); `<pc>` in PC lines becomes `{v:1}` (`pcnames.official_dialogue`).
- The English region packs are bigger (r006 1.05 MB vs 0.57 MB JP), so the export also moves
  1 MB from the third fixed heap to the main heap (`memfix.patch_main`,
  `ExportOptions.main_heap_extra`): Cobblestone main heap peak 45.3 MB of 47.2 MB, third heap
  peak 1.2 MB of 2 MB.

## Word wrap for runtime text (`dq11tool/wordwrap.py`, 2026-10-13)

- Strings the game assembles at runtime (battle messages with a monster name inserted into a
  template) are laid out by the game itself, which breaks a line at any character (written for
  Japanese): "...of the Very Lon / Name is defeated.".
- `wordwrap.patch` (applied by the export after codefix, `ExportOptions.word_wrap`) hooks both
  copies of the width check: the text writer (0x6F0514 -> cave 0x724A00) and the line measurer
  the message windows use (0x6EFEB4 -> cave 0x724B00). At a space it measures the next word
  with the font's own widths; if it does not fit, the space becomes the newline. A single word
  longer than the line still breaks as before. The caves are in codestr's reserved text tail
  (`RESERVED_TEXT_TAIL` 0x624A00 file offset); testhook probes start at 0x724C00.
- Assembly source is in the module docstring/comments (assembled with keystone). Registers
  were found with `emutest/wrapfind.py` (read watchpoint on the font's width entry of 'V'
  while the long test name is shown).
- Verified in game with `python emutest/battlego.py wordwrap "Malicious Abominable Showman of
  the Very Long Name"`: top-screen battle line and the framed window both break at spaces, no
  crash. Proof image: `docs/battle_word_wrap.png`.

## Battle grammar: articles and plurals (`dq11tool/grammar.py`, 2026-10-13)

- The PC script builds battle text with grammar macros (`<LM_DefSgl_Target>` "the slime",
  `<LM_IfSing_Damage(point,points)>`); the 3DS script has none, so the flat import read
  "Slime takes 1 points of damage!". The exporter now keeps them as grammar tags, resolved in
  game after the names and numbers are inserted:
  `{the} {The} {a} {A}` (article before a name), `{pl}` (plural ending after a name),
  `{n:one|more}` (by the nearest number), `{noun}` (flag at the start of common-noun names).
  Encoded as U+E000-U+E008 (no game string uses the private use area); every font has
  zero-width blank glyphs for them (`fontimport.add_blank`), so a path that misses the pass
  shows nothing stray.
- Names: `{noun}` on the 668 enemy names whose PC battle name is lower case ("slime");
  73 proper names (Jasper, party guests) get no article. 3DS-only names were put in sentence
  case. Menus show the names unchanged (the flag is zero-width / removed at a sentence start).
- `python -c "from dq11tool import pcnames; ..."`: `pcnames.plan_nouns`, `pcnames.plan_grammar`
  (+ `apply_simple`) did the DB changes (backups `project.db.before-nouns-*`, `-grammar-*`).
  476 official battle lines tagged; 147 stay flat because "The ..." would overflow the
  estimated width (metrics measures the widest resolution).
- The pass: `dq11tool/grammar_asm.py` (ARM source, keystone) -> `grammar_code.py` (bytes,
  regenerate with `python -m dq11tool.grammar_asm --write`), applied by `grammar.patch`
  (ExportOptions.grammar). Cave 0x724400 (codestr.RESERVED_TEXT_TAIL is now 0x624400). Hooks
  at the end of both pane SetString functions (0x53F9D8 in 0x53F940, 0x53FA74 in 0x53F9EC):
  every pane text is resolved in its buffer ([pane+0xA4], capacity +0xCC), the length +0xCE
  and the returned count are updated. A first hook in the field message setter (0x2E0628)
  did not cover battle text.
- Tests without the game: `python emutest/grammartest.py [N]` runs the ARM code in
  `emutest/armsim.py` (small A32 interpreter) against the Python reference `grammar.resolve`
  (5000+ random cases match). In game: `python emutest/battlego.py LABEL --dump` (extra
  screenshots and a RAM string dump `strings.txt` per result step). Verified: window
  "The slime is defeated.", menus/labels unchanged, no crash.

## Scenario tests: one event per run, every text logged (`emutest/scenario.py`, 2026-10-14)

No walking: the hero resumes at the Cobblestone church altar facing the priest, one A press
starts the configured event, and every text the game puts into a layout pane is recorded with
its game time.

```
python emutest/scenario.py battle LABEL --enemies "Slime x3, Dracky" --zone 0x562 --mode 2d --party strong
python emutest/scenario.py shop LABEL --id 1 --mode 3d          # shop.bxon record (kind byte 1 = weapon shop)
python emutest/scenario.py church LABEL --mode 2way             # the real priest (greeting + menu)
python emutest/scenario.py inn LABEL --mode 2d                  # base.ChartINN with the priest
python emutest/scenario.py steps LABEL --steps "StartUI 3 -1; WaitMenu; EndUI 3" --inputs "wait:2,A,shot"
python emutest/scenario.py suite emutest/scenarios/smoke.toml   # all kinds x modes, summary.html
python emutest/scenario.py monsters drack | zones               # ids / names to choose from
```

Output `_emutest/runs/scenario/LABEL/`: `report.html` (timeline, formatting visible, line widths,
screenshots), `events.txt` (one line per message, `[layout]` = layout placeholder texts),
`events.jsonl` (every record), `shots/`, `sheet.png`, `meta.json`. Exit code 0 ok, 1 crash
(also "emulator exited"), 2 invalid (no text after the talk / boot failed), 3 text records lost.
Options: `--mode 2d|3d|2way`, `--party asis|strong|godlike`, `--members 1,2` (PartyAdd ids),
`--gold N`, `--inputs` (after the talk: buttons, `wait:S`, `shot[:NAME]`, `battle`), `--keep`.

How it works:
- **Saves per display mode** (`scenario.SAVES`): `user_citra` (2way: 3D top + 2D bottom, the
  user's early-game mode), `user_citra_2d`, `user_citra_3d`. The 2D/3D ones were made in game:
  the game's own switch (`common.SwitchDispMode`, reloads the field at the priest) and a
  Confession save. The mode byte is Data0+0x6FCC (0 2D, 1 3D, 2 2way) but patching only that
  byte gives a 3D hero who can neither walk nor talk. Gold: u32 at Data0+0x3E28 and +0x3E2C
  (`--gold`; 5000 or more earns the "Gold Magnet" accolade popup at load).
- **Boot** (`scenario.Run.boot`) follows the text log, not the clock: A until the first
  message window (`txt1`, "View the story so far?"), B (= No) until the field's place label
  `txt_place`, B for the priest's Continue blessing, then nothing until the talk. (Fixed-time
  A/B presses once ended in the title's Save Code screen, and extra B presses broke the 3D talk.)
- **Event** (`dq11tool/testroom.py`): the area script `script/b011.pack` gets a new
  `TrgRCommand(type)`: a talk (type 10) runs `StartEvent(this.IE_TEST)`, anything else goes to
  the original (renamed `TrgRCommand_orig`). `IE_TEST` is assembled from a list of native calls
  (`("LoadScenarioBattleEncount", 25)`, `"@target"` = GetTargetActorID(), tuples = nested calls).
  `church` runs leave the script alone (the real priest talk). Calls found in the scripts:
  `StartUI(2, shop)` shop, `StartUI(24, target)` church menu (from an event it only shows the
  Continue greeting), `ChartINN()` inn, `StartUI(3, -1)` bank, `SetDisplayMode` +
  `LoadField` (= `common.SwitchDispMode`), `PartyAdd(id)`, `AllMaxHealFunc()`,
  `AddPlayerParam(member, param, amount)`: 1 max HP, 3 max MP, 5 magical might, 7 magical
  mending, 9 strength; **param 11 aborts the event** (nothing after it runs).
- **Battles** (`dq11tool/battleparam.py`): `gamecmn.pack:scenario_battle.bxon` (64-byte
  records; +0x00 (count, relptr) monsters `u16 enemy id, u8 0, u8 count`, +0x26 u16 battle
  zone / background). Record 25 is rewritten per run (array appended to the file and
  re-pointed) and started with `LoadScenarioBattleEncount(25)`. Enemy id = record index in
  `enemy_base.bxon` (44-byte records, +0 relptr name). The group keeps record 25's group name:
  the defeat line reads "Monster Pack is defeated." whatever the monsters are.
  `scenario.py zones` lists every zone with an example battle. The runner presses A (Fight /
  Attack / first target) until a result line (experience / wiped out / fled) and then until the
  field is back (`txt_place`); a press after that would talk to the priest again.
- **Shops** (`dq11tool/shopparam.py`): the shop is taken from the NPC that is talked to;
  `StartUI(2, x)`'s second argument is NOT a shop index (an early sweep of ids 2-172 tested the
  Cobblestone item shop 170 times). `fieldcmn.pack:shop.bxon` has 179 records of 52 bytes from
  0x2C (u8 kind, u8, 23 u16 item ids with 0xFFFF = none, u16); a run copies record `--id` over
  all of them. `shopparam.biggest(data)` lists the shops with the most goods.
  Sweep 2026-10-15 (16 big shops x 3D/2D/2way): all ok except **kind-1 shops (weapon shops)
  in 2way on the Vulkan renderer** (records 3, 26, 69, 72 tried): Azahar exits at shop open
  or hangs later, the *unmodified Japanese game* too (`--only , --pure-code`). With OpenGL
  (`AZ_SETTINGS="graphics_api=1"`) the same runs pass (greeting, Buy, Sell, Quit). An Azahar
  Vulkan bug, not the game: test weapon shops in 2way with OpenGL.
- **Squirrel bytecode** (`dq11tool/sqbin.py`): the .cnut files are Squirrel 3.0 *files* (no
  int/float size tags, varparams as u8) with the **Squirrel 2.2 opcode table** (RETURN 0x13,
  JMP 0x18, JZ 0x1A, NEWTABLE 0x1E, NEWSLOTA 0x3C ...). The official 3.1 compiler cannot be
  used. Every game script parses and rebuilds byte-identically;
  `python -m dq11tool.sqbin script.pack [--only FUNC]` disassembles.
- **Pure 3D and talking**: with the scenario boot the priest can be talked to across the altar in
  3D too. After azctl's fixed-time boot (B presses until t=65) A swung the sword instead (cause
  not pinned down; stray presses are the likely one).

### The text log (`ExportOptions.test_text_log`, `dq11tool/textlog.py`, `emutest/textrec.py`)

- The grammar stub's `bl gfix` (both pane SetString ends, see "Battle grammar") becomes
  `bl logwrap` (code cave 0x724C00-0x724F00, shared with the debug probes; see the tail map
  below).
  It logs the pane text before the grammar pass (raw: `{the}`, `{CR}`, `{v:..}` visible) and,
  if the pass changed it, after it (resolved), with the HID tick (= `az.game_time()`), the frame
  counter, pane address and name (pane+0x80) and the start index.
- Records go to a ring in the last 256 KB of the linear heap, left unused by
  `memfix.reserve_tail` (allocator 1's fallback heap gives them; the UI heap keeps its size).
  The host sets the ring up after boot (`CTL` 0x839F20, ring = UI heap base + size, read from
  0x7F5144). Dedupe in the game: a pane set to one of its last 4 texts within 2 frames is not
  logged again (panes refreshed or rebuilt every frame, like the gold window).
- `TextRecorder` drains the ring every 10 ms from its own RPC socket, writes events.jsonl, takes
  a screenshot whenever new text has settled for 0.25 game s, and counts lost records (ring
  overrun). A map reload creates thousands of layout texts in one frame; 64 KB was too small.
- `report.py`: typed messages (one record per character) are merged into one message (start and
  end time, all steps expandable), twin panes (text + shadow) are merged, layout placeholder
  bursts are folded, message windows wider than 321 px or with more than 3 lines are flagged.
- Self-test without the game: `python emutest/textlogtest.py` (ARM code in armsim vs the Python
  model, incl. ring wrap and dedupe).
- `emutest/battlego.py` records too (`--no-textlog` to skip).

Found with it (2026-10-14): top-screen battle lines stay blank (the game builds them in pieces,
the grammar pass resolves the lone `{The}` piece to nothing); "The monsters appear!！" (doubled
mark); 2D: battle command window overlaps ("AttackAbilities"), the church dialogue's 4th line
runs out of the window; 3D bottom screen label "地図切り替え" untranslated.

## Code tail map and executable fixes (2026-10-15)

Free `.text` tail after 0x724040 (codestr's moved strings now go to the `.rodata` padding only,
`codestr.RESERVED_TEXT_TAIL`):

| address | user |
|---|---|
| 0x724040-0x7241FC | `autofit.py` (runtime fit of layout text) |
| 0x724200 / 0x724320-0x7243FF | `namefix.py` (party name codes; its name table, written by the export) |
| 0x724400 | `grammar_code.py` (battle grammar pass) |
| 0x724A00-0x724BBC | `wordwrap.py` (writer + line measurer hooks, shared measure) |
| 0x724C00-0x724F00 | `textlog.py` (test builds) and the debug probes |
| 0x724F00 | `testhook.py` (test input, test builds) |

`emutest/autofittest.py` checks that `autofit.CODE` matches its source and that the caves do
not overlap. `codefix.py` holds the small fixes: `INSTRUCTIONS` (single instructions, checked
against the original bytes), `patch_name_table` / `patch_save_code` (below), inline strings.

- **Runtime fit** (`autofit.py`, details in its docstring): every layout text pane except the
  message windows gets user data "fit" (one line: narrowed to the pane width) or "fim" (box:
  scaled down until the wrapped text fits). The exporter (`Bflyt.window_clamps`) first shortens
  panes that reach over their window frame or the next text on their row, so the fit keeps
  them apart. The static per-pane squeezes are off when the fit is on.
- **Save Code** (priest "Save Code", title "Save Code"): the hero's name is encoded with the
  256-entry table at 0x72EFBE; Latin characters were missing, so the priest showed only "「".
  The keyboard's characters now fill the unused and katakana entries, and the 64-character
  code alphabet (0x72EF3C) is Latin (`codefix.SAVE_CODE_ALPHABET`, position for position, so
  a Japanese code converts letter by letter). The title keyboard's page 1 has every code
  character (digits moved there; space , ' & on page 2). Key types by position (0x60EF84 /
  0x60EFF8 names, 0x60F06C code entry) made keys 60/61 voicing marks and key 35 a space:
  patched to plain keys. Verified: the priest's code typed on the title screen restores the
  save: `python emutest/titlesteps.py --code CODE "wait:4,A,wait:3,A,wait:2,DOWN,DOWN,DOWN,A,wait:3,A,wait:3,code,wait:3,shot:result"`
  (title menu item 4, the code typed key by key, Done).
- **Export warnings** (2026-10-16, user report "A LOT of warnings about missing tags"): all
  1265 were official PC lines (origin "pc") without the hero's name {v:1}, which the official
  script often leaves out (harmless). The export now calls `validate(..., official=origin ==
  "pc")` like the editor does; a full export reports 0 warnings. A warning there is real again.
- **Name keyboard keys and frames** (`namekbd.py`, 2026-10-16, user report): the accent page
  padded missing letters with blank keys (still selectable, typing spaces) and had one space
  key; the game accepts a name of only spaces ("Happy to proceed with ' '?"), while an empty
  name is refused ("Sorry, but that name is not allowed."). Now: no space key, leftover keys
  get `namekbd.FILLERS` (- . ( ) : ~ / ...), which `codefix.name_table_chars` also puts into
  the Save Code name table (a name with "(" gets a code). Command labels were centred at
  x 239 for all three keys: the cursor frames are x 221-248 (page switch) and 221-260 (Back /
  Done; the texture ends at 256), screen x = texture x + 71. `CMD_KEYS` gives each label its
  frame's centre and widest width (wider labels are squeezed, `_Pen.draw(max_width=)`);
  "Delete" became "Back" (もどる). Title route to the name keyboard (deletes slot 1 of the run
  save only): `python emutest/titlesteps.py --build "wait:4,A,wait:3,A,wait:2,DOWN,A,wait:3,A,wait:2,UP,A,wait:3,UP,A,wait:4,shot:kbd"`.
  Proof: `docs/name_keyboard_2026-10-16.png`.
- **Japanese quotes**: the talk window builder 0x614CF0 inserts "「" (0x615044) when the
  message's flag +0x15 is set, the Save Code message another one (0x395BB4); both empty now.

## Layout images and map labels (2026-10-15)

- `texlabel.py`: Japanese text inside textures, redrawn with the 2D14SH font (its fill, the
  original's colours sampled per row, our own outline and shadow): town banners
  (`ui/location/*.bch`, BCH textures 256x32; every banner is written as RGBA4 from one
  template), map hints ("Map scroll" / "off", "Hold to Skip"), quest status badges, the 3D
  field map atlas `menu_map_frame_3d.bflim` (Ⓨ "Swap map" / "Close map", Ⓡ "Show/Hide
  Objective", partly stored rotated). ETC1 images are written as RGBA4 (`texture.encode_rgba4`).
- Checking banners in game: `StartUI(6, N)` shows banner N of `fieldcmn.pack:location_cs_ui.bxon`
  (list order: 0 b002_001a, 4 b011 Cobblestone, 16 m004, 17 b016 ... 28 m024_002a), as the
  area scripts' first-visit `LocationCS*` events do:
  `scenario.py steps banners --mode 3d --steps "StartUI 6 4; WaitMenu; EndUI 6; StartUI 6 16; WaitMenu; EndUI 6" --inputs "wait:1,shot,wait:1,shot,..."`
  (the run reports INVALID, "no text after the talk": banners are textures). All 29 checked
  2026-10-15. `LoadField(map, locator, 3)` after `StartFadeOutDefault; WaitAllFade` changes the
  map (256 = Cobblestone outdoors, locator 14); `Name()` as a steps argument is a nested call.
- `maplabel.py`: the field maps spell one place name with several text panes (single
  characters on curves, stacked, parent + child panes). It clusters them, matches the joined
  Japanese against `data/map_labels.json` (official PC names) and writes the English into the
  first pane, centred, the rest empty. 482 labels in 3188 map layouts, none unresolved.
- `export.TALK_WIDE_2D`: the 2D top-screen message window is 392 px wide (text 376). Not in
  `/ui/battle_2d.pack` (`TALK_WIDE_SKIP_PACKS`): the battle has its own `win_talk_2d` (text 338
  px at x 1 in a frame it sizes itself) and the -10 px shift put its text on the frame (fixed
  2026-10-16, run `fix2d_battle`).
- **Lower-screen talk window** `win_talk_lw_2d` (common_2d + common_2way, root 320x240, text
  pane 300 px at x 2): the code builds it next to `win_talk` (0x384248). Only 4% of the
  dialogue lines fit 300 px of 2D14 (median 347), so it re-broke nearly every line (user
  screenshot "Welcome to Heliodor, / greatest city in all / Erdrea...": 312 px broke, 288 px
  fitted). `export.TALK_LOWER_2D`: the text panes get width 372 and x scale 302/372 (0.81), so
  the pre-wrapped lines stay as they are, condensed (looks clean: run `lwtop_church` showed
  the same scale on the top window). **Used in 2way mode after walking with the D-pad** (the
  circle pad brings talk back to the 3D window; user tip 2026-10-17). Reproduce:
  `scenario.py steps X --mode 2way --pre "hold:DOWN:0.5,wait:0.5,hold:UP:0.6,wait:1" --steps
  "BookIDMessageSetup 26; MessageLoop -1"` (book 26 in Cobblestone; give source 13217 a test
  text temporarily and restore it). The church priest's own talk always uses the 3D window.
- 2D dialogue line width = `gamefont.MESSAGE_2D` = 376 (top window text pane 376 in the 392
  frame, 8 px margins; lower window 376 condensed to 302). Every dialogue string fits it
  (2026-10-17; the widest is 30773 at 375 px), so neither 2D window re-breaks a line.
- `export.reflow_battle`: battle_Text strings lose the line breaks their Japanese does not
  have (PC breaks set for the 3D window broke into "with / their / shield!" in the 2D box).
  The runtime wrap balances them per window. The fit checker assumes the longest `{v:2}` and
  rejects some battle strings by a few px; those are fine in game.
- **2D dialogue wrap** (2026-10-16, user report "the line breaks onto the second line after half
  of the next word was already written there", boxes overflowing): the 2D window draws 2D14
  (16-21% wider than 3D14, not 13%), so lines wrapped for 321 px of 3D14 ran past the 2D line
  width (372.0 at scale 1.0, read with GDB from the writer object [+0x30] in the 2D church
  talk). The game types a message out by setting a growing prefix (the pane buffer holds the
  prefix + NUL, then stale text), so the runtime word wrap only sees the revealed letters: the
  word starts on the line, jumps once it no longer fits, and the extra line falls out of the
  box. Fix: `gamefont.MESSAGE_2D = (("2D14", 372),)` makes every dialogue line fit both windows
  (wrap + fit check, also in the editor), and `python -m dq11tool.rewrap --overflow --apply`
  re-broke the 386 stored texts that overflowed (breaks only). A line that fits never triggers a
  runtime break, prefix or not. Proof: `docs/2d_talk_wrap_before.png` / `_after.png`
  (scratchpad swaptest: the priest greeting replaced by text 11648, church 2d, `--burst 6`).
  Runtime-composed text that overflows can still jump while typing (no full text to look at).

## Party names, descriptions and other runtime text (2026-10-15)

- **Party names** (`namefix.py`): the save keeps each member's name in 14 bytes (6 UTF-16
  units + NUL, member record +2), copied from `syscmn.pack:player_initialize.bxon` at a new
  game. "Veronica" / "Sylvando" had been cut to "Veroni" / "Sylv.", "Hendrik" (7) ran into the
  next field ("Hendrik$"). The export writes a longer name as one character U+E010+k (sorted
  order, `namefix.assign`) and the names into a table at 0x724320; `names` (0x724200, called
  by the grammar stub's pane_fix before gfix) puts them back into every pane text. Room for
  them: `ExportOptions.name_buffer` (22-byte minimum for non-numeric text panes; the game sizes
  label buffers from the layout) and 16 units of slack in sstub (messages built from
  templates). `emutest/namefixtest.py` checks the ARM code against `namefix.resolve`.
  Saves started with an older build keep their stored short names. Test with stored names:
  `scenario.py ... --members 1,2,3,4 --rename "Veroni=Veronica,Sylv.=Sylvando,Hendrik=Hendrik"`.
- **Descriptions** (`ExportOptions.reflow_descriptions`, `export.REFLOW_FILES`): skill / spell
  descriptions (battle_act_text, passive_skill) are written without line breaks; the runtime
  word wrap breaks them for each box (battle help, Magic menu help, Skill Panel box widened to
  136 px, `export.PANE_GEOMETRY`). Wrapped for the 214 px battle help they fell apart into 5
  ragged lines in the panel. The talk window does **not** wrap at runtime: field menu / item
  messages (mainmenu_msg, item_msg, item_failure_msg) are wrapped for it (321 px of 3D14, 3
  lines, inserted names counted as "King Irwin", items as ~160 px). `gamefont.TALK_WINDOW_TABLES`
  makes the fit check use that window (it used to estimate a 2-line, 236 px box from the
  Japanese and flagged ~110 good messages).
- **Balanced runtime breaks** (`wordwrap.py`, 2026-10-15): the 3D battle captions (no box,
  centred) used to fill the first line and leave one word under it ("...takes 1 point of /
  damage!"). Writer and line measurer now share one look-ahead (`measure`: next word and the
  rest of the line) and break where the line so far is at least as wide as the rest, so two
  lines come out balanced ("...drackyma A / takes no damage!"); 3+ line text fills its lines
  and balances the last two; never before a one-letter word (monster letters A/B/C). Code at
  0x724A00-0x724BBC; `python -m dq11tool.wordwrap` checks CODE against SOURCE, `--write`
  prints new bytes. Verified 3D + 2D battle and the Skill Panel (runs bal2_3d, bal2_2d,
  skillww3d).
- Full-width colons in label format strings ("%ls：" ...) are ASCII (`codefix.INSTRUCTIONS`).
- Useful script calls for `scenario.py steps`: `StartUI 6 N` (town banner N), `SetQuestFlag
  ID STATE` (states 1-8 on quests 1-8 show all three catalogue badges), `LoadField
  256 14 3` after `StartFadeOutDefault; WaitAllFade`. The live input hook has no touch (touch
  exists only in movie input), so screens that need a tap (map scroll hint) are not reachable.

## Bisecting and emulator-only issues

- `scenario.py --only ","` keeps none of the RomFS changes (the battle/event patches still
  apply to the original files), `--pure-code` replaces code.bin by the Japanese executable with
  only the test hook, text log and grammar pass, `--opt NAME=VALUE` overrides ExportOptions,
  `--burst N` takes N screenshots after the talk (`BURST_DT` env = seconds between them).
  `AZ_SETTINGS="graphics_api=1"` overrides qt-config.ini values for one run.
- 2D menus (font 2D9) draw g, j, p, q, y raised onto the baseline ("Strength" can look like
  "Strensth" in a zoomed screenshot): that is the original font's design, nothing is clipped
  (checked pixel by pixel 2026-10-15; the accent composing's lift of 2 rows does not cut glyphs).
- The battle transition swirl shows garbage on the bottom screen also with the unmodified game
  (`--only "," --pure-code`): Azahar, not the translation.
- Scenario battles that mix monsters of other regions (for example "Hard-boiled eggsoskeleton,
  Crack-billed platypunk, Midnight horknight" in the church zone) make Azahar exit, also with
  the unmodified game. Use monsters of one zone (`scenario.py zones`).
- The smoke run `battle_longnames_2way` exits early ("connection forcibly closed", ~700-860
  records, during loading) about one run in three or four, the unmodified game too (checked
  2026-10-15 with `--only "," --pure-code`): rerun it before suspecting the mod. With pure JP
  code the runner reports "no result after 80 presses" (it waits for English result text).
  Re-measured 2026-10-16: Vulkan 3 exits in 8 runs (also without the party-chat packs), OpenGL
  (`AZ_SETTINGS="graphics_api=1"`) 0 exits in 5 - the same Azahar 2way/Vulkan problem as the
  weapon shops. "No result after 80 presses" with a long sleep/Kasap battle is the runner's
  press budget, not a crash.


## Real 3DS: only ARMv6K / VFPv2 instructions in code caves (2026-10-17)

A real 2DS crashed with "undefined instruction" when the X menu opened: `autofit.py` used
`vmov.f32 s16, #1.0` (VFPv3). Azahar executes ARMv7/VFPv3 instructions too, so emulator tests
cannot catch this. Never use movw/movt, ubfx/sbfx/bfi/bfc, sdiv/udiv, mls, rbit, VFP immediates
(`vmov.f32 sN, #x` - load a `.float` literal with vldr instead) or fixed-point vcvt. After any
cave change run `python emutest/armv6check.py workspace/extracted/exefs/code.decompressed.bin
_emutest/azahar/user/load/mods/0004000000199200/exefs/code.bin` (ldrd hits are literal-pool data).

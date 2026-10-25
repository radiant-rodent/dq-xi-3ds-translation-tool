# Dragon Quest XI (3DS) – English fan translation

An English translation of the Japanese-only Nintendo 3DS version of *Dragon Quest XI*
(ドラゴンクエストXI 過ぎ去りし時を求めて), and the tool it is made with.

![Battle text with runtime word wrap](docs/battle_word_wrap.png)

## Play it

**Web patcher: https://radiant-rodent.github.io/dq-xi-3ds-translation-tool/**

* **3DS with Luma3DS** (play from your cartridge): download the
  [LayeredFS patch](https://radiant-rodent.github.io/dq-xi-3ds-translation-tool/patch/DQXI_EN_layeredfs.zip),
  unzip it to the root of your SD card, enable “Enable game patching” in the Luma3DS
  configuration menu (hold SELECT while booting) and start the game from the cartridge.
* **Emulators, flash carts or an installed copy**: dump your Japanese cartridge as a
  **decrypted** `.3ds` (for example with GodMode9), open the web patcher and choose the
  file. It is read locally by your browser: nothing is uploaded and nothing is installed.
  You get either
  * a patched `.3ds` for emulators (Azahar, Citra forks) and flash carts, or
  * an installable `.cia`: install it with FBI on a 3DS with Luma3DS (which provides the
    signature patches the fakesigned CIA needs).

The patcher checks that the file is the original Japanese game before it changes anything.
It works in any current desktop browser (Chrome, Edge, Firefox, Safari): the page is plain
static HTML and JavaScript, and the patched image is assembled from your file and handed
to you as a normal download.

## What the mod changes

### The text

* **Everything a player reads is in English**: story dialogue and cutscenes, NPC talk,
  party chat, menus, battle messages, items, equipment, spells and abilities, monster
  names, quests, tutorials, books, the bestiary, accolades and the Tickington
  "Adventure Log" worlds.
* **The official English wording wherever it exists.** Lines the 3DS shares with the
  official English *Dragon Quest XI S* (PC) use that translation word for word, including
  its character, place, monster and item names and its British spelling. Text that only
  the 3DS has (the 2D mode, 3DS-only monsters, items and menus) is translated in the same
  style, using official names where other localisations established them.
* **Full names everywhere.** The save only has room for 6-character names, so long names
  ("Veronica", "Sylvando", "Hooded Stranger") are stored as short codes that the game
  expands when it draws them. Old saves with cut names keep working.
* **Proper English grammar in battle.** The game builds battle lines from templates; the
  mod resolves articles and plurals after the names and numbers are filled in ("The slime
  takes 1 point of damage!", "The slimes are defeated.", "A group of drackies appears!").

### Engine and layout changes

* **Word wrap at spaces.** The game breaks Japanese text anywhere. Text it assembles at
  runtime (battle messages, descriptions) now breaks between words, with balanced lines in
  the centred battle captions.
* **Crisp text that fits its frame.** Labels and boxes too small for the English are
  narrowed string by string at runtime, using condensed copies of the Latin letters carved
  on the pixel grid (four levels per font). Text is only scaled, which blurs it, when even
  the narrowest level does not fit. The narrow Latin font comes from the European
  *Dragon Quest VII*, with accented letters added.
* **Longer NPC lines** continue in extra message boxes instead of being cut off.
* **Windows resized for English**: the 2D message windows, the battle command window,
  the enemy list, the per-character action labels on the lower screen (one column per
  party member) and other panes whose Japanese size was too small.
* **More memory** for the UI layout heap and the main heap (English layouts need more
  vertex buffers, and the English NPC files are larger). Without it the item and shop
  menus crashed.
* **Images and names in English**: textures with Japanese text (town banners, map hints,
  quest badges, the 3D map) are redrawn, field map labels use the official place names,
  and the title screen shows the official English logo.
* **Name entry and Save Codes**: a Latin keyboard with accented letters for naming the
  hero, and Save Codes that use Latin letters and work with Latin names.
* **Small fixes in the game code**: no Japanese quotation marks around dialogue, ASCII
  colons and punctuation in labels, and the monster count shown as a plain number.

The game code patches only use ARMv6K / VFPv2 instructions, so they run on real Old 3DS
and 2DS hardware, not just emulators.

## The translation tool (`dq11tool/`)

A local web app for extracting, translating, reviewing and rebuilding the game.

```
pip install -r requirements.txt
python -m dq11tool            # or start.bat; opens http://127.0.0.1:8311/
```

* **Project**: point it at your decrypted Japanese `.3ds`; it extracts all text into a
  SQLite project in `workspace/` (gitignored).
* **Translate**: edit strings by category with previews of the in-game box sizes;
  optional machine-translation drafts through any OpenAI-compatible API, Anthropic or
  Google Translate (API keys are only stored locally in `workspace/secrets.json` if you ask).
* **Build**: writes a `.3ds`, a `.cia`, a Luma3DS LayeredFS folder and/or a browser patch
  (`.dq11patch`, see below).

Two inputs are not in this repository because they are copyrighted game assets:

* `dq11tool/data/m7_win_p14.bcfnt`, the narrow Latin font. Extract it from your own
  decrypted *Dragon Quest VII: Fragments of the Forgotten Past* (Europe) dump:
  `python -m dq11tool.fontimport "Dragon Quest VII (Europe).3ds"`
* `dq11tool/data/logo_en.png`, the English title logo artwork (optional; without it the
  Japanese logo is kept).

## The web patcher (`docs/`)

`docs/` is a static site (GitHub Pages: *Deploy from a branch*, `main`, `/docs`).
`docs/dq11patch.js` is a dependency-free ES module that re-implements the tool's
`.3ds` rebuild (`dq11tool/ctr/build.py`) and CIA builder (`dq11tool/ctr/cia.py`) in the
browser, including the BLZ compressor, the RomFS hash tree and a streaming SHA-256, and
produces byte-identical files.

A `.dq11patch` stores, for every changed file, a delta against the same file of the
player's own ROM (PACA archives and the ExeFS code are compared decompressed), so it
contains no original game data that the player does not already have.

The site serves two generated files, `docs/patch/DQXI_EN.dq11patch` and
`docs/patch/DQXI_EN_layeredfs.zip`. Regenerate both whenever the translation changes and
commit them with the change (see [CLAUDE.md](CLAUDE.md)):

```
python -m dq11tool.patchfile workspace --site docs/patch
```

The output is deterministic, so unchanged translations give byte-identical files. To check
the JavaScript patcher against the Python build:

```
python -m dq11tool.patchfile workspace --patch out.dq11patch --3ds reference.3ds
node tests/patcher_test.mjs DQXI.3ds out.dq11patch js.3ds reference.3ds
```

and, for the CIA (`reference.cia` from `dq11tool.ctr.cia.build_cia("reference.3ds", "reference.cia")`):

```
node tests/patcher_test.mjs DQXI.3ds out.dq11patch js.3ds reference.3ds js.cia reference.cia
```

## Emulator tests (`emutest/`)

Scripts that build test versions, boot them in a portable Azahar copy and drive the game
through its RPC server (no window automation). See [docs/AGENT_GUIDE.md](docs/AGENT_GUIDE.md).

## Legal

This is an unofficial fan project, not affiliated with or endorsed by Square Enix or
Nintendo. *Dragon Quest* is a registered trademark of Square Enix. You need your own copy
of the game: no ROM is distributed here. The LayeredFS download contains translated
versions of some of the game's files and only works with the original cartridge. The
source code is MIT licensed (see [LICENSE](LICENSE)).

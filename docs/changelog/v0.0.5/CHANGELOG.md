LayeredFS patch for the Dragon Quest XI (3DS) English translation. The [web patcher](https://radiant-rodent.github.io/dq-xi-3ds-translation-tool/) serves this version too.

## Fixed issues

- **Random yes/no popups after NPC lines** ([#1](https://github.com/radiant-rodent/dq-xi-3ds-translation-tool/issues/1)): the export damaged the header of 60 one-line talks (the likely cause), and lines split over several boxes asked their question too early. Both are fixed. Yes/no questions also match their text now: 16 lines end with the question they ask, and the 2D-mode alternative lines are split into boxes like the 3D lines.
- **Heliodor cat quest** says Amelia, not Mary, in the dialogue and the quest log ([#2](https://github.com/radiant-rodent/dq-xi-3ds-translation-tool/issues/2)).
- **":Action" prompt** is no longer covered by the Y button icon ([#3](https://github.com/radiant-rodent/dq-xi-3ds-translation-tool/issues/3)).
- **Pep Power descriptions in 2D** fit their box: the box is larger and long descriptions use narrower, sharp letters ([#4](https://github.com/radiant-rodent/dq-xi-3ds-translation-tool/issues/4)).
- **Guild menu "Quit"** no longer reads "I don't want / anything.", and the clearest profile option mistakes are fixed ([#5](https://github.com/radiant-rodent/dq-xi-3ds-translation-tool/issues/5); the full review of the profile options is still open).
- **Speech bubbles over NPC heads** no longer break in the middle of a word ([#6](https://github.com/radiant-rodent/dq-xi-3ds-translation-tool/issues/6)). The game cuts a bubble text of more than 8 letters after the 8th unless the text has its own line break, so all 249 bubble lines now have one: two lines, broken between words.
- **StreetPass profile**: long hobbies, home towns and similar values no longer run into their label, and the labels on the 2D screen are not squashed any more.

## Text

- **Line breaks** of thousands of dialogue and message texts were reworked: sentences are no longer cut in the middle where they could break at their end, and lines that stopped short are filled. No words changed.
- **Message boxes that were too narrow**: the hat shop, casino and medal prizes, horse races, training and dive messages, the Tockle spirits and a few other menu messages use the full width of the dialogue box now.
- **Tickington** reviewed: consistent names, cut-off lines rewritten, archaic "thee/thy" mistakes fixed. The hat shop seller has one speech tic ("tick") in all his lines.
- Untranslated "「…………。" lines are translated.
- **Heart symbol** ♥: the game fonts have no heart, so hearts in 46 lines showed up as notes. A heart is now part of the fonts.

![Hearts in 2D and 3D mode](https://raw.githubusercontent.com/radiant-rodent/dq-xi-3ds-translation-tool/v0.0.5/docs/changelog/v0.0.5/hearts.png)

## Textures

Everything that is drawn as an image in the casino, the horse races and a few menus now has English lettering. Where the PC version has official artwork it is used, the rest is drawn in the style of the original.

- **Horse race**: "And They're Off!", "Last Lap!", "New Record!", "Nice Work!" at the finish, the rank counters (1st, 2nd, 3rd, 4th), the Stamina gauge with its label, bar and flash.
- **Slot machines**: "Free Spins!", "Metal Mode!", "JACKPOT", the Set Bet / Start / Quit buttons.
- **Roulette**: "You Win!", "Too Bad...", "No More Bets!", the Move Cursor / Start / Set Bet / Bet x10 / Quit buttons, the payout display above the wheel (Payout, x27 ... x2, Chest, Result).
- **Poker**: all hand names (Two Pair up to Royal Straight Flush, Five of a Kind, Royal Jelly Flush), "Stake Doubled!", "Tough Luck!", "Snap!", the payout table, the Set Bet / Start / Quit / Deal buttons and the Change / Keep plates.
- **Other casino lettering**: "COINS!" and "Item Earned!".
- **Menus**: "Allocate All" and "Hit for a Bonus!" on the skill panel, the Skip icons, "Back" in the dungeon, "Start!!" of the Draconian Quest, the "Downtown Heliodor" location banner, the credits caption ("Game Design & Scenario"), "Close map" / "Swap map" / "Show/Hide Objective" on the field maps (every map has its own copy), and the Latin name keyboards with their accented letters.

![Race lettering before and after](https://raw.githubusercontent.com/radiant-rodent/dq-xi-3ds-translation-tool/v0.0.5/docs/changelog/v0.0.5/race_lettering.png)

![Casino lettering before and after](https://raw.githubusercontent.com/radiant-rodent/dq-xi-3ds-translation-tool/v0.0.5/docs/changelog/v0.0.5/casino_lettering.png)

Menus and labels (as stored in the game's image files):

![Menus and labels](https://raw.githubusercontent.com/radiant-rodent/dq-xi-3ds-translation-tool/v0.0.5/docs/changelog/v0.0.5/menus_and_labels.png)

Casino buttons and tables:

![Casino buttons and tables](https://raw.githubusercontent.com/radiant-rodent/dq-xi-3ds-translation-tool/v0.0.5/docs/changelog/v0.0.5/casino_menus.png)

Race labels and the coin letters (each letter is its own image in the game, so they are shown unjoined):

![Race labels and coin letters](https://raw.githubusercontent.com/radiant-rodent/dq-xi-3ds-translation-tool/v0.0.5/docs/changelog/v0.0.5/race_and_coins.png)

## Translation tool

- The editor shows speech-bubble lines as the game draws them and can filter them, shows `{PAGE}` as separate message boxes, and no longer reports "too wide" for texts the game wraps or narrows itself.
- `python -m dq11tool.linebreak` re-breaks lines (dry run by default), `python -m dq11tool.bubble` breaks the bubble lines.

Only the LayeredFS zip is attached.

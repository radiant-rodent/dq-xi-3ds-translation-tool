LayeredFS patch for the Dragon Quest XI (3DS) English translation. The [web patcher](https://radiant-rodent.github.io/dq-xi-3ds-translation-tool/) serves this version too.

> **Experimental:** these changes have not been fully tested in-game yet. Please report anything that looks off.

## Fixes

- **Random yes/no popups after NPC lines** ([#1](https://github.com/radiant-rodent/dq-xi-3ds-translation-tool/issues/1)): the export damaged the header of 60 one-line talks (the likely cause), and lines split over several boxes asked their question too early. Both are fixed.
- **Yes/no questions now match their text**: 16 lines end with the question they ask (e.g. "...Is something the matter?" instead of "What is it?"). 2D-mode alternative lines are split into boxes like the 3D lines.
- **Heliodor cat quest** says Amelia, not Mary, in the dialogue and the quest log ([#2](https://github.com/radiant-rodent/dq-xi-3ds-translation-tool/issues/2)).
- **":Action" prompt** is no longer covered by the Y button icon ([#3](https://github.com/radiant-rodent/dq-xi-3ds-translation-tool/issues/3)).
- **Pep Power descriptions in 2D** fit their box: the box is larger and long descriptions use narrower, sharp letters ([#4](https://github.com/radiant-rodent/dq-xi-3ds-translation-tool/issues/4)).
- **Guild menu "Quit"** no longer reads "I don't want / anything.", and the clearest profile option mistakes are fixed ([#5](https://github.com/radiant-rodent/dq-xi-3ds-translation-tool/issues/5), full review still open).
- Untranslated "「…………。" lines are translated.
- **Speech bubbles over NPC heads** no longer break in the middle of a word ("Ahh, goo / d old pep powers..."). The game cuts any bubble text of more than 8 letters after the 8th unless the text has its own line break, so all 249 bubble lines now have one: two lines of at most 130 px, broken at word boundaries, preferably after punctuation and never after "the", "of", "and"...
- **StreetPass profile hobbies** ("Slot Machines", "Weaver's Peak", long home towns) no longer run into the "Hobbies:" label; long values are narrowed to the room that is left. On the 2D screen the labels are not squashed to half their width any more.
- **Tickington hat shop**: the hat seller's speech tic is one word everywhere ("tick", other Tockles say "tock") instead of a mix of "chum" and "chappy", and every line of the shop has it.

## Text

- **Line breaks** of more than 16,000 texts: a sentence is no longer cut in the middle where it could break after its end ("Howdy! I'm a talking horse! I love to chat! Ask / me anything!" is now "...I love to chat! / Ask me anything!"), and lines that stopped short are filled. Only the breaks changed, not a word.
- **Wider dialogue box**: the hat shop, casino and medal prizes, coin shop, horse race counter, training and dive messages, the Tockle spirits (Opapp and friends), the Seren hint and a few more menu messages are shown in the full-width dialogue box, but their lines were broken for a narrow one. They use the full width now.
- **Heart symbol** ♥: the game fonts only have a note, so the 46 lines with a heart showed a note. A heart is now part of the fonts.

![Hearts in 2D and 3D mode](https://raw.githubusercontent.com/radiant-rodent/dq-xi-3ds-translation-tool/v0.0.5/docs/changelog/v0.0.5/hearts.png)

- Tickington reviewed: consistent names, cut-off lines rewritten, archaic "thee/thy" mistakes fixed.

## Translation tool

- The editor shows speech-bubble lines as the game draws them (size by length, 130 px text area) and can filter them.
- The preview shows `{PAGE}` as separate message boxes and counts only the longest box, measures grammar tags like the exporter, and no longer counts the style markers `{v:66}` / `{v:64}` as variables.
- "Too wide" warnings no longer appear for skill and item descriptions the game wraps itself, or for speech-bubble lines the game narrows anyway.
- `python -m dq11tool.linebreak` re-breaks lines (dry run by default), `python -m dq11tool.bubble` breaks the bubble lines.

## Textures

Casino and horse race lettering now uses the official English artwork from the PC version: race signs, ranks, Stamina gauge, roulette, poker hands, slot bonuses, JACKPOT, Item Earned!. The skill panel's "Allocate All" button and the coin win letters are redrawn.

![Race lettering before and after](https://raw.githubusercontent.com/radiant-rodent/dq-xi-3ds-translation-tool/v0.0.5/docs/changelog/v0.0.5/race_lettering.png)

![Casino lettering before and after](https://raw.githubusercontent.com/radiant-rodent/dq-xi-3ds-translation-tool/v0.0.5/docs/changelog/v0.0.5/casino_lettering.png)

Only the LayeredFS zip is attached.

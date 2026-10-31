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

## Text

- Tickington reviewed: consistent names, cut-off lines rewritten, archaic "thee/thy" mistakes fixed.

## Textures

Casino and horse race lettering now uses the official English artwork from the PC version: race signs, ranks, Stamina gauge, roulette, poker hands, slot bonuses, JACKPOT, Item Earned!. The skill panel's "Allocate All" button and the coin win letters are redrawn.

![Race lettering before and after](https://raw.githubusercontent.com/radiant-rodent/dq-xi-3ds-translation-tool/v0.0.5/docs/changelog/v0.0.5/race_lettering.png)

![Casino lettering before and after](https://raw.githubusercontent.com/radiant-rodent/dq-xi-3ds-translation-tool/v0.0.5/docs/changelog/v0.0.5/casino_lettering.png)

Only the LayeredFS zip is attached.

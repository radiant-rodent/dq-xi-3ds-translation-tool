"""Hand fixes of translations found with the emulator tests (emutest/scenario.py), kept as data
so they can be reviewed and re-applied: {source id: (expected current translation or None, new)}.
A fix is applied only when the current translation still is the expected one (or None = any),
so later edits in the editor are never overwritten. Origin 'manual', status 2.

    python -m dq11tool.fixes [--apply]
"""
from __future__ import annotations

import argparse
import time

from . import gamefont
from .export import validate
from .project import Project

# Encounter lines (battle_Text.bxon 13-21): {v:5} is the closing mark the game adds ("!" or "?",
# 0x28B858; codefix makes it ASCII), so the text has none of its own. Official PC wording where it
# exists (Battle_Encount_Arawareta_1-3); the "surrounded" lines are 3DS-only.
SQUEEZED = "squeezed"    # third element: a layout label the export narrows to fit; not wrapped

FIXES: dict[int, tuple] = {
    13: (None, "{A}{v:2} {s:appears|appear}{v:5}"),
    14: (None, "A group of {v:2}{pl} appears{v:5}"),
    15: (None, "A group of enemies appears{v:5}"),
    16: (None, "{The}{v:2} {s:is|are} surrounded{v:5}"),
    17: (None, "{The}{v:2}{pl} are surrounded{v:5}"),
    201: (None, "{A}{v:2} {s:appears|appear}!"),
    2113: (None, "{A}{v:2} {s:shows|show} up!"),
    2114: (None, "{A}{v:2} {s:comes|come} up out of the ground!"),
    18: (None, "The group of enemies is surrounded{v:5}"),
    19: (None, "The party is surrounded by {a}{v:2}{v:5}"),
    20: (None, "The party is surrounded by a group of {v:2}{pl}{v:5}"),
    21: (None, "The party is surrounded by a group of enemies{v:5}"),
    # The suffix the game appends to a monster name for several of a kind (0x4D63E0, count at
    # +0x20C > 1; only monster groups): proper plural and the {grp} mark for {s:..}. The user saw
    # "The slimes is defeated." (2026-10-14); official PC: "<LM_DefPlr_MonsterName> are defeated."
    11548: ("s", "{pl}{grp}"),
    37: ("{The}{v:2} is defeated.", "{The}{v:2} {s:is|are} defeated."),
    57: ("{The}{v:2} is defeated.", "{The}{v:2} {s:is|are} defeated."),
    359: ("{The}{v:3} is defeated.", "{The}{v:3} {s:is|are} defeated."),
    # the name of a mixed monster group ({v:2} of the battle lines). Official PC:
    # Battle_Result_Monster_Yaxtutuketa_3 "The group of enemies are defeated!" (British plural)
    11547: ("Monster Pack", "{noun}Group of enemies{grp}"),
    # Pep Power requirements (battle skill list, one line of 192-372 px; the PC wording
    # Menu_Battle_List_007_00x "Requires both party members to be pepped up." is too long)
    11543: ("{v:2} usable in pep with {v:3}", "{v:2} pepped up, with {v:3}"),
    11544: ("{v:2}'s {v:3} usable in pep", "{v:2} and {v:3} pepped up"),
    11545: ("{v:2}, {v:3} & {v:4} usable in pep", "{v:2}, {v:3} and {v:4} pepped up"),
    11546: ("Everyone can use when pepped", "Whole party pepped up"),
    # Abbreviated for their executable slots, which the export moves when needed
    # (Project.code_movable). Official PC names: OddEffect DisplayName_Doku / _Noroi,
    # Menu_Region_002/003, Menu_Modoru
    4932: ("Psn", "Poisoned"),
    4936: ("Crs", "Cursed"),
    8092: ("Hed", "Head"),
    8077: ("Tor", "Torso"),
    8025: ("Bck", "Back"),
    # window title; the export narrows it to 0.76 in the 90 px 2D pane, 0.97 in 3D (checked by hand)
    40544: ("DraconQuest", "Draconian Quest", SQUEEZED),
    # {v:5} of "{v:1} examined {v:5}." (7169), set at 0x4C9F80 by the kind of furniture
    # (the third, 家具 "furniture", is codefix.ADDRESS_MOVES)
    44718: ("Cab", "the wardrobe"),
    44719: ("Drawr", "the drawer"),
    # item details, top right of the bottom screen: the button to the page "Dropped By" /
    # "Sources"; "View Obtain Info" ran off the screen (user report 2026-10-14)
    8137: ("View Obtain Info", "View Sources"),
    # title screen, Save Code: grammar; the special-code warning said the opposite of the
    # Japanese (once used, it works only on this system)
    44255: ("Enter a Save Code from a church to\nrestart from near same place.",
            "Enter a Save Code from a church to\nresume from about the same place."),
    44262: ("This special Save Code can only be\nreused on this system once used.",
            "This is a special Save Code: once used,\nit works only on this system."),
    # monster name (the achievement 3576 with the same Japanese is "Scrap Merchant");
    # official PC ZukanDisplayName Name_M086_E002
    5269: ("{noun}Scrap Merchant", "{noun}Hunter Mech", SQUEEZED),
    # save slot badge しばりプレイ中 (90 px pane; the runtime fit narrows it a little)
    40684: ("Draconian", "Draconian Quest", SQUEEZED),
    # shortened for their old pane widths; official PC names (Menu_Shop_List_005_002,
    # Menu_Main_Command_000_002, OddEffect DisplayName_BeastMode_WAtk)
    40699: ("Resurrect", "Resurrection", SQUEEZED),
    8047: ("Stat", "Attributes", SQUEEZED),
    4979: ("W Side", "Wild Side", SQUEEZED),
    # camp menu (90-120 px one-line panes, runtime fit); PC "Ask Statue for Help" needs 131 px
    40471: ("Pray Statue", "Pray to Statue", SQUEEZED),
    40472: ("Merchant", "Talk to Merchant", SQUEEZED),
    40473: ("Pray and rest", "Pray and Rest", SQUEEZED),
    41470: ("Pray and rest", "Pray and Rest", SQUEEZED),
    # Attributes screens: the official PC names do not fit the 90-102 px label panes (2D pixel
    # font narrowed to 0.8 lost the g's descender), "Mag." fits 1:1 in both modes
    # (win_tsuyosa1 bottom panel, win_tsuyosa5 table). Resilience had lost its colon
    40575: (None, "Mag. Might:", SQUEEZED),
    40576: (None, "Mag. Mending:", SQUEEZED),
    40578: ("Resilience", "Resilience:", SQUEEZED),
    40349: ("Magical Might", "Mag. Might", SQUEEZED),
    40350: ("Magical Mending", "Mag. Mending", SQUEEZED),
    4951: ("Deft", "Deftness", SQUEEZED),
    # title of the active Draconian Quest restrictions (108 px one-line pane, runtime fit 0.96)
    41075: ("Drac. Quest", "Draconian Quest", SQUEEZED),
    # abbreviations left from the old limits. Monsters: the 邪 variants are "Malicious" (official
    # PC, e.g. Malicious drackyma); さまようロトのよろい as in the quest text
    5726: ("{noun}M. hocus\nchimaera", "{noun}Malicious\nhocus chimaera"),
    5771: ("{noun}W. loto\narmour", "{noun}Wandering\nErdrick's armour"),
    5814: ("Benef. Effects Removed on Attack", "Removes Beneficial Effects on Attack"),
    11431: ("Yel. ruffian mask", "Yellow ruffian mask", SQUEEZED),
    11433: ("Blue Ruffian Mask", "Blue ruffian mask"),
    42188: ("Hibiscus Cors. A", "Hibiscus corsage A", SQUEEZED),
    # Character Builder: 装備時 skills, as 6059 "Wielding: Parry +{v:8}%" (PC "... When Wielding");
    # {v:8} is a 1-2 digit number (the fit estimate takes 80 px), measured with "+30" by hand
    6041: ("MP Abs Wld+{v:8}%", "Wielding: MP Absorption +{v:8}%", SQUEEZED),
    6045: ("M.Might Wld+{v:8}", "Wielding: Mag. Might +{v:8}", SQUEEZED),
    6047: ("Max MP Wield +{v:8}", "Wielding: Max MP +{v:8}", SQUEEZED),
    6056: ("Mending Wield +{v:8}", "Wielding: Mag. Mending +{v:8}", SQUEEZED),
    # party member names (player_initialize.bxon): cut to the 60 px the Japanese length gave
    11494: ("Veroni", "Veronica", SQUEEZED),
    3303: ("Sylv.", "Sylvando", SQUEEZED),
    # 3DS-only monsters (Tickington / Hall of Remembrance; not in the PC data): the official
    # Dragon Quest XI S names (gamerguides.com area lists, dragon-quest.org; 2026-10-15) instead
    # of literal drafts. Unique bosses are proper names (no {noun}: "Dayle attacks!")
    5763: ("{noun}Psaro burn", "Psaroid"),
    5068: ("{noun}Psaro knight", "Sir Roseguardin"),
    5773: ("{noun}Slumberweed", "Dazey"),
    5768: ("{noun}Megaton\ngios", "Hunkorvus"),
    5780: ("{noun}Fake big\nbrother", "Dud Dude"),
    5776: ("{noun}Queen\nmatilda", "Maeve"),
    5777: ("{noun}Ojamaro", "Obstructicus"),
    5783: ("{noun}Dark minion", "Archfiend's Acolyte"),
    5055: ("{noun}TimeDestroyer", "The End of Time"),
    5067: ("{noun}Rebirth king", "Revivalist"),
    5785: ("{noun}Endragon\nof doom", "Doomsday Dragon"),
    5779: ("{noun}Ultra\nmetal king", "King of Metal King Slimes"),
    5761: ("{noun}Malroth\ndragon", "Wyrmalroth"),
    5760: ("{noun}Fake\ndragon", "Lord Dragon"),
    5762: ("{noun}Zoma's\nlady", "Zomaiden"),
    5764: ("{noun}Mildropos", "Nimclops"),
    5765: ("{noun}Naumann\nmoor", "Mortamammoth"),
    5766: ("{noun}Orgo\ndemira", "Orgodelotl"),
    5767: ("{noun}Rapson\nhoop", "Hoopthorne"),
    5769: ("{noun}Nelgel\ndemon", "Nelgelas"),
    5774: ("{noun}Pegasus\ngrandpa", "Grampogriff"),
    5775: ("{noun}Pegasus\nwannabe", "Nagasus"),
    5784: ("{noun}Daytime\nemperor", "Dayle"),
    5771: ("{noun}Wandering\nErdrick's armour", "Erdrick's Restless Armour"),
    5102: ("{noun}Hell shisa", "{noun}King foo dog"),
    5208: ("{noun}Beles", "{noun}Beleth"),
    5101: ("{noun}Flame\nhound", "{noun}Hot dog"),
    5207: ("{noun}Hell\nkeeper", "{noun}Hell's gatekeeper"),
    5209: ("{noun}Devil\nprince", "{noun}Prince of darkness"),
    5294: ("{noun}Laughing bag", "{noun}Bag o' laughs"),
    5295: ("{noun}Dancing\njewel", "{noun}Goodybag"),           # PC slot tips: "goodybags"
    5276: ("{noun}Bamboo\nspearman", "{noun}Bambooligan"),
    5277: ("{noun}Evil\nbamboo", "{noun}Bamboozler"),
    5278: ("{noun}Clockwork\ndaimyo", "{noun}Bamboogieman"),
    5425: ("{noun}Low bird", "{noun}Firebird"),
    5426: ("{noun}Hawk\nblizzard", "{noun}Snowbird"),
    5427: ("{noun}Purgatory\nbutterfly", "{noun}Hybird"),
    5331: ("{noun}Zombie\nmaster", "{noun}Voodoogooder"),
    5340: ("{noun}Shade binder", "{noun}Hellbound horror"),
    5359: ("{noun}Fondue", "{noun}Fromage fray"),
    5360: ("{noun}Chemical\njelly", "{noun}Fromage fey"),
    5361: ("{noun}Black\nmold kid", "{noun}Mouldy cheese"),
    5210: ("{noun}Silver\ndevil", "{noun}Silvapithecus"),
    5212: ("{noun}Devil lord", "{noun}Batmandrill"),
    5119: ("{noun}King\nelephant", "{noun}Elephant king"),
    5117: ("{noun}Nauman\nborg", "{noun}Naumannborg"),
    # Dragon Quest II / III bosses (Echo Chamber): proper names as in the current remakes
    5211: ("{noun}Bazuzu", "Pazuzu"),
    5772: ("{noun}Baramos", "Baramos"),
    5786: ("{noun}Baramos\nzombie", "Bones of Baramos"),
    # bag family (わらいぶくろ Bag o' laughs, ふくぶくろ Bag o' tricks): the sweets bag's series name
    5296: ("{noun}Sweet bag", "{noun}Bag o' sweets"),
    # 3DS-only DQ X monsters without an English release: series conventions (こまいぬ = foo dog)
    5100: ("{noun}Raging\nkomainu", "{noun}Raging foo dog"),
    5343: ("{noun}Under\nwarden", "{noun}Underwarden"),
    # Tickington bosses as named in DQ XI S (Galenholm / Briscoletti's Mansion quests)
    5065: ("{noun}Ugahn", "Wrongo Bongo"),
    5782: ("{noun}Salaman", "Pilferpithecus"),
    # 魔王ウルノーガ as a battle name (PC M601 "Lord of Shadows"); 5788 is the 2D mode's second
    # form (developer name "2Dウルノーガ第二"), shown as the same boss
    5465: ("Rise of the Lord\nof Shadows", "Lord of Shadows"),
    5788: ("{noun}2D mordegon2", "Lord of Shadows"),
    # クルッチ (no PC name): "Kurucchi" as the party name and his uncle Ojicchi's lines
    26474: ("Kulchi can be heard humming from inside.{CR}\nKulchi seems to be getting something ready.",
            "Kurucchi can be heard humming from inside.{CR}\nKurucchi seems to be getting something ready."),
    26475: ("Kulchi went back to the altar room!", "Kurucchi went back to the altar room!"),
    26479: ("Kulchi joined your party!", "Kurucchi joined your party!"),
    # Info > Recap: "Battles Won: 8x" / "Fun-Size Forge Uses: 8x" ({v:8}回): the number alone
    41613: ("{v:8}x", "{v:8}", SQUEEZED),
    # mini-medal hat shop column header (90 px, runtime fit 0.87)
    40403: ("Need Tix", "Tickets Needed", SQUEEZED),
    # Atk / Def / Agi / Exp written out where the rewrapped text still fits (help texts:
    # 96 px x 3 lines; skill level-up lines; bestiary)
    6017: ('Raises Atk while\nwielding\n{v:5}.', 'Raises attack while\nwielding\n{v:5}.'),
    9571: ('Lowers Agi of\nall enemies.\nOne use.', 'Lowers agility\nof all enemies.\nOne use.'),
    9594: ('Ups Mag\nMight a little.\nUsed up.', 'Ups magical\nmight a little.\nUsed up.'),
    9596: ('Ups Mag\nMend a little.\nUsed up.', 'Ups magical\nmending a bit.\nUsed up.'),
    9616: ("Drum that\nboosts all\nallies' Atk.", "Drum that\nboosts all\nallies' attack."),
    9876: ('Legend blade\nthat lowers\nfoe Def.', 'Legend blade\nthat lowers\nfoe defence.'),
    9884: ('Bejewelled\nblade\nlowering Def.', 'Bejewelled\nblade, lowers\ndefence.'),
    9890: ('Mighty blade\nthat lowers\nfoe Def.', 'Mighty blade\nthat lowers\nfoe defence.'),
    9894: ('Razor katana\nthat lowers\nfoe Def.', 'Razor katana\nthat lowers\nfoe defence.'),
    9896: ('God-loved\nblade cuts Atk', 'God-loved\nblade cuts\nattack'),
    9907: ('Ultimate\nblade cuts\nenemy Def', 'Ultimate\nblade cuts\ndefence.'),
    9938: ('Serrated knife\nthat lowers\nAtk.', 'Serrated knife\nthat lowers\nattack.'),
    9942: ('Serrated blade\ncuts enemy\nAtk', 'Serrated blade\ncuts enemy\nattack'),
    9988: ('Axe that\nlowers Atk of\nfoes it cuts.', 'Axe that\nlowers attack\nof foes it cuts.'),
    9994: ('Mighty axe\ncuts enemy\nAtk', 'Mighty axe\ncuts enemy\nattack'),
    9997: ("Matchless axe\nlowers foe's\nAtk.", "Matchless axe\nlowers foe's\nattack."),
    10001: ('Expert axe\nboosts Agi &\ncrits.', 'Expert axe\nboosts agility\n& crits.'),
    10003: ("Unbeatable\naxe cuts foe's\nAtk.", "Unbeatable\naxe cuts foe's\nattack."),
    10074: ('Star rang\n+Agi,+Atk at\ntimes', 'Star rang: may\nraise agility\nand attack.'),
    10145: ('Magic spear:\nlightning &\nAgi up.', 'Magic spear:\nlightning &\nagility up.'),
    10236: ("Use in battle:\nboosts allies'\nAgi.", "Use in battle:\nboosts allies'\nagility."),
    10327: ('Cruel whip\ncuts Atk, may\nparalyse.', 'Cruel whip\ncuts attack,\nmay paralyse.'),
    10531: ('Earn bonus\nExp in battle,\nup to +75.', 'Earn bonus\nEXP in battle,\nup to +75.'),
    10673: ('Magic\nbandana that\nboosts Agi.', 'Magic\nbandana that\nboosts agility.'),
    10850: ('Magic\nnecklace\nboosting Atk.', 'Magic\nnecklace that\nups attack.'),
    10871: ('Gold band\nthat ups Def a\nlittle.', 'Gold band,\nraises defence\na little.'),
    10916: ('Pure gold\narmlet that\nups Def.', 'Pure gold\narmlet that\nups defence.'),
    10940: ('Shoes earn\nExp per step.', 'Shoes earn\nEXP per step.'),
    10995: ('Clairvoyant\ncard that\nboosts Def.', 'Clairvoyant\ncard: boosts\ndefence.'),
    38063: ('If ye feel outmatched, build up Exp and grow\nstronger, then come save the town.', 'If ye feel outmatched, build up EXP and grow\nstronger, then come save the town.'),
    40881: ('{v:5}. Just {v:8} more Exp, and\nyou shall reach the next level.', '{v:5}. Just {v:8} more EXP, and\nyou shall reach the next level.'),
    43102: ('Atk with boomerangs +10!', 'Attack with boomerangs +10!'),
    43110: ('Dual-wield right-hand Atk up!', 'Dual-wield right-hand attack up!'),
    43141: ('Atk +5 with greatswords!', 'Attack +5 with\ngreatswords!'),
    43142: ('Atk +20 with\ngreatswords!', 'Attack +20 with\ngreatswords!'),
    43158: ('Atk with greatswords +10!', 'Attack with greatswords +10!'),
    43162: ('Atk with greatswords +15!', 'Attack with greatswords +15!'),
    43341: ('Famed metal kings loved\nby all for huge Exp\nrewards!', 'Famed metal kings loved\nby all for huge EXP\nrewards!'),
    43679: ('Toad-ruling frog king.\nLoves to sing, but so\ntone-deaf hearers lose\nDef.', 'Toad-ruling frog king.\nLoves to sing, but so\ntone-deaf hearers lose\ndefence.'),
    43728: ("Gyldenaut boss who\nloves loot and drink.\nLowers foes' Def for a\nbeatdown.", "Gyldenaut boss who\nloves loot and drink.\nLowers foes' defence for\na beatdown."),
    44055: ('Metal kings banded\ntogether for revenge\nafter adventurers hunted\nthem for Exp.', 'Metal kings banded\ntogether for revenge\nafter adventurers hunted\nthem for EXP.'),
    # one-line names: skill list panes are 150 px at size 13 in 3D12 (~173 font px, runtime
    # fit); 装備時 skills as "Wielding: X +N", 常時 = a plain bonus (PC "Resilience +5")
    5994: ('Always Def +{v:8}', 'Resilience +{v:8}', SQUEEZED),
    6004: ('Mag Mend +{v:8}', 'Magical Mending +{v:8}', SQUEEZED),
    6016: ('Atk Wielding +{v:8}', 'Wielding: Attack +{v:8}', SQUEEZED),
    6069: ('Dodge Wield +{v:8}%', 'Wielding: Dodge +{v:8}%', SQUEEZED),
    6072: ('HP Abs Wld+{v:8}%', 'Wielding: HP Absorption +{v:8}%', SQUEEZED),
    6074: ('Block Worn+{v:8}%', 'Wearing: Block +{v:8}%', SQUEEZED),
    6076: ('Def Worn+{v:8}', 'Wearing: Defence +{v:8}', SQUEEZED),
    6043: ('Minor MP Recovery After Battle', 'Minor MP After Battle', SQUEEZED),
    6051: ('Med MP After Win', 'Moderate MP After Battle', SQUEEZED),
    5819: ('Foe Atk Down a Lot When Attacking', 'Foe Attack Down a Lot When Attacking', SQUEEZED),
    40351: ('Btl Speed', 'Battle Speed', SQUEEZED),
    41919: ('Btl 1', 'Battle 1', SQUEEZED),
    41920: ('Btl 2', 'Battle 2', SQUEEZED),
    41921: ('Btl 3', 'Battle 3', SQUEEZED),
    41922: ('Btl 4', 'Battle 4', SQUEEZED),
    42106: ('Atk (Left):', 'Attack (Left):', SQUEEZED),
    42107: ('Atk (Right):', 'Attack (Right):', SQUEEZED),
    40572: ('Atk (L):', 'Attack (L):', SQUEEZED),
    40573: ('Atk (R):', 'Attack (R):', SQUEEZED),
    43038: ('Atk (R)', 'Attack (R)', SQUEEZED),
    # official PC names (exact matches) the old limits had cut or abbreviated ("Ctlet",
    # "Lettr", articles); one-line panes, runtime fit; ratio noted when narrowed
    3401: ('Beat the Band', 'Fit to Beat the Band', SQUEEZED),  # 0.81
    3402: ('Mager Leagues', 'The Mager Leagues', SQUEEZED),  # 0.83
    3404: ('Femme Fatale', 'The Femmes Fatales', SQUEEZED),  # 0.82
    3414: ('Pandormonium', 'Pandoramonium', SQUEEZED),
    3449: ('Undisputed Champ of Erdrea', 'Undisputed Champion of Erdrea', SQUEEZED),
    5836: ('Chance Spells & Abils Cost 0 MP', 'Chance of Spells & Abilities Costing 0 MP', SQUEEZED),
    6129: ("Smiths n' Sparkly Spots", 'Smiths and Sparkly Spots', SQUEEZED),  # 0.97
    6130: ('Walk on the Wild Side', 'A Walk on the Wild Side', SQUEEZED),  # 0.97
    6131: ('Cactus Ctlet to Die For', 'A Cactus Cutlet to Die For', SQUEEZED),  # 0.90
    6133: ('An Even Lovelier Lettr', 'An Even Lovelier Letter', SQUEEZED),  # 0.99
    6144: ('Agony and Ecstasy', 'The Agony and the Ecstasy', SQUEEZED),  # 0.88
    6151: ('Seeds for the Future', 'Planting Seeds for the Future', SQUEEZED),  # 0.82
    6153: ('Undrstanding Angri-La', 'Understanding Angri-La', SQUEEZED),  # 0.98
    6158: ("Mme Labouche's Lesson", "Madame Labouche's Lesson", SQUEEZED),  # 0.88
    6162: ("Search for Sage's Stone", "Search for the Sage's Stone", SQUEEZED),  # 0.91
    6164: ('Kngdm for Kanaloamari', 'Kingdom for Kanaloamari', SQUEEZED),  # 0.92
    6176: ('Legendary Treasure', 'Legendary Treasure of Legend', SQUEEZED),  # 0.80
    6177: ('Pepped Paladins', 'Perfectly Pepped Paladins', SQUEEZED),  # 0.95
    6813: ('Warrior Rest Inn', "Warrior's Rest Inn", SQUEEZED),  # 0.97
    7290: ('Quadrabash', 'Kaquadrabash', SQUEEZED),
    8201: ('{v:1} Pal', "{v:1}'s Pal", SQUEEZED),
    8409: ('Getaway from Gondolia', 'The Getaway from Gondolia', SQUEEZED),  # 0.89
    8433: ('Princess & Pudding Power Through', 'The Princess and the Pudding Power Through', SQUEEZED),  # 0.87
    8493: ('Truth of Tragedy', 'The Truth of the Tragedy', SQUEEZED),  # 0.98
    8527: ('Mystery of the Mural', 'The Mystery of the Mural', SQUEEZED),  # 0.95
    8538: ('Terrible Tale: Monstrous Mermaid', 'The Terrible Tale of the Monstrous Mermaid', SQUEEZED),  # 0.88
    8605: ("Truth Behind King Carnelian's Cruelty", "The Truth Behind King Carnelian's Cruelty", SQUEEZED),  # 0.91
    8674: ('Mountaintop Monastery', 'The Mountaintop Monastery', SQUEEZED),  # 0.86
    8721: ('Scourge of Seven Seas', 'The Scourge of the Seven Seas', SQUEEZED),  # 0.84
    8740: ("Restless Knight's Identity Revealed", "The Restless Knight's Identity Revealed", SQUEEZED),  # 0.99
    9038: ('Door of Departure', 'The Door of Departure', SQUEEZED),  # 0.95
    9041: ('Champs Sauvage', 'The Champs Sauvage', SQUEEZED),
    9608: ("Perfectionist'\nPearls", "Perfectionist's\nPearls"),
    9756: ('Bleached seashell', 'Sun-bleached seashell', SQUEEZED),  # 0.92
    9891: ('Thunderstrm\nsword', 'Thunderstorm\nsword'),
    9967: ('Thunder\nskean', 'Thunderstorm\nskean'),
    9983: ('Obliterator', 'Obliteratoriser', SQUEEZED),  # 0.97
    10910: ('Enchantress\ngloves', "Enchantress's\ngloves"),
    11195: ('Pearly Pieces', 'My First Pearly Pieces', SQUEEZED),  # 0.90
    11225: ("Emperor's Axe", "The Emperor's New Axe", SQUEEZED),  # 0.82
    11235: ('Fandom of Opera', 'The Fandom of the Opera', SQUEEZED),  # 0.78
    11243: ('Godly Get-Up', 'A Guide to Godly Get-Up', SQUEEZED),  # 0.78
    11317: ('Hero Book Better', "Hero's Book: Better", SQUEEZED),
    11366: ("Stuff o' Legend", 'The Stuff of Legend', SQUEEZED),  # 0.99
    41945: ("Prince's Bedchamber", "The Prince's Bedchamber", SQUEEZED),  # 0.93
    41957: ('Arena Wait Room', 'The Arena Waiting Room', SQUEEZED),  # 0.89
    41980: ('Royal Library - L1', 'The Royal Library - Level 1', SQUEEZED),  # 0.85
    41981: ('Royal Library - L2', 'The Royal Library - Level 2', SQUEEZED),  # 0.84
    41982: ('Royal Library - L3', 'The Royal Library - Level 3', SQUEEZED),  # 0.84
    41983: ('Royal Library - L4', 'The Royal Library - Level 4', SQUEEZED),  # 0.84
    42016: ('Temple of Tranquility', 'The Temple of Tranquility', SQUEEZED),  # 0.85
}

# One occurrence of a shared string worded differently (entry.override): (source id, file,
# layout member or None, loc) -> text. さくせん is "Misc." in the field menu but "Tactics" in
# the battle command window (official PC Menu_Battle_Command_000_006); 0x325424 is the battle
# command table (Fight / Tactics / Flee)
ENTRY_FIXES: dict[tuple, str] = {
    (40317, "/ui/battle_2d.pack", "blyt/win_main_2d.bflyt", 10): "Tactics",
    (40317, "/ui/battle_3d.pack", "blyt/win_main_3d.bflyt", 10): "Tactics",
    (40317, "exefs:.code", None, 0x225424): "Tactics",
    # かまいたち: the skill "Wind Sickles" (battle text) and the monster Cyclown (official PC
    # ZukanDisplayName)
    (2354, "/gamecmn.pack", None, 55716): "{noun}Cyclown",
    # Misc. menu (win_sakusen_main, 108 px items, runtime fit): しばりプレイ in full there (the
    # 80 px dive panes keep "Draconian"); プレゼント受け取り, PC "Claim Special Rewards", was
    # narrowed to 0.72 - the menu says "Special Rewards" (present_3d keeps the full name)
    (7784, "/ui/common_2d.pack", None, 1200): "Draconian Quest",
    (7784, "/ui/common_2way.pack", None, 1200): "Draconian Quest",
    (7784, "/ui/common_3d.pack", None, 1200): "Draconian Quest",
    (40478, "/ui/common_2d.pack", None, 1340): "Special Rewards",
    (40478, "/ui/common_2way.pack", None, 1340): "Special Rewards",
    (40478, "/ui/common_3d.pack", None, 1340): "Special Rewards",
    (40478, "/ui/common_2d.pack", "blyt/win_sakusen_main_2d.bflyt", 18): "Special Rewards",
    (40478, "/ui/common_2way.pack", "blyt/win_sakusen_main_3d.bflyt", 18): "Special Rewards",
    (40478, "/ui/common_3d.pack", "blyt/win_sakusen_main_3d.bflyt", 18): "Special Rewards",
    (40478, "/ui/compactcommon.pack", "blyt/win_sakusen_main_3d.bflyt", 18): "Special Rewards",
}


def plan(project, fixes=FIXES):
    db = project.db()
    gf = gamefont.for_project(project)
    out, problems = [], []
    for sid, (expect, new, *flags) in fixes.items():
        row = db.execute("SELECT text, translation FROM source WHERE id=?", (sid,)).fetchone()
        if row is None:
            problems.append(f"{sid}: no such source")
            continue
        src, cur = row
        if cur == new:
            continue
        if expect is not None and cur != expect:
            problems.append(f"{sid}: changed since ({cur!r}), kept")
            continue
        if SQUEEZED not in flags:
            new = gf.wrap(sid, src, new)
        bad = validate(src, new)
        if bad:
            problems.append(f"{sid}: {bad}")
            continue
        if SQUEEZED not in flags and not gf.fits(sid, src, new):
            lim = gf.get(sid, src)
            problems.append(f"{sid}: does not fit {lim['width']} px x {lim['lines']} lines: {new!r}")
            continue
        if new != cur:
            out.append((sid, cur, new))
    return out, problems


def plan_entries(project, fixes=ENTRY_FIXES):
    db = project.db()
    out, problems = [], []
    for (sid, file, member, loc), new in fixes.items():
        row = db.execute("SELECT e.id, e.override, s.text FROM entry e JOIN source s ON s.id = e.source_id"
                         " WHERE e.source_id=? AND e.file=? AND e.member IS ? AND e.loc=?",
                         (sid, file, member, loc)).fetchone()
        if row is None:
            problems.append(f"{sid} {file} {member} {loc:#x}: no such entry")
            continue
        eid, cur, src = row
        if cur == new:
            continue
        if cur:
            problems.append(f"entry {eid}: has another override ({cur!r}), kept")
            continue
        bad = validate(src, new)
        if bad:
            problems.append(f"entry {eid}: {bad}")
            continue
        out.append((eid, cur, new))
    return out, problems


def apply(project, changes, tag="fixes", entries=()) -> str:
    from .pcnames import _backup
    backup = _backup(project, tag)
    db = project.db()
    now = time.time()
    db.executemany("UPDATE source SET translation=?, status=2, origin='manual', updated=? WHERE id=?",
                   [(new, now, sid) for sid, _, new in changes])
    db.executemany("UPDATE entry SET override=? WHERE id=?", [(new, eid) for eid, _, new in entries])
    db.commit()
    return backup


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", default="workspace")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    project = Project(a.workdir)
    changes, problems = plan(project)
    entries, eprob = plan_entries(project)
    problems += eprob
    for eid, old, new in entries:
        print(f"entry {eid}: override {old!r} -> {new!r}")
    for sid, old, new in changes:
        print(f"{sid}: {old!r}\n   -> {new!r}")
    for p in problems:
        print("PROBLEM", p)
    if a.apply and (changes or entries):
        print("backup:", apply(project, changes, entries=entries))


if __name__ == "__main__":
    main()

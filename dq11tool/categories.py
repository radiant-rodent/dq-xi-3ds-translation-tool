"""Human-readable context categories for extracted strings."""
from __future__ import annotations

# BXON type name -> category
BXON_TYPES = {
    "CharacterIEData": "NPC Dialogue",
    "TalkPartyContent2": "Party Chat",
    "TalkParty2DOverwrit": "Party Chat",
    "TextBlock": "Event & Cutscene Dialogue",
    "BattleText": "Battle Text",
    "BattleMessage": "Battle Text",
    "EnemySpecialAttack": "Battle Text",
    "ItemParam": "Items & Equipment",
    "ItemFailureMessage": "Items & Equipment",
    "ItemAdditionalEffec": "Items & Equipment",
    "ItemFukubikiParam": "Items & Equipment",
    "SmithArtParam": "Forge",
    "SmithMaterialParam": "Forge",
    "PassiveSkillParam": "Skills & Character Builder",
    "PanelsHero": "Skills & Character Builder",
    "PanelsCamu": "Skills & Character Builder",
    "PanelsVeronica": "Skills & Character Builder",
    "PanelsSena": "Skills & Character Builder",
    "PanelsEXSena": "Skills & Character Builder",
    "PanelsSylvia": "Skills & Character Builder",
    "PanelsMartina": "Skills & Character Builder",
    "PanelsRou": "Skills & Character Builder",
    "PanelsGraig": "Skills & Character Builder",
    "EnemyBaseParam": "Monsters",
    "EnemyBookParam": "Monsters",
    "UIMessage": "Menus & System Messages",
    "UIText": "Menus & System Messages",
    "UIDetailRecordParam": "Menus & System Messages",
    "ProgramParam": "Menus & System Messages",
    "SavePointMsg": "Menus & System Messages",
    "SavePointParam": "Menus & System Messages",
    "UIGuideParam": "Story Guide & Summaries",
    "UIStoryFlagRelation": "Story Guide & Summaries",
    "CSChapterParam": "Story Guide & Summaries",
    "ScenarioIDParam": "Story Guide & Summaries",
    "SerenHintMessage": "Story Guide & Summaries",
    "UITutorialParam": "Tutorials",
    "ShopMsg": "Shops & Services",
    "ShopTypeText": "Shops & Services",
    "InnMsg": "Shops & Services",
    "ChurchMsg": "Shops & Services",
    "BankMsg": "Shops & Services",
    "JewelShopMsg": "Shops & Services",
    "CasinoMsg": "Casino & Minigames",
    "PokerHandParam": "Casino & Minigames",
    "HorseRaceRankParam": "Casino & Minigames",
    "MiniGameCategoryPar": "Casino & Minigames",
    "NPCJockeyVisualPara": "Casino & Minigames",
    "TrainingTrialParam": "Casino & Minigames",
    "QuestUIParam": "Quests",
    "QuestIDParam": "Quests",
    "AchievementParam": "Achievements",
    "UIMapParamSet": "Places & Maps",
    "LevelAreaCommonPara": "Places & Maps",
    "RuraPointParam": "Places & Maps",
    "RevivalPointParam": "Places & Maps",
    "PastWorldParam": "Places & Maps",
    "PastAltarParam": "Places & Maps",
    "UIAnotherNameParam": "Names & Titles",
    "PlayerInitializePar": "Names & Titles",
    "SpiritNameTableRow": "Names & Titles",
    "SpiritPresentParam": "Names & Titles",
    "SpiritTalkParam": "StreetPass & Spirits",
    "DummyPlayerName": "StreetPass & Spirits",
    "DummyPlayerMessage": "StreetPass & Spirits",
    "SpecialRevivalSpell": "Misc",
    "CreditTextData": "Credits",
}

EXCLUDED_BXON_TYPES = {"Level3DBin"}  # binary level data, no real text

CATEGORY_ORDER = [
    "Event & Cutscene Dialogue", "NPC Dialogue", "Party Chat", "Battle Text",
    "Items & Equipment", "Skills & Character Builder", "Monsters", "Menus & System Messages",
    "UI Layouts", "Hardcoded (Executable)", "Story Guide & Summaries", "Tutorials", "Quests",
    "Shops & Services", "Casino & Minigames", "Forge", "Places & Maps", "Names & Titles",
    "Achievements", "StreetPass & Spirits", "Credits", "Misc", "Other Data",
    "Script Labels (internal)",
]


def bxon_category(type_name: str, file_path: str, entry_name: str) -> str:
    if type_name == "TextBlock":
        if "/scenario/cs/" in file_path:
            return "Event & Cutscene Dialogue"
        return "Event & Cutscene Dialogue"
    if type_name == "CharacterIEData" and entry_name.startswith("book"):
        return "NPC Dialogue"
    return BXON_TYPES.get(type_name, "Other Data")


def bxon_context(type_name: str, file_path: str, entry_name: str) -> str:
    """Short descriptive context shown next to the string."""
    base = file_path.rsplit("/", 1)[-1].replace(".pack", "")
    extra = {
        "npc_ie.bxon": "NPC talk",
        "book_ie.bxon": "bookshelf / sign",
        "cs_text.bxon": "cutscene",
        "talk_content.bxon": "party chat",
        "script_message.bxon": "event message",
    }.get(entry_name, "")
    s = f"{type_name} · {base}/{entry_name}"
    return f"{s} ({extra})" if extra else s

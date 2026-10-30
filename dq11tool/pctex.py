"""Official English artwork from the PC (Steam) version, fitted into 3DS textures.

The PC version has English textures for most of the 3DS version's Japanese lettering (casino
results, race signs, ...). They are read from the player's own Steam install at export time (never
stored in the repository), trimmed, scaled down with premultiplied alpha (no dark fringes) and
placed where the Japanese lettering was. Words the 3DS draws one letter per pane are composed from
the PC's own letter textures.
"""
from __future__ import annotations

import io
import os
import re
import struct

import numpy as np

STEAM_DIR = r"C:\Program Files (x86)\Steam\steamapps\common\DRAGON QUEST XI"
_FOURCC = {"PF_DXT1": b"DXT1", "PF_DXT3": b"DXT3", "PF_DXT5": b"DXT5"}


def _dds(w: int, h: int, fourcc: bytes, data: bytes) -> bytes:
    hdr = struct.pack("<4sI", b"DDS ", 124) + struct.pack("<6I", 0x1007 | 0x80000, h, w, len(data), 0, 1)
    hdr += b"\0" * 44 + struct.pack("<2I4s5I", 32, 4, fourcc, 0, 0, 0, 0, 0) + struct.pack("<5I", 0x1000, 0, 0, 0, 0)
    return hdr + data


class PcTextures:
    """Texture2D assets of the PC version's Data.pak, decoded to RGBA (largest mip)."""

    def __init__(self, steam_dir: str | None = None):
        from .uepak import DQXI_KEY, Pak
        root = steam_dir or os.environ.get("DQ11_STEAM_DIR") or STEAM_DIR
        self.pak = Pak(os.path.join(root, "Game", "Content", "Paks", "Data.pak"), xor_key=DQXI_KEY)
        self._cache: dict[str, np.ndarray] = {}

    def get(self, asset: str) -> np.ndarray:
        """asset: path below JackGame/Content without extension, e.g.
        'MiniGame_modern/UmaRace/Textures/T_Hud_UmaRace_Rank_000_Jp'."""
        if asset not in self._cache:
            self._cache[asset] = self._decode("JackGame/Content/" + asset + ".uexp")
        return self._cache[asset]

    def _decode(self, name: str) -> np.ndarray:
        from PIL import Image
        d = self.pak.read(name)
        m = re.search(rb"PF_[A-Za-z0-9_]+\0", d)
        fmt = m.group()[:-1].decode()
        w, h = struct.unpack_from("<2i", d, m.start() - 16)
        p = m.end() + 12                                   # first mip, mip count, bCooked
        flags, _, size, off = struct.unpack_from("<IiiQ", d, p)
        p += 20
        data = d[p:p + size] if flags & 0x40 else self.pak.read(name[:-5] + ".ubulk")[off:off + size]
        if fmt in _FOURCC:
            im = Image.open(io.BytesIO(_dds(w, h, _FOURCC[fmt], data)))
        elif fmt == "PF_B8G8R8A8":
            im = Image.frombytes("RGBA", (w, h), data, "raw", "BGRA")
        else:
            raise ValueError(f"{name}: unsupported pixel format {fmt}")
        return np.asarray(im.convert("RGBA"))


# ---------------------------------------------------------------------- image helpers

def trim(rgba: np.ndarray, rows: bool = True, cut: int = 8) -> np.ndarray:
    """Without the transparent border (rows=False: columns only, the baseline stays)."""
    a = rgba[..., 3] > cut
    cols = np.where(a.any(0))[0]
    out = rgba[:, cols[0]:cols[-1] + 1]
    if rows:
        r = np.where(a.any(1))[0]
        out = out[r[0]:r[-1] + 1]
    return out


def resize(rgba: np.ndarray, w: int, h: int) -> np.ndarray:
    """Scaled with premultiplied alpha (Lanczos): no dark or light fringes at the edges."""
    from PIL import Image
    f = rgba.astype(np.float32) / 255
    pre = np.concatenate([f[..., :3] * f[..., 3:], f[..., 3:]], -1)
    chans = [np.asarray(Image.fromarray(pre[..., c]).resize((w, h), Image.LANCZOS)) for c in range(4)]
    out = np.clip(np.stack(chans, -1), 0, 1)
    a = out[..., 3:]
    rgb = np.where(a > 1e-4, out[..., :3] / np.maximum(a, 1e-4), 0)
    return (np.concatenate([np.clip(rgb, 0, 1), a], -1) * 255).round().astype(np.uint8)


def fit(rgba: np.ndarray, w: int, h: int, pad: int = 1) -> np.ndarray:
    """Trimmed and scaled to fit w x h (aspect kept), centred on a transparent w x h image."""
    src = trim(rgba)
    s = min((w - 2 * pad) / src.shape[1], (h - 2 * pad) / src.shape[0])
    nw, nh = max(1, round(src.shape[1] * s)), max(1, round(src.shape[0] * s))
    img = resize(src, nw, nh)
    out = np.zeros((h, w, 4), np.uint8)
    y, x = (h - nh) // 2, (w - nw) // 2
    out[y:y + nh, x:x + nw] = img
    return out


def compose(letters: list, gap: float = -0.06, space: float = 0.35) -> np.ndarray:
    """A word from letter textures of one set (same canvas height: their baseline is kept).
    None is a space; gap and space are fractions of the canvas height."""
    h = max(l.shape[0] for l in letters if l is not None)
    parts, x = [], 0
    for l in letters:
        if l is None:
            x += round(space * h)
            continue
        t = trim(l, rows=False)
        parts.append((x, t))
        x += t.shape[1] + round(gap * h)
    w = max(px + t.shape[1] for px, t in parts)
    out = np.zeros((h, w, 4), np.float32)
    for px, t in parts:                         # later letters over earlier ones
        a = t[..., 3:].astype(np.float32) / 255
        dst = out[:t.shape[0], px:px + t.shape[1]]
        dst[..., :3] = t[..., :3] * a + dst[..., :3] * (1 - a)
        dst[..., 3:] = np.maximum(dst[..., 3:], t[..., 3:])
    return out.round().astype(np.uint8)


def spell(pc: PcTextures, letter_set: dict, text: str, **kw) -> np.ndarray:
    """text in one of the PC's letter sets (letter -> asset; ' ' is a space)."""
    return compose([None if c == " " else pc.get(letter_set[c]) for c in text], **kw)


# ---------------------------------------------------------------------- the official artwork

_PR = "MiniGame_modern/Poker/Textures/Result/T_Hud_Poker_Result_"
_UR = "MiniGame_modern/UmaRace/Textures/"
_RL = "MiniGame_modern/Roulette/Textures/Rogo/T_Hud_Roulette_"
_SL = "MiniGame_modern/Slot/Textures/Rogo/T_Hud_Slot_"
_JP = "MiniGame_modern/Common/Textures/T_Hud_MiniGame_Jackpot_"


def _set(prefix: str, letters: str, first: int = 0) -> dict:
    return {c: f"{prefix}{first + i:03d}_Jp" for i, c in enumerate(letters)}


POKER_ORANGE = _set(_PR, "FHJKPRSTadefghilnorstuvwy!")          # the hands
POKER_PURPLE = _set(_PR, "DSabdeklotu!", 100)                   # won the double-up
POKER_GREEN = _set(_PR, "Sanp!", 200)                           # double-up tie
RACE_GOAL = _set(_UR + "Logo/T_Hud_UmaRace_Goal_", "NiceWork!")
ROULETTE_WIN = _set(_RL + "WinFont_", "YouWin!")
FREE_SPIN = {"F": _SL + "FreeSpin_000_Jp", "r": _SL + "FreeSpin_001_Jp", "e": _SL + "FreeSpin_002_Jp",
             "S": _SL + "FreeSpin_003_Jp", "p": _SL + "FreeSpin_004_Jp", "i": _SL + "FreeSpin_005_Jp",
             "n": _SL + "FreeSpin_006_Jp", "s": _SL + "FreeSpin_007_Jp", "!": _SL + "FreeSpin_008_Jp"}
METAL_SPIN = _set(_SL + "MetalSpin_", "Metalod!")
JACKPOT = _set(_JP, "JACKPOT")


def official(pc: PcTextures) -> dict:
    """What the official artwork replaces (texlabel.patch_pack applies it):
    'whole':  texture -> [(rect, image, height[, align])]  (placed over the old lettering; height
              None: as tall as it, <= 1: that fraction of it)
    'spread': layout -> (cells [(texture, rect)], panes, twin pane or None, image, height):
              the image cut across the panes (spread evenly over the word's width); the twin
              (a second pane showing a cell already used) moves onto that cell's pane
    'letters': texture -> image (cut across texlabel.LETTER_WORDS' cells and panes)
    'lines':  layout -> [image per texlabel.LINE_LAYOUTS line]"""
    g = pc.get
    whole = {
        "timg/racing_start_start.bflim": [((0, 0, 256, 64), g(_UR + "Logo/T_Hud_UmaRace_Start_All_Jp"), None)],
        "timg/racing_last.bflim": [((0, 0, 256, 32), g(_UR + "Logo/T_Hud_UmaRace_Final_All_Jp"), None)],
        "timg/racing_best_3.bflim": [((0, 0, 256, 64), g(_UR + "Logo/T_Hud_UmaRace_BestTime_Jp"), None)],
        "timg/jackpot_txt.bflim": [((0, 0, 256, 32), g("MiniGame_modern/Common/Textures/T_Hud_Minigame_Jackpot_All_000_Jp"), None)],
    }
    for i in range(4):
        # the badge sits in the screen corner: 1st is wider than 1位, so smaller and kept to
        # the old badge's right edge (it grows towards the screen centre)
        whole[f"timg/racing_rank{i + 1}.bflim"] = [((0, 0, 128, 64), g(f"{_UR}T_Hud_UmaRace_Rank_{i:03d}_Jp"), 0.8, "right")]
    # the race gauge's label (Ⓨダッシュ; the PC calls the gauge Stamina): the text right of the
    # Ⓨ icon; the bar and the glow follow it (texlabel.COPIED_LABELS, GLOW_LABELS)
    whole["timg/racing_gauge_base2.bflim"] = [((18, 0, 64, 16), g(_UR + "T_Hud_UmaRace_Stamina_000_Jp"), 10)]
    roulette = "timg/roulette_txt_start.bflim"
    whole[roulette] = [((0, 96, 176, 128), g(_RL + "SpinStartfont_001_Jp"), None)]           # スピンスタート!
    spread = {
        "blyt/menu_roulette_txt_3d.bflyt": [
            ([(roulette, (48 * i, 0, 48 * i + 48, 48)) for i in range(3)] + [(roulette, (144, 0, 176, 48))],
             ["roulette_txt_atari1", "roulette_txt_atari2", "roulette_txt_atari3", "roulette_txt_atari4"],
             ("roulette_txt_atari5", "roulette_txt_atari4"), spell(pc, ROULETTE_WIN, "You Win!"), None),
            ([(roulette, (48 * i, 48, 48 * i + 48, 96)) for i in range(3)],
             ["roulette_txt_hazure1", "roulette_txt_hazure2", "roulette_txt_hazure3"], None,
             g(_RL + "LoseFont_000_Jp"), None),
        ],
        "blyt/menu_stamp_itemget_3d.bflyt": [
            ([("timg/stamp_txt_3d.bflim", (32 * i, 0, 32 * i + 32, 32)) for i in range(8)],
             [f"txt{i}" for i in range(1, 9)], ("txt9", "txt8"), g("UIs_common/Texture/T_Hud_Stamp_Text_000_Jp"), None),
        ],
        "blyt/menu_casino_jackpot_3d.bflyt": [
            ([("timg/jackpot_txt2.bflim", (42 * i, 0, 42 * i + 42, 48)) for i in range(6)],
             ["txt1", "txt2", "txt3", "txt4", "txt5", "txt7"], ("txt6", "txt3"), spell(pc, JACKPOT, "JACKPOT"), None),
        ],
        "blyt/common_3d_up_racing.bflyt": [
            ([(f"timg/racing_goal_{i}.bflim", (0, 0, 64, 64)) for i in range(1, 5)],
             ["racing_goal_1", "racing_goal_2", "racing_goal_3", "racing_goal_4"], ("racing_goal_5", "racing_goal_4"),
             spell(pc, RACE_GOAL, "Nice Work!"), None),
        ],
    }
    # the slot's bonus words (one letter per pane, menu_casino_*: same geometry as texlabel)
    letters = {
        "timg/free_font.bflim": spell(pc, FREE_SPIN, "Free Spins!"),
        "timg/metal_font.bflim": spell(pc, METAL_SPIN, "Metal Mode!"),
    }
    orange = {t: spell(pc, POKER_ORANGE, t) for t in (
        "Two Pair!", "Three of a Kind!", "Straight!", "Flush!", "Full House!", "Four of a Kind!", "Straight",
        "Five of a Kind!", "Royal", "Royal Jelly", "Flush!")}
    # in texlabel.LINE_LAYOUTS order (Straight Flush, Royal Flush, Royal Jelly Flush: two lines each)
    lines = {"blyt/menu_poker_txt_3d.bflyt": [
        orange["Two Pair!"], orange["Three of a Kind!"], orange["Straight!"], orange["Flush!"],
        orange["Full House!"], orange["Four of a Kind!"], orange["Straight"], orange["Flush!"],
        orange["Five of a Kind!"], orange["Royal"], orange["Flush!"], orange["Royal Jelly"], orange["Flush!"],
        spell(pc, POKER_PURPLE, "Stake Doubled!"), g(_PR + "300_Jp"), spell(pc, POKER_GREEN, "Snap!"),
    ]}
    return {"whole": whole, "spread": spread, "letters": letters, "lines": lines}

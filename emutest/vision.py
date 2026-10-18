"""Muse vision (OpenCode Go subscription, muse-spark-1.3-contributor): locate things on a
screenshot. ~15 s per call, so it is used only at key points (where is the door / the NPC);
step-by-step routing is jevnav's job.

    locate(path, "the door of the shop building") -> (map_x, map_y, confidence) or None
Coordinates are full-screenshot pixels (400x480; bottom-screen map x 40..360, y 240..480,
hero at about (200, 360) outdoors).
"""
import base64
import json
import os
import re

import az

MODEL = "muse-spark-1.3-contributor"
BASE = "https://opencode.ai/zen/go/v1"
_client = None


def _cli():
    global _client
    if _client is None:
        from dq11tool import mt
        from dq11tool.project import Project
        p = Project(os.path.join(az.REPO, "workspace"))
        _client = mt.OpenAICompat(BASE, mt.get_key(p, "openai") or "", MODEL)
    return _client


def ask(path: str, prompt: str, budget: int = 1500) -> str:
    c = _cli()
    img = base64.b64encode(open(path, "rb").read()).decode()
    body = {"model": MODEL, "max_output_tokens": budget, "reasoning": {"effort": "minimal"},
            "input": [{"role": "user", "content": [
                {"type": "input_text", "text": prompt},
                {"type": "input_image", "image_url": "data:image/png;base64," + img}]}]}
    r = c.client.post(c.base + "/responses", json=body, timeout=120)
    r.raise_for_status()
    return c._content(r.json())


def locate(path: str, what: str):
    txt = ask(path, "3DS game screenshot (400x480): top half = 3D view, bottom half = 2D map "
                    "(map area x 40..360, y 240..480; the hero is the figure at about x 200, y 360). "
                    f"Find {what} on the bottom map. Answer only JSON: "
                    '{"map_x": int, "map_y": int, "confidence": 0-1} (confidence 0 if not visible).')
    m = re.search(r"\{.*\}", txt, re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
        if float(d.get("confidence", 0)) <= 0.1:
            return None
        return float(d["map_x"]), float(d["map_y"]), float(d.get("confidence", 0))
    except (ValueError, KeyError):
        return None

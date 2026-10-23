"""Jev (OpenCode "System One" decision model): free, ~1 s per call, text state -> typed answers.
https://opencode.ai/v2/docs/console/models/#jev . The key is the OpenCode key saved in the
translation tool (never printed)."""
import os
import time

import httpx

import az

JEV_URL = "https://opencode.ai/inference/v1/systemone"
JEV_MODEL = "jev-1.13-free"


def jev_key() -> str:
    from dq11tool import mt
    from dq11tool.project import Project
    return mt.get_key(Project(os.path.join(az.REPO, "workspace")), "openai") or ""


def jev(state: str, questions: dict, key: str) -> dict:
    """questions: {id: {"type": "choice"|"noul"|"score", "instructions": ..., "criteria": ...}}
    -> {id: {"choice": ..., "probabilities": {...}} | {"noul": p} | ...}"""
    for attempt in range(8):
        try:
            r = httpx.post(JEV_URL, json={"model": JEV_MODEL, "state": state, "questions": questions},
                           headers={"Authorization": "Bearer " + key}, timeout=60)
        except httpx.HTTPError:
            time.sleep(2 ** attempt)
            continue
        if r.status_code == 429 or r.status_code >= 500:   # the free tier is rate limited
            wait = float(r.headers.get("retry-after") or 0) or min(30, 2 ** attempt)
            if wait > 60:          # quota used up (FreeUsageLimitError): fail now, do not sleep for hours
                raise RuntimeError(f"Jev quota exhausted, retry after {wait / 3600:.1f} h: {r.text[:120]}")
            time.sleep(wait)
            continue
        r.raise_for_status()
        return r.json()["answers"]
    raise RuntimeError("Jev kept failing (rate limit)")

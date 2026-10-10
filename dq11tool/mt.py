"""Machine translation helpers that produce *draft* translations (status 1) for review.

Backends:
  openai  - any OpenAI-compatible chat API (OpenCode Zen / OpenCode Go, DeepSeek, OpenRouter,
            local servers...). The user supplies base URL, API key and model.
  claude  - Anthropic API (credentials from the environment / `ant auth login`, or a key
            entered in the UI)
  google  - Google Translate via deep-translator (no key, lower quality)

Control tags ({v:XX}) are preserved; ruby is reduced to its base text; line breaks and
{CR} pauses are re-flowed afterwards with the game font metrics.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from .export import validate
from . import boxfit
from .pcmatch import has_markup
from .metrics import FontSet, fold_to_font, wrap
from .project import Project

CLAUDE_MODEL = "claude-opus-5-5"

PRESETS = [
    {"id": "opencode-zen", "name": "OpenCode Zen", "base_url": "https://opencode.ai/zen/v1",
     "key_hint": "API key from opencode.ai/auth"},
    {"id": "opencode-go", "name": "OpenCode Go (subscription)", "base_url": "https://opencode.ai/zen/go/v1",
     "key_hint": "API key from opencode.ai/auth"},
    {"id": "deepseek", "name": "DeepSeek API", "base_url": "https://api.deepseek.com/v1", "key_hint": "platform.deepseek.com"},
    {"id": "openrouter", "name": "OpenRouter", "base_url": "https://openrouter.ai/api/v1", "key_hint": "openrouter.ai/keys"},
    {"id": "custom", "name": "Custom (OpenAI-compatible)", "base_url": "", "key_hint": ""},
]

DEFAULT_SETTINGS = {
    "backend": "openai",
    "preset": "opencode-zen",
    "base_url": "https://opencode.ai/zen/v1",
    "model": "",
    "concurrency": 4,
    "batch_size": 30,
    "remember_key": False,
}

DEFAULT_GLOSSARY = [
    {"jp": "勇者", "en": "Luminary"},
    {"jp": "カミュ", "en": "Erik"},
    {"jp": "ベロニカ", "en": "Veronica"},
    {"jp": "セーニャ", "en": "Serena"},
    {"jp": "シルビア", "en": "Sylvando"},
    {"jp": "マルティナ", "en": "Jade"},
    {"jp": "ロウ", "en": "Rab"},
    {"jp": "グレイグ", "en": "Hendrik"},
    {"jp": "ホメロス", "en": "Jasper"},
    {"jp": "エマ", "en": "Gemma"},
    {"jp": "デルカダール", "en": "Heliodor"},
    {"jp": "イシの村", "en": "Cobblestone"},
    {"jp": "ユグノア", "en": "Dundrasil"},
    {"jp": "命の大樹", "en": "Yggdrasil"},
    {"jp": "ウルノーガ", "en": "Mordegon"},
    {"jp": "スライム", "en": "slime"},
    {"jp": "ゴールド", "en": "gold"},
    {"jp": "やくそう", "en": "medicinal herb"},
    {"jp": "ホイミ", "en": "Heal"},
    {"jp": "ベホイミ", "en": "Midheal"},
    {"jp": "メラ", "en": "Frizz"},
    {"jp": "ギラ", "en": "Sizz"},
    {"jp": "ヒャド", "en": "Crack"},
    {"jp": "バギ", "en": "Woosh"},
    {"jp": "イオ", "en": "Bang"},
    {"jp": "デイン", "en": "Zap"},
    {"jp": "ルーラ", "en": "Zoom"},
    {"jp": "ザオラル", "en": "Zing"},
    {"jp": "キアリー", "en": "Squelch"},
    {"jp": "冒険の書", "en": "Adventure Log"},
]

SYSTEM_PROMPT = """You translate in-game text of the Japanese Nintendo 3DS version of Dragon Quest XI into natural, \
playful English in the style of the series' English releases. You receive a JSON list of strings, each with an id, \
the Japanese text and a short context (where the text appears in the game). Consecutive items are usually \
consecutive lines of the same conversation, so keep tone and references consistent.

Rules:
- Translate every id exactly once.
- Keep every tag of the form {v:XX} exactly as written; it is replaced at runtime by a name, item or number. \
You may move it within the sentence for natural word order.
- Do not output line breaks or {CR} tags; line wrapping and pauses are added automatically afterwards.
- Furigana tags {ruby:漢字|かな} only show readings: translate the base text, never output ruby tags.
- A leading 「 only marks spoken dialogue in Japanese: do NOT add quotation marks around dialogue in \
English (the speaker is shown by the window), and do not indent lines.
- Use only plain ASCII characters: straight quotes, '...' for ellipses, no accented letters, no em dashes.
- Every item has "max_chars": the space the Japanese text takes on screen, expressed as English characters (spaces and punctuation count). Never exceed it - text that is too long runs off the window or box. When space is tight, rephrase more compactly, drop filler words, or use the series' usual abbreviations (HP, MP, Atk, Def, Agi, Lv, Exp, G for gold, "&" for "and"). Names and menu labels must fit as a whole.
- An item with "previous" was already translated but did not fit: write a shorter version of that translation (same meaning and tone) within the new "max_chars".
- "required_tags" lists the {v:XX} tags of an item. Every one of them MUST appear in the English \
(the game inserts a name, item or number there), and no other {v:XX} tag may be added. If the official \
reference leaves a name out, work it back in naturally (e.g. as a form of address). An item with \
"tag_error" was rejected for exactly that reason: fix it.
- Some items have "reference": official English lines of the PC release whose Japanese is similar (with a similarity score). They are the canonical translation: reuse their wording, names, spell and item terms wherever the Japanese is the same, and only adapt the parts that differ. Stay within "max_chars".
- Use the glossary terms whenever the Japanese term appears."""

JSON_INSTRUCTION = 'Respond with JSON only, in exactly this shape: {"translations": [{"id": 123, "english": "..."}]}'

_lock = threading.Lock()
_VTAG = re.compile(r"\{v:[0-9A-Fa-f]+\}")
TAG_RETRIES = 2
SHORTEN_ROUNDS = 2
_session_keys: dict[str, str] = {}      # backend -> key (memory only)


# ---------------------------------------------------------------------- settings & secrets

def _settings_path(project: Project) -> str:
    return os.path.join(project.workdir, "mt_settings.json")


def _secrets_path(project: Project) -> str:
    return os.path.join(project.workdir, "secrets.json")


def load_settings(project: Project) -> dict:
    s = dict(DEFAULT_SETTINGS)
    try:
        with open(_settings_path(project), encoding="utf-8") as fp:
            s.update(json.load(fp))
    except (OSError, ValueError):
        pass
    s["has_key"] = bool(get_key(project, s["backend"]))
    return s


def save_settings(project: Project, data: dict):
    s = load_settings(project)
    for k in DEFAULT_SETTINGS:
        if k in data:
            s[k] = data[k]
    s["concurrency"] = max(1, min(int(s["concurrency"]), 256))  # sanity bound only; providers limit by budget, 429s are retried
    s["batch_size"] = max(1, min(int(s["batch_size"]), 100))
    s.pop("has_key", None)
    with open(_settings_path(project), "w", encoding="utf-8") as fp:
        json.dump(s, fp, indent=2)
    key = (data.get("api_key") or "").strip()
    if key:
        set_key(project, s["backend"], key, remember=bool(s["remember_key"]))
    elif not s["remember_key"]:
        _forget_stored_key(project, s["backend"])
    return load_settings(project)


def set_key(project: Project, backend: str, key: str, remember: bool):
    with _lock:
        _session_keys[backend] = key
    secrets = _read_secrets(project)
    if remember:
        secrets[backend] = key
    else:
        secrets.pop(backend, None)
    _write_secrets(project, secrets)


def _forget_stored_key(project: Project, backend: str):
    secrets = _read_secrets(project)
    if backend in secrets:
        secrets.pop(backend)
        _write_secrets(project, secrets)


def get_key(project: Project, backend: str) -> str | None:
    with _lock:
        if backend in _session_keys:
            return _session_keys[backend]
    return _read_secrets(project).get(backend)


def _read_secrets(project: Project) -> dict:
    try:
        with open(_secrets_path(project), encoding="utf-8") as fp:
            return json.load(fp)
    except (OSError, ValueError):
        return {}


def _write_secrets(project: Project, secrets: dict):
    path = _secrets_path(project)
    if not secrets:
        if os.path.exists(path):
            os.remove(path)
        return
    with open(path, "w", encoding="utf-8") as fp:
        json.dump(secrets, fp)


def available_backends(project: Project) -> list[dict]:
    out = [{"id": "openai", "name": "OpenAI-compatible API (OpenCode, DeepSeek, OpenRouter…)", "ready": True}]
    try:
        import anthropic  # noqa: F401
        out.append({"id": "claude", "name": f"Claude ({CLAUDE_MODEL})", "ready": True})
    except ImportError:
        pass
    out.append({"id": "google", "name": "Google Translate (free, lower quality)", "ready": True})
    return out


def glossary(project: Project) -> list[dict]:
    r = project.db().execute("SELECT value FROM meta WHERE key='glossary'").fetchone()
    return json.loads(r["value"]) if r else DEFAULT_GLOSSARY


# ---------------------------------------------------------------------- selection

HIDDEN = ("UI Placeholders", "Debug (not shown in game)", "Script Labels (internal)")


def _select(project: Project, ids, scope, overwrite: bool, limit: int | None = None):
    db = project.db()
    if ids:
        q = "SELECT s.id, s.text FROM source s WHERE s.id IN (%s)" % ",".join("?" * len(ids))
        params = list(ids)
        if not overwrite:
            q += " AND s.status = 0"
        rows = db.execute(q, params).fetchall()
    else:
        scope = scope or {}
        too_wide = bool(scope.get("include_too_wide")) and not overwrite
        # with "redo" also official PC lines that do not fit their 3DS window: they are condensed
        where = [] if overwrite else ["(s.status <= 1 OR (s.status = 2 AND s.origin = 'pc'))" if too_wide
                                      else "s.status = 0"]
        params = []
        if scope.get("category"):
            where.append("s.id IN (SELECT source_id FROM entry WHERE category = ?)")
            params.append(scope["category"])
        else:
            where.append("s.id IN (SELECT source_id FROM entry WHERE category NOT IN (%s))" % ",".join("?" * len(HIDDEN)))
            params.extend(HIDDEN)
        sql = ("SELECT s.id, s.text, s.translation, s.status, s.origin FROM source s WHERE " + " AND ".join(where) +
               " ORDER BY (SELECT MIN(id) FROM entry WHERE source_id = s.id)")
        if limit:
            sql += f" LIMIT {int(limit)}"
        rows = db.execute(sql, params).fetchall()
        if too_wide:   # drafts are redone only when something is wrong with them
            bi = _boxes(project)
            cg = compile_glossary(glossary(project))
            rows = [r for r in rows if r["status"] == 0 or not r["translation"]
                    or needs_redo(bi, r["id"], r["text"], r["translation"], cg)]
    bi = _boxes(project)
    out = []
    for r in rows:
        e = db.execute("SELECT category, context FROM entry WHERE source_id=? ORDER BY id LIMIT 1",
                       (r["id"],)).fetchone()
        it = {"id": r["id"], "text": r["text"], "context": f"{e['category']} / {e['context']}",
              "max_chars": bi.get(r["id"], r["text"])["max_chars"]}
        keys = r.keys()
        if "origin" in keys and r["origin"] == "pc" and r["status"] == 2 and r["translation"]:
            # an official line that is too long for the 3DS window: condense it, keep its wording
            it["previous"] = r["translation"].replace("{CR}", "").replace("\n", " ")
        out.append(it)
    # official PC lines similar to each string (empty until the PC script is imported)
    from .pcref import context_for
    refs = context_for(project, [it["id"] for it in out])
    for it in out:
        if refs.get(it["id"]):
            it["reference"] = refs[it["id"]]
    return out


def needs_redo(bi: boxfit.BoxIndex, sid: int, source: str, translation: str, gloss=None) -> bool:
    """A draft is redone when it does not fit its window, still contains PC markup the 3DS
    line cannot express (<pc> ...), lost / invented a {v:..} placeholder, or renders a
    glossary name differently from the official one."""
    # glossary names are not a redo reason any more: after one pass with the official names the
    # remaining differences were context forms the official script uses too ("hero" for 勇者,
    # "Mordegon" for 魔王ウルノーガ), so another pass only cost money. `gloss` is kept for callers.
    return (not bi.fits(sid, source, translation) or has_markup(translation)
            or bool(validate(source, translation)))


_KATA = "゠-ヿ"


def compile_glossary(gloss: list[dict]) -> list[tuple[re.Pattern, str, str]]:
    """Glossary entries as (Japanese pattern, jp, en), longest first; katakana names only
    match as whole words (ロウ must not hit ロウソク)."""
    out = []
    for g in sorted(gloss, key=lambda g: -len(g.get("jp") or "")):
        j, e = (g.get("jp") or "").strip(), (g.get("en") or "").strip()
        if len(j) < 2 or not e:
            continue
        pre = "(?<![" + _KATA + "])" if re.match("[" + _KATA + "]", j) else ""
        post = "(?![" + _KATA + "])" if re.search("[" + _KATA + "]$", j) else ""
        out.append((re.compile(pre + re.escape(j) + post), j, e))
    return out


def glossary_misses(compiled, source: str, translation: str) -> list[str]:
    """Glossary terms present in the Japanese whose English name the translation lacks."""
    from .pcref import normalize_jp
    s, t = normalize_jp(source), translation.lower()
    seen, miss = [], []
    for pat, j, e in compiled:
        if pat.search(s) and not any(j in x for x in seen):
            seen.append(j)
            el = e.lower()
            if el not in t and el.rstrip("s") not in t:
                miss.append(f"{j} = {e}")
    return miss


def count_too_wide(project: Project, category: str | None = None) -> int:
    """Number of machine drafts that need redoing (see needs_redo), in a category or all visible ones."""
    db = project.db()
    if category:
        cond, params = "category = ?", [category]
    else:
        cond, params = "category NOT IN (%s)" % ",".join("?" * len(HIDDEN)), list(HIDDEN)
    rows = db.execute("SELECT id, text, translation FROM source WHERE (status = 1 OR (status = 2 AND origin = 'pc'))"
                      f" AND translation IS NOT NULL AND id IN (SELECT source_id FROM entry WHERE {cond})",
                      params).fetchall()
    bi = _boxes(project)
    cg = compile_glossary(glossary(project))
    return sum(1 for r in rows if needs_redo(bi, r["id"], r["text"], r["translation"], cg))


def _postprocess(project: Project, source: str, english: str, sid: int | None = None) -> str:
    english = english.strip()
    english = re.sub(r"\{ruby:([^|{}]*)\|[^{}]*\}", r"\1", english)
    english = (english.replace("“", '"').replace("”", '"').replace("‘", "'")
               .replace("’", "'").replace("…", "...").replace("—", "-").replace("–", "-")
               .replace(" ", " "))
    english = strip_dialogue_quotes(source, english)
    # letters/symbols the game font lacks: é -> e (Japanese font only), æ -> ae, ♥ -> ♪ (before wrapping)
    english = fold_to_font(english, _font(project))
    box = _boxes(project).get(sid, source) if sid is not None else None
    if "\n" in source or (box and box["lines"] > 1):
        # wrap to the width of the window the text appears in, not of the Japanese lines;
        # short one-line messages may use the other lines of their message window
        english = wrap(english, source, _font(project), width=box["width"] if box else None)
    else:
        english = english.replace("{CR}", "").replace("\n", " ")
    return english


_LEADING_TAGS = re.compile(r"^(?:\{[^{}]*\})*")


def strip_dialogue_quotes(source: str, english: str) -> str:
    """English dialogue has no quotation marks and no Japanese indentation: remove a quote
    that opens a line whose Japanese starts with 「 (and its closing partner at the very end),
    and every ideographic space (U+3000)."""
    t = english.replace("　", " ")
    t = "\n".join(line.lstrip(" ") if i else line for i, line in enumerate(t.split("\n")))
    if source[len(_LEADING_TAGS.match(source).group(0)):].startswith("「"):
        lead = _LEADING_TAGS.match(t).group(0)
        body = t[len(lead):]
        if body.startswith('"'):
            body = body[1:]
            if body.rstrip().endswith('"') and body.count('"') % 2 == 1:
                end = body.rstrip()
                body = end[:-1] + body[len(end):]
            t = lead + body
    return t


def _save(project: Project, sid: int, english: str, overwrite: bool = False):
    db = project.db()
    # official imports (origin 'pc') may be replaced by their condensed version; hand edits never
    cond = "" if overwrite else " AND (status <= 1 OR translation IS NULL OR origin = 'pc')"
    db.execute(f"UPDATE source SET translation=?, status=1, origin='ai', updated=? WHERE id=?{cond}",
               (english, time.time(), sid))


def _batches(items: list[dict], size: int, max_chars: int = 5000):
    batch, chars = [], 0
    for it in items:
        if batch and (len(batch) >= size or chars + len(it["text"]) > max_chars):
            yield batch
            batch, chars = [], 0
        batch.append(it)
        chars += len(it["text"])
    if batch:
        yield batch


def _parse_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", text)
    try:
        return json.loads(text)
    except ValueError:
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            return json.loads(m.group(0))
        raise


def _user_payload(items: list[dict]) -> str:
    out = []
    for it in items:
        d = {"id": it["id"], "context": it["context"], "japanese": it["text"]}
        if it.get("max_chars"):
            d["max_chars"] = it["max_chars"]
        tags = sorted(set(_VTAG.findall(it["text"])))
        if tags:
            d["required_tags"] = tags
        if it.get("tag_error"):
            d["tag_error"] = it["tag_error"]
        if it.get("previous"):
            d["previous"] = it["previous"]
        if it.get("reference"):
            d["reference"] = it["reference"]
        out.append(d)
    return json.dumps(out, ensure_ascii=False)


def _font(project: Project):
    # the editor's "too wide" warning is measured with this font, so the budget uses it too
    return FontSet.for_project(project).fonts["3D14"]


def _boxes(project: Project) -> boxfit.BoxIndex:
    return boxfit.index(project, _font(project))


def _system(gloss: list[dict]) -> str:
    gl = "\n".join(f"{g['jp']} = {g['en']}" for g in gloss)
    return SYSTEM_PROMPT + "\n\nGlossary:\n" + gl


# ---------------------------------------------------------------------- OpenAI-compatible backend

PROTOCOLS = ("chat", "responses", "messages")

# OpenCode Go serves each model family through one API protocol (opencode.ai/docs/go):
# /responses for GPT, Grok and Muse Spark, /messages for Claude, MiniMax and Qwen,
# /chat/completions for everything else (DeepSeek, GLM, Kimi, MiMo, ...)
_RESPONSES_MODELS = re.compile(r"^(gpt|grok|muse|o\d)", re.I)
_MESSAGES_MODELS = re.compile(r"^(claude|minimax|qwen)", re.I)


def _guess_protocol(model: str) -> str:
    if _RESPONSES_MODELS.match(model):
        return "responses"
    if _MESSAGES_MODELS.match(model):
        return "messages"
    return "chat"


def _wrong_protocol(text: str) -> bool:
    t = text.lower()
    return any(k in t for k in ("protocol", "not supported", "unsupported", "use the responses",
                                "use the messages", "endpoint", "not found"))


class OpenAICompat:
    def __init__(self, base_url: str, api_key: str, model: str):
        import httpx
        if not base_url or not model:
            raise ValueError("base URL and model are required")
        import uuid
        self.base = base_url.rstrip("/")
        # OpenCode config names models "opencode-go/<id>"; the HTTP API wants the bare id
        self.model = model.split("/", 1)[1] if model.startswith(("opencode-go/", "opencode/")) else model
        # OpenCode Go rejects requests without a stable session id (routing/prompt caching)
        # and asks clients to send their own user agent; other providers ignore both
        headers = {"User-Agent": "dq11-translator/1.0", "x-opencode-session": f"dq11-{uuid.uuid4()}"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        if api_key:
            headers["x-api-key"] = api_key          # Anthropic-style /messages endpoints
        headers["anthropic-version"] = "2023-06-01"
        self.client = httpx.Client(timeout=httpx.Timeout(180.0, connect=15.0), headers=headers)
        self.json_mode = True
        self.protocol = _guess_protocol(self.model)

    def list_models(self) -> list[str]:
        r = self.client.get(self.base + "/models")
        r.raise_for_status()
        data = r.json()
        items = data.get("data", data if isinstance(data, list) else [])
        return sorted({(m.get("id") if isinstance(m, dict) else str(m)) for m in items if m})

    def _request(self, system: str, user: str) -> tuple[str, dict]:
        """(endpoint path, JSON body) for the current protocol."""
        if self.protocol == "responses":      # OpenAI Responses API (GPT, Grok, Muse Spark on OpenCode Go)
            # reasoning models spend part of the output budget thinking; 8k left some answers empty
            body = {"model": self.model, "instructions": system, "input": user, "max_output_tokens": 32768}
            if self.json_mode:
                body["text"] = {"format": {"type": "json_object"}}
            return "/responses", body
        if self.protocol == "messages":       # Anthropic Messages API (Claude, MiniMax, Qwen on OpenCode Go)
            return "/messages", {"model": self.model, "max_tokens": 8192, "system": system,
                                 "messages": [{"role": "user", "content": user}]}
        body = {"model": self.model, "temperature": 0.3, "max_tokens": 8192,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        if self.json_mode:
            body["response_format"] = {"type": "json_object"}
        return "/chat/completions", body

    def _content(self, data: dict) -> str:
        if self.protocol == "responses":
            if isinstance(data.get("output_text"), str):
                return data["output_text"]
            return "".join(c.get("text", "") for o in data.get("output") or [] if o.get("type") == "message"
                           for c in o.get("content") or [] if c.get("type") in ("output_text", "text"))
        if self.protocol == "messages":
            return "".join(b.get("text", "") for b in data.get("content") or [] if b.get("type") == "text")
        choice = (data.get("choices") or [{}])[0]
        return (choice.get("message") or {}).get("content") or ""

    def translate(self, items: list[dict], gloss: list[dict]) -> dict[int, str]:
        system = _system(gloss) + "\n\n" + JSON_INSTRUCTION
        user = _user_payload(items)
        tried = {self.protocol}
        attempt = 0
        while attempt < 5:
            path, body = self._request(system, user)
            try:
                r = self.client.post(self.base + path, json=body)
            except Exception:  # network hiccup
                attempt += 1
                if attempt == 5:
                    raise
                time.sleep(2 ** attempt)
                continue
            if r.status_code in (400, 404, 405, 415, 422) and _wrong_protocol(r.text):
                # the model speaks another API protocol: switch once per protocol
                nxt = next((p for p in PROTOCOLS if p not in tried), None)
                if nxt is None:
                    raise RuntimeError(f"API error {r.status_code}: {r.text[:300]}")
                with _lock:
                    self.protocol = nxt
                tried.add(nxt)
                continue
            if r.status_code == 400 and self.json_mode and ("response_format" in r.text or "format" in r.text):
                self.json_mode = False
                continue
            if r.status_code in (401, 403):
                raise PermissionError(f"API key rejected ({r.status_code}): {r.text[:200]}")
            if r.status_code in (408, 409, 429) or r.status_code >= 500:
                attempt += 1
                wait = float(r.headers.get("retry-after", 0) or 0) or 2 ** attempt
                time.sleep(min(wait, 60))
                continue
            if r.status_code >= 400:
                raise RuntimeError(f"API error {r.status_code}: {r.text[:300]}")
            content = self._content(r.json())
            if not content.strip():                # out of output budget / empty reply: try again
                attempt += 1
                time.sleep(1)
                continue
            parsed = _parse_json(content)
            return {int(t["id"]): str(t["english"]) for t in parsed.get("translations", [])
                    if isinstance(t, dict) and "id" in t and "english" in t}
        raise RuntimeError("API kept failing (rate limit or server errors)")


# ---------------------------------------------------------------------- Claude backend

class ClaudeBackend:
    def __init__(self, api_key: str | None):
        import anthropic
        self.client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()

    def translate(self, items: list[dict], gloss: list[dict]) -> dict[int, str]:
        import anthropic
        schema = {
            "type": "object",
            "properties": {"translations": {"type": "array", "items": {
                "type": "object",
                "properties": {"id": {"type": "integer"}, "english": {"type": "string"}},
                "required": ["id", "english"], "additionalProperties": False}}},
            "required": ["translations"], "additionalProperties": False,
        }
        try:
            response = self.client.beta.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=16000,
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                system=[{"type": "text", "text": _system(gloss), "cache_control": {"type": "ephemeral"}}],
                output_config={"effort": "medium", "format": {"type": "json_schema", "schema": schema}},
                messages=[{"role": "user", "content": _user_payload(items)}],
            )
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as e:
            raise PermissionError(f"Anthropic API credentials rejected: {e}") from e
        if response.stop_reason == "refusal":
            raise RuntimeError("the model declined this batch")
        if response.stop_reason == "max_tokens":
            raise RuntimeError("batch output was cut off (max_tokens)")
        text = next(b.text for b in response.content if b.type == "text")
        return {int(t["id"]): t["english"] for t in json.loads(text)["translations"]}


# ---------------------------------------------------------------------- Google backend

_PH = re.compile(r"\{v:[0-9A-Fa-f]+\}")


class GoogleBackend:
    def __init__(self):
        from deep_translator import GoogleTranslator
        self.tr = GoogleTranslator(source="ja", target="en")

    def translate(self, items: list[dict], gloss: list[dict]) -> dict[int, str]:
        out = {}
        for it in items:
            text = it["text"]
            plain = re.sub(r"\{ruby:([^|{}]*)\|[^{}]*\}", r"\1", text).replace("{CR}", "")
            tags = []

            def keep(m):
                tags.append(m.group(0))
                return f" [{len(tags) - 1}] "

            plain = _PH.sub(keep, plain).replace("\n", " ").replace("　", " ").replace("「", "").replace("」", "")
            res = self.tr.translate(plain) or ""
            for i, t in enumerate(tags):
                res = re.sub(r"\s*\[\s*%d\s*\]\s*" % i, " " + t + " ", res, count=1)
            res = re.sub(r"\s+", " ", res).strip()
            if text.lstrip().startswith("「"):
                res = '"' + res
            out[it["id"]] = res
        return out


def make_backend(project: Project, settings: dict | None = None):
    s = settings or load_settings(project)
    b = s.get("backend", "openai")
    if b == "openai":
        return OpenAICompat(s.get("base_url", ""), get_key(project, "openai") or "", s.get("model", ""))
    if b == "claude":
        return ClaudeBackend(get_key(project, "claude"))
    if b == "google":
        return GoogleBackend()
    raise ValueError("unknown backend " + b)


def list_models(project: Project, base_url: str, api_key: str | None) -> list[str]:
    key = api_key or get_key(project, "openai") or ""
    return OpenAICompat(base_url, key, "x").list_models()


def test_backend(project: Project, settings: dict, api_key: str | None = None) -> list[dict]:
    """Translate three sample strings without saving anything."""
    s = dict(load_settings(project))
    s.update({k: v for k, v in settings.items() if k in DEFAULT_SETTINGS})
    if api_key:
        set_key(project, s["backend"], api_key, remember=bool(s.get("remember_key")))
    be = make_backend(project, s)
    sample = _select(project, None, {"category": "NPC Dialogue"}, overwrite=True, limit=3)
    res = be.translate(sample, glossary(project))
    return [{"japanese": it["text"], "english": _postprocess(project, it["text"], res.get(it["id"], ""), it["id"])}
            for it in sample]


# ---------------------------------------------------------------------- job

def translate_sources(project: Project, ids=None, scope=None, overwrite: bool = False,
                      progress=None, cancel: threading.Event | None = None) -> dict:
    progress = progress or (lambda *a: None)
    cancel = cancel or threading.Event()
    s = load_settings(project)
    be = make_backend(project, s)
    gloss = glossary(project)
    items = _select(project, ids, scope, overwrite)
    total = len(items)
    stats = {"translated": 0, "failed": 0, "total": total, "errors": [], "tag_warnings": 0,
             "shortened": 0, "too_wide": 0, "tag_rejected": 0}
    bi = _boxes(project)
    stats_lock = threading.Lock()
    progress("Translating", 0, total)
    if not total:
        return stats
    batch_size = int(s.get("batch_size", 30)) if s.get("backend") != "google" else 10
    workers = int(s.get("concurrency", 4)) if s.get("backend") != "google" else 1
    done_count = 0
    db_lock = threading.Lock()

    def run_batch(batch: list[dict], depth: int = 0) -> list[tuple[dict, str]]:
        if cancel.is_set():
            return []
        try:
            res = be.translate(batch, gloss)
        except PermissionError:
            raise
        except Exception as e:
            if len(batch) > 1 and depth < 3:   # split and retry halves
                mid = len(batch) // 2
                return run_batch(batch[:mid], depth + 1) + run_batch(batch[mid:], depth + 1)
            stats["errors"].append(str(e)[:200])
            return [(it, "") for it in batch]
        out = [(it, res.get(it["id"], "")) for it in batch]
        missing = [it for it, en in out if not en]
        if missing and len(missing) < len(batch) and depth < 3:
            retry = {m["id"]: en for m, en in run_batch(missing, depth + 1)}
            out = [(it, en or retry.get(it["id"], "")) for it, en in out]
        if depth == 0:
            out = fix_tags(shorten(out))
        return out

    def fix_tags(out: list[tuple[dict, str]]) -> list[tuple[dict, str]]:
        """Placeholders are not optional: answers that lost or invented a {v:..} tag are sent
        back with the exact error (up to TAG_RETRIES times); if they still fail they are
        dropped, so the string stays on the to-do list instead of being saved broken."""
        out = list(out)
        for _ in range(TAG_RETRIES):
            bad = [(i, it, en) for i, (it, en) in enumerate(out) if en and validate(it["text"], en)]
            if not bad or cancel.is_set():
                break
            retry = [{**it, "previous": en.replace("{CR}", "").replace("\n", " "),
                      "tag_error": "; ".join(validate(it["text"], en))} for _, it, en in bad]
            try:
                res = be.translate(retry, gloss)
            except PermissionError:
                raise
            except Exception:
                res = {}
            for i, it, en in bad:
                alt = res.get(it["id"])
                if alt:
                    out[i] = (it, _postprocess(project, it["text"], alt, it["id"]))
        result = []
        for it, en in out:
            if en and validate(it["text"], en):
                with stats_lock:
                    stats["tag_rejected"] += 1
                en = ""                        # counted as failed, retried by the next run
            result.append((it, en))
        return result

    def shorten(out: list[tuple[dict, str]]) -> list[tuple[dict, str]]:
        """Measure every result with the game font; ask for a shorter version of the ones
        that do not fit, up to SHORTEN_ROUNDS times with a tighter budget each round.
        Returns post-processed English."""
        done = {it["id"]: (it, _postprocess(project, it["text"], en, it["id"]) if en else "") for it, en in out}
        budgets = {it["id"]: it["max_chars"] for it, _ in out}
        shortened = set()
        for _ in range(SHORTEN_ROUNDS):
            wide = [(it, en) for it, en in done.values() if en and not bi.fits(it["id"], it["text"], en)]
            if not wide or cancel.is_set():
                break
            retry = []
            for it, en in wide:
                # tighten the budget a little more every round
                budgets[it["id"]] = max(2, min(budgets[it["id"]] - 1, int(budgets[it["id"]] * 0.85)))
                retry.append({**it, "previous": en.replace("{CR}", "").replace("\n", " "),
                              "max_chars": budgets[it["id"]]})
            try:
                res = be.translate(retry, gloss)
            except PermissionError:
                raise
            except Exception:
                res = {}
            for it, en in wide:
                alt = res.get(it["id"])
                if alt:
                    alt = _postprocess(project, it["text"], alt, it["id"])
                    if bi.overshoot(it["id"], it["text"], alt) < bi.overshoot(it["id"], it["text"], en):
                        done[it["id"]] = (it, alt)
                        shortened.add(it["id"])
        with stats_lock:
            stats["shortened"] += len(shortened)
        return [done[it["id"]] for it, _ in out]

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(run_batch, b) for b in _batches(items, batch_size)]
        for fut in as_completed(futures):
            try:
                results = fut.result()
            except PermissionError as e:
                cancel.set()
                for f in futures:
                    f.cancel()
                raise RuntimeError(str(e)) from e
            with db_lock:
                for it, en in results:
                    if not en:
                        stats["failed"] += 1
                        continue
                    if validate(it["text"], en):
                        stats["tag_warnings"] += 1
                    if not bi.fits(it["id"], it["text"], en):
                        stats["too_wide"] += 1
                    _save(project, it["id"], en, overwrite)
                    stats["translated"] += 1
                project.db().commit()
                done_count += len(results)
                progress("Stopping…" if cancel.is_set() else "Translating", done_count, total)
    stats["cancelled"] = cancel.is_set()
    stats["errors"] = stats["errors"][:50]
    progress("Done", total, total)
    return stats

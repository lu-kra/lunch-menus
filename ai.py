"""AI fitness hints via the Google Gemini API (free tier).

- Estimates nutrition (kcal, protein, carbs, fat, fiber) of every dish in text menus.
- Reads image-only menus (Maytex, Klar) into dishes, with the same nutrition estimates.

New model calls are made only when GEMINI_API_KEY is set. Every result is cached by a hash
of the dish text / image bytes, so each dish or image is sent to the model once; the cache is
published as ai.json and reused by the next build (AI_CACHE_URL).
"""
import base64
import datetime as dt
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

VERSION = "1"  # bump after changing the prompts to re-estimate everything
API = os.environ.get("GEMINI_API_BASE", "https://generativelanguage.googleapis.com/v1beta")
FALLBACK_MODELS = ["gemini-flash-latest", "gemini-flash-lite-latest"]
BUDGET = 8 * 60          # max seconds spent on model calls per run
RETRY_AFTER = 2 * 3600   # a failed dish/image is retried after this
KEEP = 21 * 86400        # cache entries unused for this long are dropped
BATCH = 40               # dishes per text request

NUTRITION = (
    "kcal, protein, carbs, fat, fiber (numbers, grams); fried (true if the main component is "
    "breaded or deep-fried); sweet (true for a sweet/dessert main dish); note (Slovak, max 12 words: "
    "why it is or isn't a good choice for a gym-goer who wants to eat healthily, or a healthier tweak)")
PORTIONS = (
    'Printed weights are portion sizes ("150g" usually = meat, "350g" = whole plate, '
    '"150g/350g" = meat/plate, "150g+200g" = meat+side); soups are 0.33 l unless stated. Without '
    "weights assume a typical Slovak lunch portion (~150 g meat + ~200 g side). If the guest has to "
    "choose (meat, side dish), assume the healthiest option and mention it in the note.")
TEXT_PROMPT = (
    "You are a sports nutritionist. Estimate nutrition for ONE serving of each Slovak restaurant lunch "
    "dish below, as served with the listed side dishes. " + PORTIONS + " Ignore prices and ordering info.\n"
    "For every dish return: id, " + NUTRITION + ".\n"
    'Answer with JSON only: {"items": [{"id": 1, "kcal": 0, "protein": 0, "carbs": 0, "fat": 0, '
    '"fiber": 0, "fried": false, "sweet": false, "note": ""}]}\n\nDishes:\n')
IMAGE_PROMPT = (
    "The image is a Slovak restaurant's lunch menu for one week. Read it carefully and list every "
    "dish: soups and main dishes (skip side-dish lists, drinks, opening hours and allergen legends).\n"
    "For each dish return:\n"
    "- days: weekdays it is served, 0=Monday ... 4=Friday ([0,1,2,3,4] for week-long dishes)\n"
    '- section: "daily", "weekly" (week-long menu or soup of the week) or "permanent" (stála ponuka)\n'
    "- soup: true for soups\n"
    '- number: the dish number as printed ("1", "2", ...) or ""\n'
    "- name: the dish exactly as printed but in normal Slovak sentence case (not ALL CAPS), "
    'starting with its printed weights, e.g. "150g/350g Morčacia roláda so šampiňónmi, ryža"\n'
    "- price: the price that applies to this dish (menus often state one price for all daily dishes), "
    'e.g. "7,50 €"; "" for soups included in the menu price\n'
    '- allergens: allergen numbers if legible, e.g. "1,3,7", else ""\n'
    "- nutrition for ONE serving as served: " + NUTRITION + ".\n" + PORTIONS + "\n"
    'Also return week_start: the Monday of the menu\'s week as "YYYY-MM-DD" if dates are printed, else "".\n'
    'Answer with JSON only: {"week_start": "", "items": [{"days": [0], "section": "daily", '
    '"soup": false, "number": "1", "name": "", "price": "", "allergens": "", "kcal": 0, "protein": 0, '
    '"carbs": 0, "fat": 0, "fiber": 0, "fried": false, "sweet": false, "note": ""}]}')


def log(msg):
    print(msg, file=sys.stderr)


def _key(*parts):
    return hashlib.sha256("\x1f".join((VERSION,) + parts).encode()).hexdigest()[:24]


def _norm(s):
    return " ".join(str(s or "").lower().split())


def _truthy(v):
    return v is True or str(v).strip().lower() in ("true", "1", "yes")


# ---------------------------------------------------------------- cache (ai.json)
def load_cache(path=None, url=None):
    cache = {}
    sources = [("file", path)] if path and os.path.exists(path) else []
    if url:
        sources.append(("url", url))
    for kind, src in sources:
        try:
            if kind == "file":
                with open(src, encoding="utf-8") as f:
                    entries = json.load(f).get("entries", {})
            else:
                req = urllib.request.Request(f"{src}?t={int(time.time())}",
                                             headers={"User-Agent": "lunch-menus"})
                with urllib.request.urlopen(req, timeout=20) as r:
                    entries = json.load(r).get("entries", {})
            cache.update(entries)
        except Exception as e:
            log(f"AI cache not loaded from {src}: {e}")
    return cache


def save_cache(cache, path):
    now = time.time()
    entries = {k: v for k, v in cache.items() if now - v.get("t", 0) < KEEP}
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"version": VERSION, "entries": entries}, f, ensure_ascii=False)


def _lookup(cache, key, now):
    """Return (cached value or None, whether a model call is needed)."""
    entry = cache.get(key)
    if entry and "v" in entry:
        entry["t"] = now
        return entry["v"], False
    if entry and now - entry.get("t", 0) < RETRY_AFTER:
        return None, False
    return None, True


# ---------------------------------------------------------------- Gemini API
def _request(url, body=None, timeout=60):
    req = urllib.request.Request(
        url, data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json",
                 "x-goog-api-key": os.environ["GEMINI_API_KEY"].strip()})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def pick_models():
    """Newest stable Flash models first, then Flash-Lite (separate free quotas) as fallback."""
    if os.environ.get("GEMINI_MODELS"):
        return [m.strip() for m in os.environ["GEMINI_MODELS"].split(",") if m.strip()]
    found = []
    try:
        for m in _request(f"{API}/models?pageSize=1000").get("models", []):
            name = m.get("name", "").removeprefix("models/")
            mm = re.fullmatch(r"gemini-(\d+(?:\.\d+)*)-flash(-lite)?", name)
            if mm and "generateContent" in m.get("supportedGenerationMethods", []):
                found.append((bool(mm.group(2)), tuple(-int(x) for x in mm.group(1).split(".")), name))
    except Exception as e:
        log(f"AI: listing models failed: {e}")
    found.sort()
    flash = [n for lite, _, n in found if not lite][:2]
    lite = [n for lite, _, n in found if lite][:2]
    return list(dict.fromkeys(flash + lite + FALLBACK_MODELS))


def _parse_json(text):
    try:
        return json.loads(text)
    except ValueError:
        m = re.search(r"\{.*\}", text, re.S)  # e.g. wrapped in ```json fences
        return json.loads(m.group(0) if m else text)


def _items(result):
    """The "items" list of a model answer (also accepts a bare list)."""
    if isinstance(result, dict):
        result = result.get("items")
    return [x for x in result if isinstance(x, dict)] if isinstance(result, list) else []


def generate(models, parts):
    """Ask the first model that answers; returns (parsed JSON, model name)."""
    body = {"contents": [{"role": "user", "parts": parts}],
            "generationConfig": {"responseMimeType": "application/json",
                                 "thinkingConfig": {"thinkingLevel": "low"}}}
    errors = []
    for model in models:
        for _ in range(2):
            try:
                resp = _request(f"{API}/models/{model}:generateContent", body, timeout=180)
                cand = (resp.get("candidates") or [{}])[0]
                text = "".join(p.get("text", "") for p in cand.get("content", {}).get("parts", [])
                               if not p.get("thought"))
                return _parse_json(text), model
            except urllib.error.HTTPError as e:
                msg = " ".join(e.read().decode("utf-8", "replace").split())[:200]
                if e.code == 400 and "thinking" in msg.lower() and "thinkingConfig" in body["generationConfig"]:
                    del body["generationConfig"]["thinkingConfig"]  # model without thinking levels
                    continue
                errors.append(f"{model}: HTTP {e.code} {msg}")
            except Exception as e:
                errors.append(f"{model}: {type(e).__name__}: {e}"[:200])
            break
    raise RuntimeError(" | ".join(errors) or "no model available")


# ---------------------------------------------------------------- validation
def clean_nutrition(x):
    def num(k, hi):
        try:
            return max(0, min(hi, round(float(x.get(k)))))
        except (TypeError, ValueError):
            return None

    p, c, f = num("protein", 250), num("carbs", 500), num("fat", 250)
    if None in (p, c, f):
        return None
    kcal, calc = num("kcal", 5000), 4 * p + 4 * c + 9 * f
    if not kcal or abs(kcal - calc) > 0.25 * calc:
        kcal = calc
    if kcal < 20:
        return None
    return {"kcal": kcal, "protein": p, "carbs": c, "fat": f, "fiber": num("fiber", 80) or 0,
            "fried": _truthy(x.get("fried")), "sweet": _truthy(x.get("sweet")),
            "note": " ".join(str(x.get("note") or "").split())[:160]}


def clean_extraction(raw):
    order = {"daily": 0, "weekly": 1, "permanent": 2}
    items = []
    for n, x in enumerate(_items(raw)):
        name = " ".join(str(x.get("name") or "").split())[:240]
        days = x.get("days")
        days = sorted({int(d) for d in (days if isinstance(days, list) else [days])
                       if str(d).strip().isdigit() and int(d) <= 4})
        if not name or not days:
            continue
        section = str(x.get("section") or "").strip().lower()
        section = section if section in order else "daily"
        soup = _truthy(x.get("soup"))
        num = re.sub(r"\D", "", str(x.get("number") or ""))[:3]
        if soup:
            category = "Polievka týždňa" if section == "weekly" else "Polievka"
        elif section == "weekly":
            category = f"Týždenné · {num}" if num else "Týždenné"
        elif section == "permanent":
            category = f"Stála ponuka · {num}" if num else "Stála ponuka"
        else:
            category = f"Menu {num}" if num else "Menu"
        items.append({"days": days, "category": category, "name": name,
                      "price": " ".join(str(x.get("price") or "").split())[:30],
                      "allergens": re.sub(r"[^\d,]", "", str(x.get("allergens") or ""))[:40],
                      "note": "", "ai": clean_nutrition(x),
                      "_sort": (not soup, order[section], int(num) if num else 99, n)})
    items.sort(key=lambda it: it.pop("_sort"))
    week = str(raw.get("week_start") or "") if isinstance(raw, dict) else ""
    return {"week_start": week[:10], "items": items}


def apply_extraction(r, v):
    try:
        start = dt.date.fromisoformat(v.get("week_start") or "")
        start -= dt.timedelta(days=start.weekday())
    except ValueError:
        start = None
    days = {}
    for x in v["items"]:
        for d in x["days"]:
            date = start + dt.timedelta(days=d) if start else None
            day = days.setdefault(d, {"date": f"{date.day}.{date.month}." if date else "", "items": []})
            day["items"].append({k: x[k] for k in ("category", "name", "price", "allergens", "note", "ai")})
    r["days"] = dict(sorted(days.items()))
    r["aiExtracted"] = True


# ---------------------------------------------------------------- enrichment
def enrich(data, get_image, cache):
    """Attach item["ai"] nutrition and read image-only menus, using/updating `cache` in place.

    Without GEMINI_API_KEY only cached results are applied (no model calls)."""
    can_call = bool(os.environ.get("GEMINI_API_KEY"))
    now, deadline = time.time(), time.time() + BUDGET
    status = {"calls": 0, "cached": 0, "models": [], "errors": []}
    models = []

    def call(parts, what):
        if not can_call or time.time() > deadline:
            return None
        if not models:
            models.extend(pick_models())
            log(f"AI models: {', '.join(models)}")
        status["calls"] += 1
        try:
            result, model = generate(models, parts)
        except Exception as e:
            status["errors"].append(f"{what}: {e}"[:300])
            log(f"AI {what} failed: {e}")
            return False
        if model not in status["models"]:
            status["models"].append(model)
        return result

    # 1) Restaurants that publish only an image of the week's menu.
    for r in data["restaurants"]:
        if r["days"] or not r["weekImages"]:
            continue
        try:
            blobs = [get_image(im["src"]) for im in r["weekImages"][:3]]
        except Exception as e:
            status["errors"].append(f"{r['name']} image: {e}")
            continue
        key = _key("img", *[hashlib.sha256(body).hexdigest() for body, _ in blobs])
        value, need = _lookup(cache, key, now)
        if need:
            parts = [{"text": IMAGE_PROMPT}] + [
                {"inlineData": {"mimeType": ctype.split(";")[0], "data": base64.b64encode(body).decode()}}
                for body, ctype in blobs]
            result = call(parts, f"{r['name']} image")
            if result is not None:
                value = clean_extraction(result) if result else None
                if value and value["items"]:
                    cache[key] = {"v": value, "t": now}
                else:
                    value = None
                    cache[key] = {"err": "no dishes read", "t": now}
        elif value:
            status["cached"] += 1
        if value:
            apply_extraction(r, value)

    # 2) Text menus: one nutrition estimate per unique dish.
    todo = {}
    for r in data["restaurants"]:
        if r.get("aiExtracted"):
            continue
        for d in r["days"].values():
            for it in d["items"]:
                key = _key("dish", _norm(it["name"]), _norm(it["note"]))
                value, need = _lookup(cache, key, now)
                if value:
                    it["ai"] = value
                    status["cached"] += 1
                elif need:
                    todo.setdefault(key, []).append(it)
    keys = list(todo)
    for i in range(0, len(keys), BATCH):
        chunk = keys[i:i + BATCH]
        lines = []
        for n, key in enumerate(chunk, 1):
            it = todo[key][0]
            lines.append(f"{n}. [{it['category']}] {it['name']}" + (f" — {it['note']}" if it["note"] else ""))
        result = call([{"text": TEXT_PROMPT + "\n".join(lines)}], f"{len(chunk)} dishes")
        if result is None:
            break
        got = {}
        for x in _items(result):
            try:
                n = int(x.get("id"))
            except (TypeError, ValueError, AttributeError):
                continue
            value = clean_nutrition(x)
            if value and 1 <= n <= len(chunk):
                got[chunk[n - 1]] = value
        for key in chunk:
            if key in got:
                cache[key] = {"v": got[key], "t": now}
                for it in todo[key]:
                    it["ai"] = got[key]
            else:
                cache[key] = {"err": "no estimate", "t": now}

    data["ai"] = status
    return status

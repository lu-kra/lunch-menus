#!/usr/bin/env python3
"""Lunch menu aggregator: scrapes daily menus and serves them on a single page.

Run:    python3 server.py              (then open http://localhost:8066)
Build:  python3 server.py --build DIR  (static site for GitHub Pages)
"""
import copy
import datetime as dt
import hashlib
import html as htmllib
import json
import os
import re
import shutil
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import ai

PORT = int(os.environ.get("PORT", "8066"))
CACHE_TTL = 20 * 60
ROOT = os.path.dirname(os.path.abspath(__file__))
AI_CACHE_FILE = os.path.join(ROOT, ".ai-cache.json")
# Some of the sites block non-browser user agents (HTTP 466).
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,image/*,*/*;q=0.8",
    "Accept-Language": "sk,en;q=0.8",
}
DAYS = ["pondelok", "utorok", "streda", "štvrtok", "piatok", "sobota", "nedeľa"]
S = re.S | re.I


def fetch(url, binary=False):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=20) as r:
        data = r.read()
        if binary:
            return data, r.headers.get("Content-Type", "application/octet-stream")
        return data.decode(r.headers.get_content_charset() or "utf-8", "replace")


def text(fragment):
    s = re.sub(r"<br\s*/?>", "\n", fragment, flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = htmllib.unescape(s).replace("\xa0", " ")
    lines = [re.sub(r"[ \t\r\f\v]+", " ", ln).strip() for ln in s.split("\n")]
    return "\n".join(ln for ln in lines if ln)


def day_index(name):
    name = name.strip().lower()
    for i, d in enumerate(DAYS):
        if name.startswith(d[:3]):
            return i
    return None


def item(category="", name="", price="", allergens="", note=""):
    return {"category": category, "name": name, "price": price,
            "allergens": allergens, "note": note}


# ---------------------------------------------------------------- R66
def parse_r66(html):
    start = html.find('class="menu-ponuka-container"')
    if start < 0:
        raise ValueError("menu container not found")
    parts = re.split(r"<h2[^>]*>", html[start:])[1:]
    days = {}
    for part in parts:
        head, _, rest = part.partition("</h2>")
        m = re.match(r"(\S+)\s+(\d{1,2}\.\d{1,2}\.?)", text(head))
        if not m or day_index(m.group(1)) is None:
            continue
        items = []
        for p in re.findall(r"<p[^>]*>(.*?)</p>", rest, S):
            line = " ".join(text(p).split())
            if not line:
                continue
            if line.startswith("(") and items:
                items[-1]["note"] = (items[-1]["note"] + " " + line).strip()
                continue
            num = re.match(r"^(\d+)\.\s*(.+)$", line)
            if num:
                desc, _, price = num.group(2).partition("...")
                alg = re.search(r"\s+A/([\d,\s]+)$", desc)
                if alg:
                    desc = desc[: alg.start()]
                pm = re.match(r"([\d.,]+\s*€)\s*(.*)", price.strip())
                items.append(item(f"Menu {num.group(1)}", desc.strip(),
                                  pm.group(1) if pm else price.strip(),
                                  alg.group(1).strip() if alg else "",
                                  re.sub(r"^\(u nás\)\s*/\s*", "", pm.group(2)) if pm else ""))
            elif not items or items[-1]["category"] == "Polievka":
                alg = re.search(r"\s+([\d,]+)$", line)
                items.append(item("Polievka", line[: alg.start()] if alg else line,
                                  allergens=alg.group(1) if alg else ""))
        days[day_index(m.group(1))] = {"date": m.group(2), "items": items}
    if not days:
        raise ValueError("no days parsed")
    return {"days": days}


# ---------------------------------------------------------------- Liptov 1713
def parse_liptov(html):
    period = re.search(r"Denné menu<br><small>(.*?)</small>", html, S)
    period = text(period.group(1)) if period else ""
    first = re.match(r"(\d{1,2})\.(\d{1,2})\.", period)
    year = re.search(r"(\d{4})", period)
    end = html.find("<h4>Alergény")
    heads = list(re.finditer(r'<h3 class="card-title[^"]*">(.*?)</h3>', html, S))
    days = {}
    for n, h in enumerate(heads):
        idx = day_index(text(h.group(1)))
        if idx is None:
            continue
        stop = heads[n + 1].start() if n + 1 < len(heads) else (end if end > 0 else len(html))
        chunk = html[h.end():stop]
        perm = chunk.find("Stála ponuka")
        items = []
        rows = re.finditer(r'<div class="row mb-\d">\s*<div class="col-md-2[^"]*">'
                           r'<strong>(.*?)</strong></div>(.*?)(?=<div class="row mb-\d">|<h4|$)',
                           chunk, S)
        for row in rows:
            label, body = text(row.group(1)), row.group(2)
            if label.lower().startswith("polievk"):
                inner = re.search(r'<div class="col-md-10">(.*)', body, S)
                for soup in re.split(r"<br\s*/?>", inner.group(1) if inner else "", flags=re.I):
                    alg = re.search(r"<em>(.*?)</em>", soup, S)
                    name = " ".join(text(re.sub(r"<small>.*?</small>", "", soup, flags=S)).split())
                    if name:
                        items.append(item(label, name,
                                          allergens=text(alg.group(1)).strip("()") if alg else ""))
                continue
            category = "Stála ponuka · " + label if 0 <= perm < row.start() else label
            desc = re.search(r'<div class="col-md-8[^"]*">(.*?)</div>', body, S)
            price = re.search(r'text-md-right">(.*?)</div>', body, S)
            desc = desc.group(1) if desc else ""
            alg = re.search(r"<em>(.*?)</em>", desc, S)
            notes = [text(x) for x in re.findall(r"<small>(?!\s*<em>)(.*?)</small>", desc, S)]
            name = text(re.split(r"<br|<small", desc, maxsplit=1)[0])
            items.append(item(category, " ".join(name.split()),
                              " ".join(text(price.group(1)).split()) if price else "",
                              text(alg.group(1)).strip("()") if alg else "",
                              "; ".join(x for x in notes if x)))
        date = ""
        if first and year:
            try:
                start = dt.date(int(year.group(1)), int(first.group(2)), int(first.group(1)))
                d = start + dt.timedelta(days=idx - start.weekday())
                date = f"{d.day}.{d.month}."
            except ValueError:
                pass
        days[idx] = {"date": date, "items": items}
    if not days:
        raise ValueError("no days parsed")
    return {"days": days, "period": period}


# ---------------------------------------------------------------- Maytex (one image per week)
def parse_maytex(html):
    m = re.search(r'<img[^>]+src="([^"]*?/img/den\.jpg[^"]*)"', html, S)
    if not m:
        raise ValueError("menu image not found")
    url = urllib.parse.urljoin("https://www.maytex.sk/", re.sub(r"(?<!:)//+", "/", m.group(1)))
    return {"days": {}, "weekImages": [{"src": url, "full": url}]}


# ---------------------------------------------------------------- Klar (one image per week)
def parse_klar(html):
    tags = re.findall(r'<img[^>]+/uploads/[^>]*[Mm]enu[^>]*>', html, S)
    if not tags:
        raise ValueError("menu image not found")
    shown = []
    for tag in tags:
        full = re.search(r'\ssrc="([^"]+)"', tag).group(1)
        # The original is ~4.5 MB; prefer the ~1086px wide variant from srcset.
        mid = re.search(r"(\S+-1086x\d+\.\w+)\s", tag)
        if full not in [s["full"] for s in shown]:
            shown.append({"src": mid.group(1) if mid else full, "full": full})
    period = re.search(r"(\d{1,2}\.-\d{1,2}\.\d{1,2}\.\d{4})", shown[0]["full"])
    return {"days": {}, "weekImages": shown, "period": period.group(1) if period else ""}


# ---------------------------------------------------------------- SME restaurant menus
def parse_sme(html):
    sections = re.split(
        r"""<div\b[^>]*class=["'][^"']*\b(?:dnesne_menu|ostatne_menu)\b[^"']*["'][^>]*>""",
        html, flags=S)[1:]
    days = {}
    for section in sections:
        heading = re.search(r"<h2\b[^>]*>(.*?)</h2>", section, S)
        date = re.search(r"(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})",
                         text(heading.group(1))) if heading else None
        if not date:
            continue
        day = dt.date(int(date.group(3)), int(date.group(2)), int(date.group(1)))
        rows = re.split(
            r"""<div\b[^>]*class=["'][^"']*\bjedlo_polozka\b[^"']*["'][^>]*>""",
            section, flags=S)[1:]
        items, permanent = [], False
        for row in rows:
            desc = re.search(r"""<div\b[^>]*class=["'][^"']*\bleft\b[^"']*["'][^>]*>(.*?)</div>""",
                             row, S)
            if not desc:
                continue
            name = " ".join(text(desc.group(1)).split())
            if re.match(r"stála ponuka", name, re.I):
                permanent = True
                continue
            if not name:
                continue
            price = re.search(
                r"""<span\b[^>]*class=["'][^"']*\bright\b[^"']*["'][^>]*>(.*?)</span>""",
                row, S)
            soup = re.match(r"polievka\s*:?\s*", name, re.I)
            number = re.match(r"(\d+)\s*[:.)]\s*", name)
            if soup:
                category, name = "Polievka", name[soup.end():]
            elif re.match(r"0[,.]\d+\s*l\b", name, re.I):
                category = "Polievka"
            elif number:
                category, name = f"Menu {number.group(1)}", name[number.end():]
            else:
                category = "Menu"
            if permanent and category != "Polievka":
                category = "Stála ponuka · " + category
            allergens = re.search(
                r"\s+(?:A(?:lergény)?\s*[:/]\s*)?\(?((?:1[0-4]|[1-9])"
                r"(?:\s*,\s*(?:1[0-4]|[1-9]))*)\)?\s*$", name, re.I)
            if allergens:
                name = name[:allergens.start()]
            if name.strip():
                items.append(item(category, name.strip(),
                                  " ".join(text(price.group(1)).split()) if price else "",
                                  re.sub(r"\s+", "", allergens.group(1)) if allergens else ""))
        days[day.weekday()] = {"date": f"{day.day}.{day.month}.", "items": items}
    if not any(d["items"] for d in days.values()):
        raise ValueError("Na SME sa nepodarilo nájsť zverejnené denné menu.")
    return {"days": days}


RESTAURANTS = [
    {"id": "r66", "name": "Route 66", "url": "https://restauracia.r66.sk/sk/menu/denne-menu#mmenu",
     "fetch": "https://restauracia.r66.sk/sk/menu/denne-menu", "parse": parse_r66},
    {"id": "liptov1713", "name": "Liptov 1713", "url": "https://www.liptov1713.sk/#denne-menu",
     "fetch": "https://www.liptov1713.sk/", "parse": parse_liptov},
    {"id": "maytex", "name": "Maytex", "url": "https://www.maytex.sk/",
     "fetch": "https://www.maytex.sk/", "parse": parse_maytex},
    {"id": "klar", "name": "Hotel Klar", "url": "https://klar.sk/denne-menu/",
     "fetch": "https://klar.sk/denne-menu/", "parse": parse_klar},
    {"id": "ukapra", "name": "Posedenie u Kapra",
     "url": "https://restauracie.sme.sk/restauracia/posedenie-u-kapra_12152-liptovsky-mikulas_2866/denne-menu",
     "fetch": "https://restauracie.sme.sk/restauracia/posedenie-u-kapra_12152-liptovsky-mikulas_2866/denne-menu",
     "parse": parse_sme},
]
IMAGE_HOSTS = {"www.maytex.sk", "maytex.sk", "klar.sk", "www.klar.sk"}

_cache = {"time": 0, "data": None}
_img_cache = {}
_lock = threading.Lock()


def fetch_image(url):
    if url not in _img_cache:
        if len(_img_cache) > 50:
            _img_cache.clear()
        _img_cache[url] = fetch(url, binary=True)
    return _img_cache[url]


def scrape_one(r):
    out = {"id": r["id"], "name": r["name"], "url": r["url"], "days": {},
           "weekImages": [], "period": "", "error": None}
    try:
        out.update(r["parse"](fetch(r["fetch"])))
    except Exception as e:  # one broken site must not break the others
        if isinstance(e, urllib.error.HTTPError) and e.code == 403:
            out["error"] = ("Zdroj odmietol automatické načítanie (HTTP 403). "
                            "Originálne menu otvoríš kliknutím na názov reštaurácie.")
        else:
            out["error"] = f"{type(e).__name__}: {e}"
    return out


def get_menus(force=False):
    with _lock:
        if not force and _cache["data"] and time.time() - _cache["time"] < CACHE_TTL:
            return _cache["data"]
        with ThreadPoolExecutor(len(RESTAURANTS)) as ex:
            results = list(ex.map(scrape_one, RESTAURANTS))
        data = {"fetchedAt": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
                "restaurants": results}
        # AI fitness hints: cached results always, new model calls only with GEMINI_API_KEY.
        cache = ai.load_cache(AI_CACHE_FILE, os.environ.get("AI_CACHE_URL"))
        if cache or os.environ.get("GEMINI_API_KEY"):
            try:
                ai.enrich(data, fetch_image, cache)
                ai.save_cache(cache, AI_CACHE_FILE)
            except Exception as e:  # the hints are optional, the menus must still work
                print(f"AI failed: {type(e).__name__}: {e}", file=sys.stderr)
        _cache.update(time=time.time(), data=data, ai=cache)
        return data


def map_images(data, fn):
    """Return a copy of data with every image's src/full rewritten by fn(url, kind)."""
    data = copy.deepcopy(data)
    for r in data["restaurants"]:
        imgs = list(r.get("weekImages", []))
        for d in r["days"].values():
            imgs += d.get("images", [])
        for im in imgs:
            im["src"], im["full"] = fn(im["src"], "src"), fn(im["full"], "full")
    return data


def proxied(url, _kind):
    return "img?u=" + urllib.parse.quote(url, safe="")


def build(out):
    """Write a static site (index.html, menus.json, img/) into `out`."""
    os.makedirs(os.path.join(out, "img"), exist_ok=True)
    data = get_menus()

    def local(url, kind):
        if kind == "full":  # zoom links point to the original, avoids storing ~5 MB files
            return url
        try:
            body, ctype = fetch_image(url)
        except Exception as e:
            print(f"image failed {url}: {e}", file=sys.stderr)
            return url
        ext = {"image/png": ".png", "image/webp": ".webp", "image/gif": ".gif"}.get(
            ctype.split(";")[0], ".jpg")
        name = "img/" + hashlib.sha1(url.encode()).hexdigest()[:12] + ext
        with open(os.path.join(out, name), "wb") as f:
            f.write(body)
        return name

    data = map_images(data, local)
    data["static"] = True
    with open(os.path.join(out, "menus.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    if _cache.get("ai"):  # published so the next build can reuse it (AI_CACHE_URL)
        ai.save_cache(_cache["ai"], os.path.join(out, "ai.json"))
    shutil.copy(os.path.join(ROOT, "index.html"), os.path.join(out, "index.html"))
    for r in data["restaurants"]:
        print(f"{r['name']}: {len(r['days'])} days, {len(r['weekImages'])} images"
              + (" (read by AI)" if r.get("aiExtracted") else "")
              + (f" ERROR {r['error']}" if r["error"] else ""))
    if data.get("ai"):
        st = data["ai"]
        print(f"AI: {st['calls']} calls ({', '.join(st['models']) or '-'}), {st['cached']} cached"
              + "".join(f"\n  AI error: {e}" for e in st["errors"]))


class Handler(BaseHTTPRequestHandler):
    def send(self, code, body, ctype, extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(url.query)
        if url.path in ("/", "/index.html"):
            with open(os.path.join(ROOT, "index.html"), "rb") as f:
                self.send(200, f.read(), "text/html; charset=utf-8")
        elif url.path in ("/menus.json", "/api/menus"):
            data = map_images(get_menus(force="refresh" in qs), proxied)
            self.send(200, json.dumps(data, ensure_ascii=False).encode(),
                      "application/json; charset=utf-8", {"Cache-Control": "no-store"})
        elif url.path == "/img":
            src = qs.get("u", [""])[0]
            if urllib.parse.urlparse(src).hostname not in IMAGE_HOSTS:
                return self.send(403, b"forbidden", "text/plain")
            try:
                body, ctype = fetch_image(src)
                self.send(200, body, ctype, {"Cache-Control": "max-age=1800"})
            except Exception as e:
                self.send(502, str(e).encode(), "text/plain")
        else:
            self.send(404, b"not found", "text/plain")

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--build":
        build(sys.argv[2])
        sys.exit(0)
    print(f"Lunch menus: http://localhost:{PORT}")
    ThreadingHTTPServer(("", PORT), Handler).serve_forever()

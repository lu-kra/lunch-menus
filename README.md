# Obedové menu

**Live: https://lu-kra.github.io/lunch-menus/**

One page with this week's lunch menus from:

- [Route 66](https://restauracia.r66.sk/sk/menu/denne-menu#mmenu) – parsed into dishes
- [Liptov 1713](https://www.liptov1713.sk/) – parsed into dishes
- [Maytex](https://www.maytex.sk/) – weekly menu image (read into dishes by AI)
- [Hotel Klar](https://klar.sk/denne-menu/) – weekly menu image (read into dishes by AI)

## Run

```sh
python3 server.py          # http://localhost:8066  (PORT=xxxx to change)
```

Python 3 only, no dependencies. The server scrapes the sites (cached for 20 minutes, use
**⟳ Obnoviť** to force), proxies the menu images and serves `index.html`.

## Using the page

- Day tabs Po–Pi (opens on today), search box filters dishes across restaurants (diacritics-insensitive).
- Liptov 1713's permanent offer is shown alongside the daily menu.
- Click a dish to mark it as your pick for that day (saved in the browser).
- A yellow warning appears when a restaurant hasn't published the current week's menu yet.
- **💪 AI tip pre fitness** – the healthiest dish of each restaurant for a gym-goer (see below).

## Hosting (GitHub Pages)

`python3 server.py --build _site` writes a static site (`index.html`, `menus.json`, `ai.json`, `img/`).
The workflow `.github/workflows/pages.yml` deploys it on every push to `main`, on demand, and daily
at 05:17, 08:17, 10:17, 14:17 and 17:17 UTC, including weekends. In Slovakia these times are
06:17, 09:17, 11:17, 15:17 and 18:17 in winter, or one hour later in summer. GitHub may delay
scheduled runs. An overlapping run does not cancel a deployment already in progress.

## AI fitness tips (Google Gemini, free tier)

`ai.py` asks Google Gemini to estimate kcal, protein, carbs, fat and fiber of every dish and to read the
image-only menus (Maytex, Klar) into dishes. The page then scores each dish 0–100: plenty of protein
(also as a share of energy), 450–850 kcal, fat ≤ ~30 % of energy and fiber score high; fried and sweet
main dishes are penalised; soups aren't scored. The best dish of each restaurant is highlighted and listed
in the 💪 panel. The values are estimates from the dish name and portion size, not lab data.

- Get a free API key at https://aistudio.google.com/apikey and store it as the repository secret
  `GEMINI_API_KEY` (locally: `GEMINI_API_KEY=... python3 server.py`). Without a key the page works as before.
- Every dish and menu image is sent to the model only once: results are cached by a hash of the text /
  image in `ai.json`, which each build downloads from the previous deployment (`AI_CACHE_URL`) and
  publishes again (locally `.ai-cache.json`). A normal day needs only a few requests.
- The newest Gemini Flash / Flash-Lite models are picked automatically, falling back to the next one when a
  quota is used up. Override with `GEMINI_MODELS=model-a,model-b`.
- After changing the prompts in `ai.py`, bump `VERSION` to re-estimate everything.

## Adding a restaurant

Write a `parse_xxx(html)` function in `server.py` returning `{"days": {weekday: {"date", "items"}}}`
and/or `{"weekImages": [...]}`, then add an entry to `RESTAURANTS` (and its image host to `IMAGE_HOSTS`).

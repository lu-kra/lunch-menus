# Obedové menu

One page with this week's lunch menus from:

- [Route 66](https://restauracia.r66.sk/sk/menu/denne-menu#mmenu) – parsed into dishes
- [Liptov 1713](https://www.liptov1713.sk/) – parsed into dishes
- [Maytex](https://www.maytex.sk/) – weekly menu image
- [Hotel Klar](https://klar.sk/denne-menu/) – weekly menu image

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

## Adding a restaurant

Write a `parse_xxx(html)` function in `server.py` returning `{"days": {weekday: {"date", "items"}}}`
and/or `{"weekImages": [...]}`, then add an entry to `RESTAURANTS` (and its image host to `IMAGE_HOSTS`).

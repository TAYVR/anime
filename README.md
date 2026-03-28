# AnimeLek Scraper

Scrapes **https://animelek.vip** — all 83 pages of the anime catalogue, including:
- Anime metadata (title, poster, status, genres, description, studios, year)
- Episode list per anime
- Watch servers (`data-ep-url`) per episode
- Download links per episode

## 🗂️ Output Structure

```
data/
  animes.json          ← full catalogue (array of anime objects)
  episodes/
    <episode-slug>.json  ← one file per episode
state.json             ← resume state (which pages / slugs already done)
scraper.log            ← rolling log file
```

## 🚀 Local Usage

```bash
# 1. Create virtual environment
python -m venv .venv
.venv\Scripts\activate      # Windows
# source .venv/bin/activate  # Linux/Mac

# 2. Install dependencies
pip install -r requirements.txt

# 3. Run (supports resume — safe to Ctrl+C and restart)
python scraper.py
```

## 🔁 How Resume Works

`state.json` tracks:
- `last_page_scraped` — last listing page fully scraped
- `scraped_anime_slugs` — slugs whose detail page was fetched
- `scraped_episode_slugs` — slugs whose episode page was fetched
- `last_run` — UTC timestamp of last run

Just re-run `python scraper.py` — it will skip already-done items.

## 🤖 GitHub Actions

- Schedule: **every ~5h30min** (two cron schedules combined)
- Needs one secret: **`GH_PAT`** — a Personal Access Token with `repo` scope

### 🔑 Secret Keys Used

| Secret | Where to get | When used |
|--------|-------------|-----------|
| `GH_PAT` | GitHub → Settings → Developer settings → PAT (classic) → `repo` scope | Committing & pushing scraped data back to the repo |

> **No paid APIs are used.** The scraper only fetches public pages using `requests` + `BeautifulSoup`.

### Setting up GH_PAT

1. Go to https://github.com/settings/tokens/new
2. Select scope: `repo` (full control of private repositories)
3. Copy the token
4. In your repo → Settings → Secrets and variables → Actions → New secret
5. Name: `GH_PAT`, value: the token you copied

## 📦 JSON Schema

### `data/animes.json` (array)

```json
{
  "id": "abc123def456",
  "slug": "assassins-pride",
  "url": "https://animelek.vip/anime/assassins-pride/",
  "title": "Assassins Pride",
  "poster": "https://animelek.vip/uploads/posters/...",
  "status": "مكتمل",
  "description": "…قصة…",
  "genres": ["فنتازيا", "حركة"],
  "type": "TV",
  "year": "2019",
  "season": "",
  "studios": "",
  "episodes_count": 12,
  "episodes": [
    {
      "id": "xyz789",
      "slug": "assassins-pride-1-الحلقة",
      "url": "https://animelek.vip/episode/…",
      "number": "1"
    }
  ],
  "scraped_at": "2026-03-28T20:00:00"
}
```

### `data/episodes/<slug>.json`

```json
{
  "id": "xyz789",
  "slug": "assassins-pride-1-الحلقة",
  "url": "https://animelek.vip/episode/…",
  "number": "1",
  "thumbnail": "https://...",
  "watch_servers": [
    { "server": "ok ru HD", "quality": "HD", "url": "https://ok.ru/videoembed/…" },
    { "server": "google FHD", "quality": "FHD", "url": "https://drive.google.com/…" }
  ],
  "download_links": [
    { "server": "mega SD", "quality": "SD", "url": "https://mega.nz/…" },
    { "server": "mp4upload HD", "quality": "HD", "url": "https://…" }
  ],
  "scraped_at": "2026-03-28T20:01:00"
}
```

## 🛡️ Anti-Detection

- Random `User-Agent` rotation via `fake-useragent`
- Random delay: **1.5 – 3.5 seconds** between requests
- Exponential back-off on 429/5xx errors
- Up to **4 retries** per URL

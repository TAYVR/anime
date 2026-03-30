"""
AnimeLek Scraper — https://animelek.vip/
Scrapes all anime listings (83 pages), then each anime's episodes,
watch servers, and download links.

Output: data/animes.json  (full catalogue) + data/episodes/<slug>.json
State : state.json         (resume support)
"""

import os
import re
import json
import time
import random
import logging
import hashlib
import urllib.parse
import cloudscraper

from pathlib import Path
from datetime import datetime
from bs4 import BeautifulSoup
from fake_useragent import UserAgent
from tqdm import tqdm

# ─────────────────────────────────────────────────────────────────── config ──
BASE_URL      = "https://animelek.vip"
LIST_URL      = "https://animelek.vip/قائمة-الأنمي/"
TOTAL_PAGES   = 83
DATA_DIR      = Path("data")
EPISODES_DIR  = DATA_DIR / "episodes"
STATE_FILE    = Path("state.json")
ANIMES_FILE   = DATA_DIR / "animes.json"

# delays (seconds)
MIN_DELAY = 1.5
MAX_DELAY = 3.5
ERROR_DELAY = 10   # wait after an error

# retry
MAX_RETRIES = 4

# logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("scraper.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)

DATA_DIR.mkdir(parents=True, exist_ok=True)
EPISODES_DIR.mkdir(parents=True, exist_ok=True)

# ─────────────────────────────────────────────────────────────── http layer ──
ua = UserAgent()

# cloudscraper transparently handles Cloudflare JS / cookie challenges
SESSION = cloudscraper.create_scraper()

def fetch(url: str, retries: int = MAX_RETRIES) -> BeautifulSoup | None:
    """Fetch URL with retry/back-off and return parsed BeautifulSoup."""
    
    # URL encode arabic characters safely (handle case where it's already encoded)
    # unquote first to normalize, then quote the non-ascii parts
    url = urllib.parse.unquote(url)
    url = urllib.parse.quote(url, safe=':/?&=')
    
    for attempt in range(1, retries + 1):
        try:
            time.sleep(random.uniform(MIN_DELAY, MAX_DELAY))
            resp = SESSION.get(url, timeout=30)
            resp.encoding = "utf-8"
            if resp.status_code == 200:
                return BeautifulSoup(resp.text, "lxml")
            elif resp.status_code == 429:
                wait = ERROR_DELAY * attempt
                log.warning(f"Rate limited on {url}. Waiting {wait}s …")
                time.sleep(wait)
            elif resp.status_code in (403, 404):
                log.warning(f"HTTP {resp.status_code} for {url}. Skipping.")
                return None
            else:
                log.warning(f"HTTP {resp.status_code} for {url}. Attempt {attempt}/{retries}.")
        except Exception as e:
            log.error(f"Request error ({attempt}/{retries}) for {url}: {e}")
            time.sleep(ERROR_DELAY * attempt)
    log.error(f"Failed to fetch {url} after {retries} attempts.")
    return None


# ─────────────────────────────────────────────────────────────────── state  ──
def load_state() -> dict:
    if STATE_FILE.exists():
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "last_page_scraped": 0,
        "scraped_anime_slugs": [],
        "scraped_episode_ids": [],
        "last_run": None,
    }


def save_state(state: dict):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


# ─────────────────────────────────────────────────────────── data helpers  ──
def load_json(path: Path) -> list | dict:
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def save_json(path: Path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def slug_from_url(url: str) -> str:
    """Extract slug from animelek URLs."""
    return url.rstrip("/").split("/")[-1]


def make_id(url: str) -> str:
    """Stable short ID from URL."""
    return hashlib.md5(url.encode()).hexdigest()[:12]


# ──────────────────────────────────────────────── page 1: list scraping  ───
def parse_anime_card(card) -> dict | None:
    """Parse a single card div into a dict."""
    try:
        # 1. find link (the slug and main url)
        # Look for a.overlay or basically any link to /anime/
        link_tag = card.select_one("a[href*='/anime/']")
        if not link_tag:
            return None

        url = link_tag.get("href", "").strip()
        if not url.startswith("http"):
            url = BASE_URL + url if url.startswith("/") else BASE_URL + "/" + url

        # 2. find thumbnail
        img_tag = card.find("img")
        poster = ""
        if img_tag:
            poster = img_tag.get("src") or img_tag.get("data-src") or img_tag.get("data-lazy-src") or ""

        # 3. find title
        title = ""
        # try alt from img
        if img_tag:
            title = img_tag.get("alt", "").strip()
        # try the title attribute from link
        if not title:
            title = link_tag.get("title", "").strip()
        # try to find a h3 or h2 or .title
        if not title:
            title_el = card.find(["h3", "h2", "h1"]) or card.select_one(".title, .anime-card-title")
            if title_el:
                title = title_el.get_text(strip=True)

        # 4. find status
        status_el = card.select_one(".anime-card-status, .status, [class*='status']")
        status = status_el.get_text(strip=True) if status_el else ""

        # clean title: remove " انمي" suffix
        title = re.sub(r"\s+انمي\s*$", "", title).strip()

        slug = slug_from_url(url)

        return {
            "id":     make_id(url),
            "slug":   slug,
            "url":    url,
            "title":  title,
            "poster": poster,
            "status": status,
        }
    except Exception as e:
        log.warning(f"  Card parse error for {getattr(card, 'name', 'tag')}: {e}")
        return None


def scrape_list_page(page: int) -> list[dict]:
    """Scrape one listing page and return list of anime stubs."""
    url = LIST_URL if page == 1 else f"{LIST_URL}?page={page}"
    soup = fetch(url)
    if not soup:
        log.error(f"  Page {page}: Failed to fetch or empty response.")
        return []

    # Try multiple selectors — site may vary
    cards = (
        soup.select(".anime-card-container")
        or soup.select(".anime-card-poster")
        or soup.select(".anime-card")
        or soup.select("[class*='anime-card']") 
    )

    if cards:
        log.info(f"  Page {page}: Found {len(cards)} elements matching card selectors.")
    else:
        log.warning(f"  Page {page}: No elements matched card selectors. Trying fallback...")
        # Fallback: find all links to /anime/ that are likely cards
        # We look for links to /anime/ that contain an image
        links = soup.select("a[href*='/anime/']")
        potential_cards = []
        for a in links:
            parent = a.find_parent("div")
            if parent and parent.find("img"):
                potential_cards.append(parent)
        
        # Unique cards by object ID
        seen = set()
        cards = []
        for c in potential_cards:
            if id(c) not in seen:
                seen.add(id(c))
                cards.append(c)
        log.info(f"  Page {page}: Fallback found {len(cards)} potential card containers.")

    results = []
    for c in cards:
        anime = parse_anime_card(c)
        if anime:
            results.append(anime)
        else:
            log.debug("  A card failed to parse.")

    log.info(f"  Page {page}: {len(results)} anime successfully parsed.")
    return results


# ─────────────────────────────────────── page 2: anime detail scraping  ───
def parse_anime_detail(soup: BeautifulSoup, stub: dict) -> dict:
    """Enrich anime stub with metadata from the anime detail page."""
    anime = stub.copy()

    # ── title (native from <h1>) ─────────────────────────────────────────
    h1 = soup.find("h1")
    if h1:
        anime["title"] = h1.get_text(strip=True)

    # ── description ──────────────────────────────────────────────────────
    desc_el = soup.select_one("p.anime-story") or soup.select_one(".anime-story p") or soup.select_one(".anime-story")
    anime["description"] = desc_el.get_text(strip=True) if desc_el else ""

    # ── metadata table (genre, type, year, studios, …) ──────────────────
    meta: dict = {}
    # Preferred: .full-list-info as found by subagent
    # Fallback: .anime-info li
    meta_rows = soup.select(".anime-container-infos .full-list-info") or soup.select(".anime-info li")
    
    for row in meta_rows:
        label_el = row.find("span") or row.find("strong")
        if not label_el:
            continue
        label = label_el.get_text(strip=True).strip(":")
        value = row.get_text(strip=True).replace(label, "").strip().strip(":")
        meta[label] = value

    # common keys used on the site (Arabic)
    anime["genres"]    = [a.get_text(strip=True) for a in soup.select("a[href*='/anime-genre/'], .anime-genres a")]
    anime["type"]      = meta.get("النوع",  meta.get("Type", ""))
    anime["year"]      = meta.get("السنة",  meta.get("Year", ""))
    anime["season"]    = meta.get("الموسم", meta.get("Season", ""))
    anime["studios"]   = meta.get("الإستوديو", meta.get("Studio", ""))
    anime["status"]    = stub.get("status") or meta.get("الحالة", meta.get("Status", ""))

    # Try to get the poster from og:image if not set
    if not anime.get("poster"):
        og = soup.find("meta", property="og:image")
        if og:
            anime["poster"] = og.get("content", "")

    # ── episodes list ────────────────────────────────────────────────────
    episode_links = []
    seen = set()
    
    # Try multiple selectors for episodes links
    ep_selectors = [
        ".episodes-card-container a.overlay", 
        ".episodes-card a.overlay",
        ".ep-card-anime-title-detail a",
        "a[href*='/episode/']"
    ]
    
    for selector in ep_selectors:
        for a in soup.select(selector):
            href = a.get("href", "").strip()
            if href and href not in seen:
                seen.add(href)
                ep_slug = slug_from_url(href)
                # extract episode number from slug
                ep_num = _extract_ep_number(ep_slug, a)
                episode_links.append({
                    "id":     make_id(href),
                    "slug":   ep_slug,
                    "url":    href,
                    "number": ep_num,
                })

    anime["episodes_count"] = len(episode_links)
    anime["episodes"]       = episode_links
    anime["scraped_at"]     = datetime.utcnow().isoformat()
    return anime


def _extract_ep_number(slug: str, tag) -> str:
    """Try to extract episode number from slug or anchor text."""
    # From link text
    text = tag.get_text(strip=True)
    m = re.search(r"(\d+)", text)
    if m:
        return m.group(1)
    # From slug  e.g. assassins-pride-3-الحلقة
    m = re.search(r"-(\d+)-", slug)
    if m:
        return m.group(1)
    return ""


def scrape_anime_detail(stub: dict) -> dict:
    """Fetch & parse the anime detail page."""
    soup = fetch(stub["url"])
    if not soup:
        return stub
    return parse_anime_detail(soup, stub)


# ─────────────────────────────────────── page 3: episode detail scraping  ──
def parse_server_links(soup: BeautifulSoup, section_id: str) -> list[dict]:
    """Parse watch-servers or download-links list."""
    section = soup.find("div", id=section_id)
    if not section:
        return []

    links = []
    for li in section.select("li.watch"):
        a = li.find("a")
        if not a:
            continue
        # quality from class  e.g. "watch -HD"
        quality = " ".join(
            c.lstrip("-") for c in li.get("class", []) if c.startswith("-")
        )
        # server name from text
        server   = a.get_text(strip=True)
        data_url = a.get("data-ep-url", "").strip()
        href     = a.get("href", "").strip()
        url      = data_url or href

        if url and url not in ("#", ""):
            links.append({
                "server":  server,
                "quality": quality,
                "url":     url,
            })
    return links


def scrape_episode(ep_stub: dict) -> dict:
    """Fetch episode page and extract watch servers + download links."""
    ep = ep_stub.copy()
    soup = fetch(ep["url"])
    if not soup:
        ep["watch_servers"]    = []
        ep["download_links"]   = []
        ep["scraped_at"]       = datetime.utcnow().isoformat()
        return ep

    ep["watch_servers"]  = parse_server_links(soup, "watch")
    ep["download_links"] = parse_server_links(soup, "downloads")
    ep["scraped_at"]     = datetime.utcnow().isoformat()

    # thumbnail
    thumb = soup.find("meta", property="og:image")
    if thumb:
        ep["thumbnail"] = thumb.get("content", "")

    return ep


# ──────────────────────────────────────────────────────────── main runner  ──
def run():
    state  = load_state()
    animes = {a["slug"]: a for a in load_json(ANIMES_FILE)}

    log.info("=" * 60)
    log.info("AnimeLek Scraper — starting")
    log.info(f"  Already have {len(animes)} anime in catalogue")
    log.info(f"  Last page scraped: {state['last_page_scraped']}")
    log.info("=" * 60)

    # ── PHASE 1 : scrape all listing pages to collect stubs ─────────────
    start_page = state["last_page_scraped"] + 1
    if start_page <= TOTAL_PAGES:
        log.info(f"Phase 1: Scraping listing pages {start_page}–{TOTAL_PAGES} …")
        for page in tqdm(range(start_page, TOTAL_PAGES + 1), desc="List pages"):
            stubs = scrape_list_page(page)
            for stub in stubs:
                if stub["slug"] not in animes:
                    animes[stub["slug"]] = stub

            state["last_page_scraped"] = page
            save_state(state)

        save_json(ANIMES_FILE, list(animes.values()))
        log.info(f"Phase 1 done. Total anime in catalogue: {len(animes)}")
    else:
        log.info("Phase 1 already complete — skipping.")

    # ── PHASE 2 : enrich each anime with detail page ─────────────────────
    already_detailed = set(state.get("scraped_anime_slugs", []))
    need_detail = [a for slug, a in animes.items() if slug not in already_detailed]
    log.info(f"Phase 2: Enriching {len(need_detail)} anime pages …")

    for stub in tqdm(need_detail, desc="Anime details"):
        enriched = scrape_anime_detail(stub)
        animes[enriched["slug"]] = enriched
        state["scraped_anime_slugs"].append(enriched["slug"])

        # save incrementally every 10 anime
        if len(state["scraped_anime_slugs"]) % 10 == 0:
            save_json(ANIMES_FILE, list(animes.values()))
            save_state(state)

    save_json(ANIMES_FILE, list(animes.values()))
    save_state(state)
    log.info("Phase 2 done.")

    # ── PHASE 3 : scrape each episode ────────────────────────────────────
    already_ep = set(state.get("scraped_episode_ids", []))
    log.info(f"Phase 3: Scraping individual episodes …")

    total_eps = sum(len(a.get("episodes", [])) for a in animes.values())
    log.info(f"  Total episodes to potentially scrape: {total_eps}")

    ep_bar = tqdm(total=total_eps, desc="Episodes")
    for anime in animes.values():
        for ep_stub in anime.get("episodes", []):
            ep_id = ep_stub["id"]
            ep_bar.update(1)
            if ep_id in already_ep:
                continue

            ep_file = EPISODES_DIR / f"{ep_id}.json"
            if ep_file.exists():
                already_ep.add(ep_id)
                continue

            ep_data = scrape_episode(ep_stub)
            save_json(ep_file, ep_data)
            already_ep.add(ep_id)
            state["scraped_episode_ids"].append(ep_id)

            if len(state["scraped_episode_ids"]) % 50 == 0:
                save_state(state)

    ep_bar.close()
    save_state(state)
    log.info("Phase 3 done.")

    # ── wrap up ──────────────────────────────────────────────────────────
    state["last_run"] = datetime.utcnow().isoformat()
    save_state(state)
    log.info("=" * 60)
    log.info("Scrape complete ✓")
    log.info(f"  Anime: {len(animes)}")
    log.info(f"  Episodes: {len(already_ep)}")
    log.info("=" * 60)


if __name__ == "__main__":
    run()

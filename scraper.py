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
from concurrent.futures import ThreadPoolExecutor, as_completed

from pathlib import Path
from datetime import datetime
from bs4 import BeautifulSoup
from fake_useragent import UserAgent
from tqdm import tqdm

# ─────────────────────────────────────────────────────────────────── config ──
BASE_URL      = "https://animelek.top"
LIST_URL      = "https://animelek.top/قائمة-الأنمي/"
TOTAL_PAGES   = 84
DATA_DIR      = Path("data")
STATE_FILE    = Path("state.json")
ANIMES_FILE   = DATA_DIR / "animes.json"
ANIMES_SPLIT_PATTERN = "animes_{index}.json"
MAX_FILE_SIZE_MB = 10

# delays (seconds)
MIN_DELAY = 1.5
MAX_DELAY = 3.5
ERROR_DELAY = 10   # wait after an error

# retry
MAX_RETRIES = 4
MAX_WORKERS = 3  # Increase to 3+ for faster scraping (User requested x3)

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


# ─────────────────────────────────────────────────────────────── http layer ──
ua = UserAgent()

# cloudscraper transparently handles Cloudflare JS / cookie challenges
SESSION = cloudscraper.create_scraper()

def fetch(url: str, retries: int = MAX_RETRIES) -> BeautifulSoup | None:
    """Fetch URL with retry/back-off and return parsed BeautifulSoup."""
    
    # Auto-replace old domains from saved states
    url = url.replace("animelek.vip", "animelek.top")
    
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
def load_animes() -> list[dict]:
    """Load all anime from split files and the legacy single file."""
    all_animes = []
    
    # legacy file
    if ANIMES_FILE.exists():
        data = load_json(ANIMES_FILE)
        if isinstance(data, list):
            all_animes.extend(data)

    # split files
    files = list(DATA_DIR.glob("animes_*.json"))
    # sort files by index to keep things orderly
    def get_index(f):
        m = re.search(r"(\d+)", f.name)
        return int(m.group(1)) if m else 0
    files.sort(key=get_index)

    for f in files:
        data = load_json(f)
        if isinstance(data, list):
            all_animes.extend(data)

    # deduplicate by slug
    seen = set()
    deduped = []
    for a in all_animes:
        if a["slug"] not in seen:
            deduped.append(a)
            seen.add(a["slug"])
    return deduped


def save_animes(animes_list: list):
    """Split animes list and save into 10MB chunks efficiently without losing data on failure."""
    if not animes_list:
        return

    temp_files = []
    try:
        current_index = 1
        current_batch = []
        current_batch_bytes = 10 
        max_bytes = MAX_FILE_SIZE_MB * 1024 * 1024

        for anime in animes_list:
            item_json = json.dumps(anime, ensure_ascii=False, indent=2)
            item_bytes = len(item_json.encode("utf-8")) + 10 
            
            if current_batch_bytes + item_bytes > max_bytes and current_batch:
                path = DATA_DIR / ANIMES_SPLIT_PATTERN.format(index=current_index)
                temp_path = path.with_suffix(".tmp_new")
                save_json(temp_path, current_batch)
                temp_files.append((temp_path, path))
                
                current_index += 1
                current_batch = []
                current_batch_bytes = 10

            current_batch.append(anime)
            current_batch_bytes += item_bytes

        if current_batch:
            path = DATA_DIR / ANIMES_SPLIT_PATTERN.format(index=current_index)
            temp_path = path.with_suffix(".tmp_new")
            save_json(temp_path, current_batch)
            temp_files.append((temp_path, path))

        # Successfully written all new parts. Now swap.
        # 1. Identify all current animes_*.json files
        old_files = list(DATA_DIR.glob("animes_*.json"))
        old_files = [f for f in old_files if not f.name.endswith(".tmp_new") and not f.name.endswith(".tmp")]

        # 2. Rename new files to final names
        for temp_path, final_path in temp_files:
            if final_path.exists():
                # On Windows, replace() might fail if open. But save_json already used a .tmp.
                # Here we just move the .tmp_new to the final path.
                final_path.unlink(missing_ok=True)
            temp_path.rename(final_path)
            
        # 3. Remove any left-over old files (if we have fewer files now)
        new_names = {p.name for _, p in temp_files}
        for f in old_files:
            if f.name not in new_names:
                try: f.unlink()
                except: pass

        if ANIMES_FILE.exists():
            try: ANIMES_FILE.unlink()
            except: pass

    except Exception as e:
        log.error(f"Critical error during save_animes: {e}")
        for temp_path, _ in temp_files:
            if temp_path.exists(): 
                try: temp_path.unlink()
                except: pass


def load_json(path: Path) -> list | dict:
    if not path.exists():
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log.warning(f"File {path.name} could not be loaded: {e}. Returning empty.")
        return []


def save_json(path: Path, data):
    """Save JSON atomically to avoid corruption upon interrupt."""
    temp_path = path.with_suffix(".tmp")
    try:
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        # atomic rename
        if path.exists():
            path.unlink()
        temp_path.rename(path)
    except Exception as e:
        log.error(f"Failed to save {path.name}: {e}")
        if temp_path.exists():
            temp_path.unlink()


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
        if img_tag:
            title = img_tag.get("alt", "").strip()
        if not title:
            title = link_tag.get("title", "").strip()
        if not title:
            title_el = card.find(["h3", "h2", "h1"]) or card.select_one(".title, .anime-card-title")
            if title_el:
                title = title_el.get_text(strip=True)

        # 4. find status
        status_el = card.select_one(".anime-card-status, .status, [class*='status']")
        status = status_el.get_text(strip=True) if status_el else ""

        # clean title
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
        log.warning(f"  Card parse error: {e}")
        return None


def scrape_list_page(page: int) -> list[dict]:
    """Scrape one listing page and return list of anime stubs."""
    url = LIST_URL if page == 1 else f"{LIST_URL}?page={page}"
    soup = fetch(url)
    if not soup:
        log.error(f"  Page {page}: Failed to fetch.")
        return []

    cards = (
        soup.select(".anime-card-container")
        or soup.select(".anime-card-poster")
        or soup.select(".anime-card")
        or soup.select("[class*='anime-card']") 
    )

    if not cards:
        links = soup.select("a[href*='/anime/']")
        potential_cards = []
        for a in links:
            parent = a.find_parent("div")
            if parent and parent.find("img"):
                potential_cards.append(parent)
        
        seen = set()
        cards = []
        for c in potential_cards:
            if id(c) not in seen:
                seen.add(id(c))
                cards.append(c)

    results = []
    for c in cards:
        anime = parse_anime_card(c)
        if anime:
            results.append(anime)

    log.info(f"  Page {page}: {len(results)} anime parsed.")
    return results


# ─────────────────────────────────────── page 2: anime detail scraping  ───
def parse_anime_detail(soup: BeautifulSoup, stub: dict) -> dict:
    """Enrich anime stub with metadata from the anime detail page."""
    anime = stub.copy()

    h1 = soup.find("h1")
    if h1:
        anime["title"] = h1.get_text(strip=True)

    desc_el = soup.select_one("p.anime-story") or soup.select_one(".anime-story p") or soup.select_one(".anime-story")
    anime["description"] = desc_el.get_text(strip=True) if desc_el else ""

    meta: dict = {}
    meta_rows = soup.select(".anime-container-infos .full-list-info") or soup.select(".anime-info li")
    
    for row in meta_rows:
        label_el = row.find("span") or row.find("strong")
        if not label_el: continue
        label = label_el.get_text(strip=True).strip(":")
        value = row.get_text(strip=True).replace(label, "").strip().strip(":")
        meta[label] = value

    anime["genres"]    = [a.get_text(strip=True) for a in soup.select("a[href*='/anime-genre/'], .anime-genres a")]
    anime["type"]      = meta.get("النوع",  meta.get("Type", ""))
    anime["year"]      = meta.get("السنة",  meta.get("Year", ""))
    anime["season"]    = meta.get("الموسم", meta.get("Season", ""))
    anime["studios"]   = meta.get("الإستوديو", meta.get("Studio", ""))
    anime["status"]    = stub.get("status") or meta.get("الحالة", meta.get("Status", ""))

    if not anime.get("poster"):
        og = soup.find("meta", property="og:image")
        if og: anime["poster"] = og.get("content", "")

    episode_links = []
    seen = set()
    ep_selectors = [".episodes-card-container a.overlay", ".episodes-card a.overlay", "a[href*='/episode/']"]
    
    for selector in ep_selectors:
        for a in soup.select(selector):
            href = a.get("href", "").strip()
            if href and href not in seen:
                seen.add(href)
                ep_slug = slug_from_url(href)
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
    text = tag.get_text(strip=True)
    m = re.search(r"(\d+)", text)
    if m: return m.group(1)
    m = re.search(r"-(\d+)-", slug)
    if m: return m.group(1)
    return ""


def scrape_anime_detail(stub: dict) -> dict:
    soup = fetch(stub["url"])
    return parse_anime_detail(soup, stub) if soup else stub


# ─────────────────────────────────────── page 3: episode detail scraping  ──
def parse_server_links(soup: BeautifulSoup, section_id: str) -> list[dict]:
    section = soup.find("div", id=section_id)
    if not section: return []
    links = []
    for li in section.select("li.watch"):
        a = li.find("a")
        if not a: continue
        quality = " ".join(c.lstrip("-") for c in li.get("class", []) if c.startswith("-"))
        server   = a.get_text(strip=True)
        url      = a.get("data-ep-url", "").strip() or a.get("href", "").strip()
        if url and url not in ("#", ""):
            links.append({"server": server, "quality": quality, "url": url})
    return links


def scrape_episode(ep_stub: dict) -> dict:
    ep = ep_stub.copy()
    soup = fetch(ep["url"])
    if not soup:
        ep["watch_servers"] = []; ep["download_links"] = []; return ep
    ep["watch_servers"]  = parse_server_links(soup, "watch")
    ep["download_links"] = parse_server_links(soup, "downloads")
    ep["scraped_at"]     = datetime.utcnow().isoformat()
    thumb = soup.find("meta", property="og:image")
    if thumb: ep["thumbnail"] = thumb.get("content", "")
    return ep


# ──────────────────────────────────────────────────────────── main runner  ──
def run():
    state  = load_state()
    animes = {a["slug"]: a for a in load_animes()}

    log.info("=" * 60)
    log.info("AnimeLek Scraper — starting")
    log.info(f"  Catalogue: {len(animes)} anime")
    log.info(f"  Last Page: {state['last_page_scraped']}")
    log.info("=" * 60)

    # ── PHASE 1 : collect anime stubs ───────────────────────────────────
    if state["last_page_scraped"] < TOTAL_PAGES:
        log.info(f"Phase 1: Resuming from page {state['last_page_scraped'] + 1} …")
        
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            pages = range(state["last_page_scraped"] + 1, TOTAL_PAGES + 1)
            f_to_p = {executor.submit(scrape_list_page, p): p for p in pages}
            
            for f in tqdm(as_completed(f_to_p), total=len(f_to_p), desc="List pages"):
                p = f_to_p[f]
                try:
                    stubs = f.result()
                    for s in stubs:
                        if s["slug"] not in animes: animes[s["slug"]] = s
                    
                    # Update progress
                    if p > state["last_page_scraped"]:
                        state["last_page_scraped"] = p
                        # Save state more frequently in Phase 1
                        if p % 5 == 0 or p == TOTAL_PAGES:
                            save_state(state)
                            save_animes(list(animes.values()))
                except Exception as e:
                    log.error(f"Error page {p}: {e}")

        save_animes(list(animes.values()))
        save_state(state)
        log.info(f"Phase 1 done. Total: {len(animes)}")
    else:
        log.info("Phase 1 already complete.")

    # ── PHASE 2 : enrich metadata ────────────────────────────────────────
    already_detailed = set(state.get("scraped_anime_slugs", []))
    need_detail = [a for s, a in animes.items() if s not in already_detailed or "description" not in a]
    
    if need_detail:
        log.info(f"Phase 2: Enriching {len(need_detail)} anime …")
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            f_to_a = {executor.submit(scrape_anime_detail, s): s for s in need_detail}
            for i, f in enumerate(tqdm(as_completed(f_to_a), total=len(f_to_a), desc="Details"), 1):
                try:
                    res = f.result()
                    animes[res["slug"]] = res
                    state["scraped_anime_slugs"].append(res["slug"])
                    if i % 20 == 0:
                        save_animes(list(animes.values()))
                        save_state(state)
                except Exception as e: log.error(f"Error detail: {e}")

        save_animes(list(animes.values()))
        save_state(state)
    log.info("Phase 2 done.")

    # ── PHASE 3 : scrape episodes ────────────────────────────────────────
    already_ep = set(state.get("scraped_episode_ids", []))
    tasks = []
    for s, a in animes.items():
        for i, ep in enumerate(a.get("episodes", [])):
            if ep["id"] not in already_ep or not ep.get("watch_servers"):
                tasks.append((s, i, ep))

    if tasks:
        log.info(f"Phase 3: Scraping {len(tasks)} episodes …")
        ep_bar = tqdm(total=len(tasks) + len(already_ep), initial=len(already_ep), desc="Episodes")
        
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            f_to_e = {executor.submit(scrape_episode, t[2]): t for t in tasks}
            for i, f in enumerate(as_completed(f_to_e), 1):
                try:
                    slug, idx, _ = f_to_e[f]
                    data = f.result()
                    if slug in animes:
                        animes[slug]["episodes"][idx].update(data)
                    if data["id"] not in already_ep:
                        state["scraped_episode_ids"].append(data["id"])
                        already_ep.add(data["id"])
                    ep_bar.update(1)

                    if i % 50 == 0: # Save more frequently (every 50 episodes)
                        save_state(state)
                        save_animes(list(animes.values()))
                except Exception as e: 
                    log.error(f"Error episode: {e}")
                    ep_bar.update(1)
        ep_bar.close()
        save_state(state)
        save_animes(list(animes.values()))

    state["last_run"] = datetime.utcnow().isoformat()
    save_state(state)
    log.info("Scrape complete ✓")



if __name__ == "__main__":
    run()

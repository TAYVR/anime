import requests
from bs4 import BeautifulSoup
import json
import os
import time
import random

# Use environment variable for security in GitHub Actions
TMDB_API_KEY = os.getenv("TMDB_API_KEY", "882e741f7283dc9ba1654d4692ec30f6")
BASE_URL = "https://anime3rb.com"
LIST_URL = "https://anime3rb.com/titles/list"

STATE_FILE = "state.json"
DATA_FILE = "anime_data.json"

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0",
    "Mozilla/5.0 (AppleWebKit/537.36; Like Gecko; Chrome/122.0.0.0 Safari/537.36)  Edge/122.0.2365.52"
]

def get_request(url, retries=5, delay=5, is_json=False):
    for i in range(retries):
        headers = {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Accept-Encoding": "gzip, deflate, br",
            "DNT": "1",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Cache-Control": "max-age=0",
        }
        try:
            # Use a session to persist cookies
            session = requests.Session()
            response = session.get(url, headers=headers, timeout=20)
            
            if response.status_code == 403:
                print(f"  [403] Forbidden at {url}. Retrying ({i+1}/{retries}) with different UA and longer delay...")
                time.sleep(delay * (i + 1) + random.uniform(1, 5))
                continue
                
            response.raise_for_status()
            return response.json() if is_json else response.text
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 503:
                print(f"  [503] {url} - Retrying ({i+1}/{retries}) after {delay}s...")
                time.sleep(delay)
                delay *= 2
            else:
                print(f"HTTP error fetching {url}: {e}")
                if i < retries - 1:
                    time.sleep(delay)
                    continue
                break
        except Exception as e:
            print(f"Error fetching {url}: {e}")
            time.sleep(delay)
    return None

def get_soup(url):
    html = get_request(url)
    return BeautifulSoup(html, 'html.parser') if html else None

def get_tmdb_id(title):
    search_url = f"https://api.themoviedb.org/3/search/multi?api_key={TMDB_API_KEY}&query={title}"
    data = get_request(search_url, is_json=True)
    return data['results'][0]['id'] if data and data.get('results') and len(data['results']) > 0 else None

def fetch_episode_data(ep_url):
    soup = get_soup(ep_url)
    if not soup: return None
    
    data = {"watch_frame": None, "download_links": []}
    video_tag = soup.find('video', id='video_html5_api')
    if video_tag: data['watch_frame'] = video_tag.get('src')
    
    sections = soup.find_all('div', attrs={'wire:key': lambda x: x and x.startswith('download.')})
    for section in sections:
        labels = section.find_all('label')
        btns = section.find_all('a', href=lambda x: x and '/download/' in x)
        for label, btn in zip(labels, btns):
            data['download_links'].append({"quality": label.get_text(strip=True), "url": btn['href']})
    return data

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, 'r') as f:
            return json.load(f)
    return {"current_page": 1}

def save_state(state):
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f)

def scrape_anime():
    # Load existing data
    try:
        if os.path.exists(DATA_FILE):
            with open(DATA_FILE, 'r', encoding='utf-8') as f:
                all_anime_data = json.load(f)
        else:
            all_anime_data = []
    except:
        all_anime_data = []

    existing_urls = {item['url'] for item in all_anime_data}
    state = load_state()
    page = state.get("current_page", 1)

    animes_processed_this_run = 0
    MAX_ANIMES_PER_RUN = 15 # Lowered to be more stealthy

    while animes_processed_this_run < MAX_ANIMES_PER_RUN:
        print(f"Scraping page {page}...")
        soup = get_soup(f"{LIST_URL}?page={page}")
        if not soup:
            print("Failed to load page. Ending run.")
            break
        
        cards = soup.find_all('div', class_='my-2', attrs={'wire:key': True})
        if not cards:
            # Fallback to general title-card search
            cards = soup.find_all('div', class_='title-card')
            
        if not cards:
            print("No more anime found. Resetting state to page 1 for next cycle.")
            state["current_page"] = 1
            save_state(state)
            break
            
        for card in cards:
            if animes_processed_this_run >= MAX_ANIMES_PER_RUN:
                break

            link_tag = card.find('a', href=lambda x: x and '/titles/' in x)
            if not link_tag: continue
            
            anime_url = link_tag['href']
            if anime_url in existing_urls:
                continue

            title_tag = card.find('h2', class_='title-name')
            if not title_tag:
                 title_tag = card.find('h2', class_='text-[1.08rem]')
            
            title = title_tag.get_text(strip=True) if title_tag else "Unknown"
            print(f"  [{animes_processed_this_run+1}/{MAX_ANIMES_PER_RUN}] Scraping: {title}")
            
            genres = [span.get_text(strip=True) for span in card.find_all('span', href=lambda x: x and '/genre/' in x)]
            season_badge = card.find('span', class_='badge')
            season = season_badge.get_text(strip=True) if season_badge else None
            synopsis_p = card.find('p', class_='synopsis')
            synopsis = synopsis_p.get_text(strip=True) if synopsis_p else None
            
            anime_soup = get_soup(anime_url)
            episodes = []
            if anime_soup:
                video_list = anime_soup.find('div', class_='video-list')
                if video_list:
                    ep_tags = video_list.find_all('a', href=lambda x: x and '/episode/' in x)
                    for ep_tag in ep_tags:
                        ep_url = ep_tag['href']
                        ep_name = ep_url.split('/')[-1]
                        print(f"    Episode: {ep_name}")
                        ep_data = fetch_episode_data(ep_url)
                        if ep_data:
                            ep_data["episode_name"] = ep_name
                            ep_data["url"] = ep_url
                            episodes.append(ep_data)
                        time.sleep(random.uniform(2, 4)) # Stealthy delay

            all_anime_data.append({
                "title": title,
                "url": anime_url,
                "tmdb_id": get_tmdb_id(title),
                "season": season,
                "genres": genres,
                "synopsis": synopsis,
                "episodes": episodes
            })
            existing_urls.add(anime_url)
            animes_processed_this_run += 1
            
            with open(DATA_FILE, 'w', encoding='utf-8') as f:
                json.dump(all_anime_data, f, ensure_ascii=False, indent=4)
            
            time.sleep(random.uniform(3, 6)) # Stealthy delay between animes

        state["current_page"] = page
        save_state(state)

        if not soup.find('a', attrs={'rel': 'next'}):
            print("No next page. All done.")
            state["current_page"] = 1
            save_state(state)
            break
            
        page += 1
        time.sleep(random.uniform(5, 10))

if __name__ == "__main__":
    scrape_anime()

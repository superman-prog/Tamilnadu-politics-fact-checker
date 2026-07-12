"""
Scavenger Scout — Tamil Nadu political fact-checking pipeline.

Harvests trending videos from TN news YouTube channels, cheaply pre-filters
them with Groq, deep-analyzes the politically-relevant survivors with Gemini
(video understanding + Google Search grounding + a brute-force DuckDuckGo
scrape), and writes Reddit/X-ready fact-check reports to ./reports.

Environment variables:
    YOUTUBE_API_KEY      required
    GEMINI_KEYS_STRING   required, comma-separated pool of Gemini API keys
    GROQ_API_KEY         required
    GEMINI_MODEL         optional, default "gemini-flash-latest"
    GROQ_FILTER_MODEL    optional, default "openai/gpt-oss-20b"
    DRY_RUN              optional, "true" to skip Gemini calls + report writes
    MAX_TRENDING_RESULTS optional, default 3
    LOOKBACK_HOURS       optional, default 48
    DB_RETENTION_DAYS    optional, default 45
"""

import os
import json
import time
import re
import random
import logging
import queue
import threading
from datetime import datetime, timedelta, timezone, date
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from groq import Groq
from google import genai
from google.genai import types
from google.genai.errors import APIError

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ==========================================
# LOGGING
# ==========================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("scavenger_scout")

# ==========================================
# CONFIGURATION & API KEYS
# ==========================================
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
GEMINI_KEYS_STRING = os.environ.get("GEMINI_KEYS_STRING")
YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY")
GROQ_FILTER_MODEL = os.environ.get("GROQ_FILTER_MODEL", "openai/gpt-oss-20b")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-flash-latest")

DRY_RUN = os.environ.get("DRY_RUN", "false").strip().lower() in ("1", "true", "yes")

MAX_TRENDING_RESULTS = int(os.environ.get("MAX_TRENDING_RESULTS", "3"))
LOOKBACK_HOURS = int(os.environ.get("LOOKBACK_HOURS", "48"))
DB_RETENTION_DAYS = int(os.environ.get("DB_RETENTION_DAYS", "45"))

MAX_GEMINI_RETRIES = 4
RETRYABLE_GEMINI_CODES = {429, 500, 503, 504}

DB_PATH = "database.json"
CHANNEL_CACHE_PATH = "channel_cache.json"

CHANNEL_HANDLES = [
    "PolimerNews", "thanthitv", "ChanakyaaTV",
    "Behindwoodstv", "Sunnewstamil", "News18Tamilnadu", "KalaignarTVNews"
]

POLITICAL_KEYWORDS = [
    "CM", "Vijay", "Stalin", "EPS", "Udhayanidhi", "Edappadi", "Seeman",
    "Annamalai", "Thirumavalavan", "DMK", "ADMK", "AIADMK", "TVK", "NTK",
    "BJP", "VCK", "Congress", "Assembly", "Election", "Karur", "Police"
]

_CURRENT_MONTH_YEAR = datetime.now().strftime("%B %Y")

PHASE_1_PROMPT = f"""
You are a hypersensitive political radar for Tamil Nadu, scanning news as of {_CURRENT_MONTH_YEAR}.
Is this video title related to Tamil Nadu politics, elections, political leaders, or government controversies?
Reply ONLY with YES or NO.
"""

LAYER_1_FORENSIC_PROMPT = f"""
You are an aggressive, high-engagement political fact-checking agent optimized for social media distribution (Reddit & X).
Analyze the video content and leverage your integrated search tools to cross-reference live metrics and expose disinformation.

EDITORIAL DIRECTIVES:
1. If the video is unavailable, non-political, or contains no verifiable factual claims to check, reply with EXACTLY the single word INSUFFICIENT_DATA and nothing else. Do not force the template below onto content that doesn't warrant it.
2. Adopt a bold, punchy, investigative tone. Use hooks like "Why is X lying?" or "Did they get a paycheck from Y?" when exposing clear falsehoods.
3. Ground all analyses in the current reality as of {_CURRENT_MONTH_YEAR} (e.g., C. Joseph Vijay/TVK governs Tamil Nadu as Chief Minister following the 2026 state elections). Treat that as a floor, not a ceiling — confirm anything more recent with your search tools before citing it.
4. You MUST output BOTH formats below separated by a clear horizontal rule (---). Do not combine them.

=========================================
[FORMAT 1: REDDIT POST OPTIMIZATION]
=========================================
### 🛰️ r/TamilNadu VIRAL RADAR | FACT CHECK: [Insert Catchy, Edgy Title - e.g., Why is X lying? Did he get his paycheck from Y?]
**Source Feed**: [Insert Video URL]

**THE QUICK BREAKDOWN:**
[Blazing-fast, 2-sentence summary of the video's core claim vs. what live web data explicitly proves.]

**🚨 THE CLAIMS VS THE REALITY:**
* **🗣️ The Claim:** "[Speaker Name]: 'Literal quote or paraphrase'"
  * **❌ Verdict:** [TRUE / MISLEADING / FALSE / PURE OPINION]
  * **🎯 Why they are lying / wrong:** A sharp, aggressive 2-sentence exposure of the factual error or political bias, utilizing current TN dynamics.

* **🗣️ The Claim:** "[Speaker Name]: 'Next major claim'"
  * **❌ Verdict:** [TRUE / MISLEADING / FALSE / PURE OPINION]
  * **🎯 Factual Counter-Evidence:** Crisp, unassailable counter-facts.

**📜 TL;DR KEY TAKEAWAYS:**
* 🔹 [High-impact summary point 1]
* 🔹 [High-impact summary point 2]

=========================================
---
=========================================
[FORMAT 2: X / TWITTER THREAD CONSTRAINTS]
=========================================
**🧵 X THREAD CONSTRAINTS (Output exactly as separate numbered posts, each under 280 characters):**

1/ 🚨 FACT CHECK: [Punchy, aggressive topic hook - Why is X lying? 👇] [Insert URL]

2/ 🗣️ CLAIM: [Speaker] states [Claim Summary]. 
❌ VERDICT: [FALSE / MISLEADING]. 
🎯 REALITY: [Ultra-short factual correction exposing the lie]. 

3/ 🗣️ CLAIM: [Speaker 2] claims [Claim 2]. 
❌ VERDICT: [FALSE / PURE OPINION]. 
🎯 REALITY: [Quick contextual takedown]. Did they get a paycheck from the opposition? 🤔

4/ 📈 WHY THIS IS TRENDING: [1-2 sentences on why this video is currently farming massive clicks across Tamil Nadu networks]. #TamilNadu #TNPolitics
"""

# ==========================================
# 🚀 PRE-SCRAPE LAYER DEFINITION
# ==========================================
def force_live_search(query_string):
    """
    THE BRUTE-FORCE HACK: Takes the choice away from the AI.
    Scrapes the live web and forcefully dumps raw snippets into the prompt text.
    Never raises — always returns a string, even on failure, so the caller
    can safely fold the result straight into the model's prompt.
    """
    try:
        time.sleep(random.uniform(0.3, 1.2))

        url = f"https://html.duckduckgo.com/html/?q={requests.utils.quote(query_string)}"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        res = requests.get(url, headers=headers, timeout=8)
        res.raise_for_status()

        snippets = re.findall(r'<a class="result__snippet"[^>]*>(.*?)</a>', res.text, re.DOTALL)
        cleaned_snippets = [re.sub(r'<[^>]+>', '', s).strip() for s in snippets[:3]]

        return " | ".join(cleaned_snippets) if cleaned_snippets else "No live snippets found."
    except Exception as e:
        log.warning(f"⚠️ force_live_search failed for '{query_string[:40]}...': {e}")
        return "No live snippets available (search layer failed)."

# ==========================================
# CORE CLASSES AND HELPER FUNCTIONS
# ==========================================
def _safe_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class VideoEntry:
    def __init__(self, video_id, title, description, statistics=None):
        self.id = video_id
        self.title = title
        self.description = description
        self.link = f"https://www.youtube.com/watch?v={video_id}"

        stats = statistics or {}
        self.views = _safe_int(stats.get('viewCount'))
        self.likes = _safe_int(stats.get('likeCount'))
        self.comments = _safe_int(stats.get('commentCount'))

        self.reach_score = self.views + (self.likes * 5) + (self.comments * 10)

    def __repr__(self):
        return f"<VideoEntry id={self.id} reach={self.reach_score} title='{self.title[:40]}'>"


def get_channel_id(handle, api_key, session):
    try:
        url = "https://www.googleapis.com/youtube/v3/channels"
        params = {"part": "id", "forHandle": handle, "key": api_key}
        response = session.get(url, params=params, timeout=10).json()

        if "error" in response:
            log.warning(f"⚠️ YouTube API error resolving @{handle}: {response['error'].get('message', 'unknown error')}")
            return None
        if response.get("items"):
            return response["items"][0]["id"]
    except requests.RequestException as e:
        log.warning(f"⚠️ Network error resolving @{handle}: {e}")
    except Exception as e:
        log.warning(f"⚠️ Channel ID resolution failed for @{handle}: {e}")
    return None


CLUSTER_STOPWORDS = {"THE", "AND", "FOR", "NEW", "TOP", "OFF", "OUT", "NOW", "ALL", "WHY", "HOW"}


def extract_cluster_key(title):
    clean = title.upper()
    clean = re.sub(r'\|.*|LIVE.*|🔴.*', '', clean)

    words = [w for w in re.findall(r'\b[A-Z0-9]{3,}\b', clean) if w not in CLUSTER_STOPWORDS]
    if words:
        return "_".join(words[:3])


    tamil_words = re.findall(r'[\u0B80-\u0BFF]{3,}', clean)
    if tamil_words:
        return "_".join(tamil_words[:3])

    return clean.strip()[:20] or "UNKNOWN"

# ==========================================
# PERSISTENT STATE (dedup database + channel-id cache)
# ==========================================
def load_processed_db():
    """Returns {video_id: date_processed_iso}. Migrates the old flat-list format."""
    if not os.path.exists(DB_PATH):
        return {}
    try:
        with open(DB_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        log.warning(f"⚠️ Could not read {DB_PATH} ({e}); starting with an empty database.")
        return {}

    if isinstance(data, list):
        today = datetime.now(timezone.utc).date().isoformat()
        return {vid: today for vid in data}
    if isinstance(data, dict):
        return data
    return {}


def prune_processed_db(db):
    """Drops entries older than DB_RETENTION_DAYS so database.json doesn't grow forever."""
    cutoff = datetime.now(timezone.utc).date() - timedelta(days=DB_RETENTION_DAYS)
    pruned = {}
    for vid, date_str in db.items():
        try:
            if date.fromisoformat(date_str) >= cutoff:
                pruned[vid] = date_str
        except (ValueError, TypeError):
            pruned[vid] = date_str  
        return pruned


def save_processed_db(db):
    try:
        with open(DB_PATH, "w", encoding="utf-8") as f:
            json.dump(db, f)
    except OSError as e:
        log.warning(f"⚠️ Could not persist {DB_PATH}: {e}")


def load_channel_cache():
    if not os.path.exists(CHANNEL_CACHE_PATH):
        return {}
    try:
        with open(CHANNEL_CACHE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save_channel_cache(cache):
    try:
        with open(CHANNEL_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(cache, f)
    except OSError as e:
        log.warning(f"⚠️ Could not persist {CHANNEL_CACHE_PATH}: {e}")

# ==========================================
# MAIN ORCHESTRATION ENGINE
# ==========================================
def run_scout():
    log.info("🛰️ Booting Master Swarm Engine (Live Search Enabled)...")
    if DRY_RUN:
        log.info("🧪 DRY_RUN is on — harvesting and Groq filtering will run, but Gemini analysis and report writes will be skipped.")

    missing = [name for name, val in [
        ("YOUTUBE_API_KEY", YOUTUBE_API_KEY),
        ("GEMINI_KEYS_STRING", GEMINI_KEYS_STRING),
        ("GROQ_API_KEY", GROQ_API_KEY),
    ] if not val]
    if missing:
        log.error(f"❌ Missing required environment variable(s): {', '.join(missing)}")
        return

    keys_list = [k.strip() for k in GEMINI_KEYS_STRING.split(",") if k.strip()]
    if not keys_list:
        log.error("❌ GEMINI_KEYS_STRING is set but contains no usable keys.")
        return

    api_key_queue = queue.Queue()
    for key in keys_list:
        api_key_queue.put(key)

    try:
        groq_client = Groq(api_key=GROQ_API_KEY)
    except Exception as e:
        log.error(f"❌ Groq API initialization failed: {e}")
        return

    os.makedirs("reports", exist_ok=True)

    db_lock = threading.Lock()
    processed_db = prune_processed_db(load_processed_db())
    channel_cache = load_channel_cache()
    cache_dirty = False

    def mark_processed(video_id):
        with db_lock:
            processed_db[video_id] = datetime.now(timezone.utc).date().isoformat()
            save_processed_db(processed_db)

    raw_discovered_entries = []
    log.info("📡 Harvesting trending arrays from source networks...")

    time_limit = (
        (datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS))
        .isoformat(timespec='seconds')
        .replace("+00:00", "Z")
    )

    with requests.Session() as session:
        for handle in CHANNEL_HANDLES:
            channel_id = channel_cache.get(handle)
            if not channel_id:
                channel_id = get_channel_id(handle, YOUTUBE_API_KEY, session)
                if channel_id:
                    channel_cache[handle] = channel_id
                    cache_dirty = True
            if not channel_id:
                log.warning(f"⚠️ Could not resolve channel ID for @{handle}, skipping.")
                continue

            try:
                url = "https://www.googleapis.com/youtube/v3/search"
                params = {
                    "part": "snippet",
                    "channelId": channel_id,
                    "type": "video",
                    "order": "viewCount",
                    "publishedAfter": time_limit,
                    "maxResults": str(MAX_TRENDING_RESULTS),
                    "key": YOUTUBE_API_KEY
                }
                search_response = session.get(url, params=params, timeout=10).json()

                if "error" in search_response:
                    log.warning(f"❌ YouTube API error searching @{handle}: {search_response['error'].get('message', 'unknown error')}")
                    continue

                items = search_response.get("items", [])
                video_ids = [item["id"]["videoId"] for item in items if item.get("id", {}).get("videoId")]
                if not video_ids:
                    continue

                stats_url = "https://www.googleapis.com/youtube/v3/videos"
                stats_params = {
                    "part": "snippet,statistics",
                    "id": ",".join(video_ids),
                    "key": YOUTUBE_API_KEY
                }
                stats_response = session.get(stats_url, params=stats_params, timeout=10).json()

                if "error" in stats_response:
                    log.warning(f"❌ YouTube API error fetching stats for @{handle}: {stats_response['error'].get('message', 'unknown error')}")
                    continue

                for item in stats_response.get("items", []):
                    snippet = item["snippet"]
                    raw_discovered_entries.append(VideoEntry(
                        item["id"],
                        snippet["title"],
                        snippet.get("description", ""),
                        item.get("statistics", {})
                    ))
                log.info(f"✅ Loaded live metrics for: @{handle}")
            except requests.RequestException as e:
                log.warning(f"❌ Network error processing @{handle}: {e}")
            except Exception as e:
                log.warning(f"❌ Failed processing metrics for @{handle}: {e}")

    if cache_dirty:
        save_channel_cache(channel_cache)

    topic_clusters = {}
    for entry in raw_discovered_entries:
        if entry.id in processed_db:
            continue

        keyword_hit = any(kw.upper() in entry.title.upper() for kw in POLITICAL_KEYWORDS)
        if not keyword_hit:
            try:
                radar = groq_client.chat.completions.create(
                    model=GROQ_FILTER_MODEL,
                    messages=[{"role": "system", "content": PHASE_1_PROMPT}, {"role": "user", "content": entry.title}],
                    temperature=0.0
                )
                if "YES" not in radar.choices[0].message.content.strip().upper():
                    mark_processed(entry.id)
                    continue
            except Exception as e:
                log.warning(f"⚠️ Phase 1 Radar Error for '{entry.title[:40]}': {e}")
                continue

        cluster_key = extract_cluster_key(entry.title)
        topic_clusters.setdefault(cluster_key, []).append(entry)

    targets_to_process = []
    for cluster_key, entries in topic_clusters.items():
        best_entry = max(entries, key=lambda x: x.reach_score)
        targets_to_process.append(best_entry)
        for entry in entries:
            if entry.id != best_entry.id:
                mark_processed(entry.id)

    if not targets_to_process:
        log.info("✅ Sweep Complete. No new viral anomalies identified.")
        return

    log.info(f"🎯 Deduplicated Targets Locked: {len(targets_to_process)}")
    log.info(f"🚀 Deploying Swarm Workers across {len(keys_list)} concurrent threads...")

    def process_target(entry):
        title, url, desc, video_id = entry.title, entry.link, entry.description, entry.id

        if DRY_RUN:
            log.info(f"🧪 [DRY RUN] Would deep-analyze: '{title[:60]}' ({entry.views} views, reach={entry.reach_score})")
            return False

        current_key = api_key_queue.get()
        try:
            client = genai.Client(api_key=current_key)

            log.info(f"🛰️ Injecting forced web data layer for topic: {title[:30]}...")
            live_web_data = force_live_search(f"{title} news Tamil Nadu {datetime.now().year}")

            payload_context = (
                f"Title Context: {title}\n"
                f"Description Context: {desc}\n"
                f"🚨 VERIFIED LIVE WEB GROUND TRUTH (FORCE-FED): {live_web_data}\n"
                f"Engagement Footprint: {entry.views} views, {entry.likes} likes."
            )

            raw_report = None
            for attempt in range(MAX_GEMINI_RETRIES):
                try:
                    res = client.models.generate_content(
                        model=GEMINI_MODEL,
                        contents=[
                            types.Part(
                                file_data=types.FileData(file_uri=url, mime_type="video/mp4"),
                                video_metadata=types.VideoMetadata(fps=0.2)
                            ),
                            types.Part.from_text(text=LAYER_1_FORENSIC_PROMPT),
                            types.Part.from_text(text=payload_context)
                        ],
                        config=types.GenerateContentConfig(
                            temperature=0.1,
                            media_resolution=types.MediaResolution.MEDIA_RESOLUTION_LOW,
                            # Keeps native grounding active alongside the force-fed data.
                            tools=[types.Tool(google_search=types.GoogleSearch())]
                        )
                    )
                    try:
                        raw_report = res.text.strip() if res.text else None
                    except Exception:
                        raw_report = None
                    break
                except APIError as e:
                    code = getattr(e, "code", None)
                    if code in RETRYABLE_GEMINI_CODES:
                        log.warning(f"⏳ HTTP {code} on '{title[:40]}' (attempt {attempt + 1}/{MAX_GEMINI_RETRIES}) — rotating key")
                        api_key_queue.put(current_key)
                        current_key = api_key_queue.get()
                        client = genai.Client(api_key=current_key)
                        time.sleep(min(2 ** attempt + random.uniform(0, 1), 20))
                    else:
                        log.error(f"❌ Model failed on '{title[:40]}': {getattr(e, 'message', e)}")
                        break
                except Exception as e:
                    log.warning(f"⚠️ Pipeline anomaly on '{title[:40]}': {e}")
                    break

            if not raw_report or "INSUFFICIENT_DATA" in raw_report:
                mark_processed(video_id)
                return False

            log.info(f"✅ Success: fact-check generated for '{title[:40]}...'")

            safe_title = re.sub(r'[\\/*?:"<>|]', "", title).strip()[:50].replace(' ', '_')
            timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            filename = f"reports/{timestamp}_{safe_title}.md"

            report_content = (
                f"# 🛰️ Grounded Social Pulse: {title}\n"
                f"**Source Video**: {url}\n\n"
                f"> *Automated OSINT Engine via Scavenger Scout Swarm*\n"
                f"> *Project Repo: https://github.com/superman-prog/Tamilnadu-politics-fact-checker*\n\n"
                f"---\n\n"
                f"{raw_report}"
            )

            with open(filename, "w", encoding="utf-8") as f:
                f.write(report_content)

            mark_processed(video_id)
            return True

        except Exception as e:
            log.error(f"💥 FATAL THREAD CRASH on '{title[:40]}': {e}")
            mark_processed(video_id)
            return False
        finally:
            api_key_queue.put(current_key)

    report_count = 0
    with ThreadPoolExecutor(max_workers=len(keys_list)) as executor:
        futures = [executor.submit(process_target, target) for target in targets_to_process]
        for future in as_completed(futures):
            try:
                if future.result():
                    report_count += 1
            except Exception as e:
                log.warning(f"⚠️ Unhandled Executor Exception: {e}")

    log.info(f"✅ Swarm Complete. {report_count}/{len(targets_to_process)} targets produced fact-check reports.")


if __name__ == "__main__":
    run_scout()

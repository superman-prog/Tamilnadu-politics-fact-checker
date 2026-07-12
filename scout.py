"""
Scavenger Scout — Tamil Nadu political fact-checking pipeline.
Hardened Sequential Edition.
"""

import os
import json
import time
import re
import random
import logging
import urllib.parse
from datetime import datetime, timedelta, timezone, date

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
1. If the video is unavailable, non-political, or contains no verifiable factual claims to check, reply with EXACTLY the single word INSUFFICIENT_DATA and nothing else.
2. Adopt a bold, punchy, investigative tone. Use hooks like "Why is X lying?" or "Did they get a paycheck from Y?" when exposing clear falsehoods.
3. Ground all analyses in the current reality as of {_CURRENT_MONTH_YEAR} (e.g., C. Joseph Vijay/TVK governs Tamil Nadu as Chief Minister following the 2026 state elections).
4. You MUST output BOTH formats below separated by a clear horizontal rule (---). Do not combine them.

=========================================
[FORMAT 1: REDDIT POST OPTIMIZATION]
=========================================
### 🛰️ r/TamilNadu VIRAL RADAR | FACT CHECK: [Insert Catchy, Edgy Title]
**Source Feed**: [Insert Video URL]

**THE QUICK BREAKDOWN:**
[Blazing-fast summary of the video's core claim vs. live web data.]

**🚨 THE CLAIMS VS THE REALITY:**
* **🗣️ The Claim:** "[Speaker Name]: 'Literal quote or paraphrase'"
  * **❌ Verdict:** [TRUE / MISLEADING / FALSE / PURE OPINION]
  * **🎯 Why they are lying / wrong:** Dynamic context analysis.

**📜 TL;DR KEY TAKEAWAYS:**
* 🔹 [Summary point 1]

=========================================
---
=========================================
[FORMAT 2: X / TWITTER THREAD CONSTRAINTS]
=========================================
1/ 🚨 FACT CHECK: Why is X lying? 👇 [Insert URL]
"""

# ==========================================
# 🚀 PRE-SCRAPE LAYER DEFINITION
# ==========================================
def force_live_search(query_string):
    try:
        url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(query_string)}"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        res = requests.get(url, headers=headers, timeout=10)
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

def get_channel_id(handle, api_key, session):
    try:
        url = "https://www.googleapis.com/youtube/v3/channels"
        params = {"part": "id", "forHandle": handle, "key": api_key}
        response = session.get(url, params=params, timeout=10).json()
        if response.get("items"):
            return response["items"][0]["id"]
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
# PERSISTENT STATE MANAGEMENT
# ==========================================
def load_processed_db():
    if not os.path.exists(DB_PATH):
        return {}
    try:
        with open(DB_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            today = datetime.now(timezone.utc).date().isoformat()
            return {vid: today for vid in data}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}

def prune_processed_db(db):
    cutoff = datetime.now(timezone.utc).date() - timedelta(days=DB_RETENTION_DAYS)
    pruned = {}
    for vid, date_str in db.items():
        try:
            if date.fromisoformat(date_str) >= cutoff:
                pruned[vid] = date_str
        except Exception:
            pruned[vid] = date_str  
    return pruned # 🚀 CRITICAL FIX: Shifted outside loop block so database elements load successfully

def save_processed_db(db):
    try:
        with open(DB_PATH, "w", encoding="utf-8") as f:
            json.dump(db, f)
    except OSError as e:
        log.warning(f"⚠️ Could not persist database: {e}")

def load_channel_cache():
    if not os.path.exists(CHANNEL_CACHE_PATH):
        return {}
    try:
        with open(CHANNEL_CACHE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_channel_cache(cache):
    try:
        with open(CHANNEL_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(cache, f)
    except OSError as e:
        log.warning(f"⚠️ Could not persist channel cache: {e}")

# ==========================================
# MAIN ORCHESTRATION ENGINE
# ==========================================
def run_scout():
    log.info("🛰️ Booting Hardened Sequential Engine...")
    
    if not all([YOUTUBE_API_KEY, GEMINI_KEYS_STRING, GROQ_API_KEY]):
        log.error("❌ Missing required environment variables.")
        return

    keys_list = [k.strip() for k in GEMINI_KEYS_STRING.split(",") if k.strip()]
    if not keys_list:
        log.error("❌ No valid Gemini keys discovered.")
        return
    key_index = 0

    try:
        groq_client = Groq(api_key=GROQ_API_KEY)
    except Exception as e:
        log.error(f"❌ Groq initialization failed: {e}")
        return

    os.makedirs("reports", exist_ok=True)
    processed_db = prune_processed_db(load_processed_db())
    channel_cache = load_channel_cache()
    cache_dirty = False

    raw_discovered_entries = []
    time_limit = ((datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)).isoformat(timespec='seconds').replace("+00:00", "Z"))

    with requests.Session() as session:
        for handle in CHANNEL_HANDLES:
            channel_id = channel_cache.get(handle)
            if not channel_id:
                channel_id = get_channel_id(handle, YOUTUBE_API_KEY, session)
                if channel_id:
                    channel_cache[handle] = channel_id
                    cache_dirty = True
            if not channel_id: continue

            try:
                url = "https://www.googleapis.com/youtube/v3/search"
                params = {
                    "part": "snippet", "channelId": channel_id, "type": "video",
                    "order": "viewCount", "publishedAfter": time_limit,
                    "maxResults": str(MAX_TRENDING_RESULTS), "key": YOUTUBE_API_KEY
                }
                search_response = session.get(url, params=params, timeout=10).json()
                items = search_response.get("items", [])
                video_ids = [item["id"]["videoId"] for item in items if item.get("id", {}).get("videoId")]
                if not video_ids: continue

                stats_url = "https://www.googleapis.com/youtube/v3/videos"
                stats_params = {"part": "snippet,statistics", "id": ",".join(video_ids), "key": YOUTUBE_API_KEY}
                stats_response = session.get(stats_url, params=stats_params, timeout=10).json()

                for item in stats_response.get("items", []):
                    snippet = item["snippet"]
                    raw_discovered_entries.append(VideoEntry(
                        item["id"], snippet["title"], snippet.get("description", ""), item.get("statistics", {})
                    ))
                log.info(f"✅ Loaded live metrics for: @{handle}")
            except Exception as e:
                log.warning(f"❌ Metrics fail for @{handle}: {e}")

    if cache_dirty:
        save_channel_cache(channel_cache)

    topic_clusters = {}
    for entry in raw_discovered_entries:
        if entry.id in processed_db: continue

        keyword_hit = any(kw.upper() in entry.title.upper() for kw in POLITICAL_KEYWORDS)
        if not keyword_hit:
            try:
                radar = groq_client.chat.completions.create(
                    model=GROQ_FILTER_MODEL,
                    messages=[{"role": "system", "content": PHASE_1_PROMPT}, {"role": "user", "content": entry.title}],
                    temperature=0.0
                )
                if "YES" not in radar.choices[0].message.content.strip().upper():
                    processed_db[entry.id] = datetime.now(timezone.utc).date().isoformat()
                    continue
            except Exception:
                continue

        cluster_key = extract_cluster_key(entry.title)
        topic_clusters.setdefault(cluster_key, []).append(entry)

    targets_to_process = []
    for cluster_key, entries in topic_clusters.items():
        best_entry = max(entries, key=lambda x: x.reach_score)
        targets_to_process.append(best_entry)
        for entry in entries:
            if entry.id != best_entry.id:
                processed_db[entry.id] = datetime.now(timezone.utc).date().isoformat()

    save_processed_db(processed_db)

    if not targets_to_process:
        log.info("✅ Sweep Complete. No new viral anomalies identified.")
        return

    log.info(f"🎯 Targets Locked: {len(targets_to_process)}. Running sequentially to prevent API crashes...")
    report_count = 0

    for entry in targets_to_process:
        title, url, desc, video_id = entry.title, entry.link, entry.description, entry.id

        if DRY_RUN:
            log.info(f"🧪 [DRY RUN] Skipping analysis for: '{title[:40]}'")
            continue

        log.info(f"\n🎬 Processing Target: '{title[:50]}'")
        live_web_data = force_live_search(f"{title} news Tamil Nadu {datetime.now().year}")

        payload_context = (
            f"Title Context: {title}\nDescription Context: {desc}\n"
            f"🚨 VERIFIED LIVE WEB GROUND TRUTH (FORCE-FED): {live_web_data}\n"
            f"Engagement Footprint: {entry.views} views, {entry.likes} likes."
        )

        success = False
        for attempt in range(MAX_GEMINI_RETRIES):
            current_key = keys_list[key_index]
            client = genai.Client(api_key=current_key)
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
                        tools=[types.Tool(google_search=types.GoogleSearch())]
                    )
                )
                
                raw_report = res.text.strip() if (res and res.text) else None
                
                if not raw_report or "INSUFFICIENT_DATA" in raw_report:
                    log.warning(f"⏭️ Insufficient context or skipped for target: {title[:30]}")
                    processed_db[video_id] = datetime.now(timezone.utc).date().isoformat()
                    save_processed_db(processed_db)
                    success = True
                    break

                safe_title = re.sub(r'[\\/*?:"<>|]', "", title).strip()[:50].replace(' ', '_')
                filename = f"reports/{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}_{safe_title}.md"
                
                with open(filename, "w", encoding="utf-8") as f:
                    f.write(f"# 🛰️ Grounded Social Pulse: {title}\n**Source Video**: {url}\n\n---\n\n{raw_report}")
                
                log.info(f"✅ Success: fact-check generated: {filename}")
                processed_db[video_id] = datetime.now(timezone.utc).date().isoformat()
                save_processed_db(processed_db)
                report_count += 1
                success = True
                
                # 🚀 Safe spacing between video runs to keep RPM green
                time.sleep(15)
                break

            except APIError as e:
                if getattr(e, 'code', None) == 429:
                    key_index = (key_index + 1) % len(keys_list)
                    log.warning(f"⏳ Rate limited (429) on key slot [{key_index}]. Cool-down backing off for 35s...")
                    time.sleep(35)
                else:
                    log.error(f"❌ Model failed: {getattr(e, 'message', str(e))}")
                    break
            except Exception as e:
                log.error(f"⚠️ Pipeline anomaly: {e}")
                break

        if not success:
            log.warning(f"⏭️ Max retries hit. Marking {video_id} processed to avoid looping.")
            processed_db[video_id] = datetime.now(timezone.utc).date().isoformat()
            save_processed_db(processed_db)

    log.info(f"✅ Run Complete. {report_count}/{len(targets_to_process)} target files generated.")

if __name__ == "__main__":
    run_scout()

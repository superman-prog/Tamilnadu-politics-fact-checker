import os
import json
import time
import re
import queue
import threading
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
from groq import Groq
from google import genai
from google.genai import types
from google.genai.errors import APIError

# --- CONFIGURATION & API KEYS ---
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
GEMINI_KEYS_STRING = os.environ.get("GEMINI_KEYS_STRING")
YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY")

CHANNEL_HANDLES = [
    "PolimerNews", "thanthitv", "ChanakyaaTV",
    "Behindwoodstv", "Sunnewstamil", "News18Tamilnadu", "KalaignarTVNews"
]

# Pull the top 3 highest-performing/trending videos per channel per run
MAX_TRENDING_RESULTS = 3

POLITICAL_KEYWORDS = [
    "CM", "Vijay", "Stalin", "EPS", "Udhayanidhi", "Edappadi", "Seeman",
    "Annamalai", "Thirumavalavan", "DMK", "ADMK", "AIADMK", "TVK", "NTK",
    "BJP", "VCK", "Congress", "Assembly", "Election", "Karur", "Police"
]

PHASE_1_PROMPT = """
You are a hypersensitive political radar for Tamil Nadu in July 2026. 
Is this video title related to Tamil Nadu politics, elections, political leaders, or government controversies?
Reply ONLY with YES or NO.
"""

LAYER_1_FORENSIC_PROMPT = """
You are an aggressive, high-engagement political fact-checking agent optimized for social media distribution (Reddit & X).
Analyze the video content and leverage your integrated search tools to cross-reference live metrics and expose disinformation.

EDITORIAL DIRECTIVES:
1. Adopt a bold, punchy, investigative tone. Use hooks like "Why is X lying?" or "Did they get a paycheck from Y?" when exposing clear falsehoods.
2. Ground all analyses in the current reality of July 2026 (e.g., C. Joseph Vijay serving as Chief Minister of Tamil Nadu following the 2026 state elections, and the recent passing of singer S. Janaki).
3. You MUST output BOTH formats below separated by a clear horizontal rule (---). Do not combine them.

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

class VideoEntry:
    def __init__(self, video_id, title, description, statistics=None):
        self.id = video_id
        self.title = title
        self.description = description
        self.link = f"https://www.youtube.com/watch?v={video_id}"
        
        # Parse metric values for high-reach scoring calculations
        stats = statistics or {}
        self.views = int(stats.get('viewCount', 0))
        self.likes = int(stats.get('likeCount', 0))
        self.comments = int(stats.get('commentCount', 0))
        
        # Dynamic Multiplier Formulation for engagement tracking
        self.reach_score = self.views + (self.likes * 5) + (self.comments * 10)


def get_channel_id(handle, api_key):
    try:
        url = "https://www.googleapis.com/youtube/v3/channels"
        params = {"part": "id", "forHandle": handle, "key": api_key}
        response = requests.get(url, params=params).json()
        if "items" in response and response["items"]:
            return response["items"][0]["id"]
    except Exception as e:
        print(f"⚠️ Channel ID resolution failed for @{handle}: {e}")
    return None


def extract_cluster_key(title):
    """Normalizes titles to identify if multiple videos are talking about the exact same topic."""
    clean = title.upper()
    # 🚀 CRITICAL FIX: Properly escaped the regex pipe to prevent structural matching failures
    clean = re.sub(r'\|.*|LIVE.*|🔴.*', '', clean)
    words = re.findall(r'\b[A-Z0-9]{4,}\b', clean) 
    return "_".join(words[:3]) if words else clean[:20]


# ==========================================
# 🚀 PRE-SCRAPE LAYER DEFINITION
# ==========================================
def force_live_search(query_string):
    """
    THE BRUTE-FORCE HACK: Takes the choice away from the AI.
    Scrapes the live web and forcefully dumps raw snippets into the prompt text.
    """
    try:
        url = f"https://html.duckduckgo.com/html/?q={requests.utils.quote(query_string)}"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        res = requests.get(url, headers=headers, timeout=5)
        
        snippets = re.findall(r'<a class="result__snippet"[^>]*>(.*?)</a>', res.text, re.DOTALL)
        cleaned_snippets = [re.sub(r'<[^>]+>', '', s).strip() for s in snippets[:3]]
        
        return " | ".join(cleaned_snippets) if cleaned_snippets else "No live snippets found."
    except Exception as e:
        return f"Force-search layer failed: {e}

    
def run_scout():
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 🛰️ Booting Engagement-Ranked Swarm Engine...")

    if not YOUTUBE_API_KEY or not GEMINI_KEYS_STRING:
        print("❌ Critical API keys are missing.")
        return

    keys_list = [k.strip() for k in GEMINI_KEYS_STRING.split(",") if k.strip()]
    api_key_queue = queue.Queue()
    for key in keys_list:
        api_key_queue.put(key)

    try:
        groq_client = Groq(api_key=GROQ_API_KEY)
    except Exception as e:
        print(f"❌ Groq API initialization failed: {e}")
        return

    os.makedirs("reports", exist_ok=True)
    db_path = "database.json"
    db_lock = threading.Lock()
    
    if os.path.exists(db_path):
        with open(db_path, "r", encoding="utf-8") as f:
            processed_db = json.load(f)
    else:
        processed_db = []

    raw_discovered_entries = []
    print("📡 Harvesting trending arrays from source networks...")

    # 🚀 CRITICAL FIX: Calculates exactly 48 hours ago to prevent parsing 10-year-old viral videos
    time_limit = (datetime.utcnow() - timedelta(hours=48)).isoformat(timespec='seconds') + "Z"

    for handle in CHANNEL_HANDLES:
        channel_id = get_channel_id(handle, YOUTUBE_API_KEY)
        if not channel_id:
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
            search_response = requests.get(url, params=params).json()

            if "items" in search_response and search_response["items"]:
                video_ids = [item["id"]["videoId"] for item in search_response["items"] if item["id"].get("videoId")]
                if not video_ids:
                    continue
                
                # 🚀 RE-HYDRATION LAYER: Fetch precise likes, views, and comments via video details endpoint
                stats_url = "https://www.googleapis.com/youtube/v3/videos"
                stats_params = {
                    "part": "snippet,statistics",
                    "id": ",".join(video_ids),
                    "key": YOUTUBE_API_KEY
                }
                stats_response = requests.get(stats_url, params=stats_params).json()

                for item in stats_response.get("items", []):
                    snippet = item["snippet"]
                    raw_discovered_entries.append(VideoEntry(
                        item["id"],
                        snippet["title"],
                        snippet.get("description", ""),
                        item.get("statistics", {})
                    ))
                print(f"✅ Loaded live metrics for: @{handle}")
        except Exception as e:
            print(f"❌ Failed processing metrics for @{handle}: {e}")

    # --- Phase 1: Topic Clustering & Engagement Filter ---
    topic_clusters = {}
    
    for entry in raw_discovered_entries:
        if entry.id in processed_db:
            continue

        keyword_hit = any(kw.upper() in entry.title.upper() for kw in POLITICAL_KEYWORDS)
        if not keyword_hit:
            try:
                radar = groq_client.chat.completions.create(
                    model="llama-3.1-8b-instant",
                    messages=[{"role": "system", "content": PHASE_1_PROMPT}, {"role": "user", "content": entry.title}],
                    temperature=0.0
                )
                if "YES" not in radar.choices[0].message.content.strip().upper():
                    with db_lock:
                        processed_db.append(entry.id)
                    continue
            except Exception as e:
                print(f"⚠️ Phase 1 Radar Error for {entry.title}: {e}")
                continue

        # 🚀 CLUSTERING LOGIC: Group entries talking about the exact same structural topics
        cluster_key = extract_cluster_key(entry.title)
        if cluster_key not in topic_clusters:
            topic_clusters[cluster_key] = []
        topic_clusters[cluster_key].append(entry)

    # For each cluster, select exclusively the video with the highest reach score
    targets_to_process = []
    for cluster_key, entries in topic_clusters.items():
        best_entry = max(entries, key=lambda x: x.reach_score)
        targets_to_process.append(best_entry)
        
        # Silently log the lower-ranked duplicate video IDs so they aren't processed in future runs
        for entry in entries:
            if entry.id != best_entry.id:
                processed_db.append(entry.id)

    with open(db_path, "w", encoding="utf-8") as f:
        json.dump(processed_db, f)

    if not targets_to_process:
        print("\n✅ Sweep Complete. No new viral anomalies identified.")
        return

    print(f"\n🎯 Deduplicated Targets Locked: {len(targets_to_process)}")
    print(f"🚀 Deploying Swarm Workers across {len(keys_list)} concurrent threads...\n")

        def process_target(entry):
        title = entry.title
        url = entry.link
        desc = entry.description
        video_id = entry.id
        
        current_key = api_key_queue.get()
        client = genai.Client(api_key=current_key)
        
        # 🚀 THE BRUTE FORCE EXECUTION: Run search execution before Gemini is invoked
        print(f"🛰️ Injecting forced web data layer for topic: {title[:30]}...")
        live_web_data = force_live_search(f"{title} news Tamil Nadu 2026")
        
        # Merge the force-scraped data straight into the text block Gemini reads
        payload_context = (
            f"Title Context: {title}\n"
            f"Description Context: {desc}\n"
            f"🚨 VERIFIED LIVE WEB GROUND TRUTH (FORCE-FED): {live_web_data}\n"
            f"Engagement Footprint: {entry.views} views, {entry.likes} likes."
        )
        
        raw_report = None
    
        try:
            for attempt in range(3):
                try:
                    res = client.models.generate_content(
                        model="gemini-flash-latest",
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
                            # 🚀 UNLIMITED FREE REAL-TIME LOOKUPS: Native Server-Side Search Enabled
                            tools=[types.Tool(google_search=types.GoogleSearch())]
                        )
                    )
                    raw_report = res.text.strip()
                    break 
                except APIError as e:
                    if e.code == 429:
                        api_key_queue.put(current_key)
                        current_key = api_key_queue.get()
                        client = genai.Client(api_key=current_key)
                        time.sleep(3)
                    else:
                        print(f"❌ Model failed on '{title}': {e.message}")
                        break
                except Exception as e:
                    print(f"⚠️ Pipeline anomaly on '{title}': {e}")
                    break

            if not raw_report or "INSUFFICIENT_DATA" in raw_report:
                with db_lock:
                    processed_db.append(video_id)
                    with open(db_path, "w", encoding="utf-8") as f: json.dump(processed_db, f)
                return

            print(f"✅ Success: Social media fact-check generated for '{title[:30]}...'")

            safe_title = re.sub(r'[\\/*?:"<>|]', "", title)[:50]
            timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            filename = f"reports/{timestamp}_{safe_title.replace(' ', '_')}.md"

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

            with db_lock:
                processed_db.append(video_id)
                with open(db_path, "w", encoding="utf-8") as f:
                    json.dump(processed_db, f)

        except Exception as e:
            print(f"💥 FATAL THREAD CRASH on '{title}': {e}")
            with db_lock:
                processed_db.append(video_id)
                with open(db_path, "w", encoding="utf-8") as f: json.dump(processed_db, f)

        finally:
            api_key_queue.put(current_key)

    with ThreadPoolExecutor(max_workers=len(keys_list)) as executor:
        futures = [executor.submit(process_target, target) for target in targets_to_process]
        for future in as_completed(futures):
            try:
                future.result() 
            except Exception as e:
                print(f"⚠️ Unhandled Executor Exception: {e}")

    print("\n✅ Swarm Complete. Highly trending arrays processed and synchronized.")

if __name__ == "__main__":
    run_scout()
            

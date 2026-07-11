import os
import json
import time
import re
from datetime import datetime
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
You are a headless, backend OSINT forensic engine analyzing a political video directly from YouTube.
Watch the video visual frames, process the audio track completely, and read the provided title context.

STRICT DIRECTIVES:
1. NEVER ask the user to provide a link, upload a file, or give an attachment.
2. If the video contains no spoken words, is completely unrelated to politics, or cannot be analyzed, output EXACTLY: "INSUFFICIENT_DATA".
3. You MUST structure the report exactly in the order below using Markdown. DO NOT deviate.

## 🚨 FLAGGED CONTESTED CLAIMS (SUMMARY)
[Put the most explosive, contested, or factually dubious claims HERE AT THE VERY TOP. For each claim, provide:]
* **[Speaker Name]**: "Quote or paraphrase of the claim."
  * **Verdict**: [TRUE / MISLEADING / FALSE / PURE OPINION]
  * **Analysis**: Your objective political analysis and context of why it is flagged, referencing current TN political dynamics.

---

## 📜 COMPLETE DIALOGUE TRANSCRIPT & SUMMARY
[Provide a chronological, highly detailed breakdown of the entire conversation/speech. Translate heavy Tamil political rhetoric into clear English. Identify who is speaking, track timestamps if possible, and outline their core arguments. DO NOT give a one-paragraph summary. Provide a literal, section-by-section or line-by-line breakdown of what is actually said in the video.]
"""

class VideoEntry:
    def __init__(self, video_id, title, description):
        self.id = video_id
        self.title = title
        self.description = description
        self.link = f"https://www.youtube.com/watch?v={video_id}"

class GeminiRotator:
    """Manages pool distribution for up to 10 distinct API keys and handles automatic failover models."""
    def __init__(self, keys_string):
        self.keys = [k.strip() for k in keys_string.split(",") if k.strip()]
        self.current_index = 0
        self.client = None
        self.rotate_client()
        
    def rotate_client(self):
        if not self.keys:
            raise ValueError("No valid Gemini API keys parsed out of variable string context.")
        current_key = self.keys[self.current_index]
        masked = f"...{current_key[-4:]}" if len(current_key) > 4 else "???"
        print(f"🔑 Initializing Client Slot [{self.current_index}] (Key ending in {masked})")
        self.client = genai.Client(api_key=current_key)
        
    def next_key(self):
        self.current_index = (self.current_index + 1) % len(self.keys)
        print(f"🔄 Rotating to Client Slot [{self.current_index}]...")
        self.rotate_client()

def get_uploads_playlist(handle, api_key):
    try:
        url = "https://www.googleapis.com/youtube/v3/channels"
        params = {"part": "contentDetails", "forHandle": handle, "key": api_key}
        response = requests.get(url, params=params).json()
        if "items" in response and response["items"]:
            return response["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]
    except Exception as e:
        print(f"⚠️ API handle lookup failed for @{handle}: {e}")
    return None

def run_scout():
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 🛰️ Booting Native Video OSINT Scavenger Engine...")

    if not YOUTUBE_API_KEY:
        print("❌ YouTube API key is missing.")
        return

    try:
        groq_client = Groq(api_key=GROQ_API_KEY)
        gemini_pool = GeminiRotator(GEMINI_KEYS_STRING)
    except Exception as e:
        print(f"❌ Core API initialization failed: {e}")
        return

    os.makedirs("reports", exist_ok=True)
    db_path = "database.json"
    processed_db = json.load(open(db_path, "r")) if os.path.exists(db_path) else []

    all_entries = []
    print("📡 Resolving system upload streams via handles...")

    for handle in CHANNEL_HANDLES:
        uploads_playlist = get_uploads_playlist(handle, YOUTUBE_API_KEY)
        if not uploads_playlist:
            continue
            
        try:
            url = "https://www.googleapis.com/youtube/v3/playlistItems"
            params = {"part": "snippet", "playlistId": uploads_playlist, "maxResults": "3", "key": YOUTUBE_API_KEY}
            response = requests.get(url, params=params).json()
            
            if "items" in response:
                for item in response["items"]:
                    snippet = item["snippet"]
                    all_entries.append(VideoEntry(
                        snippet["resourceId"]["videoId"], 
                        snippet["title"], 
                        snippet.get("description", "")
                    ))
                print(f"✅ Loaded live feed items for: @{handle}")
        except Exception as e:
            print(f"❌ Network issue tracking data for @{handle}: {e}")

    for entry in all_entries:
        if entry.id in processed_db:
            continue

        title = entry.title
        url = entry.link
        desc = entry.description

        keyword_hit = any(kw.upper() in title.upper() for kw in POLITICAL_KEYWORDS)

        if not keyword_hit:
            try:
                radar = groq_client.chat.completions.create(
                    model="llama-3.1-8b-instant",
                    messages=[{"role": "system", "content": PHASE_1_PROMPT}, {"role": "user", "content": title}],
                    temperature=0.0
                )
                if "YES" not in radar.choices[0].message.content.strip().upper():
                    processed_db.append(entry.id)
                    with open(db_path, "w") as f: json.dump(processed_db, f)
                    continue
            except Exception as e:
                print(f"⚠️ Phase 1 API Error: {e}")
                continue

        print(f"\n🎯 Target Locked: {title}")

        raw_report = None
        payload_context = f"Title Context: {title}\nDescription Context: {desc}"
        
        for key_attempt in range(len(gemini_pool.keys)):
            model_target = "gemini-flash-latest"
            try:
                res = gemini_pool.client.models.generate_content(
                    model=model_target,
                    contents=[
                        types.Part.from_uri(file_uri=url, mime_type="video/mp4"),
                        types.Part.from_text(text=LAYER_1_FORENSIC_PROMPT),
                        types.Part.from_text(text=payload_context)
                    ],
                    config=types.GenerateContentConfig(temperature=0.1)
                )
                raw_report = res.text.strip()
                break 
            except APIError as e:
                if e.code == 429:
                    print(f"⏳ Quota limit (429). Shifting API keys and pausing for 10s...")
                    time.sleep(10)
                    gemini_pool.next_key()
                    continue 
                else:
                    print(f"❌ Model {model_target} failed: {e.message}")
                    break
            except Exception as e:
                print(f"⚠️ Pipeline anomaly: {e}")
                break
        
        if not raw_report:
            print(f"❌ Exhausted resources for {title}. Marking as processed to prevent infinite loop.")
            processed_db.append(entry.id)
            with open(db_path, "w") as f: json.dump(processed_db, f)
            continue

        if "INSUFFICIENT_DATA" in raw_report:
            print(f"⚠️ Insufficient spoken context detected.")
            processed_db.append(entry.id)
            with open(db_path, "w") as f: json.dump(processed_db, f)
            continue

        print("✅ Gemini successfully extracted the transcript and claims.")

        safe_title = re.sub(r'[\\/*?:"<>|]', "", title)[:50]
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        filename = f"reports/{timestamp}_{safe_title.replace(' ', '_')}.md"

        report_content = (
            f"# 🛰️ Grounded Report: {title}\n"
            f"**Source Video**: {url}\n\n"
            f"> *Automated OSINT Engine via Scavenger Scout*\n"
            f"> *Project Repo: https://github.com/superman-prog/Tamilnadu-politics-fact-checker*\n\n"
            f"---\n\n"
            f"{raw_report}"
        )

        with open(filename, "w", encoding="utf-8") as f:
            f.write(report_content)
        
        processed_db.append(entry.id)
        with open(db_path, "w") as f:
            json.dump(processed_db, f)

        time.sleep(20)

    print("\n✅ Sweep Complete. Database updated.")

if __name__ == "__main__":
    run_scout()
        

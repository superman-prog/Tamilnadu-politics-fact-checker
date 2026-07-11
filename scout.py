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
You are a headless, backend OSINT forensic engine analyzing text data extracted from a YouTube broadcast feed.
Structure the final analytical brief exactly in the order below:

## 🚨 FLAGGED CONTESTED CLAIMS (SUMMARY)
* **[Speaker Name / Context]**: "Core claims extracted from the news title and script summary."
  * **Verdict**: [TRUE / MISLEADING / FALSE / PURE OPINION]
  * **Analysis**: Provide a scannable context of why it is flagged, referencing current TN political dynamics.

---

## 📜 COMPLETE DIALOGUE TRANSCRIPT & SUMMARY
Provide a highly detailed breakdown of the contextual narrative implied by this update. Identify the core focus and political arguments involved.
"""

LAYER_2_EDITOR_PROMPT = """
You are the final editor. Your ONLY job is to ensure the text uses professional Markdown formatting, fix any broken structures, and verify that the "🚨 FLAGGED CONTESTED CLAIMS" section is at the absolute top of the report. Remove any conversational filler. Output ONLY the final markdown text.
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
        # Redacts key footprint securely in logs
        masked_key = f"...{current_key[-4:]}" if len(current_key) > 4 else "???"
        print(f"🔑 Initializing connection via Client Slot [{self.current_index}] (Key ending in {masked_key})")
        self.client = genai.Client(api_key=current_key)
        
    def next_key(self):
        self.current_index = (self.current_index + 1) % len(self.keys)
        print(f"🔄 Traffic constraint identified. Shifting load allocation to Client Slot [{self.current_index}]...")
        self.rotate_client()

def get_uploads_playlist_from_handle(handle, api_key):
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
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 🛰️ Booting Scavenger Scout Engine (Load-Balanced Quota Mode)...")

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
    print("📡 Resolving system upload streams via official API handles...")

    for handle in CHANNEL_HANDLES:
        uploads_playlist_id = get_uploads_playlist_from_handle(handle, YOUTUBE_API_KEY)
        if not uploads_playlist_id:
            continue
            
        try:
            url = "https://www.googleapis.com/youtube/v3/playlistItems"
            params = {"part": "snippet", "playlistId": uploads_playlist_id, "maxResults": "3", "key": YOUTUBE_API_KEY}
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
        video_id = entry.id
        if video_id in processed_db:
            continue

        title = entry.title
        url = entry.link
        description = entry.description

        title_upper = title.upper()
        keyword_hit = any(kw.upper() in title_upper for kw in POLITICAL_KEYWORDS)

        if not keyword_hit:
            try:
                radar_res = groq_client.chat.completions.create(
                    model="llama-3.1-8b-instant",
                    messages=[{"role": "system", "content": PHASE_1_PROMPT}, {"role": "user", "content": title}],
                    temperature=0.0
                )
                if "YES" not in radar_res.choices[0].message.content.strip().upper():
                    processed_db.append(video_id)
                    continue
            except Exception as e:
                print(f"⚠️ Phase 1 API Error on '{title}': {e}")
                continue

        print(f"\n🎯 Target Locked: {title}")

        # 🚀 PHASE 2 - LAYER 1: Text Analysis with Client Pool Rotation & Model Cascading
        raw_report = None
        payload_context = f"Title: {title}\nDescription Metadata: {description}\nSource Link: {url}"
        
        # We try across distinct API tokens inside our rotation loop structure
        for key_attempt in range(len(gemini_pool.keys)):
            # Cascade models: try primary first, then fall back to high-capacity lite profile
            for model_target in ["gemini-flash-latest", "gemini-3.1-flash-lite"]:
                try:
                    forensic_response = gemini_pool.client.models.generate_content(
                        model=model_target,
                        contents=[
                            types.Part.from_text(text=LAYER_1_FORENSIC_PROMPT),
                            types.Part.from_text(text=payload_context)
                        ],
                        config=types.GenerateContentConfig(temperature=0.1)
                    )
                    raw_report = forensic_response.text.strip()
                    break # Success! Break out of model cascading loop
                except APIError as e:
                    if e.code == 429:
                        print(f"⏳ Quota limit tripped on {model_target} using current slot. Forcing key index rotation...")
                        gemini_pool.next_key()
                        break # Break out of the current model loop to try the new key instead
                    else:
                        print(f"⚠️ Model {model_target} failed with code {e.code}. Checking cascade path...")
                        continue # Try the next fallback model structure
                except Exception as e:
                    print(f"⚠️ Secondary pipeline glitch on {model_target}: {e}")
                    continue
            
            if raw_report:
                break # Success! Break out of the key rotation attempt loop

        if not raw_report:
            print(f"❌ Layer 1 processing failure: Exhausted all key resources and model profiles. Skipping.")
            continue

        print("✅ Layer 1 processing resolved cleanly.")

        # PHASE 2 - LAYER 2: Editorial Clean-up via Groq
        try:
            editor_res = groq_client.chat.completions.create(
                model="llama-3.1-8b-instant",
                messages=[{"role": "system", "content": LAYER_2_EDITOR_PROMPT}, {"role": "user", "content": raw_report}],
                temperature=0.1
            )
            final_report = editor_res.choices[0].message.content.strip()
            print("🔥 Layer 2 structural refinement applied.")
        except Exception as e:
            final_report = raw_report

        safe_title = re.sub(r'[\\/*?:"<>|]', "", title)[:50]
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        filename = f"reports/{timestamp}_{safe_title.replace(' ', '_')}.md"

        report_content = (
            f"# 🛰️ Grounded Report: {title}\n"
            f"**Source Video**: {url}\n\n"
            f"> *Automated OSINT Engine via Scavenger Scout*\n"
            f"> *Project Repo: https://github.com/superman-prog/Tamilnadu-politics-fact-checker*\n\n"
            f"---\n\n"
            f"{final_report}"
        )

        with open(filename, "w", encoding="utf-8") as f:
            f.write(report_content)
        
        processed_db.append(video_id)
        with open(db_path, "w") as f:
            json.dump(processed_db, f)

        # Small 5s buffer to balance load across keys naturally
        time.sleep(5)

    print("\n✅ Sweep Complete. Database updated.")

if __name__ == "__main__":
    run_scout()
    

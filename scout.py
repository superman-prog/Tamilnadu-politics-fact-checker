"""
Scavenger Scout - Open Source OSINT Fact-Checking Framework
An automated pipeline that monitors regional RSS video feeds, screens for 
political relevance via local LLMs, and processes multimodal video content 
for automated fact-checking.

License: MIT
"""

import os
import sys
import time
import json
import random
import datetime
import feedparser
from groq import Groq
from google import genai
from google.genai import types

# =====================================================================
# SYSTEM CONFIGURATION
# =====================================================================

TARGET_CHANNELS = [
    "https://www.youtube.com/feeds/videos.xml?channel_id=UCmyKnNRH0wH-r8I-ceP-dsg", # Puthiya Thalaimurai
    "https://www.youtube.com/feeds/videos.xml?channel_id=UC-JFyL0zDFOsPMpuWu39rPA", # Thanthi TV
    "https://www.youtube.com/feeds/videos.xml?channel_id=UC8Z-VjXBtDJTvq6aqkIskPg", # Polimer News
    "https://www.youtube.com/feeds/videos.xml?channel_id=UC3sBClkAe3U88g699C2gVpA", # News18 TN
    "https://www.youtube.com/feeds/videos.xml?channel_id=UC76P_GZsn8D4stV6T-qX9OQ", # Sun News
    "https://www.youtube.com/feeds/videos.xml?channel_id=UClA17VpOfGqOWhZ8gWhI4Xg", # TVK Official
    "https://www.youtube.com/feeds/videos.xml?channel_id=UC38z3fT9RO4yugLJoCZLygw", # Behindwoods O2
    "https://www.youtube.com/feeds/videos.xml?channel_id=UCoOu4D7foJWfKvcDLxqrF2Q", # Chanakyaa
    "https://www.youtube.com/feeds/videos.xml?channel_id=UCsavukkumedianetwork",     # Savukku Media Network
    "https://www.youtube.com/feeds/videos.xml?channel_id=UCt3YmAt8OQyE0s1gqL6qW_Q", # Vikatan TV
    "https://www.youtube.com/feeds/videos.xml?channel_id=UCuJshcPrI8V8gOdBgNsk3wQ", # News7 Tamil
    "https://www.youtube.com/feeds/videos.xml?channel_id=UCb0f_X_6vRlhGzAsEwM0X3Q"  # Saattai
]

KEYWORDS = [
    "Vijay", "Stalin", "Edappadi", "EPS", "Annamalai", "Seeman", 
    "Thirumavalavan", "Mahendran", "Udhayanidhi", "Ramadoss",
    "TVK", "DMK", "AIADMK", "BJP", "NTK", "VCK", "Congress", "PMK",
    "CM", "Chief Minister", "Power Cut", "Current", "Minsaram", 
    "Assembly", "Press Meet", "Interview", "Speech", "Live", 
    "Protest", "Arrest", "Scandal", "Election", "Collector",
    "Meeting", "Conference", "Govt", "Welfare", "Scheme", "Budget",
    "Cabinet", "Ordinance", "GO", "TASMAC", "Cauvery", "NEET",
    "Bypoll", "By-election", "Manifesto", "Alliance", "Front"
]

DATABASE_FILE = "database.json"
REPORTS_DIR = "reports"
FEED_SCAN_LIMIT = 6             
MIN_FEED_DELAY = 1.0           
MAX_FEED_DELAY = 3.0

# Jitter boundaries for organic API pacing
MIN_PROCESS_JITTER = 15
MAX_PROCESS_JITTER = 38

# =====================================================================
# SYSTEM PROMPTS
# =====================================================================

SCOUT_PROMPT = """
You are a rapid-filter AI analyst. Is this YouTube title related to Tamil Nadu politics, government policies, leadership meetings, public administrative updates, press conferences, or community issues?
Answer ONLY with the word "YES" or "NO". Do not include any explanations or punctuation.
"""

HEAVY_PROMPT = """
Analyze the attached video track and act as an unbiased political fact-checker.

FORMAT YOUR RESPONSE EXACTLY AS FOLLOWS:

### 🚨 FLAGGED CONTESTED CLAIMS (SUMMARY)
[List only statements from the video that are FALSE, MISLEADING, or UNVERIFIED.]
* **[Speaker Name]**: "[Quote translated to English]"
  * **Verdict**: [FALSE / MISLEADING / UNVERIFIED]
  * **Analysis**: [Reasoned paragraph explaining real-world metrics, cross-references, and context.]
  * **Source**: [Specific public records or official announcements]

---

### 📜 COMPLETE DIALOGUE TRANSCRIPT
[Provide the chronological dialogue sequence. Translate all spoken lines to English.]
[Speaker Name]: [English translated dialogue line]
"""

AUDITOR_PROMPT = """
You are the Chief Editorial Auditor. Review the political fact-check report below. 
1. Clarify overly complex political jargon or ambiguous metrics.
2. Provide a 'Clear-English Distillation' bullet point for contradictory statements.
3. Keep the original structure intact, but output a refined, clear version.
"""

# =====================================================================
# UTILITIES
# =====================================================================

def load_db():
    if not os.path.exists(DATABASE_FILE):
        return {"date": str(datetime.date.today()), "api_calls_today": 0, "backlog": [], "cooled_keys": {}}
    with open(DATABASE_FILE, "r") as f:
        return json.load(f)

def save_db(db):
    with open(DATABASE_FILE, "w") as f:
        json.dump(db, f, indent=4)

# =====================================================================
# MAIN PIPELINE
# =====================================================================

def run_scout():
    print("🚀 Initializing Scavenger Scout Pipeline...")
    db = load_db()
    today_str = str(datetime.date.today())

    # Daily reset
    if db.get("date") != today_str:
        db["date"] = today_str
        db["api_calls_today"] = 0
        db["cooled_keys"] = {}

    # Abstracted Single-String Key Loader (Load Balancer)
    raw_keys_string = os.environ.get("GEMINI_KEYS_STRING", "")
    active_keys = [k.strip() for k in raw_keys_string.split(",") if k.strip()]
    groq_key = os.environ.get("GROQ_API_KEY")

    if not active_keys or not groq_key:
        print("❌ CRITICAL ERROR: API key strings are missing in environment.")
        sys.exit(1)

    scout_client = Groq(api_key=groq_key)
    new_videos = []

    # PHASE 1: FEED INGESTION & SCREENING
    print("📡 Scanning RSS Feeds...")
    for feed_url in TARGET_CHANNELS:
        feed = feedparser.parse(feed_url)
        for entry in feed.entries[:FEED_SCAN_LIMIT]:
            title = entry.title
            link = entry.link

            if any(k.lower() in title.lower() for k in KEYWORDS) and link not in db.get("backlog", []):
                time.sleep(random.uniform(MIN_FEED_DELAY, MAX_FEED_DELAY))
                try:
                    scout_response = scout_client.chat.completions.create(
                        model="llama-3.1-8b-instant",
                        messages=[
                            {"role": "system", "content": SCOUT_PROMPT},
                            {"role": "user", "content": f"Title: {title}"}
                        ],
                        temperature=0.1
                    )
                    if "YES" in scout_response.choices[0].message.content.strip().upper():
                        print(f"🎯 Relevance confirmed: {title}")
                        new_videos.append({"title": title, "url": link})
                except Exception as e:
                    print(f"⚠️ Screening error: {e}")

    # PHASE 2: MULTIMODAL PROCESSING WITH DYNAMIC FAILOVER
    current_key_index = 0

    for video in new_videos:
        success = False
        
        while not success and current_key_index < len(active_keys):
            # Skip keys that are actively cooling down today
            if str(current_key_index) in db.get("cooled_keys", {}):
                current_key_index += 1
                continue

            selected_key = active_keys[current_key_index]
            heavy_client = genai.Client(api_key=selected_key)

            print(f"🧠 Processing media via Key Slot [{current_key_index + 1}]: {video['title']}")
            
            try:
                # True Random Jitter before processing
                sleep_time = random.randint(MIN_PROCESS_JITTER, MAX_PROCESS_JITTER)
                print(f"⏳ Pacing API call organically ({sleep_time}s)...")
                time.sleep(sleep_time)

                # LAYER 1: Deep Extraction (High-Volume Model)
                gemini_response = heavy_client.models.generate_content(
                    model="gemini-3.1-flash-lite", 
                    contents=types.Content(parts=[
                        types.Part(file_data=types.FileData(file_uri=video['url'])),
                        types.Part(text=HEAVY_PROMPT)
                    ]),
                    config=types.GenerateContentConfig(temperature=0.10)
                )
                raw_report = gemini_response.text
                print("✅ Layer 1 resolved. Routing to Editor...")

                # LAYER 2: Refinement
                try:
                    auditor_response = scout_client.chat.completions.create(
                        model="llama-3.3-70b-versatile",
                        messages=[
                            {"role": "system", "content": AUDITOR_PROMPT},
                            {"role": "user", "content": f"Original Report Data:\n{raw_report}"}
                        ],
                        temperature=0.2
                    )
                    final_report = auditor_response.choices[0].message.content
                    print("🔥 Layer 2 refinement applied.")
                except Exception as audit_err:
                    print(f"⚠️ Editor bypass ({audit_err}). Using baseline report.")
                    final_report = raw_report

                # SAVE REPORT
                os.makedirs(REPORTS_DIR, exist_ok=True)
                timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
                safe_title = "".join([c for c in video['title'] if c.isalpha() or c.isdigit() or c == ' ']).rstrip()
                filename = f"{REPORTS_DIR}/{timestamp}_{safe_title[:30].replace(' ', '_')}.md"

                report_content = (
                    f"# 🛰️ Grounded Report: {video['title']}\n"
                    f"**Source Video**: {video['url']}\n\n"
                    f"> *This forensic analysis was automated by Scavenger Scout.* \n"
                    f"> *Join the project: **https://github.com/superman-prog/Tamilnadu-politics-fact-checker***\n\n"
                    f"---\n\n"
                    f"{final_report}"
                )

                with open(filename, "w", encoding="utf-8") as f:
                    f.write(report_content)

                if "backlog" not in db:
                    db["backlog"] = []
                db["backlog"].append(video['url'])
                db["api_calls_today"] += 1
                success = True # Break the while loop and move to the next video

            except Exception as e:
                err_msg = str(e)
                print(f"⚠️ API Exception on Key Slot [{current_key_index + 1}]: {err_msg}")
                
                # Dynamic Failover for Rate Limits & Quotas
                if any(x in err_msg for x in ["429", "Quota", "ResourceExhausted", "Forbidden", "403"]):
                    print(f"🛑 Key Slot [{current_key_index + 1}] exhausted. Isolating and advancing to next key...")
                    if "cooled_keys" not in db:
                        db["cooled_keys"] = {}
                    db["cooled_keys"][str(current_key_index)] = today_str
                    current_key_index += 1
                    
                    # True Random Recovery Jitter
                    recovery_time = random.randint(65, 115)
                    print(f"⏳ Taking a randomized {recovery_time}-second breather before initializing the new key...")
                    time.sleep(recovery_time)
                    
                # 500/503 Server Errors (Retry the same key)
                elif any(x in err_msg for x in ["500", "503", "Internal Server Error", "Service Unavailable"]):
                    print("🌩️ Google server unavailable. Retrying the same key in 30s...")
                    time.sleep(30)
                    
                # Broken Links
                elif any(x in err_msg for x in ["404", "Not Found", "VideoUnavailable"]):
                    print("🗑️ Source video missing. Skipping to next news item.")
                    break
                    
                else:
                    print("❌ Unknown error. Dropping bad target string link.")
                    break

    save_db(db)
    print("🏁 Pipeline execution complete. Systems returning to low-power state.")

if __name__ == "__main__":
    run_scout()
                

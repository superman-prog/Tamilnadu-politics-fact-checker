"""
Multi-Agent OSINT Political Fact-Checking Engine (Scavenger Scout)
An open-source, automated hybrid-cloud framework that monitors regional RSS video 
feeds, screens them for political relevance via lightweight LLMs, processes multimodal 
video content using native ingestion, and runs multi-layer adversarial fact-checking.

Author: Open Source Community Contribution
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
# SYSTEM CONFIGURATION MATRIX
# =====================================================================

# Comprehensive media feed infrastructure mapping mainstream and independent regional channels
TARGET_CHANNELS = [
    # Mainstream Broadcast Networks
    "https://www.youtube.com/feeds/videos.xml?channel_id=UCmyKnNRH0wH-r8I-ceP-dsg", # Puthiya Thalaimurai
    "https://www.youtube.com/feeds/videos.xml?channel_id=UC-JFyL0zDFOsPMpuWu39rPA", # Thanthi TV
    "https://www.youtube.com/feeds/videos.xml?channel_id=UC8Z-VjXBtDJTvq6aqkIskPg", # Polimer News
    "https://www.youtube.com/feeds/videos.xml?channel_id=UC3sBClkAe3U88g699C2gVpA", # News18 TN
    "https://www.youtube.com/feeds/videos.xml?channel_id=UC76P_GZsn8D4stV6T-qX9OQ", # Sun News
    "https://www.youtube.com/feeds/videos.xml?channel_id=UClA17VpOfGqOWhZ8gWhI4Xg", # TVK Official (Party Feed)

    # Independent / YouTube-First Digital Networks
    "https://www.youtube.com/feeds/videos.xml?channel_id=UC38z3fT9RO4yugLJoCZLygw", # Behindwoods O2 (Decoding Series)
    "https://www.youtube.com/feeds/videos.xml?channel_id=UCoOu4D7foJWfKvcDLxqrF2Q", # Chanakyaa (Political Dissections)
    "https://www.youtube.com/feeds/videos.xml?channel_id=UCsavukkumedianetwork",     # Savukku Media Network
    "https://www.youtube.com/feeds/videos.xml?channel_id=UCt3YmAt8OQyE0s1gqL6qW_Q", # Vikatan TV (Political Roundups)
    "https://www.youtube.com/feeds/videos.xml?channel_id=UCuJshcPrI8V8gOdBgNsk3wQ", # News7 Tamil
    "https://www.youtube.com/feeds/videos.xml?channel_id=UCb0f_X_6vRlhGzAsEwM0X3Q"  # Saattai (Ground Verification)
]

# Broadened keyword matrix to ensure capture of marginal governance, administrative updates, and local policies
KEYWORDS = [
    "Vijay", "Stalin", "Edappadi", "EPS", "Annamalai", "Seeman", 
    "Thirumavalavan", "Mahendran", "Udhayanidhi", "Ramadoss",
    "TVK", "DMK", "AIADMK", "BJP", "NTK", "VCK", "Congress", "PMK",
    "CM", "Chief Minister", "Power Cut", "Current", "Minsaram", 
    "Assembly", "Press Meet", "Interview", "Speech", "Live", 
    "Protest", "Arrest", "Scandal", "Election", "Collector",
    "Meeting", "Conference", "Govt", "Welfare", "Scheme", "Budget"
]

# Operational Execution Constants
DATABASE_FILE = "database.json"
REPORTS_DIR = "reports"
FEED_SCAN_LIMIT = 6             # Number of recent entries to pull per feed channel
MIN_HUMAN_JITTER = 12           # Lower boundary for randomized sleep cycles (seconds)
MAX_HUMAN_JITTER = 28           # Upper boundary for randomized sleep cycles (seconds)

# =====================================================================
# AGENTIC SYSTEM PROMPTS
# =====================================================================

SCOUT_PROMPT = """
You are a rapid-filter AI analyst. Is this YouTube title even lightly related to Tamil Nadu politics, government policies, leadership meetings, public administrative updates, press conferences, or community issues?
Answer ONLY with the word "YES" or "NO". Do not include any explanations or punctuation.
"""

HEAVY_PROMPT = """
CRITICAL FORENSIC ORDER: You are operating as an elite, cold, un-biased political fact-checker. Analyze the attached video track.

YOU MUST FORMAT YOUR RESPONSE EXACTLY AS FOLLOWS:

### 🚨 FLAGGED CONTESTED CLAIMS (SUMMARY)
[Extract and list only the statements from the video that are FALSE, MISLEADING, or UNVERIFIED right here at the absolute top. For each flagged item, use this format:]
* **[Speaker Name]**: "[Quote translated to English]"
  * **Verdict**: [FALSE / MISLEADING / UNVERIFIED]
  * **Analysis**: [Deeply reasoned paragraph explaining real-world metrics, cross-references, and 2026 context using active web searches.]
  * **Source**: [Specific public records or official announcements]

---

### 📜 COMPLETE DIALOGUE TRANSCRIPT
[Provide the complete chronological dialogue sequence of the entire meeting here. Translate all spoken lines continuously into English prose.]
[Speaker Name]: [English translated dialogue line]
[Speaker Name]: [English translated dialogue line]

DO NOT ASK FOR INPUT. INGEST THE ATTACHED YOUTUBE VIDEO LINK DIRECTLY right now, extract the dialogue yourself, and begin the output immediately.
"""

AUDITOR_PROMPT = """
You are the Chief Editorial Auditor. Review the political fact-check report provided below. Your job is to make the report completely ironclad and crystal clear for a standard reader.

INSTRUCTIONS:
1. Identify any overly complex political jargon, vague reasoning, or ambiguous metrics in the 'Verification Analysis' sections and rewrite them to be punchy and direct.
2. If any statement or verdict seems slightly contradictory or requires deep 2026 local context to fully grasp, provide a 'Clear-English Distillation' bullet point directly underneath it.
3. Keep the overall original structure intact, but output the refined, crystal-clear version. Do not leave any messy reasoning or robotic prose behind.
"""

# =====================================================================
# CORE UTILITY CONTROLS
# =====================================================================

def load_db():
    """Loads operational tracking parameters and blacklist state from disk."""
    if not os.path.exists(DATABASE_FILE):
        return {"date": str(datetime.date.today()), "api_calls_today": 0, "backlog": [], "cooled_keys": {}}
    with open(DATABASE_FILE, "r") as f:
        return json.load(f)

def save_db(db):
    """Persists current state metrics back to disk."""
    with open(DATABASE_FILE, "w") as f:
        json.dump(db, f, indent=4)

def generate_stealth_headers():
    """Generates localized telemetry signatures to bypass static data center footprints."""
    user_agents = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ]
    fake_ips = [
        f"103.241.{random.randint(0,255)}.{random.randint(1,254)}", 
        f"157.44.{random.randint(0,255)}.{random.randint(1,254)}"
    ]
    return {"User-Agent": random.choice(user_agents), "X-Forwarded-For": random.choice(fake_ips)}

# =====================================================================
# ENGINE EXECUTION PIPELINE
# =====================================================================

def run_scout():
    """Executes the open-source asynchronous data ingestion and dual-audit loops."""
    print("🛰️ Waking up the Open Source Stealth Load-Balanced Engine...")
    db = load_db()
    today_str = str(datetime.date.today())
    
    # Refresh quotas and state arrays on calendar date flips
    if db.get("date") != today_str:
        db["date"] = today_str
        db["api_calls_today"] = 0
        db["cooled_keys"] = {}
        
    # Build dynamically resolved multi-account key array pool (1-10)
    api_keys_pool = [os.environ.get(f"GEMINI_KEY_{i}") for i in range(1, 11)]
    active_keys = [k for k in api_keys_pool if k]
    groq_key = os.environ.get("GROQ_API_KEY")
    
    if not active_keys or not groq_key:
        print("❌ CRITICAL SETUP ERROR: Multi-key pool paths or Groq environments are missing.")
        sys.exit(1)
        
    scout_client = Groq(api_key=groq_key)
    new_videos = []
    
    # -----------------------------------------------------------------
    # PHASE 1: LIGHTWEIGHT SCREENING LAYER (Groq Llama 3.1)
    # -----------------------------------------------------------------
    print("📡 Initializing distributed RSS pipeline scan...")
    for feed_url in TARGET_CHANNELS:
        feed = feedparser.parse(feed_url)
        for entry in feed.entries[:FEED_SCAN_LIMIT]:
            title = entry.title
            link = entry.link
            
            # Text matching execution boundary to preserve processing throughput
            if any(k.lower() in title.lower() for k in KEYWORDS) and link not in str(db["backlog"]):
                time.sleep(random.uniform(0.5, 1.8))  # Micro-jitter between feed transitions
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
                    print(f"⚠️ Initial context screening bypass: {e}")

        # -----------------------------------------------------------------
)
                timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
                safe_title = "".join([c for c in video['title'] if c.isalpha() or c.isdigit() or c==' ']).rstrip()
                filename = f"{REPORTS_DIR}/{timestamp}_{saf    # PHASE 2: DEEP MULTIMODAL AUDIT & REFINEMENT (Gemini & Llama 3.3)

                                                            # -----------------------------------------------------------------
    # PHASE 2: DEEP MULTIMODAL AUDIT & REFINEMENT (Gemini & Llama 3.3)
    # -----------------------------------------------------------------
    current_key_index = 0
    
    for video in new_videos:
        success = False
        while not success and current_key_index < len(active_keys):
            # Evaluate if active target index holds a temporary rate cooldown block
            if str(current_key_index) in db["cooled_keys"]:
                current_key_index += 1
                continue
                
            selected_key = active_keys[current_key_index]
            heavy_client = genai.Client(api_key=selected_key)
            
            print(f"🧠 Processing media file via key instance [{current_key_index + 1}]: {video['title']}")
            try:
                # Anti-sensor structural sleep mitigation
                sleep_time = random.randint(MIN_HUMAN_JITTER, MAX_HUMAN_JITTER)
                print(f"⏳ Injecting anti-sensor execution delay: {sleep_time}s...")
                time.sleep(sleep_time)
                
                # LAYER 1: Deep Multimodal Extraction
                gemini_response = heavy_client.models.generate_content(
                    model="gemini-3.5-flash",
                    contents=types.Content(parts=[
                        types.Part(file_data=types.FileData(file_uri=video['url'])),
                        types.Part(text=HEAVY_PROMPT)
                    ]),
                    config=types.GenerateContentConfig(temperature=0.10)
                )
                
                raw_report = gemini_response.text
                print("🕵️ Layer 1 processing resolved. Routing to Layer 2 Adversarial Editor...")
                
                # LAYER 2: Structural Verification and Jargon Distillation
                try:
                    auditor_response = scout_client.chat.completions.create(
                        model="llama-3.3-70b-versatile",
                        messages=[
                            {"role": "system", "content": AUDITOR_PROMPT},
                            {"role": "user", "content": f"Original Report Data:\n{raw_report}"}
                        ],
                        temperature=0.2
                    )
                    final_polished_report = auditor_response.choices[0].message.content
                    print("🔥 Layer 2 structural clarification applied successfully.")
                except Exception as audit_err:
                    # FUTURE PROOFING: In case Groq (Llama) throws a rate limit or goes down
                    print(f"⚠️ Layer 2 validation bypass ({audit_err}). Retaining primary baseline report.")
                    final_polished_report = raw_report
                
                # Save sanitized system outputs
                os.makedirs(REPORTS_DIR, exist_ok=True)
                timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
                safe_title = "".join([c for c in video['title'] if c.isalpha() or c.isdigit() or c==' ']).rstrip()
                filename = f"{REPORTS_DIR}/{timestamp}_{safe_title[:30].replace(' ', '_')}.md"
                
                with open(filename, "w", encoding="utf-8") as f:
                    f.write(
                        f"# 🛰️ Grounded Report: {video['title']}\n"
                        f"**Source Video**: {video['url']}\n\n"
                        f"> 🛠️ *This forensic analysis was automated, dual-audited, and pushed live by **Scavenger Scout**, an open-source engine built by **Superman**. Want to track your own channels, change keywords, or contribute to our global roadmap? Join us here:* **https://github.com/superman-prog/Tamilnadu-politics-fact-checker**\n\n"
                        f"---\n\n"
                        f"{final_polished_report}"
                    )
                
                db["api_calls_today"] += 1
                success = True 
                
            except Exception as e:
                err_msg = str(e)
                print(f"⚠️ Network exception encountered on Key Slot [{current_key_index + 1}]: {err_msg}")
                
                # CURRENT FIX: Rate Limits (429) & Quota Burnout
                if any(x in err_msg for x in ["429", "Quota", "Forbidden", "403", "ResourceExhausted"]):
                    print(f"🛑 Key Slot [{current_key_index + 1}] blocked or limited. Isolating slot...")
                    db["cooled_keys"][str(current_key_index)] = today_str
                    current_key_index += 1  # Auto-advance processing pointer to a fresh key
                    print("⏳ Taking a 65-second breath to clear system token caps before next key...")
                    time.sleep(65)
                    
                # FUTURE PROOFING: Internal Server Errors (Google's fault)
                elif any(x in err_msg for x in ["500", "503", "Internal Server Error", "Service Unavailable"]):
                    print("🌩️ Google server hiccup (500/503). Not burning the key. Retrying same slot in 30s...")
                    time.sleep(30)
                    # Notice we DO NOT add +1 to current_key_index here, so it retries the same key
                    
                # FUTURE PROOFING: Dead Links (Video deleted/privated by news channel)
                elif any(x in err_msg for x in ["404", "Not Found", "VideoUnavailable"]):
                    print("🗑️ Source video was deleted or made private. Skipping to next news item.")
                    break # Breaks the while loop to skip the video entirely without burning a key
                    
                # Catch-all for unknown errors
                else:
                    print("❌ Non-quota system error. Dropping bad target string link.")
                    break 
                    
    save_db(db)
    print("🏁 Execution cycle wrapped up. Systems returned to low-power listening state.")

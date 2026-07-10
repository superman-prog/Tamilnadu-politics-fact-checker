import os
import json
import time
import re
from datetime import datetime
import feedparser
from groq import Groq
from google import genai
from google.genai import types

# --- CONFIGURATION & API KEYS ---
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
GEMINI_KEYS_STRING = os.environ.get("GEMINI_KEYS_STRING")

# 🚀 TARGET FEEDS
TARGET_FEEDS = [
    "https://www.youtube.com/feeds/videos.xml?channel_id=UCFzg1PqG9yXXSvcQ0F2-z4Q", # Polimer News
    "https://www.youtube.com/feeds/videos.xml?user=PuthiyaThalaimuraiTV",            # Puthiya Thalaimurai
    "https://www.youtube.com/feeds/videos.xml?channel_id=UC1HqQxN1Xk_kO6xeqZzV_Nw", # Thanthi TV
    "https://www.youtube.com/feeds/videos.xml?channel_id=UC_zEjiN-K9V2OnaW5wI4oJg", # Chanakyaa
    "https://www.youtube.com/feeds/videos.xml?channel_id=UClX6M-hDrc_4v5q4jB5uU9w", # Rangaraj Pandey
    "https://www.youtube.com/feeds/videos.xml?channel_id=UCn-w-H7-P1E-v8-l3yF8o4g", # Sun News
    "https://www.youtube.com/feeds/videos.xml?channel_id=UC-w1Tvu90iE3iU8e7cWzXWg", # News18
    "https://www.youtube.com/feeds/videos.xml?channel_id=UCX0vJ8H91b7d5q0_z2_WkRg"  # Kalaignar
]

# 🚀 AGGRESSIVE KEYWORD FILTER
POLITICAL_KEYWORDS = [
    "CM", "Vijay", "Stalin", "EPS", "Udhayanidhi", "Edappadi", "Seeman", 
    "Annamalai", "Thirumavalavan", "DMK", "ADMK", "AIADMK", "TVK", "NTK", 
    "BJP", "VCK", "Congress", "Assembly", "Election", "Karur", "Police"
]

# --- THE HEAVY PROMPTS ---

PHASE_1_PROMPT = """
You are a hypersensitive political radar for Tamil Nadu in July 2026. 
Is this video title related to Tamil Nadu politics, elections, political leaders, or government controversies?
Reply ONLY with YES or NO.
"""

LAYER_1_FORENSIC_PROMPT = """
You are a headless, backend OSINT forensic engine. You do not have a chat interface. 
You are analyzing a political video directly from YouTube.

STRICT DIRECTIVES:
1. NEVER ask the user to provide a link, upload a file, or give an attachment. You already have the video data.
2. If the video contains no spoken words, is completely unrelated to politics, or cannot be analyzed, output EXACTLY: "INSUFFICIENT_DATA".
3. If valid, you MUST structure the report exactly in the order below. DO NOT deviate.

REPORT STRUCTURE:

## 🚨 FLAGGED CONTESTED CLAIMS (SUMMARY)
[Put the most explosive, contested, or factually dubious claims HERE AT THE VERY TOP. For each claim, provide:]
* **[Speaker Name]**: "Quote or paraphrase of the claim."
  * **Verdict**: [TRUE / MISLEADING / FALSE / PURE OPINION]
  * **Analysis**: [Your objective political analysis and context of why it is flagged, referencing current TN political dynamics.]

---

## 📜 COMPLETE DIALOGUE TRANSCRIPT & SUMMARY
[Provide a chronological, highly detailed breakdown of the entire conversation/speech. Translate heavy Tamil political rhetoric into clear English. Identify who is speaking and what their core arguments are.]
"""

LAYER_2_EDITOR_PROMPT = """
You are the final editor. Your ONLY job is to ensure the text uses professional Markdown formatting, fix any broken structures, and verify that the "🚨 FLAGGED CONTESTED CLAIMS" section is at the absolute top of the report. Remove any AI conversational filler (like "Here is the report"). Output ONLY the final markdown text.
"""

def run_scout():
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 🛰️ Booting Heavy Scavenger Scout Engine (Native Vision Mode)...")

    try:
        groq_client = Groq(api_key=GROQ_API_KEY)
        gemini_keys = [k.strip() for k in GEMINI_KEYS_STRING.split(",") if k.strip()]
        gemini_client = genai.Client(api_key=gemini_keys[0]) 
    except Exception as e:
        print(f"❌ Core API initialization failed: {e}")
        return

    os.makedirs("reports", exist_ok=True)
    db_path = "database.json"
    if os.path.exists(db_path):
        with open(db_path, "r") as f:
            processed_db = json.load(f)
    else:
        processed_db = []

    all_entries = []
    for feed_url in TARGET_FEEDS:
        parsed_feed = feedparser.parse(feed_url)
        all_entries.extend(parsed_feed.entries)

    for entry in all_entries:
        video_id = entry.id
        if video_id in processed_db:
            continue

        title = entry.title
        url = entry.link

        title_upper = title.upper()
        keyword_hit = any(kw.upper() in title_upper for kw in POLITICAL_KEYWORDS)

        if not keyword_hit:
            try:
                radar_res = groq_client.chat.completions.create(
                    model="llama-3.1-8b-instant",
                    messages=[
                        {"role": "system", "content": PHASE_1_PROMPT},
                        {"role": "user", "content": title}
                    ],
                    temperature=0.0
                )
                is_relevant = radar_res.choices[0].message.content.strip().upper()
                if "YES" not in is_relevant:
                    processed_db.append(video_id)
                    continue
            except Exception as e:
                print(f"⚠️ Phase 1 API Error on '{title}': {e}")
                continue

        print(f"\n🎯 Target Locked: {title}")

        # 🚀 PHASE 2 - LAYER 1: Deep Forensic Analysis (Gemini 2.5 Flash Native Video)
        try:
            forensic_response = gemini_client.models.generate_content(
                model="gemini-2.5-flash",
                contents=[
                    types.Part.from_text(text=LAYER_1_FORENSIC_PROMPT),
                    types.Part.from_text(text=f"Title: {title}\nAnalyze this video directly:"),
                    types.Part.from_uri(file_uri=url, mime_type="video/mp4") # Native Ingestion 
                ],
                config=types.GenerateContentConfig(temperature=0.1)
            )
            raw_report = forensic_response.text.strip()
            
            if "INSUFFICIENT_DATA" in raw_report:
                print(f"⚠️ Layer 1 rejected payload. Model detected insufficient spoken context.")
                processed_db.append(video_id)
                continue
                
            print("✅ Layer 1 processing resolved natively. Contested claims placed at the top.")
        except Exception as e:
            print(f"❌ Layer 1 Failure: {e}")
            continue

        # PHASE 2 - LAYER 2: Editorial Clean-up (Groq 8B)
        try:
            editor_res = groq_client.chat.completions.create(
                model="llama-3.1-8b-instant",
                messages=[
                    {"role": "system", "content": LAYER_2_EDITOR_PROMPT},
                    {"role": "user", "content": raw_report}
                ],
                temperature=0.1
            )
            final_report = editor_res.choices[0].message.content.strip()
            print("🔥 Layer 2 structural refinement applied.")
        except Exception as e:
            print(f"⚠️ Layer 2 validation bypass ({e}). Using baseline report.")
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

        time.sleep(5)

    print("\n✅ Sweep Complete. Database updated.")

if __name__ == "__main__":
    run_scout()
    

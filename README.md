hey there! even though i named this tamilnadu politics fact-checker it can actually apply to any government in any nation maybe even random YouTuber or influencers just change the channel ids and keywords everything beyond this is technical jargon but if you actually want to contribute to this in any shape or form please read below and our future goals.

## 🎯 Future Goals & Roadmap (Contribute!)

We built the core engine, but the vision for **Scavenger Scout** is much bigger. If you are looking to contribute to an open-source OSINT project, here is where we need your help:

*   **[ ] Webhooks & Alerts:** Build integrations to push the generated Markdown reports directly to a Discord channel, Telegram bot, or Slack workspace the second a contested claim is flagged.
*   **[ ] Frontend Dashboard:** Move beyond raw GitHub Markdown files. We want to build a lightweight, serverless frontend (like Streamlit or Next.js) that pulls from the `/reports` folder and displays the fact-checks in a beautiful, searchable news-feed UI.
*   **[ ] Expanded Platform Radar:** Upgrade the `feedparser` logic to ingest data beyond just YouTube RSS feeds—specifically targeting X (Twitter) Spaces, Instagram Reels, and direct news website scraping.
*   **[ ] Serverless Database Migration:** Transition from the local `database.json` tracker to a proper serverless cloud database (like Supabase or Firebase) to allow for deep-searching historical claims over months or years.
*   **[ ] Dynamic Language Agnosticism:** Currently optimized for translating regional dialects to English. We want to add a configuration layer where users can set their target input/output languages dynamically for any country on earth.

*Want to build one of these? Fork the repo, open a Pull Request, and let's scale this engine!*


# 🛰️ Scavenger Scout

An automated political fact-checking engine designed to monitor regional broadcast networks and independent digital media platforms. The pipeline aggregates fresh media uploads via RSS feeds, filters entries for political or administrative relevance using Groq (**Llama 3.1**), and executes a deep, dual-layer adversarial analysis on the video track using Google (**Gemini 3.5 Flash**) and Groq (**Llama 3.3-70B**).

The framework is entirely serverless, stateless, and optimized to run natively within a GitHub Actions runner environment on a customizable periodic schedule.

---

## 🏗️ System Architecture

```text
[ Regional Media RSS Feeds ]
              │
              ▼
 📡 PHASE 1: Relevance Screening (Llama 3.1)
    Evaluates raw metadata and titles to filter for political or local governance contexts.
              │
              ▼ (If Confirmed)
 🧠 PHASE 2: Multimodal Grounding (Gemini 3.5 Flash)
    Ingests video audio tracks natively and executes live search queries to verify assertions.
              │
              ▼
 🔥 PHASE 3: Editorial Audit (Llama 3.3-70B)
    Cross-checks analysis, strips away machine-text jargon, and distills complex local metrics.
              │
              ▼
 [ 📜 Standardized Markdown Report Saved to /reports ]

⚡ Technical Highlights
 * Adversarial Multi-Model Verification: Routes text payloads across distinct model architectures from independent providers to cross-examine arguments, minimize factual drift, and mitigate hallucinations.
 * Dynamic Key Rotation Pool: Resilient against free-tier API access restrictions. The engine automatically handles key cooling cycles; if a token hits a rate limit (429) or access boundary (403), the script logs the status and fails over to the next active credential slot.
 * Telemetry Obfuscation & Jitter Control: Evades data-center traffic flags by introducing randomized processing delays (12-28s execution jitter) and varying localized header telemetry to mirror standard human browsing intervals.
 * Priority-Inverted Summaries: Structured specifically to separate noise from substance. Verified reports automatically isolate and highlight FALSE, MISLEADING, or UNVERIFIED content at the top of the file before appending the complete translated dialogue transcript below.
📁 Repository Blueprint
├── .github/workflows/
│   └── run.yml          # GitHub Actions workflow engine (configured for 2-hour intervals)
├── reports/             # Output destination folder for generated forensic markdown files
├── .gitignore           # Protective barrier preventing local tracking caches from staging
├── database.json        # Engine state storage (persists daily counters and cooldown indexes)
└── scout.py             # Core Python automation pipeline logic

🚀 Deployment Guide
Setting up an independent monitoring node requires four straightforward configuration steps:
1. Fork the Repository
Click the Fork button at the top right of this repository to copy the entire automated workspace infrastructure into your GitHub profile.
2. Obtain API Credentials
 * Generate API execution keys inside the Google AI Studio console.
 * Generate a standard API developer key from the Groq Cloud Console.
3. Configure Repository Secrets
To map environment tokens safely to your pipeline, head to your fork's dashboard:
 * Navigate to Settings ➔ Secrets and variables ➔ Actions.
 * Select New repository secret and define the following variables:
   * GROQ_API_KEY (Your Groq deployment token)
   * GEMINI_KEY_1 (Your primary Google API token)
   * GEMINI_KEY_2 through GEMINI_KEY_10 (Optional) (Valid secondary tokens to scale daily transaction boundaries)
4. Trigger the Workflow Engine
The automation sequence triggers out of the box via the repository’s cron schedule. To initiate an on-demand test cycle immediately:
 * Go to the Actions tab on your GitHub dashboard.
 * Select Automated Multi-Agent OSINT Engine from the left navigation panel.
 * Open the Run workflow dropdown panel and execute the code live.
📊 Standard Report Format
Output logs are saved cleanly within the /reports directory following this layout:
# 🛰️ Grounded Report: Media Stream Breakdown
**Source**: [https://www.youtube.com/watch?example](https://www.youtube.com/watch?example)

---

### 🚨 FLAGGED CONTESTED CLAIMS (SUMMARY)
* **Speaker Name**: "Local infrastructure upgrades are 100% complete ahead of schedule."
  * **Verdict**: FALSE
  * **Analysis**: Regional public utility audits indicate that Phase 2 power infrastructure expansions are currently halted at 43% completion due to distribution challenges.
  * **Source**: Department of Public Administration (Official Statement)

---

### 📜 COMPLETE DIALOGUE TRANSCRIPT
[Speaker A]: Welcome to the administrative update session...
[Speaker B]: Let us look closely at the data models...



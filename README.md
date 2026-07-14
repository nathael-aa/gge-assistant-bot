# 🏰 GGE Assistant

**GGE Assistant** is the ultimate Swiss Army knife designed for the **Goodgame Empire (GGE)** and **Empire: Four Kingdoms (E4K)** community. 
A truly standalone Discord bot, it features a comprehensive arsenal of military intelligence, alliance management, statistical analysis, and real-time server monitoring tools.

🌐 **Massive Multi-Server & Multilingual Support**: Fully operational across **46 supported servers** (including FR1, INT3, WORLD2, GB1, and many more!), with native support for French, English, and German.

---

## 🚀 Core Features

### ⚔️ Intelligence & Strategy (Warfare)
* **Radars & Targets:** Movement detection, search for legal targets according to server rules (HR), and proximity analysis.
* **Protection Tracker:** Precise monitoring of enemy dove (protection) expiration times.
* **Alliance Scanners:** Analysis of Might (PP), enemy roster breakdown, and tracking of description changes (alliance walls).

### 🏆 Events & Statistical Rankings
* **Live Rankings:** Dynamic pagination of ongoing events (Season, League, Outer Realms, Nomads, etc.).
* **Histories:** Detailed long-term tracking of player and alliance performances.
* **Wheel of Affluence (WOA):** Statistical analysis of rewards and rankings.

### ⚙️ Automation & Scanners (Background)
* Bash scripts (`auto_pa_daily.sh`) orchestrate invisible daily tasks.
* **Server Scanner:** Complete map extraction to cache levels, positions, and statistics across all 46 active servers.
* **Wall Scanner:** Archiving of alliance descriptions to detect secret pacts and hidden wars.

---

## 📜 Command List

| Command | Description |
| :--- | :--- |
| `/help` | Displays the complete user manual for the bot. |
| `/setup` | Configures the language and game server for this Discord server or your profile. |
| `/link_account` | Links your Discord account to your GGE username. |
| `/status` | Checks system health (Bot, NAS Storage, GGE-Tracker API). |
| `/changelog` | Discover the latest features, fixes, and improvements to the bot. |
| `/calendar` | Event calendar management and display. |
| `/rank` | Live event analysis and rankings. |
| `/leaderboard` | General server rankings (Might, Honor, etc.). |
| `/player` | Detailed player information card. |
| `/alliance` | Detailed alliance profile (Quick overview and paginated). |
| `/history` | Displays a player's complete history. |
| `/event_player` | Displays a player's latest score or history during an event. |
| `/event_alliance`| Alliance ranking and participation in an event. |
| `/compare_player`| Responsive comparative analysis and threat index calculation between two players. |
| `/woa` | Wheel of Affluence analysis and statistics. |
| `/radar` | Personal war radar. |
| `/rival` | Competition radar (Restricted to Private Messages). |
| `/fortress` | PVE Fortress radar (Sands, Fire, Peaks). |
| `/alliance_scanner`| Real-time enemy roster analysis (Doves, Might, Targets). |
| `/alliance_might`| Alliance Might history over X days. |
| `/alliance_description`| Displays the history of the last 7 wall changes for an alliance. |
| `/proximity` | Finds the closest enemy castles to your positions. |
| `/target` | Finds legal targets based on chosen war rules. |
| `/hr` | Checks if an attack between two players respects the chosen rules. |
| `/dove` | Checks a player's protection (dove) end date and time. |
| `/diplomacy` | Diplomatic relations management (Admins only). |
| `/contact` | Sends an issue, bug, or suggestion directly to the developer. |

---

## 🛠️ Project Architecture

The project is structured modularly, separating Discord logic from background analysis scripts.

```text
gge-assistant-bot/
├── cogs/                  # Discord Modules (Slash commands sorted by theme)
│   ├── admin.py, aide.py, classement.py, radar.py, guerre.py, profils.py...
├── scanners/              # Asynchronous analysis scripts (GGE-Tracker API)
│   ├── alerter.py
│   └── server_scanner.py
├── data/                  # 🔒 Local JSON databases & archives
│   ├── alliances/         # Rulesets and diplomacy
│   ├── configs/           # Block, user, and calendar configurations..
│   ├── joueurs/           # Linked usernames and sessions
│   └── server_scans/      # Global server snapshots
├── locales/               # Translation files (i18n)
│   ├── en.json, fr.json, de.json
├── logs/                  # 🔒 Activity logs organized by process
├── auto_pa_daily.sh       # Cron/scanner task trigger script
├── discord_bot.py         # Main entry point (Bot initialization)
├── docker-compose.yaml    # Container orchestration
├── Dockerfile             # Application image build
├── README.md              # Redame text document
├── requirements.txt       # Python dependencies (discord.py, aiohttp, etc.)
└── utils.py               # Shared utility functions
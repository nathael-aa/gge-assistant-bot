🏰 GGE Assistant
GGE Assistant is the ultimate Swiss Army knife designed for the Goodgame Empire (GGE) and Empire: Four Kingdoms (E4K) community.
A truly standalone Discord bot, it features a comprehensive arsenal of military intelligence, personal war radars, statistical analysis, and real-time server monitoring tools.

🌐 Massive Multi-Server & Multilingual Support: Fully operational across 46 supported servers (including FR1, INT3, WORLD2, GB1, and many more!), with native support for French, English, and German.

🚀 Core Features
⚔️ Intelligence & Strategy (Warfare)
Personal War Radar: Advanced target searching (/target) based on a highly customizable personal dashboard (configure your own PP limits, Tier differences, and Dove filters).

Protection Tracker: Precise monitoring of enemy dove (protection) expiration times.

Alliance Scanners: Analysis of Might (PP), enemy roster breakdown, and tracking of description changes (alliance walls).

🏆 Events & Statistical Rankings
Live Rankings: Dynamic pagination of ongoing events (Season, League, Outer Realms, Nomads, etc.).

Storm Islands: Real-time automated tracking and alerts for Aquamarine islands (Spawns and Captures).

Histories: Detailed long-term tracking of player and alliance performances.

Wheel of Affluence (WOA): Statistical analysis of rewards and rankings.

⚙️ Automation & Security (Background)
GitHub Actions CI/CD: Daily automated security jobs including linting (Ruff), dependency audits (pip-audit), and container vulnerability scanning (Trivy).

Server Scanner: Complete map extraction to cache levels, positions, and statistics across all 46 active servers.

Wall Scanner: Archiving of alliance descriptions to detect secret pacts and hidden wars.

## 🛠️ Project Architecture

The project is structured modularly, separating Discord logic from background analysis scripts.

```text
gge-assistant/
├── .github/workflows/     # 🔒 GitHub Actions (Automated security scans & CI)
├── cogs/                  # Discord Modules (Slash commands sorted by theme)
│   ├── admin.py, storms.py, aide.py, classement.py, radar.py, guerre.py, scan_server.py...
├── data/                  # 🔒 Local JSON databases & archives (Ignored in Git)
│   ├── admins/            # Maintenance mode, blocklists, and contacts
│   ├── configs/           # General configurations and user custom rules
│   ├── joueurs/           # Linked usernames, active sessions, radars, and votes
│   ├── serveurs/          # Server-specific setups (Calendars, Storm alerts)
│   └── server_scans/      # Global server snapshots from the API
├── locales/               # Translation files (i18n)
│   ├── en.json, fr.json, de.json
├── logs/                  # 🔒 Activity logs
├── discord_bot.py         # Main entry point (Bot initialization)
├── docker-compose.yaml    # Container orchestration
├── Dockerfile             # Application image build
├── emojis.py              # Centralized emoji dictionary
├── README.md              # Documentation
├── requirements.txt       # Python dependencies (discord.py, aiohttp, etc.)
├── ruff.toml              # Python linter and formatter configuration
└── utils.py               # Shared utility functions and UI Views
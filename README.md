# 🏰 GGE Assistant

**GGE Assistant** est le couteau suisse ultime conçu pour la communauté **Goodgame Empire (GGE)** et **Empire: Four Kingdoms (E4K)**. 
Véritable bot Discord autonome, il intègre un arsenal complet d'outils de renseignement militaire, de gestion d'alliance, d'analyse statistique et de surveillance des serveurs en temps réel.

🌐 **Multi-serveurs et Multilingue** : Conçu pour s'adapter à n'importe quel serveur (FR1, INT3, WORLD2, GB1...) avec un support natif du Français, de l'Anglais et de l'Allemand.

---

## 🚀 Fonctionnalités Principales

### ⚔️ Renseignement & Stratégie (Guerre)
* **Radars & Cibles :** Détection des mouvements, recherche de cibles légales selon les règles du serveur (HR), et analyse de proximité.
* **Traqueur de protection :** Surveillance précise de l'heure de fin des colombes ennemies.
* **Scanners d'Alliances :** Analyse de la puissance (PP), du roster ennemi et de l'historique des changements de description (murs d'alliance).

### 🏆 Événements & Classements Statistiques
* **Classements en direct :** Pagination dynamique des événements en cours (Saison, Ligue, Royaumes Extérieurs, Nomades, etc.).
* **Historiques :** Suivi détaillé des performances des joueurs et des alliances sur le long terme.
* **Roue d'Abondance (WOA) :** Analyse statistique des gains et classements.

### ⚙️ Automatisation & Scanners (Arrière-plan)
* Des scripts bash (`auto_pa_daily.sh`) orchestrent des tâches quotidiennes invisibles.
* **Server Scanner :** Aspiration complète de la carte pour mettre en cache les niveaux, positions et statistiques.
* **Murs Scanner :** Archivage des descriptions d'alliances pour détecter les pactes et les guerres cachées.

---

## 📜 Liste des Commandes

| Commande | Description |
| :--- | :--- |
| `/help` | Affiche le manuel d'utilisation complet du bot. |
| `/setup` | Configure la langue et le serveur de jeu pour ce serveur Discord ou votre profil. |
| `/link_account` | Lie votre compte Discord à votre pseudo GGE. |
| `/status` | Vérifie l'état de santé du système (Bot, Stockage NAS, API GGE-Tracker). |
| `/changelog` | Découvrez les dernières nouveautés, correctifs et améliorations du bot. |
| `/calendar` | Gestion et affichage du calendrier des événements. |
| `/rank` | Analyse et classements des événements en direct. |
| `/leaderboard` | Classements généraux du serveur (Puissance, Honneur, etc.). |
| `/player` | Fiche d'informations détaillée d'un joueur. |
| `/alliance` | Profil détaillé d'une alliance (Aperçu rapide et paginé). |
| `/history` | Affiche l'historique complet d'un joueur. |
| `/event_player` | Affiche le dernier score ou l'historique d'un joueur lors d'un événement. |
| `/event_alliance`| Classement et participation d'une alliance à un événement. |
| `/compare_player`| Analyse comparative réactive et calcul de l'indice de danger entre deux joueurs. |
| `/woa` | Analyse et statistiques de la Roue de l'Abondance (Wheel of Affluence). |
| `/radar` | Radar de guerre personnel. |
| `/rival` | Radar de concurrence (Limité aux Messages Privés). |
| `/fortress` | Radar de Forteresses PVE (Sables, Glaces, Pics). |
| `/alliance_scanner`| Analyse le roster ennemi en temps réel (Colombes, PP, Cibles). |
| `/alliance_might`| Historique de la Puissance (PP) d'une alliance sur X jours. |
| `/alliance_description`| Affiche l'historique des 7 derniers changements de mur pour une alliance. |
| `/proximity` | Trouve les châteaux ennemis les plus proches de vos positions. |
| `/target` | Trouve des cibles légales en fonction des règles de guerre choisies. |
| `/hr` | Vérifie si une attaque entre deux joueurs respecte les règles choisies. |
| `/dove` | Vérifie la date et l'heure de fin de protection (colombe) d'un joueur. |
| `/diplomacy` | Gestion des relations diplomatiques (Réservé aux administrateurs). |
| `/contact` | Envoie un problème, un bug ou une suggestion directement au développeur. |

---

## 🛠️ Architecture du Projet

Le projet est structuré de manière modulaire, séparant la logique Discord des scripts d'analyse de fond.

```text
gge-assistant/
├── cogs/                  # Modules Discord (Commandes /slash triées par thèmes)
│   ├── admin.py, aide.py, classement.py, radar.py, guerre.py, profils.py...
├── scanners/              # Scripts d'analyse asynchrones (API GGE-Tracker)
│   ├── alerter.py
│   ├── murs_scanner.py
│   └── server_scanner.py
├── data/                  # 🔒 Bases de données JSON locales & archives
│   ├── alliances/         # Règlements et diplomaties
│   ├── configs/           # Configurations bot, serveurs, calendrier
│   ├── joueurs/           # Pseudos liés et sessions
│   ├── murs_scans/        # Archives quotidiennes des murs (E4K_FR1, INT3, WORLD2...)
│   └── server_scans/      # Captures globales des serveurs
├── locales/               # Fichiers de traduction (i18n)
│   ├── en.json, fr.json, de.json
├── logs/                  # 🔒 Journaux d'activité organisés par processus
├── auto_pa_daily.sh       # Script de déclenchement des tâches cron/scanners
├── discord_bot.py         # Point d'entrée principal (Initialisation du bot)
├── docker-compose.yml     # Orchestration des conteneurs
├── Dockerfile             # Construction de l'image de l'application
├── requirements.txt       # Dépendances Python (discord.py, aiohttp, etc.)
└── utils.py               # Fonctions utilitaires partagées
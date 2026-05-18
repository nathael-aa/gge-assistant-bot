# 🏰 GGE Assistant

Un bot Discord complet et autonome conçu pour la communauté **Goodgame Empire (GGE)**. Il intègre des outils de surveillance en temps réel, de gestion d'alliance et d'analyse des serveurs (Actuellement limité au serveur E4K_FR1 et en français mais adaptable).

---

## 🚀 Fonctionnalités

### 🧠 Modules Principaux (Cogs)
* **`radar`** : Surveillance avancée des mouvements et cibles sur la carte.
* **`guerre` & `forteresses`** : Outils stratégiques pour la gestion des conflits et des structures.
* **`profils`** : Fiches d'informations détaillées sur les joueurs et les alliances.
* **`sanctions`** : Suivi de la diplomatie et gestion de la modération/listes noires.
* **`aide`** : Menu recensant toutes les commandes disponibles.

### 🔍 Scanners Automatiques
Le bot fait tourner des scripts d'arrière-plan pour analyser le serveur de jeu :
* `alliance_scanner` & `player_scanner` : Suivi de l'évolution des joueurs et des alliances.
* `murs_scanner` : Analyse de l'état des murs d'alliance.
* `server_scanner` : Monitoring global de l'état du serveur de jeu.

---

## 🛠️ Architecture du Projet

```text
gge-assistant/
├── cogs/                  # Modules de commandes Discord
│   ├── aide.py
│   ├── events.py
│   ├── forteresses.py
│   ├── guerre.py
│   ├── profils.py
│   ├── radar.py
│   └── sanctions.py
├── scanners/              # Scripts d'analyse automatique de l'API GGE
│   ├── __init__.py
│   ├── alliance_scanner.py
│   ├── murs_scanner.py
│   ├── player_scanner.py
│   └── server_scanner.py
├── data/                  # 🔒 Données locales (Ignoré par Git)
├── logs/                  # 🔒 Journaux d'activité (Ignoré par Git)
├── Dockerfile             # Configuration de l'image de l'application
├── docker-compose.yml     # Orchestration du conteneur Docker
└── requirements.txt       # Dépendances Python (discord.py, aiohttp...)

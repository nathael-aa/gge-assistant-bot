# 🏰 GGE Assistant

Un bot Discord complet et autonome conçu pour la communauté **Goodgame Empire (GGE)**. Il intègre des outils de surveillance en temps réel, de gestion d'alliance et d'analyse des serveurs (Actuellement limité au serveur E4K_FR1 et en français mais adaptable).

---

## 🚀 Fonctionnalités

### 🧠 Modules Principaux (Cogs)
* **`radar`** : Surveillance avancée des mouvements et cibles sur la carte.
* **`guerre` & `forteresses`** : Outils stratégiques pour la gestion des conflits et le farm des structures PVE.
* **`profils`** : Fiches d'informations détaillées sur les joueurs et les alliances, suivi des résultats d'événements, des historique de joueur et du mode protéction (colombe).
* **`sanctions`** : Module permettant d'enregistrer, ou de supprimer des sanctions sur un joueur (sauvegardé par serveur).
* **`aide`** : Menu recensant toutes les commandes disponibles, de vérifier les statuts et le moyens de contacter le développeur.

### 🔍 Scanners Automatiques
Le bot fait tourner des scripts d'arrière-plan (via auto_pa_daily.sh) pour analyser le serveur de jeu :
* `alliance_scanner` & `player_scanner` : Suivi de l'évolution des joueurs et des alliances.
* `murs_scanner` : Récupère le texte écris sur chaque mur d'alliance du serveur.
* `server_scanner` : Aspiration de la liste de tous les joueurs du serveur E4K_FR1 avec leurs informations de base.

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
├── scanners/              # Scripts d'analyse automatique de l'API GGE-Tracker
│   ├── __init__.py
│   ├── alliance_scanner.py
│   ├── murs_scanner.py
│   ├── player_scanner.py
│   └── server_scanner.py
├── data/                  # 🔒 Données locales (Ignoré par Git)
├── logs/                  # 🔒 Journaux d'activité (Ignoré par Git)
├── auto_pa_daily.sh       # Fichier permettant le lancement quotidien des scanners
├── config.json            # Méthode de stockage, serveur de jeu
├── discord_bot.py         # Fichier centrale du bot permettant son lancement et son initialisation
├── Dockerfile             # Configuration de l'image de l'application
├── docker-compose.yml     # Orchestration du conteneur Docker
├── utils.py               # 🔒 Fichier contenant des informations utiles au bot (ID, clés..)
└── requirements.txt       # Dépendances Python (discord.py, aiohttp...)

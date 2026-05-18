#!/bin/bash

# 1. On va dans le dossier de ton projet
cd /volume1/gge-assistant

# 2. On crée le flag dans le dossier data
touch data/scan.flag

# 3. On ordonne au conteneur Docker de lancer les scripts avec SON Python dans le nouveau dossier
echo "Lancement du server_scanner..."
docker exec gge-discord-bot python3 scanners/server_scanner.py

echo "Lancement du murs_scanner..."
docker exec gge-discord-bot python3 scanners/murs_scanner.py

# 4. On nettoie le flag
rm -f data/scan.flag
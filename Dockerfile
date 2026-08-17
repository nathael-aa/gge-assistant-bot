FROM python:3.11-slim

# CORRIGER LES FAILLES DE SÉCURITÉ DE L'OS :
RUN apt-get update && apt-get upgrade -y && rm -rf /var/lib/apt/lists/*

# Définir le fuseau horaire pour que les logs soient à l'heure française
ENV TZ=Europe/Paris

WORKDIR /app

# 1. Installer les dépendances en premier (optimise le cache Docker)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 2. Copier le reste des scripts
COPY . .

# 3. Créer les répertoires de données s'ils n'existent pas
RUN mkdir -p data/server_scans data/alliance_scans data/player_details data/events_scans logs

# 4. Lancer le bot Discord par défaut
CMD ["python3", "discord_bot.py"]
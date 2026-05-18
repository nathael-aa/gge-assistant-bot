FROM python:3.11-slim

# Définir le fuseau horaire pour que les logs soient à l'heure française
ENV TZ=Europe/Paris

WORKDIR /app

# 1. Installer les dépendances en premier (optimise le cache Docker)
# Assure-toi que ton fichier s'appelle bien requirements.txt sur ton NAS
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 2. Copier le reste des scripts
COPY . .

# 3. Créer les répertoires de données s'ils n'existent pas
# Ton arborescence utilise 'data/server_scans', 'data/alliance_scans', etc.
RUN mkdir -p data/server_scans data/alliance_scans data/player_details data/events_scans logs

# 4. Lancer le bot Discord par défaut
# C'est ce qui rendra le bot "vivant" sur ton NAS
CMD ["python3", "discord_bot.py"]
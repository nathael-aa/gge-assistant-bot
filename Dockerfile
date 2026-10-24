FROM python:3.11-slim

# Définir le fuseau horaire pour les logs
ENV TZ=Europe/Paris

WORKDIR /app

# 1. Copier uniquement les requirements pour optimiser le cache Docker
COPY requirements.txt .

# 2. Installer les dépendances et préparer l'arborescence avec les bonnes permissions (UID/GID 1001)
RUN pip install --no-cache-dir -r requirements.txt && \
    mkdir -p data/server_scans data/alliance_scans data/player_details data/events_scans logs assets/emojis && \
    chown -R 1001:1001 /app

# 3. Copier le reste des scripts en attribuant directement la propriété à l'utilisateur 1001
COPY --chown=1001:1001 . .

# 4. Basculer sur l'utilisateur sécurisé (doit correspondre au docker-compose)
USER 1001

# 5. Lancer le bot Discord
CMD ["python3", "discord_bot.py"]
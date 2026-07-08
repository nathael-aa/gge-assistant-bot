#!/bin/bash
cd /home/nathael/gge-assistant-bot || exit 1

echo "======================================================"
echo "🌍 DÉMARRAGE DE LA ROUTINE MULTI-SERVEURS QUOTIDIENNE"
echo "======================================================"

# Flag pour bloquer la RAM du bot Discord pendant l'écriture
touch data/scan.flag

# Fonction d'alerte : si le scanner plante, on appelle notre alerter.py
lancer_scan() {
    local script_path=$1
    echo "🚀 Lancement de $script_path en mode GLOBAL..."
    
    # Le script Python gère lui-même la boucle des serveurs et les pauses (time.sleep) !
    docker exec gge-assistant python3 "$script_path"
    
    if [ $? -ne 0 ]; then
        echo "❌ Erreur détectée sur $script_path, envoi de l'alerte MP..."
        docker exec gge-assistant python3 scanners/alerter.py "$script_path"
    fi
}

lancer_scan "scanners/server_scanner.py"
lancer_scan "scanners/murs_scanner.py"

rm -f data/scan.flag
echo "✅ Routine terminée avec succès. Tous les serveurs actifs ont été scannés."
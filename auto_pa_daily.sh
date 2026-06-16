#!/bin/bash
cd /volume1/gge-assistant || exit 1

# Flag pour le bot
touch data/scan.flag

# Fonction d'alerte : si le scanner plante, on appelle notre alerter.py
lancer_scan() {
    local script_path=$1
    echo "🚀 Lancement de $script_path..."
    docker exec gge-assistant python3 "$script_path"
    
    if [ $? -ne 0 ]; then
        echo "❌ Erreur détectée sur $script_path, envoi de l'alerte MP..."
        docker exec gge-assistant python3 scanners/alerter.py "$script_path"
    fi
}

lancer_scan "scanners/server_scanner.py"
lancer_scan "scanners/murs_scanner.py"

rm -f data/scan.flag
echo "✅ Routine terminée."
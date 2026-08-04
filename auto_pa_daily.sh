#!/bin/bash
cd /home/nathael/gge-assistant-bot || exit 1
#cd /volume1/gge-assistant || exit 1

echo "======================================================"
echo "🌍 DÉMARRAGE DE LA ROUTINE MULTI-SERVEURS QUOTIDIENNE"
echo "======================================================"

# Flag pour bloquer la RAM du bot Discord pendant l'écriture
touch data/scan.flag

lancer_scan() {
    local script_path=$1
    echo "🚀 Lancement de $script_path..."

    /usr/bin/docker exec gge-assistant python3 "$script_path"
}

lancer_scan "scanners/server_scanner.py"

# Nettoyage du flag
rm -f data/scan.flag

echo "✅ Routine terminée avec succès."
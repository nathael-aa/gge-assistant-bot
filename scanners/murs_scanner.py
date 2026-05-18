# -*- coding: utf-8 -*-
import json
import requests
from pathlib import Path
import time
import os
from datetime import datetime
from urllib.parse import quote

BASE_DATA_PATH = Path(os.getenv('DATA_PATH', '/app/data'))

def scan_murs():
    print("🚀 Début de l'extraction ultra-rapide des murs d'alliances (fly.dev)...")
    
    # 1. Lire le dernier scan serveur
    try:
        scans_dir = BASE_DATA_PATH / 'server_scans'
        if not scans_dir.exists():
            print(f"❌ Le dossier {scans_dir} n'existe pas. Lance d'abord le server_scanner !")
            return
            
        server_files = list(scans_dir.rglob('server_*.json'))
        if not server_files:
            print("❌ Aucun fichier serveur trouvé.")
            return
            
        latest_scan = max(server_files, key=lambda p: p.stat().st_mtime)
        with open(latest_scan, 'r', encoding='utf-8') as f:
            players_data = json.load(f).get('players', {})
    except Exception as e:
        print(f"❌ Erreur lecture serveur: {e}")
        return

    # 2. Extraire les VRAIS IDs et Noms
    alliances_map = {}
    for p_info in players_data.values():
        a_obj = p_info.get('alliance')
        a_name = a_obj.get('name') if isinstance(a_obj, dict) else (p_info.get('alliance_name') or a_obj)
        
        # 🛡️ CORRECTION : Extraction blindée de l'ID d'alliance
        aid_val = p_info.get('allianceId') or p_info.get('alliance_id')
        if not aid_val and isinstance(a_obj, dict):
            aid_val = a_obj.get('allianceId') or a_obj.get('alliance_id')

        if not aid_val or str(aid_val) == "0" or str(aid_val).lower() == "none":
            continue
            
        raw_aid = str(aid_val)
        
        if a_name and a_name != "Sans alliance":
            # On retire le fameux "164" (Serveur FR1) à la fin de l'ID si présent
            if raw_aid.endswith('164') and len(raw_aid) > 3:
                real_aid = raw_aid[:-3]
            else:
                real_aid = raw_aid
                
            alliances_map[real_aid] = a_name

    print(f"🎯 {len(alliances_map)} alliances uniques trouvées. Lancement du scan...")

    # 3. Interrogation Directe Fly.dev
    murs_data = {}
    session = requests.Session()
    
    # On garde le déguisement de navigateur, c'est toujours une bonne pratique !
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json"
    })
    
    count = 0
    
    for real_aid, aname in alliances_map.items():
        count += 1
        if count % 20 == 0:
            print(f"⏳ {count}/{len(alliances_map)} murs scannés...")
            
        try:
            # 🎯 LA CORRECTION EST ICI : On fabrique exactement la chaîne "%22AID%22:1234"
            encoded_payload = f"%22AID%22:{real_aid}"
            url_desc = f"https://empire-api.fly.dev/EmpirefourkingdomsExGG_2/ain/{encoded_payload}"
            
            r = session.get(url_desc, timeout=10)
            
            if r.status_code == 200:
                data = r.json()
                if data.get('return_code') == 0:
                    content = data.get('content', {})
                    if 'A' in content:
                        desc = str(content['A'].get('D', '')).lower()
                        # Nettoyage des balises HTML
                        desc = desc.replace('<br>', '\n').replace('<br/>', '\n')
                        murs_data[str(aname).lower()] = desc
            else:
                if count <= 5:
                    print(f"⚠️ [Debug] Erreur HTTP {r.status_code} sur {aname}.")
                    
        except Exception as e:
            pass # On ignore les erreurs réseaux pour continuer la boucle
            
        time.sleep(0.3) # ⏱️ Pause API

    # 4. Sauvegarde (AVEC DOSSIER QUOTIDIEN)
    out_dir = BASE_DATA_PATH / 'murs_scans'
    
    # Création du dossier du jour (ex: /app/data/murs_scans/2026-03-27)
    today = datetime.now().strftime("%Y-%m-%d")
    daily_dir = out_dir / today
    daily_dir.mkdir(parents=True, exist_ok=True)
    
    # Fichier d'archive avec l'heure
    timestamp = datetime.now().strftime("%H-%M-%S")
    archive_file = daily_dir / f"murs_alliances_{timestamp}.json"
    
    # Fichier "principal" pour que le bot le trouve toujours au même endroit
    latest_file = out_dir / "murs_alliances.json"
    
    # On sauvegarde l'archive du jour
    with open(archive_file, 'w', encoding='utf-8') as f:
        json.dump(murs_data, f, ensure_ascii=False, indent=2)
        
    # On écrase le fichier principal pour le bot Discord
    with open(latest_file, 'w', encoding='utf-8') as f:
        json.dump(murs_data, f, ensure_ascii=False, indent=2)
        
    print(f"✅ Murs sauvegardés avec succès ! ({len(murs_data)} descriptions récupérées)")
    print(f"📁 Archive créée : {archive_file.name}")

if __name__ == "__main__":
    scan_murs()
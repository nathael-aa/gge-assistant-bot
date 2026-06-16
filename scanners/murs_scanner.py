# -*- coding: utf-8 -*-
import json
import requests
from pathlib import Path
import time
import os
import logging
from datetime import datetime
from urllib.parse import quote
from logging.handlers import TimedRotatingFileHandler

# Configuration du Logger pour le script indépendant
os.makedirs('/app/logs', exist_ok=True)
logger = logging.getLogger("MursScanner")
logger.setLevel(logging.INFO)
formatter = logging.Formatter('%(asctime)s | [%(levelname)s] | %(message)s', datefmt='%Y-%m-%d %H:%M:%S')

file_handler = TimedRotatingFileHandler('/app/logs/murs_scanner.log', when="midnight", interval=1, backupCount=14, encoding='utf-8')
def custom_log_namer(default_name): return default_name.replace(".log.", "_") + ".log"
file_handler.namer = custom_log_namer
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)

console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)

BASE_DATA_PATH = Path(os.getenv('DATA_PATH', '/app/data'))

def scan_murs():
    logger.info("🚀 Début de l'extraction ultra-rapide des murs d'alliances (fly.dev)...")
    
    try:
        scans_dir = BASE_DATA_PATH / 'server_scans'
        if not scans_dir.exists():
            logger.error(f"❌ Le dossier {scans_dir} n'existe pas. Lance d'abord le server_scanner !")
            return
            
        server_files = list(scans_dir.rglob('server_*.json'))
        if not server_files:
            logger.error("❌ Aucun fichier serveur trouvé.")
            return
            
        latest_scan = max(server_files, key=lambda p: p.stat().st_mtime)
        with open(latest_scan, 'r', encoding='utf-8') as f:
            players_data = json.load(f).get('players', {})
    except Exception as e:
        logger.error(f"❌ Erreur lecture serveur: {e}")
        return

    alliances_map = {}
    for p_info in players_data.values():
        a_obj = p_info.get('alliance')
        a_name = a_obj.get('name') if isinstance(a_obj, dict) else (p_info.get('alliance_name') or a_obj)
        
        aid_val = p_info.get('allianceId') or p_info.get('alliance_id')
        if not aid_val and isinstance(a_obj, dict):
            aid_val = a_obj.get('allianceId') or a_obj.get('alliance_id')

        if not aid_val or str(aid_val) == "0" or str(aid_val).lower() == "none":
            continue
            
        raw_aid = str(aid_val)
        
        if a_name and a_name != "Sans alliance":
            if raw_aid.endswith('164') and len(raw_aid) > 3:
                real_aid = raw_aid[:-3]
            else:
                real_aid = raw_aid
                
            alliances_map[real_aid] = a_name

    logger.info(f"🎯 {len(alliances_map)} alliances uniques trouvées. Lancement du scan...")

    murs_data = {}
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json"
    })
    
    count = 0
    for real_aid, aname in alliances_map.items():
        count += 1
        if count % 20 == 0:
            logger.info(f"⏳ {count}/{len(alliances_map)} murs scannés...")
            
        try:
            encoded_payload = f"%22AID%22:{real_aid}"
            url_desc = f"https://empire-api.fly.dev/EmpirefourkingdomsExGG_2/ain/{encoded_payload}"
            
            r = session.get(url_desc, timeout=10)
            
            if r.status_code == 200:
                data = r.json()
                if data.get('return_code') == 0:
                    content = data.get('content', {})
                    if 'A' in content:
                        desc = str(content['A'].get('D', '')).lower()
                        desc = desc.replace('<br>', '\n').replace('<br/>', '\n')
                        murs_data[str(aname).lower()] = desc
            else:
                if count <= 5:
                    logger.debug(f"⚠️ [Debug] Erreur HTTP {r.status_code} sur {aname}.")
                    
        except Exception as e:
            pass
            
        time.sleep(0.3)

    out_dir = BASE_DATA_PATH / 'murs_scans'
    today = datetime.now().strftime("%Y-%m-%d")
    daily_dir = out_dir / today
    daily_dir.mkdir(parents=True, exist_ok=True)
    
    archive_file = daily_dir / f"murs_alliances_{datetime.now().strftime('%H-%M-%S')}.json"
    latest_file = out_dir / "murs_alliances.json"
    
    with open(archive_file, 'w', encoding='utf-8') as f:
        json.dump(murs_data, f, ensure_ascii=False, indent=2)
        
    with open(latest_file, 'w', encoding='utf-8') as f:
        json.dump(murs_data, f, ensure_ascii=False, indent=2)
        
    logger.info(f"✅ Murs sauvegardés avec succès ! ({len(murs_data)} descriptions, Fichier : {archive_file.name})")

if __name__ == "__main__":
    scan_murs()
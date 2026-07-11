# -*- coding: utf-8 -*-
import json
import requests
from pathlib import Path
import time
import os
import sys
import logging
from datetime import datetime
from urllib.parse import quote
from logging.handlers import TimedRotatingFileHandler

os.makedirs('/app/logs/murs', exist_ok=True)
logger = logging.getLogger("MursScanner")
logger.setLevel(logging.INFO)
formatter = logging.Formatter('%(asctime)s | [%(levelname)s] | %(message)s', datefmt='%Y-%m-%d %H:%M:%S')

file_handler = TimedRotatingFileHandler('/app/logs/murs/murs_scanner.log', when="midnight", interval=1, backupCount=14, encoding='utf-8')
def custom_log_namer(default_name): return default_name.replace(".log.", "_") + ".log"
file_handler.namer = custom_log_namer
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)

console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)

class MursScanner:
    def __init__(self):
        self.base_data_path = Path(os.getenv('DATA_PATH', '/app/data'))
        self.serveurs_config_path = self.base_data_path / 'configs' / 'serveurs.json'
        self.users_config_path = self.base_data_path / 'configs' / 'users.json'
        self.rankings_config_path = self.base_data_path / 'configs' / 'rankings_config.json'
        
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json"
        })

    def get_active_servers(self):
        active_servers = set()
        
        # 🏢 1. Lecture des configurations de serveurs Discord (Guilds)
        if self.serveurs_config_path.exists():
            try:
                with open(self.serveurs_config_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    for guild_id, config in data.items():
                        srv = config.get("gge_server")
                        if srv: active_servers.add(srv)
            except Exception as e: 
                logger.error(f"⚠️ Erreur lecture serveurs.json : {e}")
                
        # 👤 2. Lecture des configurations individuelles (Users)
        if hasattr(self, 'users_config_path') and self.users_config_path.exists():
            try:
                with open(self.users_config_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    for user_id, config in data.items():
                        srv = config.get("gge_server")
                        if srv: active_servers.add(srv)
            except Exception as e:
                logger.error(f"⚠️ Erreur lecture users.json : {e}")

        if not active_servers: 
            active_servers.add("E4K_FR1")
            
        return list(active_servers)

    def nettoyer_vieux_fichiers(self, jours=14):
        logger.info(f"🧹 Nettoyage des archives de murs de plus de {jours} jours...")
        now = time.time()
        fichiers_supprimes = 0
        out_dir = self.base_data_path / 'murs_scans'
        
        if not out_dir.exists(): return
            
        for filepath in out_dir.rglob('murs_alliances_*.json'):
            if filepath.is_file():
                if (now - filepath.stat().st_mtime) / (24 * 3600) > jours:
                    try:
                        filepath.unlink()
                        fichiers_supprimes += 1
                    except: pass
        logger.info(f"✅ Nettoyage terminé : {fichiers_supprimes} archives supprimées.")

    def scan_murs_serveur(self, serveur):
        logger.info(f"🚀 Extraction des murs pour le serveur : {serveur}")
        
        # 🔍 1. Récupération dynamique de la zone API depuis rankings_config.json
        zone_api = None
        if self.rankings_config_path.exists():
            try:
                with open(self.rankings_config_path, 'r', encoding='utf-8') as f:
                    rankings_config = json.load(f)
                    
                    # On cible la clé "servers" du JSON
                    servers_dict = rankings_config.get("servers", {})
                    
                    # On convertit le nom reçu en minuscules (ex: "GR1" devient "gr1")
                    serveur_lower = serveur.lower()
                    
                    # 1ère tentative : on cherche directement le nom (ex: "e4k_gr1" ou "fr1")
                    zone_api = servers_dict.get(serveur_lower)
                    
                    # 2ème tentative : si on reçoit juste "gr1", on tente de rajouter "e4k_" devant
                    if not zone_api and not serveur_lower.startswith("e4k_"):
                        zone_api = servers_dict.get(f"e4k_{serveur_lower}")
                        
            except Exception as e:
                logger.error(f"❌ Erreur lors de la lecture de {self.rankings_config_path.name} : {e}")
        
        # 2. Lecture du dernier scan serveur correspondant
        try:
            scans_dir = self.base_data_path / 'server_scans' / serveur
            if not scans_dir.exists():
                logger.error(f"❌ Dossier introuvable pour {serveur}. Scan ignoré.")
                return
                
            server_files = list(scans_dir.rglob('server_*.json'))
            if not server_files:
                logger.error(f"❌ Aucun fichier serveur trouvé pour {serveur}.")
                return
                
            latest_scan = max(server_files, key=lambda p: p.stat().st_mtime)
            with open(latest_scan, 'r', encoding='utf-8') as f:
                players_data = json.load(f).get('players', {})
        except Exception as e:
            logger.error(f"❌ Erreur lecture serveur {serveur}: {e}")
            return

        # 3. Cartographie des Alliances
        alliances_map = {}
        for p_info in players_data.values():
            a_obj = p_info.get('alliance')
            a_name = a_obj.get('name') if isinstance(a_obj, dict) else (p_info.get('alliance_name') or a_obj)
            aid_val = p_info.get('allianceId') or p_info.get('alliance_id')
            if not aid_val and isinstance(a_obj, dict):
                aid_val = a_obj.get('allianceId') or a_obj.get('alliance_id')

            if not aid_val or str(aid_val) in ["0", "None"]: continue
                
            raw_aid = str(aid_val)
            if a_name and a_name != "Sans alliance":
                if len(raw_aid) > 3:
                    real_aid = raw_aid[:-3]
                else:
                    real_aid = raw_aid
                    
                alliances_map[real_aid] = a_name

        logger.info(f"🎯 {len(alliances_map)} alliances uniques trouvées sur {serveur}.")

        # 4. Extraction de l'API Fly.dev
        murs_data = {}
        count = 0
        
        for real_aid, aname in alliances_map.items():
            count += 1
            if count % 50 == 0: logger.info(f"⏳ {count}/{len(alliances_map)} murs scannés sur {serveur}...")
                
            try:
                encoded_payload = f"%22AID%22:{real_aid}"
                url_desc = f"https://empire-api.fly.dev/{zone_api}/ain/{encoded_payload}"
                r = self.session.get(url_desc, timeout=10)
                
                if r.status_code == 200:
                    data = r.json()
                    if data.get('return_code') == 0:
                        content = data.get('content', {})
                        if 'A' in content:
                            desc = str(content['A'].get('D', '')).lower()
                            desc = desc.replace('<br>', '\n').replace('<br/>', '\n')
                            murs_data[str(aname).lower()] = desc
                else:
                    if count <= 2: logger.debug(f"⚠️ Erreur HTTP {r.status_code} sur {aname} ({serveur}).")
            except: pass
            time.sleep(0.2)

        # 5. Sauvegarde dans les bons sous-dossiers
        out_dir = self.base_data_path / 'murs_scans' / serveur
        today = datetime.now().strftime("%Y-%m-%d")
        daily_dir = out_dir / today
        daily_dir.mkdir(parents=True, exist_ok=True)
        
        archive_file = daily_dir / f"murs_alliances_{datetime.now().strftime('%H-%M-%S')}.json"
        latest_file = out_dir / "murs_alliances.json"
        
        with open(archive_file, 'w', encoding='utf-8') as f:
            json.dump(murs_data, f, ensure_ascii=False, indent=2)
            
        with open(latest_file, 'w', encoding='utf-8') as f:
            json.dump(murs_data, f, ensure_ascii=False, indent=2)
            
        logger.info(f"✅ Murs {serveur} sauvegardés ! ({len(murs_data)} alliances)")

    def run(self, serveur_cible=None):
        if serveur_cible:
            logger.info(f"🚀 LANCEMENT D'URGENCE MURS POUR : {serveur_cible}")
            self.scan_murs_serveur(serveur_cible)
        else:
            servers_to_scan = self.get_active_servers()
            logger.info(f"🚀 DÉMARRAGE MULTI-SCAN MURS : {len(servers_to_scan)} serveur(s)")
            
            for index, srv in enumerate(servers_to_scan):
                self.scan_murs_serveur(srv)
                if index < len(servers_to_scan) - 1:
                    logger.info(f"⏳ Pause de 30 secondes avant le prochain serveur...")
                    time.sleep(30)
                    
            self.nettoyer_vieux_fichiers(jours=31)

if __name__ == "__main__":
    scanner = MursScanner()
    if len(sys.argv) > 1:
        scanner.run(serveur_cible=sys.argv[1])
    else:
        scanner.run()
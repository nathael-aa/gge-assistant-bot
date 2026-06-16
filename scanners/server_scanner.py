import requests
import json
from datetime import datetime
from pathlib import Path
import time
import os
import logging
from logging.handlers import TimedRotatingFileHandler

# Configuration du Logger pour le script indépendant
os.makedirs('/app/logs', exist_ok=True)
logger = logging.getLogger("ServerScanner")
logger.setLevel(logging.INFO)
formatter = logging.Formatter('%(asctime)s | [%(levelname)s] | %(message)s', datefmt='%Y-%m-%d %H:%M:%S')

file_handler = TimedRotatingFileHandler('/app/logs/server_scanner.log', when="midnight", interval=1, backupCount=14, encoding='utf-8')
def custom_log_namer(default_name): return default_name.replace(".log.", "_") + ".log"
file_handler.namer = custom_log_namer
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)

console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)

class FullServerScanner:
    def __init__(self, config_file='/app/config.json'):
        if not os.path.exists(config_file):
            logger.error(f"❌ ERREUR CRITIQUE : Le fichier {config_file} est introuvable !")
            exit(1)
            
        with open(config_file, 'r', encoding='utf-8') as f:
            self.config = json.load(f)
        
        self.api_url = "https://api.gge-tracker.com/api/v1"
        self.server = self.config.get('server', 'E4K_FR1')
        self.output_dir = Path('/app/data/server_scans')
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        self.session = requests.Session()
        self.headers = {
            "gge-server": self.server,
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) GGE-Assistant/2.0"
        }
        
        logger.info(f"✅ Scanner initialisé pour {self.server} (via GGE-Tracker)")

    def get_daily_dir(self):
        today = datetime.now().strftime("%Y-%m-%d")
        daily_dir = self.output_dir / today
        daily_dir.mkdir(parents=True, exist_ok=True)
        return daily_dir

    def get_all_players(self):
        logger.info(f"🔍 SCAN COMPLET du serveur {self.server} - Connexion à l'API GGE-Tracker...")
        
        all_players = {}
        page = 1
        total_pages = 1
        erreurs_suite = 0
        
        while page <= total_pages:
            try:
                url = f"{self.api_url}/players"
                params = {
                    "limit": 100, 
                    "page": page,
                    "banFilter": 0,
                    "allianceFilter": -1,
                    "protectionFilter": -1,
                    "inactiveFilter": 1,
                    "kingdomFilter": 999,
                    "orderBy": "might_current",
                    "orderType": "DESC"
                }
                
                response = self.session.get(url, headers=self.headers, params=params, timeout=15)
                
                if response.status_code != 200:
                    logger.warning(f"❌ Erreur API ({response.status_code}) à la page {page}: Pause de 5s...")
                    time.sleep(5)
                    erreurs_suite += 1
                    if erreurs_suite >= 3:
                        logger.error(f"⏭️ Trop d'erreurs, on ignore la page {page}.")
                        page += 1
                        erreurs_suite = 0
                    continue
                
                data = response.json()
                
                if page == 1:
                    pagination = data.get('pagination', {})
                    total_pages = pagination.get('total_pages', 1)
                    total_items = pagination.get('total_items_count', '?')
                    logger.info(f"📊 {total_items} joueurs à scanner sur {total_pages} pages.")
                
                players_list = data.get('players', [])
                if not players_list:
                    break
                
                for p in players_list:
                    name = p.get('player_name')
                    if not name:
                        continue
                        
                    alliance_raw = p.get('alliance_name') or 'Sans alliance'
                    if alliance_raw.startswith('[') and alliance_raw.endswith(']'):
                        alliance_raw = alliance_raw.strip('[]')
                        
                    all_players[name] = {
                        'player_id': p.get('player_id'),
                        'rank': 0, 
                        'score': 0,
                        'category': 1,
                        'alliance': alliance_raw,
                        'alliance_id': p.get('alliance_id'),
                        'level': p.get('level', 0),
                        'legendary_level': p.get('legendary_level', 0),
                        'honor': p.get('honor', 0),
                        'victory_points': 0,
                        'main_points': p.get('might_current', 0),
                        'structures': []
                    }
                
                if page % 50 == 0 or page == total_pages:
                    progress = (page / total_pages) * 100
                    logger.info(f"📄 Page {page:>5}/{total_pages} ({progress:>5.1f}%) | {len(all_players):>6} joueurs en mémoire")
                
                page += 1
                erreurs_suite = 0 
                time.sleep(0.4)
                
            except KeyboardInterrupt:
                logger.warning("⚠️ Scan interrompu par l'utilisateur")
                break
            except Exception as e:
                logger.error(f"❌ Erreur inattendue page {page}: {e}")
                time.sleep(3)
                erreurs_suite += 1
                if erreurs_suite >= 3:
                    logger.error(f"⏭️ Abandon de la page {page} suite à 3 erreurs.")
                    page += 1
                    erreurs_suite = 0
        
        return all_players

    def save_results(self, players_data, duration):
        timestamp = datetime.now().strftime("%H%M%S")
        filename = f"server_{timestamp}.json"
        filepath = self.get_daily_dir() / filename
        
        alliances = set(p['alliance'] for p in players_data.values() if p['alliance'] != 'Sans alliance')
        
        output_data = {
            'scan_date': datetime.now().isoformat(),
            'scan_duration': round(duration, 2),
            'server': self.server,
            'total_players': len(players_data),
            'stats': {
                'total_alliances': len(alliances),
                'total_capitals': 0,
                'total_outposts': 0,
                'total_castles': 0
            },
            'players': players_data
        }
        
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(output_data, f, indent=2, ensure_ascii=False)
        
        # 🛠️ Correction de la variable muette : stockage et affichage du poids du fichier
        file_size_mb = filepath.stat().st_size / (1024 * 1024)
        logger.info(f"💾 Résultats sauvegardés: {filepath} ({file_size_mb:.2f} Mo)")
        return filepath
    
    def run(self):
        logger.info(f"🚀 DEBUT DU SCAN SERVEUR COMPLET : {self.server}")
        start_time = time.time()
        players = self.get_all_players()
        
        if not players:
            logger.error("❌ Aucun joueur trouvé. L'API a peut-être refusé la connexion.")
            return None
        
        duration = time.time() - start_time
        filepath = self.save_results(players, duration)
        return filepath

if __name__ == "__main__":
    scanner = FullServerScanner()
    scanner.run()
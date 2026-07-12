# -*- coding: utf-8 -*-
import requests
import json
from datetime import datetime
from pathlib import Path
import time
import os
import sys
import logging
from logging.handlers import TimedRotatingFileHandler

os.makedirs('/app/logs/serveur', exist_ok=True)
logger = logging.getLogger("ServerScanner")
logger.setLevel(logging.INFO)
formatter = logging.Formatter('%(asctime)s | [%(levelname)s] | %(message)s', datefmt='%Y-%m-%d %H:%M:%S')

file_handler = TimedRotatingFileHandler('/app/logs/serveur/server_scanner.log', when="midnight", interval=1, backupCount=14, encoding='utf-8')
def custom_log_namer(default_name): return default_name.replace(".log.", "_") + ".log"
file_handler.namer = custom_log_namer
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)

console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)

class FullServerScanner:
    def __init__(self):
        self.api_url = "https://api.gge-tracker.com/api/v1"
        data_path = os.getenv('DATA_PATH', '/app/data')
        self.base_output_dir = Path(data_path) / 'server_scans'
        
        self.configuration_path = Path(data_path) / 'configs' / 'configuration.json'
        
        self.session = requests.Session()
        self.server = None 
        self.headers = {
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) GGE-Assistant/2.0"
        }
        
        # 🚨 ALARMES DISCORD (Remplace par ton URL de Webhook)
        self.webhook_url = "https://discord.com/api/webhooks/1525853187280474222/Y4fycCy0IW019tZCMhGOdJzS1vqg7wSYn1ZEhtVO2o9Atuwr8ek-zieIsN9kG86Ndlcq"

    def send_discord_alert(self, title, description, color=16711680):
        """Envoie une notification sur Discord via Webhook"""
        if not getattr(self, 'webhook_url', None) or self.webhook_url == "https://discord.com/api/webhooks/1525853187280474222/Y4fycCy0IW019tZCMhGOdJzS1vqg7wSYn1ZEhtVO2o9Atuwr8ek-zieIsN9kG86Ndlcq":
            return
            
        try:
            payload = {
                "embeds": [{
                    "title": title,
                    "description": description,
                    "color": color,
                    "timestamp": datetime.utcnow().isoformat()
                }]
            }
            requests.post(self.webhook_url, json=payload, timeout=5)
        except Exception as e:
            logger.error(f"❌ Impossible d'envoyer l'alerte Discord : {e}")

    def get_active_servers(self):
        active_servers = set()

        if self.configuration_path.exists():
            try:
                with open(self.configuration_path, 'r', encoding='utf-8') as f:
                    config_data = json.load(f)
                    
                # 🔄 Lecture de la nouvelle section "active_servers"
                servers_status = config_data.get("active_servers", {})
                
                # On ajoute le serveur uniquement si sa valeur est True
                for srv_name, is_active in servers_status.items():
                    if is_active is True:
                        active_servers.add(srv_name.upper())
                        
            except Exception as e:
                logger.error(f"❌ Erreur lecture configuration.json : {e}")

        # Sécurité de secours au cas où le fichier est cassé
        if not active_servers:
            logger.warning("⚠️ Aucun serveur actif trouvé. Fallback sur E4K_FR1.")
            active_servers.add("E4K_FR1")
            
        return list(active_servers)

    def get_daily_dir(self, serveur):
        today = datetime.now().strftime("%Y-%m-%d")
        daily_dir = self.base_output_dir / serveur / today
        daily_dir.mkdir(parents=True, exist_ok=True)
        return daily_dir

    def get_all_players(self):
        logger.info(f"🔍 SCAN COMPLET du serveur {self.server} - Connexion à l'API...")
        
        all_players = {}
        page = 1
        total_pages = 1
        erreurs_suite = 0
        
        while page <= total_pages:
            try:
                url = f"{self.api_url}/players"
                params = {
                    "limit": 100, "page": page, "banFilter": 0, "allianceFilter": -1,
                    "protectionFilter": -1, "inactiveFilter": 1, "kingdomFilter": 999,
                    "orderBy": "might_current", "orderType": "DESC"
                }
                
                response = self.session.get(url, headers=self.headers, params=params, timeout=15)
                
                if response.status_code != 200:
                    logger.warning(f"❌ Erreur API ({response.status_code}) à la page {page}: Pause de 5s...")
                    time.sleep(5)
                    erreurs_suite += 1
                    if erreurs_suite >= 3:
                        logger.error(f"⏭️ Trop d'erreurs, on ignore la page {page}.")
                        self.send_discord_alert("⚠️ Alerte Scanner (API Instable)", f"Serveur : **{self.server}**\nTrop d'erreurs API à la suite. La page {page} a été ignorée. Le scan sera incomplet.", 16753920) # Orange
                        page += 1
                        erreurs_suite = 0
                    continue
                
                data = response.json()
                
                if page == 1:
                    pagination = data.get('pagination', {})
                    total_pages = pagination.get('total_pages', 1)
                    total_items = pagination.get('total_items_count', '?')
                    logger.info(f"📊 {total_items} joueurs à scanner sur {total_pages} pages pour {self.server}.")
                
                players_list = data.get('players', [])
                if not players_list: break
                
                for p in players_list:
                    name = p.get('player_name')
                    if not name: continue
                        
                    alliance_raw = p.get('alliance_name') or 'Sans alliance'
                    if alliance_raw.startswith('[') and alliance_raw.endswith(']'):
                        alliance_raw = alliance_raw.strip('[]')
                        
                    all_players[name] = {
                        'player_id': p.get('player_id'), 'rank': 0, 'score': 0, 'category': 1,
                        'alliance': alliance_raw, 'alliance_id': p.get('alliance_id'),
                        'level': p.get('level', 0), 'legendary_level': p.get('legendary_level', 0),
                        'honor': p.get('honor', 0), 'victory_points': 0,
                        'main_points': p.get('might_current', 0), 'structures': []
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
                    self.send_discord_alert("⚠️ Alerte Scanner (API Instable)", f"Serveur : **{self.server}**\nTrop d'erreurs inattendues à la suite. La page {page} a été ignorée. Le scan sera incomplet.", 16753920) # Orange
                    page += 1
                    erreurs_suite = 0
        
        return all_players

    def save_results(self, players_data, duration, serveur):
        timestamp = datetime.now().strftime("%H%M%S")
        filename = f"server_{timestamp}.json"
        filepath = self.get_daily_dir(serveur) / filename
        
        alliances = set(p['alliance'] for p in players_data.values() if p['alliance'] != 'Sans alliance')
        
        output_data = {
            'scan_date': datetime.now().isoformat(), 'scan_duration': round(duration, 2),
            'server': serveur, 'total_players': len(players_data),
            'stats': {'total_alliances': len(alliances), 'total_capitals': 0, 'total_outposts': 0, 'total_castles': 0},
            'players': players_data
        }
        
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(output_data, f, indent=2, ensure_ascii=False)
        
        file_size_mb = filepath.stat().st_size / (1024 * 1024)
        logger.info(f"💾 Résultats sauvegardés: {filepath} ({file_size_mb:.2f} Mo)")
        
        flag_file = Path('/app/data/scan.flag')
        with open(flag_file, 'w') as f: f.write(serveur)
        
        return filepath

    def nettoyer_vieux_fichiers(self, jours=3):
        logger.info(f"🧹 Démarrage du nettoyage des archives de plus de {jours} jours...")
        now = time.time()
        fichiers_supprimes = 0
        
        if not self.base_output_dir.exists(): return
            
        for filepath in self.base_output_dir.rglob('server_*.json'):
            if filepath.is_file():
                file_age_days = (now - filepath.stat().st_mtime) / (24 * 3600)
                if file_age_days > jours:
                    try:
                        filepath.unlink()
                        fichiers_supprimes += 1
                    except Exception as e:
                        logger.error(f"❌ Erreur lors de la suppression de {filepath}: {e}")
                        
        for dirpath in sorted(self.base_output_dir.rglob('*'), key=lambda p: len(p.parts), reverse=True):
            if dirpath.is_dir() and not any(dirpath.iterdir()):
                try: dirpath.rmdir()
                except: pass
                
        logger.info(f"✅ Nettoyage terminé : {fichiers_supprimes} anciens fichiers supprimés.")
    
    def run(self, serveur_cible=None):
        try:
            if serveur_cible:
                servers_to_scan = [serveur_cible]
                logger.info(f"🚀 LANCEMENT D'URGENCE POUR : {serveur_cible}")
            else:
                servers_to_scan = self.get_active_servers()
                logger.info(f"🚀 DÉMARRAGE DU MULTI-SCAN QUOTIDIEN : {len(servers_to_scan)} serveur(s) : {', '.join(servers_to_scan)}")
            
            for index, srv in enumerate(servers_to_scan):
                self.server = srv
                self.headers["gge-server"] = self.server 
                
                start_time = time.time()
                players = self.get_all_players()
                
                if not players:
                    logger.error(f"❌ Aucun joueur trouvé pour {self.server}.")
                    self.send_discord_alert("❌ Échec Critique du Scanner", f"Le scan de la base de données pour le serveur **{self.server}** a totalement échoué (0 joueur trouvé). L'API Tracker est peut-être hors ligne.", 16711680) # Rouge
                else:
                    duration = time.time() - start_time
                    self.save_results(players, duration, self.server)
                    # Alerte verte optionnelle (tu peux la commenter si c'est trop de spam sur Discord)
                    # self.send_discord_alert("✅ Scan Terminé", f"Le serveur **{self.server}** a été mis à jour avec succès.\n👥 Joueurs enregistrés : **{len(players)}**\n⏱️ Durée : {round(duration, 1)}s", 65280)
                
                if not serveur_cible and index < len(servers_to_scan) - 1:
                    logger.info(f"⏳ Scan de {srv} terminé. Pause de 15 secondes...")
                    time.sleep(15)
                    
            if not serveur_cible:
                self.nettoyer_vieux_fichiers(jours=3)
                self.send_discord_alert("✅ Multi-Scan Quotidien Terminé", f"Tous les serveurs actifs ({len(servers_to_scan)}) ont été mis à jour dans la base de données.", 65280)
                
        except Exception as e:
            logger.error(f"🚨 CRASH FATAL DU SCRIPT : {e}")
            self.send_discord_alert("🚨 CRASH DU SCANNER", f"Le script `server_scanner.py` a planté de manière inattendue :\n```py\n{e}\n```", 16711680)

if __name__ == "__main__":
    scanner = FullServerScanner()
    if len(sys.argv) > 1:
        serveur_demande = sys.argv[1]
        scanner.run(serveur_cible=serveur_demande)
    else:
        scanner.run()
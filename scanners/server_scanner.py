import requests
import json
from datetime import datetime
from pathlib import Path
import time
import os

class FullServerScanner:
    def __init__(self, config_file='/app/config.json'):
        # 🛡️ SÉCURITÉ : On vérifie que la config existe bien
        if not os.path.exists(config_file):
            print(f"❌ ERREUR CRITIQUE : Le fichier {config_file} est introuvable !")
            exit(1)
            
        with open(config_file, 'r', encoding='utf-8') as f:
            self.config = json.load(f)
        
        self.api_url = "https://api.gge-tracker.com/api/v1"
        self.server = self.config.get('server', 'E4K_FR1') # Valeur par défaut si oubliée
        # Dossier de sortie
        self.output_dir = Path('/app/data/server_scans')
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        # Préparation de la session HTTP
        self.session = requests.Session()
        self.headers = {
            "gge-server": self.server,
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) GGE-Assistant/2.0"
        }
        
        print(f"✅ Scanner initialisé pour {self.server} (via GGE-Tracker)")

    def get_daily_dir(self):
        today = datetime.now().strftime("%Y-%m-%d")
        daily_dir = self.output_dir / today
        daily_dir.mkdir(parents=True, exist_ok=True)
        return daily_dir

    def get_all_players(self):
        print(f"\n🔍 SCAN COMPLET du serveur {self.server}")
        print(f"   Connexion à l'API GGE-Tracker en cours...\n")
        
        all_players = {}
        page = 1
        total_pages = 1
        erreurs_suite = 0 # 🛡️ Compteur anti-boucle infinie
        
        while page <= total_pages:
            try:
                url = f"{self.api_url}/players?page={page}&orderBy=player_name&orderType=ASC"
                params = {
                    "limit": 100, 
                    "page": page,
                    "banFilter": 0,
                    "allianceFilter": -1,
                    "protectionFilter": -1,
                    "inactiveFilter": 1,
                    "kingdomFilter": 999,  # INDISPENSABLE : pour scanner tous les mondes d'un coup
                    "orderBy": "might_current",
                    "orderType": "DESC"
                }
                
                response = self.session.get(url, headers=self.headers, params=params, timeout=15)
                
                if response.status_code != 200:
                    print(f"  ❌ Erreur API ({response.status_code}): Pause de 5s...")
                    time.sleep(5)
                    erreurs_suite += 1
                    if erreurs_suite >= 3:
                        print(f"  ⏭️ Trop d'erreurs, on ignore la page {page}.")
                        page += 1
                        erreurs_suite = 0
                    continue
                
                data = response.json()
                
                if page == 1:
                    pagination = data.get('pagination', {})
                    total_pages = pagination.get('total_pages', 1)
                    total_items = pagination.get('total_items_count', '?')
                    print(f"  📊 {total_items} joueurs à scanner sur {total_pages} pages.")
                    print(f"  ⏳ Cette opération peut prendre du temps. Laissez tourner...\n")
                
                players_list = data.get('players', [])
                if not players_list:
                    break
                
                for p in players_list:
                    name = p.get('player_name')
                    if not name:
                        continue
                        
                    all_players[name] = {
                        'player_id': p.get('player_id'),
                        'rank': 0, 
                        'score': 0,
                        'category': 1,
                        'alliance': p.get('alliance_name') or 'Sans alliance',
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
                    print(f"  📄 Page {page:>5}/{total_pages} ({progress:>5.1f}%) | {len(all_players):>6} joueurs en mémoire", flush=True)
                
                page += 1
                erreurs_suite = 0 # Réinitialise si la page a fonctionné
                time.sleep(0.4)
                
            except KeyboardInterrupt:
                print(f"\n⚠️ Scan interrompu par l'utilisateur")
                break
            except Exception as e:
                print(f"  ❌ Erreur inattendue page {page}: {e}")
                time.sleep(3)
                erreurs_suite += 1
                if erreurs_suite >= 3:
                    print(f"  ⏭️ Abandon de la page {page} suite à 3 erreurs.")
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
        
        file_size = filepath.stat().st_size / (1024 * 1024)
        
        print(f"\n💾 Résultats sauvegardés: {filepath}")
        print(f"   📊 {len(players_data)} joueurs")
        print(f"   💾 Taille: {file_size:.2f} MB")
        
        return filepath
    
    def run(self):
        print(f"\n{'#'*60}")
        print(f"#  SCAN COMPLET DU SERVEUR {self.server}")
        print(f"{'#'*60}\n")
        
        start_time = time.time()
        players = self.get_all_players()
        
        if not players:
            print("\n❌ Aucun joueur trouvé. L'API a peut-être refusé la connexion.")
            return None
        
        duration = time.time() - start_time
        filepath = self.save_results(players, duration)
        
        alliances = {}
        for p in players.values():
            if p['alliance'] != 'Sans alliance':
                alliances[p['alliance']] = alliances.get(p['alliance'], 0) + 1
        
        top_alliances = sorted(alliances.items(), key=lambda x: x[1], reverse=True)[:5]
        
        print(f"\n{'='*60}")
        print(f"✅ SCAN COMPLET TERMINÉ")
        print(f"{'='*60}")
        print(f"  ⏱️  Durée: {duration//60:.0f}m {duration%60:.0f}s")
        print(f"  👥 Joueurs enregistrés: {len(players)}")
        print(f"  🏰 Alliances actives: {len(alliances)}")
        print(f"\n🏆 Top 5 Alliances (par nombre de membres):")
        for i, (name, count) in enumerate(top_alliances, 1):
            print(f"  {i}. {name}: {count} membres")
        print(f"{'='*60}\n")
        
        return filepath

if __name__ == "__main__":
    scanner = FullServerScanner()
    scanner.run()
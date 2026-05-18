# -*- coding: utf-8 -*-
import aiohttp
import asyncio
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

logger = logging.getLogger(__name__)

class PlayerDetailsCollector:
    """Récupère et parse les détails d'un joueur depuis l'API GGE-Tracker."""
    def __init__(self):
        # 🛡️ Petite sécurité gardée : on lit config.json en priorité, sinon .env, sinon 'FR1'
        self.server = self._load_server_name()
        self.api_url = "https://api.gge-tracker.com/api/v1"
        
        self.details_dir = Path('/app/data/player_details')
        self.details_dir.mkdir(parents=True, exist_ok=True)
        
        self.headers = {
            "gge-server": self.server,
            "accept": "application/json",
            "User-Agent": "Mozilla/5.0 GGE-Assistant/5.0"
        }
        
        self.castle_types = {
            1: "Château Principal", 3: "Capitale", 4: "Avant-Poste",
            10: "Village à Ressource", 12: "Château Secondaire",
            22: "Cité Marchande", 23: "Tour Royale", 24: "Ile aux Ressources", 26: "Monument"
        }
        self.worlds = {
            0: "Le Grand Empire", 1: "Les Sables Brûlants",
            2: "Glacier Éternel", 3: "Pics du Feu", 4: "Les Îles Orageuses"
        }

    def _load_server_name(self):
        """Tente de lire le serveur depuis config.json, sinon utilise les variables d'environnement"""
        try:
            config_path = Path(__file__).parent / 'config.json'
            if config_path.exists():
                with open(config_path, 'r', encoding='utf-8') as f:
                    return json.load(f).get('server', os.getenv('GGE_SERVER', 'FR1'))
        except Exception:
            pass
        return os.getenv('GGE_SERVER', 'FR1')

    def get_daily_dir(self):
        today = datetime.now().strftime("%Y-%m-%d")
        daily_dir = self.details_dir / today
        daily_dir.mkdir(parents=True, exist_ok=True)
        return daily_dir
    
    def get_castle_type_label(self, type_id):
        return self.castle_types.get(type_id, f"Type inconnu ({type_id})")
    
    def get_world_label(self, world_id):
        return self.worlds.get(world_id, f"Monde inconnu ({world_id})")

    async def get_player_full_data(self, session, player_name):
        """Récupère les données complètes en asynchrone (ne bloque pas le bot)."""
        safe_name = quote(str(player_name))
        search_url = f"{self.api_url}/players/{safe_name}"
        
        try:
            async with session.get(search_url, headers=self.headers, timeout=15) as response:
                if response.status != 200:
                    logger.warning(f"Joueur {player_name} introuvable (HTTP {response.status})")
                    return None
                    
                # TA LOGIQUE ORIGINALE RESTAURÉE ICI :
                basic_info = await response.json()
                if isinstance(basic_info, list):
                    if not basic_info: return None
                    basic_info = basic_info[0]
                    
                player_id = basic_info.get('player_id')
                if not player_id:
                    return None
                
                stats_url = f"{self.api_url}/statistics/ranking/player/{player_id}"
                async with session.get(stats_url, headers=self.headers, timeout=15) as stats_response:
                    stats_data = {}
                    if stats_response.status == 200:
                        stats_data = await stats_response.json()
                    
                return self._build_parsed_data(basic_info, stats_data)
                
        except asyncio.TimeoutError:
            logger.error(f"Timeout lors de la recherche du joueur {player_name}")
            return None
        except Exception as e:
            logger.error(f"Erreur API pour {player_name}: {e}")
            return None

    def _build_parsed_data(self, basic_info, stats_data):
        outposts = []
        vassal_villages = []
        
        # 1. Le Grand Empire
        for c in stats_data.get('castles', []):
            if len(c) >= 3:
                t_id = c[2]
                struct = {
                    'world_id': 0, 'coords_x': c[0], 'coords_y': c[1],
                    'type': t_id, 'world_label': self.get_world_label(0),
                    'type_label': self.get_castle_type_label(t_id)
                }
                if t_id == 10: vassal_villages.append(struct)
                else: outposts.append(struct)
                    
        # 2. Les Autres Royaumes
        for c in stats_data.get('castles_realm', []):
            if len(c) >= 4:
                w_id, t_id = c[0], c[3]
                struct = {
                    'world_id': w_id, 'coords_x': c[1], 'coords_y': c[2],
                    'type': t_id, 'world_label': self.get_world_label(w_id),
                    'type_label': self.get_castle_type_label(t_id)
                }
                if t_id == 10: vassal_villages.append(struct)
                else: outposts.append(struct)

        # 3. Villages à Ressources (Correction API)
        for v in stats_data.get('villages', []):
            if len(v) >= 3:
                w_id = v[0]
                struct = {
                    'world_id': w_id, 'coords_x': v[1], 'coords_y': v[2],
                    'type': 10, 'world_label': self.get_world_label(w_id),
                    'type_label': "Village à Ressource"
                }
                vassal_villages.append(struct)

        parsed_data = {
            'player_id': basic_info.get('player_id'),
            'name': basic_info.get('player_name'),
            'level': basic_info.get('level', 0) or stats_data.get('level', 0),
            'legendary_level': basic_info.get('legendary_level', 0) or stats_data.get('legendary_level', 0),
            'honor': basic_info.get('honor', 0),
            'main_points': stats_data.get('might_current', 0),
            'alliance': {
                'id': basic_info.get('alliance_id'),
                'name': basic_info.get('alliance_name') or stats_data.get('alliance_name') or 'Sans alliance',
                'rank': basic_info.get('alliance_rank') or stats_data.get('alliance_rank')
            },
            'outposts': outposts,
            'vassal_villages': vassal_villages 
        }
        
        return {
            'collected_at': datetime.now().isoformat(),
            'player_name': basic_info.get('player_name'),
            'server': self.server,
            'parsed_data': parsed_data
        }
    
    def save_player_details(self, player_name, full_data):
        if not full_data: return None
        timestamp = datetime.now().strftime("%H-%M-%S")
        clean_name = str(player_name).replace(' ', '_').replace('/', '-').replace('\\', '-')
        filename = f"{clean_name}_{timestamp}.json"
        filepath = self.get_daily_dir() / filename
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(full_data, f, indent=2, ensure_ascii=False)
        return filepath

async def _cli_main(player_name):
    collector = PlayerDetailsCollector()
    async with aiohttp.ClientSession() as session:
        data = await collector.get_player_full_data(session, player_name)
        if data:
            fp = collector.save_player_details(player_name, data)
            print(f"JSON_FILE:{fp}")
            return 0
        return 1

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        sys.exit(asyncio.run(_cli_main(' '.join(sys.argv[1:]))))
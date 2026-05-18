# -*- coding: utf-8 -*-
import asyncio
import aiohttp
import json
from datetime import datetime
from pathlib import Path
import sys
from urllib.parse import quote
import logging

logger = logging.getLogger(__name__)

class AllianceDetailsCollector:
    def __init__(self, config_file='config.json'):
        config_path = Path(config_file)
        if config_path.exists():
            with open(config_file, 'r', encoding='utf-8') as f:
                self.config = json.load(f)
        else:
            self.config = {'server': 'E4K_FR1'}
        
        self.server = self.config.get('server', 'E4K_FR1')
        self.api_url = "https://api.gge-tracker.com/api/v1"
        
        self.details_dir = Path('/app/data/alliance_details')
        self.details_dir.mkdir(parents=True, exist_ok=True)
        
        self.headers = {
            "gge-server": self.server,
            "accept": "application/json",
            "User-Agent": "Mozilla/5.0 GGE-Assistant/5.0"
        }

    def get_daily_dir(self):
        today = datetime.now().strftime("%Y-%m-%d")
        daily_dir = self.details_dir / today
        daily_dir.mkdir(parents=True, exist_ok=True)
        return daily_dir

    async def fetch_json(self, session, url, custom_timeout=30):
        try:
            async with session.get(url, headers=self.headers, timeout=custom_timeout) as resp:
                if resp.status == 200:
                    return await resp.json()
        except Exception as e:
            logger.debug(f"Erreur fetch {url}: {e}")
        return None

    async def get_alliance_full_data(self, alliance_name):
        safe_name = quote(str(alliance_name))
        search_url = f"{self.api_url}/alliances/name/{safe_name}"
        
        async with aiohttp.ClientSession() as session:
            data1 = await self.fetch_json(session, search_url, 15)
            if not data1:
                return None
            
            target_alliance = data1[0] if isinstance(data1, list) and data1 else data1
            if not target_alliance: 
                return None
                
            alliance_id = target_alliance.get('alliance_id') or target_alliance.get('id') or target_alliance.get('allianceId')
            if not alliance_id: 
                return None

            detail_url = f"{self.api_url}/alliances/id/{alliance_id}"
            stats_url = f"{self.api_url}/statistics/alliance/{alliance_id}"
            pulse_url = f"{self.api_url}/statistics/alliance/{alliance_id}/pulse"

            members_data, stats_data, pulse_data = await asyncio.gather(
                self.fetch_json(session, detail_url, 30),
                self.fetch_json(session, stats_url, 40),
                self.fetch_json(session, pulse_url, 30)
            )

            if isinstance(members_data, list) and len(members_data) > 0: members_data = members_data[0]
            elif not members_data: members_data = {}
            
            stats_data = stats_data or {}
            pulse_data = pulse_data or {}
                
            return self._build_parsed_data(target_alliance, members_data, stats_data, pulse_data)

    def _build_parsed_data(self, basic_info, members_data, stats_data, pulse_data):
        # 🎯 LA CORRECTION MAGIQUE EST ICI : On cherche la clé 'players' dictée par l'API !
        members = members_data.get('players', members_data.get('members', members_data.get('playerList', [])))
        
        parsed_members = []
        tot_might = 0
        tot_honor = 0
        tot_fame = 0
        leader_name = "Inconnu"

        for m in members:
            rank = m.get('allianceRank', m.get('alliance_rank', m.get('rank', 9)))
            might = int(m.get('might_current', m.get('might', m.get('main_points', 0))))
            honor = int(m.get('honor', 0))
            fame = int(m.get('current_fame', m.get('fame', 0)))
            
            tot_might += might
            tot_honor += honor
            tot_fame += fame
            
            if str(rank) in ["0", "1"]:
                if leader_name == "Inconnu" or str(rank) == "0":
                    leader_name = m.get('player_name', m.get('playerName', m.get('name', 'Inconnu')))

            parsed_members.append({
                'name': m.get('player_name', m.get('playerName', m.get('name', 'Inconnu'))),
                'might': might,
                'honor': honor,
                'fame': fame,
                'level': m.get('level', 0),
                'leg_level': m.get('legendary_level', m.get('legendaryLevel', 0)),
                'rank': rank
            })

        parsed_members.sort(key=lambda x: (int(x['rank']), -x['might']))

        parsed_data = {
            'alliance_id': basic_info.get('alliance_id') or basic_info.get('allianceId'),
            'name': basic_info.get('alliance_name') or basic_info.get('name', 'Inconnue'),
            'members_count': len(parsed_members),
            'leader': leader_name,
            'total_might': tot_might,
            'total_honor': tot_honor,
            'total_fame': tot_fame,
            'members': parsed_members,
            'stats_diffs': stats_data.get('diffs', {}),
            'stats_history': {
                'loot': stats_data.get('points', {}).get('player_loot_history', []),
                'might': stats_data.get('points', {}).get('player_might_history', [])
            },
            'pulse': pulse_data
        }

        return {
            'collected_at': datetime.now().isoformat(),
            'alliance_name': parsed_data['name'],
            'server': self.server,
            'parsed_data': parsed_data
        }
    
    def save_alliance_details(self, alliance_name, full_data):
        if not full_data: return None
        timestamp = datetime.now().strftime("%H-%M-%S")
        clean_name = str(alliance_name).replace(' ', '_').replace('/', '-').replace('\\', '-')
        filename = f"{clean_name}_{timestamp}.json"
        filepath = self.get_daily_dir() / filename
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(full_data, f, indent=2, ensure_ascii=False)
        return filepath

async def get_alliance_cli(alliance_name):
    collector = AllianceDetailsCollector()
    async with aiohttp.ClientSession() as session:
        data = await collector.get_alliance_full_data(alliance_name)
        if data:
            filepath = collector.save_alliance_details(alliance_name, data)
            print(f"JSON_FILE:{filepath}")
            return 0
    return 1

if __name__ == "__main__":
    if len(sys.argv) > 1:
        sys.exit(asyncio.run(get_alliance_cli(' '.join(sys.argv[1:]))))
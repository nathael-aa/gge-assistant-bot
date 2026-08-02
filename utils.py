# -*- coding: utf-8 -*-
import os
import json
import time
import asyncio
import logging
from pathlib import Path
from datetime import datetime, timedelta
from discord import app_commands
import discord
import urllib.parse

logger = logging.getLogger("GGE_Bot")

# ==========================================
# ⚙️ GESTION DES CHEMINS & DOSSIERS
# ==========================================
BASE_DIR = Path(__file__).parent
LOCALES_DIR = BASE_DIR / 'locales'
BASE_DATA_PATH = BASE_DIR / 'data'

CONFIG_DIR = BASE_DATA_PATH / 'configs'
JOUEURS_DIR = BASE_DATA_PATH / 'joueurs'
ALLIANCES_DIR = BASE_DATA_PATH / 'alliances'

for directory in [CONFIG_DIR, JOUEURS_DIR, ALLIANCES_DIR, LOCALES_DIR]:
    directory.mkdir(parents=True, exist_ok=True)

# ==========================================
# 🧠 CACHE RAM POUR ÉVITER LE RÉVEIL DU NAS
# ==========================================
USERS_CONFIG_CACHE = None
GUILDS_CONFIG_CACHE = None
BLOCKS_CACHE = None

def clear_config_cache():
    """Fonction à appeler dans ton /setup pour forcer la mise à jour de la RAM"""
    global USERS_CONFIG_CACHE, GUILDS_CONFIG_CACHE
    USERS_CONFIG_CACHE = None
    GUILDS_CONFIG_CACHE = None

# ==========================================
# 🌍 GESTION DYNAMIQUE DES SERVEURS ET LANGUES
# ==========================================
async def get_server_config(interaction: discord.Interaction):
    global USERS_CONFIG_CACHE, GUILDS_CONFIG_CACHE
    
    default_lang = "fr"
    default_server = "E4K_FR1"
    
    # 1. On charge TOUS les utilisateurs en RAM une seule fois
    if USERS_CONFIG_CACHE is None:
        path_users = CONFIG_DIR / 'users.json'
        if path_users.exists():
            try:
                with open(path_users, 'r', encoding='utf-8') as f:
                    USERS_CONFIG_CACHE = json.load(f)
            except:
                USERS_CONFIG_CACHE = {}
        else:
            USERS_CONFIG_CACHE = {}
            
    # Vérification ultra-rapide dans la RAM
    user_id = str(interaction.user.id)
    if user_id in USERS_CONFIG_CACHE:
        u_lang = USERS_CONFIG_CACHE[user_id].get("langue", default_lang)
        u_srv = USERS_CONFIG_CACHE[user_id].get("gge_server", default_server)
        return u_lang, u_srv

    # 2. On charge TOUS les serveurs en RAM une seule fois
    if interaction.guild:
        if GUILDS_CONFIG_CACHE is None:
            path_guilds = CONFIG_DIR / 'serveurs.json'
            if path_guilds.exists():
                try:
                    with open(path_guilds, 'r', encoding='utf-8') as f:
                        GUILDS_CONFIG_CACHE = json.load(f)
                except:
                    GUILDS_CONFIG_CACHE = {}
            else:
                GUILDS_CONFIG_CACHE = {}
                
        guild_id = str(interaction.guild.id)
        if guild_id in GUILDS_CONFIG_CACHE:
            g_lang = GUILDS_CONFIG_CACHE[guild_id].get("langue", default_lang)
            g_srv = GUILDS_CONFIG_CACHE[guild_id].get("gge_server", default_server)
            return g_lang, g_srv

    return default_lang, default_server

async def get_api_headers(interaction: discord.Interaction = None, custom_server: str = None):
    server = "E4K_FR1"
    if custom_server:
        server = custom_server
    elif interaction:
        _, server = await get_server_config(interaction)
        
    return {
        'accept': 'application/json',
        'gge-server': server,
        'User-Agent': 'Mozilla/5.0 GGE-Assistant/2.0'
    }

# ==========================================
# 🌍 MOTEUR DE TRADUCTION (i18n)
# ==========================================
_translations = {}

def charger_langues():
    _translations.clear()
    if not LOCALES_DIR.exists():
        logger.warning(f"Le dossier des langues n'existe pas : {LOCALES_DIR}")
        return

    for fichier in LOCALES_DIR.glob("*.json"):
        langue = fichier.stem
        try:
            with open(fichier, 'r', encoding='utf-8') as f:
                _translations[langue] = json.load(f)
            logger.info(f"📚 Langue chargée : {langue.upper()} ({len(_translations[langue])} clés)")
        except Exception as e:
            logger.error(f"❌ Erreur lors du chargement de {fichier.name} : {e}")

def t(langue: str, cle: str, defaut: str = None, **kwargs) -> str:
    dico = _translations.get(langue, _translations.get('fr', {}))
    texte = dico.get(cle, defaut if defaut else f"[{cle}_MANQUANT]")
    
    if kwargs:
        try:
            return texte.format(**kwargs)
        except KeyError as e:
            logger.warning(f"Variable {e} manquante pour la traduction de la clé '{cle}'")
            return texte
    return texte

# ==========================================
# ⚙️ VARIABLES GLOBALES & CACHE
# ==========================================
MON_ID_DISCORD = 1166375576685265040
TOKEN = os.getenv('DISCORD_TOKEN')
TOPGG_TOKEN = os.getenv("TOPGG_TOKEN")

CACHE = {}

TRACKER_EVENTS = {
    "Nomades": ["player_event_nomad_history"],
    "Nomad Invasion": ["player_event_nomad_history"],
    "Samouraïs": ["player_event_samurai_history"],
    "Samurai Invasion": ["player_event_samurai_history"],
    "Corbeaux de Sang": ["player_event_bloodcrow_history"],
    "Bloodcrow Invasion": ["player_event_bloodcrow_history"],
    "Guerre des Royaumes": ["player_event_war_realms_history"],
    "War of the Realms": ["player_event_war_realms_history"],
    "Îles Orageuses": ["aquamarine"],
    "Storm Islands": ["aquamarine"],
    "Bataille de Bérimond": ["player_event_berimond_invasion_history", "player_event_berimond_kingdom_history"],
    "Battle of Berimond": ["player_event_berimond_invasion_history", "player_event_berimond_kingdom_history"]
}

# ==========================================
# 🔐 MOTEUR DE VERROUILLAGE ASYNCHRONE
# ==========================================
FILE_LOCKS = {}

def get_file_lock(filepath):
    path_key = str(Path(filepath).resolve())
    if path_key not in FILE_LOCKS:
        FILE_LOCKS[path_key] = asyncio.Lock()
    return FILE_LOCKS[path_key]

# ==========================================
# 🔧 Footer global
# ==========================================
BOT_VERSION = "GGE Assistant • Version 1.1.4"

async def setup_embed_footer(embed: discord.Embed, interaction: discord.Interaction = None, langue: str = "fr", custom_server: str = None):
    txt = BOT_VERSION
    if custom_server:
        txt += f" • {custom_server}"
    elif interaction:
        _, server = await get_server_config(interaction)
        txt += f" • {server}"
    embed.set_footer(text=txt)

# ==========================================
# 🔧 MAINTENANCE & CACHES JSON
# ==========================================
def load_maintenance():
    path = CONFIG_DIR / 'maintenance.json'
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f).get("maintenance_mode", False)
        except Exception as e:
            logger.error(f"❌ Impossible de lire maintenance.json : {e}")
    return False

async def load_blocks_async():
    global BLOCKS_CACHE
    if BLOCKS_CACHE is not None:
        return BLOCKS_CACHE
        
    path = CONFIG_DIR / 'blocks.json'
    async with get_file_lock(path):
        if os.path.exists(path):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    BLOCKS_CACHE = json.load(f)
                    return BLOCKS_CACHE
            except Exception as e: 
                logger.error(f"❌ Fichier blocks.json corrompu ou illisible : {e}")
            
    BLOCKS_CACHE = {"global_commands": {}, "blocked_users": {}}
    return BLOCKS_CACHE

async def save_blocks_async(data):
    global BLOCKS_CACHE
    BLOCKS_CACHE = data 
    path = CONFIG_DIR / 'blocks.json'
    async with get_file_lock(path):
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4, ensure_ascii=False)

async def load_configuration_async():
    path = CONFIG_DIR / 'configuration.json'
    async with get_file_lock(path):
        if os.path.exists(path):
            try:
                with open(path, 'r', encoding='utf-8') as f: return json.load(f)
            except Exception as e: 
                logger.error(f"❌ Erreur configuration.json : {e}")
        return {"servers": {}, "scan_minutes": {}}

async def save_configuration_async(data):
    path = CONFIG_DIR / 'configuration.json'
    async with get_file_lock(path):
        with open(path, 'w', encoding='utf-8') as f: json.dump(data, f, indent=4, ensure_ascii=False)

async def load_objectifs_async():
    path = CONFIG_DIR / 'event_objectifs.json'
    async with get_file_lock(path):
        if os.path.exists(path):
            try:
                with open(path, 'r', encoding='utf-8') as f: return json.load(f)
            except Exception as e: 
                logger.error(f"❌ Erreur event_objectifs.json : {e}")
    return {}

async def save_objectifs_async(data):
    path = CONFIG_DIR / 'event_objectifs.json'
    async with get_file_lock(path):
        with open(path, 'w', encoding='utf-8') as f: json.dump(data, f, indent=4)

async def load_pseudos_async():
    path = JOUEURS_DIR / 'discord_pseudos.json'
    async with get_file_lock(path):
        if os.path.exists(path):
            try:
                with open(path, 'r', encoding='utf-8') as f: return json.load(f)
            except Exception as e:
                logger.error(f"❌ Erreur discord_pseudos.json : {e}")
    return {}

async def save_pseudos_async(data):
    path = JOUEURS_DIR / 'discord_pseudos.json'
    async with get_file_lock(path):
        with open(path, 'w', encoding='utf-8') as f: json.dump(data, f, indent=4)

async def load_rivals_async():
    path = CONFIG_DIR / 'rival_radar.json'
    async with get_file_lock(path):
        if os.path.exists(path):
            try:
                with open(path, 'r', encoding='utf-8') as f: return json.load(f)
            except Exception as e:
                logger.error(f"❌ Erreur rival_radar.json : {e}")
    return {}

async def save_rivals_async(data):
    path = CONFIG_DIR / 'rival_radar.json'
    async with get_file_lock(path):
        with open(path, 'w', encoding='utf-8') as f: json.dump(data, f, indent=4)

async def load_dungeons_async():
    path = JOUEURS_DIR / 'dungeons_sessions.json'
    async with get_file_lock(path):
        if os.path.exists(path):
            try:
                with open(path, 'r', encoding='utf-8') as f: return json.load(f)
            except Exception as e:
                logger.error(f"❌ Erreur dungeons_sessions.json : {e}")
    return {"sessions": {}}

async def save_dungeons_async(data):
    path = JOUEURS_DIR / 'dungeons_sessions.json'
    async with get_file_lock(path):
        with open(path, 'w', encoding='utf-8') as f: json.dump(data, f, indent=4)

async def load_maintenance_async():
    path = CONFIG_DIR / 'maintenance.json'
    async with get_file_lock(path):
        if os.path.exists(path):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    return json.load(f).get("maintenance_mode", False)
            except Exception as e:
                logger.error(f"❌ Erreur maintenance.json : {e}")
    return False

async def save_maintenance_async(etat):
    path = CONFIG_DIR / 'maintenance.json'
    async with get_file_lock(path):
        with open(path, 'w', encoding='utf-8') as f:
            json.dump({"maintenance_mode": etat}, f)

async def load_diplo_async():
    path = ALLIANCES_DIR / 'diplomatie.json'
    async with get_file_lock(path):
        if os.path.exists(path):
            try:
                with open(path, 'r', encoding='utf-8') as f: return json.load(f)
            except Exception as e:
                logger.error(f"❌ Erreur diplomatie.json : {e}")
    return {}

async def save_diplo_async(data):
    path = ALLIANCES_DIR / 'diplomatie.json'
    async with get_file_lock(path):
        with open(path, 'w', encoding='utf-8') as f: json.dump(data, f, indent=4, ensure_ascii=False)

async def load_surveillance_async():
    path = CONFIG_DIR / 'surveillance.json'
    async with get_file_lock(path):
        if os.path.exists(path):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    if "alliances" not in data: data["alliances"] = {}
                    return data
            except Exception as e:
                logger.error(f"❌ Erreur surveillance.json : {e}")
    return {"players": {}, "alliances": {}}

async def save_surveillance_async(data):
    path = CONFIG_DIR / 'surveillance.json'
    async with get_file_lock(path):
        with open(path, 'w', encoding='utf-8') as f: json.dump(data, f, indent=4)

# ==========================================
# 🛠️ OUTILS UNIVERSELS & UI
# ==========================================
class PaginationView(discord.ui.View):
    def __init__(self, embeds):
        super().__init__(timeout=7200)
        self.embeds = embeds
        self.current_page = 0
    @discord.ui.button(label="⏮️", style=discord.ButtonStyle.secondary)
    async def prev(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page = max(0, self.current_page - 1)
        await interaction.response.edit_message(embed=self.embeds[self.current_page])
    @discord.ui.button(label="⏭️", style=discord.ButtonStyle.secondary)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page = min(len(self.embeds) - 1, self.current_page + 1)
        await interaction.response.edit_message(embed=self.embeds[self.current_page])

def format_num(n):
    try:
        n = int(n)
        signe = "-" if n < 0 else ""
        n = abs(n) 
        
        if n >= 1_000_000_000: return f"{signe}{n/1_000_000_000:.1f}B"
        if n >= 1_000_000: return f"{signe}{n/1_000_000:.1f}M"
        if n >= 1_000: return f"{signe}{n/1_000:.1f}k"
        return f"{signe}{n}"
    except: 
        return "0"

def get_discord_timestamp(iso_str, style="R", langue="fr"):
    try:
        dt = datetime.fromisoformat(str(iso_str).replace('Z', '+00:00'))
        return f"<t:{int(dt.timestamp())}:{style}>"
    except: 
        return t(langue, "utils_unknown_date", defaut="Date inconnue")

# ==========================================
# 🧠 LECTURE DU CACHE ET AUTOCOMPLÉTION
# ==========================================
async def get_cached_data(serveur="E4K_FR1"):
    global CACHE
    
    if serveur not in CACHE:
        CACHE[serveur] = {
            'players': [], 'players_data': {},
            'alliances': [], 'alliance_members': {},
            'last_refresh': 0
        }

    def _lecture_lourde():
        try:
            dossier_serveur = BASE_DATA_PATH / 'server_scans' / serveur
            if not dossier_serveur.exists(): return {}
            
            player_files = list(dossier_serveur.rglob('server_*.json'))
            if not player_files: return {}
            
            latest = max(player_files, key=lambda p: p.stat().st_mtime)
            with open(latest, 'r', encoding='utf-8') as f: 
                return json.load(f).get('players', {})
        except Exception as e: 
            logger.error(f"❌ [Cache] Erreur lors de la lecture lourde de {serveur} : {e}")
            return {}

    if time.time() - CACHE[serveur]['last_refresh'] > 300:
        players_data = await asyncio.to_thread(_lecture_lourde)
        CACHE[serveur]['players_data'] = players_data
        CACHE[serveur]['players'] = list(players_data.keys())
        
        alliances_set = set()
        alliance_members = {}
        for p_name, p_info in players_data.items():
            a_name = p_info.get('alliance') or p_info.get('alliance_name')
            if isinstance(a_name, dict): a_name = a_name.get('name')
            if a_name and a_name not in ["Sans alliance", ""]:
                alliances_set.add(a_name)
                if a_name not in alliance_members: alliance_members[a_name] = []
                alliance_members[a_name].append(p_name)
                
        CACHE[serveur]['alliances'] = list(alliances_set)
        CACHE[serveur]['alliance_members'] = alliance_members 
        CACHE[serveur]['last_refresh'] = time.time()
        
    return CACHE[serveur]

async def joueur_autocomplete(interaction: discord.Interaction, current: str):
    _, serveur = await get_server_config(interaction)
    data = await get_cached_data(serveur)
    return [app_commands.Choice(name=n, value=n) for n in data.get('players', []) if current.lower() in n.lower()][:25]

async def alliance_autocomplete(interaction: discord.Interaction, current: str):
    _, serveur = await get_server_config(interaction)
    data = await get_cached_data(serveur)
    return [app_commands.Choice(name=n, value=n) for n in data.get('alliances', []) if current.lower() in n.lower()][:25]

async def event_autocomplete(interaction: discord.Interaction, current: str):
    events_en = ["Nomad Invasion", "Samurai Invasion", "Bloodcrow Invasion", "War of the Realms", "Storm Islands", "Battle of Berimond"]
    return [app_commands.Choice(name=e, value=e) for e in events_en if current.lower() in e.lower()][:25]

async def event_alliance_autocomplete(interaction: discord.Interaction, current: str):
    events_en = ["Nomad Invasion", "Samurai Invasion", "Bloodcrow Invasion", "War of the Realms", "Battle of Berimond"]
    return [app_commands.Choice(name=e, value=e) for e in events_en if current.lower() in e.lower()][:25]

async def generer_rapport_alliance_embed(bot, event_name, event_keys, alliance_name, clr_alliance=0x3498DB, interaction: discord.Interaction = None, custom_server: str = None):
    headers = await get_api_headers(interaction=interaction, custom_server=custom_server)
    safe_alliance = urllib.parse.quote(alliance_name)
    
    langue = "fr"
    if interaction:
        langue, _ = await get_server_config(interaction)
    
    search_url = f"https://api.gge-tracker.com/api/v1/alliances/name/{safe_alliance}"
    
    try:
        async with bot.session.get(search_url, headers=headers, timeout=10) as resp:
            if resp.status != 200: return None, t(langue, "utils_err_alliance_id", defaut="ID Alliance introuvable."), None, None
            data1 = await resp.json()
            target = data1[0] if isinstance(data1, list) and data1 else data1
            alliance_id = target.get('alliance_id') or target.get('id')
    except Exception: 
        return None, t(langue, "utils_err_api_connection", defaut="Erreur de connexion avec GGE-Tracker."), None, None

    if not alliance_id: 
        return None, t(langue, "utils_err_alliance_not_found", defaut="Alliance introuvable."), None, None
        
    stats_url = f"https://api.gge-tracker.com/api/v1/statistics/alliance/{alliance_id}"
    try:
        async with bot.session.get(stats_url, headers=headers, timeout=15) as resp:
            if resp.status != 200: return None, t(langue, "utils_err_stats_download", defaut="Échec du téléchargement des stats."), None, None
            stats_data = await resp.json()
    except Exception: 
        return None, t(langue, "utils_err_history_download", defaut="Erreur lors du téléchargement de l'historique."), None, None
            
    best_history = []
    global_latest_str = ""
    
    for key in event_keys:
        curr_history = stats_data.get("points", {}).get(key, [])
        if curr_history:
            dates = [entry.get("date", "") for entry in curr_history if entry.get("date")]
            if dates:
                curr_max = max(dates)
                if curr_max > global_latest_str:
                    global_latest_str = curr_max
                    best_history = curr_history
                    
    if not best_history:
        msg = t(langue, "utils_err_no_points", alliance=alliance_name, nom_event=event_name, defaut=f"Aucun point enregistré pour **{alliance_name}** sur **{event_name}**.")
        return None, msg, None, None
        
    player_dict = {}
    alliance_members = set()
    
    cache = await get_cached_data(custom_server)
    local_data = cache.get('players_data', {})

    api_pids = set(str(entry.get("player_id")) for entry in best_history)

    for p_name, p_info in local_data.items():
        local_pid = str(p_info.get('player_id', p_info.get('id', '')))
        
        matched_pid = local_pid
        for api_pid in api_pids:
            if api_pid == local_pid or api_pid.startswith(local_pid) or local_pid.startswith(api_pid):
                matched_pid = api_pid
                break
                
        player_dict[matched_pid] = p_name
        
        p_all_id = str(p_info.get('allianceId', p_info.get('alliance_id', '')))
        is_in_alliance = False
        
        if p_all_id and (str(alliance_id).startswith(p_all_id) or p_all_id.startswith(str(alliance_id))):
            is_in_alliance = True
        elif str(p_info.get('allianceName', '')).lower() == alliance_name.lower():
            is_in_alliance = True
            
        if is_in_alliance:
            alliance_members.add(matched_pid)

    cutoff_str = ""
    if best_history:
        dates_uniques = set(entry.get("date", "") for entry in best_history if entry.get("date"))
        if dates_uniques:
            dts_tries = sorted([datetime.fromisoformat(d.replace('Z', '+00:00')) for d in dates_uniques])
            debut_cluster_actuel = dts_tries[-1]
            for i in range(len(dts_tries)-2, -1, -1):
                if ((dts_tries[i+1] - dts_tries[i]).total_seconds() / 86400.0) > 2.0:
                    debut_cluster_actuel = dts_tries[i+1]
                    break
                debut_cluster_actuel = dts_tries[i]
            cutoff_str = (debut_cluster_actuel - timedelta(hours=1)).isoformat().replace('+00:00', 'Z')

    latest_points = {}
    for entry in best_history:
        pid = str(entry.get("player_id"))
        pt = int(entry.get("point", 0))
        d_str = entry.get("date", "")
        if cutoff_str and d_str < cutoff_str: continue
        if pid not in latest_points or d_str > latest_points[pid]['date']:
            latest_points[pid] = {'date': d_str, 'point': pt}
            
    active_players = []
    zero_players = []
    all_pids_to_check = set(alliance_members).union(set(latest_points.keys()))
    
    for pid in all_pids_to_check:
        pt = latest_points.get(pid, {}).get('point', 0)
        default_unknown = f"ID Inconnu ({pid[:4]}...)"
        p_name = player_dict.get(pid, t(langue, "utils_unknown_id", pid_short=pid[:4], defaut=default_unknown)) 
        
        if pt > 0: active_players.append((p_name, pt))
        elif pid in alliance_members: zero_players.append(p_name)
            
    active_players.sort(key=lambda x: x[1], reverse=True)

    if not active_players and not zero_players:
        return None, t(langue, "utils_err_event_not_started", defaut="Événement non démarré ou vide."), None, None

    total_score = sum(x[1] for x in active_players)
    total_current_members = len(alliance_members)
    active_current_count = sum(1 for pid in alliance_members if latest_points.get(pid, {}).get('point', 0) > 0)
    taux_participation = (active_current_count / total_current_members) * 100 if total_current_members > 0 else 0.0

    lignes_classement = []
    for j, (name, score) in enumerate(active_players):
        medal = "🥇" if j == 0 else "🥈" if j == 1 else "🥉" if j == 2 else f"**{j+1}.**"
        lignes_classement.append(f"{medal} **{name}** ➔ **{format_num(score)} pts**")
    for name in zero_players: lignes_classement.append(f"<:movements:1512526112830521637> **{name}** ➔ **0 pts**")

    stats_text = t(langue, "utils_embed_stats_text",
                   total_score=format_num(total_score), 
                   taux_participation=f"{taux_participation:.1f}", 
                   active=active_current_count, 
                   total=total_current_members,
                   defaut=f"**Points Totaux** : **{format_num(total_score)}**\n**Participation** : {taux_participation:.1f}% ({active_current_count}/{total_current_members} membres)")

    embed = discord.Embed(title=f"<:alliance:1512503083861540914> {alliance_name} - {event_name}", color=clr_alliance, timestamp=discord.utils.utcnow())
    embed.add_field(name=t(langue, "utils_embed_stats_title", defaut="<:stats:1512517930490003726> Statistiques"), value=stats_text, inline=False)
    
    chunk_txt = ""
    part_num = 1
    for ligne in lignes_classement:
        if len(chunk_txt) + len(ligne) + 1 > 1024:
            part_title = t(langue, "utils_embed_ranking_part", part_num=part_num, defaut=f"<:ranking:1512438311132729525> Classement (Partie {part_num})")
            embed.add_field(name=part_title, value=chunk_txt, inline=False)
            chunk_txt = ligne + "\n"
            part_num += 1
        else:
            chunk_txt += ligne + "\n"
            
    if chunk_txt:
        part_title = t(langue, "utils_embed_ranking_part", part_num=part_num, defaut=f"<:ranking:1512438311132729525> Classement (Partie {part_num})")
        embed.add_field(name=part_title, value=chunk_txt, inline=False)
        
    if global_latest_str:
        ts_r = get_discord_timestamp(global_latest_str, 'R', langue)
        ts_t = get_discord_timestamp(global_latest_str, 't', langue)
        
        update_title = t(langue, "utils_embed_update_title", defaut="⏱️ Actualisation")
        update_text = t(langue, "utils_embed_update_text", ts_r=ts_r, ts_t=ts_t, defaut=f"Dernier relevé effectué {ts_r} (*{ts_t}*)")
        
        embed.add_field(name=update_title, value=update_text, inline=False)
        
    return embed, lignes_classement, stats_text, global_latest_str
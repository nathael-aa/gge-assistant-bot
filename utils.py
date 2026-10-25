import asyncio
import gc
import json
import logging
import os
import random
import re
import time
import urllib.parse
from datetime import datetime, timedelta
from pathlib import Path

import discord
from discord import app_commands

import observability as obs

logger = logging.getLogger("GGE_Bot")
BOT_VERSION = "GGE Assistant • Version 1.3.9"

# ========================================
# ⚙️ GESTION DES CHEMINS & DOSSIER
# ========================================
BASE_DIR = Path(__file__).parent

LOCALES_DIR = BASE_DIR / "locales"
BASE_DATA_PATH = BASE_DIR / "data"

CONFIG_DIR = BASE_DATA_PATH / "configs"
JOUEURS_DIR = BASE_DATA_PATH / "joueurs"
SERVEURS_DIR = BASE_DATA_PATH / "serveurs"
ADMINS_DIR = BASE_DATA_PATH / "admins"

for directory in [CONFIG_DIR, JOUEURS_DIR, SERVEURS_DIR, ADMINS_DIR, LOCALES_DIR]:
    directory.mkdir(parents=True, exist_ok=True)

# ========================================
# ⚙️ RECUPERATION .ENV
# ========================================

MON_ID_DISCORD = int(os.getenv("MON_ID_DISCORD", 0))
TOKEN = os.getenv("DISCORD_TOKEN")
TOPGG_TOKEN = os.getenv("TOPGG_TOKEN")


# ========================================
# 📦 CACHE MANAGER (BPA-06)
# ========================================


class CacheManager:
    """Gestionnaire centralisé pour remplacer les variables globales mutables."""

    def __init__(self):
        self.server_data = {}
        self.users_config = None
        self.guilds_config = None
        self.blocks = None
        self.file_locks = {}
        self.translations = {}

    def clear_configs(self):
        self.users_config = None
        self.guilds_config = None


cache_mngr = CacheManager()


def clear_config_cache():
    cache_mngr.clear_configs()


# ========================================
# CONFIGURATION UTILISATEUR
# ========================================


async def get_server_config(interaction: discord.Interaction):
    default_lang = "fr"
    default_server = "E4K_FR1"

    if cache_mngr.users_config is None:
        path_users = CONFIG_DIR / "users.json"
        if path_users.exists():
            try:

                def _read_users():
                    with open(path_users, encoding="utf-8") as f:
                        return json.load(f)

                cache_mngr.users_config = await asyncio.to_thread(_read_users)
            except Exception as e:
                obs.record_error(source="utils", scope="load_users_config", exception=e, cog="utils")
                cache_mngr.users_config = {}
        else:
            cache_mngr.users_config = {}

    user_id = str(interaction.user.id)
    if user_id in cache_mngr.users_config:
        u_lang = cache_mngr.users_config[user_id].get("langue", default_lang)
        u_srv = cache_mngr.users_config[user_id].get("gge_server", default_server)
        return u_lang, u_srv

    if interaction.guild:
        if cache_mngr.guilds_config is None:
            path_guilds = CONFIG_DIR / "serveurs.json"
            if path_guilds.exists():
                try:

                    def _read_guilds():
                        with open(path_guilds, encoding="utf-8") as f:
                            return json.load(f)

                    cache_mngr.guilds_config = await asyncio.to_thread(_read_guilds)
                except Exception as e:
                    obs.record_error(source="utils", scope="load_guilds_config", exception=e, cog="utils")
                    cache_mngr.guilds_config = {}
            else:
                cache_mngr.guilds_config = {}

        guild_id = str(interaction.guild.id)
        if guild_id in cache_mngr.guilds_config:
            g_lang = cache_mngr.guilds_config[guild_id].get("langue", default_lang)
            g_srv = cache_mngr.guilds_config[guild_id].get("gge_server", default_server)
            return g_lang, g_srv

    return default_lang, default_server


# ========================================
# HEADERS
# ========================================


async def get_api_headers(interaction: discord.Interaction = None, custom_server: str = None):
    server = "E4K_FR1"
    if custom_server:
        server = custom_server
    elif interaction:
        _, server = await get_server_config(interaction)

    headers = {
        "Accept": "application/json",
        "gge-server": server,
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) GGE-Assistant/3.0 (Async)",
    }

    api_key = os.getenv("GGETRACKER_API_KEY")
    if api_key:
        headers["X-Api-Key"] = api_key

    return headers


# ========================================
# EMOJIS ET TRADUCTIONS
# ========================================

try:
    from emojis import DICT_EMOJIS
except ImportError as e:
    logger.error(f"❌ Impossible de charger les émojis suivants : {e}")
    obs.record_error(source="utils", scope="import_emojis", exception=e, cog="utils")
    DICT_EMOJIS = {}


def charger_langues():
    cache_mngr.translations.clear()
    if not LOCALES_DIR.exists():
        logger.warning(f"⚠️ Le dossier des langues n'existe pas : {LOCALES_DIR}")
        return

    for fichier in LOCALES_DIR.glob("*.json"):
        langue = fichier.stem
        try:
            with open(fichier, encoding="utf-8") as f:
                cache_mngr.translations[langue] = json.load(f)
            logger.info(f"📚 Langue chargée : {langue.upper()} ({len(cache_mngr.translations[langue])} clés)")
        except Exception as e:
            logger.error(f"❌ Erreur lors du chargement de {fichier.name} : {e}")
            obs.record_error(source="utils", scope="load_locales", exception=e, cog="utils")


def t(langue: str, cle: str, defaut: str = None, **kwargs) -> str:
    dico_lang = cache_mngr.translations.get(langue, {})
    dico_en = cache_mngr.translations.get("en", {})
    dico_fr = cache_mngr.translations.get("fr", {})

    texte = dico_lang.get(cle)
    if texte is None:
        texte = dico_en.get(cle)
    if texte is None:
        texte = dico_fr.get(cle)
    if texte is None:
        texte = defaut if defaut is not None else f"[{cle}_MANQUANT]"

    variables_fusionnees = {**DICT_EMOJIS, **kwargs}
    if variables_fusionnees:
        texte = re.sub(r"\{(\w+)\}", lambda m: str(variables_fusionnees.get(m.group(1), m.group(0))), str(texte))

    return texte


# ========================================
# CLASSE PAGINATION
# ========================================


class PaginationView(discord.ui.View):
    def __init__(self, embeds, timeout=3600):
        super().__init__(timeout=timeout)
        self.embeds = embeds
        self.current_page = 0
        self.message = None

        self.btn_prev = discord.ui.Button(
            emoji=DICT_EMOJIS.get("e_last", "⏮️"), style=discord.ButtonStyle.secondary, custom_id="page_prev"
        )
        self.btn_prev.callback = self.cb_prev

        self.btn_next = discord.ui.Button(
            emoji=DICT_EMOJIS.get("e_next", "⏭️"), style=discord.ButtonStyle.secondary, custom_id="page_next"
        )
        self.btn_next.callback = self.cb_next

        self.add_item(self.btn_prev)
        self.add_item(self.btn_next)
        self.update_buttons()

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True

        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass
            except Exception as e:
                logger.error(f"❌ Erreur désactivation boutons PaginationView : {e}")

    def update_buttons(self):
        self.btn_prev.disabled = self.current_page == 0
        self.btn_next.disabled = self.current_page == len(self.embeds) - 1

    async def cb_prev(self, interaction: discord.Interaction):
        self.current_page = max(0, self.current_page - 1)
        self.update_buttons()
        if not self.message:
            self.message = interaction.message
        await interaction.response.edit_message(embed=self.embeds[self.current_page], view=self)

    async def cb_next(self, interaction: discord.Interaction):
        self.current_page = min(len(self.embeds) - 1, self.current_page + 1)
        self.update_buttons()
        if not self.message:
            self.message = interaction.message
        await interaction.response.edit_message(embed=self.embeds[self.current_page], view=self)


class RefreshOnlyView(discord.ui.View):
    def __init__(self, callback_func, langue="fr", timeout=3600):
        super().__init__(timeout=timeout)
        self.message = None
        self.callback_func = callback_func

        self.refresh_btn = discord.ui.Button(
            style=discord.ButtonStyle.primary,
            emoji=DICT_EMOJIS.get("e_refresh", "🔄"),
            label=t(langue, "btn_refresh", defaut="Refresh"),
        )
        self.refresh_btn.callback = self.cb_refresh
        self.add_item(self.refresh_btn)

    async def cb_refresh(self, interaction: discord.Interaction):
        if not self.message:
            self.message = interaction.message
        await self.callback_func(interaction)

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


# ========================================
# VOTES ET RAPPELS
# ========================================

VOTES_FILE = JOUEURS_DIR / "votes.json"


async def prompt_vote_if_lucky(interaction: discord.Interaction, probability_percent: int, langue: str = "fr"):
    user_id_str = str(interaction.user.id)

    if VOTES_FILE.exists():
        try:

            def _read_votes():
                with open(VOTES_FILE, encoding="utf-8") as f:
                    return json.load(f)

            votes_data = await asyncio.to_thread(_read_votes)

            if user_id_str in votes_data:
                deadline = datetime.fromisoformat(votes_data[user_id_str])
                if datetime.now() < deadline:
                    return

        except Exception as e:
            print(f"❌ [DEBUG VOTE] Erreur lors de la lecture du bouclier : {e}")
            obs.record_error(source="utils", scope="vote_shield", exception=e, cog="utils")

    if random.randint(1, 100) > probability_percent:
        return

    vote_url = "https://top.gg/bot/1472309793065533493/vote"
    review_url = "https://top.gg/bot/1472309793065533493#reviews"

    btn_vote_lbl = t(langue, "vote_prompt_btn", defaut="Voter (1 clic)")
    btn_review_lbl = t(langue, "review_prompt_btn", defaut="Laisser un avis")
    titre_txt = t(langue, "vote_prompt_title", defaut="⭐ Coucou ! Un petit service ?")

    desc_txt = t(
        langue,
        "vote_prompt_desc",
        defaut="Tu utilises souvent cette commande ! Si GGE Assistant t'aide au quotidien, prends quelques secondes pour voter ou nous laisser un petit avis sur Top.gg. C'est gratuit et ça nous aide énormément ! ❤️\n\n*(Ce message disparaîtra pendant 7 jours après ton vote !)*",
    )

    view = discord.ui.View()

    btn_vote = discord.ui.Button(label=btn_vote_lbl, url=vote_url, emoji=DICT_EMOJIS.get("e_sparkles", "✨"))
    view.add_item(btn_vote)

    btn_review = discord.ui.Button(label=btn_review_lbl, url=review_url, emoji=DICT_EMOJIS.get("e_main", "⭐"))
    view.add_item(btn_review)

    embed = discord.Embed(title=titre_txt, description=desc_txt, color=discord.Color.random())

    try:
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)
    except Exception:
        pass


# ========================================
# RESULTAT API
# ========================================


def _get_api_timestamp(*sources):
    dates_trouvees = []

    def search_ts(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k in [
                    "updated_at",
                    "updatedAt",
                    "last_update",
                    "date",
                    "collected_at",
                    "last_collected_at",
                    "attacked_at",
                ] and isinstance(v, str):
                    if len(v) >= 10 and v[4] == "-":
                        dates_trouvees.append(v)

            for v in obj.values():
                if isinstance(v, (dict, list)):
                    search_ts(v)

        elif isinstance(obj, list):
            for item in obj:
                if isinstance(item, (dict, list)):
                    search_ts(item)

    for src in sources:
        if src:
            search_ts(src)

    if dates_trouvees:
        try:
            latest_str = max(dates_trouvees)
            return datetime.fromisoformat(latest_str.replace("Z", "+00:00"))
        except:
            pass

    return discord.utils.utcnow()


# ========================================
# FORMAT DES NOMBRES
# ========================================


def format_num(n):
    try:
        n = int(n)
        signe = "-" if n < 0 else ""
        n = abs(n)

        if n >= 1_000_000_000:
            return f"{signe}{n / 1_000_000_000:.1f}B"
        if n >= 1_000_000:
            return f"{signe}{n / 1_000_000:.1f}M"
        if n >= 1_000:
            return f"{signe}{n / 1_000:.1f}k"
        return f"{signe}{n}"
    except:
        return "0"


# ========================================
# TIMESTAMP DISCORD
# ========================================


def get_discord_timestamp(iso_str, style="R", langue="fr"):
    try:
        dt = datetime.fromisoformat(str(iso_str).replace("Z", "+00:00"))
        return f"<t:{int(dt.timestamp())}:{style}>"
    except:
        return t(langue, "utils_unknown_date", defaut="Date inconnue")


# ========================================
# RECUPERATION DONNEES EN CACHE
# ========================================


async def get_cached_data(serveur="E4K_FR1"):
    if len(cache_mngr.server_data) >= 1 and serveur not in cache_mngr.server_data:
        logger.info(f"🧹 [Anti-OOM] Vidage du cache mémoire complet pour charger {serveur}.")
        cache_mngr.server_data.clear()
        gc.collect()

    if serveur not in cache_mngr.server_data:
        cache_mngr.server_data[serveur] = {
            "players": [],
            "players_data": {},
            "alliances": [],
            "alliance_members": {},
            "last_refresh": 0,
        }

    def _lecture_lourde():
        dossier_serveur = BASE_DATA_PATH / "server_scans" / serveur
        if not dossier_serveur.exists():
            return {}

        player_files = list(dossier_serveur.rglob("server_*.json"))
        if not player_files:
            return {}

        latest = max(player_files, key=lambda p: p.stat().st_mtime)

        try:
            with open(latest, encoding="utf-8") as f:
                return json.load(f).get("players", {})
        except json.JSONDecodeError as e:
            logger.error(f"❌ [Cache] Fichier corrompu détecté pour {serveur} : {e}. Suppression du fichier...")
            try:
                latest.unlink()
            except OSError as err:
                logger.error(f"Impossible de supprimer le fichier corrompu : {err}")
            return {}
        except Exception as e:
            logger.error(f"❌ [Cache] Erreur lors de la lecture lourde de {serveur} : {e}")
            obs.record_error(source="utils", scope="cache_load", exception=e, cog="utils")
            return {}

    if time.time() - cache_mngr.server_data[serveur]["last_refresh"] > 300:
        players_data = await asyncio.to_thread(_lecture_lourde)
        cache_mngr.server_data[serveur]["players_data"] = players_data
        cache_mngr.server_data[serveur]["players"] = list(players_data.keys())

        alliances_set = set()
        alliance_members = {}
        for p_name, p_info in players_data.items():
            a_name = p_info.get("alliance") or p_info.get("alliance_name")
            if isinstance(a_name, dict):
                a_name = a_name.get("name")
            if a_name and a_name not in ["Sans alliance", ""]:
                alliances_set.add(a_name)
                if a_name not in alliance_members:
                    alliance_members[a_name] = []
                alliance_members[a_name].append(p_name)

        cache_mngr.server_data[serveur]["alliances"] = list(alliances_set)
        cache_mngr.server_data[serveur]["alliance_members"] = alliance_members
        cache_mngr.server_data[serveur]["last_refresh"] = time.time()

    return cache_mngr.server_data[serveur]


async def find_player_in_cache(serveur: str, player_name: str):
    cache = await get_cached_data(serveur)
    players_data = cache.get("players_data", {})

    for p_name, p_info in players_data.items():
        if p_name.lower() == player_name.lower():
            return p_name, p_info

    return player_name, None


# ========================================
# LECTURE DES INDEX ULTRA-LÉGERS
# ========================================


async def get_autocomplete_list(serveur: str, type_liste: str):
    def _lecture_index():
        try:
            dossier_serveur = BASE_DATA_PATH / "server_scans" / serveur
            if not dossier_serveur.exists():
                return []

            date_dirs = sorted([d for d in dossier_serveur.iterdir() if d.is_dir()], reverse=True)
            if not date_dirs:
                return []

            index_file = date_dirs[0] / f"{type_liste}_autocomplete.json"
            if index_file.exists():
                with open(index_file, encoding="utf-8") as f:
                    return json.load(f)
            return []
        except Exception:
            return []

    return await asyncio.to_thread(_lecture_index)


# ========================================
# FONCTIONS AUTOCOMPLETE DISCORD
# ========================================


async def joueur_autocomplete(interaction: discord.Interaction, current: str):
    _, serveur = await get_server_config(interaction)
    joueurs = await get_autocomplete_list(serveur, "players")

    return [
        app_commands.Choice(name=str(n)[:100], value=str(n)[:100])
        for n in joueurs
        if current.lower() in str(n).lower() and str(n).strip()
    ][:25]


async def alliance_autocomplete(interaction: discord.Interaction, current: str):
    _, serveur = await get_server_config(interaction)
    alliances = await get_autocomplete_list(serveur, "alliances")

    return [
        app_commands.Choice(name=str(n)[:100], value=str(n)[:100])
        for n in alliances
        if current.lower() in str(n).lower() and str(n).strip()
    ][:25]


async def event_autocomplete(interaction: discord.Interaction, current: str):
    events_en = [
        "Nomad Invasion",
        "Samurai Invasion",
        "Bloodcrow Invasion",
        "War of the Realms",
        "Battle of Berimond",
    ]
    return [app_commands.Choice(name=e, value=e) for e in events_en if current.lower() in e.lower()][:25]


async def event_alliance_autocomplete(interaction: discord.Interaction, current: str):
    events_en = ["Nomad Invasion", "Samurai Invasion", "Bloodcrow Invasion", "War of the Realms", "Battle of Berimond"]
    return [app_commands.Choice(name=e, value=e) for e in events_en if current.lower() in e.lower()][:25]


# ========================================
# FONCTION EMBED RESULTAT EVENT
# ========================================


async def generer_rapport_alliance_embed(
    bot,
    event_name,
    event_keys,
    alliance_name,
    clr_alliance=0x3498DB,
    interaction: discord.Interaction = None,
    custom_server: str = None,
):
    headers = await get_api_headers(interaction=interaction, custom_server=custom_server)
    safe_alliance = urllib.parse.quote(alliance_name)

    langue = "fr"
    serveur_cible = custom_server or "E4K_FR1"

    if interaction:
        langue, srv = await get_server_config(interaction)
        if not custom_server:
            serveur_cible = srv

    search_url = f"https://api.gge-tracker.com/api/v1/alliances/name/{safe_alliance}"

    try:
        async with bot.session.get(search_url, headers=headers, timeout=10) as resp:
            if resp.status != 200:
                return None, t(langue, "utils_err_alliance_id", defaut="ID Alliance introuvable."), None, None
            data1 = await resp.json()
            target = data1[0] if isinstance(data1, list) and data1 else data1
            alliance_id = target.get("alliance_id") or target.get("id")
    except Exception:
        return None, t(langue, "utils_err_api_connection", defaut="Erreur de connexion avec GGE-Tracker."), None, None

    if not alliance_id:
        return None, t(langue, "utils_err_alliance_not_found", defaut="Alliance introuvable."), None, None

    stats_url = f"https://api.gge-tracker.com/api/v1/statistics/alliance/{alliance_id}"
    try:
        async with bot.session.get(stats_url, headers=headers, timeout=15) as resp:
            if resp.status != 200:
                return (
                    None,
                    t(langue, "utils_err_stats_download", defaut="Échec du téléchargement des stats."),
                    None,
                    None,
                )
            stats_data = await resp.json()
    except Exception:
        return (
            None,
            t(langue, "utils_err_history_download", defaut="Erreur lors du téléchargement de l'historique."),
            None,
            None,
        )

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
        msg = t(
            langue,
            "utils_err_no_points",
            alliance=alliance_name,
            nom_event=event_name,
            defaut=f"Aucun point enregistré pour **{alliance_name}** sur **{event_name}**.",
        )
        return None, msg, None, None

    player_dict = {}
    alliance_members = set()

    cache = await get_cached_data(serveur_cible)
    local_data = cache.get("players_data", {})

    api_pids = set(str(entry.get("player_id")) for entry in best_history)

    for p_name, p_info in local_data.items():
        local_pid = str(p_info.get("player_id", p_info.get("id", "")))

        matched_pid = local_pid
        for api_pid in api_pids:
            if api_pid == local_pid or api_pid.startswith(local_pid) or local_pid.startswith(api_pid):
                matched_pid = api_pid
                break

        player_dict[matched_pid] = p_name

        p_all_id = str(p_info.get("allianceId", p_info.get("alliance_id", "")))
        is_in_alliance = False

        if p_all_id and (str(alliance_id).startswith(p_all_id) or p_all_id.startswith(str(alliance_id))):
            is_in_alliance = True
        elif str(p_info.get("allianceName", "")).lower() == alliance_name.lower():
            is_in_alliance = True

        if is_in_alliance:
            alliance_members.add(matched_pid)

    cutoff_str = ""
    if best_history:
        dates_uniques = set(entry.get("date", "") for entry in best_history if entry.get("date"))
        if dates_uniques:
            dts_tries = sorted([datetime.fromisoformat(d.replace("Z", "+00:00")) for d in dates_uniques])
            debut_cluster_actuel = dts_tries[-1]
            for i in range(len(dts_tries) - 2, -1, -1):
                if ((dts_tries[i + 1] - dts_tries[i]).total_seconds() / 86400.0) > 2.0:
                    debut_cluster_actuel = dts_tries[i + 1]
                    break
                debut_cluster_actuel = dts_tries[i]
            cutoff_str = (debut_cluster_actuel - timedelta(hours=1)).isoformat().replace("+00:00", "Z")

    latest_points = {}
    for entry in best_history:
        pid = str(entry.get("player_id"))
        pt = int(entry.get("point", 0))
        d_str = entry.get("date", "")
        if cutoff_str and d_str < cutoff_str:
            continue
        if pid not in latest_points or d_str > latest_points[pid]["date"]:
            latest_points[pid] = {"date": d_str, "point": pt}

    active_players = []
    zero_players = []
    all_pids_to_check = set(alliance_members).union(set(latest_points.keys()))

    for pid in all_pids_to_check:
        pt = latest_points.get(pid, {}).get("point", 0)
        default_unknown = f"ID Inconnu ({pid[:4]}...)"
        p_name = player_dict.get(pid, t(langue, "utils_unknown_id", pid_short=pid[:4], defaut=default_unknown))

        if pt > 0:
            active_players.append((p_name, pt))
        elif pid in alliance_members:
            zero_players.append(p_name)

    active_players.sort(key=lambda x: x[1], reverse=True)

    if not active_players and not zero_players:
        return None, t(langue, "utils_err_event_not_started", defaut="Événement non démarré ou vide."), None, None

    total_score = sum(x[1] for x in active_players)
    total_current_members = len(alliance_members)
    active_current_count = sum(1 for pid in alliance_members if latest_points.get(pid, {}).get("point", 0) > 0)
    taux_participation = (active_current_count / total_current_members) * 100 if total_current_members > 0 else 0.0

    lignes_classement = []
    for j, (name, score) in enumerate(active_players):
        medal = "🥇" if j == 0 else "🥈" if j == 1 else "🥉" if j == 2 else f"**{j + 1}.**"
        lignes_classement.append(f"{medal} **{name}** ➔ **{format_num(score)} pts**")
    for name in zero_players:
        lignes_classement.append(f"{DICT_EMOJIS.get('e_movements', '🚶‍♂️')} **{name}** ➔ **0 pts**")

    stats_text = t(
        langue,
        "utils_embed_stats_text",
        total_score=format_num(total_score),
        taux_participation=f"{taux_participation:.1f}",
        active=active_current_count,
        total=total_current_members,
        defaut=f"**Points Totaux** : **{format_num(total_score)}**\n**Participation** : {taux_participation:.1f}% ({active_current_count}/{total_current_members} membres)",
    )

    embed = discord.Embed(
        title=f"{DICT_EMOJIS.get('e_alliance_icon', '🛡️')} {alliance_name} | {event_name}",
        color=clr_alliance,
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(
        name=t(langue, "utils_embed_stats_title", defaut="{e_stats} Statistiques"),
        value=stats_text,
        inline=False,
    )

    chunk_txt = ""
    part_num = 1
    for ligne in lignes_classement:
        if len(chunk_txt) + len(ligne) + 1 > 1024:
            part_title = t(
                langue,
                "utils_embed_ranking_part",
                part_num=part_num,
                defaut=f"{{e_ranking}} Classement (Partie {part_num})",
            )
            embed.add_field(name=part_title, value=chunk_txt, inline=False)
            chunk_txt = ligne + "\n"
            part_num += 1
        else:
            chunk_txt += ligne + "\n"

    if chunk_txt:
        part_title = t(
            langue,
            "utils_embed_ranking_part",
            part_num=part_num,
            defaut=f"{{e_ranking}} Classement (Partie {part_num})",
        )
        embed.add_field(name=part_title, value=chunk_txt, inline=False)

    if global_latest_str:
        ts_r = get_discord_timestamp(global_latest_str, "R", langue)
        ts_t = get_discord_timestamp(global_latest_str, "t", langue)

        update_title = t(langue, "utils_embed_update_title", defaut="⏱️ Actualisation")
        update_text = t(
            langue, "utils_embed_update_text", ts_r=ts_r, ts_t=ts_t, defaut=f"Dernier relevé effectué {ts_r} (*{ts_t}*)"
        )

        embed.add_field(name=update_title, value=update_text, inline=False)

    return embed, lignes_classement, stats_text, global_latest_str


# ==========================================
# 🌍 CORRESPONDANCE DES SERVEURS (FLY.DEV)
# ==========================================
FLYDEV_MAPPING = {
    "int1": "EmpireEx",
    "de1": "EmpireEx_2",
    "fr1": "EmpireEx_3",
    "cz1": "EmpireEx_4",
    "pl1": "EmpireEx_5",
    "pt1": "EmpireEx_6",
    "int2": "EmpireEx_7",
    "es1": "EmpireEx_8",
    "it1": "EmpireEx_9",
    "tr1": "EmpireEx_10",
    "nl1": "EmpireEx_11",
    "hu1": "EmpireEx_12",
    "skn1": "EmpireEx_13",
    "ru1": "EmpireEx_14",
    "ro1": "EmpireEx_15",
    "bg1": "EmpireEx_16",
    "hu2": "EmpireEx_17",
    "sk1": "EmpireEx_18",
    "gb1": "EmpireEx_19",
    "br1": "EmpireEx_20",
    "us1": "EmpireEx_21",
    "au1": "EmpireEx_22",
    "kr1": "EmpireEx_23",
    "jp1": "EmpireEx_24",
    "his1": "EmpireEx_25",
    "in1": "EmpireEx_26",
    "cn1": "EmpireEx_27",
    "gr1": "EmpireEx_28",
    "lt1": "EmpireEx_29",
    "sa1": "EmpireEx_32",
    "ae1": "EmpireEx_33",
    "eg1": "EmpireEx_34",
    "arab1": "EmpireEx_35",
    "asia": "EmpireEx_36",
    "hant1": "EmpireEx_37",
    "es2": "EmpireEx_38",
    "int3": "EmpireEx_43",
    "world1": "EmpireEx_46",
    "world2": "EmpireEx_49",
    "global": None,
    "partner_sp3": None,
    "e4k_ro1": None,
    "e4k_hu1": None,
    "e4k_cz1": None,
    "e4k_sk1": None,
    "e4k_bg1": None,
    "e4k_hr1": None,
    "e4k_se1": None,
    "e4k_no1": None,
    "e4k_fi1": None,
    "e4k_dk1": None,
    "e4k_tw1": None,
    "e4k_int1": None,
    "e4k_de1": "EmpirefourkingdomsExGG",
    "e4k_fr1": "EmpirefourkingdomsExGG_2",
    "e4k_pl1": "EmpirefourkingdomsExGG_3",
    "e4k_us1": "EmpirefourkingdomsExGG_4",
    "e4k_gb1": "EmpirefourkingdomsExGG_5",
    "e4k_nl1": "EmpirefourkingdomsExGG_6",
    "e4k_es1": "EmpirefourkingdomsExGG_7",
    "e4k_pt1": "EmpirefourkingdomsExGG_8",
    "e4k_it1": "EmpirefourkingdomsExGG_9",
    "e4k_ru1": "EmpirefourkingdomsExGG_10",
    "e4k_skn1": "EmpirefourkingdomsExGG_11",
    "e4k_his1": "EmpirefourkingdomsExGG_12",
    "e4k_br1": "EmpirefourkingdomsExGG_13",
    "e4k_jp1": "EmpirefourkingdomsExGG_14",
    "e4k_kr1": "EmpirefourkingdomsExGG_15",
    "e4k_cn1": "EmpirefourkingdomsExGG_16",
    "e4k_au1": "EmpirefourkingdomsExGG_17",
    "e4k_ph1": "EmpirefourkingdomsExGG_18",
    "e4k_ar1": "EmpirefourkingdomsExGG_19",
    "e4k_mx1": "EmpirefourkingdomsExGG_20",
    "e4k_int2": "EmpirefourkingdomsExGG_21",
    "e4k_tr1": "EmpirefourkingdomsExGG_22",
    "e4k_gr1": "EmpirefourkingdomsExGG_23",
    "e4k_arab1": "EmpirefourkingdomsExGG_24",
    "e4k_in1": "EmpirefourkingdomsExGG_25",
    "e4k_id1": "EmpirefourkingdomsExGG_26",
    "e4k_asia1": "EmpirefourkingdomsExGG_27",
    "e4k_de2": "EmpirefourkingdomsExGG_28",
    "e4k_us2": "EmpirefourkingdomsExGG_29",
    "e4k_hant1": "EmpirefourkingdomsExGG_30",
    "e4k_ru2": "EmpirefourkingdomsExGG_31",
    "e4k_int3": "EmpirefourkingdomsExGG_32",
    "e4k_int4": "EmpirefourkingdomsExGG_34",
    "e4k_world1": "EmpirefourkingdomsExGG_36",
    "e4k_world2": "EmpirefourkingdomsExGG_37",
}

# ==========================================
# 📊 CONSTANTES DES ÉVÉNEMENTS & CLASSEMENTS
# ==========================================

EVENT_IDS = {
    "achievements": 1,
    "plunder": 2,
    "honor": 5,
    "might": 6,
    "legendary": 7,
    "berimond": 30,
    "nobility": 40,
    "foreigners": 44,
    "nomads": 46,
    "samurais": 51,
    "season": 53,
    "bloodcrows": 58,
    "shapeshifters": 60,
    "master": 61,
    "league": 63,
    "woa": 72,
    "realms": 76,
    "horizon": 78,
    "patronage": 79,
    "flora": 80,
    "snowglobe": 81,
    "hollowmoon": 82,
    "sandfortune": 83,
    "banquet": 85,
    "midnight": 87,
    "alliance_honor": 10,
    "alliance_might": 11,
    "alliance_command": 12,
    "alliance_cargo": 13,
    "alliance_foreigners": 45,
    "alliance_nomad": 47,
    "alliance_samurais": 52,
    "alliance_bloodcrows": 59,
    "alliance_league": 67,
    "alliance_horizon": 77,
}

BRACKETS = {
    "1": "Niveaux Classiques (< 70)",
    "2": "Légendaire 1 - 299",
    "3": "Légendaire 300 - 649",
    "4": "Légendaire 650 - 949",
    "5": "Légendaire 950+",
    "6": "Niveau 70",
}

# ==========================================
# CORRESPONDANCE NOM DES EVENTS
# ==========================================

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
    "Battle of Berimond": ["player_event_berimond_invasion_history", "player_event_berimond_kingdom_history"],
}

# ==========================================
# GESTION DES EVENEMENTS ET DESCIPRTIONS
# ==========================================

EVENT_MAPPING = {
    "samurai invasion": {
        "name_key": "cal_ev_samurai",
        "name_default": "Samouraï",
        "emoji": "{e_samurai}",
        "color": 0xBF0000,
        "tracker_name": "Samouraïs",
        "start": "11:00",
        "end": "10:00",
    },
    "nomad invasion": {
        "name_key": "cal_ev_nomad",
        "name_default": "Nomade",
        "emoji": "{e_nomads}",
        "color": 0xEDC951,
        "tracker_name": "Nomades",
        "start": "11:00",
        "end": "10:00",
    },
    "bloodcrow invasion": {
        "name_key": "cal_ev_bloodcrow",
        "name_default": "Corbeaux de Sang",
        "emoji": "{e_bloodcrow}",
        "color": 0xEDC951,
        "tracker_name": "Corbeaux de Sang",
        "start": "11:00",
        "end": "10:00",
    },
    "war of the realms": {
        "name_key": "cal_ev_realms",
        "name_default": "Guerre des Royaumes",
        "emoji": "{e_war_realms}",
        "color": 0xA69EB0,
        "tracker_name": "Guerre des Royaumes",
        "start": "11:00",
        "end": "10:00",
    },
    "berimond": {
        "name_key": "cal_ev_berimond",
        "name_default": "Bérimond",
        "emoji": "{e_berimond}",
        "color": 0x4B86B4,
        "tracker_name": "Bataille de Bérimond",
        "start": "11:00",
        "end": "10:00",
    },
    "bladecoast": {
        "name_key": "cal_ev_bladecoast",
        "name_default": "Côte Tranchante",
        "emoji": "{e_bladecoast}",
        "color": 0xBFB5B2,
        "tracker_name": None,
        "start": "11:00",
        "end": "10:00",
    },
    "rift raid": {
        "name_key": "cal_ev_rift",
        "name_default": "Raid de la Faille",
        "emoji": "{e_riftraid}",
        "color": 0xFB2E01,
        "tracker_name": None,
        "start": "11:00",
        "end": "10:00",
    },
    "grand tournament": {
        "name_key": "cal_ev_tournament",
        "name_default": "Grand Tournoi",
        "emoji": "{e_grandtournament}",
        "color": 0x03396C,
        "tracker_name": None,
        "start": "11:00",
        "end": "10:00",
    },
    "beyond the horizon": {
        "name_key": "cal_ev_horizon",
        "name_default": "Au-delà de l'horizon",
        "emoji": "{e_bth}",
        "color": 0x006666,
        "tracker_name": None,
        "start": "11:00",
        "end": "00:40",
    },
    "outer realms": {
        "name_key": "cal_ev_outer",
        "name_default": "Royaumes extérieurs",
        "emoji": "{e_outerrealmsicon}",
        "color": 0xFFE28A,
        "tracker_name": None,
        "start": "11:00",
        "end": "00:40",
    },
    "imperial patronage": {
        "name_key": "cal_ev_patronage",
        "name_default": "Patronage impérial",
        "emoji": "{e_patronage}",
        "color": 0xE8702A,
        "tracker_name": None,
        "start": "11:00",
        "end": "10:00",
    },
    "grand nobility contest": {
        "name_key": "cal_ev_nobility",
        "name_default": "Grand concours de noblesse",
        "emoji": "{e_ltpe}",
        "color": 0xE8702A,
        "tracker_name": None,
        "start": "11:00",
        "end": "10:00",
    },
}

# ==========================================
# FOOTER COMMUN TOUT EMBED
# ==========================================


async def setup_embed_footer(
    embed: discord.Embed, interaction: discord.Interaction = None, langue: str = "fr", custom_server: str = None
):
    txt = BOT_VERSION
    if custom_server:
        txt += f" • {custom_server}"
    elif interaction:
        _, server = await get_server_config(interaction)
        txt += f" • {server}"
    embed.set_footer(text=txt)


# ==========================================
# FILELOCK
# ==========================================


def get_file_lock(filepath):
    path_key = str(Path(filepath).resolve())
    if path_key not in cache_mngr.file_locks:
        cache_mngr.file_locks[path_key] = asyncio.Lock()
    return cache_mngr.file_locks[path_key]


# ==========================================
# HELPERS JSON NON-BLOQUANTS
# ==========================================


def _json_read_sync(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _text_write_sync(path, payload: str):
    with open(path, "w", encoding="utf-8") as f:
        f.write(payload)


async def async_json_load(path):
    return await asyncio.to_thread(_json_read_sync, path)


async def async_json_dump(path, data, indent=4, ensure_ascii=False):
    payload = json.dumps(data, indent=indent, ensure_ascii=ensure_ascii)
    await asyncio.to_thread(_text_write_sync, path, payload)


# ==========================================
# LOAD / SAVE
# ==========================================


async def load_blocks_async():
    if cache_mngr.blocks is not None:
        return cache_mngr.blocks

    path = ADMINS_DIR / "blocks.json"
    async with get_file_lock(path):
        if os.path.exists(path):
            try:
                cache_mngr.blocks = await async_json_load(path)
                return cache_mngr.blocks
            except Exception as e:
                logger.error(f"❌ Fichier blocks.json corrompu ou illisible : {e}")
                obs.record_error(source="utils", scope="load_blocks", exception=e, cog="utils")

    cache_mngr.blocks = {"global_commands": {}, "blocked_users": {}}
    return cache_mngr.blocks


async def save_blocks_async(data):
    cache_mngr.blocks = data
    path = ADMINS_DIR / "blocks.json"
    async with get_file_lock(path):
        await async_json_dump(path, data, indent=4, ensure_ascii=False)


async def load_configuration_async():
    path = CONFIG_DIR / "servers_cache.json"
    async with get_file_lock(path):
        if os.path.exists(path):
            try:
                return await async_json_load(path)
            except Exception as e:
                logger.error(f"❌ Erreur lecture servers_cache.json : {e}")
                obs.record_error(source="utils", scope="load_configuration", exception=e, cog="utils")
        return {"servers_info": {}}


async def save_configuration_async(data):
    path = CONFIG_DIR / "servers_cache.json"
    async with get_file_lock(path):
        await async_json_dump(path, data, indent=4, ensure_ascii=False)


async def load_pseudos_async():
    path = JOUEURS_DIR / "discord_pseudos.json"
    async with get_file_lock(path):
        if os.path.exists(path):
            try:
                return await async_json_load(path)
            except Exception as e:
                logger.error(f"❌ Erreur discord_pseudos.json : {e}")
                obs.record_error(source="utils", scope="load_pseudos", exception=e, cog="utils")
    return {}


async def save_pseudos_async(data):
    path = JOUEURS_DIR / "discord_pseudos.json"
    async with get_file_lock(path):
        await async_json_dump(path, data, indent=4, ensure_ascii=True)


async def load_rivals_async():
    path = JOUEURS_DIR / "rival_radar.json"
    async with get_file_lock(path):
        if os.path.exists(path):
            try:
                return await async_json_load(path)
            except Exception as e:
                logger.error(f"❌ Erreur rival_radar.json : {e}")
                obs.record_error(source="utils", scope="load_rivals", exception=e, cog="utils")
    return {}


async def save_rivals_async(data):
    path = JOUEURS_DIR / "rival_radar.json"
    async with get_file_lock(path):
        await async_json_dump(path, data, indent=4, ensure_ascii=True)


async def load_dungeons_async():
    path = JOUEURS_DIR / "forteresses_sessions.json"
    async with get_file_lock(path):
        if os.path.exists(path):
            try:
                return await async_json_load(path)
            except Exception as e:
                logger.error(f"❌ Erreur forteresses_sessions.json : {e}")
                obs.record_error(source="utils", scope="load_dungeons", exception=e, cog="utils")
    return {"sessions": {}}


async def save_dungeons_async(data):
    path = JOUEURS_DIR / "forteresses_sessions.json"
    async with get_file_lock(path):
        await async_json_dump(path, data, indent=4, ensure_ascii=True)


def load_maintenance():
    path = ADMINS_DIR / "maintenance.json"
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f).get("maintenance_mode", False)
        except Exception as e:
            logger.error(f"❌ Impossible de lire maintenance.json : {e}")
            obs.record_error(source="utils", scope="load_maintenance", exception=e, cog="utils")
    return False


async def load_maintenance_async():
    path = ADMINS_DIR / "maintenance.json"
    async with get_file_lock(path):
        if os.path.exists(path):
            try:
                return (await async_json_load(path)).get("maintenance_mode", False)
            except Exception as e:
                logger.error(f"❌ Erreur maintenance.json : {e}")
                obs.record_error(source="utils", scope="load_maintenance_async", exception=e, cog="utils")
    return False


async def save_maintenance_async(etat):
    path = ADMINS_DIR / "maintenance.json"
    async with get_file_lock(path):
        await async_json_dump(path, {"maintenance_mode": etat}, indent=None, ensure_ascii=True)


async def load_surveillance_async():
    path = JOUEURS_DIR / "surveillance.json"
    async with get_file_lock(path):
        if os.path.exists(path):
            try:
                data = await async_json_load(path)
                if "alliances" not in data:
                    data["alliances"] = {}
                return data
            except Exception as e:
                logger.error(f"❌ Erreur surveillance.json : {e}")
                obs.record_error(source="utils", scope="load_surveillance", exception=e, cog="utils")
    return {"players": {}, "alliances": {}}


async def save_surveillance_async(data):
    path = JOUEURS_DIR / "surveillance.json"
    async with get_file_lock(path):
        await async_json_dump(path, data, indent=4, ensure_ascii=True)

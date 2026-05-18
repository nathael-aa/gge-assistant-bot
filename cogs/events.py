# -*- coding: utf-8 -*-
import os
import json
import urllib.parse
import asyncio
import aiohttp
from urllib.parse import quote
from datetime import datetime, timedelta
import discord
from discord import app_commands
from discord.ext import commands, tasks
import logging
from logging.handlers import RotatingFileHandler
import traceback

# 🛠️ Import de la boîte à outils
from utils import (
    BASE_DATA_PATH, 
    joueur_autocomplete, 
    alliance_autocomplete, 
    event_autocomplete,
    TRACKER_EVENTS,
    format_num,
    get_discord_timestamp,
    BOT_VERSION,
    PaginationView,
    get_cached_data
)

logger = logging.getLogger("GGE_Bot")

# ==========================================
# 💾 GESTION DES OBJECTIFS D'ALLIANCE (Par Serveur)
# ==========================================
OBJECTIFS_FILE = BASE_DATA_PATH / 'event_objectifs.json'

def load_objectifs():
    if os.path.exists(OBJECTIFS_FILE):
        try:
            with open(OBJECTIFS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except: pass
    return {}

def save_objectifs(data):
    with open(OBJECTIFS_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4)

def get_tier_and_label(lvl, leg):
    """Détermine la tranche de niveau du joueur"""
    if leg < 300 and lvl <= 70: return "T1", "Niv 1-299"
    if leg < 650: return "T2", "Niv 300-649"
    if leg < 950: return "T3", "Niv 650-949"
    return "T4", "Niv 950+"

# ==========================================
# 💾 GESTION DES PSEUDOS ET DU RADAR RIVAUX
# ==========================================
PSEUDOS_FILE = BASE_DATA_PATH / 'discord_pseudos.json'
RIVAL_FILE = BASE_DATA_PATH / 'rival_radar.json'

radar_logger = logging.getLogger("Radar_Log")
radar_logger.setLevel(logging.INFO)

# ----------------------------------------------

def load_pseudos():
    if os.path.exists(PSEUDOS_FILE):
        try:
            with open(PSEUDOS_FILE, 'r', encoding='utf-8') as f: return json.load(f)
        except: pass
    return {}

def save_pseudos(data):
    with open(PSEUDOS_FILE, 'w', encoding='utf-8') as f: json.dump(data, f, indent=4)

def load_rivals():
    if os.path.exists(RIVAL_FILE):
        try:
            with open(RIVAL_FILE, 'r', encoding='utf-8') as f: return json.load(f)
        except: pass
    return {}

def save_rivals(data):
    with open(RIVAL_FILE, 'w', encoding='utf-8') as f: json.dump(data, f, indent=4)

def log_rival_event(user_id, rival_name, event_type, message):
    """📝 Enregistre une trace sécurisée et auto-nettoyante dans le log radar."""

class EventsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        if not self.rival_check_task.is_running():
            self.rival_check_task.start()

    async def cog_unload(self):
        self.rival_check_task.cancel()

    # ==========================================
    # 🎯 DÉFINIR LES OBJECTIFS (PROPRE AU SERVEUR)
    # ==========================================
    @app_commands.command(name="event_objectif_set", description="⚙️ Définit les objectifs de points par tranche de niveau")
    @app_commands.guild_only() 
    @app_commands.autocomplete(nom_event=event_autocomplete)
    @app_commands.describe(
        obj_t1="Objectif Niv 1-299 (Points)",
        obj_t2="Objectif Niv 300-649 (Points)",
        obj_t3="Objectif Niv 650-949 (Points)",
        obj_t4="Objectif Niv 950+ (Points)"
    )
    async def event_objectif_set(self, interaction: discord.Interaction, nom_event: str, obj_t1: int, obj_t2: int, obj_t3: int, obj_t4: int):
        from utils import MON_ID_DISCORD
        if not interaction.user.guild_permissions.administrator and interaction.user.id != MON_ID_DISCORD:
            return await interaction.response.send_message("❌ Réservé aux administrateurs.", ephemeral=True)

        event_keys = TRACKER_EVENTS.get(nom_event)
        if not event_keys:
            return await interaction.response.send_message(f"❌ Événement inconnu.", ephemeral=True)

        data = load_objectifs()
        guild_id = str(interaction.guild_id) 
        
        if guild_id not in data:
            data[guild_id] = {}
            
        data[guild_id][nom_event] = {
            "T1": obj_t1,
            "T2": obj_t2,
            "T3": obj_t3,
            "T4": obj_t4
        }
        save_objectifs(data)
        
        embed = discord.Embed(title=f"🎯 Objectifs mis à jour : {nom_event}", color=discord.Color.green())
        embed.description = f"Paramètres enregistrés pour le serveur **{interaction.guild.name}**."
        embed.add_field(name="Niv 1-299", value=f"**{format_num(obj_t1)}** pts", inline=True)
        embed.add_field(name="Niv 300-649", value=f"**{format_num(obj_t2)}** pts", inline=True)
        embed.add_field(name="Niv 650-949", value=f"**{format_num(obj_t3)}** pts", inline=True)
        embed.add_field(name="Niv 950+", value=f"**{format_num(obj_t4)}** pts", inline=True)
        
        await interaction.response.send_message(embed=embed)

    # ==========================================
    # 📊 BILAN DES OBJECTIFS (PROPRE AU SERVEUR)
    # ==========================================
    @app_commands.command(name="event_bilan", description="📈 Vérifie qui a atteint l'objectif d'alliance")
    @app_commands.guild_only() 
    @app_commands.autocomplete(nom_event=event_autocomplete)
    @app_commands.autocomplete(alliance=alliance_autocomplete)
    @app_commands.describe(lissage="Lisser le score sur les 3 dernières éditions de l'événement ?")
    async def event_bilan(self, interaction: discord.Interaction, nom_event: str, alliance: str, lissage: bool = False):
        try: await interaction.response.defer(thinking=True)
        except: return

        event_keys = TRACKER_EVENTS.get(nom_event)
        if not event_keys:
            return await interaction.followup.send(f"❌ Événement inconnu.")

        guild_id = str(interaction.guild_id)
        objectifs = load_objectifs().get(guild_id, {}).get(nom_event)
        if not objectifs:
            return await interaction.followup.send(f"⚠️ Aucun objectif défini pour **{nom_event}** sur ce serveur. Utilise `/event_objectif_set` d'abord.")

        headers = {'accept': 'application/json', 'gge-server': 'E4K_FR1'}
        safe_alliance = urllib.parse.quote(alliance)
        
        search_url = f"https://api.gge-tracker.com/api/v1/alliances/name/{safe_alliance}"
        try:
            async with self.bot.session.get(search_url, headers=headers, timeout=10) as resp:
                if resp.status != 200: return await interaction.followup.send("⚠️ Alliance introuvable via l'API.")
                data1 = await resp.json()
                target = data1[0] if isinstance(data1, list) and data1 else data1
                alliance_id = target.get('alliance_id') or target.get('id')
        except: return await interaction.followup.send("⚠️ Erreur de connexion avec GGE-Tracker.")

        if not alliance_id: return await interaction.followup.send("⚠️ Alliance introuvable.")

        stats_url = f"https://api.gge-tracker.com/api/v1/statistics/alliance/{alliance_id}"
        try:
            async with self.bot.session.get(stats_url, headers=headers, timeout=15) as resp:
                if resp.status != 200: return await interaction.followup.send("⚠️ Impossible de télécharger les statistiques.")
                stats_data = await resp.json()
        except: return await interaction.followup.send("⚠️ Erreur lors du téléchargement de l'historique.")

        best_history = []
        for key in event_keys:
            curr_history = stats_data.get("points", {}).get(key, [])
            if curr_history:
                best_history = curr_history
                break

        if not best_history:
            return await interaction.followup.send(f"⚠️ Aucun point enregistré pour **{alliance}** sur **{nom_event}**.")

        player_dict = {}
        alliance_members = set()
        cache = await get_cached_data()
        local_data = cache.get('players_data', {})

        for p_name, p_info in local_data.items():
            pid = str(p_info.get('player_id', p_info.get('id', '')))
            if pid and not pid.endswith('164'): pid += '164'
            
            lvl = int(p_info.get('level', 0))
            leg = int(p_info.get('legendary_level', 0))
            player_dict[pid] = {"name": p_name, "lvl": lvl, "leg": leg}
            
            p_all_id = str(p_info.get('allianceId', p_info.get('alliance_id', '')))
            if p_all_id and not p_all_id.endswith('164'): p_all_id += '164'
            if p_all_id == str(alliance_id) or str(p_info.get('allianceName', '')).lower() == alliance.lower():
                alliance_members.add(pid)

        if not alliance_members:
            return await interaction.followup.send(f"⚠️ Aucun membre de cette alliance trouvé dans le scan local.")

        dates_uniques = set(entry.get("date", "") for entry in best_history if entry.get("date"))
        dts_tries = sorted([datetime.fromisoformat(d.replace('Z', '+00:00')) for d in dates_uniques])
        
        clusters = []
        current_cluster = []
        for i, dt in enumerate(dts_tries):
            if not current_cluster:
                current_cluster.append(dt)
            else:
                diff_jours = (dt - current_cluster[-1]).total_seconds() / 86400.0
                if diff_jours > 2.0:
                    clusters.append(current_cluster)
                    current_cluster = [dt]
                else:
                    current_cluster.append(dt)
        if current_cluster: clusters.append(current_cluster)

        nb_events_cible = 3 if lissage else 1
        target_clusters = clusters[-nb_events_cible:] if len(clusters) >= nb_events_cible else clusters
        
        if not target_clusters: return await interaction.followup.send("⚠️ Données insuffisantes pour l'analyse.")

        player_cluster_scores = {pid: [] for pid in alliance_members}
        
        for cluster in target_clusters:
            start_date = cluster[0].isoformat().replace('+00:00', 'Z')
            end_date = (cluster[-1] + timedelta(days=1)).isoformat().replace('+00:00', 'Z')
            
            cluster_max = {}
            for entry in best_history:
                d_str = entry.get("date")
                pid = str(entry.get("player_id"))
                pt = int(entry.get("point", 0))
                
                if start_date <= d_str <= end_date and pid in alliance_members:
                    cluster_max[pid] = max(cluster_max.get(pid, 0), pt)
            
            for pid in alliance_members:
                player_cluster_scores[pid].append(cluster_max.get(pid, 0))

        resultats_par_tier = {"T1": [], "T2": [], "T3": [], "T4": []}
        
        reussites = 0
        total_evalues = len(alliance_members)

        for pid in alliance_members:
            scores = player_cluster_scores[pid]
            score_retenu = sum(scores) // len(scores) if scores else 0
            
            p_data = player_dict.get(pid, {"name": f"Inconnu_{pid[:4]}", "lvl": 0, "leg": 0})
            tier_key, tier_label = get_tier_and_label(p_data["lvl"], p_data["leg"])
            
            objectif_requis = objectifs.get(tier_key, 0)
            a_reussi = score_retenu >= objectif_requis
            
            if a_reussi: reussites += 1
            
            resultats_par_tier[tier_key].append({
                "name": p_data["name"],
                "score": score_retenu,
                "requis": objectif_requis,
                "reussi": a_reussi
            })

        mode_txt = "Moyenne lissée sur 3 événements" if lissage else "Dernier événement uniquement"
        taux = (reussites / total_evalues) * 100 if total_evalues > 0 else 0
        
        embed = discord.Embed(title=f"🎯 Bilan d'Objectifs : {alliance} - {nom_event}", color=discord.Color.gold(), timestamp=discord.utils.utcnow())
        embed.description = f"📊 **Mode :** {mode_txt}\n🏆 **Taux de réussite :** {taux:.1f}% ({reussites}/{total_evalues} membres)"

        for tier_key in ["T4", "T3", "T2", "T1"]:
            joueurs_tier = resultats_par_tier[tier_key]
            if not joueurs_tier: continue
            
            joueurs_tier.sort(key=lambda x: (-int(x["reussi"]), -x["score"])) 
            
            lignes = []
            for j in joueurs_tier:
                icone = "✅" if j["reussi"] else "❌"
                lignes.append(f"{icone} **{j['name']}** : {format_num(j['score'])} / {format_num(j['requis'])}")
            
            tier_label = get_tier_and_label(0, 999 if tier_key=="T4" else 700 if tier_key=="T3" else 400 if tier_key=="T2" else 100)[1]
            
            chunk_txt = ""
            for ligne in lignes:
                if len(chunk_txt) + len(ligne) > 1000:
                    embed.add_field(name=f"🛡️ Tranche {tier_label}", value=chunk_txt, inline=False)
                    chunk_txt = ligne + "\n"
                else:
                    chunk_txt += ligne + "\n"
            if chunk_txt:
                embed.add_field(name=f"🛡️ Tranche {tier_label}", value=chunk_txt, inline=False)

        embed.set_footer(text=BOT_VERSION)
        await interaction.followup.send(embed=embed)

    # =========================
    # COMMANDE : EVENT JOUEUR
    # =========================
    @app_commands.command(name="event_joueur", description="Consulter le dernier score d'un joueur ou son cumul.")
    @app_commands.autocomplete(nom_event=event_autocomplete)
    @app_commands.autocomplete(joueur=joueur_autocomplete)
    @app_commands.choices(mode=[
        app_commands.Choice(name="🎯 Dernier score", value="dernier"),
        app_commands.Choice(name="📊 Cumul", value="cumul")
    ])
    async def event_joueur(self, interaction: discord.Interaction, nom_event: str, joueur: str, mode: app_commands.Choice[str]):
        await interaction.response.defer(thinking=True)
        
        event_keys = TRACKER_EVENTS.get(nom_event)
        if not event_keys:
            return await interaction.followup.send(f"❌ Événement inconnu ou non géré par l'API.")

        player_id = None
        cache = await get_cached_data()
        local_data = cache.get('players_data', {})

        for p_name, p_info in local_data.items():
            if p_name.lower() == joueur.lower():
                raw_id = str(p_info.get('player_id', p_info.get('id', '')))
                player_id = raw_id + '164' if raw_id and not raw_id.endswith('164') else raw_id
                joueur = p_name 
                break

        if not player_id:
            return await interaction.followup.send(f"⚠️ Joueur **{joueur}** introuvable dans le cache local.")

        headers = {'accept': 'application/json', 'gge-server': 'E4K_FR1'}
        stats_url = f"https://api.gge-tracker.com/api/v1/statistics/player/{player_id}"
        
        try:
            async with self.bot.session.get(stats_url, headers=headers, timeout=10) as resp:
                if resp.status != 200:
                    return await interaction.followup.send(f"⚠️ Erreur API GGE-Tracker pour {joueur}.")
                stats_data = await resp.json()
        except Exception as e:
            logger.error(f"🚨 ERREUR API event_joueur : {type(e).__name__} - {str(e)}")
            return await interaction.followup.send(f"⚠️ Impossible de se connecter. Erreur interne : {type(e).__name__}")

        merged_history = []
        for key in event_keys:
            merged_history.extend(stats_data.get("points", {}).get(key, []))
        
        if not merged_history:
            return await interaction.followup.send(f"⚠️ **{joueur}** n'a aucun point enregistré pour l'événement **{nom_event}**.")

        merged_history.sort(key=lambda x: x.get("date", ""))
        alliance_name = stats_data.get("alliance_name") or "Sans alliance"

        sessions = []
        current_session = []
        for entry in merged_history:
            d_str = entry.get("date")
            pt = int(entry.get("point", 0))
            if not d_str: continue
            
            try:
                dt = datetime.fromisoformat(d_str.replace('Z', '+00:00'))
                if not current_session:
                    current_session.append((dt, pt))
                else:
                    last_dt = current_session[-1][0]
                    last_pt = current_session[-1][1]
                    
                    if pt < last_pt or (dt - last_dt).days > 3:
                        sessions.append(current_session)
                        current_session = [(dt, pt)]
                    else:
                        current_session.append((dt, pt))
            except: pass
            
        if current_session:
            sessions.append(current_session)

        # ==================================
        # 🟢 MODE : CUMUL (Paginé)
        # ==================================
        if mode.value == "cumul":
            nb_events = 30
            
            events_joues = len(sessions)
            avertissement = ""
            if events_joues < nb_events:
                avertissement = f"\n\n⚠️ **Manque de données** : Cumul sur {nb_events} events, mais seulement **{events_joues}** ont été joués/enregistrés."
                nb_events = events_joues
                
            recent_sessions = sessions[-nb_events:] if nb_events > 0 else []
            
            if not recent_sessions:
                return await interaction.followup.send(f"⚠️ Aucun historique exploitable pour calculer un cumul.")

            scores = []
            lignes_details = []
            for i, s in enumerate(reversed(recent_sessions)):
                max_score = max(s, key=lambda x: x[1])[1]
                scores.append(max_score)
                start_d = s[0][0].strftime("%d/%m/%Y")
                lignes_details.append(f"🔹 **Event -{i+1}** ({start_d}) : **{format_num(max_score)}** pts")

            total_score = sum(scores)
            moyenne = total_score // len(scores)
            
            sorted_scores = sorted(scores)
            n = len(sorted_scores)
            mediane = (sorted_scores[n//2 - 1] + sorted_scores[n//2]) // 2 if n % 2 == 0 else sorted_scores[n//2]
            
            pire_score = sorted_scores[0]
            meilleur_score = sorted_scores[-1]

            start_date_global = recent_sessions[0][0][0].strftime("%d/%m/%Y")
            end_date_global = recent_sessions[-1][-1][0].strftime("%d/%m/%Y")

            stats_txt = (
                f"> 🏆 **TOTAL CUMULÉ : {format_num(total_score)} pts** 🔥\n"
                f"> 📊 **Moyenne/Event** : {format_num(moyenne)} pts\n"
                f"> ⚖️ **Médiane** : {format_num(mediane)} pts\n"
                f"> 🚀 **Meilleur Score** : {format_num(meilleur_score)} pts\n"
                f"> 📉 **Pire Score** : {format_num(pire_score)} pts"
            )
            
            embeds = []
            chunk_size = 15
            nb_pages = max(1, (len(lignes_details) - 1) // chunk_size + 1)

            for i in range(0, len(lignes_details), chunk_size):
                chunk = lignes_details[i:i+chunk_size]
                page_actuelle = (i // chunk_size) + 1
                
                embed = discord.Embed(title=f"📊 Analyse & Cumul : {joueur} - {nom_event}", color=discord.Color.gold(), timestamp=discord.utils.utcnow())
                embed.description = f"**Alliance actuelle :** [{alliance_name}]{avertissement}"
                embed.add_field(name=f"📈 Bilan sur les {len(recent_sessions)} derniers events\n*(Période du {start_date_global} au {end_date_global})*", value=stats_txt, inline=False)
                embed.add_field(name=f"Détails des sessions (Page {page_actuelle}/{nb_pages})", value="\n".join(chunk), inline=False)
                embed.set_footer(text=BOT_VERSION)
                embeds.append(embed)

            if len(embeds) == 1:
                await interaction.followup.send(embed=embeds[0])
            else:
                view = PaginationView(embeds)
                await interaction.followup.send(embed=embeds[0], view=view)

        # ==================================
        # 🟢 MODE : DERNIER SCORE
        # ==================================
        else:
            latest_point = 0
            latest_date = ""
            for entry in merged_history:
                d_str = entry.get("date", "")
                pt = int(entry.get("point", 0))
                if d_str > latest_date:
                    latest_date = d_str
                    latest_point = pt

            if latest_point == 0:
                return await interaction.followup.send(f"⚠️ **{joueur}** est actuellement à 0 pt sur **{nom_event}**.")

            embed = discord.Embed(title=f"🎯 Score en direct : {joueur} - {nom_event}", color=discord.Color.blue(), timestamp=discord.utils.utcnow())
            embed.add_field(name="👤 Profil", value=f"**Joueur :** {joueur}\n**Alliance :** [{alliance_name}]", inline=True)
            embed.add_field(name="🏅 Score Actuel", value=f"**{format_num(latest_point)} pts** 🔥", inline=True)
            
            if latest_date:
                ts_r = get_discord_timestamp(latest_date, 'R')
                ts_t = get_discord_timestamp(latest_date, 't')
                embed.add_field(name="⏱️ Dernière Frappe", value=f"Relevée par l'API {ts_r} (*{ts_t}*)", inline=False)
                
            embed.set_footer(text=BOT_VERSION)
            await interaction.followup.send(embed=embed)

    # =========================
    # COMMANDE : EVENT ALLIANCE
    # =========================
    @app_commands.command(name="event_alliance", description="Classement et participation d'une alliance sur un événement")
    @app_commands.autocomplete(nom_event=event_autocomplete)
    @app_commands.autocomplete(alliance=alliance_autocomplete)
    @app_commands.choices(affichage=[
        app_commands.Choice(name="📑 Pages interactives (Boutons)", value="pages"),
        app_commands.Choice(name="📜 Liste complète (Message unique)", value="liste")
    ])
    @app_commands.describe(
        affichage="Mode d'affichage des joueurs (Pages avec boutons OU Long message unique)",
        format_txt="Recevoir en plus le classement sous forme de fichier .txt ?"
    )
    async def event_alliance(self, interaction: discord.Interaction, nom_event: str, alliance: str, affichage: str = "pages", format_txt: bool = False):
        await interaction.response.defer(thinking=True)
        
        event_keys = TRACKER_EVENTS.get(nom_event)
        if not event_keys:
            return await interaction.followup.send(f"❌ Événement inconnu ou non géré par l'API.")

        headers = {'accept': 'application/json', 'gge-server': 'E4K_FR1'}
        safe_alliance = urllib.parse.quote(alliance)
        
        search_url = f"https://api.gge-tracker.com/api/v1/alliances/name/{safe_alliance}"
        try:
            async with self.bot.session.get(search_url, headers=headers, timeout=10) as resp:
                if resp.status != 200:
                    return await interaction.followup.send("⚠️ Impossible de trouver l'ID de cette alliance.")
                data1 = await resp.json()
                target = data1[0] if isinstance(data1, list) and data1 else data1
                alliance_id = target.get('alliance_id') or target.get('id')
        except Exception:
            return await interaction.followup.send("⚠️ Erreur de connexion avec GGE-Tracker.")

        if not alliance_id:
            return await interaction.followup.send("⚠️ Alliance introuvable.")
            
        stats_url = f"https://api.gge-tracker.com/api/v1/statistics/alliance/{alliance_id}"
        try:
            async with self.bot.session.get(stats_url, headers=headers, timeout=15) as resp:
                if resp.status != 200:
                    return await interaction.followup.send("⚠️ Impossible de télécharger les statistiques.")
                stats_data = await resp.json()
        except Exception:
            return await interaction.followup.send("⚠️ Erreur lors du téléchargement de l'historique.")
                
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
            return await interaction.followup.send(f"⚠️ Aucun point enregistré pour **{alliance}** sur l'événement **{nom_event}**.")
            
        player_dict = {}
        alliance_members = set()
        cache = await get_cached_data()
        local_data = cache.get('players_data', {})

        for p_name, p_info in local_data.items():
            pid = str(p_info.get('player_id', p_info.get('id', '')))
            if pid and not pid.endswith('164'): pid += '164'
            player_dict[pid] = p_name
            
            p_all_id = str(p_info.get('allianceId', p_info.get('alliance_id', '')))
            if p_all_id and not p_all_id.endswith('164'): p_all_id += '164'
            
            if p_all_id == str(alliance_id) or str(p_info.get('allianceName', '')).lower() == alliance.lower():
                alliance_members.add(pid)

        cutoff_str = ""
        if best_history:
            dates_uniques = set(entry.get("date", "") for entry in best_history if entry.get("date"))
            if dates_uniques:
                dts_tries = sorted([datetime.fromisoformat(d.replace('Z', '+00:00')) for d in dates_uniques])
                debut_cluster_actuel = dts_tries[-1]
                
                for i in range(len(dts_tries)-2, -1, -1):
                    diff_jours = (dts_tries[i+1] - dts_tries[i]).total_seconds() / 86400.0
                    if diff_jours > 2.0:
                        debut_cluster_actuel = dts_tries[i+1]
                        break
                    debut_cluster_actuel = dts_tries[i]
                
                cutoff_str = (debut_cluster_actuel - timedelta(hours=1)).isoformat().replace('+00:00', 'Z')

        latest_points = {}
        for entry in best_history:
            pid = str(entry.get("player_id"))
            pt = int(entry.get("point", 0))
            d_str = entry.get("date", "")
            
            if cutoff_str and d_str < cutoff_str:
                continue
            
            if pid not in latest_points or d_str > latest_points[pid]['date']:
                latest_points[pid] = {'date': d_str, 'point': pt}
                
        active_players = []
        zero_players = []
        
        all_pids_to_check = set(alliance_members).union(set(latest_points.keys()))
        
        for pid in all_pids_to_check:
            pt = latest_points.get(pid, {}).get('point', 0)
            p_name = player_dict.get(pid, f"ID Inconnu ({pid[:4]}...)") 
            
            if pt > 0:
                active_players.append((p_name, pt))
            elif pid in alliance_members:
                zero_players.append(p_name)
                
        active_players.sort(key=lambda x: x[1], reverse=True)

        if not active_players and not zero_players:
            return await interaction.followup.send(f"⚠️ Impossible de récupérer les joueurs. L'événement n'a peut-être pas commencé.")

        # --- Statistiques Globales ---
        total_score = sum(x[1] for x in active_players)
        total_current_members = len(alliance_members)
        active_current_count = sum(1 for pid in alliance_members if latest_points.get(pid, {}).get('point', 0) > 0)
        taux_participation = (active_current_count / total_current_members) * 100 if total_current_members > 0 else 0.0

        # --- Gestion du fichier .TXT ---
        if format_txt:
            date_api = "Inconnue"
            if global_latest_str:
                try: date_api = datetime.fromisoformat(global_latest_str.replace('Z', '+00:00')).strftime("%d/%m à %H:%M")
                except: pass
                
            import io
            txt_content = f"🏆 CLASSEMENT {nom_event.upper()} - {alliance.upper()} 🏆\n"
            txt_content += f"Date de mise à jour (API) : {date_api}\n"
            txt_content += f"Points Totaux : {total_score:,}\n"
            txt_content += f"Taux de participation : {taux_participation:.1f}% ({active_current_count}/{total_current_members})\n"
            txt_content += "="*50 + "\n\n"
            
            txt_content += "--- JOUEURS CLASSÉS ---\n"
            for j, (name, score) in enumerate(active_players):
                txt_content += f"{j+1}. {name} - {score:,} pts\n"
                
            if zero_players:
                txt_content += "\n--- JOUEURS A 0 POINT ---\n"
                for name in zero_players:
                    txt_content += f"- {name}\n"
            
            file_bytes = io.BytesIO(txt_content.encode('utf-8'))
            fichier_discord = discord.File(file_bytes, filename=f"Classement_{alliance}_{nom_event}.txt")
            
            return await interaction.followup.send(content=f"✅ Voici le classement détaillé de **{alliance}** :", file=fichier_discord)

        # --- Création des lignes de classement ---
        lignes_classement = []
        for j, (name, score) in enumerate(active_players):
            medal = "🥇" if j == 0 else "🥈" if j == 1 else "🥉" if j == 2 else f"**{j+1}.**"
            lignes_classement.append(f"{medal} **{name}** ➔ **{format_num(score)} pts**")
            
        for name in zero_players:
            lignes_classement.append(f"😴 **{name}** ➔ **0 pts**")

        stats_text = (
            f"**Points Totaux** : **{format_num(total_score)}**\n"
            f"**Participation** : {taux_participation:.1f}% ({active_current_count}/{total_current_members} membres)"
        )

        # --- MODE : MESSAGE UNIQUE (Liste Complète) ---
        if affichage == "liste":
            embed = discord.Embed(title=f"🛡️ {alliance} - {nom_event}", color=discord.Color.green(), timestamp=discord.utils.utcnow())
            embed.add_field(name="📊 Statistiques", value=stats_text, inline=False)
            
            chunk_txt = ""
            part_num = 1
            for ligne in lignes_classement:
                if len(chunk_txt) + len(ligne) + 1 > 1024:
                    embed.add_field(name=f"🏆 Classement (Partie {part_num})", value=chunk_txt, inline=False)
                    chunk_txt = ligne + "\n"
                    part_num += 1
                else:
                    chunk_txt += ligne + "\n"
                    
            if chunk_txt:
                embed.add_field(name=f"🏆 Classement (Partie {part_num})", value=chunk_txt, inline=False)
                
            if global_latest_str:
                ts_r = get_discord_timestamp(global_latest_str, 'R')
                ts_t = get_discord_timestamp(global_latest_str, 't')
                embed.add_field(name="⏱️ Actualisation", value=f"Dernier relevé effectué {ts_r} (*{ts_t}*)", inline=False)
                
            embed.set_footer(text=BOT_VERSION)
            await interaction.followup.send(embed=embed)

        # --- MODE : PAGES INTERACTIVES ---
        else:
            embeds = []
            chunk_size = 15
            nb_pages = max(1, (len(lignes_classement) - 1) // chunk_size + 1)
            
            for i in range(0, len(lignes_classement), chunk_size):
                chunk = lignes_classement[i:i+chunk_size]
                page_actuelle = (i // chunk_size) + 1
                
                embed = discord.Embed(title=f"🛡️ {alliance} - {nom_event}", color=discord.Color.green(), timestamp=discord.utils.utcnow())
                embed.add_field(name="📊 Statistiques", value=stats_text, inline=False)
                embed.add_field(name=f"🏆 Classement (Page {page_actuelle}/{nb_pages})", value="\n".join(chunk), inline=False)
                
                if global_latest_str:
                    ts_r = get_discord_timestamp(global_latest_str, 'R')
                    ts_t = get_discord_timestamp(global_latest_str, 't')
                    embed.add_field(name="⏱️ Actualisation", value=f"Dernier relevé effectué {ts_r} (*{ts_t}*)", inline=False)
                    
                embed.set_footer(text=BOT_VERSION)
                embeds.append(embed)

            if len(embeds) == 1:
                await interaction.followup.send(embed=embeds[0])
            else:
                view = PaginationView(embeds)
                await interaction.followup.send(embed=embeds[0], view=view)

    # =========================
    # COMMANDE : SERVEUR
    # =========================
    @app_commands.command(name="serveur", description="Affiche les statistiques globales du serveur et le Top 15 Alliances")
    async def serveur(self, interaction: discord.Interaction):
        try: await interaction.response.defer(thinking=True)
        except: return

        api_data = {}
        headers = {'accept': 'application/json', 'gge-server': 'E4K_FR1'}
        
        url = "https://api.gge-tracker.com/api/v1/server/statistics"
        try:
            async with self.bot.session.get(url, headers=headers, timeout=10) as resp:
                if resp.status == 200:
                    json_resp = await resp.json()
                    if isinstance(json_resp, list) and len(json_resp) > 0:
                        api_data = json_resp[0]
        except Exception: pass

        alliance_might = {}
        total_joueurs_locaux = 0
        cache = await get_cached_data()
        local_data = cache.get('players_data', {})
        total_joueurs_locaux = len(local_data)
        
        for p_name, p_info in local_data.items():
            a_name = p_info.get('alliance') or p_info.get('alliance_name')
            if isinstance(a_name, dict): a_name = a_name.get('name')
            might = int(p_info.get('main_points', 0))
            
            if a_name and a_name not in ["", "Sans alliance", "None"]:
                alliance_might[a_name] = alliance_might.get(a_name, 0) + might

        top_alliances = sorted(alliance_might.items(), key=lambda x: x[1], reverse=True)[:15]

        embed = discord.Embed(title="🌍 Tableau de Bord - E4K_FR1", color=0x010101, timestamp=discord.utils.utcnow())

        if api_data:
            nb_joueurs = api_data.get('players_count') or total_joueurs_locaux
            nb_alli = api_data.get('alliance_count', 'Inconnu')
            colombes = api_data.get('players_in_peace', 0)
            puiss_totale = api_data.get('total_might', 0)
            puiss_moyenne = api_data.get('avg_might', 0)
            
            stats_txt = f"👥 **Joueurs actifs** : {nb_joueurs:,}\n" \
                        f"🛡️ **Alliances** : {nb_alli:,}\n" \
                        f"🕊️ **Sous colombe** : {colombes:,}\n" \
                        f"⚔️ **Puissance Totale** : {format_num(puiss_totale)} PP\n" \
                        f"⚖️ **Puissance Moyenne** : {format_num(puiss_moyenne)} PP/joueur"
            embed.add_field(name="📊 Statistiques Globales", value=stats_txt, inline=False)
        else:
            embed.add_field(name="📊 Statistiques Globales", value="⚠️ *Données de l'API indisponibles actuellement.*", inline=False)

        if top_alliances:
            top_txt = ""
            for i, (nom_alli, score) in enumerate(top_alliances):
                medal = "🥇" if i == 0 else "🥈" if i == 1 else "🥉" if i == 2 else f"**{i+1}.**"
                top_txt += f"{medal} **{nom_alli}** ➔ **{format_num(score)}** PP\n"
            embed.add_field(name="🏆 Top 15 Alliances (Puissance)", value=top_txt, inline=False)
        else:
            embed.add_field(name="🏆 Top 15 Alliances", value="⚠️ *Impossible de calculer le classement local.*", inline=False)

        embed.set_footer(text=BOT_VERSION)
        await interaction.followup.send(embed=embed)

    # ==========================================
    # 🔗 LIAISON DU COMPTE DISCORD
    # ==========================================
    @app_commands.command(name="set_pseudo", description="🔗 Lie ton compte Discord à ton pseudo GGE")
    @app_commands.autocomplete(pseudo=joueur_autocomplete)
    async def set_pseudo(self, interaction: discord.Interaction, pseudo: str):
        data = load_pseudos()
        data[str(interaction.user.id)] = pseudo
        save_pseudos(data)
        await interaction.response.send_message(f"✅ Ton compte Discord est maintenant lié au seigneur **{pseudo}** !", ephemeral=True)

    # ==========================================
    # 🕵️ RADAR DE RIVAUX
    # ==========================================

    rival_group = app_commands.Group(name="rival", description="🏆 Radar de Compétition (MP uniquement)")

    @rival_group.command(name="start", description="🚀 Lance ton radar de compétition (MP uniquement)")
    @app_commands.autocomplete(nom_event=event_autocomplete)
    @app_commands.describe(seuil="Alerte quand un rival atteint X% de ton score (ex: 90)")
    async def rival_start(self, interaction: discord.Interaction, nom_event: str, seuil: int = 90):
        if interaction.guild is not None:
            return await interaction.response.send_message("🤫 **Chut !** Pour garder ton avantage secret, cette commande ne s'utilise qu'en **Message Privé** avec moi.", ephemeral=True)

        pseudos = load_pseudos()
        uid = str(interaction.user.id)
        
        if uid not in pseudos:
            return await interaction.response.send_message("❌ Tu dois d'abord lier ton compte avec `/set_pseudo` !")
            
        data = load_rivals()
        data[uid] = {
            "event": nom_event,
            "seuil": max(50, min(99, seuil)),
            "rivaux": [],
            "last_known_scores": {},
            "started_at": discord.utils.utcnow().isoformat()  # 💡 NOUVEAU : Sauvegarde de la date de départ
        }
        save_rivals(data)
        await interaction.response.send_message(f"Campagne de suivi lancée ! Radar **{nom_event}** activé (Seuil: **{seuil}%**). Ajoute tes cibles avec `/rival add` !")

    @rival_group.command(name="add", description="🕵️ Ajoute jusqu'à 5 joueurs à ton radar (MP uniquement)")
    @app_commands.autocomplete(joueur1=joueur_autocomplete)
    @app_commands.autocomplete(joueur2=joueur_autocomplete)
    @app_commands.autocomplete(joueur3=joueur_autocomplete)
    @app_commands.autocomplete(joueur4=joueur_autocomplete)
    @app_commands.autocomplete(joueur5=joueur_autocomplete)
    async def rival_add(self, interaction: discord.Interaction, joueur1: str, joueur2: str = None, joueur3: str = None, joueur4: str = None, joueur5: str = None):
        if interaction.guild is not None:
            return await interaction.response.send_message("🤫 **Chut !** Ajoute tes cibles secrètes en **Message Privé** uniquement.", ephemeral=True)

        data = load_rivals()
        uid = str(interaction.user.id)
        
        if uid not in data:
            return await interaction.response.send_message("❌ Utilise d'abord `/rival start`.")
            
        liste_actuelle = data[uid]["rivaux"]
        nouveaux_joueurs = [j for j in [joueur1, joueur2, joueur3, joueur4, joueur5] if j]
        ajoutes = []
        
        for j in nouveaux_joueurs:
            # 🚨 SÉCURITÉ LIMITATION STRICTE À 10 JOUEURS
            if len(liste_actuelle) >= 10:
                if ajoutes:
                    await interaction.response.send_message(f"⚠️ Limite de **10 joueurs** atteinte ! Ajouts partiels : {', '.join(ajoutes)}")
                else:
                    await interaction.response.send_message("⚠️ Limite de **10 joueurs max** atteinte ! Impossible d'ajouter de nouveaux rivaux.")
                save_rivals(data)
                return
                
            if j not in liste_actuelle:
                liste_actuelle.append(j)
                ajoutes.append(j)
                
        save_rivals(data)
        
        if ajoutes:
            await interaction.response.send_message(f"🎯 Joueurs ajoutés au radar : **{', '.join(ajoutes)}**\n*(Total: {len(liste_actuelle)}/10)*")
        else:
            await interaction.response.send_message("⚠️ Ces joueurs étaient déjà dans ton radar.")

    @rival_group.command(name="list", description="📋 Affiche l'état de ton radar de rivaux (MP uniquement)")
    async def rival_list(self, interaction: discord.Interaction):
        if interaction.guild is not None:
            return await interaction.response.send_message("🤫 **Chut !** Viens en **Message Privé** pour voir ta liste de cibles.", ephemeral=True)

        data = load_rivals()
        uid = str(interaction.user.id)
        if uid not in data: return await interaction.response.send_message("🕸️ Ton radar est inactif.")
        
        config = data[uid]
        embed = discord.Embed(title=f"🕵️ Radar Actif : {config['event']}", color=discord.Color.dark_grey())
        embed.description = f"**Seuil d'alerte :** {config['seuil']}%\n**Cibles ({len(config['rivaux'])}/10) :**\n" + "\n".join([f"🔸 {r}" for r in config['rivaux']])
        await interaction.response.send_message(embed=embed)

    @rival_group.command(name="stop", description="🛑 Coupe ton radar de rivaux (MP uniquement)")
    async def rival_stop(self, interaction: discord.Interaction):
        if interaction.guild is not None:
            return await interaction.response.send_message("🤫 **Chut !** Gère ton radar en **Message Privé**.", ephemeral=True)

        data = load_rivals()
        if str(interaction.user.id) in data:
            del data[str(interaction.user.id)]
            save_rivals(data)
            await interaction.response.send_message("🛑 Radar désactivé. Tes rivaux peuvent souffler.")
        else:
            await interaction.response.send_message("⚠️ Tu n'avais aucun radar actif.")

    # ==========================================
    # 🛰️ LA BOUCLE DE SURVEILLANCE DES RIVAUX
    # ==========================================
    @tasks.loop(minutes=10)
    async def rival_check_task(self):
        try:
            data = load_rivals()
            pseudos = load_pseudos()
            if not data: return

            headers = {'User-Agent': 'Mozilla/5.0 GGE-Assistant', 'accept': 'application/json', 'gge-server': 'E4K_FR1'}
            maintenant = discord.utils.utcnow()
            changes_detected = False

            # ⏰ 1. FILTRE D'AUTO-FERMETURE (4 jours max)
            for user_id, config in list(data.items()):
                started_at = config.get("started_at")
                if started_at:
                    try:
                        dt_start = datetime.fromisoformat(started_at.replace('Z', '+00:00'))
                        if (maintenant - dt_start).total_seconds() >= 345600:  # 345600 secondes = 4 jours
                            # Envoi du message d'information à l'utilisateur
                            try:
                                user = self.bot.get_user(int(user_id)) or await self.bot.fetch_user(int(user_id))
                                embed_close = discord.Embed(
                                    title="🛑 FIN DU RADAR / FERMETURE", 
                                    description=f"Ton radar de compétition pour l'événement **{config['event']}** a été automatiquement arrêté après 4 jours d'activité pour préserver le système. Tu peux le re-créer si il s'agit d'une erreur",
                                    color=discord.Color.red()
                                )
                                await user.send(embed=embed_close)
                            except Exception as e:
                                logger.error(f"Impossible d'avertir l'utilisateur {user_id} de la fermeture du rival : {e}")
                            
                            # Nettoyage
                            del data[user_id]
                            changes_detected = True
                            continue  # On zappe le reste de l'analyse pour cette personne
                    except Exception as e:
                        logger.error(f"Erreur calcul expiration du rival pour {user_id} : {e}")

            if not data:
                if changes_detected: save_rivals(data)
                return

            player_dict = {}
            cache = await get_cached_data()
            local_data = cache.get('players_data', {})
            for p_name, p_info in local_data.items():
                raw_id = str(p_info.get('player_id', p_info.get('id', '')))
                pid = raw_id + '164' if raw_id and not raw_id.endswith('164') else raw_id
                player_dict[p_name.lower()] = pid

            cache_api_scores = {}

            async def get_score(pseudo, event_keys):
                pseudo_lower = pseudo.lower()
                if pseudo_lower in cache_api_scores:
                    return cache_api_scores[pseudo_lower]

                pid = player_dict.get(pseudo_lower)
                if not pid:
                    try:
                        search_url = f"https://api.gge-tracker.com/api/v1/players/{urllib.parse.quote(pseudo)}"
                        async with self.bot.session.get(search_url, headers=headers, timeout=5) as r:
                            if r.status == 200:
                                p_data = await r.json()
                                if isinstance(p_data, list) and p_data:
                                    raw_id = str(p_data[0].get('id', p_data[0].get('playerId')))
                                    pid = raw_id + '164' if raw_id and not raw_id.endswith('164') else raw_id
                    except: pass
                    
                if not pid: 
                    cache_api_scores[pseudo_lower] = (0, "")
                    return 0, ""

                try:
                    stats_url = f"https://api.gge-tracker.com/api/v1/statistics/player/{pid}"
                    async with self.bot.session.get(stats_url, headers=headers, timeout=10) as r:
                        if r.status == 200:
                            pts_dict = (await r.json()).get("points", {})
                            merged_history = []
                            for key in event_keys:
                                merged_history.extend(pts_dict.get(key, []))
                                
                            if not merged_history: 
                                cache_api_scores[pseudo_lower] = (0, "")
                                return 0, ""
                            
                            merged_history.sort(key=lambda x: x.get("date", ""))
                            latest_entry = merged_history[-1]
                            
                            resultat = (int(latest_entry.get("point", 0)), latest_entry.get("date", ""))
                            cache_api_scores[pseudo_lower] = resultat
                            return resultat
                except: pass
                
                cache_api_scores[pseudo_lower] = (0, "")
                return 0, ""

            for user_id, config in data.items():
                my_pseudo = pseudos.get(user_id)
                if not my_pseudo: continue 
                
                event_keys = TRACKER_EVENTS.get(config["event"], [])
                if not event_keys: continue

                my_score, my_date = await get_score(my_pseudo, event_keys)
                
                if "last_known_scores" not in config:
                    config["last_known_scores"] = {}

                last_my_score = config["last_known_scores"].get("_ME_", my_score)
                ma_frappe = my_score - last_my_score
                if ma_frappe != 0 or "_ME_" not in config["last_known_scores"]:
                    config["last_known_scores"]["_ME_"] = my_score
                    changes_detected = True

                for rival in config["rivaux"]:
                    rival_score, rival_date = await get_score(rival, event_keys)
                    if rival_score == 0: continue
                    
                    last_score = config["last_known_scores"].get(rival, 0)
                    if rival_score <= last_score and last_score != 0:
                        continue
                        
                    frappe = rival_score - last_score
                    config["last_known_scores"][rival] = rival_score
                    changes_detected = True
                    
                    ratio = (rival_score / my_score) * 100 if my_score > 0 else 99999
                    seuil = config["seuil"]
                    alerte_embed = None
                    
                    ma_frappe_txt = f"\n*(Pendant ce temps, tu as fait **+{format_num(ma_frappe)}** pts)*" if ma_frappe > 0 else ""
                    
                    if rival_score > my_score:
                        alerte_embed = discord.Embed(title="🚨 ALERTE ROUGE : DÉPASSEMENT !", color=discord.Color.brand_red())
                        alerte_embed.description = f"**{rival}** vient de te passer devant !{ma_frappe_txt}"
                        alerte_embed.add_field(name="Ses Points Gagnés", value=f"**+{format_num(frappe)}** pts 🔥", inline=False)
                        alerte_embed.add_field(name="Son Score Actuel", value=f"**{format_num(rival_score)}**", inline=True)
                        alerte_embed.add_field(name="Ton Score Actuel", value=f"**{format_num(my_score)}**", inline=True)
                        log_rival_event(user_id, rival, "DÉPASSEMENT", f"+{frappe} pts. Total: {rival_score} vs {my_score}.")
                            
                    elif ratio >= seuil:
                        alerte_embed = discord.Embed(title="⚠️ ALERTE : RAPPROCHEMENT", color=discord.Color.orange())
                        alerte_embed.description = f"**{rival}** s'approche dangereusement ! Il a atteint **{int(ratio)}%** de ton score.{ma_frappe_txt}"
                        alerte_embed.add_field(name="Ses Points Gagnés", value=f"**+{format_num(frappe)}** pts ⚔️", inline=False)
                        alerte_embed.add_field(name="Écart restant", value=f"**{format_num(my_score - rival_score)}** points", inline=False)
                        log_rival_event(user_id, rival, "APPROCHE", f"+{frappe} pts. Ratio: {ratio:.1f}%.")

                    if alerte_embed:
                        if rival_date:
                            ts_r = get_discord_timestamp(rival_date, 'R')
                            ts_t = get_discord_timestamp(rival_date, 't')
                            alerte_embed.add_field(name="⏱️ Relevé API", value=f"Frappe enregistrée par l'API {ts_r} (*{ts_t}*)", inline=False)
                        else:
                            alerte_embed.set_footer(text=f"Heure du scan : {datetime.now().strftime('%H:%M')}")

                        try:
                            user = self.bot.get_user(int(user_id)) or await self.bot.fetch_user(int(user_id))
                            await user.send(embed=alerte_embed)
                        except Exception as e:
                            log_rival_event(user_id, rival, "ERREUR MP", f"Impossible d'envoyer l'alerte : {e}")

                    await asyncio.sleep(1)

            if changes_detected:
                save_rivals(data)

        except Exception as e:
            logger.error(f"🚨 [RIVAL RADAR CRASH] : {traceback.format_exc()}")

# 🔌 Branchement du Cog
async def setup(bot: commands.Bot):
    await bot.add_cog(EventsCog(bot))
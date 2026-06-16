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

# 🛠️ Import de la boîte à outils unifiée (Ajout des versions ASYNC et de MON_ID_DISCORD)
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
    get_cached_data,
    MON_ID_DISCORD,
    setup_embed_footer,
    generer_rapport_alliance_embed,
    load_objectifs_async,
    save_objectifs_async,
    load_pseudos_async,
    save_pseudos_async,
    load_rivals_async,
    save_rivals_async
)

logger = logging.getLogger("GGE_Bot")
radar_logger = logging.getLogger("Radar_Log")
radar_logger.setLevel(logging.INFO)

def get_tier_and_label(lvl, leg):
    """Détermine la tranche de niveau du joueur"""
    if leg < 300 and lvl <= 70: return "T1", "Niv 1-299"
    if leg < 650: return "T2", "Niv 300-649"
    if leg < 950: return "T3", "Niv 650-949"
    return "T4", "Niv 950+"

def log_rival_event(user_id, rival_name, event_type, message):
    """📝 Enregistre une trace sécurisée dans le log radar."""
    pass

class EventsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        
        # 🎨 PALETTE AMÉTHYSTE ET VIOLET (Une nuance unique par commande d'événement)
        self.clr_obj_set        = discord.Color.from_rgb(75, 0, 130)     # Indigo / Violet Profond
        self.clr_bilan          = discord.Color.from_rgb(102, 51, 153)   # Rebecca Purple
        self.clr_joueur_dernier = discord.Color.from_rgb(138, 43, 226)  # Violet Éclatant
        self.clr_joueur_cumul   = discord.Color.from_rgb(153, 50, 204)  # Orchidée Sombre
        self.clr_alliance       = discord.Color.from_rgb(186, 85, 211)  # Orchidée Médium
        self.clr_serveur        = discord.Color.from_rgb(218, 112, 214) # Orchidée Douce
        self.clr_rival_list     = discord.Color.from_rgb(238, 130, 238) # Violet Clair
        self.clr_woa_historique = discord.Color.from_rgb(81, 45, 168)  # Violet Profond
        self.clr_woa_classement = discord.Color.from_rgb(170, 0, 255)  # Violet Néon
        self.clr_woa_bilan      = discord.Color.from_rgb(126, 87, 194) # Lavande Douce
        self.clr_aqua           = discord.Color.from_rgb(0, 180, 216)   # Bleu Abysse / Cyan (Aquamarine)

    async def cog_load(self):
        if not self.rival_check_task.is_running():
            self.rival_check_task.start()

    async def cog_unload(self):
        self.rival_check_task.cancel()

    # ==========================================
    # 🎯 DÉFINIR LES OBJECTIFS (PROPRE AU SERVEUR)
    # ==========================================
    @app_commands.command(name="event_objectif_set", description="Définit les objectifs de points par tranche de niveau")
    @app_commands.guild_only() 
    @app_commands.autocomplete(nom_event=event_autocomplete)
    @app_commands.describe(
        obj_t1="Objectif Niv 1-299 (Points)",
        obj_t2="Objectif Niv 300-649 (Points)",
        obj_t3="Objectif Niv 650-949 (Points)",
        obj_t4="Objectif Niv 950+ (Points)"
    )
    async def event_objectif_set(self, interaction: discord.Interaction, nom_event: str, obj_t1: int, obj_t2: int, obj_t3: int, obj_t4: int):
        if not interaction.user.guild_permissions.administrator and interaction.user.id != MON_ID_DISCORD:
            return await interaction.response.send_message("<:error:1512505075220611172> Réservé aux administrateurs.", ephemeral=True)

        event_keys = TRACKER_EVENTS.get(nom_event)
        if not event_keys:
            return await interaction.response.send_message("<:error:1512505075220611172> Événement inconnu.", ephemeral=True)

        # 🔐 Sécurisé : Lecture et écriture via verrous asynchrones
        data = await load_objectifs_async()
        guild_id = str(interaction.guild_id) 
        
        if guild_id not in data:
            data[guild_id] = {}
            
        data[guild_id][nom_event] = {
            "T1": obj_t1,
            "T2": obj_t2,
            "T3": obj_t3,
            "T4": obj_t4
        }
        await save_objectifs_async(data)
        
        embed = discord.Embed(title=f"<:icon_points:1512502439339888820> Objectifs mis à jour : {nom_event}", color=self.clr_obj_set)
        embed.description = f"Paramètres enregistrés pour le serveur **{interaction.guild.name}**."
        embed.add_field(name="Niv 1-299", value=f"**{format_num(obj_t1)}** pts", inline=True)
        embed.add_field(name="Niv 300-649", value=f"**{format_num(obj_t2)}** pts", inline=True)
        embed.add_field(name="Niv 650-949", value=f"**{format_num(obj_t3)}** pts", inline=True)
        embed.add_field(name="Niv 950+", value=f"**{format_num(obj_t4)}** pts", inline=True)
        
        await interaction.response.send_message(embed=embed)

    # ==========================================
    # BILAN DES OBJECTIFS (PROPRE AU SERVEUR)
    # ==========================================
    @app_commands.command(name="event_bilan", description="Vérifie qui a atteint l'objectif d'alliance")
    @app_commands.guild_only() 
    @app_commands.autocomplete(nom_event=event_autocomplete)
    @app_commands.autocomplete(alliance=alliance_autocomplete)
    @app_commands.describe(lissage="Lisser le score sur les 3 dernières éditions de l'événement ?")
    async def event_bilan(self, interaction: discord.Interaction, nom_event: str, alliance: str, lissage: bool = True):
        try: await interaction.response.defer(thinking=True)
        except: return

        event_keys = TRACKER_EVENTS.get(nom_event)
        if not event_keys:
            return await interaction.followup.send(f"<:error:1512505075220611172> Événement inconnu.")

        guild_id = str(interaction.guild_id)
        # 🔐 Sécurisé : Chargement asynchrone protégé
        objectifs_data = await load_objectifs_async()
        objectifs = objectifs_data.get(guild_id, {}).get(nom_event)
        if not objectifs:
            return await interaction.followup.send(f"<:error:1512505075220611172> Aucun objectif défini pour **{nom_event}** sur ce serveur. Utilise `/event_objectif_set` d'abord.")

        headers = {'accept': 'application/json', 'gge-server': 'E4K_FR1'}
        safe_alliance = urllib.parse.quote(alliance)
        
        search_url = f"https://api.gge-tracker.com/api/v1/alliances/name/{safe_alliance}"
        try:
            async with self.bot.session.get(search_url, headers=headers, timeout=10) as resp:
                if resp.status != 200: return await interaction.followup.send("<:error:1512505075220611172> Alliance introuvable via l'API.")
                data1 = await resp.json()
                target = data1[0] if isinstance(data1, list) and data1 else data1
                alliance_id = target.get('alliance_id') or target.get('id')
        except: return await interaction.followup.send("<:error:1512505075220611172> Erreur de connexion avec GGE-Tracker.")

        if not alliance_id: return await interaction.followup.send("<:error:1512505075220611172> Alliance introuvable.")

        stats_url = f"https://api.gge-tracker.com/api/v1/statistics/alliance/{alliance_id}"
        try:
            async with self.bot.session.get(stats_url, headers=headers, timeout=15) as resp:
                if resp.status != 200: return await interaction.followup.send("<:error:1512505075220611172> Impossible de télécharger les statistiques.")
                stats_data = await resp.json()
        except: return await interaction.followup.send("<:error:1512505075220611172> Erreur lors du téléchargement de l'historique.")

        best_history = []
        for key in event_keys:
            curr_history = stats_data.get("points", {}).get(key, [])
            if curr_history:
                best_history = curr_history
                break

        if not best_history:
            return await interaction.followup.send(f"<:error:1512505075220611172> Aucun point enregistré pour **{alliance}** sur **{nom_event}**.")

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
            return await interaction.followup.send(f"<:error:1512505075220611172> Aucun membre de cette alliance trouvé dans le scan local.")

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
        
        if not target_clusters: return await interaction.followup.send("<:error:1512505075220611172> Données insuffisantes pour l'analyse.")

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
        
        embed = discord.Embed(title=f"<:stats:1512517930490003726> Bilan d'Objectifs : {alliance} - {nom_event}", color=self.clr_bilan, timestamp=discord.utils.utcnow())
        embed.description = f"<:podium:1512523218299392131> **Mode :** {mode_txt}\n<:ranking:1512438311132729525> **Taux de réussite :** {taux:.1f}% ({reussites}/{total_evalues} membres)"

        for tier_key in ["T4", "T3", "T2", "T1"]:
            joueurs_tier = resultats_par_tier[tier_key]
            if not joueurs_tier: continue
            
            joueurs_tier.sort(key=lambda x: (-int(x["reussi"]), -x["score"])) 
            
            lignes = []
            for j in joueurs_tier:
                icone = "<:star:1512573195088171088>" if j["reussi"] else "<:movements:1512526112830521637>"
                lignes.append(f"{icone} **{j['name']}** : {format_num(j['score'])} / {format_num(j['requis'])}")
            
            tier_label = get_tier_and_label(0, 999 if tier_key=="T4" else 700 if tier_key=="T3" else 400 if tier_key=="T2" else 100)[1]
            
            chunk_txt = ""
            for ligne in lignes:
                if len(chunk_txt) + len(ligne) > 1000:
                    embed.add_field(name=f"<:alliance:1512503083861540914> Tranche {tier_label}", value=chunk_txt, inline=False)
                    chunk_txt = ligne + "\n"
                else:
                    chunk_txt += ligne + "\n"
            if chunk_txt:
                embed.add_field(name=f"<:alliance:1512503083861540914> Tranche {tier_label}", value=chunk_txt, inline=False)

        setup_embed_footer(embed, interaction)
        await interaction.followup.send(embed=embed)

    # =========================================
    # COMMANDE : EVENT JOUEUR (AIGUILLAGE AUTO)
    # ==========================================
    @app_commands.command(name="event_joueur", description="Consulter le dernier score d'un joueur ou son historique.")
    @app_commands.autocomplete(nom_event=event_autocomplete)
    @app_commands.autocomplete(joueur=joueur_autocomplete)
    @app_commands.choices(mode=[
        app_commands.Choice(name="Dernier score", value="dernier"),
        app_commands.Choice(name="Historique", value="cumul")
    ])
    async def event_joueur(self, interaction: discord.Interaction, nom_event: str, joueur: str, mode: app_commands.Choice[str]):
        await interaction.followup.defer(thinking=True) if interaction.response.is_done() else await interaction.response.defer(thinking=True)
        
        player_id = None
        alliance_name = "Sans alliance"
        cache = await get_cached_data()
        local_data = cache.get('players_data', {})

        for p_name, p_info in local_data.items():
            if p_name.lower() == joueur.lower():
                raw_id = str(p_info.get('player_id', p_info.get('id', '')))
                player_id = raw_id + '164' if raw_id and not raw_id.endswith('164') else raw_id
                joueur = p_name 
                alli_raw = p_info.get('allianceName', p_info.get('alliance_id', 'Sans alliance'))
                if isinstance(alli_raw, dict): alli_raw = alli_raw.get('name', 'Sans alliance')
                if alli_raw and alli_raw not in ["", "None"]: alliance_name = alli_raw
                break

        if not player_id:
            return await interaction.followup.send(f"<:error:1512505075220611172> Joueur **{joueur}** introuvable dans le cache local.")

        headers = {'accept': 'application/json', 'gge-server': 'E4K_FR1'}
        base_api = "https://api.gge-tracker.com/api/v1"

        if "îles orageuses" in nom_event.lower() or "aquamarine" in nom_event.lower():
            try:
                async with self.bot.session.get(f"{base_api}/aquamarine/player/{player_id}", headers=headers, timeout=10) as resp:
                    if resp.status != 200:
                        return await interaction.followup.send(f"<:error:1512505075220611172> Aucun historique Aquamarine trouvé pour **{joueur}**.")
                    snapshots = (await resp.json()).get("snapshots", [])
            except Exception as e:
                logger.error(f"🚨 ERREUR API Aquamarine : {str(e)}")
                return await interaction.followup.send(f"<:error:1512505075220611172> Erreur technique Aquamarine : {type(e).__name__}")
                
            if not snapshots:
                return await interaction.followup.send(f"<:error:1512505075220611172> **{joueur}** ne possède aucun snapshot enregistré pour l'Aquamarine.")
            
            snapshots.sort(key=lambda x: x.get('collected_at', ''), reverse=True)
            
            if alliance_name.isdigit() or (alliance_name.endswith('164') and alliance_name[:-3].isdigit()):
                target_aid = alliance_name if alliance_name.endswith('164') else alliance_name + '164'
                for _, info in local_data.items():
                    curr_aid = str(info.get('allianceId', info.get('alliance_id', '')))
                    if curr_aid and not curr_aid.endswith('164'): curr_aid += '164'
                    if curr_aid == target_aid:
                        a_name = info.get('allianceName', info.get('alliance', ''))
                        if isinstance(a_name, dict): a_name = a_name.get('name', '')
                        if a_name and not a_name.isdigit():
                            alliance_name = a_name
                            break

            if mode.value == "dernier":
                live = snapshots[0]
                live_metrics = {m['metric_id']: int(m['value']) for m in live.get('metrics', [])}
                
                pts_cargo = live_metrics.get(100, 0)
                total_am = live_metrics.get(15, 0)
                cargo_iles = live_metrics.get(16, 0)
                cargo_forts = live_metrics.get(17, 0)
                cargo_pvp_win = live_metrics.get(18, 0)
                cargo_depense = live_metrics.get(19, 0)
                cargo_pvp_loss = live_metrics.get(20, 0)

                pts_cargo_str = f"{pts_cargo:,}".replace(",", " ")
                total_am_str = f"{total_am:,}".replace(",", " ")
                cargo_iles_str = f"{cargo_iles:,}".replace(",", " ")
                cargo_forts_str = f"{cargo_forts:,}".replace(",", " ")
                cargo_pvp_win_str = f"{cargo_pvp_win:,}".replace(",", " ")
                cargo_depense_str = f"{cargo_depense:,}".replace(",", " ")
                cargo_pvp_loss_str = f"{cargo_pvp_loss:,}".replace(",", " ")

                status_txt = (
                    f"<:pointscargo:1512161268411273429> **Points cargo (Classement) :** {pts_cargo_str} pts\n\n"
                    f"<:aquamarinetotalcollectee:1512162700518752410> **Total aigue-marine collectées :** {total_am_str}\n"
                    f" ↳ <:aquamarineiles:1512162072249765908> *Dans les îles à ressources :* {cargo_iles_str}\n"
                    f" ↳ <:aquamarineforts:1512162154890133506> *Dans les forts orageux :* {cargo_forts_str}\n"
                    f" ↳ <:aquamarinegagnerjcj:1512162488504811590> *Gagné en JcJ :* {cargo_pvp_win_str}\n"
                    f" ↳ <:aquamarineperdujcj:1512162424365646067> *Perdu en JcJ :* {cargo_pvp_loss_str}\n\n"
                    f"<:aquamarinedepenser:1512162297425039423> **Dépensé en points cargo :** {cargo_depense_str}"
                )

                maintenant_str = discord.utils.utcnow().strftime("%Y-%m")
                snapshot_month = live.get('collected_at', '')[:7]
                
                mois_fr = {"01":"Janvier", "02":"Février", "03":"Mars", "04":"Avril", "05":"Mai", "06":"Juin", 
                           "07":"Juillet", "08":"Août", "09":"Septembre", "10":"Octobre", "11":"Novembre", "12":"Décembre"}
                
                title_txt = f"🎯 Score en direct de {joueur} for : {nom_event}"
                warning_desc = ""
                couleur_embed = self.clr_aqua
                
                if snapshot_month != maintenant_str:
                    try:
                        annee_s, mois_s = snapshot_month.split("-")
                        nom_mois_s = f"{mois_fr.get(mois_s, mois_s)} {annee_s}"
                        title_txt = f"⚓ Score Final Édition Précédente : {joueur}"
                        warning_desc = f"<:error:1512505075220611172> **Attention :** Ce joueur n'a pas encore lancé l'édition actuelle du mois de Juin. Affichage des archives de l'édition de **{nom_mois_s}**.\n\n"
                        couleur_embed = discord.Color.orange()
                    except: pass

                embed = discord.Embed(title=title_txt, color=couleur_embed, timestamp=discord.utils.utcnow())
                embed.add_field(name="<:players:1512504277392953426> Profil", value=f"**Joueur :** {joueur}\n**Alliance :** {alliance_name}", inline=True)
                embed.add_field(name="<:stats:1512517930490003726> État des Réserves Live\n\n", value=f"{warning_desc}{status_txt}", inline=False)
                
                latest_date = live.get('collected_at', '')
                if latest_date:
                    ts_r = get_discord_timestamp(latest_date, 'R')
                    ts_t = get_discord_timestamp(latest_date, 't')
                    embed.add_field(name="⏱️ Dernière Frappe", value=f"Relevée par l'API {ts_r} (*{ts_t}*)", inline=False)

                setup_embed_footer(embed, interaction)
                return await interaction.followup.send(embed=embed)

            elif mode.value == "cumul":
                from collections import defaultdict
                months_dict = defaultdict(list)
                
                for sn in snapshots:
                    try:
                        dt = datetime.fromisoformat(sn['collected_at'].replace('Z', '+00:00'))
                        month_key = dt.strftime("%Y-%m")
                        months_dict[month_key].append({
                            'dt': dt,
                            'collected_at': sn['collected_at'],
                            'metrics': {m['metric_id']: int(m['value']) for m in sn.get('metrics', [])}
                        })
                    except: continue

                if not months_dict:
                    return await interaction.followup.send("<:error:1512505075220611172> Impossible d'analyser l'historique mensuel.")

                sorted_months = sorted(months_dict.keys(), reverse=True)
                total_pages = len(sorted_months)
                
                grand_total_cargo = 0
                grand_total_am = 0
                monthly_finals = {}
                
                for m_key in sorted_months:
                    final_sn = max(months_dict[m_key], key=lambda x: x['dt'])
                    grand_total_cargo += final_sn['metrics'].get(100, 0)
                    grand_total_am += final_sn['metrics'].get(15, 0)
                    monthly_finals[m_key] = final_sn

                mois_fr = {"01":"Janvier", "02":"Février", "03":"Mars", "04":"Avril", "05":"Mai", "06":"Juin", 
                           "07":"Juillet", "08":"Août", "09":"Septembre", "10":"Octobre", "11":"Novembre", "12":"Décembre"}

                embeds = []
                for i, m_key in enumerate(sorted_months):
                    annee, mois_num = m_key.split("-")
                    nom_mois = f"{mois_fr.get(mois_num, mois_num)} {annee}"
                    
                    final_sn = monthly_finals[m_key]
                    m_metrics = final_sn['metrics']
                    
                    pts_f = m_metrics.get(100, 0)
                    am_f = m_metrics.get(15, 0)
                    iles_f = m_metrics.get(16, 0)
                    forts_f = m_metrics.get(17, 0)
                    pvp_w_f = m_metrics.get(18, 0)
                    dep_f = m_metrics.get(19, 0)
                    
                    gt_cargo_str = f"{grand_total_cargo:,}".replace(",", " ")
                    gt_am_str = f"{grand_total_am:,}".replace(",", " ")
                    pts_f_str = f"{pts_f:,}".replace(",", " ")
                    am_f_str = f"{am_f:,}".replace(",", " ")
                    iles_f_str = f"{iles_f:,}".replace(",", " ")
                    forts_f_str = f"{forts_f:,}".replace(",", " ")
                    pvp_w_f_str = f"{pvp_w_f:,}".replace(",", " ")
                    dep_f_str = f"{dep_f:,}".replace(",", " ")
                    
                    stats_txt = (
                        f"🏆 **Total Historique Cargo :** {gt_cargo_str} <:aquamarinedepenser:1512162297425039423>\n"
                        f"📈 **Total Historique Aigue-Marine :** {gt_am_str} <:aquamarinetotalcollectee:1512162700518752410>\n"
                        f"📅 **Score Final de l'édition :** {pts_f_str} <:pointscargo:1512161268411273429>"
                    )
                    
                    details_txt = (
                        f"<:aquamarinetotalcollectee:1512162700518752410> **Aigue-marine collectées :** {am_f_str}\n"
                        f" ↳ <:aquamarineiles:1512162072249765908> *Dans les îles à ressources :* {iles_f_str}\n"
                        f" ↳ <:aquamarineforts:1512162154890133506> *Dans les forts orageux :* {forts_f_str}\n"
                        f" ↳ <:aquamarinegagnerjcj:1512162488504811590> *Gagné en JcJ :* {pvp_w_f_str}\n\n"
                        f"<:aquamarinedepenser:1512162297425039423> **Dépensé en points cargo :** {dep_f_str}"
                    )

                    embed = discord.Embed(
                        title=f"<:stats:1512517930490003726> Historique Îles Orageuses de {joueur}",
                        description=f"**Alliance :** {alliance_name}\n\n**<:icon_world:1512517516012814537> Vue d'ensemble historique**\n\n{stats_txt}\n\n**📜 Archives de l'édition ({nom_mois})**\n{details_txt}",
                        color=self.clr_joueur_cumul,
                        timestamp=discord.utils.utcnow()
                    )
                    
                    try:
                        dt_releve = datetime.fromisoformat(final_sn['collected_at'].replace('Z', '+00:00'))
                        heure_releve = dt_releve.strftime("%d/%m/%Y à %H:%M")
                        setup_embed_footer(embed, interaction)
                    except:
                        setup_embed_footer(embed, interaction)
                        
                    embeds.append(embed)

                if len(embeds) == 1: 
                    await interaction.followup.send(embed=embeds[0])
                else:
                    view = PaginationView(embeds)
                    await interaction.followup.send(embed=embeds[0], view=view)
                return

        event_keys = TRACKER_EVENTS.get(nom_event)
        if not event_keys:
            return await interaction.followup.send(f"<:error:1512505075220611172> Événement inconnu ou non géré par l'API.")

        try:
            async with self.bot.session.get(f"{base_api}/statistics/player/{player_id}", headers=headers, timeout=10) as resp:
                if resp.status != 200:
                    return await interaction.followup.send(f"<:error:1512505075220611172> Erreur API GGE-Tracker pour {joueur}.")
                stats_data = await resp.json()
        except Exception as e:
            logger.error(f"<:error:1512505075220611172> ERREUR API event_joueur : {type(e).__name__} - {str(e)}")
            return await interaction.followup.send(f"<:error:1512505075220611172> Impossible de se connecter à l'API.")

        merged_history = []
        for key in event_keys:
            merged_history.extend(stats_data.get("points", {}).get(key, []))
        
        if not merged_history:
            return await interaction.followup.send(f"<:error:1512505075220611172> **{joueur}** n'a aucun point enregistré pour l'événement **{nom_event}**.")

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

        if mode.value == "cumul":
            nb_events = 30
            events_joues = len(sessions)
            avertissement = ""
            if events_joues < nb_events:
                avertissement = f"\n\n<:error:1512505075220611172> **Manque de données** : Cumul sur {nb_events} events, mais seulement **{events_joues}** ont été joués/enregistrés."
                nb_events = events_joues
                
            recent_sessions = sessions[-nb_events:] if nb_events > 0 else []
            if not recent_sessions:
                return await interaction.followup.send(f"<:error:1512505075220611172> Aucun historique exploitable pour calculer un cumul.")

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
                f"> <:podium:1512523218299392131> **TOTAL CUMULÉ : {format_num(total_score)} pts**\n"
                f"> <:ranking:1512438311132729525> **Moyenne/Event** : {format_num(moyenne)} pts\n"
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
                
                embed = discord.Embed(title=f"<:stats:1512517930490003726> Analyse & Historique de {joueur} pour : {nom_event}", color=self.clr_joueur_cumul, timestamp=discord.utils.utcnow())
                embed.description = f"<:alliance:1512503083861540914> **Alliance actuelle :** [{alliance_name}]{avertissement}"
                embed.add_field(name=f"<:stats:1512517930490003726> Bilan sur les {len(recent_sessions)} derniers events\n*(Période du {start_date_global} au {end_date_global})*", value=stats_txt, inline=False)
                embed.add_field(name=f"Détails des sessions (Page {page_actuelle}/{nb_pages})", value="\n".join(chunk), inline=False)
                setup_embed_footer(embed, interaction)
                embeds.append(embed)

            if len(embeds) == 1: await interaction.followup.send(embed=embeds[0])
            else:
                view = PaginationView(embeds)
                await interaction.followup.send(embed=embeds[0], view=view)

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
                return await interaction.followup.send(f"<:error:1512505075220611172> **{joueur}** est actuellement à 0 pt sur **{nom_event}**.")

            embed = discord.Embed(title=f"<:podium:1512523218299392131> Score en direct de {joueur} pour : {nom_event}", color=self.clr_joueur_dernier, timestamp=discord.utils.utcnow())
            embed.add_field(name="<:players:1512504277392953426> Profil", value=f"**Joueur :** {joueur}\n**Alliance :** [{alliance_name}]", inline=True)
            embed.add_field(name="<:icon_points:1512502439339888820> Score Actuel", value=f"**{format_num(latest_point)} pts**", inline=True)
            
            if latest_date:
                ts_r = get_discord_timestamp(latest_date, 'R')
                ts_t = get_discord_timestamp(latest_date, 't')
                embed.add_field(name="⏱️ Dernier relevé", value=f"Relevé par l'API {ts_r} (*{ts_t}*)", inline=False)
                
            setup_embed_footer(embed, interaction)
            await interaction.followup.send(embed=embed)

    # =========================================
    # COMMANDE : EVENT ALLIANCE
    # =========================================
    @app_commands.command(name="event_alliance", description="Classement et participation d'une alliance sur un événement")
    @app_commands.autocomplete(nom_event=event_autocomplete)
    @app_commands.autocomplete(alliance=alliance_autocomplete)
    @app_commands.choices(affichage=[
        app_commands.Choice(name="📑 Pages interactives (Boutons)", value="pages"),
        app_commands.Choice(name="📜 Liste complète (Message unique)", value="liste")
    ])
    async def event_alliance(self, interaction: discord.Interaction, nom_event: str, alliance: str, affichage: str = "liste"):
        await interaction.followup.defer(thinking=True) if interaction.response.is_done() else await interaction.response.defer(thinking=True)
        
        event_keys = TRACKER_EVENTS.get(nom_event)
        if not event_keys:
            return await interaction.followup.send(f"<:error:1512505075220611172> Événement inconnu.")

        # Appel au moteur universel
        embed, error_or_lignes, stats_text, global_latest_str = await generer_rapport_alliance_embed(self.bot, nom_event, event_keys, alliance, self.clr_alliance)
        
        if not embed:
            # S'il n'y a pas d'embed, c'est qu'il y a eu une erreur, stockée dans error_or_lignes
            return await interaction.followup.send(f"<:error:1512505075220611172> {error_or_lignes}")

        # Si l'utilisateur veut la liste brute
        if affichage == "liste":
            setup_embed_footer(embed, interaction)
            await interaction.followup.send(embed=embed)
        
        # Si l'utilisateur veut le menu interactif par boutons
        else:
            embeds = []
            lignes_classement = error_or_lignes
            chunk_size = 15
            nb_pages = max(1, (len(lignes_classement) - 1) // chunk_size + 1)
            for i in range(0, len(lignes_classement), chunk_size):
                chunk = lignes_classement[i:i+chunk_size]
                emb = discord.Embed(title=f"<:alliance:1512503083861540914> {alliance} - {nom_event}", color=self.clr_alliance, timestamp=discord.utils.utcnow())
                emb.add_field(name="<:stats:1512517930490003726> Statistiques", value=stats_text, inline=False)
                emb.add_field(name=f"<:ranking:1512438311132729525> Classement (Page {i//chunk_size+1}/{nb_pages})", value="\n".join(chunk), inline=False)
                
                if global_latest_str:
                    ts_r = get_discord_timestamp(global_latest_str, 'R')
                    ts_t = get_discord_timestamp(global_latest_str, 't')
                    emb.add_field(name="⏱️ Actualisation", value=f"Dernier relevé effectué {ts_r} (*{ts_t}*)", inline=False)
                    
                setup_embed_footer(emb, interaction)
                embeds.append(emb)
                
            view = PaginationView(embeds)
            await interaction.followup.send(embed=embeds[0], view=view)

    # ==========================================
    # 🔗 LIAISON DU COMPTE DISCORD
    # ==========================================
    @app_commands.command(name="set_pseudo", description="Lie ton compte Discord à ton pseudo GGE")
    @app_commands.autocomplete(pseudo=joueur_autocomplete)
    async def set_pseudo(self, interaction: discord.Interaction, pseudo: str):
        # 🔐 Sécurisé : Lecture/Écriture asynchrones protégées
        data = await load_pseudos_async()
        data[str(interaction.user.id)] = pseudo
        await save_pseudos_async(data)
        await interaction.response.send_message(f"<:players:1512504277392953426> Compte lié à **{pseudo}** !", ephemeral=True)

    # ==========================================
    # 🕵️ GROUPE /RIVAL (RADAR ÉVÉNEMENT)
    # ==========================================
    rival_group = app_commands.Group(name="rival", description="Radar de Compétition (MP uniquement)")

    @rival_group.command(name="start", description="Lance ton radar de compétition")
    @app_commands.autocomplete(nom_event=event_autocomplete)
    async def rival_start(self, interaction: discord.Interaction, nom_event: str, seuil: int = 90):
        if interaction.guild: return await interaction.response.send_message("<:error:1512505075220611172> À faire en **Message Privé**.", ephemeral=True)
        pseudos = await load_pseudos_async()
        if str(interaction.user.id) not in pseudos: return await interaction.response.send_message("<:error:1512505075220611172> Fais `/set_pseudo` d'abord.")
        
        # 🔐 Sécurisé : Modification asynchrone isolée
        data = await load_rivals_async()
        data[str(interaction.user.id)] = {"event": nom_event, "seuil": max(50, min(99, seuil)), "rivaux": [], "last_known_scores": {}, "started_at": discord.utils.utcnow().isoformat()}
        await save_rivals_async(data)
        await interaction.response.send_message(f"<:icon_analyze:1512573874150314005> Radar activé pour **{nom_event}** !")

    @rival_group.command(name="add", description="Ajoute des rivaux (Max 10)")
    async def rival_add(self, interaction: discord.Interaction, joueur1: str, joueur2: str = None, joueur3: str = None, joueur4: str = None, joueur5: str = None):
        if interaction.guild: return await interaction.response.send_message("<:error:1512505075220611172> En privé uniquement.", ephemeral=True)
        # 🔐 Sécurisé : Enregistrement avec verrous
        data = await load_rivals_async()
        uid = str(interaction.user.id)
        if uid not in data: return await interaction.response.send_message("<:error:1512505075220611172> Fais `/rival start` d'abord.")
        for j in [joueur1, joueur2, joueur3, joueur4, joueur5]:
            if j and j not in data[uid]["rivaux"] and len(data[uid]["rivaux"]) < 10: data[uid]["rivaux"].append(j)
        await save_rivals_async(data)
        await interaction.response.send_message("<:icon_search:1512505406474293438> Rivaux mis à jour !")

    @rival_group.command(name="list", description="Affiche tes rivaux")
    async def rival_list(self, interaction: discord.Interaction):
        if interaction.guild: return await interaction.response.send_message("<:error:1512505075220611172> En privé uniquement.", ephemeral=True)
        data = await load_rivals_async()
        if str(interaction.user.id) not in data: return await interaction.response.send_message("🕸️ Radar inactif.")
        config = data[str(interaction.user.id)]
        embed = discord.Embed(title=f"<:icon_name:1512505444172697611> Radar Actif : {config['event']}", color=self.clr_rival_list)
        embed.description = f"**Seuil :** {config['seuil']}%\n" + "\n".join([f"🔸 {r}" for r in config['rivaux']])
        setup_embed_footer(embed, interaction)
        await interaction.response.send_message(embed=embed)

    @rival_group.command(name="stop", description="Arrête le radar")
    async def rival_stop(self, interaction: discord.Interaction):
        data = await load_rivals_async()
        if str(interaction.user.id) in data:
            del data[str(interaction.user.id)]
            await save_rivals_async(data)
            await interaction.response.send_message("🛑 Radar désactivé.")

    @tasks.loop(minutes=1)
    async def rival_check_task(self):
        pass

    # ========================================================
    # GROUPE DE COMMANDES : ROUE DE LA FORTUNE (WOA)
    # ========================================================
    woa = app_commands.Group(name="woa", description="Analyses et statistiques de la Roue de la Fortune")

    @woa.command(name="historique", description="Consulte l'historique des tickets dépensés par un joueur")
    @app_commands.autocomplete(joueur=joueur_autocomplete) 
    async def woa_historique(self, interaction: discord.Interaction, joueur: str):
        try: await interaction.response.defer(thinking=True)
        except: return
        headers = {'accept': 'application/json', 'gge-server': 'E4K_FR1'}
        session = self.bot.session 
        base_api = "https://api.gge-tracker.com/api/v1"
        try:
            async with session.get(f"{base_api}/players/{urllib.parse.quote(joueur)}", headers=headers, timeout=8) as r:
                if r.status != 200: return await interaction.followup.send(f"<:error:1512505075220611172> Joueur **{joueur}** introuvable.")
                res_base = await r.json()
                if isinstance(res_base, list) and res_base: res_base = res_base[0]
                raw_id = str(res_base.get("player_id", res_base.get("id", "")))
                player_id = raw_id + '164' if raw_id and not raw_id.endswith('164') else raw_id
                vrai_nom = res_base.get("player_name", joueur)

            async with session.get(f"{base_api}/woa/events/player/{player_id}", headers=headers, timeout=8) as r:
                if r.status != 200: return await interaction.followup.send(f"<:error:1512505075220611172> Aucun historique WoA trouvé.")
                events = (await r.json()).get("events", [])

            if not events: return await interaction.followup.send(f"<:error:1512505075220611172> Aucune participation enregistrée.")

            from collections import defaultdict
            grand_total = sum(int(ev.get('point', 0)) for ev in events)
            months_data = defaultdict(list)
            for ev in events:
                try:
                    dt = datetime.fromisoformat(ev['date'].replace('Z', '+00:00'))
                    months_data[dt.strftime("%Y-%m")].append({'dt': dt, 'date_str': dt.strftime("%d/%m/%Y"), 'pts': int(ev.get('point', 0)), 'rank': str(ev.get('rank', '?'))})
                except: continue

            embeds = []
            sorted_months = sorted(months_data.keys(), reverse=True)
            mois_fr = {"01":"Janvier", "02":"Février", "03":"Mars", "04":"Avril", "05":"Mai", "06":"Juin", "07":"Juillet", "08":"Août", "09":"Septembre", "10":"Octobre", "11":"Novembre", "12":"Décembre"}

            for i, m_key in enumerate(sorted_months):
                month_events = months_data[m_key]
                annee, mois_num = m_key.split("-")
                month_total = sum(e['pts'] for e in month_events)
                best_ev = max(month_events, key=lambda x: x['pts'])
                
                stats_txt = f"🏆 **Total Historique :** {grand_total:,} <:woaticket:1512165398718583016>\n<:stats:1512517930490003726> **Total {mois_fr.get(mois_num, mois_num)} {annee} :** {month_total:,} <:woaticket:1512165398718583016>\n🚀 **Jour max :** {best_ev['pts']:,} <:woaticket:1512165398718583016> *(le {best_ev['dt'].strftime('%d/%m')})*".replace(',', ' ')
                
                lignes = []
                for ev in month_events:
                    pts_str = f"{ev['pts']:,}".replace(",", " ")
                    rank = ev['rank']
                    medal = "🥇" if rank == "1" else "🥈" if rank == "2" else "🥉" if rank == "3" else f"**#{rank}**"
                    lignes.append(f"• **{ev['date_str']}** │ Rang {medal} ➔ **{pts_str} <:woaticket:1512165398718583016>**")

                embed = discord.Embed(title=f"<:woaicon:1512165794740572292> Historique Roue de la Fortune : {vrai_nom}", description=f"**<:stats:1512517930490003726> Vue d'ensemble**\n{stats_txt}\n\n**📜 Détails ({mois_fr.get(mois_num, mois_num)})**\n" + "\n".join(lignes), color=self.clr_woa_historique, timestamp=discord.utils.utcnow())
                setup_embed_footer(embed, interaction)
                embeds.append(embed)

            view = PaginationView(embeds)
            await interaction.followup.send(embed=embeds[0], view=view)
        except Exception as e: await interaction.followup.send(f"<:error:1512505075220611172> Erreur technique : {e}")

    @woa.command(name="bilan", description="Affiche le bilan de consommation des tickets")
    async def woa_bilan(self, interaction: discord.Interaction):
        try: await interaction.response.defer(thinking=True)
        except: return
        headers = {'accept': 'application/json', 'gge-server': 'E4K_FR1'}
        session = self.bot.session
        base_api = "https://api.gge-tracker.com/api/v1"
        try:
            async with session.get(f"{base_api}/woa/events?page=1", headers=headers, timeout=8) as r:
                data = await r.json()
                all_events = data.get("events", [])
                total_pages = data.get("pagination", {}).get("total_pages", 1)
            if total_pages > 1:
                tasks_list = [session.get(f"{base_api}/woa/events?page={p}", headers=headers, timeout=8) for p in range(2, total_pages + 1)]
                responses = await asyncio.gather(*tasks_list, return_exceptions=True)
                for resp in responses:
                    if isinstance(resp, aiohttp.ClientResponse) and resp.status == 200: all_events.extend((await resp.json()).get("events", []))
            all_events.sort(key=lambda x: x.get("date", ""), reverse=True)
            
            t_editions = len(all_events)
            t_tickets = sum(int(ev.get('total_tickets', 0)) for ev in all_events)
            t_parts = sum(int(ev.get('participants', 0)) for ev in all_events)
            
            moy_tickets = t_tickets // t_editions if t_editions > 0 else 0
            moy_parts = t_parts // t_editions if t_editions > 0 else 0
            
            stats_globales = f"<:stats:1512517930490003726> **Éditions :** {t_editions}\n<:woaticket:1512165398718583016> **Tickets :** {t_tickets:,}\n<:Le_Hraut_Lumbricus_2:1512573890298380388> **Participants :** {t_parts:,}\n⚖️ **Moyenne :** {moy_tickets:,} <:woaticket:1512165398718583016> / {moy_parts:,} <:Le_Hraut_Lumbricus_2:1512573890298380388>".replace(",", " ")
            
            lignes, j_vus = [], set()
            for ev in all_events:
                try: d_str = datetime.fromisoformat(ev['date'].replace('Z', '+00:00')).strftime("%d/%m/%Y")
                except: continue
                if len(j_vus) >= 31 and d_str not in j_vus: break
                j_vus.add(d_str)
                parts = f"{int(ev.get('participants', 0)):,}".replace(",", " ")
                tix = f"{int(ev.get('total_tickets', 0)):,}".replace(",", " ")
                lignes.append(f"📅 **{d_str}** │ <:players:1512504277392953426> {parts} │ <:woaticket:1512165398718583016> **{tix}**")
            
            embeds = []
            for i in range(0, len(lignes), 15):
                embed = discord.Embed(title="<:woaicon:1512165794740572292> Bilan Économique : Roue d'Abondance", description=f"**<:icon_world:1512517516012814537> Statistiques Globales**\n{stats_globales}\n\n**📜 Détail des 31 dernières éditions**\n" + "\n".join(lignes[i:i+15]), color=self.clr_woa_bilan, timestamp=discord.utils.utcnow())
                setup_embed_footer(embed, interaction)
                embeds.append(embed)
            view = PaginationView(embeds)
            await interaction.followup.send(embed=embeds[0], view=view)
        except Exception as e: await interaction.followup.send(f"<:error:1512505075220611172> Erreur : {e}")

    # ========================================================
    # 🏆 GROUPE DE COMMANDES RACINE : CLASSEMENT (TOP 100 + HEURE)
    # ========================================================
    classement = app_commands.Group(name="classement", description="Classements généraux du serveur FR1")

    @classement.command(name="woa", description="Affiche le Top 100 de la dernière Roue de la Fortune")
    async def classement_woa(self, interaction: discord.Interaction):
        try: await interaction.response.defer(thinking=True)
        except: return
        headers = {'accept': 'application/json', 'gge-server': 'E4K_FR1'}
        session = self.bot.session
        base_api = "https://api.gge-tracker.com/api/v1"
        try:
            async with session.get(f"{base_api}/woa/events", headers=headers, timeout=8) as r:
                if r.status != 200: return await interaction.followup.send("<:error:1512505075220611172> API indisponible.")
                latest_date_str = (await r.json())["events"][0]["date"]
                encoded_date = urllib.parse.quote(latest_date_str)

            async with session.get(f"{base_api}/woa/events/date/{encoded_date}?page=1", headers=headers, timeout=8) as r:
                data_rank = await r.json()
                all_players = data_rank.get("players", [])
                total_pages = data_rank.get("pagination", {}).get("total_pages", 1)

            pages_to_fetch = min(total_pages, 7)
            if pages_to_fetch > 1:
                fetch_tasks = [
                    session.get(f"{base_api}/woa/events/date/{encoded_date}?page={p}", headers=headers, timeout=8)
                    for p in range(2, pages_to_fetch + 1)
                ]
                responses = await asyncio.gather(*fetch_tasks, return_exceptions=True)
                for resp in responses:
                    if isinstance(resp, aiohttp.ClientResponse) and resp.status == 200:
                        all_players.extend((await resp.json()).get("players", []))

            if not all_players: return await interaction.followup.send("<:error:1512505075220611172> Aucun joueur trouvé.")

            all_players = all_players[:100]

            lignes = []
            for idx, p in enumerate(all_players):
                rang = idx + 1
                nom = p.get("player_name", "???")
                pts = f"{int(p.get('point', 0)):,}".replace(",", " ")
                alli = p.get("alliance_name") or "Sans alliance"
                medal = "🥇" if rang == 1 else "🥈" if rang == 2 else "🥉" if rang == 3 else f"**{rang}.**"
                lignes.append(f"{medal} **{nom}** [{alli}] ➔ **{pts} <:woaticket:1512165398718583016>**")

            heure_releve_txt = ""
            try:
                dt_releve = datetime.fromisoformat(latest_date_str.replace('Z', '+00:00'))
                heure_releve_txt = f" • Relevé : {dt_releve.strftime('%d/%m/%Y à %H:%M')}"
            except: pass

            embeds = []
            nb_pages_discord = max(1, (len(lignes) - 1) // 10 + 1)
            for i in range(0, len(lignes), 10):
                embed = discord.Embed(title="Top 100 Serveur - Roue de la Fortune", color=self.clr_woa_classement, timestamp=discord.utils.utcnow())
                embed.add_field(name="Classement global", value="\n".join(lignes[i:i+10]), inline=False)
                setup_embed_footer(embed, interaction)
                embeds.append(embed)

            view = PaginationView(embeds)
            await interaction.followup.send(embed=embeds[0], view=view)
        except Exception as e: await interaction.followup.send(f"<:error:1512505075220611172> Erreur : {e}")

    @classement.command(name="iles_orageuses", description="Affiche le Top 100 des pilleurs d'Aquamarine")
    async def classement_iles(self, interaction: discord.Interaction):
        try: await interaction.response.defer(thinking=True)
        except: return
        headers = {'accept': 'application/json', 'gge-server': 'E4K_FR1'}
        session = self.bot.session
        base_api = "https://api.gge-tracker.com/api/v1"
        try:
            async with session.get(f"{base_api}/aquamarine?page=1&order_by=100&order_dir=DESC", headers=headers, timeout=8) as r:
                if r.status != 200: return await interaction.followup.send("<:error:1512505075220611172> API indisponible.")
                data_rank = await r.json()
                players = data_rank.get("players", [])
                total_pages = data_rank.get("pagination", {}).get("total_pages", 1)
                total_items = data_rank.get("pagination", {}).get("total_items_count", "?")

            pages_to_fetch = min(total_pages, 15)
            if pages_to_fetch > 1:
                fetch_tasks = [
                    session.get(f"{base_api}/aquamarine?page={p}&order_by=100&order_dir=DESC", headers=headers, timeout=8)
                    for p in range(2, pages_to_fetch + 1)
                ]
                responses = await asyncio.gather(*fetch_tasks, return_exceptions=True)
                for resp in responses:
                    if isinstance(resp, aiohttp.ClientResponse) and resp.status == 200:
                        players.extend((await resp.json()).get("players", []))

            if not players: return await interaction.followup.send("<:error:1512505075220611172> Aucun joueur trouvé.")

            mois_actif = ""
            for p in players:
                date_p = p.get("last_collected_at", "")
                if date_p and date_p[:7] > mois_actif:
                    mois_actif = date_p[:7]

            players_filtres = []
            for p in players:
                date_p = p.get("last_collected_at", "")
                if date_p and date_p.startswith(mois_actif):
                    players_filtres.append(p)

            players = players_filtres[:100]

            if not players:
                return await interaction.followup.send("<:error:1512505075220611172> Aucun joueur n'a encore débuté l'édition de ce mois-ci.")

            heure_releve_txt = ""
            if players and players[0].get("last_collected_at"):
                try:
                    dt_releve = datetime.fromisoformat(players[0]["last_collected_at"].replace('Z', '+00:00'))
                    heure_releve_txt = f" • Relevé : {dt_releve.strftime('%d/%m/%Y à %H:%M')}"
                except: pass

            lignes = []
            for r_idx, p in enumerate(players):
                rang = r_idx + 1
                nom = p.get("player_name", "???")
                metrics = p.get("metrics", {})
                pts = f"{int(metrics.get('100', 0)):,}".replace(",", " ")
                medal = "🥇" if rang == 1 else "🥈" if rang == 2 else "🥉" if rang == 3 else f"**{rang}.**"
                lignes.append(f"{medal} **{nom}** ➔ **{pts} <:pointscargo:1512161268411273429>**")

            embeds = []
            nb_pages_discord = max(1, (len(lignes) - 1) // 10 + 1)
            
            mois_fr = {"01":"Janvier", "02":"Février", "03":"Mars", "04":"Avril", "05":"Mai", "06":"Juin", 
                       "07":"Juillet", "08":"Août", "09":"Septembre", "10":"Octobre", "11":"Novembre", "12":"Décembre"}
            annee_actuelle, mois_num_actuel = mois_actif.split("-")
            nom_mois_actif = f"{mois_fr.get(mois_num_actuel, mois_num_actuel)} {annee_actuelle}"

            for i in range(0, len(lignes), 10):
                embed = discord.Embed(
                    title=f"Top 100 Serveur - Îles Orageuses ({nom_mois_actif})", 
                    description=f"Classement basé sur les points cargo de l'édition en cours.\n*Total recensé sur le serveur : {total_items} joueurs*", 
                    color=self.clr_aqua, 
                    timestamp=discord.utils.utcnow()
                )
                embed.add_field(name="Classement global", value="\n".join(lignes[i:i+10]), inline=False)
                setup_embed_footer(embed, interaction)
                embeds.append(embed)

            view = PaginationView(embeds)
            await interaction.followup.send(embed=embeds[0], view=view)
        except Exception as e: await interaction.followup.send(f"<:error:1512505075220611172> Erreur technique : {e}")

# 🔌 Branchement du Cog
async def setup(bot: commands.Bot):
    await bot.add_cog(EventsCog(bot))
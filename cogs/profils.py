# -*- coding: utf-8 -*-
import os
import json
import asyncio
import traceback
import urllib.parse
from datetime import datetime, timedelta
import discord
from discord import app_commands
from discord.ext import commands
import aiohttp
import logging

# 🛠️ On importe nos outils depuis utils.py
from utils import (
    BASE_DATA_PATH, 
    joueur_autocomplete, 
    alliance_autocomplete, 
    format_num, 
    get_discord_timestamp, 
    BOT_VERSION,
    PaginationView
)

logger = logging.getLogger("GGE_Bot")

class ProfilsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.bdd_chemin = "/app/data/bdd_items_gge.json"
        self.serveur_par_defaut = "E4K_FR1"

    # =========================
    # COMMANDE : JOUEUR
    # =========================
    @app_commands.command(name="joueur", description="Profil détaillé d'un joueur")
    @app_commands.autocomplete(nom=joueur_autocomplete)
    async def joueur(self, interaction: discord.Interaction, nom: str):
        try: await interaction.response.defer(thinking=True)
        except: return
        
        logger.info(f"👤 [Joueur] Consultation par {interaction.user.name} pour le joueur : {nom}")
        
        try:
            process = await asyncio.create_subprocess_exec(
                "python3", "scanners/player_scanner.py", nom,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            try:
                stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=45.0)
                result_stdout = stdout.decode('utf-8')
            except asyncio.TimeoutError:
                process.kill()
                await interaction.followup.send("⏳ Le serveur local a mis trop de temps (>45s).")
                return
                
            if "JSON_FILE:" in result_stdout:
                raw_path = result_stdout.split("JSON_FILE:")[1].strip()
                path = raw_path.replace('/volume1/gge-assistant', '/app')
                
                if os.path.exists(path):
                    with open(path, 'r', encoding='utf-8') as f:
                        full_json = json.load(f)
                        data = full_json.get('parsed_data', {})
                    
                    if not data:
                        await interaction.followup.send("⚠️ Données vides.")
                        return

                    main_pts = int(data.get('main_points', 0))
                    honor_pts = int(data.get('honor', 0))

                    alliance_info = data.get('alliance', {})
                    rank_id = alliance_info.get('rank')
                    ranks_map = {0: "Chef", 1: "Représentant", 2: "Maréchal", 3: "Trésorier", 4: "Diplomate", 5: "Recruteur", 6: "Général", 7: "Sergent", 8: "Membre", 9: "Novice"}
                    
                    role_txt = f" ({ranks_map[rank_id]})" if rank_id in ranks_map else (f" (Grade {rank_id})" if rank_id is not None else "")
                    alliance_name = alliance_info.get('name', 'Sans alliance')
                    alliance_display = f"**{alliance_name}**{role_txt}" if alliance_name != 'Sans alliance' else "**Sans alliance**"

                    outposts = data.get('outposts', [])
                    type_emojis = {1: "🏰", 3: "👑", 4: "⛺", 10: "🌾", 12: "🏯", 22: "💰", 23: "🗼", 24: "🏝️", 26: "🏛️"}
                    sort_priority = {1: 0, 4: 1, 12: 2, 3: 3, 22: 4, 23: 5, 24: 7, 26: 6}
                    
                    if outposts:
                        outposts.sort(key=lambda x: (sort_priority.get(int(x.get('type', 99)), 99), x.get('world_id', 0)))

                    embed = discord.Embed(title=f"👑 Profil de {data.get('name', nom)}", color=discord.Color.dark_blue(), timestamp=discord.utils.utcnow())
                    embed.add_field(name="📊 Informations", value=f"**Niveau** : {data.get('level', 0)} (Lég. {data.get('legendary_level', 0)})\n**ID** : `{data.get('player_id', 'Inconnu')}`", inline=True)
                    embed.add_field(name="⚔️ Classement", value=f"💪 **Puiss.** : {main_pts:,}\n🏅 **Honneur** : {honor_pts:,}", inline=True)
                    embed.add_field(name="🛡️ Alliance", value=alliance_display, inline=False)
                    
                    if outposts:
                        coords_txt = ""
                        for op in outposts[:10]:
                            emoji = type_emojis.get(int(op.get('type', 99)), "📌")
                            coords_txt += f"{emoji} **{op['type_label']}** ({op['world_label']}) ➔ `{op['coords_x']}:{op['coords_y']}`\n"
                        if len(outposts) > 10: coords_txt += f"*... et {len(outposts) - 10} autres positions.*\n"
                        embed.add_field(name=f"📍 Positions ({len(outposts)})", value=coords_txt[:1024], inline=False)

                    vassals = data.get('vassal_villages', [])
                    if vassals:
                        v_glace = len([v for v in vassals if v.get('world_id') == 2])
                        v_sable = len([v for v in vassals if v.get('world_id') == 1])
                        v_pic = len([v for v in vassals if v.get('world_id') == 3])
                        v_orage = len([v for v in vassals if v.get('world_id') == 4])
                        v_emp = len([v for v in vassals if v.get('world_id') == 0])
                        v_txt = f"❄️ Glace: **{v_glace}** | 🏜️ Sables: **{v_sable}** | 🌋 Pics: **{v_pic}** | 🌍 Empires: **{v_emp}** | 🌩️ Orages: **{v_orage}**"
                        v_coords = "\n".join([f"🌾 VR ({v['world_label']}) ➔ `{v['coords_x']}:{v['coords_y']}`" for v in vassals[:5]])
                        embed.add_field(name=f"🌾 Villages à Ressources ({len(vassals)})", value=f"{v_txt}\n\n{v_coords}"[:1024], inline=False)
                    
                    collected = full_json.get('collected_at')
                    if collected:
                        ts_r = get_discord_timestamp(collected, 'R')
                        ts_t = get_discord_timestamp(collected, 't')
                        embed.add_field(name="⏱️ Source des données", value=f"Relevé API en direct {ts_r} (*{ts_t}*)", inline=False)

                    embed.set_footer(text=BOT_VERSION)
                    await interaction.followup.send(embed=embed)
                else:
                    await interaction.followup.send(f"❌ Fichier introuvable.")
            else:
                await interaction.followup.send(f"❌ Joueur **{nom}** introuvable.")

        except Exception as e:
            logger.error(f"[Profils - Joueur] Erreur fatale : {traceback.format_exc()}")
            try: await interaction.followup.send(f"⚠️ Erreur système interne.")
            except: pass

    # =========================
    # COMMANDE ALLIANCE
    # =========================
    @app_commands.command(name="alliance", description="Profil détaillé d'une alliance (Rapide & Paginé)")
    @app_commands.autocomplete(nom=alliance_autocomplete)
    async def alliance(self, interaction: discord.Interaction, nom: str):
        try: await interaction.response.defer(thinking=True)
        except: return
        
        logger.info(f"🛡️ [Alliance] Consultation par {interaction.user.name} pour : {nom}")
        
        try:
            is_live = False
            target_alliance_id = None
            alliance_name = nom
            total_might = total_fame = total_honor = 0
            leader_name = "Inconnu"
            members = []
            
            # --- PLAN A : L'API EN DIRECT (Le TGV 🚅) ---
            try:
                # 💡 Astuce de génie : On réutilise ton scanner d'alliance qui est déjà parfait !
                from scanners.alliance_scanner import AllianceDetailsCollector
                collector = AllianceDetailsCollector()
                api_data = await collector.get_alliance_full_data(nom)
                
                if api_data and 'parsed_data' in api_data:
                    is_live = True
                    parsed = api_data['parsed_data']
                    target_alliance_id = parsed.get('alliance_id')
                    alliance_name = parsed.get('name', nom)
                    leader_name = parsed.get('leader', 'Inconnu')
                    total_might = parsed.get('total_might', 0)
                    total_honor = parsed.get('total_honor', 0)
                    total_fame = parsed.get('total_fame', 0)
                    members = parsed.get('members', [])
            except Exception as e:
                logger.warning(f"[Profils - Alliance] API inaccessible, passage au Plan B... ({e})")

            # --- PLAN B : LE CACHE LOCAL (Mode Survie 🛡️) ---
            local_date = None
            if not is_live:
                player_files = list((BASE_DATA_PATH / 'server_scans').rglob('server_*.json'))
                local_data = {}
                
                if player_files:
                    latest = max(player_files, key=lambda p: p.stat().st_mtime)
                    with open(latest, 'r', encoding='utf-8') as f:
                        full_json = json.load(f)
                        local_data = full_json.get('players', {})
                        local_date = full_json.get('collected_at')

                # 1. On cherche l'ID de l'alliance
                for p_info in local_data.values():
                    a_obj = p_info.get('alliance')
                    a_name = a_obj.get('name') if isinstance(a_obj, dict) else (p_info.get('alliance_name') or a_obj)
                    
                    if a_name and str(a_name).lower() == nom.lower():
                        aid = p_info.get('allianceId') or p_info.get('alliance_id')
                        if not aid and isinstance(a_obj, dict):
                            aid = a_obj.get('allianceId') or a_obj.get('alliance_id')
                        
                        if aid:
                            target_alliance_id = str(aid)
                            alliance_name = str(a_name)
                            break
                            
                if not target_alliance_id:
                    return await interaction.followup.send(f"❌ Alliance **{nom}** introuvable (ni sur l'API en direct, ni dans le cache local).")

                # 2. On reconstruit l'alliance joueur par joueur (Blindage anti-crash)
                for p_info in local_data.values():
                    a_obj = p_info.get('alliance')
                    aid = p_info.get('allianceId') or p_info.get('alliance_id')
                    if not aid and isinstance(a_obj, dict):
                        aid = a_obj.get('allianceId') or a_obj.get('alliance_id')
                        
                    if str(aid) == target_alliance_id:
                        m_rank = int(p_info.get('alliance_rank', 9))
                        # Protection si la valeur "might" est vide (None)
                        m_might = int(p_info.get('main_points') or p_info.get('might_current') or 0)
                        m_fame = int(p_info.get('fame') or 0)
                        m_honor = int(p_info.get('honor') or 0)
                        
                        total_might += m_might
                        total_fame += m_fame
                        total_honor += m_honor
                        
                        m_name = p_info.get('name', 'Inconnu')
                        if str(m_rank) in ["0", "1"] and (leader_name == "Inconnu" or str(m_rank) == "0"):
                            leader_name = m_name
                            
                        members.append({
                            "name": m_name,
                            "level": p_info.get('level', 0),
                            "leg_level": p_info.get('legendary_level', 0),
                            "might": m_might,
                            "fame": m_fame,
                            "honor": m_honor,
                            "rank": m_rank
                        })

                # 3. Tri des membres par puissance
                members.sort(key=lambda x: (int(x.get('rank', 9)), -x.get('might', 0)))

            if not members:
                return await interaction.followup.send(f"⚠️ L'alliance **{alliance_name}** semble être une ville fantôme (0 membre).")

            # --- AFFICHAGE PAGINÉ ---
            embeds = []
            rank_emojis = {0: "👑", 1: "⭐", 2: "⚔️", 3: "💰", 4: "📜", 5: "📯", 6: "🛡️", 7: "🎖️", 8: "👤", 9: "🆕"}
            chunk_size = 15
            nb_pages = max(1, (len(members) - 1) // chunk_size + 1)

            footer_txt = "🟢 Données relevée via API à" if is_live else "🟠 Mode Survie : Données reconstituées du cache"

            for i in range(0, len(members), chunk_size):
                chunk = members[i:i+chunk_size]
                page_actuelle = (i // chunk_size) + 1
                
                embed = discord.Embed(title=f"🛡️ Alliance : {alliance_name}", color=discord.Color.dark_green(), timestamp=discord.utils.utcnow())
                
                embed.add_field(name="📊 Informations", value=f"**Chef** : 👑 {leader_name}\n**Membres** : 👥 {len(members)} / 65\n**ID** : `{target_alliance_id}`", inline=True)
                embed.add_field(name="⚔️ Statistiques Globales", value=f"💪 **Puiss.** : {format_num(total_might)}\n🏆 **Gloire** : {format_num(total_fame)}\n🏅 **Honneur** : {format_num(total_honor)}", inline=True)
                
                memb_txt = ""
                for m in chunk:
                    lvl = m.get('level', 0)
                    leg = m.get('leg_level', m.get('leg', 0))
                    emoji = rank_emojis.get(int(m.get('rank', 9)), "👤")
                    memb_txt += f"{emoji} **{m.get('name', 'Inconnu')}** ({lvl}/{leg}) ➔ {format_num(m.get('might', 0))} | {format_num(m.get('fame', 0))}\n"
                
                embed.add_field(name=f"👥 Membres (Page {page_actuelle}/{nb_pages})     *PP | Gloire*", value=memb_txt, inline=False)
                
                if not is_live and local_date:
                    ts_r = get_discord_timestamp(local_date, 'R')
                    embed.add_field(name="⏱️ Base de données (Plan B)", value=f"Cache local datant de {ts_r}", inline=False)

                embed.set_footer(text=f"{BOT_VERSION} | {footer_txt}")
                embeds.append(embed)

            if len(embeds) == 1:
                await interaction.followup.send(embed=embeds[0])
            else:
                view = PaginationView(embeds)
                await interaction.followup.send(embed=embeds[0], view=view)

        except Exception as e:
            logger.error(f"[Profils - Alliance] Erreur fatale : {traceback.format_exc()}")
            try: await interaction.followup.send(f"⚠️ Erreur système interne lors du chargement de l'alliance.")
            except: pass

    # =========================
    # COMMANDE : HISTORIQUE
    # =========================
    @app_commands.command(name="historique", description="Affiche l'historique d'un joueur (Pseudo, Alliance, Déménagements)")
    @app_commands.choices(choix=[
        app_commands.Choice(name="👤 Changements de Pseudo", value="pseudo"),
        app_commands.Choice(name="🛡️ Changements d'Alliance", value="alliance"),
        app_commands.Choice(name="📍 Déménagements (Positions)", value="position"),
        app_commands.Choice(name="📜 Tout afficher", value="tout")
    ])
    @app_commands.autocomplete(joueur=joueur_autocomplete)
    async def historique(self, interaction: discord.Interaction, choix: app_commands.Choice[str], joueur: str):
        try: await interaction.response.defer(thinking=True)
        except: return
        
        logger.info(f"📜 [Historique] Consultation par {interaction.user.name} (Type: {choix.name}, Joueur: {joueur})")

        p_id = None
        if choix.value in ["pseudo", "alliance", "tout"]:
            try:
                process = await asyncio.create_subprocess_exec(
                    "python3", "scanners/player_scanner.py", joueur,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                stdout, _ = await asyncio.wait_for(process.communicate(), timeout=20.0)
                result_stdout = stdout.decode('utf-8')
                
                if "JSON_FILE:" in result_stdout:
                    raw_path = result_stdout.split("JSON_FILE:")[1].strip()
                    path = raw_path.replace('/volume1/gge-assistant', '/app')
                    if os.path.exists(path):
                        with open(path, 'r', encoding='utf-8') as f:
                            data = json.load(f).get('parsed_data', {})
                            p_id = str(data.get('player_id', ''))
                            if p_id and not p_id.endswith('164'):
                                p_id += '164'
            except Exception: pass

            if not p_id:
                await interaction.followup.send(f"❌ Impossible de trouver l'ID interne de **{joueur}** dans la base locale.")
                return

        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36',
            'accept': 'application/json',
            'gge-server': 'E4K_FR1'
        }

        urls_to_fetch = {}
        if choix.value in ["pseudo", "tout"]: urls_to_fetch["pseudo"] = f"https://api.gge-tracker.com/api/v1/updates/players/{p_id}/names"
        if choix.value in ["alliance", "tout"]: urls_to_fetch["alliance"] = f"https://api.gge-tracker.com/api/v1/updates/players/{p_id}/alliances"
        if choix.value in ["position", "tout"]: urls_to_fetch["position"] = f"https://api.gge-tracker.com/api/v1/server/movements?page=1&castleType=1&movementType=3&search={urllib.parse.quote(joueur)}&searchType=player"

        results = {}
        async with aiohttp.ClientSession() as session:
            async def fetch_url(name, url):
                try:
                    async with session.get(url, headers=headers, timeout=10) as resp:
                        if resp.status == 200:
                            results[name] = await resp.json()
                except Exception: pass

            tasks = [fetch_url(name, url) for name, url in urls_to_fetch.items()]
            await asyncio.gather(*tasks)

        def parse_date(iso_str):
            if not iso_str: return "Date inconnue"
            return get_discord_timestamp(iso_str, 'd')

        embed = discord.Embed(title=f"{choix.name} - {joueur}", color=discord.Color.dark_teal(), timestamp=discord.utils.utcnow())

        def process_lines(cat_name, emoji, key_old, key_new, list_data, limit):
            lines = []
            for item in list_data:
                d = parse_date(item.get('date', item.get('created_at')))
                if cat_name == "position":
                    x_old, y_old = item.get('position_x_old'), item.get('position_y_old')
                    x_new, y_new = item.get('position_x_new'), item.get('position_y_new')
                    lines.append(f"📍 {d} : `{x_old}:{y_old}` ➔ `{x_new}:{y_new}`")
                elif cat_name == "pseudo":
                    old = item.get(key_old) or "*Inconnu*"
                    new = item.get(key_new) or "*Inconnu*"
                    lines.append(f"📅 {d} : ~~{old}~~ ➔ **{new}**")
                elif cat_name == "alliance":
                    old = item.get(key_old) or "*Sans alliance*"
                    new = item.get(key_new) or "*Sans alliance*"
                    lines.append(f"🛡️ {d} : *{old}* ➔ **{new}**")
            
            if not lines:
                if choix.value == "tout": embed.add_field(name=f"{emoji} {cat_name.capitalize()}", value="📭 Aucun historique", inline=False)
                return

            if limit > 0 and len(lines) > limit:
                lines = lines[:limit]
                lines.append("*(... et d'autres plus anciens)*")
                embed.add_field(name=f"{emoji} Derniers {cat_name.capitalize()}s", value="\n".join(lines), inline=False)
            else:
                chunk_size = 15
                for i in range(0, len(lines), chunk_size):
                    chunk = lines[i:i+chunk_size]
                    title_supp = f" {i+1} à {min(i+chunk_size, len(lines))}" if len(lines) > 15 else ""
                    embed.add_field(name=f"{emoji} {cat_name.capitalize()}s{title_supp}", value="\n".join(chunk), inline=False)

        max_lines = 8 if choix.value == "tout" else 0 

        if "pseudo" in results: process_lines("pseudo", "👤", "old_player_name", "new_player_name", results["pseudo"].get("updates", []), max_lines)
        if "alliance" in results: process_lines("alliance", "🛡️", "old_alliance_name", "new_alliance_name", results["alliance"].get("updates", []), max_lines)
        if "position" in results: process_lines("position", "📍", "", "", results["position"].get("movements", []), max_lines)

        if not embed.fields:
            embed.description = "📭 Aucun historique trouvé pour ce joueur."

        embed.set_footer(text=BOT_VERSION)
        await interaction.followup.send(embed=embed)

    # ==========================================
    # 📈 COMMANDE : HISTORIQUE ALLIANCE PP
    # ==========================================

    @app_commands.command(name="alliance_pp", description="📈 Historique de la Puissance (PP) d'une alliance sur X jours")
    @app_commands.autocomplete(alliance=alliance_autocomplete)
    @app_commands.describe(jours="Période à analyser en jours (Par défaut: 3, Maximum: 10)")
    async def alliance_pp(self, interaction: discord.Interaction, alliance: str, jours: int = 3):
        try: await interaction.response.defer(thinking=True)
        except: return
        
        logger.info(f"📈 [Alliance PP] Historique demandé par {interaction.user.name} (Alliance: {alliance}, Jours: {jours})")

        # 1. Sécurité sur le nombre de jours
        jours = max(1, min(10, jours))
        date_limite = discord.utils.utcnow() - timedelta(days=jours)

        # 2. Récupération de l'ID de l'alliance (API)
        headers = {'accept': 'application/json', 'gge-server': 'E4K_FR1'}
        safe_alliance = urllib.parse.quote(alliance)
        
        async with aiohttp.ClientSession() as session:
            try:
                search_url = f"https://api.gge-tracker.com/api/v1/alliances/name/{safe_alliance}"
                async with session.get(search_url, headers=headers, timeout=10) as resp:
                    if resp.status != 200: return await interaction.followup.send("⚠️ Impossible de trouver cette alliance.")
                    data1 = await resp.json()
                    target = data1[0] if isinstance(data1, list) and data1 else data1
                    alliance_id = target.get('alliance_id') or target.get('id')
            except Exception:
                return await interaction.followup.send("⚠️ Erreur de connexion avec l'API GGE-Tracker.")

            if not alliance_id:
                return await interaction.followup.send(f"⚠️ Alliance **{alliance}** introuvable.")

            # 3. Récupération des Statistiques (Le fameux gros JSON !)
            try:
                stats_url = f"https://api.gge-tracker.com/api/v1/statistics/alliance/{alliance_id}"
                async with session.get(stats_url, headers=headers, timeout=15) as resp:
                    if resp.status != 200: return await interaction.followup.send("⚠️ Impossible de télécharger l'historique.")
                    stats_data = await resp.json()
            except Exception:
                return await interaction.followup.send("⚠️ Erreur lors du téléchargement des statistiques.")

        # 4. Traitement des données "player_might_history"
        might_history = stats_data.get("points", {}).get("player_might_history", [])
        if not might_history:
            return await interaction.followup.send(f"⚠️ Aucun historique de puissance disponible pour **{alliance}**.")

        # On va regrouper les données par Jour (YYYY-MM-DD)
        daily_data = {}
        for entry in might_history:
            d_str = entry.get("date")
            pid = str(entry.get("player_id"))
            pt = int(entry.get("point", 0))
            if not d_str: continue

            try:
                dt = datetime.fromisoformat(d_str.replace('Z', '+00:00'))
                if dt < date_limite: continue # On ignore ce qui est trop vieux
                
                day_str = dt.strftime("%d/%m/%Y")
                
                if day_str not in daily_data:
                    daily_data[day_str] = {}
                
                # Si l'API a pris 2 photos le même jour, on garde le meilleur score du joueur
                daily_data[day_str][pid] = max(daily_data[day_str].get(pid, 0), pt)
            except: pass

        if not daily_data:
            return await interaction.followup.send(f"⚠️ Aucune donnée enregistrée pour **{alliance}** sur les **{jours} derniers jours**.")

        # 5. Calcul de la puissance totale de l'alliance par jour
        alliance_daily_might = {}
        for day, players in daily_data.items():
            alliance_daily_might[day] = sum(players.values())

        # On remet les jours dans l'ordre chronologique (du plus vieux au plus récent)
        # Petite astuce : on reconvertit en datetime pour trier correctement
        sorted_days = sorted(alliance_daily_might.keys(), key=lambda d: datetime.strptime(d, "%d/%m/%Y"))
        
        # 6. Extraction des Bilan & Statistiques Globale
        premier_jour = sorted_days[0]
        dernier_jour = sorted_days[-1]
        
        pp_debut = alliance_daily_might[premier_jour]
        pp_fin = alliance_daily_might[dernier_jour]
        variation_totale = pp_fin - pp_debut
        
        pic_pp = max(alliance_daily_might.values())
        pire_pp = min(alliance_daily_might.values())

        def format_diff(val):
            if val > 0: return f"🟢 +{format_num(val)}"
            elif val < 0: return f"🔴 {format_num(val)}"
            else: return "⚪ 0"

        stats_txt = (
            f"📅 **Période** : du {premier_jour} au {dernier_jour}\n"
            f"🚀 **PP de Départ** : {format_num(pp_debut)}\n"
            f"🏁 **PP Actuels** : {format_num(pp_fin)}\n"
            f"📈 **Variation Globale** : **{format_diff(variation_totale)}**\n"
            f"🌟 **Pic historique** : {format_num(pic_pp)} PP\n"
            f"📉 **Pire journée** : {format_num(pire_pp)} PP"
        )

        # 7. Création de l'historique textuel détaillé
        lignes_historique = []
        pp_veille = None
        
        # On lit à l'envers pour afficher le plus récent en haut de la liste !
        for day in reversed(sorted_days):
            pp_jour = alliance_daily_might[day]
            
            # On cherche le jour d'avant pour calculer la différence quotidienne
            index_jour = sorted_days.index(day)
            if index_jour > 0:
                pp_hier = alliance_daily_might[sorted_days[index_jour - 1]]
                diff_jour = pp_jour - pp_hier
                diff_txt = f"({format_diff(diff_jour)})"
            else:
                diff_txt = "(Point de départ)"

            lignes_historique.append(f"🔹 **{day}** ➔ **{format_num(pp_jour)} PP** *{diff_txt}*")

        # 8. Affichage Paginé !
        from utils import PaginationView, BOT_VERSION # Import de sécurité
        
        embeds = []
        chunk_size = 15
        nb_pages = max(1, (len(lignes_historique) - 1) // chunk_size + 1)

        alliance_name_real = target.get('alliance_name') or target.get('name', alliance)

        for i in range(0, len(lignes_historique), chunk_size):
            chunk = lignes_historique[i:i+chunk_size]
            page_actuelle = (i // chunk_size) + 1
            
            embed = discord.Embed(
                title=f"📈 Évolution Puissance : {alliance_name_real}", 
                color=discord.Color.blue(), 
                timestamp=discord.utils.utcnow()
            )
            embed.description = f"Analyse sur les **{jours} derniers jours**."
            
            # Bilan global toujours visible en haut
            embed.add_field(name="📊 Bilan Global de la Période", value=stats_txt, inline=False)
            
            # Liste quotidienne
            embed.add_field(name=f"📅 Historique Quotidien (Page {page_actuelle}/{nb_pages})", value="\n".join(chunk), inline=False)
            
            embeds.append(embed)

        if len(embeds) == 1:
            embed.set_footer(text=BOT_VERSION)
            await interaction.followup.send(embed=embeds[0])
        else:
            embed.set_footer(text=BOT_VERSION)
            view = PaginationView(embeds)
            await interaction.followup.send(embed=embeds[0], view=view)

    # ==========================================
    # 🕊️ COMMANDE : VÉRIFIER LA COLOMBE
    # ==========================================
    @app_commands.command(name="colombe", description="🕊️ Vérifie la date et l'heure de fin de protection d'un joueur")
    @app_commands.autocomplete(joueur=joueur_autocomplete)
    async def colombe(self, interaction: discord.Interaction, joueur: str):
        await interaction.response.defer()
        
        logger.info(f"🕊️ [Colombe] Vérification par {interaction.user.name} pour : {joueur}")
        
        headers = {'accept': 'application/json', 'gge-server': 'E4K_FR1'}
        # L'URL de l'API pour récupérer les infos du joueur
        url = f"https://api.gge-tracker.com/api/v1/players/{urllib.parse.quote(joueur)}"
        
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(url, headers=headers, timeout=5) as r:
                    if r.status == 200:
                        data = await r.json()
                        p_data = data[0] if isinstance(data, list) and data else data
                        
                        if not p_data:
                            return await interaction.followup.send(f"⚠️ Joueur **{joueur}** introuvable sur l'API.")
                            
                        peace_str = p_data.get("peace_disabled_at")
                        
                        # Si l'API renvoie null/None, c'est qu'il n'y a pas de colombe
                        if not peace_str or peace_str == "null":
                            return await interaction.followup.send(f"⚔️ **{joueur}** n'a actuellement **aucune colombe** en cours. Cible libre !")
                            
                        # Si on a une date, on la convertit
                        dt_peace = datetime.fromisoformat(peace_str.replace('Z', '+00:00'))
                        maintenant = discord.utils.utcnow()
                        ts = int(dt_peace.timestamp())
                        
                        if dt_peace > maintenant:
                            # La colombe est toujours active
                            embed = discord.Embed(title="🕊️ Statut de la Colombe", color=discord.Color.blue())
                            embed.add_field(name="Cible", value=f"**{joueur}**", inline=False)
                            embed.add_field(name="Fin de protection", value=f"<t:{ts}:f>\nSoit **<t:{ts}:R>**", inline=False)
                            await interaction.followup.send(embed=embed)
                        else:
                            # La date est dans le passé, la colombe a expiré
                            await interaction.followup.send(f"⚔️ La colombe de **{joueur}** a expiré le <t:{ts}:f> (<t:{ts}:R>). Cible libre !")
                            
                    else:
                        await interaction.followup.send(f"⚠️ Impossible de récupérer les infos de **{joueur}** (Erreur API : {r.status}).")
            except Exception as e:
                await interaction.followup.send(f"❌ Erreur de connexion au serveur GGE-Tracker.")

# 🔌 Branchement du Cog
async def setup(bot: commands.Bot):
    await bot.add_cog(ProfilsCog(bot))
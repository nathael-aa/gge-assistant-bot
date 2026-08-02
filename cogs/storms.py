# -*- coding: utf-8 -*-
import asyncio
from discord.ext import tasks
import logging
import urllib.parse
import json
import os
from datetime import datetime
import discord
from discord import app_commands
from discord.ext import commands

from utils import (
    PaginationView, 
    get_api_headers,
    get_server_config,
    setup_embed_footer,
    joueur_autocomplete,
    t
)

logger = logging.getLogger("GGE_Bot")

# Fichier de sauvegarde
STORM_CONFIG_PATH = "data/configs/storm_alerts.json"
SERVEURS_DE_TEST = [1342424613660921908]


async def load_storm_config():
    if not os.path.exists(STORM_CONFIG_PATH):
        return {"guilds": {}, "notified": []}
    try:
        with open(STORM_CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"guilds": {}, "notified": []}

async def save_storm_config(data):
    os.makedirs(os.path.dirname(STORM_CONFIG_PATH), exist_ok=True)
    with open(STORM_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

# ==========================================
# ⚙️ CONFIGURATION DES ID DE L'API STORM
# ==========================================
FORT_LEVELS_MAPPING = {
    10: {"lvl": 40, "desc": "40"},
    11: {"lvl": 50, "desc": "50"},
    7:  {"lvl": 60, "desc": "60 "},
    12: {"lvl": 60, "desc": "60 <:shield:1533179119800418334>"},
    8:  {"lvl": 70, "desc": "70"},
    13: {"lvl": 70, "desc": "70 <:shield:1533179119800418334>"},
    9:  {"lvl": 80, "desc": "80"},
    14: {"lvl": 80, "desc": "80 <:shield:1533179119800418334>"}
}

ISLE_RESOURCE_MAPPING = {
    1: "Wood (40,000<:wood:1533427512611311728>)",
    4: "Wood (20,000<:wood:1533427512611311728>)",
    2: "Stone (40,000<:stone:1533427511315402822>)",
    5: "Stone (20,000<:stone:1533427511315402822>)",
    3: "Aquamarine (52,000<:aquamarine_brut:1533424307512807486>)",
    6: "Aquamarine (11,500<:aquamarine_brut:1533424307512807486>)"
}
# ==========================================

class StormsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.clr_forts = discord.Color.from_rgb(139,196,191)
        self.clr_isles = discord.Color.from_rgb(211,240,227)
        self.clr_occupier = discord.Color.from_rgb(137,196,199)
        self.clr_status = discord.Color.from_rgb(132,206,209)
        self.clr_setup = discord.Color.from_rgb(144,232,219)
        self.api_base = "https://api-beta.gge-tracker.com/api/v1/"

        self.active_alerts = []

    # Création du groupe de commandes principal /storm
    storm_group = app_commands.Group(
        name="storm", 
        description="Commands for the Storm Islands event",
        guild_ids=SERVEURS_DE_TEST
    )

    # ========================================================
    # ⚔️ COMMANDE : /storm forts
    # ========================================================
    @storm_group.command(name="forts", description="Search for storm forts based on your criteria")
    @app_commands.choices(
        availability=[
            app_commands.Choice(name="Available Now", value=1),
            app_commands.Choice(name="In < 5 mins", value=2),
            app_commands.Choice(name="In < 1 hour", value=3),
            app_commands.Choice(name="All", value=0)
        ]
    )
    @app_commands.describe(
        availability="When the fort will be attackable (Default: Available Now)",
        lvl40="Include level 40 forts",
        lvl50="Include level 50 forts",
        lvl60="Include level 60 forts",
        lvl70="Include level 70 forts",
        lvl80="Include level 80 forts",
        min_attacks="Minimum attacks left (0-10)"
    )
    async def storm_forts(self, interaction: discord.Interaction, availability: int = 1, lvl40: bool = False, lvl50: bool = False, lvl60: bool = False, lvl70: bool = False, lvl80: bool = False, min_attacks: app_commands.Range[int, 0, 10] = 0):
        # On signale à Discord que le bot réfléchit
        await interaction.response.defer(thinking=True)
        langue, serveur = await get_server_config(interaction)
        headers = await get_api_headers(interaction)

        # 🔄 Sous-fonction qui gère tout (utilisable par la commande ET par le bouton)
        async def fetch_and_build_view(current_inter: discord.Interaction, is_refresh: bool):
            # 1. Construction dynamique de la liste des IDs
            isle_ids = []
            if lvl40: isle_ids.append(10)
            if lvl50: isle_ids.append(11)
            if lvl60: isle_ids.extend([7, 12])
            if lvl70: isle_ids.extend([8, 13])
            if lvl80: isle_ids.extend([9, 14])

            if not isle_ids:
                isle_ids = [7, 8, 9, 10, 11, 12, 13, 14]

            # 2. Paramètres API
            params = {
                "page": 1,
                "size": 500,
                "minAttacksLeft": min_attacks,
                "filterByIsleIds": f"[{','.join(map(str, isle_ids))}]",
                "orderDirection": "desc"
            }

            if availability != 0:
                params["filterByAvailability"] = availability

            url = f"{self.api_base}/storms/forts"
            async with self.bot.session.get(url, headers=headers, params=params, timeout=15) as r:
                if r.status != 200:
                    msg = t(langue, "cmd_storm_api_err", defaut="<:error:1512505075220611172> Error connecting to the Storms API.")
                    return await current_inter.followup.send(msg, ephemeral=True) if is_refresh else await current_inter.followup.send(msg)
                data = await r.json()

            forts = data.get("forts", [])
            if not forts:
                msg = t(langue, "cmd_storm_no_forts", defaut="<:Information:1533430015264555099> No forts match your criteria.")
                return await current_inter.followup.send(msg, ephemeral=True) if is_refresh else await current_inter.followup.send(msg)

            # 3. Filtrage et Tri
            forts_filtres = []
            for fort in forts:
                fort_info = FORT_LEVELS_MAPPING.get(fort.get("isle_id"))
                if fort_info:
                    forts_filtres.append((fort_info["desc"], fort))

            if availability in [2, 3]:
                def sort_by_spawn(item):
                    desc, fort = item
                    raw_time = fort.get("available_at", "")
                    try:
                        dt = datetime.fromisoformat(raw_time.replace('Z', '+00:00'))
                        ts = int(dt.timestamp())
                    except:
                        ts = 9999999999
                    return (ts, -fort.get("isle_id", 0))
                forts_filtres.sort(key=sort_by_spawn)
            else:
                forts_filtres.sort(key=lambda x: (x[0], x[1].get("attacks_left", 0)), reverse=True)

            # 4. Construction des Embeds (15 par page)
            embeds = []
            items_par_page = 15
            total_pages = (len(forts_filtres) - 1) // items_par_page + 1
            titre_base = t(langue, "cmd_storm_forts_title", defaut="<:fort:1533175610636243024> Available Storm Forts")
            
            # Petit ajout : Affichage de la date d'actualisation
            last_update = f"\n*<:time:1512573766096654458> Last refreshed: <t:{int(datetime.now().timestamp())}:T>*"

            for i in range(0, len(forts_filtres), items_par_page):
                page_items = forts_filtres[i : i + items_par_page]
                numero_page = (i // items_par_page) + 1
                
                embed = discord.Embed(
                    title=f"{titre_base} (Page {numero_page}/{total_pages})", 
                    color=self.clr_forts
                )
                
                lignes_description = []
                for desc, fort in page_items:
                    attaques = fort.get("attacks_left", 0)
                    x, y = fort.get("position_x", 0), fort.get("position_y", 0)
                    
                    time_str = ""
                    if availability in [2, 3]:
                        raw_time = fort.get("available_at", "")
                        try:
                            dt = datetime.fromisoformat(raw_time.replace('Z', '+00:00'))
                            time_str = f" | <:time:1512573766096654458> <t:{int(dt.timestamp())}:R>"
                        except:
                            pass

                    lignes_description.append(f"**Lvl. {desc}** | <:compass:1512504625364729987> `({x}:{y})` | <:attaque:1512570903886692474> {attaques} att. left{time_str}")
                
                embed.description = "\n".join(lignes_description) + "\n" + last_update
                await setup_embed_footer(embed, interaction, langue)
                embeds.append(embed)

            # 5. Création de la Vue et Ajout du Bouton Refresh
            if len(embeds) > 1:
                view = PaginationView(embeds)
            else:
                view = discord.ui.View(timeout=600)
                
            refresh_btn = discord.ui.Button(
                style=discord.ButtonStyle.primary, 
                emoji="<:refresh:1533433306610274425>", 
                label=t(langue, "btn_refresh", defaut="Refresh")
            )
            
            # Callback exclusif au bouton
            async def refresh_callback(btn_inter: discord.Interaction):
                await btn_inter.response.defer() # On dit à Discord qu'on charge
                await fetch_and_build_view(btn_inter, is_refresh=True)
                
            refresh_btn.callback = refresh_callback
            view.add_item(refresh_btn) # On ajoute le bouton à la vue (paginée ou non)

            # 6. Envoi ou Édition
            if is_refresh:
                # Si c'est un refresh, on modifie le message actuel
                await current_inter.edit_original_response(embed=embeds[0], view=view)
            else:
                # Premier appel de la commande
                await current_inter.followup.send(embed=embeds[0], view=view)

        # Appel initial lors du lancement de la commande
        await fetch_and_build_view(interaction, is_refresh=False)

    # ========================================================
    # 🏝️ COMMANDE : /storm isles
    # ========================================================
    @storm_group.command(name="isles", description="Search for resource islands in the Storm Islands")
    @app_commands.choices(
        status=[
            app_commands.Choice(name="All", value=0),
            app_commands.Choice(name="Free (Ready to capture)", value=1),
            app_commands.Choice(name="Occupied", value=2),
            app_commands.Choice(name="Respawning (Sunk)", value=3),
        ],
        resource=[
            app_commands.Choice(name="All", value=0),
            app_commands.Choice(name="Aquamarine", value=1), 
            app_commands.Choice(name="Wood", value=2),
            app_commands.Choice(name="Stone", value=3),
        ]
    )
    async def storm_isles(self, interaction: discord.Interaction, status: app_commands.Choice[int], resource: app_commands.Choice[int]):
        await interaction.response.defer(thinking=True)
        langue, serveur = await get_server_config(interaction)
        headers = await get_api_headers(interaction)

        # 🔄 Sous-fonction qui gère tout (utilisable par la commande ET par le bouton)
        async def fetch_and_build_view(current_inter: discord.Interaction, is_refresh: bool):
            # 1. Paramètres API
            params = {
                "page": 1,
                "size": 500, # On limite pour éviter de saturer
            }
            if status.value != 0:
                params["filterByState"] = status.value

            url = f"{self.api_base}/storms/isles"
            async with self.bot.session.get(url, headers=headers, params=params, timeout=15) as r:
                if r.status != 200:
                    msg = t(langue, "cmd_storm_api_err", defaut="<:error:1512505075220611172> Error connecting to the Storms API.")
                    return await current_inter.followup.send(msg, ephemeral=True) if is_refresh else await current_inter.followup.send(msg)
                data = await r.json()

            isles = data.get("isles", [])
            
            # 2. Filtrage local par ressource (l'API n'a pas filterByIsleIds pour les îles)
            if resource.value == 1:
                allowed_isles = [3, 6] # Aquamarine
            elif resource.value == 2:
                allowed_isles = [1, 4] # Wood
            elif resource.value == 3:
                allowed_isles = [2, 5] # Stone
            else:
                allowed_isles = []

            if allowed_isles:
                isles = [i for i in isles if i.get("isle_id") in allowed_isles]

            if not isles:
                msg = t(langue, "cmd_storm_no_isles", defaut="<:Information:1533430015264555099> No islands match your criteria.")
                return await current_inter.followup.send(msg, ephemeral=True) if is_refresh else await current_inter.followup.send(msg)

            # 3. Tri (Chronologique si Respawning, sinon tri par type de ressource)
            if status.value == 3:
                def sort_by_spawn(isle):
                    raw_time = isle.get("available_at", "")
                    try:
                        dt = datetime.fromisoformat(raw_time.replace('Z', '+00:00'))
                        return int(dt.timestamp())
                    except:
                        return 9999999999
                isles.sort(key=sort_by_spawn)
            else:
                isles.sort(key=lambda x: x.get("isle_id", 0))

            # 4. Construction des Embeds (15 par page)
            embeds = []
            items_par_page = 15
            total_pages = (len(isles) - 1) // items_par_page + 1
            titre_base = t(langue, "cmd_storm_isles_title", defaut="<:island:1533175666915278931> Storm Islands")
            
            last_update = f"\n*<:refresh:1533433306610274425> Last refreshed: <t:{int(datetime.now().timestamp())}:T>*"

            for i in range(0, len(isles), items_par_page):
                page_items = isles[i : i + items_par_page]
                numero_page = (i // items_par_page) + 1
                
                embed = discord.Embed(
                    title=f"{titre_base} (Page {numero_page}/{total_pages})", 
                    color=self.clr_isles
                )
                
                lignes_description = []
                for isle in page_items:
                    x, y = isle.get("position_x"), isle.get("position_y")
                    res_nom = ISLE_RESOURCE_MAPPING.get(isle.get("isle_id"), "Unknown")
                    etat = isle.get("state")
                    
                    # ⚠️ CORRECTION : L'API renvoie 0 (Libre), 1 (Occupé), 2 (En réapparition)
                    if etat == 0:
                        ligne = f"🟢 **{res_nom}** | <:compass:1512504625364729987> `({x}:{y})` | *Free!*"
                    elif etat == 1:
                        # Utilisation de 'or' au cas où l'API renvoie un vrai JSON 'null'
                        occupant = isle.get("occupier_name") or "Unknown"
                        alliance = isle.get("occupier_alliance_name") or "None"
                        ligne = f"🔴 **{res_nom}** | <:compass:1512504625364729987> `({x}:{y})` | 🛡️ {occupant} (*{alliance}*)"
                    elif etat == 2:
                        raw_time = isle.get("available_at", "")
                        try:
                            dt = datetime.fromisoformat(raw_time.replace('Z', '+00:00'))
                            ts = int(dt.timestamp())
                            time_str = f"<t:{ts}:R>"
                        except:
                            time_str = "Soon"
                        ligne = f"<:time:1512573766096654458> **{res_nom}** | <:compass:1512504625364729987> `({x}:{y})` | Respawns: {time_str}"
                    else:
                        ligne = f"<:Information:1533430015264555099> **{res_nom}** | <:compass:1512504625364729987> `({x}:{y})` | State unknown"

                    lignes_description.append(ligne)
                
                embed.description = "\n".join(lignes_description) + "\n" + last_update
                await setup_embed_footer(embed, interaction, langue)
                embeds.append(embed)

            # 5. Création de la Vue et Ajout du Bouton Refresh
            if len(embeds) > 1:
                view = PaginationView(embeds)
            else:
                view = discord.ui.View(timeout=600)
                
            refresh_btn = discord.ui.Button(
                style=discord.ButtonStyle.primary, 
                emoji="<:refresh:1533433306610274425>", 
                label=t(langue, "btn_refresh", defaut="Refresh")
            )
            
            async def refresh_callback(btn_inter: discord.Interaction):
                await btn_inter.response.defer()
                await fetch_and_build_view(btn_inter, is_refresh=True)
                
            refresh_btn.callback = refresh_callback
            view.add_item(refresh_btn)

            # 6. Envoi ou Édition
            if is_refresh:
                await current_inter.edit_original_response(embed=embeds[0], view=view)
            else:
                await current_inter.followup.send(embed=embeds[0], view=view)

        # Appel initial
        await fetch_and_build_view(interaction, is_refresh=False)

    # ========================================================
    # 🕵️‍♂️ COMMANDE : /storm occupier
    # ========================================================
    @storm_group.command(name="occupier", description="List all islands currently held by a specific player")
    @app_commands.autocomplete(player=joueur_autocomplete)
    @app_commands.describe(player="Exact player name")
    async def storm_occupier(self, interaction: discord.Interaction, player: str):
        await interaction.response.defer(thinking=True)
        langue, serveur = await get_server_config(interaction)
        headers = await get_api_headers(interaction)

        safe_joueur = urllib.parse.quote(player)
        url = f"{self.api_base}/storms/isles?filterByOccupierName={safe_joueur}&size=4000"

        async with self.bot.session.get(url, headers=headers, timeout=15) as r:
            if r.status != 200:
                return await interaction.followup.send(t(langue, "cmd_storm_api_err", defaut="<:error:1512505075220611172> Error connecting to the Storms API."))
            data = await r.json()

        isles = data.get("isles", [])
        if not isles:
            return await interaction.followup.send(t(langue, "cmd_storm_no_occupier", defaut=f"<:Information:1533430015264555099> The player **{player}** does not currently hold any resource islands."))

        titre = t(langue, "cmd_storm_occupier_title", defaut=f"<:players:1512504277392953426> Islands held by {player}")
        embed = discord.Embed(title=titre, description=f"Total islands under control: **{len(isles)}**", color=self.clr_occupier)
        
        chunk = ""
        for isle in isles:
            x, y = isle.get("position_x"), isle.get("position_y")
            res_nom = ISLE_RESOURCE_MAPPING.get(isle.get("isle_id"), "Unknown")
            ligne = f"<:compass:1512504625364729987> `({x}:{y})` | {res_nom}\n"
            
            if len(chunk) + len(ligne) > 1024:
                embed.add_field(name="Positions", value=chunk, inline=False)
                chunk = ligne
            else:
                chunk += ligne
                
        if chunk:
            embed.add_field(name="Positions", value=chunk, inline=False)

        await setup_embed_footer(embed, interaction, langue)
        await interaction.followup.send(embed=embed)

    # ========================================================
    # 📡 COMMANDE : /storm status
    # ========================================================
    @storm_group.command(name="status", description="Displays the freshness state of the Storm Islands map scan")
    async def storm_status(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)
        langue, serveur = await get_server_config(interaction)
        headers = await get_api_headers(interaction)

        url = f"{self.api_base}/storms/meta"
        async with self.bot.session.get(url, headers=headers, timeout=10) as r:
            if r.status != 200:
                return await interaction.followup.send(t(langue, "cmd_storm_api_err", defaut="<:error:1512505075220611172> Error connecting to the Storms API."))
            data = await r.json()

        # Formatage des timestamps
        last_scan = data.get("last_scan_at", "")
        
        try:
            ts_scan = int(datetime.fromisoformat(last_scan.replace('Z', '+00:00')).timestamp())
            scan_str = f"<t:{ts_scan}:R>"
        except:
            scan_str = "Unknown"

        embed = discord.Embed(title=t(langue, "cmd_storm_status_title", defaut="<:status:1533435056087896164> Storm Islands Status"), color=self.clr_status)
        
        desc = (
            f"**Last Scan:** {scan_str}\n"
            f"**Covered Radius:** {data.get('scan_radius', 0)} tiles\n\n"
            f"<:fort:1533175610636243024> **Tracked Forts:** {data.get('forts_count', 0):,}\n"
            f"<:island:1533175666915278931> **Tracked Isles:** {data.get('isles_count', 0):,}"
        )
        
        embed.description = desc
        await setup_embed_footer(embed, interaction, langue)
        await interaction.followup.send(embed=embed)

    # ========================================================
    # ⚙️ COMMANDE : /storm setup
    # ========================================================
    @storm_group.command(name="setup", description="Configure automatic alerts for respawning islands")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(
        channel="The channel where notifications will be sent",
        ping_small="Ping for small islands (Default: False)",
        ping_big="Ping for big islands (Default: False)",
        role="Role to ping (Leave empty to use @here if a ping is active)"
    )
    async def storm_setup(self, interaction: discord.Interaction, channel: discord.TextChannel, ping_small: bool = False, ping_big: bool = False, role: discord.Role = None):
        await interaction.response.defer(thinking=True)
        langue, serveur = await get_server_config(interaction)

        bot_permissions = channel.permissions_for(interaction.guild.me)
        if not bot_permissions.send_messages or not bot_permissions.embed_links:
            msg = t(langue, "cmd_storm_setup_perms", defaut=f"<:error:1512505075220611172> I need permissions to send messages and embed links in {channel.mention}.")
            return await interaction.followup.send(msg)

        ping_format = role.mention if role else "@here"
        
        # --- SAUVEGARDE DANS LE JSON ---
        data = await load_storm_config()
        if "guilds" not in data:
            data["guilds"] = {}

        data["guilds"][str(interaction.guild.id)] = {
            "channel_id": channel.id,
            "ping_small": ping_small,
            "ping_big": ping_big,
            "ping_role": ping_format,
            "gge_server": serveur,
            "langue": langue
        }
        await save_storm_config(data)
        # -------------------------------

        embed = discord.Embed(
            title=t(langue, "cmd_storm_setup_title", defaut="<:setup:14457223> Storm Islands Alerts Configured!"),
            color=self.clr_setup,
            description=f"Notifications will be sent in {channel.mention}."
        )
        
        etat_big = "<:greencirclebullet:1533440867598340186> Yes" if ping_big else "<:tomatobulletpoint:1533440866063224933> No"
        etat_small = "<:greencirclebullet:1533440867598340186> Yes" if ping_small else "<:tomatobulletpoint:1533440866063224933> No"
        mention_txt = ping_format if (ping_big or ping_small) else "No mention"

        embed.add_field(name="Ping Big Isles", value=etat_big, inline=True)
        embed.add_field(name="Ping Small Isles", value=etat_small, inline=True)
        embed.add_field(name="Mention", value=mention_txt, inline=True)

        await setup_embed_footer(embed, interaction, langue)
        await interaction.followup.send(embed=embed)

    def cog_load(self):
        self.storm_alert_loop.start()

    def cog_unload(self):
        self.storm_alert_loop.cancel()

    @tasks.loop(minutes=1.0)
    async def storm_alert_loop(self):
        now = datetime.now().timestamp()

        # ==========================================================
        # 🔄 MISE À JOUR DES EMBEDS (Passage en rouge quand ça spawn)
        # ==========================================================
        alerts_to_keep = []
        for alert in self.active_alerts:
            if now >= alert["ts"]:
                # Le temps est écoulé, l'île a spawn !
                emb = alert["embed"]
                emb.color = discord.Color.red()
                # On met à jour le titre pour indiquer que c'est apparu
                emb.title = "<:island:1533175666915278931> Island Spawned!"
                try:
                    await alert["message"].edit(embed=emb)
                except Exception:
                    pass # Le message a pu être supprimé entre temps
            else:
                # Pas encore l'heure, on garde en mémoire
                alerts_to_keep.append(alert)
                
        self.active_alerts = alerts_to_keep
        # ==========================================================

        data = await load_storm_config()
        guilds_config = data.get("guilds", {})
        notified = data.get("notified", [])
        
        if not guilds_config:
            return 
            
        # 1. Regrouper les configurations Discord par serveur de jeu GGE
        servers_to_check = {}
        for guild_id_str, config in guilds_config.items():
            gge_server = config.get("gge_server", "E4K_FR1")
            if gge_server not in servers_to_check:
                servers_to_check[gge_server] = []
            servers_to_check[gge_server].append((guild_id_str, config))

        modifie = False
        total_annoncailles = 0

        # 2. Faire une requête API pour CHAQUE serveur de jeu concerné
        for gge_server, guilds_list in servers_to_check.items():
            headers = await get_api_headers(custom_server=gge_server)
            params = {"size": 4000, "filterByState": 3}
            
            try:
                async with self.bot.session.get(f"{self.api_base}storms/isles", headers=headers, params=params, timeout=15) as r:
                    if r.status != 200: continue
                    api_data = await r.json()
            except Exception as e:
                logger.error(f"❌ [Storm Alerts] Erreur API pour {gge_server} : {e}")
                continue

            isles = api_data.get("isles", [])
            isles_to_announce = []
            
            # 3. Filtrer les îles
            for isle in isles:
                isle_id = isle.get("isle_id")
                
                # 🛑 FILTRE OPTIMISÉ : On ignore immédiatement le Bois et la Pierre
                if isle_id not in [3, 6]:
                    continue

                raw_time = isle.get("available_at", "")
                try:
                    dt = datetime.fromisoformat(raw_time.replace('Z', '+00:00'))
                    ts = dt.timestamp()
                    time_left = ts - now
                    
                    x, y = isle.get("position_x"), isle.get("position_y")
                    
                    # 🛠️ CORRECTION DU BUG DE DOUBLON
                    # Au lieu du timestamp exact, on arrondit à l'heure près (// 3600).
                    # Une île ne spawn jamais deux fois aux mêmes coordonnées dans la même heure.
                    uid = f"{gge_server}_{x}_{y}_{int(ts) // 3600}" 
                    
                    if 0 < time_left <= 300 and uid not in notified:
                        isles_to_announce.append((isle, ts))
                        notified.append(uid)
                        modifie = True
                except:
                    continue

            # 4. Envoyer les annonces
            if not isles_to_announce:
                continue

            total_annoncailles += len(isles_to_announce)

            for guild_id_str, config in guilds_list:
                channel_id = config.get("channel_id")
                ping_small = config.get("ping_small", False)
                ping_big = config.get("ping_big", False)
                ping_role = config.get("ping_role", "")
                langue = config.get("langue", "fr")

                channel = self.bot.get_channel(channel_id)
                if not channel:
                    try: channel = await self.bot.fetch_channel(channel_id)
                    except: continue

                for isle, ts in isles_to_announce:
                    isle_id = isle.get("isle_id")
                    is_big = (isle_id == 3)
                    is_small = (isle_id == 6)
                    
                    should_ping = (is_big and ping_big) or (is_small and ping_small)
                    res_nom = ISLE_RESOURCE_MAPPING.get(isle_id, "Unknown Island")
                    x, y = isle.get("position_x"), isle.get("position_y")
                    
                    msg_content = f"{ping_role}" if should_ping else None
                    titre = t(langue, "alert_storm_title", defaut="<:island:1533175666915278931> Island Respawning Soon!")
                    
                    # ⏱️ FORMAT PRÉCIS : Ajout de :T pour l'heure exacte (avec secondes) et :R pour le compte à rebours
                    desc = t(langue, "alert_storm_desc", name=res_nom, ts=int(ts), x=x, y=y, defaut=f"**{res_nom}** will spawn at **<t:{int(ts)}:T>** (<t:{int(ts)}:R>)\n<:compass:1512504625364729987> Coords: `{x}:{y}`")

                    embed = discord.Embed(
                        title=titre,
                        description=desc,
                        color=self.clr_isles
                    )
                    
                    try:
                        # On envoie le message et on l'ajoute à la mémoire pour l'éditer plus tard
                        sent_msg = await channel.send(content=msg_content, embed=embed)
                        self.active_alerts.append({
                            "message": sent_msg,
                            "embed": embed,
                            "ts": ts
                        })
                    except discord.Forbidden:
                        pass 

        # 5. Sauvegarde générale
        if modifie:
            if len(notified) > 200: 
                notified = notified[-200:]
            data["notified"] = notified
            await save_storm_config(data)
            
        if total_annoncailles > 0:
            logger.info(f"✅ [Storm Alerts] {total_annoncailles} îles annoncées au total ce cycle !")

    @storm_alert_loop.before_loop
    async def before_storm_alert_loop(self):
        await self.bot.wait_until_ready()

    # ========================================================
    # 🛑 COMMANDE : /storm stop
    # ========================================================
    @storm_group.command(name="stop", description="Stop automatic alerts for respawning islands")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True) # Réservé aux admins, comme le setup
    async def storm_stop(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)
        langue, serveur = await get_server_config(interaction)

        data = await load_storm_config()
        guild_id_str = str(interaction.guild.id)

        # On vérifie si le serveur a bien une configuration active
        if "guilds" in data and guild_id_str in data["guilds"]:
            # On supprime le serveur du dictionnaire
            del data["guilds"][guild_id_str]
            await save_storm_config(data)
            
            embed = discord.Embed(
                title=t(langue, "cmd_storm_stop_title", defaut="🛑 Alerts Stopped"),
                description=t(langue, "cmd_storm_stop_desc", defaut="Automatic Storm Islands alerts have been successfully disabled for this server."),
                color=discord.Color.red()
            )
            await setup_embed_footer(embed, interaction, langue)
            await interaction.followup.send(embed=embed)
        else:
            # Si le serveur n'était pas configuré
            msg = t(langue, "cmd_storm_stop_none", defaut="<:Information:1533430015264555099> No active alerts configuration found for this server.")
            await interaction.followup.send(msg)

async def setup(bot: commands.Bot):
    await bot.add_cog(StormsCog(bot))
    
    # --- AJOUT TEMPORAIRE POUR FORCER LA MISE À JOUR ---
    try:
        # On force Discord à enregistrer les commandes spécifiquement pour ton serveur de test
        guild = discord.Object(id=1342424613660921908)
        await bot.tree.sync(guild=guild)
        
        # On force aussi une synchro globale pour "effacer" l'ancienne version publique
        await bot.tree.sync()
        logger.info("✅ Commandes synchronisées de force pour le serveur de test !")
    except Exception as e:
        logger.error(f"❌ Erreur de synchronisation : {e}")
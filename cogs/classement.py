import asyncio
import json
import logging
import os
import urllib.parse

import discord
from discord import app_commands
from discord.ext import commands

from utils import (
    PaginationView,
    alliance_autocomplete,
    get_api_headers,
    get_cached_data,
    get_server_config,
    joueur_autocomplete,
    prompt_vote_if_lucky,
    setup_embed_footer,
    t,
)


class ClassementCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.logger = logging.getLogger("GGEAssistant")
        self.ranking_api_url = "https://empire-api.fly.dev"
        self.config_path = "data/configs/configuration.json"

        self.servers_map = {}
        self.event_ids = {}
        self.brackets_map = {}

        self.load_rankings_config()

    def load_rankings_config(self):
        """Charge la configuration depuis le fichier externe."""
        try:
            self.logger.info(f"📝 [Classement] Tentative de chargement du JSON à : {self.config_path}")
            if os.path.exists(self.config_path):
                with open(self.config_path, encoding="utf-8") as f:
                    config_data = json.load(f)

                    # 💡 NOUVELLE LOGIQUE : On lit 'servers_info' et on extrait 'api_name'
                    servers_info = config_data.get("servers_info", {})
                    self.servers_map = {
                        srv_name.lower(): srv_data.get("api_name")
                        for srv_name, srv_data in servers_info.items()
                        if isinstance(srv_data, dict) and srv_data.get("api_name")
                    }

                    self.event_ids = config_data.get("event_ids", {})
                    self.brackets_map = config_data.get("brackets", {})

                self.logger.info(
                    f"📝 [Classement] Fichier chargé ! Serveurs API: {len(self.servers_map)}, Events: {len(self.event_ids)}"
                )
            else:
                self.logger.warning(
                    f"⚠️ [Classement] Fichier introuvable à {self.config_path} ! Vérifie ton arborescence."
                )
        except Exception as e:
            self.logger.error(f"❌ [Classement] Erreur critique lors de l'initialisation du JSON : {e}")

    # ========================================================
    # 🏆 GROUPE DE COMMANDES : RANK
    # ========================================================
    classement = app_commands.Group(name="rank", description="Live event analysis and rankings")

    def format_page(self, chunk, page_index, langue, color=discord.Color.gold(), is_alliance=False):
        embed = discord.Embed(color=color)
        description = ""
        for p in chunk:
            if isinstance(p, dict):
                rank = int(p.get("R", 0))
                score = p.get("S", 0)
                name = p.get("P", "Inconnu")
                alliance = p.get("A", "Aucune")
            else:
                if len(p) < 2:
                    continue
                rank = int(p[0])
                score = p[1]
                info = next((item for item in p if isinstance(item, dict)), {})
                name = info.get("N", "Inconnu")
                alliance = info.get("AN", "Aucune")

            pts_str = f"{score:,}".replace(",", " ")
            medal = "🥇" if rank == 1 else "🥈" if rank == 2 else "🥉" if rank == 3 else f"**#{rank}**"

            # Si c'est une alliance, on n'affiche pas la parenthèse avec le nom de l'alliance
            if is_alliance:
                description += f"{medal} **{name}** ➔ `{pts_str}` pts\n"
            else:
                description += f"{medal} **{name}** ({alliance}) ➔ `{pts_str}` pts\n"

        embed.description = description
        return embed

    @classement.command(name="statistics", description="Displays a live ranking for player statistics.")
    @app_commands.autocomplete(player=joueur_autocomplete)
    @app_commands.choices(
        statistic=[
            app_commands.Choice(name="Achievement points", value="achievements"),
            app_commands.Choice(name="Plunder points", value="plunder"),
            app_commands.Choice(name="Honor points", value="honor"),
            app_commands.Choice(name="Might points", value="might"),
            app_commands.Choice(name="Building Might", value="master"),
            app_commands.Choice(name="Legendary level", value="legendary"),
        ]
    )
    async def classement_statistics(self, interaction: discord.Interaction, statistic: str, player: str):
        await interaction.response.defer(thinking=True)

        langue, serveur_local = await get_server_config(interaction)
        serveur_api = self.servers_map.get(serveur_local.lower(), serveur_local)

        event_id = self.event_ids.get(statistic, 1)

        STAT_MAP = {
            "achievements": {
                "name": t(langue, "cal_stat_achievements", defaut="Points de Succès"),
                "emoji": "<:season:1523401590936174613>",
            },
            "plunder": {
                "name": t(langue, "cal_stat_plunder", defaut="Points de Pillage"),
                "emoji": "<:loot:1512439015570276553>",
            },
            "honor": {
                "name": t(langue, "cal_stat_honor", defaut="Points d'Honneur"),
                "emoji": "<:honor:1512573860204253214>",
            },
            "might": {
                "name": t(langue, "cal_stat_might", defaut="Points de Puissance"),
                "emoji": "<:pp1:1512438903821570160>",
            },
            "master": {
                "name": t(langue, "cal_stat_master", defaut="Puissance de Construction"),
                "emoji": "<:decoration:1532426465260605630>",
            },
            "legendary": {
                "name": t(langue, "cal_stat_legendary", defaut="Niveau Légendaire"),
                "emoji": "<:lvl:1512571152524906596>",
            },
        }
        stat_info = STAT_MAP.get(statistic, {"name": statistic.capitalize(), "emoji": "<:stats:1512517930490003726>"})

        cache = await get_cached_data(serveur_local)
        local_data = cache.get("players_data", {})
        p_info = None

        for p_name, data in local_data.items():
            if p_name.lower() == player.lower():
                p_info = data
                player = p_name
                break

        if not p_info:
            headers = await get_api_headers(interaction)
            async with self.bot.session.get(
                f"https://api.gge-tracker.com/api/v1/players/{urllib.parse.quote(player)}", headers=headers, timeout=5
            ) as r:
                if r.status == 200:
                    data = await r.json()
                    if isinstance(data, list) and data:
                        p_info = data[0]
                        player = p_info.get("player_name", player)

        if not p_info:
            return await interaction.followup.send(
                t(
                    langue,
                    "ev_err_player_not_found",
                    player=player,
                    defaut=f"❌ **{player}** est introuvable. Vérifie l'orthographe du pseudo.",
                )
            )

        lvl = int(p_info.get("level", p_info.get("lvl", 0)))

        if statistic in ["plunder", "legendary"]:
            tranche = 1
            nom_tranche = t(langue, "ev_bracket_all", defaut="Classement Global")
            bracket_icon = "<:icon_points:1512502439339888820>"
        else:
            if lvl < 20:
                tranche = 1
            elif lvl < 30:
                tranche = 2
            elif lvl < 40:
                tranche = 3
            elif lvl < 50:
                tranche = 4
            elif lvl < 70:
                tranche = 5
            else:
                tranche = 6

            nom_tranche_defaut = "Niveaux 70+" if tranche == 6 else "Tranche Classique"
            nom_tranche = t(langue, f"stat_bracket_{tranche}", defaut=nom_tranche_defaut)
            bracket_icon = "<:lvl:1512571152524906596>"

        all_players_raw = []
        player_found = False

        MAX_RANK = 5000
        PLAYERS_PER_PAGE = 5
        BATCH_SIZE = 20

        current_sv = 1

        async def fetch_chunk(sv_val):
            url = f"{self.ranking_api_url}/{serveur_api}/hgh/%22LT%22:{event_id},%22LID%22:{tranche},%22SV%22:%22{sv_val}%22"
            for _ in range(3):
                try:
                    async with self.bot.session.get(url, timeout=5) as r:
                        if r.status == 200:
                            return await r.json()
                except:
                    pass
                await asyncio.sleep(0.3)
            return None

        while current_sv <= MAX_RANK and not player_found:
            tasks = []
            for _ in range(BATCH_SIZE):
                if current_sv > MAX_RANK:
                    break
                tasks.append(fetch_chunk(current_sv))
                current_sv += PLAYERS_PER_PAGE

            if not tasks:
                break

            responses = await asyncio.gather(*tasks)
            batch_empty = True

            for jsonData in responses:
                if jsonData and str(jsonData.get("return_code")) == "0":
                    l_chunk = jsonData.get("content", {}).get("L", [])
                    if l_chunk:
                        all_players_raw.extend(l_chunk)
                        batch_empty = False

                        for p in l_chunk:
                            if len(p) >= 3 and p[2].get("N", "").lower() == player.lower():
                                player_found = True
                                break

            if batch_empty:
                break

        if not all_players_raw:
            return await interaction.followup.send(
                t(
                    langue,
                    "ev_rank_empty",
                    tranche=nom_tranche,
                    ev=stat_info["name"],
                    defaut=f"📭 Aucun classement trouvé pour la **{nom_tranche}**.",
                )
            )

        seen_players = set()
        all_players_clean = []
        for p in all_players_raw:
            if len(p) < 3:
                continue
            name = p[2].get("N", "")
            if name and name not in seen_players:
                seen_players.add(name)
                all_players_clean.append(p)

        all_players = sorted(all_players_clean, key=lambda x: int(x[0]))

        page_cible = 0
        chunks = [all_players[i : i + 10] for i in range(0, len(all_players), 10)]

        for i, chunk in enumerate(chunks):
            trouve_dans_page = False
            for p in chunk:
                if len(p) >= 3 and p[2].get("N", "").lower() == player.lower():
                    page_cible = i
                    trouve_dans_page = True
                    break
            if trouve_dans_page:
                break

        if not player_found:
            return await interaction.followup.send(
                t(
                    langue,
                    "ev_rank_not_in_top",
                    player=player,
                    limit=len(all_players),
                    tranche=nom_tranche,
                    defaut=f"📉 **{player}** n'a pas été trouvé dans le Top {len(all_players)} de la {nom_tranche}.",
                )
            )

        mot_classement = t(langue, "ev_word_rank", defaut="Classement")
        titre = f"{stat_info['emoji']} {mot_classement} {stat_info['name']}\n{bracket_icon} {nom_tranche}"

        # --- AJOUT DES COULEURS ---
        if statistic == "achievements":
            embed_color = discord.Color(0xEADA24)
        elif statistic == "plunder":
            embed_color = discord.Color(0x522E00)
        elif statistic == "honor":
            embed_color = discord.Color(0xFFFFFF)
        elif statistic == "might":
            embed_color = discord.Color(0xA70000)
        elif statistic == "master":
            embed_color = discord.Color(0xB93132)
        elif statistic == "legendary":
            embed_color = discord.Color(0xB3ECEC)
        else:
            embed_color = discord.Color.gold()

        embeds = []
        for i, chunk in enumerate(chunks):
            embed = self.format_page(chunk, i, langue, color=embed_color)
            embed.title = titre
            embed.set_footer(text=f"Page {i + 1}/{len(chunks)}")
            await setup_embed_footer(embed, interaction, langue)
            embeds.append(embed)

        view = PaginationView(embeds)

        if hasattr(view, "current_page"):
            view.current_page = page_cible
        if hasattr(view, "index"):
            view.index = page_cible
        if hasattr(view, "update_buttons"):
            view.update_buttons()

        await interaction.followup.send(embed=embeds[page_cible], view=view)
        await prompt_vote_if_lucky(interaction, probability_percent=5, langue=langue)

    @classement.command(name="event", description="Displays an event's live ranking.")
    @app_commands.autocomplete(player=joueur_autocomplete)
    @app_commands.choices(
        evenement=[
            app_commands.Choice(name="Nomad Invasion", value="nomads"),
            app_commands.Choice(name="War of the Realms", value="foreigners"),
            app_commands.Choice(name="Samurai Invasion", value="samurais"),
            app_commands.Choice(name="Bloodcrow Invasion", value="bloodcrows"),
            app_commands.Choice(name="Battle of Berimond", value="berimond"),
        ]
    )
    async def classement_joueur(self, interaction: discord.Interaction, evenement: str, player: str):
        await interaction.response.defer(thinking=True)

        langue, serveur_local = await get_server_config(interaction)
        serveur_api = self.servers_map.get(serveur_local.lower(), serveur_local)

        event_id = self.event_ids.get(evenement, 30 if evenement == "berimond" else 44)

        EVENT_MAP = {
            "nomads": {
                "name": t(langue, "cal_ev_nomad", defaut="Invasion des Nomades"),
                "emoji": "<:nomads:1512431070719774750>",
            },
            "foreigners": {
                "name": t(langue, "cal_ev_realms", defaut="Guerre des Royaumes"),
                "emoji": "<:war_realms:1512573773658980504>",
            },
            "samurais": {
                "name": t(langue, "cal_ev_samurai", defaut="Invasion des Samouraïs"),
                "emoji": "<:samurai:1512430844935929868>",
            },
            "bloodcrows": {
                "name": t(langue, "cal_ev_bloodcrow", defaut="Corbeaux de Sang"),
                "emoji": "<:bloodcrow:1512430942990368928>",
            },
            "berimond": {
                "name": t(langue, "cal_ev_berimond", defaut="Bataille de Bérimond"),
                "emoji": "<:berimond:1512430901756428390>",
            },
        }
        ev_info = EVENT_MAP.get(evenement, {"name": evenement.capitalize(), "emoji": "<:events4:1532431480398286878>"})

        cache = await get_cached_data(serveur_local)
        local_data = cache.get("players_data", {})
        p_info = None

        for p_name, data in local_data.items():
            if p_name.lower() == player.lower():
                p_info = data
                player = p_name
                break

        if not p_info:
            headers = await get_api_headers(interaction)
            async with self.bot.session.get(
                f"https://api.gge-tracker.com/api/v1/players/{urllib.parse.quote(player)}", headers=headers, timeout=5
            ) as r:
                if r.status == 200:
                    data = await r.json()
                    if isinstance(data, list) and data:
                        p_info = data[0]
                        player = p_info.get("player_name", player)

        if not p_info:
            return await interaction.followup.send(
                t(
                    langue,
                    "ev_err_player_not_found",
                    player=player,
                    defaut=f"❌ **{player}** est introuvable. Vérifie l'orthographe du pseudo.",
                )
            )

        lvl = int(p_info.get("level", p_info.get("lvl", 0)))
        leg_raw = (
            p_info.get("legendaryLevel")
            or p_info.get("legendary_level")
            or p_info.get("paragonLevel")
            or p_info.get("paragon_level")
            or 0
        )
        leg = int(leg_raw)

        if evenement == "berimond":
            if lvl < 70:
                possible_lids = [1, 2]  # 1 = Ursidae (Ours), 2 = Gerbrandt (Lion) [40-69]
            else:
                possible_lids = [3, 4]  # 3 = Ursidae (Ours), 4 = Gerbrandt (Lion) [70+]
        else:
            if lvl < 70:
                possible_lids = [1]
            else:
                if leg < 300:
                    possible_lids = [2]
                elif leg < 650:
                    possible_lids = [3]
                elif leg < 950:
                    possible_lids = [4]
                else:
                    possible_lids = [5]

        def extract_player_name(p):
            if isinstance(p, dict):
                return p.get("P", "")
            info = next((item for item in p if isinstance(item, dict)), {})
            return info.get("N", "")

        accumulated_data = {lid: [] for lid in possible_lids}
        found_lid = None
        player_found = False

        MAX_RANK = 1000
        PLAYERS_PER_PAGE = 5
        BATCH_SIZE = 20
        current_sv = 1

        async def fetch_chunk(sv_val, lid):
            url = (
                f"{self.ranking_api_url}/{serveur_api}/hgh/%22LT%22:{event_id},%22LID%22:{lid},%22SV%22:%22{sv_val}%22"
            )
            for _ in range(3):
                try:
                    async with self.bot.session.get(url, timeout=5) as r:
                        if r.status == 200:
                            return await r.json()
                except:
                    pass
                await asyncio.sleep(0.3)
            return None

        while current_sv <= MAX_RANK and not player_found:
            task_lids = []
            task_coros = []

            for lid in possible_lids:
                for i in range(BATCH_SIZE):
                    sv_val = current_sv + (i * PLAYERS_PER_PAGE)
                    if sv_val > MAX_RANK:
                        break
                    task_lids.append(lid)
                    task_coros.append(fetch_chunk(sv_val, lid))

            if not task_coros:
                break

            responses = await asyncio.gather(*task_coros)
            batch_empty = True

            for i, jsonData in enumerate(responses):
                lid = task_lids[i]
                if jsonData and str(jsonData.get("return_code")) == "0":
                    l_chunk = jsonData.get("content", {}).get("L", [])
                    if l_chunk:
                        accumulated_data[lid].extend(l_chunk)
                        batch_empty = False

                        if not player_found:
                            for p in l_chunk:
                                p_name = extract_player_name(p)
                                if p_name and p_name.lower() == player.lower():
                                    player_found = True
                                    found_lid = lid
                                    break

            if batch_empty:
                break
            current_sv += BATCH_SIZE * PLAYERS_PER_PAGE

        has_any_data = any(len(data) > 0 for data in accumulated_data.values())

        if not has_any_data:
            embed_empty = discord.Embed(
                title="📭 Classement indisponible",
                description=t(
                    langue,
                    "ev_rank_empty",
                    ev=ev_info["name"],
                    defaut=f"Aucun classement trouvé pour **{ev_info['name']}**.\n*L'événement n'est peut-être pas actif ou personne n'a marqué de points.*",
                ),
                color=discord.Color.light_grey(),
            )
            await setup_embed_footer(embed_empty, interaction, langue)
            return await interaction.followup.send(embed=embed_empty)

        if not player_found:
            if evenement == "berimond":
                lvl_str = "Légendaires (70+)" if lvl >= 70 else "Niveaux 40-69"
                embed_err = discord.Embed(
                    title="🔍 Joueur non trouvé dans Bérimond",
                    description=(
                        f"**{player}** n'apparaît pas dans le Top 1000 actuel (*{lvl_str}*).\n\n"
                        f"> 💡 **Causes possibles :**\n"
                        f"> • Le joueur n'a pas encore rejoint son camp (**<:berimond:1512430901756428390> Ursidae** ou **<:berimond:1512430901756428390> Gerbrandt**).\n"
                        f"> • Le joueur n'a pas encore marqué le moindre point.\n"
                        f"> • Le joueur est classé au-delà du Top 1000."
                    ),
                    color=discord.Color.gold(),
                )
                await setup_embed_footer(embed_err, interaction, langue)
                return await interaction.followup.send(embed=embed_err)
            else:
                nom_tranche_defaut = self.brackets_map.get(str(possible_lids[0]), f"Tranche {possible_lids[0]}")
                nom_tranche = t(langue, f"ev_bracket_{possible_lids[0]}", defaut=nom_tranche_defaut)
                embed_err = discord.Embed(
                    title="🔍 Joueur non trouvé",
                    description=t(
                        langue,
                        "ev_rank_not_in_top",
                        player=player,
                        limit=1000,
                        tranche=nom_tranche,
                        defaut=f"**{player}** n'a pas été trouvé dans le Top 1000 actuel de la tranche **{nom_tranche}**.",
                    ),
                    color=discord.Color.orange(),
                )
                await setup_embed_footer(embed_err, interaction, langue)
                return await interaction.followup.send(embed=embed_err)

        all_players_raw = accumulated_data[found_lid]
        mot_classement = t(langue, "ev_word_rank", defaut="Classement")

        if evenement == "berimond":
            BERIMOND_DETAILS = {
                1: ("Camp Ursidae (Ours) <:berimond:1512430901756428390>", "Niveaux 40-69"),
                2: ("Camp Gerbrandt (Lion) <:berimond:1512430901756428390>", "Niveaux 40-69"),
                3: ("Camp Ursidae (Ours) <:berimond:1512430901756428390>", "Légendaires (70+)"),
                4: ("Camp Gerbrandt (Lion) <:berimond:1512430901756428390>", "Légendaires (70+)"),
            }
            camp_txt, lvl_txt = BERIMOND_DETAILS.get(found_lid, ("Bérimond", "Global"))
            titre = f"<:berimond:1512430901756428390> Bataille de Bérimond — {camp_txt}\n<:lvl:1512571152524906596> {lvl_txt}"
        else:
            nom_tranche_defaut = self.brackets_map.get(str(found_lid), f"Tranche {found_lid}")
            nom_tranche = t(langue, f"ev_bracket_{found_lid}", defaut=nom_tranche_defaut)
            bracket_icon = "<:lvl:1512571152524906596>"
            titre = f"{ev_info['emoji']} {mot_classement} {ev_info['name']}\n{bracket_icon} {nom_tranche}"

        seen_players = set()
        all_players_clean = []
        for p in all_players_raw:
            p_name = extract_player_name(p)
            if p_name and p_name not in seen_players:
                seen_players.add(p_name)
                all_players_clean.append(p)

        all_players = sorted(
            all_players_clean, key=lambda x: int(x[0]) if not isinstance(x, dict) else int(x.get("R", 9999))
        )

        page_cible = 0
        chunks = [all_players[i : i + 10] for i in range(0, len(all_players), 10)]

        for i, chunk in enumerate(chunks):
            trouve_dans_page = False
            for p in chunk:
                if extract_player_name(p).lower() == player.lower():
                    page_cible = i
                    trouve_dans_page = True
                    break
            if trouve_dans_page:
                break

        if evenement == "berimond":
            if found_lid in [1, 3]:
                embed_color = discord.Color(0x0094FF)
            elif found_lid in [2, 4]:
                embed_color = discord.Color(0xFF0000)
        elif evenement == "nomads":
            embed_color = discord.Color(0xEDCB5A)
        elif evenement == "samurais":
            embed_color = discord.Color(0xD43C27)
        elif evenement == "bloodcrows":
            embed_color = discord.Color(0x670111)
        else:
            embed_color = discord.Color(0x49415D)

        embeds = []
        for i, chunk in enumerate(chunks):
            embed = self.format_page(chunk, i, langue, color=embed_color)
            embed.title = titre
            embed.set_footer(text=f"Page {i + 1}/{len(chunks)}")
            await setup_embed_footer(embed, interaction, langue)
            embeds.append(embed)

        view = PaginationView(embeds)
        if hasattr(view, "current_page"):
            view.current_page = page_cible
        if hasattr(view, "index"):
            view.index = page_cible
        if hasattr(view, "update_buttons"):
            view.update_buttons()

        await interaction.followup.send(embed=embeds[page_cible], view=view)
        await prompt_vote_if_lucky(interaction, probability_percent=5, langue=langue)

    @classement.command(name="gacha", description="Displays a player's live ranking for Gacha events")
    @app_commands.autocomplete(player=joueur_autocomplete)
    @app_commands.choices(
        evenement=[
            app_commands.Choice(name="Fatal Flora Trap", value="flora"),
            app_commands.Choice(name="The Enchanted Snowglobe", value="snowglobe"),
            app_commands.Choice(name="Summoning of the Hollow Moon", value="hollowmoon"),
            app_commands.Choice(name="Sand of Fortune", value="sandfortune"),
            app_commands.Choice(name="King's Banquet", value="banquet"),
            app_commands.Choice(name="Midnight Market", value="midnight"),
        ]
    )
    async def classement_gacha(self, interaction: discord.Interaction, evenement: str, player: str):
        await interaction.response.defer(thinking=True)

        langue, serveur_local = await get_server_config(interaction)
        serveur_api = self.servers_map.get(serveur_local.lower(), serveur_local)

        event_id = self.event_ids.get(evenement, 80)

        EVENT_MAP = {
            "flora": {
                "name": t(langue, "cal_ev_flora", defaut="Piège de la Flore Fatale"),
                "emoji": "<:FloraToken:1532427755671650465>",
            },
            "snowglobe": {
                "name": t(langue, "cal_ev_snowglobe", defaut="La Boule à Neige Enchantée"),
                "emoji": "<:FrozenCarrot:1532428873768374382>",
            },
            "hollowmoon": {
                "name": t(langue, "cal_ev_hollowmoon", defaut="Invocation de la Lune Creuse"),
                "emoji": "<:Moonegg:1532428876876091573>",
            },
            "sandfortune": {
                "name": t(langue, "cal_ev_sandfortune", defaut="Sables de la Fortune"),
                "emoji": "<:Orange:1532428875424989246>",
            },
            "banquet": {
                "name": t(langue, "cal_ev_banquet", defaut="Banquet du Roi"),
                "emoji": "<:Cake:1532428872639971380>",
            },
            "midnight": {
                "name": t(langue, "cal_ev_midnight", defaut="Marché de Minuit"),
                "emoji": "<:Midnight_key:1532429792413089835>",
            },
        }
        ev_info = EVENT_MAP.get(
            evenement, {"name": evenement.capitalize(), "emoji": "<:gacha_currency:1532431673830932612>"}
        )

        cache = await get_cached_data(serveur_local)
        local_data = cache.get("players_data", {})
        p_info = None

        for p_name, data in local_data.items():
            if p_name.lower() == player.lower():
                p_info = data
                player = p_name
                break

        if not p_info:
            headers = await get_api_headers(interaction)
            async with self.bot.session.get(
                f"https://api.gge-tracker.com/api/v1/players/{urllib.parse.quote(player)}", headers=headers, timeout=5
            ) as r:
                if r.status == 200:
                    data = await r.json()
                    if isinstance(data, list) and data:
                        p_info = data[0]
                        player = p_info.get("player_name", player)

        if not p_info:
            return await interaction.followup.send(
                t(
                    langue,
                    "ev_err_player_not_found",
                    player=player,
                    defaut=f"❌ **{player}** est introuvable. Vérifie l'orthographe du pseudo.",
                )
            )

        tranche = 1
        nom_tranche = t(langue, "ev_bracket_all", defaut="Classement Global")

        all_players_raw = []
        player_found = False

        MAX_RANK = 5000
        PLAYERS_PER_PAGE = 5
        BATCH_SIZE = 20

        current_sv = 1

        async def fetch_chunk(sv_val):
            url = f"{self.ranking_api_url}/{serveur_api}/hgh/%22LT%22:{event_id},%22LID%22:{tranche},%22SV%22:%22{sv_val}%22"
            for _ in range(3):
                try:
                    async with self.bot.session.get(url, timeout=5) as r:
                        if r.status == 200:
                            return await r.json()
                except:
                    pass
                await asyncio.sleep(0.3)
            return None

        while current_sv <= MAX_RANK and not player_found:
            tasks = []
            for _ in range(BATCH_SIZE):
                if current_sv > MAX_RANK:
                    break
                tasks.append(fetch_chunk(current_sv))
                current_sv += PLAYERS_PER_PAGE

            if not tasks:
                break

            responses = await asyncio.gather(*tasks)
            batch_empty = True

            for jsonData in responses:
                if jsonData and str(jsonData.get("return_code")) == "0":
                    l_chunk = jsonData.get("content", {}).get("L", [])
                    if l_chunk:
                        all_players_raw.extend(l_chunk)
                        batch_empty = False

                        for p in l_chunk:
                            if len(p) >= 3 and p[2].get("N", "").lower() == player.lower():
                                player_found = True
                                break

            if batch_empty:
                break

        if not all_players_raw:
            return await interaction.followup.send(
                t(
                    langue,
                    "ev_rank_empty",
                    tranche=nom_tranche,
                    ev=ev_info["name"],
                    defaut=f"📭 Aucun classement trouvé pour la **{nom_tranche}**.\n*{ev_info['name']} n'a peut-être pas encore commencé ou personne n'a marqué de points.*",
                )
            )

        seen_players = set()
        all_players_clean = []
        for p in all_players_raw:
            if len(p) < 3:
                continue
            name = p[2].get("N", "")
            if name and name not in seen_players:
                seen_players.add(name)
                all_players_clean.append(p)

        all_players = sorted(all_players_clean, key=lambda x: int(x[0]))

        page_cible = 0
        chunks = [all_players[i : i + 10] for i in range(0, len(all_players), 10)]

        for i, chunk in enumerate(chunks):
            trouve_dans_page = False
            for p in chunk:
                if len(p) >= 3 and p[2].get("N", "").lower() == player.lower():
                    page_cible = i
                    trouve_dans_page = True
                    break
            if trouve_dans_page:
                break

        if not player_found:
            return await interaction.followup.send(
                t(
                    langue,
                    "ev_rank_not_in_top",
                    player=player,
                    limit=len(all_players),
                    tranche=nom_tranche,
                    defaut=f"📉 **{player}** n'a pas été trouvé dans le Top {len(all_players)} actuel de la {nom_tranche}.",
                )
            )

        mot_classement = t(langue, "ev_word_rank", defaut="Classement")
        titre = (
            f"{ev_info['emoji']} {mot_classement} {ev_info['name']}\n<:icon_points:1512502439339888820> {nom_tranche}"
        )

        if evenement == "flora":
            embed_color = discord.Color(0x81C24A)
        elif evenement == "snowglobe":
            embed_color = discord.Color(0xFFFFFF)
        elif evenement == "hollowmoon":
            embed_color = discord.Color(0xFF8D00)
        elif evenement == "sandfortune":
            embed_color = discord.Color(0xFFE29C)
        elif evenement == "midnight":
            embed_color = discord.Color(0x282442)
        else:
            embed_color = discord.Color.gold()

        embeds = []
        for i, chunk in enumerate(chunks):
            embed = self.format_page(chunk, i, langue, color=embed_color)
            embed.title = titre
            embed.set_footer(text=f"Page {i + 1}/{len(chunks)}")
            await setup_embed_footer(embed, interaction, langue)
            embeds.append(embed)

        view = PaginationView(embeds)

        if hasattr(view, "current_page"):
            view.current_page = page_cible
        if hasattr(view, "index"):
            view.index = page_cible
        if hasattr(view, "update_buttons"):
            view.update_buttons()

        await interaction.followup.send(embed=embeds[page_cible], view=view)
        await prompt_vote_if_lucky(interaction, probability_percent=5, langue=langue)

    @classement.command(name="realms", description="Displays a player's ranking for cross-server events")
    @app_commands.autocomplete(player=joueur_autocomplete)
    @app_commands.choices(
        evenement=[
            app_commands.Choice(name="Outer Realms (Current)", value="realms_current"),
            app_commands.Choice(name="Outer Realms (Finished)", value="realms_finished"),
            app_commands.Choice(name="Beyond the Horizon", value="horizon"),
        ]
    )
    async def classement_realms(self, interaction: discord.Interaction, evenement: str, player: str):
        await interaction.response.defer(thinking=True)

        langue, serveur_local = await get_server_config(interaction)
        serveur_api = self.servers_map.get(serveur_local.lower(), serveur_local)

        if evenement == "realms_current":
            event_id = 62
        elif evenement == "realms_finished":
            event_id = self.event_ids.get("realms", 76)
        else:
            event_id = self.event_ids.get("horizon", 78)

        EVENT_MAP = {
            "realms_current": {
                "name": t(langue, "cal_ev_realms_current", defaut="Royaumes Extérieurs (En cours)"),
                "emoji": "<:outerrealmsicon:1512573734404231329>",
            },
            "realms_finished": {
                "name": t(langue, "cal_ev_realms_finished", defaut="Royaumes Extérieurs (Terminé)"),
                "emoji": "<:outerrealmsicon:1512573734404231329>",
            },
            "horizon": {
                "name": t(langue, "cal_ev_horizon", defaut="Au-delà de l'Horizon"),
                "emoji": "<:bth:1512574690441302026>",
            },
        }
        ev_info = EVENT_MAP.get(evenement, {"name": evenement.capitalize(), "emoji": "<:events4:1532431480398286878>"})

        cache = await get_cached_data(serveur_local)
        local_data = cache.get("players_data", {})
        p_info = None

        for p_name, data in local_data.items():
            if p_name.lower() == player.lower():
                p_info = data
                player = p_name
                break

        if not p_info:
            headers = await get_api_headers(interaction)
            async with self.bot.session.get(
                f"https://api.gge-tracker.com/api/v1/players/{urllib.parse.quote(player)}", headers=headers, timeout=5
            ) as r:
                if r.status == 200:
                    data = await r.json()
                    if isinstance(data, list) and data:
                        p_info = data[0]
                        player = p_info.get("player_name", player)

        if not p_info:
            return await interaction.followup.send(
                t(
                    langue,
                    "ev_err_player_not_found",
                    player=player,
                    defaut=f"❌ **{player}** est introuvable. Vérifie l'orthographe du pseudo.",
                )
            )

        suffixe = serveur_local.replace("e4k_", "").upper()
        target_name_exact = f"{player}_{suffixe}".lower()

        tranche = 1
        nom_tranche = t(langue, "ev_bracket_cross_server", defaut="Classement Inter-Serveurs")

        all_players_raw = []
        player_found = False

        MAX_RANK = 10000
        PLAYERS_PER_PAGE = 5
        BATCH_SIZE = 20

        current_sv = 1

        async def fetch_chunk(sv_val):
            url = f"{self.ranking_api_url}/{serveur_api}/hgh/%22LT%22:{event_id},%22LID%22:{tranche},%22SV%22:%22{sv_val}%22"
            for _ in range(3):
                try:
                    async with self.bot.session.get(url, timeout=5) as r:
                        if r.status == 200:
                            return await r.json()
                except:
                    pass
                await asyncio.sleep(0.3)
            return None

        while current_sv <= MAX_RANK and not player_found:
            tasks = []
            for _ in range(BATCH_SIZE):
                if current_sv > MAX_RANK:
                    break
                tasks.append(fetch_chunk(current_sv))
                current_sv += PLAYERS_PER_PAGE

            if not tasks:
                break

            responses = await asyncio.gather(*tasks)
            batch_empty = True

            for jsonData in responses:
                if jsonData and str(jsonData.get("return_code")) == "0":
                    l_chunk = jsonData.get("content", {}).get("L", [])
                    if l_chunk:
                        all_players_raw.extend(l_chunk)
                        batch_empty = False

                        for p in l_chunk:
                            info = next((item for item in p if isinstance(item, dict)), {})
                            api_name = info.get("N", "").lower()
                            if api_name and (
                                api_name == target_name_exact
                                or api_name == player.lower()
                                or api_name.startswith(f"{player.lower()}_")
                            ):
                                player_found = True
                                break

            if batch_empty:
                break

        if not all_players_raw:
            return await interaction.followup.send(
                t(
                    langue,
                    "ev_rank_empty",
                    tranche=nom_tranche,
                    ev=ev_info["name"],
                    defaut=f"📭 Aucun classement trouvé pour la **{nom_tranche}**.",
                )
            )

        seen_players = set()
        all_players_clean = []
        for p in all_players_raw:
            info = next((item for item in p if isinstance(item, dict)), {})
            name = info.get("N", "")
            if name and name not in seen_players:
                seen_players.add(name)
                all_players_clean.append(p)

        all_players = sorted(all_players_clean, key=lambda x: int(x[0]))

        page_cible = 0
        chunks = [all_players[i : i + 10] for i in range(0, len(all_players), 10)]

        for i, chunk in enumerate(chunks):
            trouve_dans_page = False
            for p in chunk:
                info = next((item for item in p if isinstance(item, dict)), {})
                api_name = info.get("N", "").lower()
                if api_name and (
                    api_name == target_name_exact
                    or api_name == player.lower()
                    or api_name.startswith(f"{player.lower()}_")
                ):
                    page_cible = i
                    trouve_dans_page = True
                    break
            if trouve_dans_page:
                break

        if not player_found:
            return await interaction.followup.send(
                t(
                    langue,
                    "ev_rank_not_in_top",
                    player=player,
                    limit=len(all_players),
                    tranche=nom_tranche,
                    defaut=f"📉 **{player}** n'a pas été trouvé dans le Top {len(all_players)}.",
                )
            )

        mot_classement = t(langue, "ev_word_rank", defaut="Classement")
        titre = (
            f"{ev_info['emoji']} {mot_classement} {ev_info['name']}\n<:icon_points:1512502439339888820> {nom_tranche}"
        )

        if evenement == "horizon":
            embed_color = discord.Color(0x4A7160)
        else:
            embed_color = discord.Color(0xF25500)

        embeds = []
        for i, chunk in enumerate(chunks):
            embed = self.format_page(chunk, i, langue, color=embed_color)
            embed.title = titre
            embed.set_footer(text=f"Page {i + 1}/{len(chunks)}")
            await setup_embed_footer(embed, interaction, langue)
            embeds.append(embed)

        view = PaginationView(embeds)

        if hasattr(view, "current_page"):
            view.current_page = page_cible
        if hasattr(view, "index"):
            view.index = page_cible
        if hasattr(view, "update_buttons"):
            view.update_buttons()

        await interaction.followup.send(embed=embeds[page_cible], view=view)
        await prompt_vote_if_lucky(interaction, probability_percent=5, langue=langue)

    @classement.command(name="league", description="Displays a player's ranking for Season and League events")
    @app_commands.autocomplete(player=joueur_autocomplete)
    @app_commands.choices(
        evenement=[
            app_commands.Choice(name="Season / Festival", value="season"),
            app_commands.Choice(name="Kingdom League", value="league"),
        ]
    )
    async def classement_league(self, interaction: discord.Interaction, evenement: str, player: str):
        await interaction.response.defer(thinking=True)

        langue, serveur_local = await get_server_config(interaction)
        serveur_api = self.servers_map.get(serveur_local.lower(), serveur_local)

        event_id = self.event_ids.get(evenement, 53)

        EVENT_MAP = {
            "season": {
                "name": t(langue, "cal_ev_season", defaut="Saison / Festival"),
                "emoji": "<:season:1523401590936174613>",
            },
            "league": {
                "name": t(langue, "cal_ev_league", defaut="Ligue du Royaume"),
                "emoji": "<:league:1523402089873539192>",
            },
        }
        ev_info = EVENT_MAP.get(
            evenement, {"name": evenement.capitalize(), "emoji": "<:leagueicon:1532432050231972030>"}
        )

        cache = await get_cached_data(serveur_local)
        local_data = cache.get("players_data", {})
        p_info = None

        for p_name, data in local_data.items():
            if p_name.lower() == player.lower():
                p_info = data
                player = p_name
                break

        if not p_info:
            headers = await get_api_headers(interaction)
            async with self.bot.session.get(
                f"https://api.gge-tracker.com/api/v1/players/{urllib.parse.quote(player)}", headers=headers, timeout=5
            ) as r:
                if r.status == 200:
                    data = await r.json()
                    if isinstance(data, list) and data:
                        p_info = data[0]
                        player = p_info.get("player_name", player)

        if not p_info:
            return await interaction.followup.send(
                t(
                    langue,
                    "ev_err_player_not_found",
                    player=player,
                    defaut=f"❌ **{player}** est introuvable. Vérifie l'orthographe du pseudo.",
                )
            )

        lvl = int(p_info.get("level", p_info.get("lvl", 0)))
        leg_raw = (
            p_info.get("legendaryLevel")
            or p_info.get("legendary_level")
            or p_info.get("paragonLevel")
            or p_info.get("paragon_level")
            or 0
        )
        leg = int(leg_raw)

        api_command = "hgh"
        pagination_param = "SV"
        is_string_val = True

        if evenement == "league":
            tranche = 1
            nom_tranche = t(langue, "ev_bracket_all", defaut="Classement Global")
            bracket_icon = "<:lvl:1512571152524906596>"

        elif evenement == "season":
            api_command = "llsp"
            pagination_param = "R"
            is_string_val = False

            if lvl < 70:
                return await interaction.followup.send(
                    t(
                        langue,
                        "ev_err_season_level",
                        defaut="❌ Le classement de saison n'est disponible que pour les joueurs de niveau Légendaire (70+).",
                    )
                )

            if leg < 200:
                tranche = 1
                nom_tranche_defaut = "Légendaire 1 - 199"
            elif leg < 650:
                tranche = 2
                nom_tranche_defaut = "Légendaire 200 - 649"
            elif leg < 950:
                tranche = 3
                nom_tranche_defaut = "Légendaire 650 - 949"
            else:
                tranche = 4
                nom_tranche_defaut = "Légendaire 950+"

            nom_tranche = t(langue, f"season_bracket_{tranche}", defaut=nom_tranche_defaut)
            bracket_icon = "<:lvl:1512571152524906596>"

        all_players_raw = []
        player_found = False

        MAX_RANK = 1500
        PLAYERS_PER_PAGE = 10
        BATCH_SIZE = 20

        current_sv = 1

        async def fetch_chunk(sv_val):
            if is_string_val:
                url = f"{self.ranking_api_url}/{serveur_api}/{api_command}/%22LT%22:{event_id},%22LID%22:{tranche},%22{pagination_param}%22:%22{sv_val}%22"
            else:
                url = f"{self.ranking_api_url}/{serveur_api}/{api_command}/%22LT%22:{event_id},%22LID%22:{tranche},%22{pagination_param}%22:{sv_val}"

            for _ in range(3):
                try:
                    async with self.bot.session.get(url, timeout=5) as r:
                        if r.status == 200:
                            return await r.json()
                except:
                    pass
                await asyncio.sleep(0.3)
            return None

        while current_sv <= MAX_RANK and not player_found:
            tasks = []
            for _ in range(BATCH_SIZE):
                if current_sv > MAX_RANK:
                    break
                tasks.append(fetch_chunk(current_sv))
                current_sv += PLAYERS_PER_PAGE

            if not tasks:
                break

            responses = await asyncio.gather(*tasks)
            batch_empty = True

            for jsonData in responses:
                if jsonData and str(jsonData.get("return_code")) == "0":
                    l_chunk = jsonData.get("content", {}).get("L", [])
                    if l_chunk:
                        all_players_raw.extend(l_chunk)
                        batch_empty = False

                        for p in l_chunk:
                            if isinstance(p, dict):
                                api_name = p.get("P", "").lower()
                            else:
                                info = next((item for item in p if isinstance(item, dict)), {})
                                api_name = info.get("N", "").lower()

                            if api_name == player.lower():
                                player_found = True
                                break

            if batch_empty:
                break

        if not all_players_raw:
            return await interaction.followup.send(
                t(
                    langue,
                    "ev_rank_empty",
                    tranche=nom_tranche,
                    ev=ev_info["name"],
                    defaut=f"📭 Aucun classement trouvé pour la **{nom_tranche}**.\n*{ev_info['name']} n'a peut-être pas encore commencé.*",
                )
            )

        seen_players = set()
        all_players_clean = []
        for p in all_players_raw:
            if isinstance(p, dict):
                name = p.get("P", "")
                rank_val = int(p.get("R", 999999))
            else:
                info = next((item for item in p if isinstance(item, dict)), {})
                name = info.get("N", "")
                rank_val = int(p[0]) if len(p) > 0 else 999999

            if name and name not in seen_players:
                seen_players.add(name)
                all_players_clean.append((rank_val, p))

        all_players_sorted = sorted(all_players_clean, key=lambda x: x[0])
        all_players = [item[1] for item in all_players_sorted]

        page_cible = 0
        chunks = [all_players[i : i + 10] for i in range(0, len(all_players), 10)]

        for i, chunk in enumerate(chunks):
            trouve_dans_page = False
            for p in chunk:
                if isinstance(p, dict):
                    api_name = p.get("P", "").lower()
                else:
                    info = next((item for item in p if isinstance(item, dict)), {})
                    api_name = info.get("N", "").lower()

                if api_name == player.lower():
                    page_cible = i
                    trouve_dans_page = True
                    break
            if trouve_dans_page:
                break

        if not player_found:
            return await interaction.followup.send(
                t(
                    langue,
                    "ev_rank_not_in_top",
                    player=player,
                    limit=len(all_players),
                    tranche=nom_tranche,
                    defaut=f"📉 **{player}** n'a pas été trouvé dans le Top {len(all_players)}.",
                )
            )

        mot_classement = t(langue, "ev_word_rank", defaut="Classement")
        titre = f"{ev_info['emoji']} {mot_classement} {ev_info['name']}\n{bracket_icon} {nom_tranche}"

        if evenement == "season":
            embed_color = discord.Color(0xDDAADD)
        else:
            embed_color = discord.Color(0x004D25)  # League

        embeds = []
        for i, chunk in enumerate(chunks):
            embed = self.format_page(chunk, i, langue, color=embed_color)
            embed.title = titre
            embed.set_footer(text=f"Page {i + 1}/{len(chunks)}")
            await setup_embed_footer(embed, interaction, langue)
            embeds.append(embed)

        view = PaginationView(embeds)

        if hasattr(view, "current_page"):
            view.current_page = page_cible
        if hasattr(view, "index"):
            view.index = page_cible
        if hasattr(view, "update_buttons"):
            view.update_buttons()

        await interaction.followup.send(embed=embeds[page_cible], view=view)
        await prompt_vote_if_lucky(interaction, probability_percent=5, langue=langue)

    @classement.command(name="contests", description="Displays a player's ranking for specific contests")
    @app_commands.autocomplete(player=joueur_autocomplete)
    @app_commands.choices(
        evenement=[
            app_commands.Choice(name="Shapeshifters", value="shapeshifters"),
            app_commands.Choice(name="Nobility Contest", value="nobility"),
            app_commands.Choice(name="Wheel of Unimaginable Affluence", value="woa"),
            app_commands.Choice(name="Imperial patronage", value="patronage"),
        ]
    )
    async def classement_contests(self, interaction: discord.Interaction, evenement: str, player: str):
        await interaction.response.defer(thinking=True)

        langue, serveur_local = await get_server_config(interaction)
        serveur_api = self.servers_map.get(serveur_local.lower(), serveur_local)

        event_id = self.event_ids.get(evenement, 60)

        EVENT_MAP = {
            "shapeshifters": {
                "name": t(langue, "cal_ev_shape", defaut="Les Métamorphes"),
                "emoji": "<:Shapeshifter:1532432592450752552>",
            },
            "nobility": {
                "name": t(langue, "cal_ev_nobility", defaut="Concours de Noblesse"),
                "emoji": "<:nobility_contest:1532432846461993303>",
            },
            "woa": {
                "name": t(langue, "cal_ev_woa", defaut="Guerre des Alliances"),
                "emoji": "<:woa_points:1512573716259668098>",
            },
            "patronage": {
                "name": t(langue, "cal_ev_patronage", defaut="Patronage"),
                "emoji": "<:patronage:1514704230106140874>",
            },
        }
        ev_info = EVENT_MAP.get(evenement, {"name": evenement.capitalize(), "emoji": "🏆"})

        cache = await get_cached_data(serveur_local)
        local_data = cache.get("players_data", {})
        p_info = None

        for p_name, data in local_data.items():
            if p_name.lower() == player.lower():
                p_info = data
                player = p_name
                break

        if not p_info:
            headers = await get_api_headers(interaction)
            async with self.bot.session.get(
                f"https://api.gge-tracker.com/api/v1/players/{urllib.parse.quote(player)}", headers=headers, timeout=5
            ) as r:
                if r.status == 200:
                    data = await r.json()
                    if isinstance(data, list) and data:
                        p_info = data[0]
                        player = p_info.get("player_name", player)

        if not p_info:
            return await interaction.followup.send(
                t(
                    langue,
                    "ev_err_player_not_found",
                    player=player,
                    defaut=f"❌ **{player}** est introuvable. Vérifie l'orthographe du pseudo.",
                )
            )

        lvl = int(p_info.get("level", p_info.get("lvl", 0)))
        leg_raw = (
            p_info.get("legendaryLevel")
            or p_info.get("legendary_level")
            or p_info.get("paragonLevel")
            or p_info.get("paragon_level")
            or 0
        )
        leg = int(leg_raw)

        tranche = 1
        nom_tranche = t(langue, "ev_bracket_all", defaut="Classement Global")
        bracket_icon = "<:icon_points:1512502439339888820>"

        if evenement == "shapeshifters":
            if lvl < 70:
                tranche = 1
            else:
                if leg < 300:
                    tranche = 2
                elif leg < 650:
                    tranche = 3
                elif leg < 950:
                    tranche = 4
                else:
                    tranche = 5
            nom_tranche_defaut = self.brackets_map.get(str(tranche), f"Tranche {tranche}")
            nom_tranche = t(langue, f"ev_bracket_{tranche}", defaut=nom_tranche_defaut)
            bracket_icon = "<:lvl:1512571152524906596>"

        elif evenement == "nobility":
            if lvl < 15:
                tranche = 1
                nom_tranche_defaut = "Niveaux 10-14"
            elif lvl < 20:
                tranche = 2
                nom_tranche_defaut = "Niveaux 15-19"
            elif lvl < 25:
                tranche = 3
                nom_tranche_defaut = "Niveaux 20-24"
            elif lvl < 30:
                tranche = 4
                nom_tranche_defaut = "Niveaux 25-29"
            elif lvl < 50:
                tranche = 5
                nom_tranche_defaut = "Niveaux 30-49"
            elif lvl < 70:
                tranche = 6
                nom_tranche_defaut = "Niveaux 50-69"
            else:
                tranche = 7
                nom_tranche_defaut = "Légendaires (70+)"

            nom_tranche = t(langue, f"nobility_bracket_{tranche}", defaut=nom_tranche_defaut)
            bracket_icon = "<:lvl:1512571152524906596>"

        all_players_raw = []
        player_found = False

        MAX_RANK = 5000
        PLAYERS_PER_PAGE = 5
        BATCH_SIZE = 20

        current_sv = 1

        async def fetch_chunk(sv_val):
            url = f"{self.ranking_api_url}/{serveur_api}/hgh/%22LT%22:{event_id},%22LID%22:{tranche},%22SV%22:%22{sv_val}%22"
            for _ in range(3):
                try:
                    async with self.bot.session.get(url, timeout=5) as r:
                        if r.status == 200:
                            return await r.json()
                except:
                    pass
                await asyncio.sleep(0.3)
            return None

        while current_sv <= MAX_RANK and not player_found:
            tasks = []
            for _ in range(BATCH_SIZE):
                if current_sv > MAX_RANK:
                    break
                tasks.append(fetch_chunk(current_sv))
                current_sv += PLAYERS_PER_PAGE

            if not tasks:
                break

            responses = await asyncio.gather(*tasks)
            batch_empty = True

            for jsonData in responses:
                if jsonData and str(jsonData.get("return_code")) == "0":
                    l_chunk = jsonData.get("content", {}).get("L", [])
                    if l_chunk:
                        all_players_raw.extend(l_chunk)
                        batch_empty = False

                        for p in l_chunk:
                            if len(p) >= 3 and p[2].get("N", "").lower() == player.lower():
                                player_found = True
                                break

            if batch_empty:
                break

        if not all_players_raw:
            return await interaction.followup.send(
                t(
                    langue,
                    "ev_rank_empty",
                    tranche=nom_tranche,
                    ev=ev_info["name"],
                    defaut=f"📭 Aucun classement trouvé pour la **{nom_tranche}**.\n*{ev_info['name']} n'est peut-être pas actif.*",
                )
            )

        seen_players = set()
        all_players_clean = []
        for p in all_players_raw:
            if len(p) < 3:
                continue
            name = p[2].get("N", "")
            if name and name not in seen_players:
                seen_players.add(name)
                all_players_clean.append(p)

        all_players = sorted(all_players_clean, key=lambda x: int(x[0]))

        page_cible = 0
        chunks = [all_players[i : i + 10] for i in range(0, len(all_players), 10)]

        for i, chunk in enumerate(chunks):
            trouve_dans_page = False
            for p in chunk:
                if len(p) >= 3 and p[2].get("N", "").lower() == player.lower():
                    page_cible = i
                    trouve_dans_page = True
                    break
            if trouve_dans_page:
                break

        if not player_found:
            return await interaction.followup.send(
                t(
                    langue,
                    "ev_rank_not_in_top",
                    player=player,
                    limit=len(all_players),
                    tranche=nom_tranche,
                    defaut=f"📉 **{player}** n'a pas été trouvé dans le Top {len(all_players)} actuel de la {nom_tranche}.",
                )
            )

        mot_classement = t(langue, "ev_word_rank", defaut="Classement")
        titre = f"{ev_info['emoji']} {mot_classement} {ev_info['name']}\n{bracket_icon} {nom_tranche}"

        if evenement == "shapeshifters":
            embed_color = discord.Color(0x2B211C)
        elif evenement == "nobility":
            embed_color = discord.Color(0xFFBF5B)
        elif evenement == "woa":
            embed_color = discord.Color(0x512DA8)
        else:
            embed_color = discord.Color.gold()

        embeds = []
        for i, chunk in enumerate(chunks):
            embed = self.format_page(chunk, i, langue, color=embed_color)
            embed.title = titre
            embed.set_footer(text=f"Page {i + 1}/{len(chunks)}")
            await setup_embed_footer(embed, interaction, langue)
            embeds.append(embed)

        view = PaginationView(embeds)

        if hasattr(view, "current_page"):
            view.current_page = page_cible
        if hasattr(view, "index"):
            view.index = page_cible
        if hasattr(view, "update_buttons"):
            view.update_buttons()

        await interaction.followup.send(embed=embeds[page_cible], view=view)
        await prompt_vote_if_lucky(interaction, probability_percent=5, langue=langue)

    @classement.command(name="alliance", description="Displays live rankings and statistics for alliances")
    @app_commands.choices(
        categorie=[
            app_commands.Choice(name="Honor Points", value="alliance_honor"),
            app_commands.Choice(name="Might Points", value="alliance_might"),
            app_commands.Choice(name="Command Points", value="alliance_command"),
            app_commands.Choice(name="Cargo Points", value="alliance_cargo"),
            app_commands.Choice(name="Nomad Invasion", value="alliance_nomad"),
            app_commands.Choice(name="War of the Realms", value="alliance_foreigners"),
            app_commands.Choice(name="Samurai Invasion", value="alliance_samurais"),
            app_commands.Choice(name="Bloodcrow Invasion", value="alliance_bloodcrows"),
            app_commands.Choice(name="Kingdom League", value="alliance_league"),
            app_commands.Choice(name="Beyond the Horizon", value="alliance_horizon"),
        ]
    )
    @app_commands.autocomplete(alliance_name=alliance_autocomplete)
    async def classement_alliance(self, interaction: discord.Interaction, categorie: str, alliance_name: str):
        await interaction.response.defer(thinking=True)

        langue, serveur_local = await get_server_config(interaction)
        serveur_api = self.servers_map.get(serveur_local.lower(), serveur_local)

        event_id = self.event_ids.get(categorie, 10)

        CAT_MAP = {
            "alliance_honor": {
                "name": t(langue, "cal_stat_honor", defaut="Points d'Honneur"),
                "emoji": "<:honor:1512573860204253214>",
                "color": discord.Color(0xFFFFFF),
            },
            "alliance_might": {
                "name": t(langue, "cal_stat_might", defaut="Points de Puissance"),
                "emoji": "<:pp2:1512571027119538335>",
                "color": discord.Color(0xBB270D),
            },
            "alliance_command": {
                "name": t(langue, "cal_stat_command", defaut="Points de Commandement"),
                "emoji": "<:Porteurs_de_bouclier:1512574622271279114>",
                "color": discord.Color(0xAFAFAF),
            },
            "alliance_cargo": {
                "name": t(langue, "cal_stat_cargo", defaut="Points de Fret"),
                "emoji": "<:pointscargo:1512161268411273429>",
                "color": discord.Color(0x84CED1),
            },
            "alliance_nomad": {
                "name": t(langue, "cal_ev_nomad", defaut="Invasion des Nomades"),
                "emoji": "<:nomads:1512431070719774750>",
                "color": discord.Color(0xEDCB5A),
            },
            "alliance_foreigners": {
                "name": t(langue, "cal_ev_realms", defaut="Guerre des Royaumes"),
                "emoji": "<:war_realms:1512573773658980504>",
                "color": discord.Color(0x49415D),
            },
            "alliance_samurais": {
                "name": t(langue, "cal_ev_samurai", defaut="Invasion des Samouraïs"),
                "emoji": "<:samurai:1512430844935929868>",
                "color": discord.Color(0xD43C27),
            },
            "alliance_bloodcrows": {
                "name": t(langue, "cal_ev_bloodcrow", defaut="Corbeaux de Sang"),
                "emoji": "<:bloodcrow:1512430942990368928>",
                "color": discord.Color(0x670111),
            },
            "alliance_league": {
                "name": t(langue, "cal_ev_league", defaut="Ligue du Royaume"),
                "emoji": "<:league:1523402089873539192>",
                "color": discord.Color(0x004D25),
            },
            "alliance_horizon": {
                "name": t(langue, "cal_ev_horizon", defaut="Au-delà de l'Horizon"),
                "emoji": "<:bth:1512574690441302026>",
                "color": discord.Color(0x4A7160),
            },
        }

        cat_info = CAT_MAP.get(
            categorie,
            {
                "name": categorie.capitalize(),
                "emoji": "<:alliance_icon:1512574688415580242>",
                "color": discord.Color.gold(),
            },
        )
        embed_color = cat_info["color"]

        tranche = 1
        nom_tranche = t(langue, "ev_bracket_alliance", defaut="Classement Global Alliances")

        def extract_name(p):
            if isinstance(p, dict):
                return p.get("P", p.get("N", ""))
            info = next((item for item in p if isinstance(item, dict)), {})
            return info.get("N", "")

        all_alliances_raw = []
        alliance_found = False

        MAX_RANK = 1000
        PLAYERS_PER_PAGE = 5
        BATCH_SIZE = 20
        current_sv = 1

        async def fetch_chunk(sv_val):
            url = f"{self.ranking_api_url}/{serveur_api}/hgh/%22LT%22:{event_id},%22LID%22:{tranche},%22SV%22:%22{sv_val}%22"
            for _ in range(3):
                try:
                    async with self.bot.session.get(url, timeout=5) as r:
                        if r.status == 200:
                            return await r.json()
                except:
                    pass
                await asyncio.sleep(0.3)
            return None

        while current_sv <= MAX_RANK and not alliance_found:
            tasks = []
            for _ in range(BATCH_SIZE):
                if current_sv > MAX_RANK:
                    break
                tasks.append(fetch_chunk(current_sv))
                current_sv += PLAYERS_PER_PAGE

            if not tasks:
                break

            responses = await asyncio.gather(*tasks)
            batch_empty = True

            for jsonData in responses:
                if jsonData and str(jsonData.get("return_code")) == "0":
                    l_chunk = jsonData.get("content", {}).get("L", [])
                    if l_chunk:
                        all_alliances_raw.extend(l_chunk)
                        batch_empty = False

                        for p in l_chunk:
                            a_name = extract_name(p)
                            if a_name and a_name.lower() == alliance_name.lower():
                                alliance_found = True
                                break

            if batch_empty:
                break

        if not all_alliances_raw:
            embed_empty = discord.Embed(
                title="📭 Classement indisponible",
                description=t(
                    langue,
                    "ev_rank_empty",
                    ev=cat_info["name"],
                    defaut=f"Aucun classement trouvé pour **{cat_info['name']}**.\n*L'événement n'est peut-être pas actif ou personne n'a marqué de points.*",
                ),
                color=discord.Color.light_grey(),
            )
            await setup_embed_footer(embed_empty, interaction, langue)
            return await interaction.followup.send(embed=embed_empty)

        if not alliance_found:
            embed_err = discord.Embed(
                title="🔍 Alliance non trouvée",
                description=t(
                    langue,
                    "ev_rank_not_in_top",
                    player=alliance_name,
                    limit=MAX_RANK,
                    tranche=nom_tranche,
                    defaut=f"L'alliance **{alliance_name}** n'a pas été trouvée dans le Top {MAX_RANK} actuel pour **{cat_info['name']}**.",
                ),
                color=discord.Color.orange(),
            )
            await setup_embed_footer(embed_err, interaction, langue)
            return await interaction.followup.send(embed=embed_err)

        seen_alliances = set()
        all_alliances_clean = []
        for p in all_alliances_raw:
            a_name = extract_name(p)
            if a_name and a_name not in seen_alliances:
                seen_alliances.add(a_name)
                all_alliances_clean.append(p)

        all_alliances = sorted(
            all_alliances_clean, key=lambda x: int(x[0]) if not isinstance(x, dict) else int(x.get("R", 9999))
        )

        page_cible = 0
        chunks = [all_alliances[i : i + 10] for i in range(0, len(all_alliances), 10)]

        for i, chunk in enumerate(chunks):
            trouve_dans_page = False
            for p in chunk:
                if extract_name(p).lower() == alliance_name.lower():
                    page_cible = i
                    trouve_dans_page = True
                    break
            if trouve_dans_page:
                break

        titre = f"{cat_info['emoji']} Classement {cat_info['name']}\n🛡️ {nom_tranche}"

        embeds = []
        for i, chunk in enumerate(chunks):
            embed = self.format_page(chunk, i, langue, color=embed_color, is_alliance=True)
            embed.title = titre
            embed.set_footer(text=f"Page {i + 1}/{len(chunks)}")
            await setup_embed_footer(embed, interaction, langue)
            embeds.append(embed)

        view = PaginationView(embeds)

        if hasattr(view, "current_page"):
            view.current_page = page_cible
        if hasattr(view, "index"):
            view.index = page_cible
        if hasattr(view, "update_buttons"):
            view.update_buttons()

        await interaction.followup.send(embed=embeds[page_cible], view=view)
        await prompt_vote_if_lucky(interaction, probability_percent=5, langue=langue)


async def setup(bot):
    await bot.add_cog(ClassementCog(bot))

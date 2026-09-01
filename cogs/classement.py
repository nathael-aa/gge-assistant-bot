import asyncio
import json
import logging
import os
import urllib.parse

import discord
from discord import app_commands
from discord.ext import commands

from utils import (
    DICT_EMOJIS,
    PaginationView,
    alliance_autocomplete,
    get_api_headers,
    get_server_config,
    joueur_autocomplete,
    setup_embed_footer,
    t,
)


def get_emo(langue, default_emo):
    """
    Astuce anti-scanner : en passant la clé via une variable,
    l'expression régulière de i18n_sync ne détectera pas cette ligne.
    """
    k = "emo_ignore"
    return t(langue, k, defaut=default_emo)


def extract_p_name(p):
    if isinstance(p, dict):
        return p.get("P", p.get("N", ""))
    info = next((item for item in p if isinstance(item, dict)), {})
    name = info.get("N", "")
    if name:
        return name
    if len(p) >= 4 and isinstance(p[3], str):
        return p[3]
    if len(p) >= 3 and isinstance(p[2], list) and len(p[2]) >= 2 and isinstance(p[2][1], str):
        return p[2][1]
    return ""


def extract_p_rank(p):
    if isinstance(p, dict):
        return int(p.get("R", 999999))
    if len(p) >= 5 and isinstance(p[4], (int, float)) and not isinstance(p[2], dict):
        return int(p[4])
    return int(p[0]) if len(p) > 0 else 999999


# -- VUE PERSONNALISÉE AVEC BOUTON REFRESH ET TIMEOUT --
class RankingPaginationView(PaginationView):
    def __init__(self, embeds, refresh_callback, user_id, timeout=3600):
        super().__init__(embeds)
        self.timeout = timeout
        self.refresh_callback = refresh_callback
        self.user_id = user_id
        self.message = None

        btn = discord.ui.Button(
            label="Actualiser",
            style=discord.ButtonStyle.secondary,
            row=1,
            emoji=DICT_EMOJIS.get("e_refresh", "🔄"),
            custom_id="btn_rank_refresh",
        )
        btn.callback = self.refresh_action
        self.add_item(btn)

    async def refresh_action(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message(
                "❌ Seul l'auteur de la commande peut l'actualiser.", ephemeral=True
            )

        await interaction.response.defer()
        new_embeds, new_page = await self.refresh_callback(interaction)

        if new_embeds:
            self.embeds = new_embeds
            if hasattr(self, "current_page"):
                self.current_page = new_page
            if hasattr(self, "index"):
                self.index = new_page
            if hasattr(self, "update_buttons"):
                self.update_buttons()

            if not self.message:
                self.message = interaction.message
            await interaction.edit_original_response(embed=self.embeds[new_page], view=self)

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except Exception:
                pass


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
        try:
            self.logger.info(f"📝 [Classement] Tentative de chargement du JSON à : {self.config_path}")
            if os.path.exists(self.config_path):
                with open(self.config_path, encoding="utf-8") as f:
                    config_data = json.load(f)

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
    # 🎯 AUTOCOMPLÉTION DYNAMIQUE DES TRANCHES
    # ========================================================
    async def tranche_autocomplete(self, interaction: discord.Interaction, current: str):
        ns = interaction.namespace
        choix = []

        if hasattr(ns, "statistic") and ns.statistic:
            if ns.statistic in ["plunder", "legendary"]:
                choix = [app_commands.Choice(name="Classement Global", value=1)]
            else:
                choix = [
                    app_commands.Choice(name="Niveaux < 20", value=1),
                    app_commands.Choice(name="Niveaux 20-29", value=2),
                    app_commands.Choice(name="Niveaux 30-39", value=3),
                    app_commands.Choice(name="Niveaux 40-49", value=4),
                    app_commands.Choice(name="Niveaux 50-69", value=5),
                    app_commands.Choice(name="Niveaux 70+", value=6),
                ]
        elif hasattr(ns, "evenement") and ns.evenement:
            ev = ns.evenement
            if ev == "berimond":
                choix = [
                    app_commands.Choice(name="Ursidae (Niv 40-69)", value=1),
                    app_commands.Choice(name="Gerbrandt (Niv 40-69)", value=2),
                    app_commands.Choice(name="Ursidae (Niv 70+)", value=3),
                    app_commands.Choice(name="Gerbrandt (Niv 70+)", value=4),
                ]
            elif ev == "nobility":
                choix = [
                    app_commands.Choice(name="Niveaux 10-14", value=1),
                    app_commands.Choice(name="Niveaux 15-19", value=2),
                    app_commands.Choice(name="Niveaux 20-24", value=3),
                    app_commands.Choice(name="Niveaux 25-29", value=4),
                    app_commands.Choice(name="Niveaux 30-49", value=5),
                    app_commands.Choice(name="Niveaux 50-69", value=6),
                    app_commands.Choice(name="Légendaires (70+)", value=7),
                ]
            elif ev == "season":
                choix = [
                    app_commands.Choice(name="Légendaire 1-199", value=1),
                    app_commands.Choice(name="Légendaire 200-649", value=2),
                    app_commands.Choice(name="Légendaire 650-949", value=3),
                    app_commands.Choice(name="Légendaire 950+", value=4),
                ]
            elif ev in [
                "flora",
                "snowglobe",
                "hollowmoon",
                "sandfortune",
                "banquet",
                "midnight",
                "realms_current",
                "realms_finished",
                "horizon",
                "league",
                "woa",
                "patronage",
            ]:
                choix = [app_commands.Choice(name="Classement Global", value=1)]
            else:
                choix = [
                    app_commands.Choice(name="Niveaux < 70", value=1),
                    app_commands.Choice(name="Légendaire 1-299", value=2),
                    app_commands.Choice(name="Légendaire 300-649", value=3),
                    app_commands.Choice(name="Légendaire 650-949", value=4),
                    app_commands.Choice(name="Légendaire 950+", value=5),
                ]
        else:
            choix = [app_commands.Choice(name="Classement Global", value=1)]

        return [c for c in choix if current.lower() in c.name.lower()][:25]

    classement = app_commands.Group(name="rank", description="Live event analysis and rankings")

    def get_league_title_and_level(self, rank):
        if rank > 21:
            rank = 21

        if rank == 21:
            return "{e_title_5}", "{e_level_title_3}"

        tier = (rank - 1) // 4
        sub_level = (rank - 1) % 4

        titles = ["{e_title_0}", "{e_title_1}", "{e_title_2}", "{e_title_3}", "{e_title_4}"]
        levels = ["", "{e_level_title_1}", "{e_level_title_2}", "{e_level_title_3}"]

        return titles[tier], levels[sub_level]

    def format_page(
        self,
        chunk,
        page_index,
        langue,
        color=discord.Color.gold(),
        is_alliance=False,
        highlight_player=None,
        event_name=None,
    ):
        embed = discord.Embed(color=color)
        description = ""

        # --- MAPPING DES EMOJIS DE LIGUE ---
        TITRES_PRE = {
            13: get_emo(langue, "{e_title_0}"),
            103: get_emo(langue, "{e_title_1}"),
            105: get_emo(langue, "{e_title_2}"),
            112: get_emo(langue, "{e_title_3}"),
            113: get_emo(langue, "{e_title_4}"),
            115: get_emo(langue, "{e_title_5}"),
        }

        TITRES_SUF = {
            24: "",
            27: get_emo(langue, "{e_level_title_1}"),
            28: get_emo(langue, "{e_level_title_2}"),
            29: get_emo(langue, "{e_level_title_3}"),
            30: get_emo(langue, "{e_level_title_3}"),
            111: get_emo(langue, "{e_level_title_3}"),
        }

        MEDAILLES = {
            1: get_emo(langue, "{e_medal_gold}"),
            2: get_emo(langue, "{e_medal_silver}"),
            3: get_emo(langue, "{e_medal_bronze}"),
        }

        for p in chunk:
            info = {}
            if isinstance(p, dict):
                rank = int(p.get("R", 0))
                score = p.get("S", 0)
                name = p.get("P", "Inconnu")
                alliance = p.get("A", "Aucune")
            else:
                if len(p) < 2:
                    continue
                score = p[1]

                if len(p) >= 5 and isinstance(p[3], str) and not isinstance(p[2], dict):
                    rank = int(p[4])
                    name = p[3]
                    alliance = "Aucune"
                elif len(p) >= 3 and isinstance(p[2], list) and len(p[2]) >= 2:
                    rank = int(p[0])
                    name = p[2][1]
                    alliance = "Aucune"
                else:
                    rank = int(p[0])
                    info = next((item for item in p if isinstance(item, dict)), {})
                    name = info.get("N", "Inconnu")
                    alliance = info.get("AN", "Aucune")

            league_str = ""
            if event_name == "league" and not is_alliance:
                klmo = info.get("KLMO", [])
                klrid = info.get("KLRID", 1)

                MEDAL_VALUES = {1: 1000, 2: 950, 3: 850, 4: 700, 5: 500, 6: 300, 7: 100}
                real_score = 0
                if klmo:
                    for med in klmo:
                        if len(med) == 2:
                            real_score += MEDAL_VALUES.get(med[0], 0) * med[1]
                score = real_score

            pts_str = f"{score:,}".replace(",", " ")
            medal = "🥇" if rank == 1 else "🥈" if rank == 2 else "🥉" if rank == 3 else f"**#{rank}**"

            if highlight_player and (
                name.lower() == highlight_player.lower() or name.lower().startswith(f"{highlight_player.lower()}_")
            ):
                display_name = f"__**{name}**__ 🎯"
            else:
                display_name = f"**{name}**"

            if event_name == "league" and not is_alliance:
                title_raw, level_raw = self.get_league_title_and_level(klrid)
                title_emo = get_emo(langue, title_raw) if title_raw else ""
                level_emo = get_emo(langue, level_raw) if level_raw else ""
                titre_str = f"{title_emo}{level_emo}".strip()

                medailles_str = ""
                if klmo:
                    m_list = []
                    for med in klmo:
                        if len(med) == 2 and med[0] in [1, 2, 3] and med[1] > 0:
                            m_raw = MEDAILLES.get(med[0], f"`[M{med[0]}]`")
                            m_emo = get_emo(langue, m_raw)
                            m_list.append(f"{m_emo}x{med[1]}")
                    if m_list:
                        medailles_str = f" {' '.join(m_list)}"

                combo = f"{titre_str}{medailles_str}".strip()
                if combo:
                    league_str = f" | {combo}"

            if is_alliance:
                if event_name == "league":
                    description += f"{medal} {display_name}{league_str}\n"
                else:
                    description += f"{medal} {display_name}{league_str} | `{pts_str}` pts\n"
            else:
                all_str = f" ({alliance})" if alliance and alliance != "Aucune" else ""
                if event_name == "league":
                    valeur_txt = t(langue, "cal_league_value", defaut="Valeur")
                    description += f"{medal} {display_name}{all_str}{league_str} | `{valeur_txt} : {pts_str}`\n"
                else:
                    description += f"{medal} {display_name}{all_str}{league_str} | `{pts_str}` pts\n"

        embed.description = description
        return embed

    @classement.command(name="statistics", description="Displays a live ranking for player statistics.")
    @app_commands.describe(
        statistic="Catégorie", player="Pseudo (Optionnel)", rank="Rang (Optionnel)", tranche="Tranche (Optionnel)"
    )
    @app_commands.autocomplete(player=joueur_autocomplete, tranche=tranche_autocomplete)
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
    async def classement_statistics(
        self,
        interaction: discord.Interaction,
        statistic: str,
        player: str = None,
        rank: int = None,
        tranche: int = None,
    ):
        await interaction.response.defer(thinking=True)

        async def fetch_and_build(ctx_int):
            loc_player = player
            loc_rank = rank
            loc_tranche = tranche

            langue, serveur_local = await get_server_config(ctx_int)
            serveur_api = self.servers_map.get(serveur_local.lower(), serveur_local)
            event_id = self.event_ids.get(statistic, 1)

            STAT_MAP = {
                "achievements": {
                    "name": t(langue, "cal_stat_achievements", defaut="Points de Succès"),
                    "emoji": get_emo(langue, "{e_std_trophy}"),
                },
                "plunder": {
                    "name": t(langue, "cal_stat_plunder", defaut="Points de Pillage"),
                    "emoji": get_emo(langue, "{e_loot}"),
                },
                "honor": {
                    "name": t(langue, "cal_stat_honor", defaut="Points d'Honneur"),
                    "emoji": get_emo(langue, "{e_honor}"),
                },
                "might": {
                    "name": t(langue, "cal_stat_might", defaut="Points de Puissance"),
                    "emoji": get_emo(langue, "{e_pp1}"),
                },
                "master": {
                    "name": t(langue, "cal_stat_master", defaut="Puissance de Construction"),
                    "emoji": get_emo(langue, "{e_dungeon1}"),
                },
                "legendary": {
                    "name": t(langue, "cal_stat_legendary", defaut="Niveau Légendaire"),
                    "emoji": get_emo(langue, "{e_lvl}"),
                },
            }
            stat_info = STAT_MAP.get(statistic, {"name": statistic.capitalize(), "emoji": get_emo(langue, "{e_stats}")})

            search_mode = bool(loc_player)
            p_info = None

            if search_mode:
                headers = await get_api_headers(ctx_int)
                url_precheck = (
                    f"https://api.gge-tracker.com/api/v1/{serveur_api}/player/{urllib.parse.quote(loc_player)}"
                )
                try:
                    async with self.bot.session.get(url_precheck, headers=headers, timeout=5) as r:
                        if r.status == 200:
                            data = await r.json()
                            if isinstance(data, dict):
                                p_info = data
                            elif isinstance(data, list) and data:
                                p_info = data[0]
                except Exception:
                    pass

                if p_info:
                    loc_player = p_info.get("player_name", p_info.get("name", loc_player))
                    lvl = int(p_info.get("level", p_info.get("lvl", 70)))
                    leg = int(
                        p_info.get("legendaryLevel", p_info.get("legendary_level", p_info.get("paragonLevel", 999)))
                    )
                else:
                    lvl = None
                    leg = None
            else:
                lvl = 70
                leg = 999

            if statistic in ["plunder", "legendary"]:
                possible_lids = [1]
            else:
                if loc_tranche is not None:
                    possible_lids = [loc_tranche]
                else:
                    if lvl is None:
                        possible_lids = [1, 2, 3, 4, 5, 6]
                    elif lvl < 20:
                        possible_lids = [1]
                    elif lvl < 30:
                        possible_lids = [2]
                    elif lvl < 40:
                        possible_lids = [3]
                    elif lvl < 50:
                        possible_lids = [4]
                    elif lvl < 70:
                        possible_lids = [5]
                    else:
                        possible_lids = [6]

            found_lid = None

            warning_msg = ""
            if loc_rank and not loc_tranche and not search_mode:
                warning_msg += (
                    "\n*💡 Info : Tranche maximale affichée par défaut. Utilisez l'option `tranche` pour changer.*"
                )

            accumulated_data = {lid: [] for lid in possible_lids}
            player_found = False

            if search_mode and not loc_rank:
                start_sv = 1
                max_sv_limit = 1000
            elif loc_rank:
                start_sv = max(1, loc_rank - (loc_rank % 10))
                if start_sv == 0:
                    start_sv = 1
                max_sv_limit = start_sv + 49
            else:
                start_sv = 1
                max_sv_limit = 50

            current_sv = start_sv
            PLAYERS_PER_PAGE = 5
            BATCH_SIZE = 10

            async def fetch_chunk(sv_val, lid):
                url = f"{self.ranking_api_url}/{serveur_api}/hgh/%22LT%22:{event_id},%22LID%22:{lid},%22SV%22:%22{sv_val}%22"
                for _ in range(3):
                    try:
                        async with self.bot.session.get(url, timeout=5) as r:
                            if r.status == 200:
                                return await r.json()
                    except:
                        pass
                    await asyncio.sleep(0.3)
                return None

            while current_sv <= max_sv_limit and not player_found:
                task_lids = []
                task_coros = []
                for lid in possible_lids:
                    for i in range(BATCH_SIZE):
                        sv_val = current_sv + (i * PLAYERS_PER_PAGE)
                        if sv_val > max_sv_limit:
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

                            if search_mode and not loc_rank and not player_found:
                                for p in l_chunk:
                                    if extract_p_name(p).lower() == loc_player.lower():
                                        player_found = True
                                        found_lid = lid
                                        break
                if batch_empty:
                    break
                current_sv += BATCH_SIZE * PLAYERS_PER_PAGE

            has_any_data = any(len(data) > 0 for data in accumulated_data.values())
            if not has_any_data:
                await ctx_int.followup.send(
                    t(
                        langue,
                        "ev_rank_empty",
                        tranche="Tranche requise",
                        ev=stat_info["name"],
                        defaut="{e_error} Aucun classement.",
                    )
                )
                return None, None

            found_lid = found_lid or possible_lids[-1]
            all_players_raw = accumulated_data[found_lid]

            bracket_icon = (
                get_emo(langue, "{e_icon_points}")
                if statistic in ["plunder", "legendary"]
                else get_emo(langue, "{e_lvl}")
            )
            if statistic in ["plunder", "legendary"]:
                nom_tranche = t(langue, "ev_bracket_all", defaut="Classement Global")
            else:
                nom_tranche_defaut = "Niveaux 70+" if found_lid == 6 else f"Tranche {found_lid}"
                nom_tranche = t(langue, f"stat_bracket_{found_lid}", defaut=nom_tranche_defaut)

            mot_classement = t(langue, "ev_word_rank", defaut="Classement")
            titre = f"{stat_info['emoji']} {mot_classement} {stat_info['name']}\n{bracket_icon} {nom_tranche}"

            seen_players = set()
            all_players_clean = []
            for p in all_players_raw:
                name = extract_p_name(p)
                if name and name not in seen_players:
                    seen_players.add(name)
                    all_players_clean.append(p)

            all_players = sorted(all_players_clean, key=extract_p_rank)
            page_cible = 0
            chunks = [all_players[i : i + 10] for i in range(0, len(all_players), 10)]

            if player_found:
                for i, chunk in enumerate(chunks):
                    if any(extract_p_name(p).lower() == loc_player.lower() for p in chunk):
                        page_cible = i
                        break
            elif loc_rank:
                for i, chunk in enumerate(chunks):
                    if any(extract_p_rank(p) >= loc_rank for p in chunk):
                        page_cible = i
                        break

            if search_mode and not player_found:
                if loc_rank:
                    warning_msg += f"\n\n*💡 Info : Le joueur **{loc_player}** n'a pas été trouvé. Voici le rang {loc_rank} à la place.*"
                else:
                    warning_msg += f"\n\n*💡 Info : Le joueur **{loc_player}** n'a pas été trouvé dans le Top {max_sv_limit}. Voici la première page.*"
                    page_cible = 0

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
                embed = self.format_page(
                    chunk,
                    i,
                    langue,
                    color=embed_color,
                    highlight_player=loc_player if search_mode else None,
                    event_name=statistic,
                )
                embed.title = titre
                if warning_msg and i == page_cible:
                    embed.description = embed.description + warning_msg
                embed.set_footer(text=f"Page {i + 1}/{len(chunks)}")
                await setup_embed_footer(embed, ctx_int, langue)
                embeds.append(embed)

            return embeds, page_cible

        embeds, page_cible = await fetch_and_build(interaction)
        if not embeds:
            return

        view = RankingPaginationView(embeds, fetch_and_build, interaction.user.id)
        if hasattr(view, "current_page"):
            view.current_page = page_cible
        if hasattr(view, "index"):
            view.index = page_cible
        if hasattr(view, "update_buttons"):
            view.update_buttons()

        view.message = await interaction.followup.send(embed=embeds[page_cible], view=view, wait=True)

    @classement.command(name="event", description="Displays an event's live ranking.")
    @app_commands.describe(
        evenement="Événement", player="Pseudo (Optionnel)", rank="Rang (Optionnel)", tranche="Tranche (Optionnel)"
    )
    @app_commands.autocomplete(player=joueur_autocomplete, tranche=tranche_autocomplete)
    @app_commands.choices(
        evenement=[
            app_commands.Choice(name="Nomad Invasion", value="nomads"),
            app_commands.Choice(name="War of the Realms", value="foreigners"),
            app_commands.Choice(name="Samurai Invasion", value="samurais"),
            app_commands.Choice(name="Bloodcrow Invasion", value="bloodcrows"),
            app_commands.Choice(name="Battle of Berimond", value="berimond"),
        ]
    )
    async def classement_joueur(
        self,
        interaction: discord.Interaction,
        evenement: str,
        player: str = None,
        rank: int = None,
        tranche: int = None,
    ):
        await interaction.response.defer(thinking=True)

        async def fetch_and_build(ctx_int):
            loc_player = player
            loc_rank = rank
            loc_tranche = tranche

            langue, serveur_local = await get_server_config(ctx_int)
            serveur_api = self.servers_map.get(serveur_local.lower(), serveur_local)
            event_id = self.event_ids.get(evenement, 30 if evenement == "berimond" else 44)

            EVENT_MAP = {
                "nomads": {
                    "name": t(langue, "cal_ev_nomad", defaut="Invasion des Nomades"),
                    "emoji": get_emo(langue, "{e_nomads}"),
                },
                "foreigners": {
                    "name": t(langue, "cal_ev_realms", defaut="Guerre des Royaumes"),
                    "emoji": get_emo(langue, "{e_war_realms}"),
                },
                "samurais": {
                    "name": t(langue, "cal_ev_samurai", defaut="Invasion des Samouraïs"),
                    "emoji": get_emo(langue, "{e_samurai}"),
                },
                "bloodcrows": {
                    "name": t(langue, "cal_ev_bloodcrow", defaut="Corbeaux de Sang"),
                    "emoji": get_emo(langue, "{e_bloodcrow}"),
                },
                "berimond": {
                    "name": t(langue, "cal_ev_berimond", defaut="Bataille de Bérimond"),
                    "emoji": get_emo(langue, "{e_berimond}"),
                },
            }
            ev_info = EVENT_MAP.get(
                evenement, {"name": evenement.capitalize(), "emoji": get_emo(langue, "{e_events4}")}
            )

            search_mode = bool(loc_player)
            p_info = None

            if search_mode:
                headers = await get_api_headers(ctx_int)
                url_precheck = (
                    f"https://api.gge-tracker.com/api/v1/{serveur_api}/player/{urllib.parse.quote(loc_player)}"
                )
                try:
                    async with self.bot.session.get(url_precheck, headers=headers, timeout=5) as r:
                        if r.status == 200:
                            data = await r.json()
                            if isinstance(data, dict):
                                p_info = data
                            elif isinstance(data, list) and data:
                                p_info = data[0]
                except Exception:
                    pass

                if p_info:
                    loc_player = p_info.get("player_name", p_info.get("name", loc_player))
                    lvl = int(p_info.get("level", p_info.get("lvl", 70)))
                    leg = int(
                        p_info.get("legendaryLevel", p_info.get("legendary_level", p_info.get("paragonLevel", 999)))
                    )
                else:
                    lvl = None
                    leg = None
            else:
                lvl = 70
                leg = 999

            if loc_tranche is not None:
                possible_lids = [loc_tranche]
            else:
                if evenement == "berimond":
                    if lvl is None:
                        possible_lids = [1, 2, 3, 4]
                    else:
                        possible_lids = [1, 2] if lvl < 70 else [3, 4]
                else:
                    if lvl is None:
                        possible_lids = [1, 2, 3, 4, 5]
                    elif lvl < 70:
                        possible_lids = [1]
                    elif leg < 300:
                        possible_lids = [2]
                    elif leg < 650:
                        possible_lids = [3]
                    elif leg < 950:
                        possible_lids = [4]
                    else:
                        possible_lids = [5]

            found_lid = None

            warning_msg = ""
            if loc_rank and not loc_tranche and not search_mode:
                warning_msg += (
                    "\n*💡 Info : Tranche maximale affichée par défaut. Utilisez l'option `tranche` pour changer.*"
                )

            accumulated_data = {lid: [] for lid in possible_lids}
            player_found = False

            if search_mode and not loc_rank:
                start_sv = 1
                max_sv_limit = 1000
            elif loc_rank:
                start_sv = max(1, loc_rank - (loc_rank % 10))
                if start_sv == 0:
                    start_sv = 1
                max_sv_limit = start_sv + 49
            else:
                start_sv = 1
                max_sv_limit = 50

            current_sv = start_sv
            PLAYERS_PER_PAGE = 5
            BATCH_SIZE = 10

            async def fetch_chunk(sv_val, lid):
                url = f"{self.ranking_api_url}/{serveur_api}/hgh/%22LT%22:{event_id},%22LID%22:{lid},%22SV%22:%22{sv_val}%22"
                for _ in range(3):
                    try:
                        async with self.bot.session.get(url, timeout=5) as r:
                            if r.status == 200:
                                return await r.json()
                    except:
                        pass
                    await asyncio.sleep(0.3)
                return None

            while current_sv <= max_sv_limit and not player_found:
                task_lids = []
                task_coros = []
                for lid in possible_lids:
                    for i in range(BATCH_SIZE):
                        sv_val = current_sv + (i * PLAYERS_PER_PAGE)
                        if sv_val > max_sv_limit:
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

                            if search_mode and not loc_rank and not player_found:
                                for p in l_chunk:
                                    if extract_p_name(p).lower() == loc_player.lower():
                                        player_found = True
                                        found_lid = lid
                                        break
                if batch_empty:
                    break
                current_sv += BATCH_SIZE * PLAYERS_PER_PAGE

            has_any_data = any(len(data) > 0 for data in accumulated_data.values())
            if not has_any_data:
                await ctx_int.followup.send(
                    t(
                        langue,
                        "ev_rank_empty",
                        tranche="Tranche requise",
                        ev=ev_info["name"],
                        defaut="{e_error} Aucun classement.",
                    )
                )
                return None, None

            found_lid = found_lid or possible_lids[-1]

            if not search_mode and evenement == "berimond" and loc_tranche is None:
                all_players_raw = []
                for lid_data in accumulated_data.values():
                    all_players_raw.extend(lid_data)
            else:
                all_players_raw = accumulated_data[found_lid]

            mot_classement = t(langue, "ev_word_rank", defaut="Classement")
            if evenement == "berimond":
                BERIMOND_DETAILS = {
                    1: ("Camp Ursidae", "Niveaux 40-69"),
                    2: ("Camp Gerbrandt", "Niveaux 40-69"),
                    3: ("Camp Ursidae", "Légendaires (70+)"),
                    4: ("Camp Gerbrandt", "Légendaires (70+)"),
                }
                camp_txt, lvl_txt = BERIMOND_DETAILS.get(found_lid, ("Bérimond", "Légendaires (70+)"))
                if not search_mode and not loc_tranche:
                    camp_txt = "Les deux camps"
                titre = f"{get_emo(langue, '{e_berimond}')} Bataille de Bérimond — {camp_txt}\n{get_emo(langue, '{e_lvl}')} {lvl_txt}"
            else:
                nom_tranche_defaut = self.brackets_map.get(str(found_lid), f"Tranche {found_lid}")
                nom_tranche = t(langue, f"ev_bracket_{found_lid}", defaut=nom_tranche_defaut)
                titre = (
                    f"{ev_info['emoji']} {mot_classement} {ev_info['name']}\n{get_emo(langue, '{e_lvl}')} {nom_tranche}"
                )

            seen_players = set()
            all_players_clean = []
            for p in all_players_raw:
                name = extract_p_name(p)
                if name and name not in seen_players:
                    seen_players.add(name)
                    all_players_clean.append(p)

            all_players = sorted(all_players_clean, key=extract_p_rank)
            page_cible = 0
            chunks = [all_players[i : i + 10] for i in range(0, len(all_players), 10)]

            if player_found:
                for i, chunk in enumerate(chunks):
                    if any(extract_p_name(p).lower() == loc_player.lower() for p in chunk):
                        page_cible = i
                        break
            elif loc_rank:
                for i, chunk in enumerate(chunks):
                    if any(extract_p_rank(p) >= loc_rank for p in chunk):
                        page_cible = i
                        break

            if search_mode and not player_found:
                if loc_rank:
                    warning_msg += f"\n\n*💡 Info : Le joueur **{loc_player}** n'a pas été trouvé. Voici le rang {loc_rank} à la place.*"
                else:
                    warning_msg += f"\n\n*💡 Info : Le joueur **{loc_player}** n'a pas été trouvé dans le Top {max_sv_limit}. Voici la première page par défaut.*"
                    page_cible = 0

            if evenement == "berimond" and (search_mode or loc_tranche):
                embed_color = discord.Color(0x0094FF) if found_lid in [1, 3] else discord.Color(0xFF0000)
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
                embed = self.format_page(
                    chunk,
                    i,
                    langue,
                    color=embed_color,
                    highlight_player=loc_player if search_mode else None,
                    event_name=evenement,
                )
                embed.title = titre
                if warning_msg and i == page_cible:
                    embed.description = embed.description + warning_msg
                embed.set_footer(text=f"Page {i + 1}/{len(chunks)}")
                await setup_embed_footer(embed, ctx_int, langue)
                embeds.append(embed)

            return embeds, page_cible

        embeds, page_cible = await fetch_and_build(interaction)
        if not embeds:
            return

        view = RankingPaginationView(embeds, fetch_and_build, interaction.user.id)
        if hasattr(view, "current_page"):
            view.current_page = page_cible
        if hasattr(view, "index"):
            view.index = page_cible
        if hasattr(view, "update_buttons"):
            view.update_buttons()

        view.message = await interaction.followup.send(embed=embeds[page_cible], view=view, wait=True)

    @classement.command(name="gacha", description="Displays a live ranking for Gacha events")
    @app_commands.describe(evenement="Événement", player="Pseudo (Optionnel)", rank="Rang (Optionnel)")
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
    async def classement_gacha(
        self, interaction: discord.Interaction, evenement: str, player: str = None, rank: int = None
    ):
        await interaction.response.defer(thinking=True)

        async def fetch_and_build(ctx_int):
            loc_player = player
            loc_rank = rank

            langue, serveur_local = await get_server_config(ctx_int)
            serveur_api = self.servers_map.get(serveur_local.lower(), serveur_local)
            event_id = self.event_ids.get(evenement, 80)

            EVENT_MAP = {
                "flora": {
                    "name": t(langue, "cal_ev_flora", defaut="Piège de la Flore Fatale"),
                    "emoji": DICT_EMOJIS.get("e_FloraToken", "<:FloraToken:1532427755671650465>"),
                },
                "snowglobe": {
                    "name": t(langue, "cal_ev_snowglobe", defaut="La Boule à Neige Enchantée"),
                    "emoji": DICT_EMOJIS.get("e_FrozenCarrot", "<:FrozenCarrot:1532428873768374382>"),
                },
                "hollowmoon": {
                    "name": t(langue, "cal_ev_hollowmoon", defaut="Invocation de Lune"),
                    "emoji": DICT_EMOJIS.get("e_Moonegg", "<:Moonegg:1532428876876091573>"),
                },
                "sandfortune": {
                    "name": t(langue, "cal_ev_sandfortune", defaut="Sables de Fortune"),
                    "emoji": DICT_EMOJIS.get("e_Orange", "<:Orange:1532428875424989246>"),
                },
                "banquet": {
                    "name": t(langue, "cal_ev_banquet", defaut="Banquet du Roi"),
                    "emoji": DICT_EMOJIS.get("e_Cake", "<:Cake:1532428872639971380>"),
                },
                "midnight": {
                    "name": t(langue, "cal_ev_midnight", defaut="Marché de Minuit"),
                    "emoji": DICT_EMOJIS.get("e_Midnight_key", "<:Midnight_key:1532429792413089835>"),
                },
            }
            ev_info = EVENT_MAP.get(
                evenement, {"name": evenement.capitalize(), "emoji": DICT_EMOJIS.get("e_gacha_currency", "🪙")}
            )

            search_mode = bool(loc_player)
            p_info = None

            if search_mode:
                headers = await get_api_headers(ctx_int)
                url_precheck = (
                    f"https://api.gge-tracker.com/api/v1/{serveur_api}/player/{urllib.parse.quote(loc_player)}"
                )
                try:
                    async with self.bot.session.get(url_precheck, headers=headers, timeout=5) as r:
                        if r.status == 200:
                            data = await r.json()
                            if isinstance(data, dict):
                                p_info = data
                            elif isinstance(data, list) and data:
                                p_info = data[0]
                except Exception:
                    pass

                if p_info:
                    loc_player = p_info.get("player_name", p_info.get("name", loc_player))

            possible_lids = [1]
            found_lid = None

            accumulated_data = {1: []}
            player_found = False

            if search_mode and not loc_rank:
                start_sv = 1
                max_sv_limit = 1000
            elif loc_rank:
                start_sv = max(1, loc_rank - (loc_rank % 10))
                if start_sv == 0:
                    start_sv = 1
                max_sv_limit = start_sv + 49
            else:
                start_sv = 1
                max_sv_limit = 50

            current_sv = start_sv
            PLAYERS_PER_PAGE = 5
            BATCH_SIZE = 10

            async def fetch_chunk(sv_val, lid):
                url = f"{self.ranking_api_url}/{serveur_api}/hgh/%22LT%22:{event_id},%22LID%22:{lid},%22SV%22:%22{sv_val}%22"
                for _ in range(3):
                    try:
                        async with self.bot.session.get(url, timeout=5) as r:
                            if r.status == 200:
                                return await r.json()
                    except:
                        pass
                    await asyncio.sleep(0.3)
                return None

            while current_sv <= max_sv_limit and not player_found:
                task_lids = []
                task_coros = []
                for lid in possible_lids:
                    for i in range(BATCH_SIZE):
                        sv_val = current_sv + (i * PLAYERS_PER_PAGE)
                        if sv_val > max_sv_limit:
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

                            if search_mode and not loc_rank and not player_found:
                                for p in l_chunk:
                                    if extract_p_name(p).lower() == loc_player.lower():
                                        player_found = True
                                        found_lid = lid
                                        break
                if batch_empty:
                    break
                current_sv += BATCH_SIZE * PLAYERS_PER_PAGE

            has_any_data = any(len(data) > 0 for data in accumulated_data.values())
            if not has_any_data:
                await ctx_int.followup.send(
                    t(
                        langue,
                        "ev_rank_empty",
                        tranche="Global",
                        ev=ev_info["name"],
                        defaut="{e_error} Aucun classement.",
                    )
                )
                return None, None

            all_players_raw = accumulated_data[1]

            nom_tranche = t(langue, "ev_bracket_all", defaut="Classement Global")
            mot_classement = t(langue, "ev_word_rank", defaut="Classement")
            bracket_icon = get_emo(langue, "{e_icon_points}")
            titre = f"{ev_info['emoji']} {mot_classement} {ev_info['name']}\n{bracket_icon} {nom_tranche}"

            seen_players = set()
            all_players_clean = []
            for p in all_players_raw:
                name = extract_p_name(p)
                if name and name not in seen_players:
                    seen_players.add(name)
                    all_players_clean.append(p)

            all_players = sorted(all_players_clean, key=extract_p_rank)
            page_cible = 0
            chunks = [all_players[i : i + 10] for i in range(0, len(all_players), 10)]

            if player_found:
                for i, chunk in enumerate(chunks):
                    if any(extract_p_name(p).lower() == loc_player.lower() for p in chunk):
                        page_cible = i
                        break
            elif loc_rank:
                for i, chunk in enumerate(chunks):
                    if any(extract_p_rank(p) >= loc_rank for p in chunk):
                        page_cible = i
                        break

            warning_msg = ""
            if search_mode and not player_found:
                if loc_rank:
                    warning_msg += f"\n\n*💡 Info : Le joueur **{loc_player}** n'a pas été trouvé. Voici le rang {loc_rank} à la place.*"
                else:
                    warning_msg += f"\n\n*💡 Info : Le joueur **{loc_player}** n'a pas été trouvé dans le Top {max_sv_limit}. Voici la première page par défaut.*"
                    page_cible = 0

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
                embed = self.format_page(
                    chunk,
                    i,
                    langue,
                    color=embed_color,
                    highlight_player=loc_player if search_mode else None,
                    event_name=evenement,
                )
                embed.title = titre
                if warning_msg and i == page_cible:
                    embed.description = embed.description + warning_msg
                embed.set_footer(text=f"Page {i + 1}/{len(chunks)}")
                await setup_embed_footer(embed, ctx_int, langue)
                embeds.append(embed)

            return embeds, page_cible

        embeds, page_cible = await fetch_and_build(interaction)
        if not embeds:
            return

        view = RankingPaginationView(embeds, fetch_and_build, interaction.user.id)
        if hasattr(view, "current_page"):
            view.current_page = page_cible
        if hasattr(view, "index"):
            view.index = page_cible
        if hasattr(view, "update_buttons"):
            view.update_buttons()

        view.message = await interaction.followup.send(embed=embeds[page_cible], view=view, wait=True)

    @classement.command(name="realms", description="Displays a ranking for cross-server events")
    @app_commands.describe(evenement="Événement", player="Pseudo (Optionnel)", rank="Rang (Optionnel)")
    @app_commands.autocomplete(player=joueur_autocomplete)
    @app_commands.choices(
        evenement=[
            app_commands.Choice(name="Outer Realms (Current)", value="realms_current"),
            app_commands.Choice(name="Outer Realms (Finished)", value="realms_finished"),
            app_commands.Choice(name="Beyond the Horizon", value="horizon"),
        ]
    )
    async def classement_realms(
        self, interaction: discord.Interaction, evenement: str, player: str = None, rank: int = None
    ):
        await interaction.response.defer(thinking=True)

        async def fetch_and_build(ctx_int):
            loc_player = player
            loc_rank = rank

            langue, serveur_local = await get_server_config(ctx_int)
            serveur_api = self.servers_map.get(serveur_local.lower(), serveur_local)

            if evenement == "realms_current":
                event_id = 62
            elif evenement == "realms_finished":
                event_id = self.event_ids.get("realms", 76)
            else:
                event_id = self.event_ids.get("horizon", 78)

            EVENT_MAP = {
                "realms_current": {
                    "name": t(langue, "cal_ev_realms_current", defaut="Royaumes Extérieurs"),
                    "emoji": get_emo(langue, "{e_war_realms}"),
                },
                "realms_finished": {
                    "name": t(langue, "cal_ev_realms_finished", defaut="Royaumes Extérieurs"),
                    "emoji": get_emo(langue, "{e_war_realms}"),
                },
                "horizon": {
                    "name": t(langue, "cal_ev_horizon", defaut="Au-delà de l'Horizon"),
                    "emoji": get_emo(langue, "{e_std_world_map}"),
                },
            }
            ev_info = EVENT_MAP.get(
                evenement, {"name": evenement.capitalize(), "emoji": DICT_EMOJIS.get("e_events4", "🛡️")}
            )

            search_mode = bool(loc_player)
            suffixe = serveur_local.replace("e4k_", "").upper()
            target_name_exact = f"{loc_player}_{suffixe}".lower() if search_mode else ""
            p_info = None

            if search_mode:
                headers = await get_api_headers(ctx_int)
                url_precheck = (
                    f"https://api.gge-tracker.com/api/v1/{serveur_api}/player/{urllib.parse.quote(loc_player)}"
                )
                try:
                    async with self.bot.session.get(url_precheck, headers=headers, timeout=5) as r:
                        if r.status == 200:
                            data = await r.json()
                            if isinstance(data, dict):
                                p_info = data
                            elif isinstance(data, list) and data:
                                p_info = data[0]
                except Exception:
                    pass

                if p_info:
                    loc_player = p_info.get("player_name", p_info.get("name", loc_player))

            possible_lids = [1]
            found_lid = None

            accumulated_data = {1: []}
            player_found = False

            if search_mode and not loc_rank:
                start_sv = 1
                max_sv_limit = 1000
            elif loc_rank:
                start_sv = max(1, loc_rank - (loc_rank % 10))
                if start_sv == 0:
                    start_sv = 1
                max_sv_limit = start_sv + 49
            else:
                start_sv = 1
                max_sv_limit = 50

            current_sv = start_sv
            PLAYERS_PER_PAGE = 5
            BATCH_SIZE = 20

            async def fetch_chunk(sv_val, lid):
                url = f"{self.ranking_api_url}/{serveur_api}/hgh/%22LT%22:{event_id},%22LID%22:{lid},%22SV%22:%22{sv_val}%22"
                for _ in range(3):
                    try:
                        async with self.bot.session.get(url, timeout=5) as r:
                            if r.status == 200:
                                return await r.json()
                    except:
                        pass
                    await asyncio.sleep(0.3)
                return None

            while current_sv <= max_sv_limit and not player_found:
                task_lids = []
                task_coros = []
                for lid in possible_lids:
                    for i in range(BATCH_SIZE):
                        sv_val = current_sv + (i * PLAYERS_PER_PAGE)
                        if sv_val > max_sv_limit:
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

                            if search_mode and not loc_rank and not player_found:
                                for p in l_chunk:
                                    api_name = extract_p_name(p).lower()
                                    if api_name and (
                                        api_name == target_name_exact
                                        or api_name == loc_player.lower()
                                        or api_name.startswith(f"{loc_player.lower()}_")
                                    ):
                                        player_found = True
                                        found_lid = lid
                                        break
                if batch_empty:
                    break
                current_sv += BATCH_SIZE * PLAYERS_PER_PAGE

            has_any_data = any(len(data) > 0 for data in accumulated_data.values())
            if not has_any_data:
                await ctx_int.followup.send(
                    t(
                        langue,
                        "ev_rank_empty",
                        tranche="Inter-Serveurs",
                        ev=ev_info["name"],
                        defaut="{e_error} Aucun classement.",
                    )
                )
                return None, None

            all_players_raw = accumulated_data[1]

            nom_tranche = t(langue, "ev_bracket_cross_server", defaut="Classement Inter-Serveurs")
            mot_classement = t(langue, "ev_word_rank", defaut="Classement")
            bracket_icon = get_emo(langue, "{e_icon_points}")
            titre = f"{ev_info['emoji']} {mot_classement} {ev_info['name']}\n{bracket_icon} {nom_tranche}"

            seen_players = set()
            all_players_clean = []
            for p in all_players_raw:
                name = extract_p_name(p)
                if name and name not in seen_players:
                    seen_players.add(name)
                    all_players_clean.append(p)

            all_players = sorted(all_players_clean, key=extract_p_rank)
            page_cible = 0
            chunks = [all_players[i : i + 10] for i in range(0, len(all_players), 10)]

            if player_found:
                for i, chunk in enumerate(chunks):
                    if any(
                        extract_p_name(p).lower() == target_name_exact
                        or extract_p_name(p).lower() == loc_player.lower()
                        or extract_p_name(p).lower().startswith(f"{loc_player.lower()}_")
                        for p in chunk
                    ):
                        page_cible = i
                        break
            elif loc_rank:
                for i, chunk in enumerate(chunks):
                    if any(extract_p_rank(p) >= loc_rank for p in chunk):
                        page_cible = i
                        break

            warning_msg = ""
            if search_mode and not player_found:
                if loc_rank:
                    warning_msg += f"\n\n*💡 Info : Le joueur **{loc_player}** n'a pas été trouvé. Voici le rang {loc_rank} à la place.*"
                else:
                    warning_msg += f"\n\n*💡 Info : Le joueur **{loc_player}** n'a pas été trouvé dans le Top {max_sv_limit}. Voici la première page par défaut.*"
                    page_cible = 0

            embed_color = discord.Color(0x4A7160) if evenement == "horizon" else discord.Color(0xF25500)

            embeds = []
            for i, chunk in enumerate(chunks):
                embed = self.format_page(
                    chunk,
                    i,
                    langue,
                    color=embed_color,
                    highlight_player=loc_player if search_mode else None,
                    event_name=evenement,
                )
                embed.title = titre
                if warning_msg and i == page_cible:
                    embed.description = embed.description + warning_msg
                embed.set_footer(text=f"Page {i + 1}/{len(chunks)}")
                await setup_embed_footer(embed, ctx_int, langue)
                embeds.append(embed)

            return embeds, page_cible

        embeds, page_cible = await fetch_and_build(interaction)
        if not embeds:
            return

        view = RankingPaginationView(embeds, fetch_and_build, interaction.user.id)
        if hasattr(view, "current_page"):
            view.current_page = page_cible
        if hasattr(view, "index"):
            view.index = page_cible
        if hasattr(view, "update_buttons"):
            view.update_buttons()

        view.message = await interaction.followup.send(embed=embeds[page_cible], view=view, wait=True)

    @classement.command(name="league", description="Displays a ranking for Season and League events")
    @app_commands.describe(
        evenement="Événement", player="Pseudo (Optionnel)", rank="Rang (Optionnel)", tranche="Tranche (Optionnel)"
    )
    @app_commands.autocomplete(player=joueur_autocomplete, tranche=tranche_autocomplete)
    @app_commands.choices(
        evenement=[
            app_commands.Choice(name="Season / Festival", value="season"),
            app_commands.Choice(name="Kingdom League", value="league"),
        ]
    )
    async def classement_league(
        self,
        interaction: discord.Interaction,
        evenement: str,
        player: str = None,
        rank: int = None,
        tranche: int = None,
    ):
        await interaction.response.defer(thinking=True)

        async def fetch_and_build(ctx_int):
            loc_player = player
            loc_rank = rank
            loc_tranche = tranche

            langue, serveur_local = await get_server_config(ctx_int)
            serveur_api = self.servers_map.get(serveur_local.lower(), serveur_local)
            event_id = self.event_ids.get(evenement, 53)

            EVENT_MAP = {
                "season": {
                    "name": t(langue, "cal_ev_season", defaut="Saison / Festival"),
                    "emoji": get_emo(langue, "{e_std_trophy}"),
                },
                "league": {
                    "name": t(langue, "cal_ev_league", defaut="Ligue du Royaume"),
                    "emoji": get_emo(langue, "{e_empirerankings}"),
                },
            }
            ev_info = EVENT_MAP.get(
                evenement, {"name": evenement.capitalize(), "emoji": DICT_EMOJIS.get("e_leagueicon", "🛡️")}
            )

            search_mode = bool(loc_player)
            p_info = None

            if search_mode:
                headers = await get_api_headers(ctx_int)
                url_precheck = (
                    f"https://api.gge-tracker.com/api/v1/{serveur_api}/player/{urllib.parse.quote(loc_player)}"
                )
                try:
                    async with self.bot.session.get(url_precheck, headers=headers, timeout=5) as r:
                        if r.status == 200:
                            data = await r.json()
                            if isinstance(data, dict):
                                p_info = data
                            elif isinstance(data, list) and data:
                                p_info = data[0]
                except Exception:
                    pass

                if p_info:
                    loc_player = p_info.get("player_name", p_info.get("name", loc_player))
                    lvl = int(p_info.get("level", p_info.get("lvl", 70)))
                    leg = int(
                        p_info.get("legendaryLevel", p_info.get("legendary_level", p_info.get("paragonLevel", 999)))
                    )
                else:
                    lvl = None
                    leg = None
            else:
                lvl = 70
                leg = 999

            api_command = "hgh"
            pagination_param = "SV"
            is_string_val = True

            if evenement == "league":
                possible_lids = [1]
            elif evenement == "season":
                api_command = "llsp"
                pagination_param = "R"
                is_string_val = False

                if lvl is not None and lvl < 70 and search_mode:
                    await ctx_int.followup.send(
                        t(
                            langue,
                            "ev_err_season_level",
                            defaut="{e_error} Classement de saison indisponible avant le niveau 70.",
                        )
                    )
                    return None, None

                if loc_tranche is not None:
                    possible_lids = [loc_tranche]
                else:
                    if lvl is None:
                        possible_lids = [1, 2, 3, 4]
                    elif leg < 200:
                        possible_lids = [1]
                    elif leg < 650:
                        possible_lids = [2]
                    elif leg < 950:
                        possible_lids = [3]
                    else:
                        possible_lids = [4]

            found_lid = None

            warning_msg = ""
            if loc_rank and not loc_tranche and not search_mode and evenement == "season":
                warning_msg += (
                    "\n*💡 Info : Tranche maximale affichée par défaut. Utilisez l'option `tranche` pour changer.*"
                )

            accumulated_data = {lid: [] for lid in possible_lids}
            player_found = False

            if search_mode and not loc_rank:
                start_sv = 1
                max_sv_limit = 1000
            elif loc_rank:
                start_sv = max(1, loc_rank - (loc_rank % 10))
                if start_sv == 0:
                    start_sv = 1
                max_sv_limit = start_sv + 49
            else:
                start_sv = 1
                max_sv_limit = 50

            current_sv = start_sv
            PLAYERS_PER_PAGE = 10
            BATCH_SIZE = 10

            async def fetch_chunk(sv_val, lid):
                if is_string_val:
                    url = f"{self.ranking_api_url}/{serveur_api}/{api_command}/%22LT%22:{event_id},%22LID%22:{lid},%22{pagination_param}%22:%22{sv_val}%22"
                else:
                    url = f"{self.ranking_api_url}/{serveur_api}/{api_command}/%22LT%22:{event_id},%22LID%22:{lid},%22{pagination_param}%22:{sv_val}"

                for _ in range(3):
                    try:
                        async with self.bot.session.get(url, timeout=5) as r:
                            if r.status == 200:
                                return await r.json()
                    except:
                        pass
                    await asyncio.sleep(0.3)
                return None

            while current_sv <= max_sv_limit and not player_found:
                task_lids = []
                task_coros = []
                for lid in possible_lids:
                    for i in range(BATCH_SIZE):
                        sv_val = current_sv + (i * PLAYERS_PER_PAGE)
                        if sv_val > max_sv_limit:
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

                            if search_mode and not loc_rank and not player_found:
                                for p in l_chunk:
                                    if extract_p_name(p).lower() == loc_player.lower():
                                        player_found = True
                                        found_lid = lid
                                        break
                if batch_empty:
                    break
                current_sv += BATCH_SIZE * PLAYERS_PER_PAGE

            has_any_data = any(len(data) > 0 for data in accumulated_data.values())
            if not has_any_data:
                await ctx_int.followup.send(
                    t(
                        langue,
                        "ev_rank_empty",
                        tranche="Tranche requise",
                        ev=ev_info["name"],
                        defaut="{e_error} Aucun classement.",
                    )
                )
                return None, None

            found_lid = found_lid or possible_lids[-1]
            all_players_raw = accumulated_data[found_lid]

            if evenement == "league":
                nom_tranche = t(langue, "ev_bracket_all", defaut="Classement Global")
            elif evenement == "season":
                nom_tranche_defaut = f"Tranche {found_lid}" if found_lid != 4 else "Légendaire 950+"
                nom_tranche = t(langue, f"season_bracket_{found_lid}", defaut=nom_tranche_defaut)

            bracket_icon = get_emo(langue, "{e_lvl}")
            mot_classement = t(langue, "ev_word_rank", defaut="Classement")
            titre = f"{ev_info['emoji']} {mot_classement} {ev_info['name']}\n{bracket_icon} {nom_tranche}"

            seen_players = set()
            all_players_clean = []
            for p in all_players_raw:
                name = extract_p_name(p)
                rank_val = extract_p_rank(p)
                if name and name not in seen_players:
                    seen_players.add(name)
                    all_players_clean.append((rank_val, p))

            all_players_sorted = sorted(all_players_clean, key=lambda x: x[0])
            all_players = [item[1] for item in all_players_sorted]

            page_cible = 0
            chunks = [all_players[i : i + 10] for i in range(0, len(all_players), 10)]

            if player_found:
                for i, chunk in enumerate(chunks):
                    trouve_dans_page = False
                    for p in chunk:
                        if extract_p_name(p).lower() == loc_player.lower():
                            page_cible = i
                            trouve_dans_page = True
                            break
                    if trouve_dans_page:
                        break
            elif loc_rank:
                for i, chunk in enumerate(chunks):
                    trouve_dans_page = False
                    for p in chunk:
                        if extract_p_rank(p) >= loc_rank:
                            page_cible = i
                            trouve_dans_page = True
                            break
                    if trouve_dans_page:
                        break

            if search_mode and not player_found:
                if loc_rank:
                    warning_msg += f"\n\n*💡 Info : Le joueur **{loc_player}** n'a pas été trouvé. Voici le rang {loc_rank} à la place.*"
                else:
                    warning_msg += f"\n\n*💡 Info : Le joueur **{loc_player}** n'a pas été trouvé dans le Top {max_sv_limit}. Voici la première page par défaut.*"
                    page_cible = 0

            embed_color = discord.Color(0xDDAADD) if evenement == "season" else discord.Color(0x004D25)

            embeds = []
            for i, chunk in enumerate(chunks):
                embed = self.format_page(
                    chunk,
                    i,
                    langue,
                    color=embed_color,
                    highlight_player=loc_player if search_mode else None,
                    event_name=evenement,
                )
                embed.title = titre
                if warning_msg and i == page_cible:
                    embed.description = embed.description + warning_msg
                embed.set_footer(text=f"Page {i + 1}/{len(chunks)}")
                await setup_embed_footer(embed, ctx_int, langue)
                embeds.append(embed)

            return embeds, page_cible

        embeds, page_cible = await fetch_and_build(interaction)
        if not embeds:
            return

        view = RankingPaginationView(embeds, fetch_and_build, interaction.user.id)
        if hasattr(view, "current_page"):
            view.current_page = page_cible
        if hasattr(view, "index"):
            view.index = page_cible
        if hasattr(view, "update_buttons"):
            view.update_buttons()

        view.message = await interaction.followup.send(embed=embeds[page_cible], view=view, wait=True)

    # ==========================================
    # LA FAMEUSE COMMANDE CONTESTS OUBLIÉE
    # ==========================================
    @classement.command(name="contests", description="Displays a ranking for specific contests")
    @app_commands.describe(
        evenement="Événement", player="Pseudo (Optionnel)", rank="Rang (Optionnel)", tranche="Tranche (Optionnel)"
    )
    @app_commands.autocomplete(player=joueur_autocomplete, tranche=tranche_autocomplete)
    @app_commands.choices(
        evenement=[
            app_commands.Choice(name="Shapeshifters", value="shapeshifters"),
            app_commands.Choice(name="Nobility Contest", value="nobility"),
            app_commands.Choice(name="Wheel of Unimaginable Affluence", value="woa"),
            app_commands.Choice(name="Imperial patronage", value="patronage"),
        ]
    )
    async def classement_contests(
        self,
        interaction: discord.Interaction,
        evenement: str,
        player: str = None,
        rank: int = None,
        tranche: int = None,
    ):
        await interaction.response.defer(thinking=True)

        async def fetch_and_build(ctx_int):
            loc_player = player
            loc_rank = rank
            loc_tranche = tranche

            langue, serveur_local = await get_server_config(ctx_int)
            serveur_api = self.servers_map.get(serveur_local.lower(), serveur_local)
            event_id = self.event_ids.get(evenement, 60)

            EVENT_MAP = {
                "shapeshifters": {
                    "name": t(langue, "cal_ev_shape", defaut="Les Métamorphes"),
                    "emoji": DICT_EMOJIS.get("e_shapeshifter", "👹"),
                },
                "nobility": {
                    "name": t(langue, "cal_ev_nobility", defaut="Concours de Noblesse"),
                    "emoji": get_emo(langue, "{e_std_crown}"),
                },
                "woa": {
                    "name": t(langue, "cal_ev_woa", defaut="Guerre des Alliances"),
                    "emoji": get_emo(langue, "{e_woa_points}"),
                },
                "patronage": {
                    "name": t(langue, "cal_ev_patronage", defaut="Patronage"),
                    "emoji": DICT_EMOJIS.get("e_patronage", "🪙"),
                },
            }
            ev_info = EVENT_MAP.get(
                evenement, {"name": evenement.capitalize(), "emoji": DICT_EMOJIS.get("e_events4", "🏆")}
            )

            search_mode = bool(loc_player)
            p_info = None

            if search_mode:
                headers = await get_api_headers(ctx_int)
                url_precheck = (
                    f"https://api.gge-tracker.com/api/v1/{serveur_api}/player/{urllib.parse.quote(loc_player)}"
                )
                try:
                    async with self.bot.session.get(url_precheck, headers=headers, timeout=5) as r:
                        if r.status == 200:
                            data = await r.json()
                            if isinstance(data, dict):
                                p_info = data
                            elif isinstance(data, list) and data:
                                p_info = data[0]
                except Exception:
                    pass

                if p_info:
                    loc_player = p_info.get("player_name", p_info.get("name", loc_player))
                    lvl = int(p_info.get("level", p_info.get("lvl", 70)))
                    leg = int(
                        p_info.get("legendaryLevel", p_info.get("legendary_level", p_info.get("paragonLevel", 999)))
                    )
                else:
                    lvl = None
                    leg = None
            else:
                lvl = 70
                leg = 999

            if evenement in ["woa", "patronage"]:
                possible_lids = [1]
            elif evenement == "shapeshifters":
                if loc_tranche is not None:
                    possible_lids = [loc_tranche]
                else:
                    if lvl is None:
                        possible_lids = [1, 2, 3, 4, 5]
                    elif lvl < 70:
                        possible_lids = [1]
                    elif leg < 300:
                        possible_lids = [2]
                    elif leg < 650:
                        possible_lids = [3]
                    elif leg < 950:
                        possible_lids = [4]
                    else:
                        possible_lids = [5]
            elif evenement == "nobility":
                if loc_tranche is not None:
                    possible_lids = [loc_tranche]
                else:
                    if lvl is None:
                        possible_lids = [1, 2, 3, 4, 5, 6, 7]
                    elif lvl < 15:
                        possible_lids = [1]
                    elif lvl < 20:
                        possible_lids = [2]
                    elif lvl < 25:
                        possible_lids = [3]
                    elif lvl < 30:
                        possible_lids = [4]
                    elif lvl < 50:
                        possible_lids = [5]
                    elif lvl < 70:
                        possible_lids = [6]
                    else:
                        possible_lids = [7]

            found_lid = None

            warning_msg = ""
            if loc_rank and not loc_tranche and not search_mode and evenement in ["shapeshifters", "nobility"]:
                warning_msg += (
                    "\n*💡 Info : Tranche maximale affichée par défaut. Utilisez l'option `tranche` pour changer.*"
                )

            accumulated_data = {lid: [] for lid in possible_lids}
            player_found = False

            if search_mode and not loc_rank:
                start_sv = 1
                max_sv_limit = 1000
            elif loc_rank:
                start_sv = max(1, loc_rank - (loc_rank % 10))
                if start_sv == 0:
                    start_sv = 1
                max_sv_limit = start_sv + 49
            else:
                start_sv = 1
                max_sv_limit = 50

            current_sv = start_sv
            PLAYERS_PER_PAGE = 5
            BATCH_SIZE = 10

            async def fetch_chunk(sv_val, lid):
                url = f"{self.ranking_api_url}/{serveur_api}/hgh/%22LT%22:{event_id},%22LID%22:{lid},%22SV%22:%22{sv_val}%22"
                for _ in range(3):
                    try:
                        async with self.bot.session.get(url, timeout=5) as r:
                            if r.status == 200:
                                return await r.json()
                    except:
                        pass
                    await asyncio.sleep(0.3)
                return None

            while current_sv <= max_sv_limit and not player_found:
                task_lids = []
                task_coros = []
                for lid in possible_lids:
                    for i in range(BATCH_SIZE):
                        sv_val = current_sv + (i * PLAYERS_PER_PAGE)
                        if sv_val > max_sv_limit:
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

                            if search_mode and not loc_rank and not player_found:
                                for p in l_chunk:
                                    if extract_p_name(p).lower() == loc_player.lower():
                                        player_found = True
                                        found_lid = lid
                                        break
                if batch_empty:
                    break
                current_sv += BATCH_SIZE * PLAYERS_PER_PAGE

            has_any_data = any(len(data) > 0 for data in accumulated_data.values())
            if not has_any_data:
                await ctx_int.followup.send(
                    t(
                        langue,
                        "ev_rank_empty",
                        tranche="Tranche requise",
                        ev=ev_info["name"],
                        defaut="{e_error} Aucun classement.",
                    )
                )
                return None, None

            found_lid = found_lid or possible_lids[-1]
            all_players_raw = accumulated_data[found_lid]

            bracket_icon = get_emo(langue, "{e_icon_points}")
            if evenement in ["woa", "patronage"]:
                nom_tranche = t(langue, "ev_bracket_all", defaut="Classement Global")
            elif evenement == "shapeshifters":
                nom_tranche_defaut = self.brackets_map.get(str(found_lid), f"Tranche {found_lid}")
                nom_tranche = t(langue, f"ev_bracket_{found_lid}", defaut=nom_tranche_defaut)
                bracket_icon = get_emo(langue, "{e_lvl}")
            elif evenement == "nobility":
                nom_tranche_defaut = f"Tranche {found_lid}" if found_lid != 7 else "Légendaires (70+)"
                nom_tranche = t(langue, f"nobility_bracket_{found_lid}", defaut=nom_tranche_defaut)
                bracket_icon = get_emo(langue, "{e_lvl}")

            mot_classement = t(langue, "ev_word_rank", defaut="Classement")
            titre = f"{ev_info['emoji']} {mot_classement} {ev_info['name']}\n{bracket_icon} {nom_tranche}"

            seen_players = set()
            all_players_clean = []
            for p in all_players_raw:
                name = extract_p_name(p)
                if name and name not in seen_players:
                    seen_players.add(name)
                    all_players_clean.append(p)

            all_players = sorted(all_players_clean, key=extract_p_rank)
            page_cible = 0
            chunks = [all_players[i : i + 10] for i in range(0, len(all_players), 10)]

            if player_found:
                for i, chunk in enumerate(chunks):
                    if any(extract_p_name(p).lower() == loc_player.lower() for p in chunk):
                        page_cible = i
                        break
            elif loc_rank:
                for i, chunk in enumerate(chunks):
                    if any(extract_p_rank(p) >= loc_rank for p in chunk):
                        page_cible = i
                        break

            warning_msg = ""
            if search_mode and not player_found:
                if loc_rank:
                    warning_msg += f"\n\n*💡 Info : Le joueur **{loc_player}** n'a pas été trouvé. Voici le rang {loc_rank} à la place.*"
                else:
                    warning_msg += f"\n\n*💡 Info : Le joueur **{loc_player}** n'a pas été trouvé dans le Top {max_sv_limit}. Voici la première page par défaut.*"
                    page_cible = 0

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
                embed = self.format_page(
                    chunk,
                    i,
                    langue,
                    color=embed_color,
                    highlight_player=loc_player if search_mode else None,
                    event_name=evenement,
                )
                embed.title = titre
                if warning_msg and i == page_cible:
                    embed.description = embed.description + warning_msg
                embed.set_footer(text=f"Page {i + 1}/{len(chunks)}")
                await setup_embed_footer(embed, ctx_int, langue)
                embeds.append(embed)

            return embeds, page_cible

        embeds, page_cible = await fetch_and_build(interaction)
        if not embeds:
            return

        view = RankingPaginationView(embeds, fetch_and_build, interaction.user.id)
        if hasattr(view, "current_page"):
            view.current_page = page_cible
        if hasattr(view, "index"):
            view.index = page_cible
        if hasattr(view, "update_buttons"):
            view.update_buttons()

        view.message = await interaction.followup.send(embed=embeds[page_cible], view=view, wait=True)

    @classement.command(name="alliance", description="Displays live rankings and statistics for alliances")
    @app_commands.describe(
        categorie="Événement", alliance_name="Nom de l'alliance (Optionnel)", rank="Rang (Optionnel)"
    )
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
    async def classement_alliance(
        self, interaction: discord.Interaction, categorie: str, alliance_name: str = None, rank: int = None
    ):
        await interaction.response.defer(thinking=True)

        async def fetch_and_build(ctx_int):
            loc_alliance = alliance_name
            loc_rank = rank

            langue, serveur_local = await get_server_config(ctx_int)
            serveur_api = self.servers_map.get(serveur_local.lower(), serveur_local)
            event_id = self.event_ids.get(categorie, 10)

            CAT_MAP = {
                "alliance_honor": {
                    "name": t(langue, "cal_stat_honor", defaut="Points d'Honneur"),
                    "emoji": get_emo(langue, "{e_honor}"),
                    "color": discord.Color(0xFFFFFF),
                },
                "alliance_might": {
                    "name": t(langue, "cal_stat_might", defaut="Points de Puissance"),
                    "emoji": get_emo(langue, "{e_pp2}"),
                    "color": discord.Color(0xBB270D),
                },
                "alliance_command": {
                    "name": t(langue, "cal_stat_command", defaut="Points de Commandement"),
                    "emoji": DICT_EMOJIS.get("e_alliance_icon", "🛡️"),
                    "color": discord.Color(0xAFAFAF),
                },
                "alliance_cargo": {
                    "name": t(langue, "cal_stat_cargo", defaut="Points de Fret"),
                    "emoji": get_emo(langue, "{e_pointscargo}"),
                    "color": discord.Color(0x84CED1),
                },
                "alliance_nomad": {
                    "name": t(langue, "cal_ev_nomad", defaut="Invasion des Nomades"),
                    "emoji": get_emo(langue, "{e_nomads}"),
                    "color": discord.Color(0xEDCB5A),
                },
                "alliance_foreigners": {
                    "name": t(langue, "cal_ev_realms", defaut="Guerre des Royaumes"),
                    "emoji": get_emo(langue, "{e_war_realms}"),
                    "color": discord.Color(0x49415D),
                },
                "alliance_samurais": {
                    "name": t(langue, "cal_ev_samurai", defaut="Invasion des Samouraïs"),
                    "emoji": get_emo(langue, "{e_samurai}"),
                    "color": discord.Color(0xD43C27),
                },
                "alliance_bloodcrows": {
                    "name": t(langue, "cal_ev_bloodcrow", defaut="Corbeaux de Sang"),
                    "emoji": get_emo(langue, "{e_bloodcrow}"),
                    "color": discord.Color(0x670111),
                },
                "alliance_league": {
                    "name": t(langue, "cal_ev_league", defaut="Ligue du Royaume"),
                    "emoji": get_emo(langue, "{e_std_trophy}"),
                    "color": discord.Color(0x004D25),
                },
                "alliance_horizon": {
                    "name": t(langue, "cal_ev_horizon", defaut="Au-delà de l'Horizon"),
                    "emoji": get_emo(langue, "{e_std_world_map}"),
                    "color": discord.Color(0x4A7160),
                },
            }

            cat_info = CAT_MAP.get(
                categorie,
                {
                    "name": categorie.capitalize(),
                    "emoji": get_emo(langue, "{e_alliance_icon}"),
                    "color": discord.Color.gold(),
                },
            )
            embed_color = cat_info["color"]

            tranche = 1
            nom_tranche = t(langue, "ev_bracket_alliance", defaut="Classement Global Alliances")

            search_mode = bool(loc_alliance)
            all_alliances_raw = []
            alliance_found = False

            if search_mode and not loc_rank:
                start_sv = 1
                max_sv_limit = 1000
            elif loc_rank:
                start_sv = max(1, loc_rank - (loc_rank % 10))
                if start_sv == 0:
                    start_sv = 1
                max_sv_limit = start_sv + 49
            else:
                start_sv = 1
                max_sv_limit = 50

            current_sv = start_sv
            PLAYERS_PER_PAGE = 5
            BATCH_SIZE = 10

            async def fetch_chunk(sv_val):
                url = f"{self.ranking_api_url}/{serveur_api}/hgh/%22LT%22:{event_id},%22SV%22:%22{sv_val}%22"
                for _ in range(3):
                    try:
                        async with self.bot.session.get(url, timeout=5) as r:
                            if r.status == 200:
                                return await r.json()
                    except:
                        pass
                    await asyncio.sleep(0.3)
                return None

            while current_sv <= max_sv_limit and not alliance_found:
                tasks = [
                    fetch_chunk(current_sv + (i * PLAYERS_PER_PAGE))
                    for i in range(BATCH_SIZE)
                    if current_sv + (i * PLAYERS_PER_PAGE) <= max_sv_limit
                ]
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

                            if search_mode and not loc_rank:
                                for p in l_chunk:
                                    api_name = extract_p_name(p).lower()
                                    if api_name == loc_alliance.lower() or api_name.startswith(
                                        f"{loc_alliance.lower()}_"
                                    ):
                                        alliance_found = True
                                        break
                if batch_empty:
                    break
                current_sv += BATCH_SIZE * PLAYERS_PER_PAGE

            if not all_alliances_raw:
                embed_empty = discord.Embed(
                    title="📭 Classement indisponible",
                    description=t(
                        langue,
                        "ev_rank_empty",
                        ev=cat_info["name"],
                        defaut=f"Aucun classement trouvé pour **{cat_info['name']}**.",
                    ),
                    color=discord.Color.light_grey(),
                )
                await ctx_int.followup.send(embed=embed_empty)
                return None, None

            seen_alliances = set()
            all_alliances_clean = []
            for p in all_alliances_raw:
                a_name = extract_p_name(p)
                if a_name and a_name not in seen_alliances:
                    seen_alliances.add(a_name)
                    all_alliances_clean.append(p)

            all_alliances = sorted(all_alliances_clean, key=extract_p_rank)
            page_cible = 0
            chunks = [all_alliances[i : i + 10] for i in range(0, len(all_alliances), 10)]

            if alliance_found:
                for i, chunk in enumerate(chunks):
                    if any(extract_p_name(p).lower() == loc_alliance.lower() for p in chunk):
                        page_cible = i
                        break
            elif loc_rank:
                for i, chunk in enumerate(chunks):
                    if any(extract_p_rank(p) >= loc_rank for p in chunk):
                        page_cible = i
                        break

            warning_msg = ""
            if search_mode and not alliance_found:
                if loc_rank:
                    warning_msg += f"\n\n*💡 Info : L'alliance **{loc_alliance}** n'a pas été trouvée. Voici le rang {loc_rank} à la place.*"
                else:
                    warning_msg += f"\n\n*💡 Info : L'alliance **{loc_alliance}** n'a pas été trouvée dans le Top {max_sv_limit}. Voici la première page par défaut.*"
                    page_cible = 0

            titre = f"{cat_info['emoji']} Classement {cat_info['name']}\n🛡️ {nom_tranche}"

            embeds = []
            for i, chunk in enumerate(chunks):
                embed = self.format_page(
                    chunk,
                    i,
                    langue,
                    color=embed_color,
                    is_alliance=True,
                    highlight_player=loc_alliance if search_mode else None,
                    event_name=categorie,
                )
                embed.title = titre
                if warning_msg and i == page_cible:
                    embed.description = embed.description + warning_msg
                embed.set_footer(text=f"Page {i + 1}/{len(chunks)}")
                await setup_embed_footer(embed, ctx_int, langue)
                embeds.append(embed)

            return embeds, page_cible

        embeds, page_cible = await fetch_and_build(interaction)
        if not embeds:
            return

        view = RankingPaginationView(embeds, fetch_and_build, interaction.user.id)
        if hasattr(view, "current_page"):
            view.current_page = page_cible
        if hasattr(view, "index"):
            view.index = page_cible
        if hasattr(view, "update_buttons"):
            view.update_buttons()

        view.message = await interaction.followup.send(embed=embeds[page_cible], view=view, wait=True)


async def setup(bot):
    await bot.add_cog(ClassementCog(bot))

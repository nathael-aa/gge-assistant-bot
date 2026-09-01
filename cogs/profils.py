import asyncio
import json
import logging
import traceback
from datetime import datetime, timedelta
from urllib.parse import quote

import discord
from discord import app_commands
from discord.ext import commands

from utils import (
    BASE_DATA_PATH,
    DICT_EMOJIS,
    PaginationView,
    _get_api_timestamp,
    alliance_autocomplete,
    format_num,
    get_api_headers,
    get_cached_data,
    get_discord_timestamp,
    get_server_config,
    joueur_autocomplete,
    prompt_vote_if_lucky,
    setup_embed_footer,
    t,
)

logger = logging.getLogger("GGE_Bot")


class HistoriqueView(discord.ui.View):
    def __init__(self, embeds_dict, interaction: discord.Interaction, langue: str = "fr"):
        super().__init__(timeout=3600)
        self.embeds_dict = embeds_dict
        self.interaction = interaction
        self.message = None
        self.langue = langue

        self.btn_pseudo.label = t(langue, "prof_hist_btn_pseudos", defaut="Pseudos")
        self.btn_pseudo.emoji = DICT_EMOJIS.get("e_listitem", "📝")

        self.btn_alliance.label = t(langue, "prof_hist_btn_alliances", defaut="Alliances")
        self.btn_alliance.emoji = DICT_EMOJIS.get("e_alliance_icon", "🛡️")

        self.btn_position.label = t(langue, "prof_hist_btn_mouvements", defaut="Mouvements")
        # Fallback sur e_compass s'il te manque e_moove dans ton DICT_EMOJIS
        self.btn_position.emoji = DICT_EMOJIS.get("e_moove", DICT_EMOJIS.get("e_compass", "📍"))

        self.btn_prev.emoji = DICT_EMOJIS.get("e_last", "⏮️")
        self.btn_next.emoji = DICT_EMOJIS.get("e_next", "⏭️")

        self.current_cat = "pseudo"
        self.current_page = 0
        self._update_navigation()

    def _update_navigation(self):
        self.clear_items()

        self.btn_pseudo.style = (
            discord.ButtonStyle.primary if self.current_cat == "pseudo" else discord.ButtonStyle.secondary
        )
        self.btn_alliance.style = (
            discord.ButtonStyle.primary if self.current_cat == "alliance" else discord.ButtonStyle.secondary
        )
        self.btn_position.style = (
            discord.ButtonStyle.primary if self.current_cat == "position" else discord.ButtonStyle.secondary
        )

        self.add_item(self.btn_pseudo)
        self.add_item(self.btn_alliance)
        self.add_item(self.btn_position)

        if len(self.embeds_dict[self.current_cat]) > 1:
            self.add_item(self.btn_prev)
            self.add_item(self.btn_next)

    async def _switch_category(self, interaction: discord.Interaction, cat: str):
        self.current_cat = cat
        self.current_page = 0
        self._update_navigation()
        if not self.message:
            self.message = interaction.message
        await interaction.response.edit_message(embed=self.embeds_dict[self.current_cat][0], view=self)

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        if hasattr(self, "message") and self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass

    @discord.ui.button(row=0)
    async def btn_pseudo(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._switch_category(interaction, "pseudo")

    @discord.ui.button(row=0)
    async def btn_alliance(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._switch_category(interaction, "alliance")

    @discord.ui.button(row=0)
    async def btn_position(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._switch_category(interaction, "position")

    @discord.ui.button(style=discord.ButtonStyle.blurple, row=1)
    async def btn_prev(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page = max(0, self.current_page - 1)
        if not self.message:
            self.message = interaction.message
        await interaction.response.edit_message(embed=self.embeds_dict[self.current_cat][self.current_page], view=self)

    @discord.ui.button(style=discord.ButtonStyle.blurple, row=1)
    async def btn_next(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page = min(len(self.embeds_dict[self.current_cat]) - 1, self.current_page + 1)
        if not self.message:
            self.message = interaction.message
        await interaction.response.edit_message(embed=self.embeds_dict[self.current_cat][self.current_page], view=self)


class ProfilsCog(commands.Cog):
    alliance_group = app_commands.Group(name="alliance", description="All commands related to alliances")
    player_group = app_commands.Group(name="player", description="All commands related to players")

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.bdd_chemin = BASE_DATA_PATH / "bdd_items_gge.json"
        # Server Command
        self.clr_server = discord.Color.from_rgb(255, 244, 230)
        # Purple for player group
        self.clr_joueur = discord.Color.from_rgb(223, 204, 241)
        self.clr_historique = discord.Color.from_rgb(200, 183, 216)
        self.clr_colombe = discord.Color.from_rgb(160, 146, 172)
        self.clr_compare_j = discord.Color.from_rgb(112, 102, 120)
        # Green for alliance group
        self.clr_alliance = discord.Color.from_rgb(129, 186, 39)
        self.clr_alliance_pp = discord.Color.from_rgb(116, 167, 35)
        self.clr_alliance_property = discord.Color.from_rgb(92, 133, 28)
        self.clr_scanner = discord.Color.from_rgb(64, 93, 19)
        self.clr_descalli = discord.Color.from_rgb(77, 111, 23)

    # ========================================================
    # COMMANDE : Server
    # ========================================================
    @app_commands.command(name="server", description="Affiche les statistiques globales de ton serveur")
    async def server_stats(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)

        langue, serveur = await get_server_config(interaction)
        headers = await get_api_headers(custom_server=serveur)
        session = self.bot.session

        local_data = {}
        try:
            scan_dir = BASE_DATA_PATH / "server_scans" / serveur
            if scan_dir.exists():
                fichiers = list(scan_dir.glob("*.json"))
                if fichiers:
                    latest_file = max(fichiers, key=lambda p: p.stat().st_mtime)
                    with open(latest_file, encoding="utf-8") as f:
                        local_data = json.load(f)
        except:
            pass

        api_current = {}
        api_previous = {}
        try:
            url = "https://api.gge-tracker.com/api/v1/server/statistics"
            async with session.get(url, headers=headers, timeout=10) as r:
                if r.status == 200:
                    data = await r.json()
                    if isinstance(data, list) and len(data) > 0:
                        api_current = data[-1]
                        if len(data) > 1:
                            api_previous = data[-2]
                    elif isinstance(data, dict):
                        api_current = data
        except Exception as e:
            logger.error(f"Erreur API /server/statistics : {e}")

        if not api_current and not local_data:
            err_msg = t(
                langue,
                "server_err_no_data",
                defaut="{e_error} Impossible de récupérer les statistiques du serveur pour le moment.",
            )
            return await interaction.followup.send(err_msg)

        def fmt(n):
            if n is None:
                return "0"
            return f"{int(n):,}".replace(",", " ")

        def get_val(key, default=0):
            return api_current.get(key) or local_data.get(key, local_data.get("stats", {}).get(key, default))

        def get_diff(key):
            if api_previous and api_current.get(key) is not None and api_previous.get(key) is not None:
                return float(api_current[key]) - float(api_previous[key])
            return 0

        e_up = DICT_EMOJIS.get("e_std_chart_increasing", "📈")
        e_down = DICT_EMOJIS.get("e_std_chart_decreasing", "📉")

        def format_var(val):
            if val == 0:
                return ""
            elif val > 0:
                return f" {e_up} (+`{fmt(val)}`)"
            else:
                return f" {e_down} (`{fmt(val)}`)"

        p_count = get_val("players_count")
        a_count = get_val("alliance_count") or get_val("total_alliances")
        p_peace = get_val("players_in_peace")
        p_chg_alli = get_val("players_who_changed_alliance")
        p_chg_name = get_val("players_who_changed_name")

        p_count_var = format_var(get_diff("players_count"))
        a_count_var = format_var(get_diff("alliance_count"))
        p_peace_var = format_var(get_diff("players_in_peace"))

        avg_lvl_raw = get_val("avg_level")
        avg_lvl = f"70/{int(avg_lvl_raw) - 70}" if avg_lvl_raw > 70 else str(int(avg_lvl_raw))

        lvl_diff = get_diff("avg_level")
        lvl_var = (
            f" {e_up} (+`{round(lvl_diff, 2)}`)"
            if lvl_diff > 0
            else f" {e_down} (`{round(lvl_diff, 2)}`)"
            if lvl_diff < 0
            else ""
        )

        t_might = get_val("total_might")
        a_might = get_val("avg_might")
        m_might = get_val("max_might")
        v_might_str = format_var(get_diff("total_might"))

        t_loot = get_val("total_loot")
        a_loot = get_val("avg_loot")
        m_loot = get_val("max_loot")
        v_loot_str = format_var(get_diff("total_loot"))

        t_honor = get_val("total_honor")
        a_honor = get_val("avg_honor")
        v_honor_str = format_var(get_diff("total_honor"))

        created_at_raw = api_current.get("created_at") or local_data.get("scan_date")
        timestamp_str = t(langue, "rad_time_recent", defaut="Récemment")
        if created_at_raw:
            try:
                dt = datetime.fromisoformat(str(created_at_raw).replace("Z", "+00:00"))
                timestamp_str = f"<t:{int(dt.timestamp())}:R>"
            except:
                pass

        titre = t(
            langue, "server_title", serv=serveur.upper(), defaut="{e_icon_world} Statistiques Globales - Serveur {serv}"
        )
        desc = t(langue, "server_desc", ts=timestamp_str, defaut="Actualisation des données : {ts}")

        embed = discord.Embed(title=titre, description=desc, color=self.clr_server)

        lbl_demo_title = t(langue, "server_demo_title", defaut="{e_players} Démographie")
        lbl_demo_val = t(
            langue,
            "server_demo_val",
            p=fmt(p_count),
            vp=p_count_var,
            a=fmt(a_count),
            va=a_count_var,
            lvl=avg_lvl,
            vlvl=lvl_var,
            peace=fmt(p_peace),
            vpeace=p_peace_var,
            c_alli=fmt(p_chg_alli),
            c_name=fmt(p_chg_name),
            defaut=(
                "• Joueurs : `{p}`{vp}\n"
                "• Alliances : `{a}`{va}\n"
                "• Niveau moyen : `{lvl}`{vlvl}\n"
                "• Sous colombe {e_peace} : `{peace}`{vpeace}\n"
                "• Transferts d'alliance : `{c_alli}`\n"
                "• Changements de pseudo : `{c_name}`"
            ),
        )
        embed.add_field(name=lbl_demo_title, value=lbl_demo_val, inline=False)

        lbl_might_title = t(langue, "server_might_title", defaut="{e_pp1} Puissance")
        lbl_might_val = t(
            langue,
            "server_might_val",
            t=fmt(t_might),
            var=v_might_str,
            avg=fmt(a_might),
            max=fmt(m_might),
            defaut="• Globale : `{t}`{var}\n• Moyenne : `{avg}`\n• Top 1 : `{max}`",
        )
        embed.add_field(name=lbl_might_title, value=lbl_might_val, inline=True)

        lbl_honor_title = t(langue, "server_honor_title", defaut="{e_honor} Honneur")
        lbl_honor_val = t(
            langue,
            "server_honor_val",
            t=fmt(t_honor),
            var=v_honor_str,
            avg=fmt(a_honor),
            defaut="• Global : `{t}`{var}\n• Moyen : `{avg}`",
        )
        embed.add_field(name=lbl_honor_title, value=lbl_honor_val, inline=True)

        lbl_loot_title = t(langue, "server_loot_title", defaut="{e_loot} Butin")
        lbl_loot_val = t(
            langue,
            "server_loot_val",
            t=fmt(t_loot),
            var=v_loot_str,
            avg=fmt(a_loot),
            max=fmt(m_loot),
            defaut="• Global : `{t}`{var}\n• Moyen : `{avg}`\n• Top 1 : `{max}`",
        )
        embed.add_field(name=lbl_loot_title, value=lbl_loot_val, inline=True)

        await setup_embed_footer(embed, interaction, langue)
        await interaction.followup.send(embed=embed)

    # ========================================================
    # 👑 COMMANDE : PLAYER PROFILE
    # ========================================================
    @player_group.command(name="profile", description="Detailed player profile")
    @app_commands.autocomplete(player=joueur_autocomplete)
    async def player_info(self, interaction: discord.Interaction, player: str):
        try:
            await interaction.response.defer(thinking=True)
        except:
            return

        langue, serveur = await get_server_config(interaction)
        lbl_date = t(langue, "guerre_lbl_date_data", defaut="⏱️ **Données datées de :**")

        try:
            full_json = await self._get_player_full_data(player, interaction, langue=langue)

            if not full_json:
                msg = t(
                    langue,
                    "error_player_not_found",
                    joueur=player,
                    defaut="{e_error} Joueur **{joueur}** introuvable.",
                )
                await interaction.followup.send(msg)
                return

            data = full_json.get("parsed_data", {})
            if not data:
                await interaction.followup.send(t(langue, "prof_err_empty_data", defaut="{e_error} Données vides."))
                return

            main_pts = int(data.get("main_points", 0))
            honor_pts = int(data.get("honor", 0))
            might_all_time = int(data.get("might_all_time", 0))
            max_honor = int(data.get("max_honor", 0))
            loot_current = int(data.get("loot_current", 0))
            loot_all_time = int(data.get("loot_all_time", 0))
            peace = data.get("peace_disabled_at")

            txt_colombe = ""
            if peace and peace != "null":
                try:
                    dt_peace = datetime.fromisoformat(peace.replace("Z", "+00:00"))
                    if dt_peace > discord.utils.utcnow():
                        txt_colombe = (
                            f"\n{DICT_EMOJIS.get('e_peace', '🕊️')} **Colombe** : <t:{int(dt_peace.timestamp())}:R>"
                        )
                except:
                    pass

            alliance_info = data.get("alliance", {})
            rank_id = alliance_info.get("rank")

            ranks_map = {
                0: t(langue, "prof_role_0", defaut="Chef"),
                1: t(langue, "prof_role_1", defaut="Représentant"),
                2: t(langue, "prof_role_2", defaut="Maréchal"),
                3: t(langue, "prof_role_3", defaut="Trésorier"),
                4: t(langue, "prof_role_4", defaut="Diplomate"),
                5: t(langue, "prof_role_5", defaut="Recruteur"),
                6: t(langue, "prof_role_6", defaut="Général"),
                7: t(langue, "prof_role_7", defaut="Sergent"),
                8: t(langue, "prof_role_8", defaut="Membre"),
                9: t(langue, "prof_role_9", defaut="Novice"),
            }

            fallback_role = (
                t(langue, "prof_role_fallback", r=rank_id, defaut=f" (Grade {rank_id})") if rank_id is not None else ""
            )
            role_txt = f" ({ranks_map[rank_id]})" if rank_id in ranks_map else fallback_role

            txt_sans_alliance = t(langue, "prof_no_alliance", defaut="Sans alliance")
            alliance_name = alliance_info.get("name", txt_sans_alliance)
            alliance_display = (
                f"**{alliance_name}**{role_txt}" if alliance_name != txt_sans_alliance else f"**{txt_sans_alliance}**"
            )

            outposts = data.get("outposts", [])
            type_emojis = {
                1: DICT_EMOJIS.get("e_castle1", "<:castle1:1512573817892110647>"),
                3: DICT_EMOJIS.get("e_castle3", "<:castle3:1512573819313979544>"),
                4: DICT_EMOJIS.get("e_castle4", "<:castle4:1512573820752498839>"),
                10: DICT_EMOJIS.get("e_date", "<:date:1512573832375042340>"),
                12: DICT_EMOJIS.get("e_castle12", "<:castle12:1521949211850182686>"),
                22: DICT_EMOJIS.get("e_castle22", "<:castle22:1512573821520183347>"),
                23: DICT_EMOJIS.get("e_castle23", "<:castle23:1512573823118086174>"),
                24: DICT_EMOJIS.get("e_aquamarine_16", "<:aquamarine_16:1512573724786950346>"),
                26: DICT_EMOJIS.get("e_castle26", "<:castle26:1512573824086835280>"),
            }
            sort_priority = {1: 0, 4: 1, 12: 2, 3: 3, 22: 4, 23: 5, 24: 7, 26: 6}

            if outposts:
                outposts.sort(key=lambda x: (sort_priority.get(int(x.get("type", 99)), 99), x.get("world_id", 0)))

            collected = full_json.get("collected_at", discord.utils.utcnow())
            if not isinstance(collected, datetime):
                collected = discord.utils.utcnow()
            ts = int(collected.timestamp())

            embed_title = t(
                langue,
                "prof_joueur_title",
                n=data.get("name", player),
                defaut="{e_players} Profil de {n}",
            )
            embed = discord.Embed(title=embed_title, color=self.clr_joueur)
            embed.description = f"{lbl_date} <t:{ts}:F> (<t:{ts}:R>)"

            info_title = t(langue, "prof_info_title", defaut="{e_information} Informations Générales")
            unk_id = t(langue, "prof_unknown", defaut="Inconnu")

            status_txt = t(langue, "prof_status_combat", defaut="{e_std_crossed_swords} **Statut :** Attaque possible")
            if peace and peace != "null":
                try:
                    dt_peace = datetime.fromisoformat(peace.replace("Z", "+00:00"))
                    if dt_peace > discord.utils.utcnow():
                        ts_peace = int(dt_peace.timestamp())
                        status_txt = t(
                            langue,
                            "prof_status_peace",
                            tp=ts_peace,
                            defaut="{e_peace} **Colombe :** Jusqu'au <t:{tp}:R> (<t:{tp}:t>)",
                        )
                except:
                    pass

            info_val = t(
                langue,
                "prof_info_val",
                lvl=data.get("level", 0),
                leg=data.get("legendary_level", 0),
                pid=data.get("player_id", unk_id),
                status=status_txt,
                defaut=("{e_lvl} **Niveau :** {lvl} (Lég. {leg})\n{e_listitem} **ID Joueur :** `{pid}`\n{status}"),
            )
            embed.add_field(name=info_title, value=info_val, inline=True)

            rank_title = t(langue, "prof_rank_title", defaut="{e_empirerankings} Statistiques")

            m_curr = f"{main_pts:,}".replace(",", " ")
            m_max = f"{might_all_time:,}".replace(",", " ")
            h_curr = f"{honor_pts:,}".replace(",", " ")
            h_max = f"{max_honor:,}".replace(",", " ")
            l_curr = f"{loot_current:,}".replace(",", " ")
            l_max = f"{loot_all_time:,}".replace(",", " ")

            rank_desc = t(
                langue,
                "prof_rank_desc",
                m_curr=m_curr,
                m_max=m_max,
                h_curr=h_curr,
                h_max=h_max,
                l_curr=l_curr,
                l_max=l_max,
                defaut=(
                    "{e_pp1} **Puissance :** {m_curr} (Max: `{m_max}`)\n"
                    "{e_honor} **Honneur :** {h_curr} (Max: `{h_max}`)\n"
                    "{e_loot} **Pillage :** {l_curr} (Max: `{l_max}`)"
                ),
            )

            embed.add_field(name=rank_title, value=rank_desc, inline=True)

            alli_title = t(langue, "prof_alli_title", defaut="{e_alliance_icon} Alliance")
            embed.add_field(name=alli_title, value=f"{alliance_display}", inline=False)

            if outposts:
                coords_txt = ""
                for op in outposts[:10]:
                    emoji = type_emojis.get(int(op.get("type", 99)), "?")
                    w_emoji = op.get("world_emoji", "🗺️")

                    c_name = op.get("custom_name")
                    display_name = f"**{c_name}**" if c_name else f"*{op['type_label']}*"

                    coords_txt += f"{w_emoji} [{emoji}] {display_name} ➔ `{op['coords_x']}:{op['coords_y']}`\n"

                if len(outposts) > 10:
                    coords_txt += (
                        t(
                            langue,
                            "prof_pos_others",
                            count=(len(outposts) - 10),
                            defaut="*... et {count} autres positions.*\n",
                        )
                        + "\n"
                    )

                pos_title = t(
                    langue,
                    "prof_pos_title",
                    count=len(outposts),
                    defaut="{e_compass} Positions ({count})",
                )
                embed.add_field(name=pos_title, value=coords_txt[:1024], inline=False)

            current_vassals = data.get("vassal_villages", [])
            if current_vassals:
                v_glace = len([v for v in current_vassals if v.get("world_id") == 2])
                v_sable = len([v for v in current_vassals if v.get("world_id") == 1])
                v_pic = len([v for v in current_vassals if v.get("world_id") == 3])
                v_orage = len([v for v in current_vassals if v.get("world_id") == 4])
                v_emp = len([v for v in current_vassals if v.get("world_id") == 0])

                v_txt = t(
                    langue,
                    "prof_vr_details",
                    vg=v_glace,
                    vs=v_sable,
                    vp=v_pic,
                    ve=v_emp,
                    vo=v_orage,
                    defaut="{e_dungeon2} Glace: **{vg}** | {e_dungeon1} Sables: **{vs}** | {e_dungeon3} Pics: **{vp}**",
                )
                v_coords = "\n".join(
                    [
                        f"{DICT_EMOJIS.get('e_date', '📅')} VR ({v['world_label']}) ➔ `{v['coords_x']}:{v['coords_y']}`"
                        for v in current_vassals[:5]
                    ]
                )

                vr_title = t(
                    langue,
                    "prof_vr_title",
                    count=len(current_vassals),
                    defaut="{e_date} Villages à Ressources ({count})",
                )
                embed.add_field(name=vr_title, value=f"{v_txt}\n\n{v_coords}"[:1024], inline=False)

            await setup_embed_footer(embed, interaction, langue)
            await interaction.followup.send(embed=embed)
            await prompt_vote_if_lucky(interaction, probability_percent=8, langue=langue)

        except Exception as e:
            logger.error(f"❌ [Profils - Joueur] Erreur fatale : {traceback.format_exc()}")
            try:
                await interaction.followup.send(
                    t(langue, "prof_err_internal", defaut="{e_error} Erreur système interne.")
                )
            except:
                pass

    # ========================================================
    # 🛰️ MÉTHODE INTERNE : COLLECTEUR DE DONNÉES JOUEUR
    # ========================================================
    async def _get_player_full_data(
        self, player_name: str, interaction: discord.Interaction = None, langue: str = "fr"
    ):
        headers = await get_api_headers(interaction)
        serveur = headers.get("gge-server", "E4K_FR1")
        api_url = "https://api.gge-tracker.com/api/v1"

        castle_types = {
            1: t(langue, "prof_castle_1", defaut="Château Principal"),
            3: t(langue, "prof_castle_3", defaut="Capitale"),
            4: t(langue, "prof_castle_4", defaut="Avant-Poste"),
            10: t(langue, "prof_castle_10", defaut="Village à Ressource"),
            12: t(langue, "prof_castle_12", defaut="Château Secondaire"),
            22: t(langue, "prof_castle_22", defaut="Cité Marchande"),
            23: t(langue, "prof_castle_23", defaut="Tour Royale"),
            24: t(langue, "prof_castle_24", defaut="Ile aux Ressources"),
            26: t(langue, "prof_castle_26", defaut="Monument"),
        }

        worlds = {
            0: t(langue, "prof_world_0", defaut="Le Grand Empire"),
            1: t(langue, "prof_world_1", defaut="Les Sables Brûlants"),
            2: t(langue, "prof_world_2", defaut="Glacier Éternel"),
            3: t(langue, "prof_world_3", defaut="Pics du Feu"),
            4: t(langue, "prof_world_4", defaut="Les Îles Orageuses"),
        }

        world_emojis = {
            0: DICT_EMOJIS.get("e_dungeon0", "<:dungeon0:1512573840704671775>"),
            1: DICT_EMOJIS.get("e_dungeon1", "<:dungeon1:1512573842277794062>"),
            2: DICT_EMOJIS.get("e_dungeon2", "<:dungeon2:1512573843267518546>"),
            3: DICT_EMOJIS.get("e_dungeon3", "<:dungeon3:1512573844538396692>"),
            4: DICT_EMOJIS.get("e_dungeon4", "<:dungeon4:1512573845737963722>"),
        }

        txt_unk_world = t(langue, "prof_world_unknown", defaut="Monde inconnu")
        txt_unk_castle = t(langue, "prof_castle_unknown", defaut="Type inconnu")

        safe_name = quote(str(player_name))
        search_url = f"{api_url}/players/{safe_name}"

        session = self.bot.session
        if not session:
            return None

        try:
            async with session.get(search_url, headers=headers, timeout=15) as response:
                if response.status != 200:
                    return None
                basic_info = await response.json()
                if isinstance(basic_info, list):
                    if not basic_info:
                        return None
                    basic_info = basic_info[0]

            player_id = basic_info.get("player_id")
            if not player_id:
                return None

            stats_url = f"{api_url}/statistics/ranking/player/{player_id}"
            async with session.get(stats_url, headers=headers, timeout=15) as stats_response:
                stats_data = {}
                if stats_response.status == 200:
                    stats_data = await stats_response.json()

            castle_names_map = {}
            search_castles_url = f"{api_url}/castle/search/{safe_name}"

            try:
                async with session.get(search_castles_url, headers=headers, timeout=10) as c_response:
                    if c_response.status == 200:
                        c_data = await c_response.json()
                        if isinstance(c_data, list):

                            async def fetch_castle_name(castle_id, kingdom_id, cx, cy):
                                analysis_url = f"{api_url}/castle/analysis/{castle_id}?kingdomId={kingdom_id}"
                                try:
                                    async with session.get(analysis_url, headers=headers, timeout=5) as a_response:
                                        if a_response.status == 200:
                                            a_data = await a_response.json()
                                            c_name = None

                                            if isinstance(a_data, list) and len(a_data) > 0:
                                                c_name = a_data[0].get("castleName")
                                            elif isinstance(a_data, dict):
                                                c_name = a_data.get("castleName")

                                            if c_name:
                                                return (int(cx), int(cy)), c_name
                                except Exception:
                                    pass
                                return None, None

                            tasks = []
                            for c in c_data:
                                c_id = c.get("id")
                                k_id = c.get("kingdomId")
                                cx = c.get("positionX")
                                cy = c.get("positionY")

                                if c_id is not None and k_id is not None and cx is not None and cy is not None:
                                    tasks.append(fetch_castle_name(c_id, k_id, int(cx), int(cy)))

                            if tasks:
                                results = await asyncio.gather(*tasks)
                                for coords, name in results:
                                    if coords and name:
                                        castle_names_map[coords] = name

            except Exception as e:
                logger.warning(f"⚠️ [API] Impossible de lier les noms de châteaux pour {player_name}: {e}")

            outposts = []
            vassal_villages = []

            for c in stats_data.get("castles", []):
                if len(c) >= 3:
                    t_id = c[2]
                    cx, cy = int(c[0]), int(c[1])
                    struct = {
                        "world_id": 0,
                        "coords_x": cx,
                        "coords_y": cy,
                        "type": t_id,
                        "world_label": worlds.get(0, worlds[0]),
                        "world_emoji": world_emojis.get(0, "?"),
                        "type_label": castle_types.get(t_id, f"{txt_unk_castle} ({t_id})"),
                        "custom_name": castle_names_map.get((cx, cy)),
                    }
                    if t_id == 10:
                        vassal_villages.append(struct)
                    else:
                        outposts.append(struct)

            for c in stats_data.get("castles_realm", []):
                if len(c) >= 4:
                    w_id, t_id = c[0], c[3]
                    cx, cy = int(c[1]), int(c[2])
                    struct = {
                        "world_id": w_id,
                        "coords_x": cx,
                        "coords_y": cy,
                        "type": t_id,
                        "world_label": worlds.get(w_id, f"{txt_unk_world} ({w_id})"),
                        "world_emoji": world_emojis.get(w_id, "?"),
                        "type_label": castle_types.get(t_id, f"{txt_unk_castle} ({t_id})"),
                        "custom_name": castle_names_map.get((cx, cy)),
                    }
                    if t_id == 10:
                        vassal_villages.append(struct)
                    else:
                        outposts.append(struct)

            for v in stats_data.get("villages", []):
                if len(v) >= 3:
                    w_id = v[0]
                    struct = {
                        "world_id": w_id,
                        "coords_x": v[1],
                        "coords_y": v[2],
                        "type": 10,
                        "world_label": worlds.get(w_id, f"{txt_unk_world} ({w_id})"),
                        "world_emoji": world_emojis.get(w_id, "?"),
                        "type_label": castle_types.get(10, t(langue, "prof_castle_10", defaut="Village à Ressource")),
                    }
                    vassal_villages.append(struct)

            parsed_data = {
                "player_id": basic_info.get("player_id"),
                "name": basic_info.get("player_name"),
                "level": basic_info.get("level", 0) or stats_data.get("level", 0),
                "legendary_level": basic_info.get("legendary_level", 0) or stats_data.get("legendary_level", 0),
                "honor": basic_info.get("honor", 0),
                "main_points": stats_data.get("might_current", 0) or stats_data.get("might", 0),
                "might_all_time": stats_data.get("might_all_time", 0),
                "max_honor": stats_data.get("max_honor", 0) or basic_info.get("max_honor", 0),
                "loot_current": stats_data.get("loot_current", 0) or basic_info.get("loot_current", 0),
                "loot_all_time": stats_data.get("loot_all_time", 0) or basic_info.get("loot_all_time", 0),
                "peace_disabled_at": basic_info.get("peace_disabled_at"),
                "alliance": {
                    "id": basic_info.get("alliance_id"),
                    "name": basic_info.get("alliance_name")
                    or stats_data.get("alliance_name")
                    or t(langue, "prof_no_alliance", defaut="Sans alliance"),
                    "rank": basic_info.get("alliance_rank") or stats_data.get("alliance_rank"),
                },
                "outposts": outposts,
                "vassal_villages": vassal_villages,
            }

            api_timestamp = _get_api_timestamp(stats_data, basic_info)

            return {
                "collected_at": api_timestamp,
                "player_name": basic_info.get("player_name"),
                "server": serveur,
                "parsed_data": parsed_data,
            }

        except Exception as e:
            logger.error(f"❌ Erreur API Joueur pour {player_name}: {e}")
            return None

    # ========================================================
    # 📜 COMMANDE : PLAYER HISTORY
    # ========================================================
    @player_group.command(name="history", description="Displays a player's complete history")
    @app_commands.autocomplete(player=joueur_autocomplete)
    async def history(self, interaction: discord.Interaction, player: str):
        try:
            await interaction.response.defer(thinking=True)
        except:
            return

        langue, serveur = await get_server_config(interaction)

        p_id = None
        actualisation_dt = discord.utils.utcnow()
        try:
            full_json = await self._get_player_full_data(player, interaction, langue=langue)
            if full_json:
                data = full_json.get("parsed_data", {})
                p_id = str(data.get("player_id", ""))
                actualisation_dt = full_json.get("collected_at", actualisation_dt)
        except:
            pass

        if not p_id:
            await interaction.followup.send(t(langue, "prof_hist_err_id", j=player, defaut="{e_error} ID introuvable."))
            return

        headers = await get_api_headers(interaction)

        urls_to_fetch = {
            "pseudo": f"https://api.gge-tracker.com/api/v1/updates/players/{p_id}/names",
            "alliance": f"https://api.gge-tracker.com/api/v1/updates/players/{p_id}/alliances",
            "position": f"https://api.gge-tracker.com/api/v1/server/movements?page=1&castleType=1&movementType=3&search={quote(player)}&searchType=player",
        }

        results = {}
        session = self.bot.session
        if session:

            async def fetch_url(name, url):
                try:
                    async with session.get(url, headers=headers, timeout=10) as resp:
                        if resp.status == 200:
                            results[name] = await resp.json()
                except:
                    pass

            tasks = [fetch_url(name, url) for name, url in urls_to_fetch.items()]
            await asyncio.gather(*tasks)

        def parse_date(iso_str):
            if not iso_str:
                return t(langue, "prof_hist_unknown_date", defaut="Date inconnue")
            return get_discord_timestamp(iso_str, "d", langue)

        embeds_dict = {"pseudo": [], "alliance": [], "position": []}
        lines_per_page = 12

        categories = [
            {
                "key": "pseudo",
                "title": t(langue, "prof_hist_cat_pseudo", defaut="Historique des Pseudos"),
                "emoji": DICT_EMOJIS.get("e_listitem", "📝"),
                "json_key": "updates",
            },
            {
                "key": "alliance",
                "title": t(langue, "prof_hist_cat_alliance", defaut="Historique des Alliances"),
                "emoji": DICT_EMOJIS.get("e_alliance_icon", "🛡️"),
                "json_key": "updates",
            },
            {
                "key": "position",
                "title": t(langue, "prof_hist_cat_position", defaut="Historique des Positions"),
                "emoji": DICT_EMOJIS.get("e_moove", DICT_EMOJIS.get("e_compass", "📍")),
                "json_key": "movements",
            },
        ]

        has_any_data = False
        unk_val = t(langue, "prof_hist_unknown", defaut="*Inconnu*")
        no_alli_val = t(langue, "prof_hist_no_alli", defaut="*Sans alliance*")

        if isinstance(actualisation_dt, str):
            try:
                actualisation_dt = datetime.fromisoformat(actualisation_dt.replace("Z", "+00:00"))
            except:
                actualisation_dt = discord.utils.utcnow()

        ts_act = int(actualisation_dt.timestamp())
        lbl_date = t(langue, "guerre_lbl_date_data", defaut="⏱️ **Données datées de :**")
        str_date_header = f"{lbl_date} <t:{ts_act}:F> (<t:{ts_act}:R>)\n\n"

        e_members_ic = DICT_EMOJIS.get("e_members", "👥")
        e_alli_ic = DICT_EMOJIS.get("e_icon_alliance", "🛡️")
        e_moove_ic = DICT_EMOJIS.get("e_moove", "🚚")

        for cat in categories:
            lines = []
            raw_data = results.get(cat["key"], {}).get(cat["json_key"], [])

            for item in raw_data:
                d = parse_date(item.get("date", item.get("created_at")))
                if cat["key"] == "pseudo":
                    old = item.get("old_player_name") or unk_val
                    new = item.get("new_player_name") or unk_val
                    lines.append(f"{e_members_ic} {d} : ~~{old}~~ ➔ **{new}**")
                elif cat["key"] == "alliance":
                    old = item.get("old_alliance_name") or no_alli_val
                    new = item.get("new_alliance_name") or no_alli_val
                    lines.append(f"{e_alli_ic} {d} : *{old}* ➔ **{new}**")
                elif cat["key"] == "position":
                    x_old, y_old = item.get("position_x_old"), item.get("position_y_old")
                    x_new, y_new = item.get("position_x_new"), item.get("position_y_new")
                    lines.append(f"{e_moove_ic} {d} : `{x_old}:{y_old}` ➔ `{x_new}:{y_new}`")

            if lines:
                has_any_data = True
                for i in range(0, len(lines), lines_per_page):
                    chunk = lines[i : i + lines_per_page]
                    page_num = (i // lines_per_page) + 1
                    total_pages_cat = (len(lines) + lines_per_page - 1) // lines_per_page
                    suffixe_titre = f" ({page_num}/{total_pages_cat})" if total_pages_cat > 1 else ""

                    emb = discord.Embed(
                        title=f"{cat['emoji']} {cat['title']} - {player}{suffixe_titre}",
                        description=str_date_header + "\n".join(chunk),
                        color=self.clr_historique,
                    )
                    await setup_embed_footer(emb, interaction, langue)
                    embeds_dict[cat["key"]].append(emb)
            else:
                desc_empty = t(langue, "prof_hist_no_data_cat", defaut=" Aucun historique disponible.")
                emb = discord.Embed(
                    title=f"{cat['emoji']} {cat['title']} - {player}",
                    description=str_date_header + desc_empty,
                    color=self.clr_historique,
                )
                await setup_embed_footer(emb, interaction, langue)
                embeds_dict[cat["key"]].append(emb)

        if not has_any_data:
            desc_empty_global = t(langue, "prof_hist_empty_desc", defaut="Aucun historique trouvé.")
            embed_vide = discord.Embed(
                title=t(langue, "prof_hist_empty_title", j=player, defaut=f"Dossier - {player}"),
                description=str_date_header + desc_empty_global,
                color=self.clr_historique,
            )
            await setup_embed_footer(embed_vide, interaction, langue)
            await interaction.followup.send(embed=embed_vide)
            return

        view = HistoriqueView(embeds_dict, interaction, langue=langue)
        view.message = await interaction.followup.send(embed=embeds_dict[view.current_cat][0], view=view, wait=True)
        await prompt_vote_if_lucky(interaction, probability_percent=8, langue=langue)

    # ========================================================
    # 🕊️ COMMANDE : PLAYER DOVE
    # ========================================================
    @player_group.command(name="dove", description="Check the date and time a player's protection ended")
    @app_commands.autocomplete(player=joueur_autocomplete)
    async def dove(self, interaction: discord.Interaction, player: str):
        await interaction.response.defer()
        langue, _ = await get_server_config(interaction)
        lbl_date = t(langue, "guerre_lbl_date_data", defaut="⏱️ **Données datées de :**")

        headers = await get_api_headers(interaction)
        url = f"https://api.gge-tracker.com/api/v1/players/{quote(player)}"

        session = self.bot.session
        if not session:
            return await interaction.followup.send(t(langue, "prof_pp_err_http", defaut="{e_error} Erreur."))

        try:
            async with session.get(url, headers=headers, timeout=5) as r:
                if r.status == 200:
                    data = await r.json()
                    p_data = data[0] if isinstance(data, list) and data else data
                    if not p_data:
                        return await interaction.followup.send(
                            t(langue, "prof_col_not_found", j=player, defaut="{e_error} Joueur introuvable.")
                        )

                    peace_str = p_data.get("peace_disabled_at")
                    if not peace_str or peace_str == "null":
                        return await interaction.followup.send(
                            t(langue, "prof_col_none", j=player, defaut="{e_information} Aucune colombe.")
                        )

                    dt_peace = datetime.fromisoformat(peace_str.replace("Z", "+00:00"))
                    maintenant = discord.utils.utcnow()
                    ts = int(dt_peace.timestamp())

                    if dt_peace > maintenant:
                        embed = discord.Embed(
                            title=t(langue, "prof_col_embed_title", defaut="{e_peace} Statut de la Colombe"),
                            color=self.clr_colombe,
                        )

                        api_timestamp = _get_api_timestamp(p_data)
                        ts_act = int(api_timestamp.timestamp())
                        embed.description = f"{lbl_date} <t:{ts_act}:F> (<t:{ts_act}:R>)"

                        embed.add_field(
                            name=t(langue, "prof_col_target", defaut="Cible"), value=f"**{player}**", inline=False
                        )
                        embed.add_field(
                            name=t(langue, "prof_col_end", defaut="Fin de protection"),
                            value=t(langue, "prof_col_end_val", ts=ts, defaut=f"<t:{ts}:f>"),
                            inline=False,
                        )

                        await setup_embed_footer(embed, interaction, langue)
                        await interaction.followup.send(embed=embed)
                        await prompt_vote_if_lucky(interaction, probability_percent=8, langue=langue)
                    else:
                        await interaction.followup.send(
                            t(
                                langue,
                                "prof_col_expired",
                                j=player,
                                ts=ts,
                                defaut="{e_information} La colombe a expiré.",
                            )
                        )
                        await prompt_vote_if_lucky(interaction, probability_percent=5, langue=langue)
                else:
                    await interaction.followup.send(
                        t(langue, "prof_col_err_api", j=player, s=r.status, defaut="{e_error} Erreur API.")
                    )
        except Exception:
            await interaction.followup.send(t(langue, "prof_col_err_conn", defaut="{e_error} Erreur de connexion."))

    # ========================================================
    # 🥊 COMMANDE : COMPARE (PLAYER)
    # ========================================================
    @player_group.command(
        name="compare", description="Responsive comparative analysis and calculation of the hazard index"
    )
    @app_commands.autocomplete(player1=joueur_autocomplete)
    @app_commands.autocomplete(player2=joueur_autocomplete)
    async def compare(self, interaction: discord.Interaction, player1: str, player2: str):
        try:
            await interaction.response.defer(thinking=True)
        except:
            return

        langue, _ = await get_server_config(interaction)

        headers = await get_api_headers(interaction)
        session = self.bot.session

        async def scruter_profil_tactique(nom_joueur: str):
            profil = {
                "nom": nom_joueur,
                "id": None,
                "lvl": 0,
                "leg": 0,
                "alliance": t(langue, "guerre_sa", defaut="Sans alliance"),
                "alli_might": 0,
                "alli_id": 0,
                "pp": 0,
                "gloire": 0,
                "butin": 0,
                "rang": 9999,
                "feux_count": 0,
                "feux_txt": "0",
                "colombe_txt": t(langue, "guerre_comp_free", defaut="Libre"),
                "malus_colombe": 0,
                "malus_feu": 0,
                "event_averages": {"nomades": 0, "samourais": 0, "corbeaux": 0, "etrangers": 0},
                "valide": False,
                "raw_api_data": None,
            }

            try:
                base_player_url = "https://api.gge-tracker.com/api/v1/players/"
                async with session.get(f"{base_player_url}{quote(nom_joueur)}", headers=headers, timeout=8) as r:
                    if r.status != 200:
                        return profil
                    res_base = await r.json()

                    if isinstance(res_base, list) and res_base:
                        res_base = res_base[0]
                    if not res_base:
                        return profil

                    profil["raw_api_data"] = res_base
                    profil["id"] = str(res_base.get("player_id", res_base.get("id", "")))
                    profil["nom"] = res_base.get("player_name", nom_joueur)
                    profil["lvl"] = int(res_base.get("level", 0))
                    profil["leg"] = int(res_base.get("legendary_level", 0))

                    all_name_raw = res_base.get("alliance_name")
                    profil["alliance"] = (
                        all_name_raw if all_name_raw else t(langue, "guerre_sa", defaut="Sans alliance")
                    )
                    profil["alli_id"] = res_base.get("allianceId", res_base.get("alliance_id", 0))
                    profil["valide"] = True

                    peace = res_base.get("peace_disabled_at")
                    if peace and peace != "null":
                        try:
                            if datetime.fromisoformat(peace.replace("Z", "+00:00")) > discord.utils.utcnow():
                                profil["colombe_txt"] = t(langue, "guerre_comp_protected", defaut="Protégé")
                                profil["malus_colombe"] = -1.0
                        except:
                            pass

                if not profil["id"]:
                    return profil

                rank_endpoint = f"https://api.gge-tracker.com/api/v1/statistics/ranking/player/{profil['id']}"
                stats_endpoint = f"https://api.gge-tracker.com/api/v1/statistics/player/{profil['id']}"
                search_endpoint = f"https://api.gge-tracker.com/api/v1/castle/search/{quote(profil['nom'])}"

                res_rank, res_stats, res_castles = await asyncio.gather(
                    *[
                        asyncio.create_task(session.get(rank_endpoint, headers=headers, timeout=6)),
                        asyncio.create_task(session.get(stats_endpoint, headers=headers, timeout=6)),
                        asyncio.create_task(session.get(search_endpoint, headers=headers, timeout=6)),
                    ]
                )

                if res_rank.status == 200:
                    dr = await res_rank.json()
                    profil["pp"] = int(dr.get("might_current", dr.get("might", 0)))
                    profil["gloire"] = int(dr.get("current_fame", dr.get("fame", 0)))
                    profil["butin"] = int(dr.get("loot_current", dr.get("loot", 0)))
                    profil["rang"] = int(dr.get("server_rank", 9999))

                if res_stats.status == 200:
                    ds = await res_stats.json()
                    pts_dict = ds.get("points", {})

                    piliers_mapping = {
                        "nomades": "player_event_nomad_history",
                        "samourais": "player_event_samurai_history",
                        "corbeaux": "player_event_bloodcrow_history",
                        "etrangers": "player_event_war_realms_history",
                    }

                    for pilier_nom, cle_api in piliers_mapping.items():
                        ev_list = pts_dict.get(cle_api, [])
                        if isinstance(ev_list, list) and ev_list:
                            valid_entries = []
                            for e in ev_list:
                                d_str = e.get("date")
                                pt_str = str(e.get("point", ""))
                                if d_str and pt_str.replace("-", "").isdigit():
                                    try:
                                        dt = datetime.fromisoformat(d_str.replace("Z", "+00:00"))
                                        valid_entries.append((dt, int(pt_str)))
                                    except:
                                        pass

                            if not valid_entries:
                                continue

                            valid_entries.sort(key=lambda x: x[0])

                            event_max_scores = []
                            current_max = valid_entries[0][1]
                            last_dt = valid_entries[0][0]

                            for dt, pt in valid_entries[1:]:
                                if (dt - last_dt).total_seconds() > 96 * 3600:
                                    event_max_scores.append(current_max)
                                    current_max = pt
                                else:
                                    if pt > current_max:
                                        current_max = pt
                                last_dt = dt

                            event_max_scores.append(current_max)

                            derniers_events = event_max_scores[-3:]
                            if derniers_events:
                                profil["event_averages"][pilier_nom] = sum(derniers_events) // len(derniers_events)

                if res_castles.status == 200:
                    dc = await res_castles.json()
                    if isinstance(dc, dict):
                        dc = [dc]
                    if isinstance(dc, list):
                        c_id = None
                        for c in dc:
                            if not isinstance(c, dict):
                                continue
                            if str(c.get("kingdomId", "0")) == "0" and str(c.get("type", "1")) == "1":
                                c_id = c.get("id")
                                break

                        if c_id:
                            analysis_url = f"https://api.gge-tracker.com/api/v1/castle/analysis/{c_id}"
                            async with session.get(analysis_url, headers=headers, timeout=5) as a_resp:
                                if a_resp.status == 200:
                                    castle_data = (await a_resp.json()).get("data", {})
                                    all_elements = []
                                    for category_list in castle_data.values():
                                        if isinstance(category_list, list):
                                            all_elements.extend(category_list)

                                    f_count = sum(
                                        1
                                        for b in all_elements
                                        if b.get("damageFactor") == 1 or str(b.get("damageFactor")).startswith("1")
                                    )
                                    profil["feux_count"] = f_count
                                    profil["feux_txt"] = f"{f_count}"
                                    if f_count > 0:
                                        profil["malus_feu"] = -1.0

                if all_name_raw and all_name_raw != "Sans alliance":
                    try:
                        alli_url = f"https://api.gge-tracker.com/api/v1/alliances/name/{quote(all_name_raw)}"
                        async with session.get(alli_url, headers=headers, timeout=5) as ar:
                            if ar.status == 200:
                                da = await ar.json()
                                if isinstance(da, list) and da:
                                    da = da[0]
                                profil["alli_might"] = int(da.get("might_current", da.get("total_might", 0)))
                    except:
                        pass

            except Exception as e:
                logger.error(f"❌ Erreur sur {nom_joueur} : {e}")
            return profil

        p1, p2 = await asyncio.gather(scruter_profil_tactique(player1), scruter_profil_tactique(player2))

        if not p1["valide"] or not p2["valide"]:
            return await interaction.followup.send(
                t(
                    langue,
                    "guerre_comp_err_load",
                    defaut="{e_error} Impossible de charger l'un des profils.",
                )
            )

        actualisation_dt = _get_api_timestamp(p1.get("raw_api_data"), p2.get("raw_api_data"))

        def duel(v1, v2, inverse=False):
            if v1 == v2:
                return "", ""
            cond = (v1 < v2) if inverse else (v1 > v2)
            return ("▲", "") if cond else ("", "▲")

        lvl_1, lvl_2 = duel((p1["lvl"], p1["leg"]), (p2["lvl"], p2["leg"]))
        rnk_1, rnk_2 = duel(p1["rang"], p2["rang"], inverse=True)
        pp_1, pp_2 = duel(p1["pp"], p2["pp"])
        glr_1, glr_2 = duel(p1["gloire"], p2["gloire"])
        btn_1, btn_2 = duel(p1["butin"], p2["butin"])
        am_1, am_2 = duel(p1["alli_might"], p2["alli_might"])

        nom_1, nom_2 = duel(p1["event_averages"]["nomades"], p2["event_averages"]["nomades"])
        sam_1, sam_2 = duel(p1["event_averages"]["samourais"], p2["event_averages"]["samourais"])
        cor_1, cor_2 = duel(p1["event_averages"]["corbeaux"], p2["event_averages"]["corbeaux"])
        et_1, et_2 = duel(p1["event_averages"]["etrangers"], p2["event_averages"]["etrangers"])

        score1 = (
            sum([1.0 for x in [lvl_1, am_1, rnk_1, pp_1, glr_1, btn_1] if x])
            + sum([0.25 for x in [nom_1, sam_1, cor_1, et_1] if x])
            + p1["malus_colombe"]
            + p1["malus_feu"]
        )
        score2 = (
            sum([1.0 for x in [lvl_2, am_2, rnk_2, pp_2, glr_2, btn_2] if x])
            + sum([0.25 for x in [nom_2, sam_2, cor_2, et_2] if x])
            + p2["malus_colombe"]
            + p2["malus_feu"]
        )
        score1, score2 = max(0.0, score1), max(0.0, score2)

        desc_calcul = t(
            langue,
            "guerre_comp_desc",
            n1=p1["nom"],
            s1=score1,
            n2=p2["nom"],
            s2=score2,
            defaut=(
                f"📊 **Comparaison sur plusieurs points**\n\n"
                f"⚙️ **Méthode de calcul de l'Indice :**\n"
                f"• Métriques Militaires/Solo : `+1.0 pt` par supériorité brute.\n"
                f"• Piliers Événementiels : `+0.25 pt` par moyenne glissante (3 éd.) supérieure.\n"
                f"• États de Robustesse : `-1.0 pt` fixe par handicap actif (Feux ou Colombe).\n\n"
                f"{{e_empirerankings}} **Résultat :**\n"
                f"🔵 **{p1['nom']}** (`{score1}🏆`) 🆚 🔴 **{p2['nom']}** (`{score2}🏆`)"
            ),
        )

        embed = discord.Embed(
            title=t(
                langue,
                "guerre_comp_title",
                defaut="{e_icon_analyze} Analyse : Grille de Confrontation",
            ),
            color=self.clr_compare_j,
        )

        lbl_date = t(langue, "guerre_lbl_date_data", defaut="⏱️ **Données datées de :**")
        embed.description = f"{lbl_date} <t:{int(actualisation_dt.timestamp())}:F> (<t:{int(actualisation_dt.timestamp())}:R>)\n\n{desc_calcul}"

        def build_row(label, v1, w1, v2, w2):
            str1 = f"{v1} {w1}".strip()
            str2 = f"{v2} {w2}".strip()
            return f"{label:<12} │ {str1:<12} │ {str2}"

        p1_all, p2_all = p1["alliance"][:10], p2["alliance"][:10]
        p1_lvl, p2_lvl = f"{p1['lvl']}(L.{p1['leg']})", f"{p2['lvl']}(L.{p2['leg']})"
        p1_rnk, p2_rnk = f"#{p1['rang']}", f"#{p2['rang']}"

        lbl_alli_s = t(langue, "ev_short_alli", defaut="Alli.")
        lbl_pa_s = t(langue, "ev_short_puiss_alli", defaut="Puiss. Alli")
        lbl_niv_s = t(langue, "ev_short_niv", defaut="Niv.")
        lbl_rang_s = t(langue, "ev_short_rang", defaut="Rang")
        lbl_puiss_s = t(langue, "ev_short_puiss", defaut="Puiss.")
        lbl_inc_s = t(langue, "ev_short_incendies", defaut="Incendies")

        lbl_nomad_s = t(langue, "ev_short_nomad", defaut="Nomad.")
        lbl_samou_s = t(langue, "ev_short_samou", defaut="Samou.")
        lbl_corb_s = t(langue, "ev_short_corb", defaut="Corbeaux")
        lbl_etr_s = t(langue, "ev_short_etr", defaut="Étrangers")

        lbl_gloire_s = t(langue, "ev_short_gloire", defaut="Gloire")
        lbl_pill_s = t(langue, "ev_short_pillage", defaut="Pillage/J")
        lbl_col_s = t(langue, "ev_short_colombe", defaut="Colombe")

        lbl_f1 = t(langue, "guerre_comp_f1", defaut="{e_players} Fiche d'Identité Générale")

        embed.add_field(
            name=lbl_f1,
            value=f"```\n{build_row(lbl_alli_s, p1_all, am_1, p2_all, am_2)}\n{build_row(lbl_pa_s, format_num(p1['alli_might']), '', format_num(p2['alli_might']), '')}\n{build_row(lbl_niv_s, p1_lvl, lvl_1, p2_lvl, lvl_2)}\n{build_row(lbl_rang_s, p1_rnk, rnk_1, p2_rnk, rnk_2)}\n```",
            inline=False,
        )

        lbl_f2 = t(langue, "guerre_comp_f2", defaut="{e_2_} Axe Militaire & Robustesse")
        embed.add_field(
            name=lbl_f2,
            value=f"```\n{build_row(lbl_puiss_s, format_num(p1['pp']), pp_1, format_num(p2['pp']), pp_2)}\n{build_row(lbl_inc_s, p1['feux_txt'], '', p2['feux_txt'], '')}\n```",
            inline=False,
        )

        lbl_f3 = t(langue, "guerre_comp_f3", defaut="{e_events4} Suivi des Événements (Moy. 3 éd.)")
        embed.add_field(
            name=lbl_f3,
            value=f"```\n{build_row(lbl_nomad_s, format_num(p1['event_averages']['nomades']), nom_1, format_num(p2['event_averages']['nomades']), nom_2)}\n{build_row(lbl_samou_s, format_num(p1['event_averages']['samourais']), sam_1, format_num(p2['event_averages']['samourais']), sam_2)}\n{build_row(lbl_corb_s, format_num(p1['event_averages']['corbeaux']), cor_1, format_num(p2['event_averages']['corbeaux']), cor_2)}\n{build_row(lbl_etr_s, format_num(p1['event_averages']['etrangers']), et_1, format_num(p2['event_averages']['etrangers']), et_2)}\n```",
            inline=False,
        )

        lbl_f4 = t(langue, "guerre_comp_f4", defaut="{e_icon_points} Activité & Vigilance")
        embed.add_field(
            name=lbl_f4,
            value=f"```\n{build_row(lbl_gloire_s, format_num(p1['gloire']), glr_1, format_num(p2['gloire']), glr_2)}\n{build_row(lbl_pill_s, format_num(p1['butin']), btn_1, format_num(p2['butin']), btn_2)}\n{build_row(lbl_col_s, p1['colombe_txt'], '', p2['colombe_txt'], '')}\n```",
            inline=False,
        )

        verdict = f"**{p1['nom']}** ({score1}🏆) vs **{p2['nom']}** ({score2}🏆)."
        if p1["malus_colombe"] < 0 or p1["malus_feu"] < 0 or p2["malus_colombe"] < 0 or p2["malus_feu"] < 0:
            verdict += t(
                langue,
                "guerre_comp_malus",
                defaut=" Les handicaps structurels (incendies de châteaux ou colombes d'esquive) ont lourdement grevé l'indice opérationnel.",
            )

        lbl_f5 = t(langue, "guerre_comp_f5", defaut="🎤 Rapport de comparaison")
        embed.add_field(name=lbl_f5, value=f"> *{verdict}*", inline=False)
        await setup_embed_footer(embed, interaction, langue)

        await interaction.followup.send(embed=embed)

    # ========================================================
    # 🛡️ COMMANDE : ALLIANCE
    # ========================================================
    @alliance_group.command(name="profile", description="Detailed profile of an alliance (Quick and paginated)")
    @app_commands.autocomplete(alliance_name=alliance_autocomplete)
    async def alliance_info(self, interaction: discord.Interaction, alliance_name: str):
        try:
            await interaction.response.defer(thinking=True)
        except:
            return

        langue, serveur = await get_server_config(interaction)
        txt_unknown = t(langue, "prof_unknown", defaut="Inconnu")
        lbl_date = t(langue, "guerre_lbl_date_data", defaut="⏱️ **Données datées de :**")

        try:
            is_live = False
            target_alliance_id = None
            total_might = total_fame = total_honor = 0
            leader_name = txt_unknown
            members = []
            collected_time = None

            try:
                api_data = await self._get_alliance_full_data(alliance_name, interaction, langue=langue)

                if api_data and "parsed_data" in api_data:
                    is_live = True
                    collected_time = api_data.get("collected_at")
                    parsed = api_data["parsed_data"]
                    target_alliance_id = parsed.get("alliance_id")
                    alliance_name = parsed.get("name", alliance_name)
                    leader_name = parsed.get("leader", txt_unknown)
                    total_might = parsed.get("total_might", 0)
                    total_honor = parsed.get("total_honor", 0)
                    total_fame = parsed.get("total_fame", 0)
                    members = parsed.get("members", [])
            except Exception as e:
                logger.warning(f"⚠️ [Profils - Alliance] API inaccessible, passage au Plan B... ({e})")

            if not is_live:
                player_files = list((BASE_DATA_PATH / "server_scans" / serveur).rglob("server_*.json"))
                local_data = {}

                if player_files:
                    latest = max(player_files, key=lambda p: p.stat().st_mtime)

                    def _load_local_json():
                        with open(latest, encoding="utf-8") as f:
                            return json.load(f)

                    full_json = await asyncio.to_thread(_load_local_json)
                    local_data = full_json.get("players", {})
                    collected_time = full_json.get("collected_at")

                for p_info in local_data.values():
                    a_obj = p_info.get("alliance")
                    a_name = a_obj.get("name") if isinstance(a_obj, dict) else (p_info.get("alliance_name") or a_obj)
                    if a_name and str(a_name).lower() == alliance_name.lower():
                        aid = p_info.get("allianceId") or p_info.get("alliance_id")
                        if not aid and isinstance(a_obj, dict):
                            aid = a_obj.get("allianceId") or a_obj.get("alliance_id")
                        if aid:
                            target_alliance_id = str(aid)
                            alliance_name = str(a_name)
                            break

                if not target_alliance_id:
                    return await interaction.followup.send(
                        t(
                            langue,
                            "prof_alli_not_found",
                            nom=alliance_name,
                            defaut="{e_error} Alliance **{nom}** introuvable.",
                        )
                    )

                for p_info in local_data.values():
                    a_obj = p_info.get("alliance")
                    aid = p_info.get("allianceId") or p_info.get("alliance_id")
                    if not aid and isinstance(a_obj, dict):
                        aid = a_obj.get("allianceId") or a_obj.get("alliance_id")

                    if str(aid) == target_alliance_id:
                        m_rank = int(p_info.get("alliance_rank", 9))
                        m_might = int(p_info.get("main_points") or p_info.get("might_current") or 0)
                        m_fame = int(p_info.get("fame") or 0)
                        m_honor = int(p_info.get("honor") or 0)

                        total_might += m_might
                        total_fame += m_fame
                        total_honor += m_honor

                        m_name = p_info.get("name", txt_unknown)
                        if str(m_rank) in ["0", "1"] and (leader_name == txt_unknown or str(m_rank) == "0"):
                            leader_name = m_name

                        members.append(
                            {
                                "name": m_name,
                                "level": p_info.get("level", 0),
                                "leg_level": p_info.get("legendary_level", 0),
                                "might": m_might,
                                "fame": m_fame,
                                "honor": m_honor,
                                "rank": m_rank,
                            }
                        )

                members.sort(key=lambda x: (int(x.get("rank", 9)), -x.get("might", 0)))

            if not members:
                return await interaction.followup.send(
                    t(
                        langue,
                        "prof_alli_ghost",
                        alli=alliance_name,
                        defaut="{e_error} L'alliance **{alli}** semble vide.",
                    )
                )

            if isinstance(collected_time, str):
                try:
                    collected_time = datetime.fromisoformat(collected_time.replace("Z", "+00:00"))
                except:
                    collected_time = discord.utils.utcnow()
            elif not collected_time:
                collected_time = discord.utils.utcnow()

            ts = int(collected_time.timestamp())
            suffixe_cache = " *(Plan B activé)*" if not is_live else ""
            str_date_header = f"{lbl_date} <t:{ts}:F> (<t:{ts}:R>){suffixe_cache}\n\n"

            embeds = []
            rank_emojis = {
                0: DICT_EMOJIS.get("e_title_0", "<:0_:1512574737677684818>"),
                1: DICT_EMOJIS.get("e_title_1", "<:1_:1512574739208470640>"),
                2: DICT_EMOJIS.get("e_title_2", "<:2_:1512574740915818527>"),
                3: DICT_EMOJIS.get("e_title_3", "<:3_:1512574742245412874>"),
                4: DICT_EMOJIS.get("e_title_4", "<:4_:1512574743369224303>"),
                5: DICT_EMOJIS.get("e_title_5", "<:5_:1512574744501817515>"),
                6: DICT_EMOJIS.get("e_title_6", "<:6_:1512574745617498172>"),
                7: DICT_EMOJIS.get("e_title_7", "<:7_:1512574746989039839>"),
                8: DICT_EMOJIS.get("e_title_8", "<:8_:1512574748356251691>"),
                9: DICT_EMOJIS.get("e_title_9", "<:9_:1512574749430120519>"),
            }
            chunk_size = 15
            nb_pages = max(1, (len(members) - 1) // chunk_size + 1)

            for i in range(0, len(members), chunk_size):
                chunk = members[i : i + chunk_size]
                page_actuelle = (i // chunk_size) + 1

                embed_title = t(
                    langue,
                    "prof_alli_embed_title",
                    a=alliance_name,
                    defaut="{e_alliance_icon} Alliance : {a}",
                )
                embed = discord.Embed(title=embed_title, color=self.clr_alliance)
                embed.description = str_date_header.strip()

                info_title = t(langue, "prof_info_title", defaut="{e_information} Informations")
                info_desc = t(
                    langue,
                    "prof_alli_info_desc",
                    l=leader_name,
                    c=len(members),
                    id=target_alliance_id,
                    defaut=f"**Chef** : {leader_name}\n**Membres** : {len(members)} / 65",
                )
                embed.add_field(name=info_title, value=info_desc, inline=True)

                stats_title = t(langue, "prof_alli_stats_title", defaut="{e_stats} Statistiques Globales")
                stats_desc = t(
                    langue,
                    "prof_alli_stats_desc",
                    m=format_num(total_might),
                    f=format_num(total_fame),
                    h=format_num(total_honor),
                    defaut=f"**Puiss.** : {format_num(total_might)}",
                )
                embed.add_field(name=stats_title, value=stats_desc, inline=True)

                memb_txt = ""
                for m in chunk:
                    lvl = m.get("level", 0)
                    leg = m.get("leg_level", m.get("leg", 0))
                    emoji = rank_emojis.get(int(m.get("rank", 9)), DICT_EMOJIS.get("e_players", "👤"))
                    memb_txt += f"{emoji} **{m.get('name', txt_unknown)}** ({lvl}/{leg}) ➔ {format_num(m.get('might', 0))} | {format_num(m.get('fame', 0))}\n"

                memb_title = t(
                    langue,
                    "prof_alli_members_title",
                    cur=page_actuelle,
                    tot=nb_pages,
                    defaut=f"Membres (Page {page_actuelle}/{nb_pages})",
                )
                embed.add_field(name=memb_title, value=memb_txt, inline=False)

                await setup_embed_footer(embed, interaction, langue)
                embeds.append(embed)

            if len(embeds) == 1:
                await interaction.followup.send(embed=embeds[0])
            else:
                view = PaginationView(embeds)
                await interaction.followup.send(embed=embeds[0], view=view)
            await prompt_vote_if_lucky(interaction, probability_percent=8, langue=langue)

        except Exception as e:
            logger.error(f"❌ [Profils - Alliance] Erreur fatale : {traceback.format_exc()}")
            try:
                await interaction.followup.send(
                    t(langue, "prof_alli_err_internal", defaut="{e_error} Erreur système interne.")
                )
            except:
                pass

    # ========================================================
    # 🛰️ MÉTHODE INTERNE : COLLECTEUR DE DONNÉES ALLIANCE
    # ========================================================
    async def _get_alliance_full_data(
        self, alliance_name: str, interaction: discord.Interaction = None, langue: str = "fr"
    ):
        headers = await get_api_headers(interaction)
        serveur = headers.get("gge-server", "E4K_FR1")
        api_url = "https://api.gge-tracker.com/api/v1"

        safe_name = quote(str(alliance_name))
        search_url = f"{api_url}/alliances/name/{safe_name}"

        session = self.bot.session
        if not session:
            return None

        try:
            async with session.get(search_url, headers=headers, timeout=10) as resp:
                if resp.status != 200:
                    return None
                data1 = await resp.json()
        except Exception:
            return None

        target_alliance = data1[0] if isinstance(data1, list) and data1 else data1
        if not target_alliance:
            return None

        alliance_id = (
            target_alliance.get("alliance_id") or target_alliance.get("id") or target_alliance.get("allianceId")
        )
        if not alliance_id:
            return None

        detail_url = f"{api_url}/alliances/id/{alliance_id}"
        stats_url = f"{api_url}/statistics/alliance/{alliance_id}"
        pulse_url = f"{api_url}/statistics/alliance/{alliance_id}/pulse"

        async def fetch_json_local(url, timeout_val):
            try:
                async with session.get(url, headers=headers, timeout=timeout_val) as r:
                    if r.status == 200:
                        return await r.json()
            except:
                pass
            return None

        members_data, stats_data, pulse_data = await asyncio.gather(
            fetch_json_local(detail_url, 30), fetch_json_local(stats_url, 40), fetch_json_local(pulse_url, 30)
        )

        if isinstance(members_data, list) and len(members_data) > 0:
            members_data = members_data[0]
        elif not members_data:
            members_data = {}

        stats_data = stats_data or {}
        pulse_data = pulse_data or {}

        members = members_data.get("players", members_data.get("members", members_data.get("playerList", [])))

        parsed_members = []
        tot_might = tot_honor = tot_fame = 0
        txt_unknown = t(langue, "prof_unknown", defaut="Inconnu")
        leader_name = txt_unknown

        for m in members:
            rank = m.get("allianceRank", m.get("alliance_rank", m.get("rank", 9)))
            might = int(m.get("might_current", m.get("might", m.get("main_points", 0))))
            honor = int(m.get("honor", 0))
            fame = int(m.get("current_fame", m.get("fame", 0)))

            tot_might += might
            tot_honor += honor
            tot_fame += fame

            if str(rank) in ["0", "1"]:
                if leader_name == txt_unknown or str(rank) == "0":
                    leader_name = m.get("player_name", m.get("playerName", m.get("name", txt_unknown)))

            parsed_members.append(
                {
                    "name": m.get("player_name", m.get("playerName", m.get("name", txt_unknown))),
                    "might": might,
                    "honor": honor,
                    "fame": fame,
                    "level": m.get("level", 0),
                    "leg_level": m.get("legendary_level", m.get("legendaryLevel", 0)),
                    "rank": rank,
                }
            )

        parsed_members.sort(key=lambda x: (int(x["rank"]), -x["might"]))

        txt_unk_alli = t(langue, "prof_unknown_alli", defaut="Inconnue")
        parsed_data = {
            "alliance_id": target_alliance.get("alliance_id") or target_alliance.get("allianceId"),
            "name": target_alliance.get("alliance_name") or target_alliance.get("name", txt_unk_alli),
            "members_count": len(parsed_members),
            "leader": leader_name,
            "total_might": tot_might,
            "total_honor": tot_honor,
            "total_fame": tot_fame,
            "members": parsed_members,
            "stats_diffs": stats_data.get("diffs", {}),
            "stats_history": {
                "loot": stats_data.get("points", {}).get("player_loot_history", []),
                "might": stats_data.get("points", {}).get("player_might_history", []),
            },
            "pulse": pulse_data,
        }

        api_timestamp = _get_api_timestamp(members_data, stats_data, target_alliance)

        return {
            "collected_at": api_timestamp,
            "alliance_name": parsed_data["name"],
            "server": serveur,
            "parsed_data": parsed_data,
        }

    # ========================================================
    # 📈 COMMANDE : HISTORIQUE ALLIANCE MIGHT
    # ========================================================
    @alliance_group.command(name="might", description="Historical Power (PP) of an alliance over X days")
    @app_commands.autocomplete(alliance_name=alliance_autocomplete)
    @app_commands.describe(days="Period to analyze in days (Default: 3, Maximum: 10)")
    async def alliance_might(self, interaction: discord.Interaction, alliance_name: str, days: int = 3):
        try:
            await interaction.response.defer(thinking=True)
        except:
            return

        langue, serveur = await get_server_config(interaction)

        days = max(1, min(10, days))
        date_limite = discord.utils.utcnow() - timedelta(days=days)

        headers = await get_api_headers(interaction)
        safe_alliance = quote(alliance_name)
        lbl_date = t(langue, "guerre_lbl_date_data", defaut="⏱️ **Données datées de :**")

        session = self.bot.session
        if not session:
            return await interaction.followup.send(t(langue, "prof_pp_err_http", defaut="{e_error} Erreur connexion."))

        try:
            search_url = f"https://api.gge-tracker.com/api/v1/alliances/name/{safe_alliance}"
            async with session.get(search_url, headers=headers, timeout=10) as resp:
                if resp.status != 200:
                    return await interaction.followup.send(
                        t(langue, "prof_pp_err_not_found", defaut="{e_error} Alliance introuvable.")
                    )
                data1 = await resp.json()
                target = data1[0] if isinstance(data1, list) and data1 else data1
                alliance_id = target.get("alliance_id") or target.get("id")
        except Exception:
            return await interaction.followup.send(t(langue, "prof_pp_err_api", defaut="{e_error} Erreur API."))

        if not alliance_id:
            return await interaction.followup.send(
                t(langue, "prof_pp_err_alli_nf", a=alliance_name, defaut="{e_error} Alliance introuvable.")
            )

        try:
            stats_url = f"https://api.gge-tracker.com/api/v1/statistics/alliance/{alliance_id}"
            detail_url = f"https://api.gge-tracker.com/api/v1/alliances/id/{alliance_id}"

            async def fetch_json(url):
                try:
                    async with session.get(url, headers=headers, timeout=15) as r:
                        if r.status == 200:
                            return await r.json()
                except:
                    pass
                return None

            res_stats, res_detail = await asyncio.gather(fetch_json(stats_url), fetch_json(detail_url))
            if not res_stats:
                return await interaction.followup.send(
                    t(langue, "prof_pp_err_dl", defaut="{e_error} Téléchargement impossible.")
                )

            stats_data = res_stats
            detail_data = res_detail or {}

        except Exception:
            return await interaction.followup.send(
                t(langue, "prof_pp_err_dl_stats", defaut="{e_error} Téléchargement impossible.")
            )

        might_history = stats_data.get("points", {}).get("player_might_history", [])
        if not might_history:
            return await interaction.followup.send(
                t(langue, "prof_pp_no_hist", a=alliance_name, defaut="{e_information} Aucun historique.")
            )

        daily_data = {}
        for entry in might_history:
            d_str = entry.get("date")
            pid = str(entry.get("player_id"))
            pt = int(entry.get("point", 0))
            if not d_str:
                continue

            try:
                dt = datetime.fromisoformat(d_str.replace("Z", "+00:00"))
                if dt < date_limite:
                    continue
                day_str = dt.strftime("%d/%m/%Y")
                if day_str not in daily_data:
                    daily_data[day_str] = {}
                daily_data[day_str][pid] = max(daily_data[day_str].get(pid, 0), pt)
            except:
                pass

        if not daily_data:
            return await interaction.followup.send(
                t(langue, "prof_pp_no_data_days", a=alliance_name, j=days, defaut="{e_information} Aucune donnée.")
            )

        alliance_daily_might = {}
        for day, players in daily_data.items():
            alliance_daily_might[day] = sum(players.values())

        sorted_days = sorted(alliance_daily_might.keys(), key=lambda d: datetime.strptime(d, "%d/%m/%Y"))

        premier_jour = sorted_days[0]
        dernier_jour = sorted_days[-1]

        pp_debut = alliance_daily_might[premier_jour]
        pp_fin = alliance_daily_might[dernier_jour]
        variation_totale = pp_fin - pp_debut

        pic_pp = max(alliance_daily_might.values())
        pire_pp = min(alliance_daily_might.values())

        def format_diff(val):
            if val > 0:
                return f"+{format_num(val)}"
            elif val < 0:
                return f"{format_num(val)}"
            return "0"

        stats_txt = t(
            langue,
            "prof_pp_bilan_desc",
            p=premier_jour,
            d=dernier_jour,
            p_d=format_num(pp_debut),
            p_f=format_num(pp_fin),
            v=format_diff(variation_totale),
            pic=format_num(pic_pp),
            pire=format_num(pire_pp),
            defaut=f"\n\n**Période** : du {premier_jour} au {dernier_jour}",
        )

        lignes_historique = []
        for day in reversed(sorted_days):
            pp_jour = alliance_daily_might[day]
            index_jour = sorted_days.index(day)
            if index_jour > 0:
                pp_hier = alliance_daily_might[sorted_days[index_jour - 1]]
                diff_jour = pp_jour - pp_hier
                diff_txt = f"({format_diff(diff_jour)})"
            else:
                diff_txt = t(langue, "prof_pp_start_point", defaut="(Point de départ)")

            lignes_historique.append(f"• **{day}** ➔ **{format_num(pp_jour)} PP** {diff_txt}")

        embeds = []
        chunk_size = 15
        nb_pages = max(1, (len(lignes_historique) - 1) // chunk_size + 1)
        alliance_name_real = target.get("alliance_name") or target.get("name", alliance_name)

        api_timestamp = _get_api_timestamp(detail_data, target, stats_data)
        ts_act = int(api_timestamp.timestamp())
        str_date_header = f"{lbl_date} <t:{ts_act}:F> (<t:{ts_act}:R>)\n\n"

        for i in range(0, len(lignes_historique), chunk_size):
            chunk = lignes_historique[i : i + chunk_size]
            page_actuelle = (i // chunk_size) + 1

            embed = discord.Embed(
                title=t(
                    langue,
                    "prof_pp_embed_title",
                    a=alliance_name_real,
                    defaut=f"Évolution de la Puissance pour : {alliance_name_real}",
                ),
                color=self.clr_alliance_pp,
            )
            embed_desc_i18n = t(
                langue, "prof_pp_embed_desc", j=days, defaut=f"Analyse sur les **{days} derniers jours**."
            )
            embed.description = str_date_header + embed_desc_i18n

            embed.add_field(name=t(langue, "prof_pp_bilan_title", defaut="Bilan Global"), value=stats_txt, inline=False)
            embed.add_field(
                name=t(langue, "prof_pp_daily_title", cur=page_actuelle, tot=nb_pages, defaut="Historique Quotidien"),
                value="\n".join(chunk),
                inline=False,
            )

            await setup_embed_footer(embed, interaction, langue)
            embeds.append(embed)

        if len(embeds) == 1:
            await interaction.followup.send(embed=embeds[0])
        else:
            view = PaginationView(embeds)
            await interaction.followup.send(embed=embeds[0], view=view)
        await prompt_vote_if_lucky(interaction, probability_percent=5, langue=langue)

    # ========================================================
    # COMMANDE : ALLIANCE PROPERTY
    # ========================================================
    @alliance_group.command(name="property", description="Displays all properties of an alliance")
    @app_commands.describe(alliance_name="Alliance name")
    @app_commands.autocomplete(alliance_name=alliance_autocomplete)
    async def alliance_property(self, interaction: discord.Interaction, alliance_name: str):
        await interaction.response.defer(thinking=True)
        langue, serveur = await get_server_config(interaction)
        headers = await get_api_headers(interaction)

        # --- 1. RECHERCHE DE L'ID DE L'ALLIANCE ---
        safe_alliance = quote(alliance_name)
        url_search = f"https://api.gge-tracker.com/api/v1/alliances/name/{safe_alliance}"

        async with self.bot.session.get(url_search, headers=headers, timeout=10) as r:
            if r.status != 200:
                return await interaction.followup.send(
                    t(
                        langue,
                        "cmd_prop_not_found",
                        alliance_name=alliance_name,
                        defaut=f"{{e_error}} Alliance **{alliance_name}** introuvable sur l'API.",
                    )
                )
            data = await r.json()
            if not data:
                return await interaction.followup.send(
                    t(
                        langue,
                        "cmd_prop_not_found",
                        alliance_name=alliance_name,
                        defaut=f"{{e_error}} Alliance **{alliance_name}** introuvable sur l'API.",
                    )
                )

            target = data[0] if isinstance(data, list) else data
            alliance_id = target.get("alliance_id") or target.get("id")
            nom_officiel = target.get("alliance_name", alliance_name)

        if not alliance_id:
            return await interaction.followup.send(
                t(
                    langue,
                    "cmd_prop_not_found",
                    alliance_name=alliance_name,
                    defaut=f"{{e_error}} ID de l'alliance **{alliance_name}** introuvable.",
                )
            )

        # --- 2. RÉCUPÉRATION DE LA CARTOGRAPHIE ---
        url_carto = f"https://api.gge-tracker.com/api/v1/cartography/id/{alliance_id}"
        async with self.bot.session.get(url_carto, headers=headers, timeout=15) as r:
            if r.status != 200:
                return await interaction.followup.send(
                    t(
                        langue,
                        "cmd_prop_api_err",
                        defaut="{e_error} Erreur lors de la récupération des données cartographiques.",
                    )
                )
            carto_data = await r.json()

        if not carto_data:
            return await interaction.followup.send(
                t(
                    langue,
                    "cmd_prop_empty",
                    defaut=f"📭 L'alliance **{alliance_name}** ne possède aucune propriété spéciale.",
                )
            )

        # --- 3. CONFIGURATION DES DONNÉES ---
        PROP_TYPES = {
            3: {
                "name": t(langue, "prop_capital", defaut="Capitale"),
                "emoji": DICT_EMOJIS.get("e_castle3", "<:castle3:1512573819313979544>"),
            },
            22: {
                "name": t(langue, "prop_city", defaut="Cité Marchande"),
                "emoji": DICT_EMOJIS.get("e_castle22", "<:castle22:1512573821520183347>"),
            },
            23: {
                "name": t(langue, "prop_tower", defaut="Tour Royale"),
                "emoji": DICT_EMOJIS.get("e_castle23", "<:castle23:1512573823118086174>"),
            },
            26: {
                "name": t(langue, "prop_monument", defaut="Monument"),
                "emoji": DICT_EMOJIS.get("e_castle26", "<:castle26:1512573824086835280>"),
            },
            28: {
                "name": t(langue, "prop_lab", defaut="Laboratoire"),
                "emoji": DICT_EMOJIS.get("e_castle28", "<:castle28:1512573825299251351>"),
            },
        }

        REALM_EMOJIS = {
            0: DICT_EMOJIS.get("e_dungeon0", "<:dungeon0:1512573840704671775>"),
            1: DICT_EMOJIS.get("e_dungeon1", "<:dungeon1:1512573842277794062>"),
            2: DICT_EMOJIS.get("e_dungeon2", "<:dungeon2:1512573843267518546>"),
            3: DICT_EMOJIS.get("e_dungeon3", "<:dungeon3:1512573844538396692>"),
            4: DICT_EMOJIS.get("e_dungeon4", "<:dungeon4:1512573845737963722>"),
        }

        all_props = []

        # --- 4. PARCOURS ET TRI DES JOUEURS ---
        for player in carto_data:
            p_name = player.get("name", "Inconnu")

            for c in player.get("castles", []):
                if len(c) >= 3 and int(c[2]) in PROP_TYPES:
                    all_props.append({"type": int(c[2]), "x": c[0], "y": c[1], "player": p_name, "world_id": 0})

            for cr in player.get("castles_realm", []):
                if len(cr) >= 4 and int(cr[3]) in PROP_TYPES:
                    all_props.append(
                        {"type": int(cr[3]), "x": cr[1], "y": cr[2], "player": p_name, "world_id": int(cr[0])}
                    )

        if len(all_props) == 0:
            msg_empty = t(
                langue,
                "cmd_prop_none",
                defaut=f"📭 L'alliance **{nom_officiel}** ne possède aucune propriété spéciale (Capitale, Tour du Roi, Monument, Labo).",
            )
            return await interaction.followup.send(msg_empty)

        all_props.sort(key=lambda p: (p["type"], p["world_id"], p["player"].lower()))

        # --- 5. CONSTRUCTION DE L'EMBED APLATI ---
        embeds = []
        titre_base = t(langue, "cmd_prop_embed_title", alliance=nom_officiel, defaut=f"🏰 Propriétés de {nom_officiel}")

        header_desc = t(
            langue,
            "cmd_prop_header",
            defaut="{e_information} **Monde | Type | Joueur ➔ Coordonnées**\n\n",
        )

        current_embed = discord.Embed(title=titre_base, color=self.clr_alliance_property)
        current_desc = header_desc

        for p in all_props:
            info = PROP_TYPES[p["type"]]
            r_emoji = REALM_EMOJIS.get(p["world_id"], "🗺️")

            ligne = f"{r_emoji} [{info['emoji']}] **{p['player']}** ➔ `{p['x']}:{p['y']}`\n"

            if len(current_desc) + len(ligne) > 4000:
                current_embed.description = current_desc
                embeds.append(current_embed)

                current_embed = discord.Embed(title=f"{titre_base} (Suite)", color=discord.Color.from_rgb(255, 215, 0))
                current_desc = header_desc + ligne
            else:
                current_desc += ligne

        if current_desc and current_desc != header_desc:
            current_embed.description = current_desc
            embeds.append(current_embed)

        for emb in embeds:
            await setup_embed_footer(emb, interaction, langue)

        if len(embeds) > 1:
            view = PaginationView(embeds)
            await interaction.followup.send(embed=embeds[0], view=view)
        else:
            await interaction.followup.send(embed=embeds[0])
        await prompt_vote_if_lucky(interaction, probability_percent=5, langue=langue)

    # ========================================================
    # 📜 COMMANDE : DESCRIPTION ALLIANCE (API GGE Tracker)
    # ========================================================
    @alliance_group.command(name="description", description="View the history of the last wall changes")
    @app_commands.autocomplete(alliance_name=alliance_autocomplete)
    async def alliance_description(self, interaction: discord.Interaction, alliance_name: str):
        try:
            await interaction.response.defer(thinking=True)
        except:
            return

        langue, serveur = await get_server_config(interaction)

        headers = await get_api_headers(interaction)
        session = self.bot.session
        if not session:
            return await interaction.followup.send(
                t(langue, "prof_desc_err_tech", defaut="{e_error} Erreur système (Session fermée).")
            )

        from urllib.parse import quote

        safe_alliance = quote(str(alliance_name))
        alliance_id = None

        # --- 1. RECHERCHE DE L'ID (VIA API NAME) ---
        try:
            search_url = f"https://api.gge-tracker.com/api/v1/alliances/name/{safe_alliance}"
            async with session.get(search_url, headers=headers, timeout=10) as resp:
                if resp.status == 200:
                    data1 = await resp.json()
                    target = data1[0] if isinstance(data1, list) and data1 else data1
                    if target:
                        alliance_id = target.get("alliance_id") or target.get("id") or target.get("allianceId")
        except Exception as e:
            self.logger.warning(f"⚠️ [Description Alliance] API /name/ injoignable pour {alliance_name} : {e}")

        # --- 2. RECHERCHE DE L'ID (PLAN B : SCAN LOCAL) ---
        if not alliance_id:
            try:
                from utils import BASE_DATA_PATH

                player_files = list((BASE_DATA_PATH / "server_scans" / serveur).rglob("server_*.json"))
                if player_files:
                    latest = max(player_files, key=lambda p: p.stat().st_mtime)

                    def _load_local_json():
                        with open(latest, encoding="utf-8") as f:
                            return json.load(f).get("players", {})

                    local_data = await asyncio.to_thread(_load_local_json)

                    for p_info in local_data.values():
                        a_obj = p_info.get("alliance")
                        a_name = (
                            a_obj.get("name") if isinstance(a_obj, dict) else (p_info.get("alliance_name") or a_obj)
                        )
                        if a_name and str(a_name).lower() == alliance_name.lower():
                            aid = p_info.get("allianceId") or p_info.get("alliance_id")
                            if not aid and isinstance(a_obj, dict):
                                aid = a_obj.get("allianceId") or a_obj.get("alliance_id")
                            if aid:
                                alliance_id = str(aid)
                                break
            except Exception as e:
                self.logger.error(f"❌ [Description Alliance] Erreur Plan B (Scan local) pour {alliance_name} : {e}")

        if not alliance_id:
            msg = t(
                langue,
                "prof_desc_not_found",
                a=alliance_name,
                defaut=f"{{e_error}} Impossible de trouver l'ID de l'alliance **{alliance_name}**.",
            )
            return await interaction.followup.send(msg)

        # --- 3. RÉCUPÉRATION DES MURS VIA L'ID TROUVÉ ---
        api_url = f"https://api.gge-tracker.com/api/v1/alliances/id/{alliance_id}"

        try:
            async with session.get(api_url, headers=headers, timeout=10) as response:
                if response.status != 200:
                    return await interaction.followup.send(
                        t(
                            langue,
                            "prof_desc_not_found",
                            a=alliance_name,
                            defaut="{e_error} Impossible de trouver les données de cette alliance dans le Tracker.",
                        )
                    )
                data = await response.json()
                if isinstance(data, list) and data:
                    data = data[0]
        except Exception as e:
            self.logger.error(f"❌ Erreur API GGE Tracker (Murs) : {e}")
            return await interaction.followup.send(
                t(langue, "prof_desc_err_tech", defaut="{e_error} Erreur technique lors de la connexion à l'API.")
            )

        # --- 4. TRAITEMENT DES DONNÉES ---
        nom_alliance = data.get("alliance_name", alliance_name)
        desc_actuelle = data.get("description")
        historique = data.get("description_history") or []

        if not desc_actuelle and not historique:
            msg_unsupported = t(
                langue,
                "prof_desc_unsupported",
                srv=serveur,
                defaut=f"{{e_warning}} Cette commande ne fonctionne pas encore sur le serveur demandé (**{serveur}**), ou le mur est totalement vide.",
            )
            return await interaction.followup.send(msg_unsupported)

        def clean_desc(text):
            if not text:
                return "*(Mur vide ou non renseigné)*"
            return text.replace("<br />", "\n").replace("<br/>", "\n").replace("<br>", "\n").strip()

        dates_trouvees = []
        if data.get("updated_at"):
            dates_trouvees.append(data.get("updated_at"))
        if data.get("updatedAt"):
            dates_trouvees.append(data.get("updatedAt"))
        for entry in historique:
            if entry.get("created_at"):
                dates_trouvees.append(entry.get("created_at"))

        from datetime import datetime

        if dates_trouvees:
            latest_str = max(dates_trouvees)
            if latest_str.endswith("Z"):
                latest_str = latest_str[:-1] + "+00:00"
            try:
                actualisation_dt = datetime.fromisoformat(latest_str)
            except:
                actualisation_dt = discord.utils.utcnow()
        else:
            actualisation_dt = discord.utils.utcnow()

        ts_act = int(actualisation_dt.timestamp())

        # --- 5. PRÉPARATION DES BLOCS DE TEXTE ---
        murs_blocks = []

        murs_blocks.append(
            {
                "name": t(langue, "prof_desc_current", defaut="📝 Description Actuelle"),
                "value": clean_desc(desc_actuelle),
            }
        )

        if historique:
            historique_trie = sorted(historique, key=lambda x: x.get("created_at", ""), reverse=True)
            for entry in historique_trie[:14]:
                try:
                    date_brute = entry.get("created_at", "")
                    if date_brute.endswith("Z"):
                        date_brute = date_brute[:-1] + "+00:00"
                    ts_change = int(datetime.fromisoformat(date_brute).timestamp())

                    header_title = t(
                        langue,
                        "prof_desc_version_api",
                        ts=ts_change,
                        defaut=f"🕰️ Ancienne version (Remplacée le <t:{ts_change}:d> à <t:{ts_change}:t>)",
                    )
                    texte_affiche = clean_desc(entry.get("old_description", ""))
                    murs_blocks.append({"name": header_title, "value": texte_affiche})
                except Exception as e:
                    self.logger.warning(f"⚠️ Erreur de parsing date historique pour {nom_alliance} : {e}")
                    continue

        # --- 6. PAGINATION (3 murs par page max) ---
        lbl_date = t(langue, "guerre_lbl_date_data", defaut="⏱️ **Données datées de :**")
        str_date_header = f"{lbl_date} <t:{ts_act}:F> (<t:{ts_act}:R>)\n\n"
        desc_i18n = t(
            langue,
            "prof_desc_embed_desc",
            l=len(murs_blocks),
            defaut="Analyse du mur d'alliance depuis le GGE Tracker.",
        )

        embed_title = t(
            langue,
            "prof_desc_embed_title",
            a=nom_alliance.upper(),
            defaut=f"Archives Alliance : {nom_alliance.upper()}",
        )

        embeds = []
        chunks = [murs_blocks[i : i + 3] for i in range(0, len(murs_blocks), 3)]

        for i, chunk in enumerate(chunks):
            embed = discord.Embed(
                title=embed_title,
                description=str_date_header + desc_i18n,
                color=getattr(self, "clr_descalli", discord.Color.blue()),
            )

            for block in chunk:
                valeur_champ = f">>> {block['value']}"
                if len(valeur_champ) > 1020:
                    valeur_champ = valeur_champ[:1017] + "..."

                embed.add_field(name=block["name"], value=valeur_champ, inline=False)

            embed.set_footer(text=f"Page {i + 1}/{len(chunks)}")
            await setup_embed_footer(embed, interaction, langue)
            embeds.append(embed)

        if len(embeds) == 1:
            await interaction.followup.send(embed=embeds[0])
        else:
            view = PaginationView(embeds)
            await interaction.followup.send(embed=embeds[0], view=view)

    # ==========================================
    # 🔍 COMMANDE : ALLIANCE SCANNER
    # ==========================================
    @alliance_group.command(name="scanner", description="Analyze the enemy roster in real time (Doves, PP, Targets)")
    @app_commands.autocomplete(alliance_name=alliance_autocomplete)
    async def alliance_scanner(self, interaction: discord.Interaction, alliance_name: str):
        try:
            await interaction.response.defer(thinking=True)
        except:
            return

        langue, serveur = await get_server_config(interaction)

        cache_data = await get_cached_data(serveur)
        local_data = cache_data.get("players_data", {})

        target_id = None
        for p_info in local_data.values():
            a_obj = p_info.get("alliance")
            a_name = a_obj.get("name") if isinstance(a_obj, dict) else (p_info.get("alliance_name") or a_obj)

            if a_name and str(a_name).lower() == alliance_name.lower():
                aid = p_info.get("allianceId") or p_info.get("alliance_id")
                if not aid and isinstance(a_obj, dict):
                    aid = a_obj.get("allianceId") or a_obj.get("alliance_id")

                if aid:
                    target_id = str(aid)
                    alliance_name = str(a_name)
                    break

        if not target_id:
            return await interaction.followup.send(
                t(
                    langue,
                    "guerre_err_alli_cache",
                    a=alliance_name,
                    defaut=f"{{e_error}} Alliance **{alliance_name}** introuvable dans le cache local.",
                )
            )

        headers = await get_api_headers(custom_server=serveur)
        url = f"https://api.gge-tracker.com/api/v1/alliances/id/{target_id}"

        try:
            async with self.bot.session.get(url, headers=headers, timeout=10) as r:
                if r.status != 200:
                    return await interaction.followup.send(
                        t(
                            langue,
                            "guerre_err_api",
                            defaut="{e_error} Erreur de l'API GGE-Tracker (Impossible d'obtenir les données live).",
                        )
                    )
                data = await r.json()
                if isinstance(data, list) and data:
                    data = data[0]
        except Exception as e:
            return await interaction.followup.send(
                t(
                    langue,
                    "guerre_err_api_join",
                    e=str(e),
                    defaut=f"{{e_error}} Impossible de joindre l'API : {e}",
                )
            )

        members = data.get("players", data.get("members", data.get("playerList", [])))
        if not members:
            return await interaction.followup.send(
                t(
                    langue,
                    "guerre_err_alli_empty",
                    defaut="{e_error} L'alliance semble vide ou l'API ne renvoie pas les membres.",
                )
            )

        maintenant = discord.utils.utcnow()
        actualisation_dt = _get_api_timestamp(data)
        colombes, cibles_libres = [], []
        txt_unk = t(langue, "prof_unknown", defaut="Inconnu")

        for m in members:
            name = m.get("player_name", m.get("playerName", m.get("name", txt_unk)))
            pp = int(m.get("might_current", m.get("might", m.get("main_points", 0))))
            peace = m.get("peace_disabled_at")

            is_protected = False
            if peace and peace != "null":
                try:
                    dt_peace = datetime.fromisoformat(peace.replace("Z", "+00:00"))
                    if dt_peace > maintenant:
                        is_protected = True
                        colombes.append({"name": name, "pp": pp, "fin": int(dt_peace.timestamp())})
                except:
                    pass

            if not is_protected:
                cibles_libres.append({"name": name, "pp": pp})

        cibles_libres.sort(key=lambda x: x["pp"], reverse=True)
        colombes.sort(key=lambda x: x["fin"])

        embeds = []
        chunk_size = 10

        e_peace_ic = DICT_EMOJIS.get("e_peace", "🕊️")
        e_atk_ic = DICT_EMOJIS.get("e_attaque", "⚔️")

        lignes_colombes = [
            f"{e_peace_ic} **{c['name']}** ({format_num(c['pp'])} PP) ➔ Fin: <t:{c['fin']}:R>" for c in colombes
        ]
        lignes_cibles = [f"{e_atk_ic} **{c['name']}** ➔ **{format_num(c['pp'])} PP**" for c in cibles_libres]

        async def creer_base_embed(titre_page):
            embed = discord.Embed(
                title=t(
                    langue,
                    "guerre_scan_title",
                    a=alliance_name,
                    defaut=f"{{e_icon_search}} Scanner de Guerre : {alliance_name}",
                ),
                color=self.clr_scanner,
            )

            desc_i18n = t(
                langue,
                "guerre_scan_desc",
                act=len(members),
                pro=len(colombes),
                vul=len(cibles_libres),
                tp=titre_page,
                defaut=f"{{e_players}} **Membres Actifs :** {len(members)}\n{{e_peace}} **Sous protection :** {len(colombes)}\n{{e_attaque}} **Cibles vulnérables :** {len(cibles_libres)}\n\n**{titre_page}**",
            )

            lbl_date = t(langue, "guerre_lbl_date_data", defaut="⏱️ **Données datées de :**")
            embed.description = f"{lbl_date} <t:{int(actualisation_dt.timestamp())}:F> (<t:{int(actualisation_dt.timestamp())}:R>)\n\n{desc_i18n}"

            await setup_embed_footer(embed, interaction, langue)
            return embed

        if lignes_colombes:
            nb_pages_col = max(1, (len(lignes_colombes) - 1) // chunk_size + 1)
            for i in range(0, len(lignes_colombes), chunk_size):
                chunk = lignes_colombes[i : i + chunk_size]
                num_page = (i // chunk_size) + 1
                embed = await creer_base_embed(
                    t(
                        langue,
                        "guerre_scan_page_col",
                        cur=num_page,
                        tot=nb_pages_col,
                        defaut=f"{{e_icon_peace}} Colombes (Page {num_page}/{nb_pages_col})",
                    )
                )
                embed.add_field(
                    name=t(langue, "guerre_scan_field_col", defaut="Prochaines à tomber"),
                    value="\n".join(chunk),
                    inline=False,
                )
                embeds.append(embed)

        if lignes_cibles:
            nb_pages_cib = max(1, (len(lignes_cibles) - 1) // chunk_size + 1)
            for i in range(0, len(lignes_cibles), chunk_size):
                chunk = lignes_cibles[i : i + chunk_size]
                num_page = (i // chunk_size) + 1
                embed = await creer_base_embed(
                    t(
                        langue,
                        "guerre_scan_page_cib",
                        cur=num_page,
                        tot=nb_pages_cib,
                        defaut=f"{{e_castle1}} Cibles Libres (Page {num_page}/{nb_pages_cib})",
                    )
                )
                embed.add_field(
                    name=t(langue, "guerre_scan_field_cib", defaut="Cibles triées par Puissance"),
                    value="\n".join(chunk),
                    inline=False,
                )
                embeds.append(embed)

        if not embeds:
            return await interaction.followup.send(
                t(
                    langue,
                    "guerre_err_no_exploit",
                    defaut="{e_error} L'alliance ne contient aucun membre exploitable.",
                )
            )

        if len(embeds) == 1:
            await interaction.followup.send(embed=embeds[0])
        else:
            view = PaginationView(embeds)
            await interaction.followup.send(embed=embeds[0], view=view)
        await prompt_vote_if_lucky(interaction, probability_percent=8, langue=langue)


async def setup(bot: commands.Bot):
    await bot.add_cog(ProfilsCog(bot))

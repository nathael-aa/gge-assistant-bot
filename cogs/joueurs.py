import asyncio
import json
import logging
import traceback
from datetime import datetime
from urllib.parse import quote

import discord
from discord import app_commands
from discord.ext import commands

import observability as obs
from utils import (
    BASE_DATA_PATH,
    DICT_EMOJIS,
    _get_api_timestamp,
    format_num,
    get_api_headers,
    get_server_config,
    joueur_autocomplete,
    prompt_vote_if_lucky,
    setup_embed_footer,
    t,
)

logger = logging.getLogger("GGE_Bot")


# ===========================================
# 🎛️ MENU INTERACTIF DES FILTRES JOUEURS
# ===========================================
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


class JoueursCog(commands.Cog):
    player_group = app_commands.Group(name="player", description="All commands related to players")

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.clr_server = discord.Color.from_rgb(255, 244, 230)
        self.clr_joueur = discord.Color.from_rgb(223, 204, 241)
        self.clr_historique = discord.Color.from_rgb(200, 183, 216)
        self.clr_colombe = discord.Color.from_rgb(160, 146, 172)
        self.clr_compare_j = discord.Color.from_rgb(112, 102, 120)

    @app_commands.command(name="server", description="Affiche les statistiques globales de ton serveur")
    @app_commands.checks.cooldown(1, 3.0, key=lambda i: i.user.id)
    async def server_stats(self, interaction: discord.Interaction):
        try:
            await interaction.response.defer(thinking=True)
        except Exception:
            return

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
        except Exception as e:
            logger.error(f"❌ [Stats] Erreur lecture fichier scan local : {e}")

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
                else:
                    logger.warning(f"⚠️ [Stats] L'API a renvoyé un statut {r.status} lors de l'appel à {url}")
        except Exception as e:
            logger.error(f"❌ [Stats] Erreur API /server/statistics : {e}")

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
            except Exception:
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

    @player_group.command(name="profile", description="Detailed player profile")
    @app_commands.checks.cooldown(1, 3.0, key=lambda i: i.user.id)
    @app_commands.autocomplete(player=joueur_autocomplete)
    async def player_info(self, interaction: discord.Interaction, player: str):
        try:
            await interaction.response.defer(thinking=True)
        except Exception:
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
                except Exception:
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
                1: DICT_EMOJIS.get("e_castle1", "{e_castle1}"),
                3: DICT_EMOJIS.get("e_castle3", "{e_castle3}"),
                4: DICT_EMOJIS.get("e_castle4", "{e_castle4}"),
                10: DICT_EMOJIS.get("e_date", "{e_date}"),
                12: DICT_EMOJIS.get("e_castle12", "{e_castle12}"),
                22: DICT_EMOJIS.get("e_castle22", "{e_castle22}"),
                23: DICT_EMOJIS.get("e_castle23", "{e_castle23}"),
                24: DICT_EMOJIS.get("e_aquamarine_16", "{e_aquamarine_16}"),
                26: DICT_EMOJIS.get("e_castle26", "{e_castle26}"),
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
                except Exception:
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

            player_id = data.get("player_id")
            if player_id:
                image_url = f"https://api.gge-tracker.com/api/v1/assets/og/player/{player_id}.jpg"
                embed.set_image(url=image_url)

            await setup_embed_footer(embed, interaction, langue)
            await interaction.followup.send(embed=embed)
            await prompt_vote_if_lucky(interaction, probability_percent=8, langue=langue)

        except Exception as e:
            logger.error(f"❌ [Profils] Erreur fatale : {traceback.format_exc()}")
            obs.record_error(source="command", scope="player_profile", exception=e, cog="joueurs")
            try:
                await interaction.followup.send(
                    t(langue, "prof_err_internal", defaut="{e_error} Erreur système interne.")
                )
            except Exception:
                pass

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
            0: DICT_EMOJIS.get("e_dungeon0", "{e_dungeon0}"),
            1: DICT_EMOJIS.get("e_dungeon1", "{e_dungeon1}"),
            2: DICT_EMOJIS.get("e_dungeon2", "{e_dungeon2}"),
            3: DICT_EMOJIS.get("e_dungeon3", "{e_dungeon3}"),
            4: DICT_EMOJIS.get("e_dungeon4", "{e_dungeon4}"),
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
                logger.warning(f"⚠️ [Profils] Impossible de lier les noms de châteaux pour {player_name}: {e}")

            outposts = []
            vassal_villages = []

            stats_data = stats_data or {}

            for c in stats_data.get("castles") or []:
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

            for c in stats_data.get("castles_realm") or []:
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

            for v in stats_data.get("villages") or []:
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
            logger.error(f"❌ [Profils] Erreur API Joueur pour {player_name}: {e}")
            return None

    @player_group.command(name="history", description="Displays a player's complete history")
    @app_commands.checks.cooldown(1, 2.0, key=lambda i: i.user.id)
    @app_commands.autocomplete(player=joueur_autocomplete)
    async def history(self, interaction: discord.Interaction, player: str):
        try:
            await interaction.response.defer(thinking=True)
        except Exception:
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
        except Exception as e:
            logger.error(f"❌ [Historique] Erreur lors de la récupération des données profil de {player} : {e}")
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
                        else:
                            logger.warning(f"⚠️ [Historique] L'API a retourné le code {resp.status} pour l'url {url}")
                except Exception as req_err:
                    logger.error(f"❌ [Historique] Erreur réseau lors de la récupération de {name} : {req_err}")
                    pass

            tasks = [fetch_url(name, url) for name, url in urls_to_fetch.items()]
            await asyncio.gather(*tasks)

        def parse_date(iso_str):
            if not iso_str:
                return t(langue, "prof_hist_unknown_date", defaut="Date inconnue")
            return f"<t:{int(datetime.fromisoformat(iso_str.replace('Z', '+00:00')).timestamp())}:d>"

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
            except Exception:
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

    @player_group.command(name="dove", description="Check the date and time a player's protection ended")
    @app_commands.checks.cooldown(1, 2.0, key=lambda i: i.user.id)
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

    @player_group.command(
        name="compare", description="Responsive comparative analysis and calculation of the hazard index"
    )
    @app_commands.checks.cooldown(1, 3.0, key=lambda i: i.user.id)
    @app_commands.autocomplete(player1=joueur_autocomplete)
    @app_commands.autocomplete(player2=joueur_autocomplete)
    async def compare(self, interaction: discord.Interaction, player1: str, player2: str):
        try:
            await interaction.response.defer(thinking=True)
        except Exception:
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
                        except Exception:
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
                    ds = ds or {}
                    pts_dict = ds.get("points") or {}

                    piliers_mapping = {
                        "nomades": "player_event_nomad_history",
                        "samourais": "player_event_samurai_history",
                        "corbeaux": "player_event_bloodcrow_history",
                        "etrangers": "player_event_war_realms_history",
                    }

                    for pilier_nom, cle_api in piliers_mapping.items():
                        ev_list = pts_dict.get(cle_api) or []
                        if isinstance(ev_list, list) and ev_list:
                            valid_entries = []
                            for e in ev_list:
                                d_str = e.get("date")
                                pt_str = str(e.get("point", ""))
                                if d_str and pt_str.replace("-", "").isdigit():
                                    try:
                                        dt = datetime.fromisoformat(d_str.replace("Z", "+00:00"))
                                        valid_entries.append((dt, int(pt_str)))
                                    except Exception:
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
                    except Exception:
                        pass

            except Exception as e:
                logger.error(f"❌ [Comparaison] Erreur sur {nom_joueur} : {e}")
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


async def setup(bot: commands.Bot):
    await bot.add_cog(JoueursCog(bot))

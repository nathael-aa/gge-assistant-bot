import asyncio
import json
import logging
import traceback
from datetime import datetime, timedelta
from urllib.parse import quote

import discord
from discord import app_commands
from discord.ext import commands

import observability as obs
from utils import (
    BASE_DATA_PATH,
    DICT_EMOJIS,
    PaginationView,
    _get_api_timestamp,
    alliance_autocomplete,
    format_num,
    get_api_headers,
    get_cached_data,
    get_server_config,
    prompt_vote_if_lucky,
    setup_embed_footer,
    t,
)

logger = logging.getLogger("GGE_Bot")


class AlliancesCog(commands.Cog):
    alliance_group = app_commands.Group(name="alliance", description="All commands related to alliances")

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.clr_alliance = discord.Color.from_rgb(129, 186, 39)
        self.clr_alliance_pp = discord.Color.from_rgb(116, 167, 35)
        self.clr_alliance_property = discord.Color.from_rgb(92, 133, 28)
        self.clr_scanner = discord.Color.from_rgb(64, 93, 19)
        self.clr_descalli = discord.Color.from_rgb(77, 111, 23)

    @alliance_group.command(name="profile", description="Detailed profile of an alliance (Quick and paginated)")
    @app_commands.checks.cooldown(1, 3.0, key=lambda i: i.user.id)
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
                0: DICT_EMOJIS.get("e_0_", "{e_0_}"),
                1: DICT_EMOJIS.get("e_1_", "{e_1_}"),
                2: DICT_EMOJIS.get("e_2_", "{e_2_}"),
                3: DICT_EMOJIS.get("e_3_", "{e_3_}"),
                4: DICT_EMOJIS.get("e_4_", "{e_4_}"),
                5: DICT_EMOJIS.get("e_5_", "{e_5_}"),
                6: DICT_EMOJIS.get("e_6_", "{e_6_}"),
                7: DICT_EMOJIS.get("e_7_", "{e_7_}"),
                8: DICT_EMOJIS.get("e_8_", "{e_8_}"),
                9: DICT_EMOJIS.get("e_9_", "{e_9_}"),
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

                if target_alliance_id and page_actuelle == 1:
                    image_url = f"https://api.gge-tracker.com/api/v1/assets/og/alliance/{target_alliance_id}.jpg"
                    embed.set_image(url=image_url)

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
            obs.record_error(source="command", scope="alliance_profile", exception=e, cog="alliances")
            try:
                await interaction.followup.send(
                    t(langue, "prof_alli_err_internal", defaut="{e_error} Erreur système interne.")
                )
            except:
                pass

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

        members = members_data.get("players") or members_data.get("members") or members_data.get("playerList") or []

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

    @alliance_group.command(name="might", description="Historical Power (PP) of an alliance over X days")
    @app_commands.checks.cooldown(1, 2.0, key=lambda i: i.user.id)
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

    @alliance_group.command(name="property", description="Displays all properties of an alliance")
    @app_commands.checks.cooldown(1, 2.0, key=lambda i: i.user.id)
    @app_commands.describe(alliance_name="Alliance name")
    @app_commands.autocomplete(alliance_name=alliance_autocomplete)
    async def alliance_property(self, interaction: discord.Interaction, alliance_name: str):
        await interaction.response.defer(thinking=True)
        langue, serveur = await get_server_config(interaction)
        headers = await get_api_headers(interaction)

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

        PROP_TYPES = {
            3: {
                "name": t(langue, "prop_capital", defaut="Capitale"),
                "emoji": DICT_EMOJIS.get("e_castle3", "{e_castle3}"),
            },
            22: {
                "name": t(langue, "prop_city", defaut="Cité Marchande"),
                "emoji": DICT_EMOJIS.get("e_castle22", "{e_castle22}"),
            },
            23: {
                "name": t(langue, "prop_tower", defaut="Tour Royale"),
                "emoji": DICT_EMOJIS.get("e_castle23", "{e_castle23}"),
            },
            26: {
                "name": t(langue, "prop_monument", defaut="Monument"),
                "emoji": DICT_EMOJIS.get("e_castle26", "{e_castle26}"),
            },
            28: {
                "name": t(langue, "prop_lab", defaut="Laboratoire"),
                "emoji": DICT_EMOJIS.get("e_castle28", "{e_castle28}"),
            },
        }

        REALM_EMOJIS = {
            0: DICT_EMOJIS.get("e_dungeon0", "{e_dungeon0}"),
            1: DICT_EMOJIS.get("e_dungeon1", "{e_dungeon1}"),
            2: DICT_EMOJIS.get("e_dungeon2", "{e_dungeon2}"),
            3: DICT_EMOJIS.get("e_dungeon3", "{e_dungeon3}"),
            4: DICT_EMOJIS.get("e_dungeon4", "{e_dungeon4}"),
        }

        all_props = []

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

    @alliance_group.command(name="description", description="View the history of the last wall changes")
    @app_commands.checks.cooldown(1, 3.0, key=lambda i: i.user.id)
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

        safe_alliance = quote(str(alliance_name))
        alliance_id = None

        try:
            search_url = f"https://api.gge-tracker.com/api/v1/alliances/name/{safe_alliance}"
            async with session.get(search_url, headers=headers, timeout=10) as resp:
                if resp.status == 200:
                    data1 = await resp.json()
                    target = data1[0] if isinstance(data1, list) and data1 else data1
                    if target:
                        alliance_id = target.get("alliance_id") or target.get("id") or target.get("allianceId")
        except Exception as e:
            logger.warning(f"⚠️ [Description Alliance] API /name/ injoignable pour {alliance_name} : {e}")

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
                logger.error(f"❌ [Description Alliance] Erreur Plan B (Scan local) pour {alliance_name} : {e}")

        if not alliance_id:
            msg = t(
                langue,
                "prof_desc_not_found",
                a=alliance_name,
                defaut=f"{{e_error}} Impossible de trouver l'ID de l'alliance **{alliance_name}**.",
            )
            return await interaction.followup.send(msg)

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
            logger.error(f"❌ Erreur API GGE Tracker (Murs) : {e}")
            return await interaction.followup.send(
                t(langue, "prof_desc_err_tech", defaut="{e_error} Erreur technique lors de la connexion à l'API.")
            )

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
                    logger.warning(f"⚠️ Erreur de parsing date historique pour {nom_alliance} : {e}")
                    continue

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

    @alliance_group.command(name="scanner", description="Analyze the enemy roster in real time (Doves, PP, Targets)")
    @app_commands.checks.cooldown(1, 3.0, key=lambda i: i.user.id)
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
    await bot.add_cog(AlliancesCog(bot))

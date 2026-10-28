import asyncio
import logging
import urllib.parse
from datetime import datetime

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

import observability as obs
from utils import (
    DICT_EMOJIS,
    PaginationView,
    _get_api_timestamp,
    get_api_headers,
    get_server_config,
    joueur_autocomplete,
    prompt_vote_if_lucky,
    setup_embed_footer,
    t,
)

logger = logging.getLogger("GGE_Bot")


def get_month_name(month_num_str, langue="fr"):
    return t(langue, f"month_{month_num_str}", defaut=month_num_str)


class RoueRichessesCog(commands.Cog):
    woa = app_commands.Group(name="woa", description="Analysis and statistics of the Wheel of Affluence")

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.clr_woa_historique = discord.Color.from_rgb(229, 148, 0)
        self.clr_woa_classement = discord.Color.from_rgb(234, 169, 50)
        self.clr_woa_bilan = discord.Color.from_rgb(240, 194, 111)

    @woa.command(name="history", description="View the history of tickets spent by a player")
    @app_commands.checks.cooldown(1, 2.0, key=lambda i: i.user.id)
    @app_commands.autocomplete(player=joueur_autocomplete)
    async def woa_historique(self, interaction: discord.Interaction, player: str):
        try:
            await interaction.response.defer(thinking=True)
        except Exception:
            return
        langue, _ = await get_server_config(interaction)
        lbl_date = t(langue, "guerre_lbl_date_data", defaut="⏱️ **Données datées de :**")

        headers = await get_api_headers(interaction)
        session = self.bot.session
        base_api = "https://api.gge-tracker.com/api/v1"
        try:
            async with session.get(f"{base_api}/players/{urllib.parse.quote(player)}", headers=headers, timeout=8) as r:
                if r.status != 200:
                    return await interaction.followup.send(
                        t(
                            langue,
                            "ev_woa_player_not_found",
                            p=player,
                            defaut=f"{{e_error}} Joueur **{player}** introuvable.",
                        )
                    )
                res_base = await r.json()
                if isinstance(res_base, list) and res_base:
                    res_base = res_base[0]
                player_id = str(res_base.get("player_id", res_base.get("id", "")))
                vrai_nom = res_base.get("player_name", player)

            async with session.get(f"{base_api}/woa/events/player/{player_id}", headers=headers, timeout=8) as r:
                if r.status != 200:
                    return await interaction.followup.send(
                        t(langue, "ev_woa_no_hist", defaut="{e_error} Aucun historique WoA trouvé.")
                    )
                events = (await r.json()).get("events", [])

            if not events:
                return await interaction.followup.send(
                    t(langue, "ev_woa_no_part", defaut="{e_error} Aucune participation enregistrée.")
                )

            actualisation_dt = _get_api_timestamp(events)

            from collections import defaultdict

            grand_total = sum(int(ev.get("point", 0)) for ev in events)
            months_data = defaultdict(list)
            for ev in events:
                try:
                    dt = datetime.fromisoformat(ev["date"].replace("Z", "+00:00"))
                    months_data[dt.strftime("%Y-%m")].append(
                        {
                            "dt": dt,
                            "date_str": dt.strftime("%d/%m/%Y"),
                            "pts": int(ev.get("point", 0)),
                            "rank": str(ev.get("rank", "?")),
                        }
                    )
                except Exception:
                    continue

            embeds = []
            sorted_months = sorted(months_data.keys(), reverse=True)

            for i, m_key in enumerate(sorted_months):
                month_events = months_data[m_key]
                annee, mois_num = m_key.split("-")
                month_total = sum(e["pts"] for e in month_events)
                best_ev = max(month_events, key=lambda x: x["pts"])
                nom_m = get_month_name(mois_num, langue)

                f_gt = f"{grand_total:,}".replace(",", " ")
                f_mt = f"{month_total:,}".replace(",", " ")
                f_mp = f"{best_ev['pts']:,}".replace(",", " ")
                f_md = best_ev["dt"].strftime("%d/%m")

                stats_txt = t(
                    langue,
                    "ev_woa_hist_stats",
                    gt=f_gt,
                    grand_total=f_gt,
                    m=nom_m,
                    mois=nom_m,
                    y=annee,
                    annee=annee,
                    mt=f_mt,
                    month_total=f_mt,
                    mp=f_mp,
                    max_points=f_mp,
                    md=f_md,
                    max_date=f_md,
                    defaut=(
                        f"🏆 **Total Historique :** {f_gt} {{e_woa_points}}\n"
                        f"{{e_stats}} **Total {nom_m} {annee} :** {f_mt} {{e_woa_points}}\n"
                        f"🚀 **Jour max :** {f_mp} {{e_woa_points}} *(le {f_md})*"
                    ),
                )

                lignes = []
                for ev in month_events:
                    pts_str = f"{ev['pts']:,}".replace(",", " ")
                    rank = ev["rank"]
                    medal = "🥇" if rank == "1" else "🥈" if rank == "2" else "🥉" if rank == "3" else f"**#{rank}**"
                    lignes.append(
                        t(
                            langue,
                            "ev_woa_hist_line",
                            d=ev["date_str"],
                            m=medal,
                            p=pts_str,
                            defaut=f"• **{ev['date_str']}** │ Rang {medal} ➔ **{pts_str} {{e_woa_points}}**",
                        )
                    )

                title = t(
                    langue,
                    "ev_woa_hist_title",
                    nom=vrai_nom,
                    defaut=f"{{e_woaicon}} Historique Roue de la Fortune : {vrai_nom}",
                )
                overv = t(langue, "ev_woa_hist_overview", defaut="**{e_stats} Vue d'ensemble**")
                det_title = t(langue, "ev_woa_hist_details", mois=nom_m, defaut=f"**📜 Détails ({nom_m})**")

                embed = discord.Embed(
                    title=title,
                    description=f"{lbl_date} <t:{int(actualisation_dt.timestamp())}:F> (<t:{int(actualisation_dt.timestamp())}:R>)\n\n{overv}\n{stats_txt}\n\n{det_title}\n"
                    + "\n".join(lignes),
                    color=self.clr_woa_historique,
                )
                await setup_embed_footer(embed, interaction, langue)
                embeds.append(embed)
            view = PaginationView(embeds)
            view.message = await interaction.followup.send(embed=embeds[0], view=view, wait=True)
            await prompt_vote_if_lucky(interaction, probability_percent=8, langue=langue)
        except Exception as e:
            logger.error(f"❌ [RoueRichesses] Erreur lors de l'affichage de l'historique pour {player} : {e}")
            obs.record_error(source="command", scope="woa_history", exception=e, cog="woa")
            await interaction.followup.send(t(langue, "ev_err_tech", e=str(e), defaut=f"{{e_error}} Erreur : {e}"))

    @woa.command(name="summary", description="Displays the ticket consumption summary")
    @app_commands.checks.cooldown(1, 3.0, key=lambda i: i.user.id)
    async def woa_bilan(self, interaction: discord.Interaction):
        try:
            await interaction.response.defer(thinking=True)
        except Exception:
            return

        langue, _ = await get_server_config(interaction)
        lbl_date = t(langue, "guerre_lbl_date_data", defaut="⏱️ **Données datées de :**")

        headers = await get_api_headers(interaction)
        session = self.bot.session
        base_api = "https://api.gge-tracker.com/api/v1"

        try:
            async with session.get(f"{base_api}/woa/events?page=1", headers=headers, timeout=8) as r:
                data = await r.json()
                all_events = data.get("events", [])
                total_pages = data.get("pagination", {}).get("total_pages", 1)

            if total_pages > 1:
                tasks_list = [
                    session.get(f"{base_api}/woa/events?page={p}", headers=headers, timeout=8)
                    for p in range(2, total_pages + 1)
                ]
                responses = await asyncio.gather(*tasks_list, return_exceptions=True)
                for resp in responses:
                    if isinstance(resp, aiohttp.ClientResponse) and resp.status == 200:
                        all_events.extend((await resp.json()).get("events", []))

            all_events.sort(key=lambda x: x.get("date", ""), reverse=True)
            actualisation_dt = _get_api_timestamp(all_events)

            t_editions = len(all_events)
            t_tickets = sum(int(ev.get("total_tickets", 0)) for ev in all_events)
            t_parts = sum(int(ev.get("participants", 0)) for ev in all_events)

            moy_tickets = t_tickets // t_editions if t_editions > 0 else 0
            moy_parts = t_parts // t_editions if t_editions > 0 else 0

            stats_globales = t(
                langue,
                "ev_woa_bilan_stats",
                ed=t_editions,
                tix=f"{t_tickets:,}".replace(",", " "),
                pts=f"{t_parts:,}".replace(",", " "),
                mt=f"{moy_tickets:,}".replace(",", " "),
                mp=f"{moy_parts:,}".replace(",", " "),
                defaut=(
                    f"{{e_stats}} **Éditions :** {t_editions}\n"
                    f"{{e_woa_points}} **Tickets :** {t_tickets:,}\n"
                    f"{{e_members}} **Participants :** {t_parts:,}\n"
                    f"⚖️ **Moyenne :** {moy_tickets:,} {{e_woa_points}} / {moy_parts:,} {{e_members}}"
                ),
            ).replace(",", " ")

            e_players = DICT_EMOJIS.get("e_players", "👤")
            e_woa_points = DICT_EMOJIS.get("e_woa_points", "🎟️")

            lignes, j_vus = [], set()
            for ev in all_events:
                try:
                    d_str = datetime.fromisoformat(ev["date"].replace("Z", "+00:00")).strftime("%d/%m/%Y")
                except Exception:
                    continue
                if len(j_vus) >= 31 and d_str not in j_vus:
                    break
                j_vus.add(d_str)
                parts = f"{int(ev.get('participants', 0)):,}".replace(",", " ")
                tix = f"{int(ev.get('total_tickets', 0)):,}".replace(",", " ")

                lignes.append(f"📅 **{d_str}** │ {e_players} {parts} │ {e_woa_points} **{tix}**")

            title = t(langue, "ev_woa_bilan_title", defaut="{e_woaicon} Bilan Économique : Roue d'Abondance")
            glob = t(langue, "ev_woa_bilan_global", defaut="**{e_icon_world} Statistiques Globales**")
            det = t(langue, "ev_woa_bilan_detail", defaut="**📜 Détail des 31 dernières éditions**")

            embeds = []
            for i in range(0, len(lignes), 15):
                embed = discord.Embed(
                    title=title,
                    description=f"{lbl_date} <t:{int(actualisation_dt.timestamp())}:F> (<t:{int(actualisation_dt.timestamp())}:R>)\n\n{glob}\n{stats_globales}\n\n{det}\n"
                    + "\n".join(lignes[i : i + 15]),
                    color=self.clr_woa_bilan,
                )
                await setup_embed_footer(embed, interaction, langue)
                embeds.append(embed)

            view = PaginationView(embeds)
            view.message = await interaction.followup.send(embed=embeds[0], view=view, wait=True)
            await prompt_vote_if_lucky(interaction, probability_percent=8, langue=langue)

        except Exception as e:
            logger.error(f"❌ [RoueRichesses] Erreur lors de l'affichage du bilan WoA : {e}")
            obs.record_error(source="command", scope="woa_summary", exception=e, cog="woa")
            await interaction.followup.send(t(langue, "ev_err_tech", e=str(e), defaut=f"{{e_error}} Erreur : {e}"))

    @woa.command(name="leaderboard", description="Displays the Top 100 from the latest Wheel of Affluence")
    @app_commands.checks.cooldown(1, 3.0, key=lambda i: i.user.id)
    async def classement_woa(self, interaction: discord.Interaction):
        try:
            await interaction.response.defer(thinking=True)
        except Exception:
            return
        langue, _ = await get_server_config(interaction)
        lbl_date = t(langue, "guerre_lbl_date_data", defaut="⏱️ **Données datées de :**")

        headers = await get_api_headers(interaction)
        session = self.bot.session
        base_api = "https://api.gge-tracker.com/api/v1"
        try:
            async with session.get(f"{base_api}/woa/events", headers=headers, timeout=8) as r:
                if r.status != 200:
                    return await interaction.followup.send(
                        t(langue, "ev_err_api_unavail", defaut="{e_error} API indisponible.")
                    )

                woa_base_data = await r.json()
                latest_date_str = woa_base_data["events"][0]["date"]
                encoded_date = urllib.parse.quote(latest_date_str)

            async with session.get(
                f"{base_api}/woa/events/date/{encoded_date}?page=1", headers=headers, timeout=8
            ) as r:
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

            if not all_players:
                return await interaction.followup.send(
                    t(langue, "ev_err_no_players", defaut="{e_error} Aucun joueur trouvé.")
                )

            all_players = all_players[:100]

            lignes = []
            e_woa = DICT_EMOJIS.get("e_woa_points", "")

            for idx, p in enumerate(all_players):
                rang = idx + 1
                nom = p.get("player_name", "???")
                pts = f"{int(p.get('point', 0)):,}".replace(",", " ")
                alli = p.get("alliance_name") or "Sans alliance"
                medal = "🥇" if rang == 1 else "🥈" if rang == 2 else "🥉" if rang == 3 else f"**{rang}.**"

                lignes.append(f"{medal} **{nom}** [{alli}] ➔ **{pts} {e_woa}**")

            actualisation_dt = _get_api_timestamp(data_rank, woa_base_data)

            embeds = []
            for i in range(0, len(lignes), 10):
                embed = discord.Embed(
                    title=t(langue, "ev_class_woa_title", defaut="Top 100 Serveur - Roue de la Fortune"),
                    color=self.clr_woa_classement,
                )
                embed.description = (
                    f"{lbl_date} <t:{int(actualisation_dt.timestamp())}:F> (<t:{int(actualisation_dt.timestamp())}:R>)"
                )
                embed.add_field(
                    name=t(langue, "ev_class_global_field", defaut="Classement global"),
                    value="\n".join(lignes[i : i + 10]),
                    inline=False,
                )
                await setup_embed_footer(embed, interaction, langue)
                embeds.append(embed)
            view = PaginationView(embeds)
            view.message = await interaction.followup.send(embed=embeds[0], view=view, wait=True)
            await prompt_vote_if_lucky(interaction, probability_percent=10, langue=langue)
        except Exception as e:
            logger.error(f"❌ [RoueRichesses] Erreur lors de l'affichage du leaderboard WoA : {e}")
            obs.record_error(source="command", scope="leaderboard_woa", exception=e, cog="woa")
            await interaction.followup.send(t(langue, "ev_err_tech", e=str(e), defaut=f"{{e_error}} Erreur : {e}"))


async def setup(bot: commands.Bot):
    await bot.add_cog(RoueRichessesCog(bot))

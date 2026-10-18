import logging
from datetime import datetime

import discord
from discord import app_commands
from discord.ext import commands

from utils import (
    DICT_EMOJIS,
    PaginationView,
    alliance_autocomplete,
    get_api_headers,
    get_server_config,
    setup_embed_footer,
    t,
)

logger = logging.getLogger("GGE_Bot")


class TournamentsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.api_base = "https://api.gge-tracker.com/api/v1"
        self.clr_gt_rank = discord.Color.from_rgb(189, 224, 254)
        self.clr_gt_alliance = discord.Color.from_rgb(162, 210, 255)

    gt_group = app_commands.Group(name="gt", description="Commands related to the Grand Tournament")

    # ==========================================
    # 🛠️ FONCTION UTILITAIRE : DERNIER SNAPSHOT
    # ==========================================
    async def get_latest_gt_snapshot(self, headers: dict):
        """Récupère l'ID du dernier événement et la date du tout dernier snapshot enregistré."""
        url = f"{self.api_base}/grand-tournament/dates"
        async with self.bot.session.get(url, headers=headers, timeout=10) as r:
            if r.status != 200:
                return None, None
            data = await r.json()

        events = data.get("events", [])
        if not events:
            return None, None

        latest_event = max(events, key=lambda x: x["event_id"])
        event_id = latest_event["event_id"]

        dates_list = latest_event.get("dates", [])

        if not dates_list:
            return event_id, None

        latest_date = dates_list[-1]

        return event_id, latest_date

    # ==========================================
    # 🏆 CLASSEMENT DU GRAND TOURNOI
    # ==========================================
    @gt_group.command(name="rank", description="Displays the current leaderboard for a Grand Tournament division")
    @app_commands.checks.cooldown(1, 3.0, key=lambda i: i.user.id)
    @app_commands.choices(
        division=[
            app_commands.Choice(name="Division 5 (Highest)", value=5),
            app_commands.Choice(name="Division 4", value=4),
            app_commands.Choice(name="Division 3", value=3),
            app_commands.Choice(name="Division 2", value=2),
            app_commands.Choice(name="Division 1 (Lowest)", value=1),
        ]
    )
    @app_commands.describe(
        division="The main division (1 to 5)",
        subdivision="The subdivision number (e.g., 1, 2, 3... Defaults to 1)",
        alliance="Optional: Find and highlight a specific alliance",
    )
    @app_commands.autocomplete(alliance=alliance_autocomplete)
    async def gt_rank(
        self,
        interaction: discord.Interaction,
        division: int,
        subdivision: app_commands.Range[int, 1, 100] = 1,
        alliance: str = None,
    ):
        await interaction.response.defer(thinking=True)
        langue, serveur = await get_server_config(interaction)
        headers = await get_api_headers(interaction)

        subdivision = subdivision or 1

        event_id, snapshot_date = await self.get_latest_gt_snapshot(headers)
        if not snapshot_date:
            return await interaction.followup.send(
                t(langue, "gt_err_no_data", defaut="{e_error} No active tournament or data unavailable.").format(
                    **DICT_EMOJIS
                )
            )

        highlight_ali = None
        target_local_page = 0

        if alliance:
            search_url = f"{self.api_base}/grand-tournament/search"
            async with self.bot.session.get(
                search_url,
                headers=headers,
                params={"date": snapshot_date, "alliance_name": alliance, "page": 1},
                timeout=15,
            ) as r:
                if r.status == 200:
                    search_data = await r.json()
                    results = search_data.get("alliances", [])
                    if results:
                        ali_data = results[0]
                        highlight_ali = ali_data.get("alliance_name")

                        division = ali_data.get("division", division)
                        subdivision = ali_data.get("subdivision", subdivision)

                        rank = ali_data.get("rank", 1)
                        target_local_page = (rank - 1) // 20
                    else:
                        emo = DICT_EMOJIS.get("e_information", "ℹ️")
                        return await interaction.followup.send(
                            t(
                                langue,
                                "gt_search_none",
                                ali=alliance,
                                defaut=f"{emo} The alliance **{alliance}** was not found in the current tournament.",
                            )
                        )

        alliances = []
        current_api_page = 1
        total_api_pages = 1

        while current_api_page <= total_api_pages:
            params = {
                "date": snapshot_date,
                "division_id": division,
                "subdivision_id": subdivision,
                "page": current_api_page,
            }
            url = f"{self.api_base}/grand-tournament/alliances"

            async with self.bot.session.get(url, headers=headers, params=params, timeout=15) as r:
                if r.status != 200:
                    err_text = (await r.text())[:150]
                    logger.error(f"[GT RANK] Erreur {r.status} : {err_text}")
                    if not alliances:
                        emo = DICT_EMOJIS.get("e_error", "❌")
                        return await interaction.followup.send(f"{emo} **API Error ({r.status})** : `{err_text}`")
                    break

                data = await r.json()
                alliances.extend(data.get("event", {}).get("alliances", []))

                pagination = data.get("pagination", {})
                total_api_pages = pagination.get("total_pages", 1)
                current_api_page += 1

        if not alliances:
            return await interaction.followup.send(
                t(langue, "gt_no_alliances", defaut="{e_information} No alliances found in this category.").format(
                    **DICT_EMOJIS
                )
            )

        try:
            dt_snap = datetime.fromisoformat(snapshot_date.replace("Z", "+00:00"))
        except:
            dt_snap = datetime.now()

        e_div = DICT_EMOJIS.get(f"e_division{division}", "🏅")
        titre = t(
            langue,
            "gt_rank_title",
            e_div=e_div,
            div=division,
            sub=subdivision,
            defaut=f"{e_div} Grand Tournament | Div. {division} - Sub. {subdivision}",
        )
        desc_maj = t(
            langue,
            "gt_last_update",
            ts=int(dt_snap.timestamp()),
            defaut=f"*Last update: <t:{int(dt_snap.timestamp())}:R>*",
        )

        embeds = []
        items_per_page = 20
        total_pages = (len(alliances) - 1) // items_per_page + 1

        for i in range(0, len(alliances), items_per_page):
            chunk = alliances[i : i + items_per_page]
            embed = discord.Embed(
                title=f"{titre} (Page {(i // items_per_page) + 1}/{total_pages})",
                description=f"{desc_maj}\n\n",
                color=self.clr_gt_rank,
            )

            lignes = []
            for ali in chunk:
                rank = ali.get("rank", 0)
                score = f"{ali.get('score', 0):,}".replace(",", " ")
                nom = ali.get("alliance_name", "Unknown")

                if highlight_ali and nom.lower() == highlight_ali.lower():
                    lignes.append(f"**#{rank} __{nom}__** 🎯 - *{score} pts*")
                else:
                    lignes.append(f"**#{rank}** {nom} - *{score} pts*")

            embed.description += "\n".join(lignes)
            await setup_embed_footer(embed, interaction, langue)
            embeds.append(embed)

        if len(embeds) == 1:
            await interaction.followup.send(embed=embeds[0])
        else:
            view = PaginationView(embeds)
            view.current_page = target_local_page
            view.message = await interaction.followup.send(embed=embeds[target_local_page], view=view)

    # ==========================================
    # 📈 HISTORIQUE ALLIANCE GT
    # ==========================================
    @gt_group.command(
        name="alliance", description="Displays the score evolution of an alliance in the Grand Tournament"
    )
    @app_commands.checks.cooldown(1, 3.0, key=lambda i: i.user.id)
    @app_commands.autocomplete(alliance=alliance_autocomplete)
    @app_commands.describe(alliance="The exact name of the alliance")
    async def gt_alliance(self, interaction: discord.Interaction, alliance: str):
        await interaction.response.defer(thinking=True)
        langue, serveur = await get_server_config(interaction)
        headers = await get_api_headers(interaction)

        event_id, snapshot_date = await self.get_latest_gt_snapshot(headers)
        if not snapshot_date or not event_id:
            return await interaction.followup.send(
                t(langue, "gt_err_no_data", defaut="{e_error} No active tournament or data unavailable.").format(
                    **DICT_EMOJIS
                )
            )

        ali_id = None
        if alliance.isdigit():
            ali_id = int(alliance)
        else:
            params_search = {"date": snapshot_date, "alliance_name": alliance, "page": 1}
            url_search = f"{self.api_base}/grand-tournament/search"

            async with self.bot.session.get(url_search, headers=headers, params=params_search, timeout=15) as r:
                if r.status != 200:
                    err = (await r.text())[:150]
                    logger.error(f"[GT ALLIANCE] Erreur Search API ({r.status}) : {err}")
                    emo = DICT_EMOJIS.get("e_error", "❌")
                    return await interaction.followup.send(f"{emo} **API Error (Search {r.status})** : `{err}`")

                data_search = await r.json()
                results = data_search.get("alliances", [])

                if not results:
                    return await interaction.followup.send(
                        t(
                            langue,
                            "gt_search_none",
                            ali=alliance,
                            defaut=f"{{e_information}} The alliance **{alliance}** was not found in the current tournament.",
                        ).format(**DICT_EMOJIS)
                    )

                ali_id = results[0].get("alliance_id")

        if not ali_id:
            return await interaction.followup.send(
                t(langue, "gt_err_no_ali_id", defaut="{e_error} Unable to determine the alliance ID.").format(
                    **DICT_EMOJIS
                )
            )

        url_hist = f"{self.api_base}/grand-tournament/alliance/{ali_id}/{event_id}"
        async with self.bot.session.get(url_hist, headers=headers, timeout=15) as r:
            if r.status == 404:
                return await interaction.followup.send(
                    t(
                        langue,
                        "gt_err_no_history",
                        defaut="{e_information} No historical data found for this alliance.",
                    ).format(**DICT_EMOJIS)
                )
            if r.status != 200:
                err = (await r.text())[:150]
                logger.error(f"[GT ALLIANCE] Erreur History API ({r.status}) : {err}")
                emo = DICT_EMOJIS.get("e_error", "❌")
                return await interaction.followup.send(f"{emo} **API Error (History {r.status})** : `{err}`")

            data_hist = await r.json()

        analysis = data_hist.get("analysis", [])
        meta = data_hist.get("meta", {})
        ali_name = meta.get("alliance_name", alliance)

        if not analysis:
            return await interaction.followup.send(
                t(
                    langue, "gt_err_no_points", defaut="{e_information} The alliance hasn't scored any points yet."
                ).format(**DICT_EMOJIS)
            )

        def parse_gt_date(date_str):
            try:
                clean = date_str.replace("T", " ").replace("Z", "").replace(".000", "")
                return datetime.strptime(clean, "%Y-%m-%d %H:%M:%S")
            except:
                return datetime.now()

        analysis.sort(key=lambda x: parse_gt_date(x.get("date", "")).timestamp(), reverse=True)

        latest_entry = analysis[0]
        oldest_entry = analysis[-1]

        ts_latest = int(parse_gt_date(latest_entry.get("date", "")).timestamp())
        ts_oldest = int(parse_gt_date(oldest_entry.get("date", "")).timestamp())

        current_div = latest_entry.get("division", "?")
        current_sub = latest_entry.get("subdivision", "?")
        current_score = f"{latest_entry.get('score', 0):,}".replace(",", " ")
        current_rank = latest_entry.get("rank", "?")

        e_div = DICT_EMOJIS.get(f"e_division{current_div}", "🏅")
        e_listitem = DICT_EMOJIS.get("e_listitem", "📆")
        e_std_scroll = DICT_EMOJIS.get("e_std_scroll", "📆")
        e_gt = DICT_EMOJIS.get("e_grandtournament", "🏆")

        synth_text = t(
            langue,
            "gt_alliance_synth",
            e_div=e_div,
            div=current_div,
            sub=current_sub,
            e_listitem=e_listitem,
            score=current_score,
            rank=current_rank,
            e_std_scroll=e_std_scroll,
            ts_oldest=ts_oldest,
            ts_latest=ts_latest,
            defaut=(
                f"{e_div} **Ligue actuelle :** Division {current_div} (Sub. {current_sub})\n"
                f"{e_listitem} **Dernier pointage :** {current_score} pts (Rang #{current_rank})\n"
                f"{e_std_scroll} **Période suivie :** Du <t:{ts_oldest}:d> au <t:{ts_latest}:d>\n\n"
                f"**(Légende : Date | Rang | Points)**\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
            ),
        )

        embeds = []
        items_per_page = 15
        total_pages = (len(analysis) - 1) // items_per_page + 1

        titre = t(
            langue,
            "gt_alliance_title",
            e_gt=e_gt,
            ali_name=ali_name,
            defaut=f"{e_gt} Analyse Grand Tournoi | {ali_name}",
        )

        for i in range(0, len(analysis), items_per_page):
            chunk = analysis[i : i + items_per_page]
            embed = discord.Embed(
                title=f"{titre} (Page {(i // items_per_page) + 1}/{total_pages})",
                description=synth_text,
                color=self.clr_gt_alliance,
            )

            lignes = []
            for entry in chunk:
                dt = parse_gt_date(entry.get("date", ""))
                ts = int(dt.timestamp())

                score = f"{entry.get('score', 0):,}".replace(",", " ")
                rank = entry.get("rank", "?")

                ligne = f"📅 <t:{ts}:d> - <t:{ts}:t> | **#{rank}** | **{score}** pts"
                lignes.append(ligne)

            embed.description += "\n".join(lignes)
            await setup_embed_footer(embed, interaction, langue)
            embeds.append(embed)

        if len(embeds) == 1:
            await interaction.followup.send(embed=embeds[0])
        else:
            view = PaginationView(embeds)
            view.message = await interaction.followup.send(embed=embeds[0], view=view)


async def setup(bot: commands.Bot):
    await bot.add_cog(TournamentsCog(bot))

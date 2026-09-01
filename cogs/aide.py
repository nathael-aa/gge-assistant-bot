import json
import logging
import os
import shutil
from datetime import datetime
from pathlib import Path

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from utils import (
    ADMINS_DIR,
    BOT_VERSION,
    DICT_EMOJIS,
    MON_ID_DISCORD,
    PaginationView,
    get_api_headers,
    get_file_lock,
    get_server_config,
    setup_embed_footer,
    t,
)

logger = logging.getLogger("GGE_Bot")

CONTACTS_FILE = ADMINS_DIR / "contacts.json"

# ==========================================
# ⚙️ CONFIGURATION CENTRALISÉE DE L'AIDE
# ==========================================
HELP_CONFIG = {
    "home": {
        "emoji": "🏠",
        "color": discord.Color.from_rgb(198, 226, 255),
        "title_key": "help_home_title",
        "desc_key": "aide_p0_desc",
    },
    "config": {
        "emoji": "⚙️",
        "color": discord.Color.from_rgb(102, 102, 102),
        "title_key": "help_cat_config",
        "commands": [
            {"name": "/setup", "desc_key": "help_cmd_setup"},
            {"name": "/link_account", "desc_key": "help_cmd_link_account"},
            {"name": "/status", "desc_key": "help_cmd_status"},
            {"name": "/help", "desc_key": "help_cmd_help"},
        ],
    },
    "communaute": {
        "emoji": "💬",
        "color": discord.Color.from_rgb(230, 230, 250),
        "title_key": "help_cat_communaute",
        "commands": [
            {"name": "/news", "desc_key": "help_cmd_news"},
            {"name": "/support", "desc_key": "help_cmd_support"},
            {"name": "/contact", "desc_key": "help_cmd_contact"},
            {"name": "/vote", "desc_key": "help_cmd_vote"},
            {"name": "/discover", "desc_key": "help_cmd_discover"},
        ],
    },
    "profils": {
        "emoji": "👥",
        "color": discord.Color.from_rgb(50, 214, 50),
        "title_key": "help_cat_profils",
        "commands": [
            {"name": "/server", "desc_key": "help_cmd_server"},
            {"name": "/player profile", "desc_key": "help_cmd_player_profile"},
            {"name": "/player history", "desc_key": "help_cmd_player_history"},
            {"name": "/player dove", "desc_key": "help_cmd_player_dove"},
            {"name": "/player compare", "desc_key": "help_cmd_player_compare"},
            {"name": "/alliance profile", "desc_key": "help_cmd_alliance_profile"},
            {"name": "/alliance might", "desc_key": "help_cmd_alliance_might"},
            {"name": "/alliance property", "desc_key": "help_cmd_alliance_property"},
            {"name": "/alliance description", "desc_key": "help_cmd_alliance_desc"},
        ],
    },
    "guerre": {
        "emoji": "⚔️",
        "color": discord.Color.from_rgb(139, 0, 0),
        "title_key": "help_cat_guerre",
        "commands": [
            {"name": "/alliance scanner", "desc_key": "help_cmd_alliance_scanner"},
            {"name": "/target (setup / search)", "desc_key": "help_cmd_target_group", "wip": True},
        ],
    },
    "events": {
        "emoji": "🏆",
        "color": discord.Color.from_rgb(175, 238, 238),
        "title_key": "help_cat_events",
        "commands": [
            {"name": "/event player", "desc_key": "help_cmd_event_player"},
            {"name": "/event alliance", "desc_key": "help_cmd_event_alliance"},
            {"name": "/calendar (setup / track / untrack / current)", "desc_key": "help_cmd_calendar_group"},
            {
                "name": "/rank (event / league / contests / statistics / gacha / realms / alliance)",
                "desc_key": "help_cmd_rank_group",
            },
            {"name": "/leaderboard (woa / storm_islands)", "desc_key": "help_cmd_leaderboard_group"},
            {"name": "/woa (history / summary)", "desc_key": "help_cmd_woa_group"},
        ],
    },
    "radars": {
        "emoji": "📡",
        "color": discord.Color.from_rgb(255, 174, 25),
        "title_key": "help_cat_radars",
        "commands": [
            {"name": "/radar (add / remove / list)", "desc_key": "help_cmd_radar_group", "advanced": False},
            {"name": "/radar alliance (add / remove)", "desc_key": "help_cmd_radar_alliance_group", "advanced": False},
            {"name": "/rival (start / stop / add / list)", "desc_key": "help_cmd_rival_group", "advanced": False},
            {"name": "/fortress (scan / stop)", "desc_key": "help_cmd_fortress_group", "advanced": True},
            {
                "name": "/storm (forts / isles / occupier / status / setup / stop)",
                "desc_key": "help_cmd_storm_group",
                "advanced": True,
            },
        ],
    },
}


# ==========================================
# 🌟 LISTE DES PROJETS À DÉCOUVRIR
# ==========================================
PROJECTS_LIST = [
    {
        "title_key": "cmd_disc_gge_tracker_title",
        "desc_key": "cmd_disc_gge_tracker_desc",
        "default_title": "{e_players} GGE-Tracker",
        "default_desc": "La référence web pour consulter des données datant de plusieurs mois, regroupant aussi de nombreux outils pour aider les joueurs ! Notre bot utilise fièrement leur excellente API pour fonctionner en temps réel.",
        "links": [
            {
                "label_key": "btn_website",
                "label_defaut": "Site Web",
                "url": "https://gge-tracker.com/",
                "emoji": DICT_EMOJIS.get("e_website", "🌐"),
            },
            {
                "label_key": "btn_discord",
                "label_defaut": "Discord",
                "url": "https://discord.gg/eb6WSHQqYh",
                "emoji": DICT_EMOJIS.get("e_discordlogo", "💬"),
            },
            {
                "label_key": "btn_api",
                "label_defaut": "API",
                "url": "https://docs.gge-tracker.com/",
                "emoji": DICT_EMOJIS.get("e_developer", "💻"),
            },
        ],
    },
    {
        "title_key": "cmd_disc_ranking_title",
        "desc_key": "cmd_disc_ranking_desc",
        "default_title": "{e_ggelogo} GGE Ranking",
        "default_desc": "Le site web historique pour consulter tous ses classements en jeu depuis un navigateur web et en direct.",
        "links": [
            {
                "label_key": "btn_website",
                "label_defaut": "Site Web",
                "url": "https://danadum.github.io/empire-rankings/",
                "emoji": DICT_EMOJIS.get("e_website", "🌐"),
            }
        ],
    },
    {
        "title_key": "cmd_disc_generalsforum_title",
        "desc_key": "cmd_disc_generalsforum_desc",
        "default_title": "{e_generalsforumlogo} Generals Forum",
        "default_desc": "Un site web combinant des aperçus sur les objets en jeu, des simulateurs et des calculateurs. Mis à jour régulièrement.",
        "links": [
            {
                "label_key": "btn_website",
                "label_defaut": "Site Web",
                "url": "https://generalsforum.com/",
                "emoji": DICT_EMOJIS.get("e_website", "🌐"),
            }
        ],
    },
]


# ==========================================
# 🎛️ MENU DÉROULANT (SELECT MENU)
# ==========================================
class HelpSelect(discord.ui.Select):
    def __init__(self, langue: str):
        self.langue = langue

        options = [
            discord.SelectOption(
                label=t(langue, "help_menu_home", defaut="Sommaire Principal"),
                emoji="🏠",
                value="home",
                description=t(langue, "help_menu_home_desc", defaut="Revenir à l'accueil du manuel"),
            ),
            discord.SelectOption(
                label=t(langue, "help_menu_config", defaut="Configuration & Système"), emoji="⚙️", value="config"
            ),
            discord.SelectOption(
                label=t(langue, "help_menu_communaute", defaut="Communauté & Support"), emoji="💬", value="communaute"
            ),
            discord.SelectOption(
                label=t(langue, "help_menu_profils", defaut="Profils & Informations"), emoji="👥", value="profils"
            ),
            discord.SelectOption(
                label=t(langue, "help_menu_guerre", defaut="Guerre & Diplomatie"), emoji="⚔️", value="guerre"
            ),
            discord.SelectOption(
                label=t(langue, "help_menu_events", defaut="Événements & Classements"), emoji="🏆", value="events"
            ),
            discord.SelectOption(
                label=t(langue, "help_menu_radars", defaut="Radars & Traqueurs"), emoji="📡", value="radars"
            ),
        ]

        super().__init__(
            placeholder=t(langue, "help_placeholder", defaut="📂 Choisissez une catégorie..."),
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        cat_id = self.values[0]
        cat_data = HELP_CONFIG[cat_id]

        embed = discord.Embed(
            title=f"{cat_data['emoji']} {t(self.langue, cat_data['title_key'], defaut=cat_id.capitalize())}",
            color=cat_data["color"],
        )

        if cat_id == "home":
            desc = t(
                self.langue,
                cat_data["desc_key"],
                version=BOT_VERSION,
                defaut=f"Bienvenue dans l'interface de navigation tactique.\n*Version du bot : {BOT_VERSION}*\n\n**Utilisez le menu déroulant ci-dessous pour explorer les commandes.**",
            )
            embed.description = desc
        else:
            # --- Légende des capacités serveur ---
            legende = t(
                self.langue,
                "help_legend",
                defaut="🟢 `Standard` ｜ 🌟 `Fonctions Avancées` ｜ {e_working} `En travaux`",
            )

            embed.description = f"{legende}\n\u200b"

            for cmd in cat_data["commands"]:
                cmd_desc = t(self.langue, cmd["desc_key"], defaut="> *Description manquante*")

                # --- Attribution dynamique de l'emoji (Priorité au WIP) ---
                is_wip = cmd.get("wip", False)
                is_advanced = cmd.get("advanced", False)

                if is_wip:
                    emoji_statut = t(self.langue, "format_wip", defaut="{e_working}")
                elif is_advanced:
                    emoji_statut = "🌟"
                else:
                    emoji_statut = "🟢"

                embed.add_field(name=f"{emoji_statut} **`{cmd['name']}`**", value=f"{cmd_desc}", inline=False)

        await setup_embed_footer(embed, interaction, self.langue)
        if not self.view.message:
            self.view.message = interaction.message
        await interaction.response.edit_message(embed=embed, view=self.view)


class HelpView(discord.ui.View):
    def __init__(self, langue: str):
        super().__init__(timeout=3600)
        self.message = None
        self.add_item(HelpSelect(langue))

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


# ==========================================
# 🤖 MODULE PRINCIPAL (COMMANDES DU BOT)
# ==========================================
class AideCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

        self.clr_discover = discord.Color.from_rgb(88, 101, 242)  # Bleu "Blurple"
        self.clr_statut = discord.Color.from_rgb(255, 207, 64)  # Gold1
        self.clr_news = discord.Color.from_rgb(255, 191, 0)  # Gold2
        self.clr_vote = discord.Color.from_rgb(232, 198, 112)  # Gold3
        self.clr_support = discord.Color.from_rgb(241, 213, 143)  # Gold4
        self.clr_contact = discord.Color.from_rgb(247, 226, 173)  # Gold5

    # ==========================================
    # 📖 COMMANDE : HELP
    # ==========================================
    @app_commands.command(name="help", description="Displays the complete user manual for the GGE Assistant bot")
    async def aide_commande(self, interaction: discord.Interaction):
        try:
            await interaction.response.defer(ephemeral=False)
        except:
            return

        langue, _ = await get_server_config(interaction)

        home_data = HELP_CONFIG["home"]
        embed = discord.Embed(
            title=t(langue, home_data["title_key"], defaut="📖 Manuel d'Utilisation - GGE Assistant"),
            color=home_data["color"],
        )
        embed.description = t(
            langue,
            home_data["desc_key"],
            version=BOT_VERSION,
            defaut=f"Bienvenue dans l'interface de navigation tactique.\n*Version du bot : {BOT_VERSION}*\n\n**Utilisez le menu déroulant ci-dessous pour explorer les commandes.**",
        )

        await setup_embed_footer(embed, interaction, langue)

        view = HelpView(langue)
        view.message = await interaction.followup.send(embed=embed, view=view, wait=True)

    # ==========================================
    # 📡 COMMANDE : STATUS
    # ==========================================
    @app_commands.command(
        name="status", description="Checks the overall health status of the system (Bot, NAS Storage, GGE-Tracker API)"
    )
    async def statut_commande(self, interaction: discord.Interaction):
        try:
            await interaction.response.defer(thinking=True)
        except:
            return

        langue, serveur = await get_server_config(interaction)

        logger.info(f"📡 [Statut] Demande de diagnostic par {interaction.user.name} sur {serveur}")
        embed = discord.Embed(
            title=t(langue, "statut_title", serveur=serveur, defaut=f"📡 Diagnostic et Santé du Système - {serveur}"),
            color=self.clr_statut,
            timestamp=discord.utils.utcnow(),
        )

        # 🤖 1. DIAGNOSTIC BOT DISCORD
        ping = round(self.bot.latency * 1000)

        val_oui = t(langue, "statut_yes", defaut="{e_parameters} Oui")
        val_non = t(langue, "statut_no", defaut="{e_icon_friends2} Non")
        maintenance_txt = val_oui if getattr(self.bot, "maintenance_mode", False) else val_non

        bot_txt = t(
            langue,
            "statut_bot_desc",
            ping=ping,
            version=BOT_VERSION,
            maint=maintenance_txt,
            defaut=(
                f"**Statut** : 🟢 Opérationnel\n"
                f"**Latence (Ping)** : `{ping} ms`\n"
                f"**Version Core** : `{BOT_VERSION}`\n"
                f"**Mode Maintenance** : {maintenance_txt}"
            ),
        )
        embed.add_field(name=t(langue, "statut_bot_title", defaut="🤖 Bot Discord"), value=bot_txt, inline=False)

        # 💾 2. DIAGNOSTIC STOCKAGE SERVEUR
        try:
            total, used, free = shutil.disk_usage("/app/data")
            total_gb = total / (1024**3)
            used_gb = used / (1024**3)
            free_gb = free / (1024**3)
            pourcentage_plein = (used / total) * 100

            # --- 🔍 Scan Serveur ---
            scans_dir = Path(f"/app/data/server_scans/{serveur}")
            last_scan_txt = t(langue, "statut_storage_no_file", defaut="{e_error} Aucun fichier trouvé")

            if scans_dir.exists():
                server_files = list(scans_dir.rglob("*.json"))
                if server_files:
                    latest_scan = max(server_files, key=lambda p: p.stat().st_mtime)
                    mtime_ts = int(latest_scan.stat().st_mtime)
                    last_scan_txt = f"<t:{mtime_ts}:F> (<t:{mtime_ts}:R>)"

            storage_txt = t(
                langue,
                "statut_storage_desc",
                total=f"{total_gb:.2f}",
                used=f"{used_gb:.2f}",
                pct=f"{pourcentage_plein:.1f}",
                free=f"{free_gb:.2f}",
                last=last_scan_txt,
                defaut=(
                    f"**Disque Local** : 🟢 Connecté (Lecture/Écriture OK)\n"
                    f"**Espace Utilisé** : `{used_gb:.2f} Go` ({pourcentage_plein:.1f}%)\n"
                    f"**Dernier Scan Serveur** : {last_scan_txt}\n"
                ),
            )
        except Exception as e:
            storage_txt = t(
                langue,
                "statut_storage_err",
                error=str(e),
                defaut=f"{{e_error}} Erreur de lecture de l'espace de stockage : {e}",
            )

        embed.add_field(
            name=t(langue, "statut_storage_title", defaut="💾 Stockage Interne"), value=storage_txt, inline=False
        )

        # 📡 3. DIAGNOSTIC API LIVE (GGE-TRACKER)
        url_api = "https://api.gge-tracker.com/api/v1/"
        headers = await get_api_headers(custom_server=serveur)

        def to_discord_ts(iso_str):
            if not iso_str or iso_str == "null":
                return t(langue, "statut_api_never", defaut="Jamais")
            try:
                dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
                return f"<t:{int(dt.timestamp())}:R>"
            except:
                return t(langue, "statut_api_unknown", defaut="Date inconnue")

        session = self.bot.session if hasattr(self.bot, "session") and self.bot.session else aiohttp.ClientSession()
        api_title = t(langue, "statut_api_title", defaut="📡 API GGE-Tracker (Live)")

        try:
            async with session.get(url_api, headers=headers, timeout=10) as r:
                if r.status == 200:
                    api_data = await r.json()
                    v_api = api_data.get("version", "Inconnue")

                    val_prog = t(langue, "statut_api_prog", defaut="{e_error} En cours...")
                    val_done = t(langue, "statut_api_done", defaut="🟢 Terminé / À jour")
                    in_progress = val_prog if api_data.get("update_in_progress", False) else val_done

                    updates = api_data.get("last_update", {})

                    api_txt = t(
                        langue,
                        "statut_api_desc",
                        v_api=v_api,
                        in_progress=in_progress,
                        defaut=(
                            f"**Statut API** : 🟢 En ligne (Code 200)\n"
                            f"**Version Distante** : `{v_api}`\n"
                            f"**Synchro Serveur GGE** : {in_progress}\n"
                        ),
                    )
                    embed.add_field(name=api_title, value=api_txt, inline=False)

                    events_txt = t(
                        langue,
                        "statut_api_events",
                        m=to_discord_ts(updates.get("might")),
                        l=to_discord_ts(updates.get("loot")),
                        n=to_discord_ts(updates.get("nomad")),
                        s=to_discord_ts(updates.get("samurai")),
                        c=to_discord_ts(updates.get("bloodcrow")),
                        e=to_discord_ts(updates.get("war_realms")),
                        b=to_discord_ts(updates.get("berimond_kingdom")),
                        defaut=(
                            f"{{e_pp1}} **Puissances** : {to_discord_ts(updates.get('might'))}\n"
                            f"{{e_loot}} **Pillages** : {to_discord_ts(updates.get('loot'))}\n"
                            f"{{e_nomads}} **Nomades** : {to_discord_ts(updates.get('nomad'))}\n"
                            f"{{e_samurai}} **Samouraïs** : {to_discord_ts(updates.get('samurai'))}\n"
                            f"{{e_bloodcrow}} **Corbeaux** : {to_discord_ts(updates.get('bloodcrow'))}\n"
                            f"{{e_war_realms}} **Etrangers** : {to_discord_ts(updates.get('war_realms'))}\n"
                            f"{{e_berimond}} **Bérimond** : {to_discord_ts(updates.get('berimond_kingdom'))}"
                        ),
                    )
                    embed.add_field(
                        name=t(langue, "statut_api_events_title", defaut="⏱️ Dernière capture des données GGE-Tracker"),
                        value=events_txt,
                        inline=False,
                    )
                else:
                    embed.add_field(
                        name=api_title,
                        value=t(
                            langue,
                            "statut_api_err_instable",
                            code=r.status,
                            defaut=f"{{e_error}} L'API répond mais rencontre une instabilité (Code : {r.status}).",
                        ),
                        inline=False,
                    )
        except Exception as e:
            embed.add_field(
                name=api_title,
                value=t(
                    langue,
                    "statut_api_err_offline",
                    error=str(e),
                    defaut=f"{{e_error}} **Hors ligne** : Impossible de joindre l'API de suivi du jeu ({e}).",
                ),
                inline=False,
            )

        await setup_embed_footer(embed, interaction, langue)
        await interaction.followup.send(embed=embed)

    # ========================================================
    # 🌍 COMMANDE : DISCOVER
    # ========================================================
    @app_commands.command(name="discover", description="Discover useful tools and community projects for GGE")
    async def discover(self, interaction: discord.Interaction):
        langue, _ = await get_server_config(interaction)

        embeds = []
        projets_par_page = 3

        # On découpe la liste en paquets (chunks)
        for i in range(0, len(PROJECTS_LIST), projets_par_page):
            chunk = PROJECTS_LIST[i : i + projets_par_page]

            embed = discord.Embed(
                title=t(langue, "cmd_discover_title", defaut="🚀 Découvertes & Projets de la Communauté"),
                description=t(
                    langue,
                    "cmd_discover_desc",
                    defaut="Découvrez des outils incroyables créés par la communauté pour enrichir votre expérience sur Goodgame Empire !\n",
                ),
                color=self.clr_discover,
            )

            for project in chunk:
                titre_traduit = t(langue, project["title_key"], defaut=project["default_title"])
                desc_traduit = t(langue, project["desc_key"], defaut=project["default_desc"])

                links_text = []
                for link in project.get("links", []):
                    label = t(langue, link["label_key"], defaut=link["label_defaut"])
                    links_text.append(f"{link['emoji']} **[{label}]({link['url']})**")

                desc_formatee = f"> *{desc_traduit}*"
                barre_liens = "  •  ".join(links_text)
                final_value = f"{desc_formatee}\n\n{barre_liens}\n\u200b"

                embed.add_field(name=titre_traduit, value=final_value, inline=False)

            await setup_embed_footer(embed, interaction, langue)
            embeds.append(embed)

        if len(embeds) == 1:
            await interaction.response.send_message(embed=embeds[0])
        else:
            view = PaginationView(embeds)
            await interaction.response.send_message(embed=embeds[0], view=view)
            view.message = await interaction.original_response()

    # ========================================================
    # 🆘 COMMANDE : SUPPORT
    # ========================================================
    @app_commands.command(name="support", description="Get the invite link to the official support server")
    async def support(self, interaction: discord.Interaction):
        langue, _ = await get_server_config(interaction)

        embed = discord.Embed(
            title=t(langue, "cmd_support_title", defaut="{e_information} Support & Communauté"),
            description=t(
                langue,
                "cmd_support_desc",
                defaut="Vous avez une question, une suggestion ou vous avez trouvé un bug ? Rejoignez le serveur Discord officiel de **GGE Assistant** !",
            ),
            color=self.clr_support,
        )

        view = discord.ui.View()
        invite_url = "https://discord.gg/zrrhxp6wDj"
        btn = discord.ui.Button(
            label=t(langue, "cmd_support_btn", defaut="Rejoindre le serveur"), url=invite_url, emoji="💬"
        )
        view.add_item(btn)

        await interaction.response.send_message(embed=embed, view=view)

    # ========================================================
    # ⭐ COMMANDE : VOTE
    # ========================================================
    @app_commands.command(name="vote", description="Support the bot by voting on Top.gg")
    async def vote(self, interaction: discord.Interaction):
        langue, _ = await get_server_config(interaction)

        embed = discord.Embed(
            title=t(langue, "cmd_vote_title", defaut="⭐ Soutenir GGE Assistant"),
            description=t(
                langue,
                "cmd_vote_desc",
                defaut="Le bot est 100% gratuit ! Le meilleur moyen de soutenir le projet et de l'aider à grandir est de voter sur Top.gg (possible toutes les 12 heures). Merci ! ❤️",
            ),
            color=self.clr_vote,
        )

        view = discord.ui.View()
        bot_id = "1472309793065533493"
        vote_url = f"https://top.gg/bot/{bot_id}/vote"
        btn = discord.ui.Button(label=t(langue, "cmd_vote_btn", defaut="Voter sur Top.gg"), url=vote_url, emoji="⭐")
        view.add_item(btn)

        await interaction.response.send_message(embed=embed, view=view)

    # ========================================================
    # 📰 COMMANDE : NEWS
    # ========================================================
    @app_commands.command(name="news", description="Read the latest bot updates and patch notes")
    async def news(self, interaction: discord.Interaction):
        langue, _ = await get_server_config(interaction)

        embed = discord.Embed(
            title=t(langue, "cmd_news_title", defaut="📰 Dernières Nouveautés"),
            description=t(
                langue,
                "cmd_news_desc",
                defaut="Découvrez les dernières annonces, les serveurs ajoutés et les nouvelles fonctionnalités du bot directement sur notre page Top.gg !",
            ),
            color=self.clr_news,
        )

        view = discord.ui.View()
        bot_id = "1472309793065533493"
        news_url = f"https://top.gg/bot/{bot_id}/announcements"
        btn = discord.ui.Button(label=t(langue, "cmd_news_btn", defaut="Voir les annonces"), url=news_url, emoji="🗞️")
        view.add_item(btn)

        await interaction.response.send_message(embed=embed, view=view)

    # ==========================================
    # 📩 COMMANDE : CONTACT
    # ==========================================
    @app_commands.command(name="contact", description="Send a problem, bug, or suggestion directly to the developer")
    @app_commands.describe(message="Write your problem or suggestion in detail here")
    async def contact_commande(self, interaction: discord.Interaction, message: str):
        try:
            await interaction.response.defer(ephemeral=True, thinking=True)
        except:
            return

        langue, _ = await get_server_config(interaction)

        logger.info(f"📩 [Contact] Nouveau message de {interaction.user.name} ({interaction.user.id})")
        maintenant_iso = discord.utils.utcnow().isoformat().replace("+00:00", "Z")

        async with get_file_lock(CONTACTS_FILE):
            try:
                tickets_existants = []
                if os.path.exists(CONTACTS_FILE):
                    with open(CONTACTS_FILE, encoding="utf-8") as f:
                        try:
                            tickets_existants = json.load(f)
                        except json.JSONDecodeError:
                            tickets_existants = []

                nouveau_ticket = {
                    "date": maintenant_iso,
                    "user_id": str(interaction.user.id),
                    "user_name": interaction.user.name,
                    "serveur": interaction.guild.name if interaction.guild else "Message Privé",
                    "message": message,
                }
                tickets_existants.append(nouveau_ticket)

                with open(CONTACTS_FILE, "w", encoding="utf-8") as f:
                    json.dump(tickets_existants, f, indent=4, ensure_ascii=False)

            except Exception as e:
                logger.error(f"❌ [Contact] Erreur écriture JSON : {e}")
                err_msg = t(
                    langue,
                    "contact_err_write",
                    defaut="❌ Une erreur technique interne a empêché l'enregistrement de ton message.",
                )
                return await interaction.followup.send(err_msg)

        try:
            developpeur = self.bot.get_user(MON_ID_DISCORD)
            if not developpeur:
                developpeur = await self.bot.fetch_user(MON_ID_DISCORD)

            embed_mp = discord.Embed(
                title="📩 Nouveau Ticket Reçu !", color=self.clr_contact, timestamp=discord.utils.utcnow()
            )
            embed_mp.add_field(
                name="{e_players} Expéditeur",
                value=f"**{interaction.user.name}** (`{interaction.user.id}`)",
                inline=True,
            )
            embed_mp.add_field(name="{e_castles} Provenance", value=f"*{nouveau_ticket['serveur']}*", inline=True)
            embed_mp.add_field(name="{e_memberlist} Message", value=f"```text\n{message}\n```", inline=False)

            # On remplace les emojis dynamiques manuellement pour le MP admin car setup_embed_footer ne lit que le footer
            embed_mp.fields[0].name = embed_mp.fields[0].name.replace("{e_players}", DICT_EMOJIS.get("e_players", "👤"))
            embed_mp.fields[1].name = embed_mp.fields[1].name.replace("{e_castles}", DICT_EMOJIS.get("e_castles", "🏰"))
            embed_mp.fields[2].name = embed_mp.fields[2].name.replace(
                "{e_memberlist}", DICT_EMOJIS.get("e_memberlist", "📋")
            )

            await setup_embed_footer(embed_mp, interaction, "fr")

            await developpeur.send(embed=embed_mp)

        except discord.Forbidden:
            logger.warning(f"⚠️ [Contact] Impossible d'envoyer le MP à {MON_ID_DISCORD} (DMs fermés).")
        except Exception as e:
            logger.error(f"❌ [Contact] Erreur lors de l'alerte MP : {e}")

        succ_msg = t(
            langue,
            "contact_success",
            defaut="{e_players} **Merci !** Ton message a bien été enregistré et transmis au développeur.",
        )
        await interaction.followup.send(succ_msg)

    # ==========================================
    # 🛡️ PROTECTEUR DE CLÉS POUR LE SCRIPT DE SYNCHRO
    # ==========================================
    def _dummy_i18n():
        langue = "fr"

        t(langue, "help_home_title")
        t(langue, "help_legend")
        t(langue, "aide_p0_desc")
        t(langue, "help_cat_config")
        t(langue, "help_cat_communaute")
        t(langue, "help_cat_profils")
        t(langue, "help_cat_guerre")
        t(langue, "help_cat_events")
        t(langue, "help_cat_radars")
        t(langue, "help_cmd_setup")
        t(langue, "help_cmd_link_account")
        t(langue, "help_cmd_status")
        t(langue, "help_cmd_help")
        t(langue, "help_cmd_news")
        t(langue, "help_cmd_support")
        t(langue, "help_cmd_contact")
        t(langue, "help_cmd_vote")
        t(langue, "help_cmd_discover")
        t(langue, "help_cmd_server")
        t(langue, "help_cmd_player_profile")
        t(langue, "help_cmd_player_history")
        t(langue, "help_cmd_player_dove")
        t(langue, "help_cmd_player_compare")
        t(langue, "help_cmd_alliance_profile")
        t(langue, "help_cmd_alliance_might")
        t(langue, "help_cmd_alliance_property")
        t(langue, "help_cmd_alliance_desc")
        t(langue, "help_cmd_alliance_scanner")
        t(langue, "help_cmd_target_group")
        t(langue, "help_cmd_event_player")
        t(langue, "help_cmd_event_alliance")
        t(langue, "help_cmd_calendar_group")
        t(langue, "help_cmd_rank_group")
        t(langue, "help_cmd_leaderboard_group")
        t(langue, "help_cmd_woa_group")
        t(langue, "help_cmd_radar_group")
        t(langue, "help_cmd_radar_alliance_group")
        t(langue, "help_cmd_rival_group")
        t(langue, "help_cmd_fortress_group")
        t(langue, "help_cmd_storm_group")
        t(langue, "help_placeholder")
        t(langue, "help_menu_home")
        t(langue, "help_menu_home_desc")
        t(langue, "help_menu_config")
        t(langue, "help_menu_communaute")
        t(langue, "help_menu_profils")
        t(langue, "help_menu_guerre")
        t(langue, "help_menu_events")
        t(langue, "help_menu_radars")
        t(langue, "cmd_discover_title")
        t(langue, "cmd_discover_desc")
        t(langue, "cmd_disc_gge_tracker_title")
        t(langue, "cmd_disc_gge_tracker_desc")
        t(langue, "cmd_disc_ranking_title")
        t(langue, "cmd_disc_ranking_desc")
        t(langue, "cmd_disc_generalsforum_title")
        t(langue, "cmd_disc_generalsforum_desc")
        t(langue, "btn_website")
        t(langue, "btn_discord")
        t(langue, "btn_api")


async def setup(bot: commands.Bot):
    await bot.add_cog(AideCog(bot))

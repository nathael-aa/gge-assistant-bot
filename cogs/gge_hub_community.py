import asyncio
import hashlib
import json
import logging
import re
import traceback
from datetime import datetime

import discord
from bs4 import BeautifulSoup
from discord import app_commands
from discord.ext import commands, tasks

import observability as obs
from utils import (
    DICT_EMOJIS,
    SERVEURS_DIR,
    get_server_config,
    setup_embed_footer,
    t,
)

logger = logging.getLogger("GGE_Bot")

HUB_CONFIG_FILE = SERVEURS_DIR / "hub_community.json"


async def load_hub_config():
    if not HUB_CONFIG_FILE.exists():
        return {"guilds": {}, "posted_news": []}
    try:
        with open(HUB_CONFIG_FILE, encoding="utf-8") as f:
            data = json.load(f)
            if "guilds" not in data:
                data["guilds"] = {}
            if "posted_news" not in data:
                data["posted_news"] = []
            return data
    except Exception:
        return {"guilds": {}, "posted_news": []}


async def save_hub_config(data):
    with open(HUB_CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


class GGEHubCommunityCog(commands.GroupCog, group_name="hub", group_description="GGE Community Hub News"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.hub_urls = {
            "e4k": "https://communityhub.goodgamestudios.com/newshube4k/",
            "empire": "https://communityhub.goodgamestudios.com/newshubempire/",
        }
        self.changelog_url = "https://communityhub.goodgamestudios.com/2026/05/18/goodgame-empire-changelog/"
        self.headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

    async def cog_load(self):
        if not self.check_hub_news_task.is_running():
            self.check_hub_news_task.start()

    async def cog_unload(self):
        self.check_hub_news_task.cancel()

    async def translate_text(self, text: str, target_lang: str) -> str:
        """Traduit un texte dynamiquement en le découpant pour éviter les limites d'URL de Google."""
        if not text or target_lang == "en":
            return text

        url = "https://clients5.google.com/translate_a/t"
        headers_trad = {"User-Agent": "Mozilla/5.0"}

        chunks = []
        current_chunk = ""
        for line in text.split("\n"):
            if len(current_chunk) + len(line) < 1000:
                current_chunk += line + "\n"
            else:
                chunks.append(current_chunk)
                current_chunk = line + "\n"
        if current_chunk:
            chunks.append(current_chunk)

        translated_text = ""
        for chunk in chunks:
            if not chunk.strip():
                translated_text += "\n"
                continue

            params = {"client": "dict-chrome-ex", "sl": "en", "tl": target_lang, "q": chunk}
            try:
                await asyncio.sleep(1)
                async with self.bot.session.get(url, params=params, headers=headers_trad, timeout=10) as r:
                    if r.status == 200:
                        data = await r.json()
                        if isinstance(data, list):
                            translated_text += "".join(str(item) for item in data)
                        else:
                            translated_text += str(data)
                    else:
                        translated_text += chunk
            except Exception as e:
                logger.error(f"❌ [Hub] Erreur traduction : {e}")
                translated_text += chunk

        return translated_text

    async def _build_and_send_embed(
        self, article, channel, ping_role, langue, serveur, final_titre, final_resume, final_full_md
    ):
        """Méthode utilitaire pour générer l'embed complet et l'envoyer proprement."""
        if article["type"] == "patchnotes":
            couleur = 0x2ECC71
            titre_prefix = t(langue, "hub_patchnote_prefix", defaut="⚙️ [MISE À JOUR]")
        elif article["type"] == "alerts":
            couleur = 0xE74C3C
            titre_prefix = t(langue, "hub_alerts_prefix", defaut="⚠️ [ALERTE / INFOS]")
        else:
            couleur = 0x3498DB
            titre_prefix = t(langue, "hub_news_prefix", defaut="📰 [ACTUALITÉ]")

        resume_fallback = t(
            langue,
            "hub_news_resume_fallback",
            defaut="Cliquez sur le lien ci-dessous pour découvrir les détails de cette annonce.",
        )
        read_more_text = t(langue, "hub_news_read_more_append", defaut="*(Lisez la suite sur le site complet)*")
        date_label = t(langue, "hub_news_date_label", defaut="Date :")
        link_label = t(langue, "hub_news_link_label", defaut="🔗 Voir l'annonce complète")

        if final_resume:
            if article["type"] == "patchnotes":
                thread_hint = t(
                    langue,
                    "hub_thread_hint",
                    defaut="👇 *Le détail complet de la mise à jour est disponible dans le fil de discussion ci-dessous !*",
                )
                final_resume_texte = f"{final_resume}\n\n{thread_hint}"
            else:
                final_resume_texte = f"{final_resume}\n\n{read_more_text}"
        else:
            final_resume_texte = resume_fallback

        embed = discord.Embed(
            title=f"{titre_prefix} {final_titre}",
            url=article["url"],
            description=f"**{date_label}** {article['date_discord']}\n\n{final_resume_texte}\n\n[{link_label}]({article['url']})",
            color=couleur,
        )

        if article.get("image"):
            embed.set_image(url=article["image"])

        await setup_embed_footer(embed, None, langue)

        try:
            message = await channel.send(content=ping_role if ping_role else None, embed=embed)

            if article["type"] == "patchnotes" and final_full_md:
                try:
                    thread_name = t(
                        langue,
                        "hub_thread_name",
                        titre=final_titre,
                        defaut=f"📄 Détails : {final_titre}",
                    )
                    thread = await message.create_thread(name=thread_name[:100], auto_archive_duration=1440)

                    chunks_md = []
                    current_chunk = ""
                    for ligne in final_full_md.split("\n"):
                        if len(current_chunk) + len(ligne) < 1900:
                            current_chunk += ligne + "\n"
                        else:
                            chunks_md.append(current_chunk)
                            current_chunk = ligne + "\n"
                    if current_chunk:
                        chunks_md.append(current_chunk)

                    for chunk in chunks_md:
                        if chunk.strip():
                            await thread.send(chunk)
                except Exception as e:
                    logger.error(f"Erreur création thread patchnote : {e}")

            obs.record_alert(
                source="hub_news",
                alert_type=article["type"],
                gge_server=serveur,
                channel="guild",
                recipients=1,
                delivered=1,
                failed=0,
                dm_blocked=0,
            )
        except Exception as e:
            logger.error(f"Erreur d'envoi annonce HUB : {e}")
            obs.record_alert(
                source="hub_news",
                alert_type=article["type"],
                gge_server=serveur,
                channel="guild",
                recipients=1,
                delivered=0,
                failed=1,
                dm_blocked=0,
            )

    @app_commands.command(name="setup", description="Configure the channel for GGE announcements")
    @app_commands.choices(
        categorie=[
            app_commands.Choice(name="News (General news, Offers, Teasers)", value="news"),
            app_commands.Choice(name="Patchnotes (Game updates, Changelogs)", value="patchnotes"),
            app_commands.Choice(name="Alerts (Bugs, Delays, Server issues)", value="alerts"),
        ]
    )
    @app_commands.describe(
        categorie="What type of announcement should be sent to this channel?",
        channel="The text channel for news",
        role="Role to ping (Leave empty for no ping)",
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def hub_setup(
        self, interaction: discord.Interaction, categorie: str, channel: discord.TextChannel, role: discord.Role = None
    ):
        await interaction.response.defer(ephemeral=True)
        langue, serveur = await get_server_config(interaction)

        bot_permissions = channel.permissions_for(interaction.guild.me)
        if (
            not bot_permissions.send_messages
            or not bot_permissions.embed_links
            or not bot_permissions.create_public_threads
        ):
            msg = t(
                langue,
                "hub_setup_perms",
                salon=channel.mention,
                defaut="{e_error} Je dois avoir la permission d'envoyer des messages, des embeds et de **créer des fils de discussion (threads)** dans {salon}.",
            ).format(**DICT_EMOJIS)
            return await interaction.followup.send(msg)

        data = await load_hub_config()
        guild_id = str(interaction.guild_id)

        if guild_id not in data["guilds"]:
            data["guilds"][guild_id] = {
                "news": {},
                "patchnotes": {},
                "alerts": {},
                "langue": langue,
                "gge_server": serveur,
            }
        elif "alerts" not in data["guilds"][guild_id]:
            data["guilds"][guild_id]["alerts"] = {}

        if role:
            ping_format = role.mention
        else:
            ping_format = ""

        data["guilds"][guild_id][categorie] = {"channel_id": channel.id, "role": ping_format}
        await save_hub_config(data)

        obs.record_guild_event(
            f"hub_setup_{categorie}",
            guild=interaction.guild,
            user_id=interaction.user.id,
            gge_server=serveur,
            new_value=f"channel:{channel.id}",
        )

        msg = t(
            langue,
            "hub_setup_success",
            categorie=categorie.upper(),
            salon=channel.mention,
            defaut="{e_check} **Configuration validée !** Les annonces de type `{categorie}` seront publiées dans {salon}.",
        ).format(**DICT_EMOJIS)

        if ping_format:
            msg_ping = t(langue, "hub_setup_ping", role=ping_format, defaut="\nLe rôle {role} sera mentionné.")
            msg += msg_ping

        await interaction.followup.send(msg)

        # FIX : Envoi forcé de la dernière annonce correspondante lors de l'activation
        # FIX : Envoi forcé de la dernière annonce correspondante lors de l'activation
        try:
            articles = await self.fetch_latest_news()

            # 🎯 On détermine à quel jeu joue ce serveur Discord
            guild_game = "e4k" if serveur.startswith("E4K_") else "empire"

            # On filtre pour ne garder que la bonne catégorie ET le bon jeu (ou "both" pour le changelog)
            cat_articles = [a for a in articles if a["type"] == categorie and a["game"] in [guild_game, "both"]]

            if cat_articles:
                latest_article = cat_articles[-1]
                texte_resume = latest_article.get("resume_content", "")

                # Fetching contenu complet si nécessaire
                if not texte_resume and latest_article["type"] in ["news", "alerts"]:
                    try:
                        async with self.bot.session.get(latest_article["url"], headers=self.headers, timeout=10) as r:
                            if r.status == 200:
                                html_article = await r.text()
                                soup_art = await asyncio.to_thread(BeautifulSoup, html_article, "html.parser")
                                content_div = soup_art.find("div", class_="elementor-widget-theme-post-content")
                                if content_div:
                                    for header in content_div.find_all(["h1", "h2", "h3"]):
                                        header.decompose()
                                    texte_complet = content_div.get_text(separator=" ", strip=True)
                                    texte_resume = (
                                        texte_complet[:400] + "..." if len(texte_complet) > 400 else texte_complet
                                    )
                    except Exception as e:
                        logger.error(f"Setup fetch error: {e}")

                # Traductions rapides
                if latest_article["type"] == "patchnotes":
                    final_titre = latest_article["title"]
                else:
                    final_titre = await self.translate_text(latest_article["title"], langue)

                final_resume = await self.translate_text(texte_resume, langue) if texte_resume else ""
                final_full_md = (
                    await self.translate_text(latest_article.get("full_content", ""), langue)
                    if latest_article.get("full_content")
                    else ""
                )

                await self._build_and_send_embed(
                    article=latest_article,
                    channel=channel,
                    ping_role=ping_format,
                    langue=langue,
                    serveur=serveur,
                    final_titre=final_titre,
                    final_resume=final_resume,
                    final_full_md=final_full_md,
                )
        except Exception as e:
            logger.error(f"❌ [Hub] Erreur lors de l'envoi de bienvenue : {e}")

    @app_commands.command(name="stop", description="Disable specific Hub announcements for this server")
    @app_commands.choices(
        categorie=[
            app_commands.Choice(name="News (General news)", value="news"),
            app_commands.Choice(name="Patchnotes (Game updates)", value="patchnotes"),
            app_commands.Choice(name="Alerts (Bugs, Delays, Server issues)", value="alerts"),
        ]
    )
    @app_commands.describe(categorie="Which type of announcement do you want to disable?")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def hub_stop(self, interaction: discord.Interaction, categorie: str):
        await interaction.response.defer(ephemeral=True)
        langue, serveur = await get_server_config(interaction)

        data = await load_hub_config()
        guild_id = str(interaction.guild_id)

        if guild_id in data["guilds"] and data["guilds"][guild_id].get(categorie):
            data["guilds"][guild_id][categorie] = {}

            if (
                not data["guilds"][guild_id].get("news")
                and not data["guilds"][guild_id].get("patchnotes")
                and not data["guilds"][guild_id].get("alerts")
            ):
                del data["guilds"][guild_id]

            await save_hub_config(data)

            obs.record_guild_event(
                f"hub_stop_{categorie}", guild=interaction.guild, user_id=interaction.user.id, gge_server=serveur
            )

            msg = t(
                langue,
                "hub_stop_success",
                categorie=categorie.upper(),
                defaut="{e_check} Les actualités de type `{categorie}` ont été désactivées pour ce serveur.",
            ).format(**DICT_EMOJIS)
            await interaction.followup.send(msg)
        else:
            msg_fail = t(
                langue,
                "hub_stop_fail",
                categorie=categorie.upper(),
                defaut="{e_warning} Les actualités de type `{categorie}` n'étaient pas configurées sur ce serveur.",
            ).format(**DICT_EMOJIS)
            await interaction.followup.send(msg_fail)

    async def fetch_latest_news(self):
        """Scrape les Hubs, crée le résumé propre ET extrait la version Markdown complète pour le fil Discord."""
        articles = []

        # 1. PARSING DES NEWS CLASSIQUES ET ALERTES (POUR E4K ET EMPIRE)
        for game_type, url in self.hub_urls.items():
            try:
                async with self.bot.session.get(url, headers=self.headers, timeout=15) as r:
                    if r.status == 200:
                        html_content = await r.text()
                        soup = await asyncio.to_thread(BeautifulSoup, html_content, "html.parser")

                        # --- A. PARSING DES HERO BANNERS (Les gros articles tout en haut) ---
                        for heading in soup.find_all(["h1", "h2"]):
                            if "e-heading-base" in heading.get("class", []):
                                parent = heading.find_parent("div", class_="elementor-element")
                                if parent:
                                    link_elem = parent.find("a", href=True)
                                    if link_elem and re.search(r"/\d{4}/\d{2}/\d{2}/", link_elem["href"]):
                                        url_article = link_elem["href"]
                                        if url_article.rstrip("/") == self.changelog_url.rstrip("/"):
                                            continue

                                        title = heading.get_text(strip=True)
                                        match = re.search(r"/(\d{4})/(\d{2})/(\d{2})/", url_article)
                                        date_obj = datetime(
                                            int(match.group(1)), int(match.group(2)), int(match.group(3))
                                        )
                                        discord_date = f"<t:{int(date_obj.timestamp())}:D>"

                                        article_id = hashlib.md5((url_article + title + game_type).encode()).hexdigest()

                                        articles.append(
                                            {
                                                "id": article_id,
                                                "title": title,
                                                "url": url_article,
                                                "date_discord": discord_date,
                                                "date_obj": date_obj,
                                                "image": None,
                                                "type": "news",  # Par défaut les Heroes sont des news
                                                "resume_content": "",
                                                "game": game_type,
                                            }
                                        )

                        # --- B. PARSING DE LA GRILLE (Articles standards) ---
                        for post in soup.find_all("article", class_="elementor-grid-item"):
                            title_elem = post.find(["h1", "h2", "h3"], class_="elementor-post__title")
                            if not title_elem:
                                continue

                            link_elem = title_elem.find("a", href=True)
                            if not link_elem:
                                continue

                            url_article = link_elem["href"]
                            title = title_elem.get_text(strip=True)

                            if url_article.rstrip("/") == self.changelog_url.rstrip("/"):
                                continue

                            date_elem = post.find("span", class_="elementor-post-date")
                            date_str = date_elem.get_text(strip=True) if date_elem else ""

                            img_elem = post.find("img")
                            img_url = img_elem["src"] if img_elem else None
                            if img_url and "?" in img_url:
                                img_url = img_url.split("?")[0]

                            classes = " ".join(post.get("class", [])).lower()
                            is_alert = "alert" in classes or "alert" in url_article.lower()
                            article_type = "alerts" if is_alert else "news"

                            article_id = hashlib.md5((url_article + title + game_type).encode()).hexdigest()

                            # Sécurité anti-doublon (si l'article est en Hero ET dans la grille)
                            if not any(a["id"] == article_id for a in articles):
                                try:
                                    clean_date_str = date_str.replace(".", "").strip()
                                    date_obj = datetime.strptime(clean_date_str, "%d %B %Y")
                                    discord_date = f"<t:{int(date_obj.timestamp())}:D>"
                                except Exception:
                                    date_obj = discord.utils.utcnow()
                                    discord_date = date_str if date_str else "Récemment"

                                articles.append(
                                    {
                                        "id": article_id,
                                        "title": title,
                                        "url": url_article,
                                        "date_discord": discord_date,
                                        "date_obj": date_obj,
                                        "image": img_url,
                                        "type": article_type,
                                        "resume_content": "",
                                        "game": game_type,
                                    }
                                )
            except Exception as e:
                logger.error(f"❌ [Hub] Erreur de parsing HTML ({game_type}) : {e}")

        # 2. PARSING DE LA PAGE DES PATCHNOTES
        try:
            async with self.bot.session.get(self.changelog_url, headers=self.headers, timeout=15) as r2:
                if r2.status == 200:
                    html_changelog = await r2.text()
                    soup_cl = await asyncio.to_thread(BeautifulSoup, html_changelog, "html.parser")

                    for details in soup_cl.find_all("details", class_="e-n-accordion-item"):
                        title_elem = details.find("div", class_="e-n-accordion-item-title-text")
                        if not title_elem:
                            continue

                        date_str = title_elem.get_text(strip=True)
                        try:
                            date_obj = datetime.strptime(date_str, "%d.%m.%Y")
                            discord_date = f"<t:{int(date_obj.timestamp())}:D>"
                        except Exception:
                            date_obj = datetime.min
                            discord_date = date_str

                        content_div = details.find("div", role="region")
                        texte_resume, texte_full_md = "", ""

                        if content_div:
                            lignes_resume = []
                            lignes_full = []
                            in_bug_section = False
                            bug_count = 0

                            for element in content_div.find_all(["h2", "h3", "h4", "li", "p"]):
                                if element.name in ["h2", "h3", "h4"]:
                                    if in_bug_section and bug_count > 0:
                                        lignes_resume.append(f"• {bug_count} bug fixes and optimizations.")
                                        bug_count = 0

                                    header_text = element.get_text(strip=True)
                                    if lignes_resume:
                                        lignes_resume.append("")
                                    lignes_resume.append(f"**{header_text}**")

                                    if lignes_full:
                                        lignes_full.append("")
                                    lignes_full.append(f"### {header_text}")

                                    if "bug" in header_text.lower() or "fix" in header_text.lower():
                                        in_bug_section = True
                                    else:
                                        in_bug_section = False

                                elif element.name == "p":
                                    text_brut = element.get_text(separator=" ", strip=True)
                                    lignes_full.append(text_brut)
                                    if not in_bug_section:
                                        lignes_resume.append(text_brut)

                                elif element.name == "li":
                                    strong_tag = element.find("strong")
                                    full_text_brut = element.get_text(separator=" ", strip=True)
                                    if strong_tag:
                                        strong_text = strong_tag.get_text(strip=True)
                                        full_text_md = full_text_brut.replace(strong_text, f"**{strong_text}**", 1)
                                        lignes_full.append(f"- {full_text_md}")
                                    else:
                                        lignes_full.append(f"- {full_text_brut}")

                                    if in_bug_section:
                                        bug_count += 1
                                        continue

                                    first_sentence = full_text_brut.split(". ")[0]
                                    if len(first_sentence) > 85:
                                        short_text = first_sentence[:85]
                                        if " " in short_text:
                                            short_text = short_text.rsplit(" ", 1)[0]
                                        lignes_resume.append(f"• {short_text}...")
                                    else:
                                        lignes_resume.append(
                                            f"• {first_sentence}{'.' if not first_sentence.endswith('.') else ''}"
                                        )

                            if in_bug_section and bug_count > 0:
                                lignes_resume.append(f"• {bug_count} bug fixes and optimizations.")

                            texte_resume = "\n".join(lignes_resume).strip()
                            texte_full_md = "\n".join(lignes_full).strip()

                        article_id = hashlib.md5(f"patchnote_{date_str}".encode()).hexdigest()

                        articles.append(
                            {
                                "id": article_id,
                                "title": f"Update {date_str}",
                                "url": self.changelog_url,
                                "date_discord": discord_date,
                                "date_obj": date_obj,
                                "image": None,
                                "type": "patchnotes",
                                "resume_content": texte_resume,
                                "full_content": texte_full_md,
                                "game": "both",
                            }
                        )
        except Exception as e:
            logger.error(f"❌ [Hub] Erreur de parsing HTML Patchnotes : {e}")

        articles.sort(key=lambda x: x["date_obj"])
        return articles

    @tasks.loop(minutes=15)
    async def check_hub_news_task(self):
        await self.check_hub_news_logic()

    async def check_hub_news_logic(self):
        """Logique d'envoi et traduction."""
        obs.set_task_name("check_hub_news_task")
        try:
            nouveaux_articles = await self.fetch_latest_news()
            if not nouveaux_articles:
                return

            data = await load_hub_config()
            posted_news = data.get("posted_news", [])
            guilds_config = data.get("guilds", {})

            if not guilds_config:
                return

            # ==========================================
            # 🛡️ GESTION DU DÉMARRAGE ET BOUCLIER ANTI-SPAM
            # ==========================================
            if not posted_news:
                logger.info("📡 [Hub] Initialisation. Mémorisation de l'historique et envoi du tout dernier article.")
                # S'il y a des articles, on archive tout sauf le dernier
                for art in nouveaux_articles[:-1]:
                    posted_news.append(art["id"])

                data["posted_news"] = posted_news[-100:]
                await save_hub_config(data)

                articles_a_publier = [nouveaux_articles[-1]]
            else:
                articles_a_publier = [art for art in nouveaux_articles if art["id"] not in posted_news]

                if len(articles_a_publier) > 3:
                    logger.warning(
                        f"🛡️ [Hub] Bouclier activé ({len(articles_a_publier)} articles détectés). Envoi uniquement du dernier au cas où."
                    )

                    # On archive tout en silence dans la base de données... SAUF le dernier !
                    for art in articles_a_publier[:-1]:
                        posted_news.append(art["id"])

                    # On ne laisse que le tout dernier article pour la suite du processus d'envoi
                    articles_a_publier = [articles_a_publier[-1]]

            if articles_a_publier:
                langues_cibles = set(config.get("langue", "fr") for config in guilds_config.values())

                for article in articles_a_publier:
                    texte_resume = article.get("resume_content", "")

                    if not texte_resume and article["type"] in ["news", "alerts"]:
                        try:
                            async with self.bot.session.get(article["url"], headers=self.headers, timeout=10) as r:
                                if r.status == 200:
                                    html_article = await r.text()
                                    soup_art = await asyncio.to_thread(BeautifulSoup, html_article, "html.parser")
                                    content_div = soup_art.find("div", class_="elementor-widget-theme-post-content")
                                    if content_div:
                                        for header in content_div.find_all(["h1", "h2", "h3"]):
                                            header.decompose()
                                        texte_complet = content_div.get_text(separator=" ", strip=True)
                                        if len(texte_complet) > 400:
                                            texte_resume = texte_complet[:400] + "..."
                                        else:
                                            texte_resume = texte_complet
                        except Exception as e:
                            logger.error(f"Impossible de lire le contenu de {article['url']} : {e}")

                    traductions = {}
                    for lang in langues_cibles:
                        if article["type"] == "patchnotes":
                            trad_titre = article["title"]
                        else:
                            trad_titre = await self.translate_text(article["title"], lang)

                        trad_resume = await self.translate_text(texte_resume, lang) if texte_resume else ""
                        trad_full = (
                            await self.translate_text(article.get("full_content", ""), lang)
                            if article.get("full_content")
                            else ""
                        )
                        traductions[lang] = {"title": trad_titre, "resume": trad_resume, "full": trad_full}

                    for guild_id_str, config in guilds_config.items():
                        cat_config = config.get(article["type"], {})
                        channel_id = cat_config.get("channel_id")

                        if not channel_id:
                            continue

                        serveur_cible = config.get("gge_server", "E4K_FR1")
                        guild_game = "e4k" if serveur_cible.startswith("E4K_") else "empire"

                        # 🎯 On ignore cette annonce si elle ne correspond pas au jeu de ce serveur
                        if article["game"] not in [guild_game, "both"]:
                            continue

                        ping_role = cat_config.get("role", "")
                        langue = config.get("langue", "fr")

                        final_titre = traductions.get(langue, {}).get("title", article["title"])
                        final_resume = traductions.get(langue, {}).get("resume", "")
                        final_full_md = traductions.get(langue, {}).get("full", "")

                        channel = self.bot.get_channel(channel_id)
                        if not channel:
                            try:
                                channel = await self.bot.fetch_channel(channel_id)
                            except:
                                continue

                        await self._build_and_send_embed(
                            article=article,
                            channel=channel,
                            ping_role=ping_role,
                            langue=langue,
                            serveur=serveur_cible,
                            final_titre=final_titre,
                            final_resume=final_resume,
                            final_full_md=final_full_md,
                        )

                    posted_news.append(article["id"])

                data["posted_news"] = posted_news[-100:]
                await save_hub_config(data)
                logger.info(f"📰 [Hub] {len(articles_a_publier)} actualités envoyées et traduites.")

        except Exception as e:
            logger.error(f"❌ [HUB CRASH] : {traceback.format_exc()}")
            obs.record_error(source="task", scope="check_hub_news_task", exception=e, cog="hub")

    @check_hub_news_task.before_loop
    async def before_check_hub_news_task(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(GGEHubCommunityCog(bot))

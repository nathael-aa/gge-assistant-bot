import asyncio
import hashlib
import json
import logging
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
        self.hub_url = "https://communityhub.goodgamestudios.com/newshubempire/"
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

    @app_commands.command(name="setup", description="Configure the channel for GGE announcements")
    @app_commands.choices(
        categorie=[
            app_commands.Choice(name="News (General news, Offers, Teasers)", value="news"),
            app_commands.Choice(name="Patchnotes (Game updates, Changelogs)", value="patchnotes"),
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
            data["guilds"][guild_id] = {"news": {}, "patchnotes": {}, "langue": langue, "gge_server": serveur}

        if role:
            ping_format = "@everyone" if role.is_default() else role.mention
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

    @app_commands.command(name="stop", description="Disable specific Hub announcements for this server")
    @app_commands.choices(
        categorie=[
            app_commands.Choice(name="News (General news)", value="news"),
            app_commands.Choice(name="Patchnotes (Game updates)", value="patchnotes"),
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

            if not data["guilds"][guild_id].get("news") and not data["guilds"][guild_id].get("patchnotes"):
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

    @app_commands.command(name="test", description="[Dev] Oublie le dernier article du site et force l'annonce")
    @app_commands.choices(
        type_article=[
            app_commands.Choice(name="News (Actualités générales)", value="news"),
            app_commands.Choice(name="Patchnotes (Mises à jour)", value="patchnotes"),
        ]
    )
    @app_commands.describe(type_article="Quel type d'article veux-tu forcer pour le test ?")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def hub_test(self, interaction: discord.Interaction, type_article: str = None):
        """Commande secrète pour tester sans toucher au JSON."""
        await interaction.response.defer(ephemeral=True)

        articles = await self.fetch_latest_news()
        if not articles:
            return await interaction.followup.send("⚠️ Impossible de lire les articles du site Web pour le test.")

        if type_article:
            articles_filtres = [art for art in articles if art["type"] == type_article]
            if not articles_filtres:
                return await interaction.followup.send(
                    f"⚠️ Aucun article de type `{type_article}` n'a été trouvé sur le site."
                )
            dernier_article = articles_filtres[-1]
        else:
            dernier_article = articles[-1]

        data = await load_hub_config()

        if data.get("posted_news") and dernier_article["id"] in data["posted_news"]:
            data["posted_news"].remove(dernier_article["id"])
            await save_hub_config(data)

            await self.check_hub_news_logic()

            await interaction.followup.send(
                f"✅ Mémoire effacée pour : **{dernier_article['title']}** (Type: `{dernier_article['type']}`). L'annonce a été envoyée !"
            )
        else:
            await interaction.followup.send(
                f"⚠️ L'article **{dernier_article['title']}** n'est pas en mémoire. Test impossible."
            )

    async def fetch_latest_news(self):
        """Scrape le Hub, crée le résumé propre ET extrait la version Markdown complète pour le fil Discord."""
        articles = []

        # 1. PARSING DES NEWS CLASSIQUES
        try:
            async with self.bot.session.get(self.hub_url, headers=self.headers, timeout=15) as r:
                if r.status == 200:
                    html_content = await r.text()
                    soup = BeautifulSoup(html_content, "html.parser")

                    for post in soup.find_all("article", class_="elementor-post"):
                        title_elem = post.find("h2", class_="elementor-post__title")
                        if not title_elem:
                            continue

                        link_elem = title_elem.find("a", href=True)
                        if not link_elem:
                            continue

                        url_article = link_elem["href"]
                        if url_article.rstrip("/") == self.changelog_url.rstrip("/"):
                            continue

                        date_elem = post.find("span", class_="elementor-post-date")
                        img_elem = post.find("img")

                        title = title_elem.get_text(strip=True)
                        date_str = date_elem.get_text(strip=True) if date_elem else ""

                        img_url = img_elem["src"] if img_elem else None
                        if img_url and "?" in img_url:
                            img_url = img_url.split("?")[0]

                        article_id = hashlib.md5(url_article.encode()).hexdigest()

                        try:
                            clean_date_str = date_str.replace(".", "").strip()
                            date_obj = datetime.strptime(clean_date_str, "%d %B %Y")
                            discord_date = f"<t:{int(date_obj.timestamp())}:D>"
                        except Exception:
                            date_obj = datetime.min
                            discord_date = date_str

                        articles.append(
                            {
                                "id": article_id,
                                "title": title,
                                "url": url_article,
                                "date_discord": discord_date,
                                "date_obj": date_obj,
                                "image": img_url,
                                "type": "news",
                            }
                        )
        except Exception as e:
            logger.error(f"❌ [Hub] Erreur de parsing HTML News : {e}")

        # 2. PARSING DE LA PAGE DES PATCHNOTES
        try:
            async with self.bot.session.get(self.changelog_url, headers=self.headers, timeout=15) as r2:
                if r2.status == 200:
                    html_changelog = await r2.text()
                    soup_cl = BeautifulSoup(html_changelog, "html.parser")

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

                        texte_resume = ""
                        texte_full_md = ""

                        if content_div:
                            lignes_resume = []
                            lignes_full = []
                            in_bug_section = False
                            bug_count = 0

                            for element in content_div.find_all(["h2", "h3", "h4", "li", "p"]):
                                # GESTION DES TITRES
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
                                    lignes_full.append(f"### {header_text}")  # Titre Markdown pour le fil

                                    if "bug" in header_text.lower() or "fix" in header_text.lower():
                                        in_bug_section = True
                                    else:
                                        in_bug_section = False

                                # GESTION DES PARAGRAPHES STANDARDS
                                elif element.name == "p":
                                    text_brut = element.get_text(separator=" ", strip=True)
                                    lignes_full.append(text_brut)
                                    if not in_bug_section:
                                        lignes_resume.append(text_brut)

                                # GESTION DES LISTES A PUCES
                                elif element.name == "li":
                                    # Pour la version Full Markdown (On tente de conserver le gras)
                                    strong_tag = element.find("strong")
                                    full_text_brut = element.get_text(separator=" ", strip=True)
                                    if strong_tag:
                                        strong_text = strong_tag.get_text(strip=True)
                                        full_text_md = full_text_brut.replace(strong_text, f"**{strong_text}**", 1)
                                        lignes_full.append(f"- {full_text_md}")
                                    else:
                                        lignes_full.append(f"- {full_text_brut}")

                                    # Pour la version Résumé (Embed)
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

            if not posted_news:
                data["posted_news"] = [art["id"] for art in nouveaux_articles]
                await save_hub_config(data)
                logger.info("📡 [Hub] Initialisation : Articles actuels mémorisés en silence.")
                return

            if not guilds_config:
                return

            articles_a_publier = [art for art in nouveaux_articles if art["id"] not in posted_news]

            if articles_a_publier:
                langues_cibles = set(config.get("langue", "fr") for config in guilds_config.values())

                for article in articles_a_publier:
                    # Lecture des News classiques si le texte n'a pas été pré-généré
                    texte_resume = article.get("resume_content", "")

                    if not texte_resume and article["type"] == "news":
                        try:
                            async with self.bot.session.get(article["url"], headers=self.headers, timeout=10) as r:
                                if r.status == 200:
                                    html_article = await r.text()
                                    soup_art = BeautifulSoup(html_article, "html.parser")
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

                        # Traduction du texte complet du fil (Si patchnote)
                        trad_full = (
                            await self.translate_text(article.get("full_content", ""), lang)
                            if article.get("full_content")
                            else ""
                        )

                        traductions[lang] = {"title": trad_titre, "resume": trad_resume, "full": trad_full}

                    couleur = 0x2ECC71 if article["type"] == "patchnotes" else 0x3498DB

                    for guild_id_str, config in guilds_config.items():
                        cat_config = config.get(article["type"], {})
                        channel_id = cat_config.get("channel_id")

                        if not channel_id:
                            continue

                        ping_role = cat_config.get("role", "")
                        langue = config.get("langue", "fr")
                        serveur_cible = config.get("gge_server", "E4K_FR1")

                        titre_prefix = (
                            t(langue, "hub_patchnote_prefix", defaut="⚙️ [MISE À JOUR]")
                            if article["type"] == "patchnotes"
                            else t(langue, "hub_news_prefix", defaut="📰 [ACTUALITÉ]")
                        )
                        resume_fallback = t(
                            langue,
                            "hub_news_resume_fallback",
                            defaut="Cliquez sur le lien ci-dessous pour découvrir les détails de cette annonce.",
                        )
                        read_more_text = t(
                            langue, "hub_news_read_more_append", defaut="*(Lisez la suite sur le site complet)*"
                        )
                        date_label = t(langue, "hub_news_date_label", defaut="Date :")
                        link_label = t(langue, "hub_news_link_label", defaut="🔗 Voir l'annonce complète")

                        final_titre = traductions.get(langue, {}).get("title", article["title"])
                        final_resume = traductions.get(langue, {}).get("resume", "")
                        final_full_md = traductions.get(langue, {}).get("full", "")

                        if final_resume:
                            # On ajoute la mention "Voir le fil" si c'est un patchnote
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

                        if article["image"]:
                            embed.set_image(url=article["image"])
                        embed.set_thumbnail(
                            url="https://i0.wp.com/communityhub.goodgamestudios.com/wp-content/uploads/2023/11/cropped-ggs_logo_reg_rgb_v_300c.png"
                        )

                        await setup_embed_footer(embed, None, langue)

                        channel = self.bot.get_channel(channel_id)
                        if not channel:
                            try:
                                channel = await self.bot.fetch_channel(channel_id)
                            except:
                                continue

                        if channel:
                            try:
                                # 1. Envoi du message principal
                                message = await channel.send(content=ping_role if ping_role else None, embed=embed)

                                # 2. Création du fil de discussion SI patchnote
                                if article["type"] == "patchnotes" and final_full_md:
                                    try:
                                        thread_name = t(
                                            langue,
                                            "hub_thread_name",
                                            titre=final_titre,
                                            defaut=f"📄 Détails : {final_titre}",
                                        )
                                        thread = await message.create_thread(
                                            name=thread_name[:100], auto_archive_duration=1440
                                        )

                                        # Découpage intelligent par sauts de ligne pour éviter les coupures de mots (Limite Discord = 2000)
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
                                    gge_server=serveur_cible,
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
                                    gge_server=serveur_cible,
                                    channel="guild",
                                    recipients=1,
                                    delivered=0,
                                    failed=1,
                                    dm_blocked=0,
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

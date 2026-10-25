import io
import json
import logging
import os
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta

import discord
from discord.ext import commands

import observability as obs
from utils import CONFIG_DIR, t

logger = logging.getLogger("GGE_Bot")


def _read_bytes_sync(path):
    with open(path, "rb") as f:
        return f.read()


class AdminSystem(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.admin_lang = getattr(bot, "admin_lang", "fr")

    @commands.command(name="sync", hidden=True)
    async def sync_tree(self, ctx):
        msg_start = t(
            self.admin_lang, "admin_sync_start", defaut="⏳ Synchronisation de l'arbre des commandes en cours..."
        )
        msg = await ctx.send(msg_start)
        try:
            synced = await self.bot.tree.sync()
            msg_success = t(
                self.admin_lang,
                "admin_sync_success",
                count=len(synced),
                defaut=f"✅ L'arbre a été synchronisé avec succès ! ({len(synced)} commandes disponibles)",
            )
            await msg.edit(content=msg_success)
            logger.info(f"🔄 Commandes slash synchronisées par {ctx.author.name}")
        except Exception as e:
            msg_err = t(self.admin_lang, "admin_sync_error", error=str(e), defaut=f"❌ Erreur : {e}")
            obs.record_error(source="admin", scope="sync", exception=e, cog="admin")
            await msg.edit(content=msg_err)

    @commands.command(name="reload", hidden=True)
    async def reload_cog(self, ctx, extension: str):
        try:
            await self.bot.reload_extension(extension)
            await ctx.send(f"✅ Module `{extension}` rechargé avec succès !")
        except Exception as e:
            obs.record_error(source="admin", scope="reload", exception=e, cog="admin")
            await ctx.send(f"❌ Erreur lors du rechargement :\n```py\n{e}\n```")

    @commands.command(name="restart", aliases=["reboot"], hidden=True)
    async def restart_bot(self, ctx):
        msg = t(
            self.admin_lang,
            "admin_restart",
            defaut="🔄 **Redémarrage en cours...** Le bot se déconnecte et devrait revenir dans quelques secondes.",
        )
        await ctx.send(msg)
        logger.warning(f"🔄 Redémarrage forcé déclenché par l'administrateur ({ctx.author.name})")
        await self.bot.close()

    @commands.command(name="setstatus")
    async def setstatus(self, ctx, *, message: str = None):
        self.bot.custom_status = message
        if message:
            await ctx.send(f"✅ Message ajouté à la rotation des statuts :\n> `{message}`")
        else:
            await ctx.send("🗑️ Le message personnalisé a été retiré de la rotation.")

    @commands.command(name="log", hidden=True)
    async def get_log(self, ctx, requested_date: str = "today"):
        import asyncio

        requested_date = requested_date.lower()
        aujourd_hui = datetime.now().strftime("%Y-%m-%d")
        logs_dir = os.path.join(os.getcwd(), "logs", "general")

        if requested_date in ["aujourd'hui", "today", "j", ""]:
            target_date = aujourd_hui
            file_path = os.path.join(logs_dir, "discord_bot.log")
        elif requested_date in ["hier", "yesterday", "h"]:
            target_date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
            file_path = os.path.join(logs_dir, f"discord_bot_{target_date}.log")
        else:
            target_date = requested_date
            file_path = os.path.join(
                logs_dir, "discord_bot.log" if target_date == aujourd_hui else f"discord_bot_{target_date}.log"
            )

        if not os.path.exists(file_path):
            msg = t(
                self.admin_lang,
                "admin_log_not_found",
                date=target_date,
                defaut=f"❌ Impossible de trouver l'archive de logs pour le **{target_date}**.",
            )
            return await ctx.send(msg)

        try:
            log_data = await asyncio.to_thread(_read_bytes_sync, file_path)
            fichier_virtuel = io.BytesIO(log_data)
            fichier_discord = discord.File(fp=fichier_virtuel, filename=f"GGE_Logs_{target_date}.txt")
            msg = t(
                self.admin_lang,
                "admin_log_success",
                date=target_date,
                defaut="📜 **Extraction réussie.** Voici les journaux :",
            )
            await ctx.send(content=msg, file=fichier_discord)
            logger.info(f"📤 Export des logs ({target_date}) envoyé à {ctx.author.name}.")
        except Exception as e:
            msg = t(
                self.admin_lang, "admin_log_error", error=str(e), defaut=f"❌ Erreur lors de la lecture des logs : {e}"
            )
            await ctx.send(msg)

    @commands.command(name="bypass", hidden=True)
    async def toggle_bypass(self, ctx):
        etat_actuel = getattr(self.bot, "bypass_createur", True)
        self.bot.bypass_createur = not etat_actuel
        if self.bot.bypass_createur:
            await ctx.send(
                "🔓 **Passe-partout ACTIVÉ.** Tu es de nouveau immunisé contre la maintenance et l'anti-spam."
            )
        else:
            await ctx.send("🔒 **Passe-partout DÉSACTIVÉ.** Tu es maintenant un simple mortel !")

    @commands.command(name="bot_servers", hidden=True)
    async def admin_serveurs(self, ctx):
        serveurs = self.bot.guilds
        nb_serveurs = len(serveurs)
        serveurs_tries = sorted(
            serveurs, key=lambda g: g.me.joined_at if g.me and g.me.joined_at else datetime.min, reverse=True
        )

        liste_serveurs_txt = [
            f"=== RAPPORT DES SERVEURS ({nb_serveurs} serveurs) ===",
            f"Généré le : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n",
        ]
        for g in serveurs_tries:
            date_str = g.me.joined_at.strftime("%Y-%m-%d") if g.me and g.me.joined_at else "Inconnue"
            proprio = f"{g.owner.name}" if g.owner else "Inconnu"
            liste_serveurs_txt.append(
                f"• {g.name} (ID: {g.id}) | Proprio: {proprio} | Membres: {g.member_count} | Ajout: {date_str}"
            )

        fichier_virtuel = io.BytesIO("\n".join(liste_serveurs_txt).encode("utf-8-sig"))
        fichier_discord = discord.File(fp=fichier_virtuel, filename=f"GGE_Servers_{nb_serveurs}.txt")

        embed = discord.Embed(
            title=t(self.admin_lang, "admin_servers_title", defaut="🤖 Serveurs connectés"),
            description=t(
                self.admin_lang,
                "admin_servers_desc_file",
                count=nb_serveurs,
                defaut=f"Le bot est sur **{nb_serveurs} serveurs**.",
            ),
            color=discord.Color.blue(),
        )
        await ctx.send(embed=embed, file=fichier_discord)

    @commands.command(name="sync_servers", hidden=True)
    async def sync_servers(self, ctx):
        import asyncio

        msg_wait = await ctx.send("⏳ **Téléchargement du XML de GGE-Tracker en cours...**")
        url = "https://ggetracker.github.io/i18n/servers.xml"

        try:
            async with self.bot.session.get(url, timeout=10) as r:
                if r.status != 200:
                    return await msg_wait.edit(content=f"❌ Erreur {r.status} lors de l'accès au XML de GGE-Tracker.")

                xml_text = await r.text()
                root = ET.fromstring(xml_text)

                config_file = CONFIG_DIR / "servers_cache.json"
                if not config_file.exists():
                    return await msg_wait.edit(content="❌ Fichier `servers_cache.json` introuvable.")

                def _read_config():
                    with open(config_file, encoding="utf-8") as f:
                        return json.load(f)

                config_data = await asyncio.to_thread(_read_config)
                anciennes_infos = config_data.get("servers_info", {})
                nouveau_servers_info = {}

                for server in root.findall(".//server"):
                    name_elem = server.find("name")
                    enabled_elem = server.find("enabled")
                    featured_elem = server.find("featured")

                    if name_elem is not None and name_elem.text:
                        name = name_elem.text.strip()
                        is_enabled = (
                            (enabled_elem.text.strip().lower() == "true")
                            if (enabled_elem is not None and enabled_elem.text)
                            else False
                        )
                        is_featured = (
                            (featured_elem.text.strip().lower() == "true")
                            if (featured_elem is not None and featured_elem.text)
                            else False
                        )

                        minutes = anciennes_infos.get(name, {}).get("scan_minutes")
                        api_name = anciennes_infos.get(name, {}).get("api_name")

                        nouveau_servers_info[name] = {
                            "enabled": is_enabled,
                            "featured": is_featured,
                            "scan_minutes": minutes,
                            "api_name": api_name,
                        }

                if not nouveau_servers_info:
                    return await msg_wait.edit(content="❌ **Erreur de lecture XML** : 0 serveurs trouvés.")

                if nouveau_servers_info != anciennes_infos:
                    config_data["servers_info"] = nouveau_servers_info

                    def _write_config():
                        with open(config_file, "w", encoding="utf-8") as f:
                            json.dump(config_data, f, indent=4, ensure_ascii=False)

                    await asyncio.to_thread(_write_config)
                    msg = f"✅ **Synchronisation réussie !**\nBase mise à jour avec **{len(nouveau_servers_info)} serveurs**."
                else:
                    msg = "✅ **Synchronisation réussie !**\nAucune modification détectée."

                await msg_wait.edit(content=msg)

        except Exception as e:
            logger.error(f"❌ [Admin] Erreur lors de la synchronisation forcée des serveurs : {e}")
            obs.record_error(source="admin", scope="sync_servers", exception=e, cog="admin")
            await msg_wait.edit(content=f"❌ Erreur lors de la synchronisation : `{e}`")

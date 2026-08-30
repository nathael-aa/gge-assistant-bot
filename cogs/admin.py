import hashlib
import io
import json
import logging
import os
import re
from datetime import datetime, timedelta

import discord
from discord.ext import commands

from utils import (
    ADMINS_DIR,
    BASE_DIR,
    CONFIG_DIR,
    JOUEURS_DIR,
    LOCALES_DIR,
    MON_ID_DISCORD,
    SERVEURS_DIR,
    charger_langues,
    load_blocks_async,
    save_blocks_async,
    save_maintenance_async,
    t,
)

logger = logging.getLogger("GGE_Bot")

EMOJI_REGEX = re.compile(r"<(a?):([a-zA-Z0-9_]+):([0-9]+)>")


class AdminCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.admin_lang = "fr"

    # 🟢 FILTRE GLOBAL ADMIN : Seul le créateur peut utiliser ce module
    def cog_check(self, ctx):
        if ctx.author.id != MON_ID_DISCORD:
            return False
        return True

    # ==========================================
    # 🆘 MENU D'AIDE ADMINISTRATEUR
    # ==========================================
    @commands.command(name="ahelp", aliases=["admin_help", "adminhelp"], hidden=True)
    async def admin_help(self, ctx):
        """[CACHÉE] !ahelp : Affiche le récapitulatif des commandes admin."""

        embed = discord.Embed(
            title="🛠️ Panneau de Contrôle Administrateur",
            description="Voici la liste de tes commandes secrètes (utilisables avec le préfixe `!`).",
            color=discord.Color.dark_red(),
        )

        # Catégorie 1 : Système & Core
        embed.add_field(
            name="⚙️ Système & Core",
            value="`!sync` ➔ Synchronise les commandes Slash.\n"
            "`!reload [cogs.nom]` ➔ Recharge un module à chaud.\n"
            "`!restart` ➔ Redémarre le bot (via Docker).\n"
            "`!m` ➔ Active/Désactive le mode maintenance.\n"
            "`!setstatus [msg]` ➔ Ajoute/retire un statut personnalisé.\n"
            "`!log [date]` ➔ Télécharge les logs système.\n"
            "`!bypass` ➔ Active/Désactive ton immunité.\n",
            inline=False,
        )

        # Catégorie 2 : Serveurs & API
        embed.add_field(
            name="🌍 Serveurs Discord & GGE",
            value="`!bot_servers` ➔ Liste les serveurs Discord utilisant le bot.\n"
            "`!bot_leave [ID]` ➔ Force le bot à quitter un serveur Discord.\n"
            "`!sync_servers` ➔ MAJ de la liste des serveurs GGE via XML (GGE-Tracker).\n"
            "`!scan_manuel [ALL/SRV]` ➔ Lance manuellement le scanner de joueurs.",
            inline=False,
        )

        # Catégorie 3 : Calendrier
        embed.add_field(
            name="📅 Gestion du Calendrier",
            value="`!cal_keys` ➔ Liste les clés d'événements valides.\n"
            "`!cal_list` ➔ Liste les événements actifs avec leurs IDs.\n"
            "`!cal_add [clé] [début] [fin]` ➔ Ajoute un événement manuel.\n"
            "`!cal_edit [id] [début] [fin]` ➔ Modifie les dates d'un événement.\n"
            "`!cal_del [id]` ➔ Supprime un événement du cache.\n"
            "`!cal_mapping [clé] [start/end] [HH:MM]` ➔ Modifie l'horaire par défaut.",
            inline=False,
        )

        # Catégorie 4 : Sécurité
        embed.add_field(
            name="⛔ Videur & Modération",
            value="`!ban_cmd [cmd] [raison]` ➔ Désactive une commande.\n"
            "`!unban_cmd [cmd]` ➔ Réactive une commande.\n"
            "`!ban_user [@user] [cmd ou ALL]` ➔ Restreint un utilisateur.\n"
            "`!unban_user [@user] [cmd ou ALL]` ➔ Lève la restriction.",
            inline=False,
        )

        # Catégorie 5 : Traductions
        embed.add_field(
            name="🌐 Traductions (i18n)",
            value="`!i18n_sync` ➔ Scanne le code et met à jour les `.json`.\n"
            "`!i18n_export [lang]` ➔ Génère un `.csv` pour les traducteurs.",
            inline=False,
        )

        # Catégorie 6 : Code & Emojis
        embed.add_field(
            name="🛠️ Code & Emojis",
            value="`!emojis_list` ➔ Liste tous les émojis utilisés.\n"
            "`!replace_raw [old] [new]` ➔ Remplace un texte/émoji dans tout le code.\n"
            "`!check_emojis` ➔ Détécteur d'émojis fantômes dans le code.\n"
            "`!check_bad_emojis` ➔ Détécteur de syntaxe d'émojis cassée.",
            inline=False,
        )

        # Catégorie 7 : Vigilance
        embed.add_field(
            name="🕵️ Système de Vigilance",
            value="`!vigi_add [pseudo_ou_id]` ➔ Place un joueur sous écoute.\n"
            "`!vigi_del [pseudo_ou_id]` ➔ Lève la surveillance.\n"
            "`!vigi_list` ➔ Voir la liste des cibles surveillées.",
            inline=False,
        )

        embed.set_footer(text="Ces commandes sont invisibles pour les utilisateurs normaux.")
        await ctx.send(embed=embed)

    # ==========================================
    # ⚙️ 1. SYSTÈME & CORE
    # ==========================================
    @commands.command(name="sync", hidden=True)
    async def sync_tree(self, ctx):
        """[CACHÉE] !sync : Synchronise l'arbre des commandes Slash."""

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
            msg_err = t(
                self.admin_lang, "admin_sync_error", error=str(e), defaut=f"❌ Erreur lors de la synchronisation : {e}"
            )
            await msg.edit(content=msg_err)

    @commands.command(name="reload", hidden=True)
    async def reload_cog(self, ctx, extension: str):
        """[CACHÉE] !reload [cogs.nom] : Recharge un module à chaud."""

        try:
            await self.bot.reload_extension(extension)
            await ctx.send(f"✅ Module `{extension}` rechargé avec succès !")
        except Exception as e:
            await ctx.send(f"❌ Erreur lors du rechargement :\n```py\n{e}\n```")

    @commands.command(name="restart", aliases=["reboot"], hidden=True)
    async def restart_bot(self, ctx):
        """[CACHÉE] !restart : Redémarre le bot (Nécessite Docker 'restart: always')."""

        msg = t(
            self.admin_lang,
            "admin_restart",
            defaut="🔄 **Redémarrage en cours...** Le bot se déconnecte et devrait revenir dans quelques secondes.",
        )
        await ctx.send(msg)
        logger.warning(f"🔄 Redémarrage forcé déclenché par l'administrateur ({ctx.author.name})")
        await self.bot.close()

    @commands.command(name="m", hidden=True)
    async def toggle_maintenance(self, ctx):
        """[CACHÉE] !m : Bascule le bot en mode maintenance."""

        self.bot.maintenance_mode = not self.bot.maintenance_mode
        await save_maintenance_async(self.bot.maintenance_mode)

        langue = getattr(self, "admin_lang", "fr")

        if self.bot.maintenance_mode:
            msg = t(
                langue,
                "admin_maint_on",
                defaut="🚧 **Mode Maintenance : 🔴 ACTIVÉ**\n*Le bot ignore toutes les commandes Slash.*",
            )
            await ctx.send(msg)
            statut_maint = t("en", "bot_activity_maintenance", defaut="🛠️ Under Maintenance 🛠️")
            activity = discord.Activity(type=discord.ActivityType.watching, name=statut_maint)
            await self.bot.change_presence(activity=activity, status=discord.Status.dnd)
        else:
            msg = t(
                langue,
                "admin_maint_off",
                defaut="🚧 **Mode Maintenance : 🟢 DÉSACTIVÉ**\n*Le bot est de nouveau accessible à tous.*",
            )
            await ctx.send(msg)
            activity = discord.Activity(type=discord.ActivityType.watching, name="/setup ➔ /help")
            await self.bot.change_presence(activity=activity, status=discord.Status.online)

    @commands.command(name="setstatus")
    async def setstatus(self, ctx, *, message: str = None):
        """[Admin] Ajoute ou retire un message de statut personnalisé."""

        self.bot.custom_status = message
        if message:
            await ctx.send(f"✅ Message ajouté à la rotation des statuts :\n> `{message}`")
        else:
            await ctx.send("🗑️ Le message personnalisé a été retiré de la rotation.")

    @commands.command(name="log", hidden=True)
    async def get_log(self, ctx, requested_date: str = "today"):
        """[CACHÉE] !log [date] : Récupère les logs système pour une date spécifique."""

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
            if target_date == aujourd_hui:
                file_path = os.path.join(logs_dir, "discord_bot.log")
            else:
                file_path = os.path.join(logs_dir, f"discord_bot_{target_date}.log")

        if not os.path.exists(file_path):
            msg = t(
                self.admin_lang,
                "admin_log_not_found",
                date=target_date,
                defaut=f"❌ Impossible de trouver l'archive de logs pour le **{target_date}**.",
            )
            return await ctx.send(msg)

        try:
            with open(file_path, "rb") as f:
                log_data = f.read()

            fichier_virtuel = io.BytesIO(log_data)
            fichier_discord = discord.File(fp=fichier_virtuel, filename=f"GGE_Logs_{target_date}.txt")

            msg = t(
                self.admin_lang,
                "admin_log_success",
                date=target_date,
                defaut=f"📜 **Extraction réussie.** Voici les journaux système du **{target_date}** :",
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
        """[CACHÉE] !bypass : Active/Désactive ton passe-partout de créateur."""

        # On lit l'état actuel (True par défaut si la variable n'existe pas encore)
        etat_actuel = getattr(self.bot, "bypass_createur", True)
        nouvel_etat = not etat_actuel

        # On sauvegarde le nouvel état dans le bot
        self.bot.bypass_createur = nouvel_etat

        if nouvel_etat:
            await ctx.send(
                "🔓 **Passe-partout ACTIVÉ.** Tu es de nouveau immunisé contre la maintenance, l'anti-spam et le videur."
            )
        else:
            await ctx.send(
                "🔒 **Passe-partout DÉSACTIVÉ.** Tu es maintenant un simple mortel ! Tes commandes Slash subiront les mêmes contrôles que les autres joueurs.\n*(Tape `!bypass` pour annuler).*"
            )

    # ==========================================
    # 🌍 2. SERVEURS DISCORD & GGE
    # ==========================================
    @commands.command(name="bot_servers", hidden=True)
    async def admin_serveurs(self, ctx):
        """[CACHÉE] !bot_servers : Liste les serveurs Discord sur lesquels le bot est présent."""

        serveurs = self.bot.guilds
        nb_serveurs = len(serveurs)
        serveurs_tries = sorted(
            serveurs, key=lambda g: g.me.joined_at if g.me and g.me.joined_at else datetime.min, reverse=True
        )

        liste_serveurs_txt = [
            f"=== RAPPORT DES SERVEURS GGE ASSISTANT ({nb_serveurs} serveurs) ===",
            f"Généré le : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n",
        ]

        for g in serveurs_tries:
            date_str = g.me.joined_at.strftime("%Y-%m-%d") if g.me and g.me.joined_at else "Inconnue"
            proprio = f"{g.owner.name}" if g.owner else "Inconnu"
            ligne = f"• {g.name} (ID: {g.id}) | Propriétaire: {proprio} | Membres: {g.member_count} | Ajout: {date_str}"
            liste_serveurs_txt.append(ligne)

        texte_complet = "\n".join(liste_serveurs_txt)
        fichier_virtuel = io.BytesIO(texte_complet.encode("utf-8-sig"))
        fichier_discord = discord.File(fp=fichier_virtuel, filename=f"GGE_Servers_{nb_serveurs}.txt")

        embed_title = t(self.admin_lang, "admin_servers_title", defaut="🤖 Serveurs connectés")
        embed_desc = t(
            self.admin_lang,
            "admin_servers_desc_file",
            count=nb_serveurs,
            defaut=f"Le bot est actuellement présent sur **{nb_serveurs} serveurs**.\n📄 *La liste complète et détaillée est disponible dans le fichier ci-joint.*",
        )

        embed = discord.Embed(title=embed_title, description=embed_desc, color=discord.Color.blue())
        embed.set_footer(text=f"Total : {nb_serveurs} serveurs | GGE Assistant Admin")
        await ctx.send(embed=embed, file=fichier_discord)

    @commands.command(name="bot_leave", hidden=True)
    async def admin_quitter(self, ctx, server_id: str):
        """[CACHÉE] !bot_leave [server_id] : Force le bot à quitter un serveur Discord."""

        try:
            guild = self.bot.get_guild(int(server_id))
            if guild is None:
                msg = t(
                    self.admin_lang,
                    "admin_leave_fail",
                    id=server_id,
                    defaut=f"❌ Serveur introuvable. Vérifie l'ID `{server_id}`.",
                )
                return await ctx.send(msg)

            nom_serveur = guild.name
            await guild.leave()

            msg = t(
                self.admin_lang,
                "admin_leave_success",
                nom=nom_serveur,
                defaut=f"✅ Succès : Le bot a quitté le serveur **{nom_serveur}**.",
            )
            await ctx.send(msg)
            logger.info(f"🚪 Le bot a quitté le serveur {nom_serveur} via commande admin.")
        except Exception as e:
            msg = t(self.admin_lang, "admin_leave_error", error=str(e), defaut=f"❌ Erreur : {e}")
            await ctx.send(msg)

    @commands.command(name="sync_servers", hidden=True)
    async def sync_servers(self, ctx):
        """[CACHÉE] !sync_servers : Force la synchronisation de la liste des serveurs via XML."""

        import json
        import xml.etree.ElementTree as ET

        msg_wait = await ctx.send("⏳ **Téléchargement du XML de GGE-Tracker en cours...**")
        url = "https://ggetracker.github.io/i18n/servers.xml"

        try:
            async with self.bot.session.get(url, timeout=10) as r:
                if r.status != 200:
                    return await msg_wait.edit(content=f"❌ Erreur {r.status} lors de l'accès au XML de GGE-Tracker.")

                xml_text = await r.text()
                root = ET.fromstring(xml_text)

                config_file = CONFIG_DIR / "configuration.json"
                if not config_file.exists():
                    return await msg_wait.edit(content="❌ Fichier `configuration.json` introuvable.")

                with open(config_file, encoding="utf-8") as f:
                    config_data = json.load(f)

                anciennes_infos = config_data.get("servers_info", {})
                vieux_scan_minutes = config_data.get("scan_minutes", {})
                vieux_noms_api = config_data.get("servers", {})

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
                        if minutes is None:
                            minutes = vieux_scan_minutes.get(name)

                        api_name = anciennes_infos.get(name, {}).get("api_name")
                        if api_name is None:
                            api_name = vieux_noms_api.get(name.lower())

                        nouveau_servers_info[name] = {
                            "enabled": is_enabled,
                            "featured": is_featured,
                            "scan_minutes": minutes,
                            "api_name": api_name,
                        }

                if not nouveau_servers_info:
                    return await msg_wait.edit(
                        content="❌ **Erreur de lecture XML** : 0 serveurs trouvés. Le format du fichier a peut-être changé."
                    )

                if nouveau_servers_info != anciennes_infos:
                    config_data["servers_info"] = nouveau_servers_info

                    config_data.pop("active_servers", None)
                    config_data.pop("special_servers", None)
                    config_data.pop("scan_minutes", None)
                    config_data.pop("servers", None)

                    with open(config_file, "w", encoding="utf-8") as f:
                        json.dump(config_data, f, indent=4, ensure_ascii=False)

                    msg = f"✅ **Synchronisation réussie !**\nLa base de données a été unifiée et mise à jour avec **{len(nouveau_servers_info)} serveurs**."
                else:
                    msg = f"✅ **Synchronisation réussie !**\nAucune modification détectée (les **{len(nouveau_servers_info)} serveurs** sont déjà parfaitement à jour)."

                await msg_wait.edit(content=msg)

        except Exception as e:
            logger.error(f"❌ [Admin] Erreur lors de la synchronisation forcée des serveurs : {e}")
            await msg_wait.edit(content=f"❌ Erreur lors de la synchronisation : `{e}`")

    @commands.command(name="scan_manuel", hidden=True)
    async def scan_manuel(self, ctx, cible: str = "ALL"):
        """[CACHÉE] !scan_manuel [ALL/Serveur] : Lance manuellement le scanner (Tous ou un seul)."""

        cible = cible.upper()
        scan_server = self.bot.get_cog("ScanCog")

        if scan_server is None:
            return await ctx.send("❌ **Erreur :** Le module `ScanCog` n'est pas chargé dans le bot.")

        if cible == "ALL":
            await ctx.send("⏳ **Lancement manuel du scanner GLOBAL...** (Tous les serveurs)")
            try:
                logger.info(f"🚀 Lancement manuel du ServerScanner GLOBAL par {ctx.author.name}")
                await scan_server.daily_scan.coro(scan_server)
                await ctx.send("✅ **Scan global terminé avec succès !**")
            except Exception as e:
                logger.error(f"❌ Erreur critique scan manuel (ALL) : {e}")
                await ctx.send(f"⚠️ **Erreur lors de l'exécution (ALL) :**\n```py\n{e}\n```")
        else:
            await ctx.send(f"⏳ **Lancement du scanner ciblé sur le serveur : `{cible}`...**")
            try:
                logger.info(f"🚀 Lancement manuel du ServerScanner ({cible}) par {ctx.author.name}")
                if hasattr(scan_server, "scan_specific_server"):
                    reussite = await scan_server.scan_specific_server(cible)
                    if reussite:
                        await ctx.send(f"✅ **Scan manuel terminé avec succès pour `{cible}` !**")
                    else:
                        await ctx.send(
                            f"❌ **Échec du scan pour `{cible}`.** Le nom du serveur est peut-être incorrect (ex: `E4K_FR1`)."
                        )
                else:
                    await ctx.send(
                        "⚠️ **Attention :** La fonction `scan_specific_server` est introuvable dans `ScanCog`."
                    )
            except Exception as e:
                logger.error(f"❌ Erreur critique scan manuel ({cible}) : {e}")
                await ctx.send(f"⚠️ **Erreur lors de l'exécution ({cible}) :**\n```py\n{e}\n```")

    # ==========================================
    # 📅 3. GESTIONNAIRE DU CALENDRIER (ADMIN)
    # ==========================================
    async def _reload_calendar_cache(self, ctx):
        """Fonction interne pour forcer la mise à jour du CalendrierCog en mémoire"""
        cog = self.bot.get_cog("CalendrierCog")
        if cog:
            await cog.load_cache_from_file()
            if hasattr(cog, "load_event_mapping"):
                cog.event_mapping = cog.load_event_mapping()
            await ctx.send("♻️ *Le cache du calendrier a été mis à jour en mémoire.*")
        else:
            await ctx.send("⚠️ *Attention : Le module Calendrier n'est pas chargé. Redémarre le bot pour appliquer.*")

    @commands.command(name="cal_keys", hidden=True)
    async def cal_keys(self, ctx):
        """[CACHÉE] Liste toutes les clés valides pour ajouter/modifier un événement."""

        import json

        mapping_file = CONFIG_DIR / "event_mapping.json"

        if not mapping_file.exists():
            return await ctx.send("❌ Fichier `event_mapping.json` introuvable.")

        with open(mapping_file, encoding="utf-8") as f:
            mapping = json.load(f)

        embed = discord.Embed(
            title="🔑 Clés d'Événements GGE",
            description="Voici la liste exacte des clés à utiliser pour les commandes `!cal_add` et `!cal_mapping`.\n⚠️ **N'oublie pas de mettre des guillemets `\" \"` s'il y a des espaces !**",
            color=discord.Color.gold(),
        )

        lignes = []
        for key, data in mapping.items():
            emoji = data.get("emoji", "📅")
            nom = data.get("name_default", key.title())
            lignes.append(f'{emoji} **{nom}** ➔ `"{key}"`')

        embed.add_field(name="Clés valides", value="\n".join(lignes), inline=False)
        await ctx.send(embed=embed)

    @commands.command(name="cal_list", hidden=True)
    async def cal_list(self, ctx):
        """[CACHÉE] Liste tous les événements actifs avec leurs IDs."""

        cal_file = SERVEURS_DIR / "calendrier.json"
        if not cal_file.exists():
            return await ctx.send("❌ Fichier calendrier.json introuvable.")

        import json

        with open(cal_file, encoding="utf-8") as f:
            data = json.load(f)

        events = data.get("cached_events", [])
        if not events:
            return await ctx.send("ℹ️ Aucun événement en cache.")

        events = sorted(events, key=lambda x: x["start"])
        lignes = ["=== LISTE DES ÉVÉNEMENTS GGE ===", f"Généré le : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"]
        for ev in events:
            start_str = ev["start"].replace("T", " ")
            end_str = ev["end"].replace("T", " ")
            lignes.append(f"[{ev['id']}] {ev['key'].upper()} : {start_str} ➔ {end_str}")

        import io

        buffer = io.BytesIO("\n".join(lignes).encode("utf-8"))
        fichier = discord.File(fp=buffer, filename="cal_events_ids.txt")

        await ctx.send(f"✅ **{len(events)} événements trouvés.** Voici la liste avec les IDs :", file=fichier)

    @commands.command(name="cal_add", hidden=True)
    async def cal_add(self, ctx, event_key: str, start: str, end: str):
        """[CACHÉE] Ajoute un event. Ex: !cal_add "rift raid" "15/09/2026 11:00" "22/09/2026 09:00" """

        try:
            dt_start = datetime.strptime(start, "%d/%m/%Y %H:%M")
            dt_end = datetime.strptime(end, "%d/%m/%Y %H:%M")
        except ValueError:
            return await ctx.send('❌ Format de date invalide. Utilise : `"JJ/MM/AAAA HH:MM"`')

        import json

        mapping_file = CONFIG_DIR / "event_mapping.json"
        with open(mapping_file, encoding="utf-8") as f:
            mapping = json.load(f)

        if event_key.lower() not in mapping:
            cles = ", ".join(f"`{k}`" for k in mapping.keys())
            return await ctx.send(f"❌ La clé `{event_key}` n'existe pas. Clés valides : {cles}")

        cal_file = SERVEURS_DIR / "calendrier.json"
        with open(cal_file, encoding="utf-8") as f:
            data = json.load(f)

        uid = f"{event_key.lower()}_{int(dt_start.timestamp())}"
        new_id = hashlib.md5(uid.encode()).hexdigest()[:5]

        data.setdefault("cached_events", []).append(
            {"id": new_id, "key": event_key.lower(), "start": dt_start.isoformat(), "end": dt_end.isoformat()}
        )

        with open(cal_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        await ctx.send(f"✅ Événement `{event_key}` ajouté avec l'ID `{new_id}`.")
        await self._reload_calendar_cache(ctx)

    @commands.command(name="cal_edit", hidden=True)
    async def cal_edit(self, ctx, event_id: str, start: str, end: str):
        """[CACHÉE] Modifie les dates. Ex: !cal_edit 1a2b3 "15/09/2026 11:00" "22/09/2026 09:00" """

        try:
            dt_start = datetime.strptime(start, "%d/%m/%Y %H:%M")
            dt_end = datetime.strptime(end, "%d/%m/%Y %H:%M")
        except ValueError:
            return await ctx.send('❌ Format de date invalide. Utilise : `"JJ/MM/AAAA HH:MM"`')

        cal_file = SERVEURS_DIR / "calendrier.json"
        import json

        with open(cal_file, encoding="utf-8") as f:
            data = json.load(f)

        trouve = False
        for ev in data.get("cached_events", []):
            if ev.get("id") == event_id:
                ev["start"] = dt_start.isoformat()
                ev["end"] = dt_end.isoformat()
                trouve = True
                break

        if not trouve:
            return await ctx.send(f"❌ Aucun événement trouvé avec l'ID `{event_id}`.")

        with open(cal_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        await ctx.send(f"✅ Événement `{event_id}` mis à jour :\n**Début:** {start}\n**Fin:** {end}")
        await self._reload_calendar_cache(ctx)

    @commands.command(name="cal_del", hidden=True)
    async def cal_del(self, ctx, event_id: str):
        """[CACHÉE] Supprime un événement du calendrier. Ex: !cal_del 1a2b3"""

        cal_file = SERVEURS_DIR / "calendrier.json"
        import json

        with open(cal_file, encoding="utf-8") as f:
            data = json.load(f)

        events = data.get("cached_events", [])
        events_filtres = [ev for ev in events if ev.get("id") != event_id]

        if len(events) == len(events_filtres):
            return await ctx.send(f"❌ Aucun événement trouvé avec l'ID `{event_id}`.")

        data["cached_events"] = events_filtres

        if "deleted_events" not in data:
            data["deleted_events"] = []
        if event_id not in data["deleted_events"]:
            data["deleted_events"].append(event_id)

        with open(cal_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        await ctx.send(
            f"🗑️ Événement `{event_id}` supprimé et ajouté à la **liste noire** (il sera ignoré par les futurs scans)."
        )
        await self._reload_calendar_cache(ctx)

    @commands.command(name="cal_mapping", hidden=True)
    async def cal_mapping(self, ctx, event_key: str, cible: str, heure: str):
        """[CACHÉE] Modifie l'heure par défaut. Ex: !cal_mapping "berimond" start "10:30" """

        if cible.lower() not in ["start", "end"]:
            return await ctx.send("❌ La cible doit être `start` ou `end`.")

        if not re.match(r"^\d{2}:\d{2}$", heure):
            return await ctx.send("❌ L'heure doit être au format `HH:MM` (ex: 11:00).")

        import json

        mapping_file = CONFIG_DIR / "event_mapping.json"

        with open(mapping_file, encoding="utf-8") as f:
            mapping = json.load(f)

        if event_key.lower() not in mapping:
            cles = ", ".join(f"`{k}`" for k in mapping.keys())
            return await ctx.send(f"❌ La clé `{event_key}` n'existe pas. Clés valides : {cles}")

        mapping[event_key.lower()][cible.lower()] = heure

        with open(mapping_file, "w", encoding="utf-8") as f:
            json.dump(mapping, f, indent=2, ensure_ascii=False)

        await ctx.send(f"⚙️ Mapping de `{event_key}` mis à jour : **{cible.lower()}** ➔ `{heure}`.")
        await self._reload_calendar_cache(ctx)

    # ==========================================
    # ⛔ 4. VIDEUR & MODÉRATION
    # ==========================================
    @commands.command(name="ban_cmd", hidden=True)
    async def ban_cmd(self, ctx, command: str, *, reason: str = "Maintenance."):
        """[CACHÉE] !ban_cmd [command] [reason] : Bloque une commande globalement."""

        data = await load_blocks_async()
        cmd_clean = command.replace("/", "").strip()
        if "global_commands" not in data:
            data["global_commands"] = {}

        data["global_commands"][cmd_clean] = reason
        await save_blocks_async(data)

        msg = t(
            self.admin_lang,
            "admin_ban_cmd",
            cmd=cmd_clean,
            reason=reason,
            defaut=f"⛔ **Videur** : La commande `/{cmd_clean}` est bloquée pour tout le monde.\n*Raison : {reason}*",
        )
        await ctx.send(msg)

    @commands.command(name="unban_cmd", hidden=True)
    async def unban_cmd(self, ctx, command: str):
        """[CACHÉE] !unban_cmd [command] : Débloque une commande globalement."""

        data = await load_blocks_async()
        cmd_clean = command.replace("/", "").strip()

        if cmd_clean in data.get("global_commands", {}):
            del data["global_commands"][cmd_clean]
            await save_blocks_async(data)
            msg = t(
                self.admin_lang,
                "admin_unban_cmd_success",
                cmd=cmd_clean,
                defaut=f"✅ **Videur** : La commande `/{cmd_clean}` est de nouveau disponible.",
            )
            await ctx.send(msg)
        else:
            msg = t(
                self.admin_lang,
                "admin_unban_cmd_fail",
                cmd=cmd_clean,
                defaut=f"⚠️ La commande `/{cmd_clean}` n'était pas bloquée.",
            )
            await ctx.send(msg)

    async def _purge_user_data(self, uid: str):
        """Parcourt les fichiers de configuration (hors serveurs) pour supprimer les données de l'utilisateur."""

        # Liste allégée : uniquement le cœur, le joueur et l'admin
        fichiers_a_scanner = [
            CONFIG_DIR / "users.json",
            CONFIG_DIR / "target.json",
            JOUEURS_DIR / "votes.json",
            JOUEURS_DIR / "surveillance.json",
            JOUEURS_DIR / "rival_radar.json",
            JOUEURS_DIR / "forteresses_sessions.json",
            JOUEURS_DIR / "discord_pseudos.json",
            ADMINS_DIR / "contacts.json",
        ]

        def purger_donnees(obj):
            modifie = False

            if isinstance(obj, dict):
                # 1. Si l'ID du banni est directement une clé principale
                if uid in obj:
                    del obj[uid]
                    modifie = True

                # 2. Scanner les sous-dossiers
                cles_a_supprimer = []
                for k, v in obj.items():
                    if isinstance(v, dict):
                        # Si le dictionnaire appartient à l'utilisateur
                        if (
                            str(v.get("author_id")) == uid
                            or str(v.get("user_id")) == uid
                            or str(v.get("added_by")) == uid
                        ):
                            cles_a_supprimer.append(k)
                        else:
                            if purger_donnees(v):
                                modifie = True
                    elif isinstance(v, list):
                        if purger_donnees(v):
                            modifie = True

                for k in cles_a_supprimer:
                    del obj[k]
                    modifie = True

            elif isinstance(obj, list):
                longueur_initiale = len(obj)
                # 3. Filtrer les listes : on détruit les éléments qui ont le banni pour auteur
                obj[:] = [
                    item
                    for item in obj
                    if not (
                        isinstance(item, dict)
                        and (
                            str(item.get("author_id")) == uid
                            or str(item.get("user_id")) == uid
                            or str(item.get("added_by")) == uid
                        )
                    )
                ]
                if len(obj) < longueur_initiale:
                    modifie = True

                # On continue l'exploration sur ce qui reste
                for item in obj:
                    if purger_donnees(item):
                        modifie = True

            return modifie

        fichiers_purges = []
        for chemin in fichiers_a_scanner:
            if not chemin.exists():
                continue

            try:
                with open(chemin, encoding="utf-8") as f:
                    data = json.load(f)

                a_ete_modifie = purger_donnees(data)

                if a_ete_modifie:
                    with open(chemin, "w", encoding="utf-8") as f:
                        json.dump(data, f, indent=2, ensure_ascii=False)
                    fichiers_purges.append(chemin.name)
            except Exception as e:
                logger.error(f"❌ Erreur lors de la purge de {chemin.name} : {e}")

        if fichiers_purges:
            logger.info(f"🧹 [Videur] Purge complète pour {uid}. Fichiers modifiés : {', '.join(fichiers_purges)}")

        return fichiers_purges

    @commands.command(name="ban_user", hidden=True)
    async def ban_user(self, ctx, user: discord.User, command: str, *, reason: str = "Abus."):
        """[CACHÉE] !ban_user [user] [command] [reason] : Restreint un utilisateur."""

        data = await load_blocks_async()
        uid = str(user.id)

        # Formatage majuscule tolérant
        cmd_clean = command.replace("/", "").strip()
        if cmd_clean.upper() == "ALL":
            cmd_clean = "ALL"

        if "blocked_users" not in data:
            data["blocked_users"] = {}
        if uid not in data["blocked_users"]:
            data["blocked_users"][uid] = {}

        data["blocked_users"][uid][cmd_clean] = reason
        await save_blocks_async(data)

        # 🔥 Lancement du "Droit à l'oubli" si bannissement total
        purge_msg = ""
        if cmd_clean == "ALL":
            fichiers_modifies = await self._purge_user_data(uid)
            if fichiers_modifies:
                purge_msg = f"\n🧹 *Purge système effectuée dans {len(fichiers_modifies)} fichiers.*"
            else:
                purge_msg = "\n🧹 *Aucune donnée parasite liée à ce joueur trouvée.*"

        scope = "Toutes les commandes" if cmd_clean == "ALL" else f"La commande `/{cmd_clean}`"
        msg = t(
            self.admin_lang,
            "admin_ban_user",
            user=user.name,
            uid=uid,
            scope=scope,
            reason=reason,
            defaut=f"🛑 **Videur** : {user.name} (`{uid}`) a été restreint.\n*Cible : {scope}*\n*Raison : {reason}*{purge_msg}",
        )
        await ctx.send(msg)

    @commands.command(name="unban_user", hidden=True)
    async def unban_user(self, ctx, user: discord.User, command: str):
        """[CACHÉE] !unban_user [user] [command] : Lève la restriction d'un utilisateur."""

        data = await load_blocks_async()
        uid = str(user.id)
        cmd_clean = command.replace("/", "").strip()

        if uid in data.get("blocked_users", {}) and cmd_clean in data["blocked_users"][uid]:
            del data["blocked_users"][uid][cmd_clean]
            if not data["blocked_users"][uid]:
                del data["blocked_users"][uid]
            await save_blocks_async(data)

            scope = "toutes les commandes" if cmd_clean == "ALL" else f"`/{cmd_clean}`"
            msg = t(
                self.admin_lang,
                "admin_unban_user_success",
                user=user.name,
                scope=scope,
                defaut=f"✅ **Videur** : {user.name} a été pardonné pour {scope}.",
            )
            await ctx.send(msg)
        else:
            msg = t(
                self.admin_lang,
                "admin_unban_user_fail",
                user=user.name,
                cmd=cmd_clean,
                defaut=f"⚠️ Aucun blocage actif trouvé pour {user.name} sur {cmd_clean}.",
            )
            await ctx.send(msg)

    # ==========================================
    # 🌐 5. TRADUCTIONS (i18n)
    # ==========================================
    class I18nConfirmView(discord.ui.View):
        def __init__(self, ctx):
            super().__init__(timeout=120)
            self.ctx = ctx
            self.delete = False
            self.responded = False
            self.message = None

        @discord.ui.button(label="🗑️ Supprimer les clés", style=discord.ButtonStyle.danger)
        async def btn_delete(self, interaction: discord.Interaction, button: discord.ui.Button):
            self.delete = True
            self.responded = True
            for item in self.children:
                item.disabled = True
            await interaction.response.edit_message(view=self)
            self.stop()

        @discord.ui.button(label="💾 Conserver (Trier & Ajouter)", style=discord.ButtonStyle.success)
        async def btn_keep(self, interaction: discord.Interaction, button: discord.ui.Button):
            self.delete = False
            self.responded = True
            for item in self.children:
                item.disabled = True
            await interaction.response.edit_message(view=self)
            self.stop()

        async def on_timeout(self):
            for item in self.children:
                item.disabled = True
            if self.message:
                try:
                    await self.message.edit(view=self)
                except discord.HTTPException:
                    pass

    @commands.command(name="i18n_sync", aliases=["sortlang", "sort_locales"], hidden=True)
    async def i18n_sync(self, ctx):
        """[CACHÉE] !i18n_sync : Scanne, Trie et Synchronise les fichiers JSON avec sécurité."""
        import json
        import re

        pattern = re.compile(r'\bt\s*\(\s*[^,]+,\s*["\']([a-zA-Z0-9_]+)["\']')
        cles_utilisees = set()

        for root, dirs, files in os.walk(BASE_DIR):
            if ".git" in root or "__pycache__" in root:
                continue
            for file in files:
                if file.endswith(".py"):
                    with open(os.path.join(root, file), encoding="utf-8") as f:
                        cles_utilisees.update(pattern.findall(f.read()))

        for i in range(1, 13):
            cles_utilisees.add(f"month_{i:02d}")

        cles_utilisees.update(
            [
                "cal_ev_nomad",
                "cal_ev_samurai",
                "cal_ev_bloodcrow",
                "cal_ev_realms",
                "cal_ev_storm",
                "cal_ev_berimond",
                "cal_ev_bladecoast",
                "cal_ev_rift",
                "cal_ev_tournament",
                "cal_ev_horizon",
                "cal_ev_outer",
                "cal_ev_patronage",
                "cal_ev_nobility",
            ]
        )

        locales_data = {}
        for fichier in LOCALES_DIR.glob("*.json"):
            with open(fichier, encoding="utf-8") as f:
                locales_data[fichier.stem] = json.load(f)

        fr_data = locales_data.get("fr", {})
        cles_existantes_fr = set(fr_data.keys())

        manquantes_fr = cles_utilisees - cles_existantes_fr
        inutiles_fr = cles_existantes_fr - cles_utilisees

        supprimer_cles = False

        if inutiles_fr:
            liste_inutiles_txt = "\n".join(sorted(inutiles_fr))
            file_bytes = io.BytesIO(liste_inutiles_txt.encode("utf-8"))
            discord_file = discord.File(fp=file_bytes, filename="cles_orphelines.txt")

            embed_warn = discord.Embed(
                title="⚠️ Clés inutilisées détectées",
                description=(
                    f"J'ai trouvé **{len(inutiles_fr)}** clés dans le fichier de référence `fr.json` "
                    f"qui n'apparaissent plus dans ton code Python.\n\n"
                    f"📄 **Ouvre le fichier texte ci-joint pour voir la liste complète sans troncature !**\n\n"
                    "Que veux-tu faire ?"
                ),
                color=discord.Color.orange(),
            )
            view = self.I18nConfirmView(ctx)
            msg = await ctx.send(embed=embed_warn, view=view, file=discord_file)
            view.message = msg
            await view.wait()

            if not view.responded:
                return await msg.edit(content="⏱️ **Délai expiré.** Action annulée par sécurité.", embed=None, view=None)

            supprimer_cles = view.delete
        else:
            msg = await ctx.send("🔄 Scan en cours, aucune clé à supprimer détectée...")

        if supprimer_cles:
            for c in inutiles_fr:
                fr_data.pop(c, None)

        for c in manquantes_fr:
            fr_data[c] = "[TODO] Texte manquant"
        fr_data_sorted = dict(sorted(fr_data.items()))
        locales_data["fr"] = fr_data_sorted

        for lang, data in locales_data.items():
            if lang == "fr":
                continue
            cles_lang = list(data.keys())
            for c in cles_lang:
                if c not in fr_data_sorted:
                    data.pop(c, None)
            for c in fr_data_sorted.keys():
                if c not in data:
                    val = fr_data_sorted[c]
                    data[c] = f"[TODO] {val}" if val != "[TODO] Texte manquant" else "[TODO] Texte manquant"
            locales_data[lang] = dict(sorted(data.items()))

        for lang, data in locales_data.items():
            fichier = LOCALES_DIR / f"{lang}.json"
            with open(fichier, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4, ensure_ascii=False)

        charger_langues()

        action_txt = "🗑️ Supprimées" if supprimer_cles else "💾 Ignorées (Conservées)"
        desc = (
            f"**Fichiers traités :** `{len(locales_data)}`\n"
            f"**Total des clés (après tri) :** `{len(fr_data_sorted)}`\n\n"
            f"➕ **Clés ajoutées :** `{len(manquantes_fr)}`\n"
            f"{action_txt} **:** `{len(inutiles_fr)}`\n\n"
            f"🟢 *Les langues ont été rechargées et triées alphabétiquement.*\n"
            f"*(Cherche '[TODO]' dans tes fichiers JSON pour voir ce qu'il faut traduire !)*"
        )
        embed_success = discord.Embed(
            title="✨ Synchronisation Terminée", description=desc, color=discord.Color.green()
        )

        if isinstance(msg, discord.Message):
            await msg.edit(content=None, embed=embed_success, view=None)
        else:
            await ctx.send(embed=embed_success)

    @commands.command(name="i18n_export", hidden=True)
    async def i18n_export(self, ctx, langue_cible: str = "en"):
        """[CACHÉE] !i18n_export [langue] : Génère un CSV pour les traducteurs communautaires."""
        import csv
        import json
        import re

        fr_file = LOCALES_DIR / "fr.json"
        cible_file = LOCALES_DIR / f"{langue_cible}.json"

        if not fr_file.exists() or not cible_file.exists():
            return await ctx.send(f"❌ Impossible de trouver `fr.json` ou `{langue_cible}.json`.")

        with open(fr_file, encoding="utf-8") as f:
            fr_data = json.load(f)
        with open(cible_file, encoding="utf-8") as f:
            cible_data = json.load(f)

        output = io.StringIO()
        writer = csv.writer(output, delimiter=";")
        writer.writerow(
            [
                "Clé (NE PAS TOUCHER)",
                "Texte Français (RÉFÉRENCE)",
                f"Traduction ({langue_cible.upper()})",
                "Variables obligatoires",
            ]
        )

        for cle, texte_fr in fr_data.items():
            variables = re.findall(r"\{[a-zA-Z0-9_]+\}", texte_fr)
            vars_str = ", ".join(variables) if variables else "Aucune"
            texte_cible = cible_data.get(cle, "[TODO] Texte manquant")
            writer.writerow([cle, texte_fr, texte_cible, vars_str])

        output.seek(0)
        file_bytes = io.BytesIO(output.getvalue().encode("utf-8-sig"))
        discord_file = discord.File(fp=file_bytes, filename=f"traduction_{langue_cible}.csv")

        embed = discord.Embed(
            title=f"📝 Fichier de traduction prêt ({langue_cible.upper()})",
            description="Envoie ce fichier `.csv` à tes traducteurs.\nIls peuvent l'ouvrir avec **Excel** ou **Google Sheets**.\n\n⚠️ Dis-leur bien de :\n1. Ne pas modifier la colonne 1.\n2. Ne pas effacer les variables (colonne 4).",
            color=discord.Color.blue(),
        )
        await ctx.send(embed=embed, file=discord_file)

    # ==========================================
    # 🛠️ 6. CODE & EMOJIS
    # ==========================================
    @commands.command(name="emojis_list", hidden=True)
    async def emojis_list(self, ctx):
        """[CACHÉE] !emojis_list : Scan le code pour trouver tous les émojis utilisés."""

        msg_wait = await ctx.send("⏳ **Scan du code en cours...**")
        emojis_found = {}
        exclude_dirs = [".git", "__pycache__", "venv", "logs", "data"]

        for root, dirs, files in os.walk(BASE_DIR):
            dirs[:] = [d for d in dirs if not any(excl in os.path.join(root, d) for excl in exclude_dirs)]
            for file in files:
                if file.endswith((".py", ".json", ".sh")):
                    filepath = os.path.join(root, file)
                    try:
                        with open(filepath, encoding="utf-8") as f:
                            matches = EMOJI_REGEX.findall(f.read())
                            for match in matches:
                                animated, name, emoji_id = match
                                full_emoji = f"<{animated}:{name}:{emoji_id}>"
                                rel_path = os.path.relpath(filepath, BASE_DIR)
                                if full_emoji not in emojis_found:
                                    emojis_found[full_emoji] = set()
                                emojis_found[full_emoji].add(rel_path)
                    except Exception:
                        pass

        if not emojis_found:
            return await msg_wait.edit(content="❌ Aucun émoji trouvé dans le code source.")

        report_lines = ["=== RAPPORT DES EMOJIS UTILISÉS DANS LE CODE ===\n"]
        for emoji, paths in sorted(emojis_found.items()):
            report_lines.append(f"{emoji} :")
            for path in sorted(paths):
                report_lines.append(f"  └─ {path}")
            report_lines.append("")

        file_buffer = io.BytesIO("\n".join(report_lines).encode("utf-8"))
        discord_file = discord.File(fp=file_buffer, filename="emojis_report.txt")

        await msg_wait.delete()
        await ctx.send(
            f"✅ **Scan terminé !** J'ai trouvé **{len(emojis_found)}** émojis uniques dans le code source.",
            file=discord_file,
        )

    @commands.command(name="replace_raw", hidden=True)
    async def replace_raw(self, ctx, old_text: str, new_text: str):
        """[CACHÉE] !replace_raw [ancien] [nouveau] : Remplacement strict de texte."""

        old_text = old_text.strip('`"').replace("[", "<").replace("]", ">")
        new_text = new_text.strip('`"').replace("[", "<").replace("]", ">")

        msg_wait = await ctx.send(f"⏳ **Remplacement strict en cours...**\nRecherche de `{old_text}` ➔ `{new_text}`")

        exclude_dirs = [".git", "__pycache__", "venv", "logs", "data"]
        occurrences, fichiers_modifies = 0, []

        for root, dirs, files in os.walk(BASE_DIR):
            dirs[:] = [d for d in dirs if not any(excl in os.path.join(root, d) for excl in exclude_dirs)]
            for file in files:
                if file.endswith((".py", ".json", ".sh")):
                    filepath = os.path.join(root, file)
                    try:
                        with open(filepath, encoding="utf-8") as f:
                            content = f.read()
                        if old_text in content:
                            count = content.count(old_text)
                            new_content = content.replace(old_text, new_text)
                            with open(filepath, "w", encoding="utf-8") as f:
                                f.write(new_content)
                            occurrences += count
                            fichiers_modifies.append(os.path.relpath(filepath, BASE_DIR))
                    except Exception:
                        pass

        if occurrences == 0:
            return await msg_wait.edit(
                content=f"⚠️ Le texte exact `{old_text}` n'a été trouvé **nulle part** dans le code."
            )

        embed = discord.Embed(
            title="♻️ Remplacement brut terminé !",
            description=f"Le code a été modifié avec succès.\n\n**Recherché :** `{old_text}`\n**Nouveau :** `{new_text}`\n**Occurrences remplacées :** {occurrences}\n**Fichiers affectés :** {len(fichiers_modifies)}",
            color=discord.Color.blue(),
        )

        liste_fichiers = "\n".join([f"`{f}`" for f in fichiers_modifies])
        if len(liste_fichiers) > 1024:
            liste_fichiers = liste_fichiers[:1000] + "\n... (liste tronquée)"
        embed.add_field(name="Fichiers modifiés", value=liste_fichiers, inline=False)
        embed.set_footer(text="⚠️ N'oublie pas de commit sur ton NAS et de relancer le bot !")

        await msg_wait.delete()
        await ctx.send(embed=embed)

    @commands.command(name="check_emojis", hidden=True)
    async def check_emojis(self, ctx):
        """[CACHÉE] Vérifie si le bot a accès à tous les émojis du code."""

        msg_wait = await ctx.send("⏳ **Scanner d'émojis en cours d'analyse...**")
        bot_emoji_ids = {str(e.id) for e in self.bot.emojis}

        try:
            app_emojis = await self.bot.fetch_application_emojis()
            for e in app_emojis:
                bot_emoji_ids.add(str(e.id))
        except Exception as e:
            print(f"Impossible de récupérer les émojis d'application : {e}")

        missing_emojis = {}
        exclude_dirs = [".git", "__pycache__", "venv", "logs", "data"]

        for root, dirs, files in os.walk(BASE_DIR):
            dirs[:] = [d for d in dirs if not any(excl in os.path.join(root, d) for excl in exclude_dirs)]
            for file in files:
                if file.endswith((".py", ".json")):
                    filepath = os.path.join(root, file)
                    try:
                        with open(filepath, encoding="utf-8") as f:
                            matches = re.compile(r"<(a?):([a-zA-Z0-9_]+):(\d+)>").findall(f.read())
                        for animated, name, emoji_id in matches:
                            if emoji_id not in bot_emoji_ids:
                                full_tag = f"<{animated}:{name}:{emoji_id}>"
                                if full_tag not in missing_emojis:
                                    missing_emojis[full_tag] = set()
                                missing_emojis[full_tag].add(file)
                    except Exception:
                        pass

        if not missing_emojis:
            return await msg_wait.edit(
                content="✅ **Parfait !** Le bot a les droits sur **TOUS** les émojis présents dans le code."
            )

        embed = discord.Embed(
            title="⚠️ Émojis inaccessibles détectés",
            description=f"Le bot ne possède pas les droits pour **{len(missing_emojis)}** émojis trouvés dans le code.",
            color=discord.Color.orange(),
        )

        count = 0
        for emoji_tag, files_set in missing_emojis.items():
            if count < 20:
                embed.add_field(
                    name=f"`{emoji_tag}`", value=f"📁 {', '.join([f'`{f}`' for f in files_set])}", inline=False
                )
                count += 1

        fichier_joint = None
        if len(missing_emojis) > 20:
            embed.set_footer(text="⚠️ Affichage limité à 20. Consultez le fichier joint pour voir la liste complète.")
            lignes_rapport = [
                "==========================================",
                f"🚨 RAPPORT DES ÉMOJIS FANTÔMES ({len(missing_emojis)} trouvés)",
                "==========================================\n",
            ]
            for tag, files_set in missing_emojis.items():
                lignes_rapport.append(f"▶ Émoji : {tag}")
                lignes_rapport.append(f"  Présent dans : {', '.join(files_set)}\n")
            buffer = io.BytesIO("\n".join(lignes_rapport).encode("utf-8"))
            fichier_joint = discord.File(fp=buffer, filename="emojis_fantomes_rapport.txt")
        else:
            embed.set_footer(text="Fin du rapport.")

        await msg_wait.delete()
        if fichier_joint:
            await ctx.send(embed=embed, file=fichier_joint)
        else:
            await ctx.send(embed=embed)

    @commands.command(name="check_bad_emojis", hidden=True)
    async def check_bad_emojis(self, ctx):
        """[CACHÉE] Traque les erreurs de syntaxe comme <<: ou >ID>."""

        msg_wait = await ctx.send("🔍 **Recherche des émojis malformés en cours...**")
        regexes = [re.compile(p) for p in [r"<<a?:[a-zA-Z0-9_]+:\d+>", r">\d+>", r"<a?:[a-zA-Z0-9_]+:\d+>>"]]
        erreurs_trouvees = {}
        exclude_dirs = [".git", "__pycache__", "venv", "logs", "data"]

        for root, dirs, files in os.walk(BASE_DIR):
            dirs[:] = [d for d in dirs if not any(excl in os.path.join(root, d) for excl in exclude_dirs)]
            for file in files:
                if file.endswith((".py", ".json")):
                    filepath = os.path.join(root, file)
                    try:
                        with open(filepath, encoding="utf-8") as f:
                            lines = f.readlines()
                        for i, line in enumerate(lines):
                            for regex in regexes:
                                matches = regex.findall(line)
                                if matches:
                                    if file not in erreurs_trouvees:
                                        erreurs_trouvees[file] = []
                                    erreurs_trouvees[file].append(f"Ligne {i + 1} : `{matches[0]}`")
                    except Exception:
                        pass

        if not erreurs_trouvees:
            return await msg_wait.edit(
                content="✅ **Code 100% propre !** Aucune balise malformée (`<<:` ou `>>` ou `>ID>`) n'a été détectée."
            )

        embed = discord.Embed(
            title="⚠️ Syntaxe d'émoji cassée",
            description="J'ai trouvé des restes de mauvais copier-coller dans ces fichiers :",
            color=discord.Color.red(),
        )
        for file, erreurs in erreurs_trouvees.items():
            valeur = "\n".join(erreurs[:10])
            if len(erreurs) > 10:
                valeur += f"\n*... et {len(erreurs) - 10} autres erreurs.*"
            embed.add_field(name=f"📁 `{file}`", value=valeur, inline=False)

        await msg_wait.delete()
        await ctx.send(embed=embed)

    # ==========================================
    # 🕵️ 7. SYSTÈME DE VIGILANCE (ANTI-CONTOURNEMENT)
    # ==========================================

    @commands.command(name="vigi_add", hidden=True)
    async def vigi_add(self, ctx, *, cible: str):
        """[CACHÉE] Ajoute un pseudo ou ID à surveiller."""

        # 💡 CORRECTION : ADMINS_DIR au lieu de ADMINS_DIR_DIR
        vigi_file = ADMINS_DIR / "vigilance.json"
        cibles = []
        if vigi_file.exists():
            with open(vigi_file, encoding="utf-8") as f:
                try:
                    cibles = json.load(f)
                    if isinstance(cibles, dict):
                        cibles = cibles.get("cibles", [])
                except:
                    cibles = []

        cible_clean = cible.lower().strip()
        if cible_clean not in cibles:
            cibles.append(cible_clean)
            with open(vigi_file, "w", encoding="utf-8") as f:
                json.dump(cibles, f, indent=2)
            await ctx.send(
                f"🕵️ **Surveillance activée.** Si quelqu'un utilise le bot avec `{cible}`, tu seras alerté en secret."
            )
        else:
            await ctx.send(f"⚠️ `{cible}` est déjà sous surveillance.")

    @commands.command(name="vigi_del", hidden=True)
    async def vigi_del(self, ctx, *, cible: str):
        """[CACHÉE] Retire un pseudo ou ID de la surveillance."""

        vigi_file = ADMINS_DIR / "vigilance.json"
        if not vigi_file.exists():
            return await ctx.send("❌ Aucun fichier de vigilance.")

        with open(vigi_file, encoding="utf-8") as f:
            cibles = json.load(f)

        cible_clean = cible.lower().strip()
        if cible_clean in cibles:
            cibles.remove(cible_clean)
            with open(vigi_file, "w", encoding="utf-8") as f:
                json.dump(cibles, f, indent=2)
            await ctx.send(f"🗑️ `{cible}` a été retiré de la liste de vigilance.")
        else:
            await ctx.send(f"❌ `{cible}` n'était pas surveillé.")

    @commands.command(name="vigi_list", hidden=True)
    async def vigi_list(self, ctx):
        """[CACHÉE] Liste les cibles surveillées."""

        vigi_file = ADMINS_DIR / "vigilance.json"
        if not vigi_file.exists():
            return await ctx.send("ℹ️ Liste vide.")

        with open(vigi_file, encoding="utf-8") as f:
            cibles = json.load(f)

        if not cibles:
            return await ctx.send("ℹ️ Liste vide.")

        embed = discord.Embed(
            title="🕵️ Cibles sous vigilance",
            description="\n".join([f"• `{c}`" for c in cibles]),
            color=discord.Color.dark_theme(),
        )
        await ctx.send(embed=embed)

    # --- LE MOTEUR D'INTERCEPTION INVISIBLE ---
    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        """Écoute silencieusement TOUTES les commandes slash pour détecter les cibles."""
        if interaction.type != discord.InteractionType.application_command:
            return

        import json

        from utils import ADMINS_DIR  # 💡 CORRECTION : Import de ADMINS_DIR

        webhook_url = os.getenv("WEBHOOK_VIGILANCE")
        if not webhook_url or not webhook_url.startswith("http"):
            return

        # 2. On charge la liste des cibles
        vigi_file = ADMINS_DIR / "vigilance.json"  # 💡 CORRECTION : ADMINS_DIR au lieu de CONFIG_DIR
        if not vigi_file.exists():
            return

        try:
            with open(vigi_file, encoding="utf-8") as f:
                cibles = json.load(f)
        except Exception:
            return

        if not cibles:
            return

        # Fonction récursive pour fouiller TOUTES les options tapées par l'utilisateur
        def extract_values(options):
            vals = []
            for opt in options:
                if "value" in opt:
                    vals.append(str(opt["value"]).lower())
                if "options" in opt:
                    vals.extend(extract_values(opt["options"]))
            return vals

        inputs = extract_values(interaction.data.get("options", []))

        # Vérification si un des mots surveillés est dans ce que l'utilisateur a tapé
        match_trouve = None
        for val in inputs:
            for cible in cibles:
                if cible in val:
                    match_trouve = cible
                    break
            if match_trouve:
                break

        if match_trouve:
            # On lance l'alerte en arrière-plan
            self.bot.loop.create_task(self._send_vigilance_alert(interaction, match_trouve, webhook_url))

    async def _send_vigilance_alert(self, interaction: discord.Interaction, match_trouve: str, webhook_url: str):
        """Envoie l'alerte au Webhook configuré."""
        import aiohttp

        cmd_name = interaction.data.get("name", "inconnue")
        timestamp_discord = int(datetime.now().timestamp())

        def format_opts(opts):
            res = []
            for o in opts:
                if "value" in o:
                    res.append(f"{o['name']}:{o['value']}")
                if "options" in o:
                    res.extend(format_opts(o["options"]))
            return res

        opts_str = " ".join(format_opts(interaction.data.get("options", [])))

        embed = discord.Embed(
            title="🚨 DÉTECTION VIGILANCE",
            description="Une cible placée sous écoute vient d'être utilisée dans une commande.",
            color=0xFCB329,
        )
        embed.add_field(
            name="👤 Exécuté par", value=f"{interaction.user.mention}\n(`{interaction.user.id}`)", inline=True
        )
        embed.add_field(
            name="🛡️ Serveur", value=f"{interaction.guild.name if interaction.guild else 'Message Privé'}", inline=True
        )
        embed.add_field(name="📅 Date & Heure", value=f"<t:{timestamp_discord}:f>", inline=True)
        embed.add_field(name="🎯 Compte surveillé", value=f"`{match_trouve}`", inline=False)
        embed.add_field(name="💻 Commande", value=f"`/{cmd_name} {opts_str}`", inline=False)

        try:
            async with aiohttp.ClientSession() as session:
                webhook = discord.Webhook.from_url(webhook_url, session=session)
                await webhook.send(embed=embed, username="GGE Videur 🕵️", avatar_url=self.bot.user.display_avatar.url)
        except Exception as e:
            logger.error(f"❌ Erreur d'envoi Webhook Vigilance : {e}")


async def setup(bot):
    await bot.add_cog(AdminCog(bot))

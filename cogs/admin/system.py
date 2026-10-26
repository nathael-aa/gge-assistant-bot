import asyncio
import io
import json
import logging
import os
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta

import discord
from discord.ext import commands

import observability as obs
from utils import (
    CONFIG_DIR,
    async_json_load,
    save_maintenance_async,
    t,
)

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

    # ===========================================
    # 🆘 MENU D'AIDE ADMINISTRATEUR
    # ===========================================
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
            "`!bypass` ➔ Active/Désactive ton immunité.\n"
            "`!export_cmds` ➔ Exporte l'arbre des commandes en JSON.\n",
            inline=False,
        )

        # Catégorie 2 : Serveurs & API
        embed.add_field(
            name="🌍 Serveurs Discord & GGE",
            value="`!bot_servers` ➔ Liste les serveurs Discord utilisant le bot.\n"
            "`!bot_leave [ID]` ➔ Force le bot à quitter un serveur Discord.\n"
            "`!sync_servers` ➔ MAJ de la liste des serveurs GGE via XML (GGE-Tracker).\n"
            "`!scan_manuel [ALL/SRV]` ➔ Lance manuellement le scanner de joueurs.\n"
            "`!broadcast_news` ➔ Envoie un message à tous les users inscrits sur le bot",
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
            "`!i18n_emojis` ➔ Vérifie et corrige les émojis entre les langues.\n"
            "`!check_emojis` ➔ Détécteur d'émojis fantômes dans le code.\n"
            "`!check_bad_emojis` ➔ Détécteur de syntaxe d'émojis cassée.\n"
            "`!export_app_emojis` ➔ Télécharge tous les émojis dans le dev bot dans le fichier assets.",
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

    @commands.command(name="reload", hidden=True)
    async def reload_cog(self, ctx, extension: str):
        """[CACHÉE] !reload [cogs.nom] : Recharge un module à chaud."""

        try:
            await self.bot.reload_extension(extension)
            await ctx.send(f"✅ Module `{extension}` rechargé avec succès !")
        except Exception as e:
            obs.record_error(source="admin", scope="reload", exception=e, cog="admin")
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

    @commands.command(name="export_cmds", hidden=True)
    async def export_commands(self, ctx):
        """[CACHÉE] !export_cmds : Exporte l'arbre des commandes en JSON."""
        msg = await ctx.send("⏳ **Génération du fichier JSON des commandes...**")

        try:
            payload = []

            def process_command(cmd, group_name=""):
                if isinstance(cmd, discord.app_commands.Group):
                    group_data = {
                        "name": f"{group_name}{cmd.name}",
                        "description": cmd.description,
                        "type": 1,
                        "options": [],
                    }
                    for sub_cmd in cmd.commands:
                        sub_data = {
                            "name": sub_cmd.name,
                            "description": sub_cmd.description,
                            "type": 1,
                            "options": self.serialize_options(sub_cmd.options) if hasattr(sub_cmd, "options") else [],
                        }
                        group_data["options"].append(sub_data)
                    payload.append(group_data)
                else:
                    cmd_data = {
                        "name": cmd.name,
                        "description": cmd.description,
                        "type": 1,
                        "options": self.serialize_options(cmd.options) if hasattr(cmd, "options") else [],
                    }
                    payload.append(cmd_data)

            # On récupère les commandes depuis l'arbre du bot
            for cmd in self.bot.tree.get_commands():
                process_command(cmd)

            # Création du fichier en mémoire pour l'envoyer directement sur Discord
            import io

            json_str = json.dumps(payload, indent=4, ensure_ascii=False)
            fichier_virtuel = io.BytesIO(json_str.encode("utf-8"))
            fichier_discord = discord.File(fp=fichier_virtuel, filename="commands.json")

            logger.info(f"📄 [JSON] Commandes exportées manuellement par {ctx.author.name}.")
            await msg.edit(content="✅ **Export réussi !** Voici l'arborescence des commandes :")
            await ctx.send(file=fichier_discord)

        except Exception as e:
            logger.error(f"❌ Erreur lors de l'export JSON : {e}")
            obs.record_error(source="admin", scope="export_commands", exception=e, cog="admin")
            await msg.edit(content=f"❌ Erreur lors de l'export : `{e}`")

    def serialize_options(self, options):
        """Transforme les options en format JSON pur."""
        serialized = []
        for opt in options:
            opt_data = {
                "name": opt.name,
                "description": opt.description,
                "type": opt.type.value,
                "required": opt.required,
            }
            if opt.autocomplete:
                opt_data["autocomplete"] = True
            if hasattr(opt, "choices") and opt.choices:
                opt_data["choices"] = [{"name": c.name, "value": c.value} for c in opt.choices]
            if hasattr(opt, "options") and opt.options:
                opt_data["options"] = self.serialize_options(opt.options)
            serialized.append(opt_data)
        return serialized

    @commands.command(name="m", hidden=True)
    async def toggle_maintenance(self, ctx):
        """[CACHÉE] !m : Bascule le bot en mode maintenance."""

        self.bot.maintenance_mode = not getattr(self.bot, "maintenance_mode", False)
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
            log_data = await asyncio.to_thread(_read_bytes_sync, file_path)

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

        etat_actuel = getattr(self.bot, "bypass_createur", True)
        nouvel_etat = not etat_actuel

        self.bot.bypass_createur = nouvel_etat

        if nouvel_etat:
            await ctx.send(
                "🔓 **Passe-partout ACTIVÉ.** Tu es de nouveau immunisé contre la maintenance, l'anti-spam et le videur."
            )
        else:
            await ctx.send(
                "🔒 **Passe-partout DÉSACTIVÉ.** Tu es maintenant un simple mortel ! Tes commandes Slash subiront les mêmes contrôles que les autres joueurs.\n*(Tape `!bypass` pour annuler).*"
            )

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

        msg_wait = await ctx.send("⏳ **Téléchargement du XML de GGE-Tracker en cours...**")
        url = "https://ggetracker.github.io/i18n/servers.xml"

        try:
            async with self.bot.session.get(url, timeout=10) as r:
                if r.status != 200:
                    await msg_wait.edit(content=f"❌ Erreur {r.status} lors de l'accès au XML de GGE-Tracker.")
                    return  # Sécurité : on return direct pour ne pas planter la suite

                xml_text = await r.text()
                root = ET.fromstring(xml_text)

                config_file = CONFIG_DIR / "servers_cache.json"
                if not config_file.exists():
                    await msg_wait.edit(content="❌ Fichier `servers_cache.json` introuvable.")
                    return

                def _read_config():
                    with open(config_file, encoding="utf-8") as f:
                        return json.load(f)

                config_data = await asyncio.to_thread(_read_config)

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
                    await msg_wait.edit(
                        content="❌ **Erreur de lecture XML** : 0 serveurs trouvés. Le format du fichier a peut-être changé."
                    )
                    return

                if nouveau_servers_info != anciennes_infos:
                    config_data["servers_info"] = nouveau_servers_info

                    config_data.pop("active_servers", None)
                    config_data.pop("special_servers", None)
                    config_data.pop("scan_minutes", None)
                    config_data.pop("servers", None)

                    def _write_config():
                        with open(config_file, "w", encoding="utf-8") as f:
                            json.dump(config_data, f, indent=4, ensure_ascii=False)

                    await asyncio.to_thread(_write_config)

                    msg = f"✅ **Synchronisation réussie !**\nLa base de données a été unifiée et mise à jour avec **{len(nouveau_servers_info)} serveurs**."
                else:
                    msg = f"✅ **Synchronisation réussie !**\nAucune modification détectée (les **{len(nouveau_servers_info)} serveurs** sont déjà parfaitement à jour)."

                await msg_wait.edit(content=msg)

        except Exception as e:
            logger.error(f"❌ [Admin] Erreur lors de la synchronisation forcée des serveurs : {e}")
            obs.record_error(source="admin", scope="sync_servers", exception=e, cog="admin")
            await msg_wait.edit(content=f"❌ Erreur lors de la synchronisation : `{e}`")

    @commands.command(name="scan_manuel", hidden=True)
    async def scan_manuel(self, ctx, cible: str = "ALL"):
        """[CACHÉE] !scan_manuel [ALL/Serveur] : Lance manuellement le scanner (Tous ou un seul)."""

        cible = cible.upper()
        scan_server = self.bot.get_cog("ScanServeursCog")

        if scan_server is None:
            return await ctx.send("❌ **Erreur :** Le module `ScanServeursCog` n'est pas chargé dans le bot.")

        if cible == "ALL":
            await ctx.send("⏳ **Lancement manuel du scanner GLOBAL...** (Tous les serveurs)")
            try:
                logger.info(f"🚀 Lancement manuel du ServerScanner GLOBAL par {ctx.author.name}")
                await scan_server.daily_scan.coro(scan_server)
                await ctx.send("✅ **Scan global terminé avec succès !**")
            except Exception as e:
                logger.error(f"❌ Erreur critique scan manuel (ALL) : {e}")
                obs.record_error(source="admin", scope="scan_manuel_all", exception=e, cog="admin")
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
                        "⚠️ **Attention :** La fonction `scan_specific_server` est introuvable dans `ScanServeursCog`."
                    )
            except Exception as e:
                logger.error(f"❌ Erreur critique scan manuel ({cible}) : {e}")
                obs.record_error(source="admin", scope="scan_manuel_specific", exception=e, cog="admin")
                await ctx.send(f"⚠️ **Erreur lors de l'exécution ({cible}) :**\n```py\n{e}\n```")

    @commands.command(name="broadcast_news")
    @commands.is_owner()
    async def broadcast_news(self, ctx: commands.Context):
        import asyncio

        lien_support = "https://discord.com/invite/zrrhxp6wDj"
        path_users = CONFIG_DIR / "users.json"

        if not path_users.exists():
            return await ctx.send("❌ Fichier users.json introuvable.")

        users_data = await async_json_load(path_users)

        total_users = len(users_data)

        # AJOUT DE LA CONFIRMATION SEC-07
        msg_confirm = await ctx.send(
            f"⚠️ **ATTENTION** : Vous allez envoyer un message privé à **{total_users} utilisateurs**.\n"
            f"Confirmez-vous cette action en cliquant sur ✅ ? (Le bot attendra 30 secondes)."
        )

        await msg_confirm.add_reaction("✅")

        def check(reaction, user):
            return user == ctx.author and str(reaction.emoji) == "✅" and reaction.message.id == msg_confirm.id

        try:
            await self.bot.wait_for("reaction_add", timeout=30.0, check=check)
        except TimeoutError:
            await msg_confirm.clear_reactions()
            return await ctx.send("❌ Délai expiré. Broadcast annulé par sécurité.")

        await msg_confirm.clear_reactions()
        await ctx.send(
            f"✅ Confirmation reçue. Démarrage de l'envoi à {total_users} utilisateurs. Temps estimé : ~{total_users * 3} secondes. Regarde la console."
        )

        succes = 0
        echecs = 0

        for user_id_str, user_info in users_data.items():
            langue = user_info.get("langue", "en")

            if langue == "fr":
                titre = "📢 Indépendance et Avenir de GGE Assistant"
                message = (
                    "Bonjour à tous ! 👋\n\n"
                    "Comme certains le savent, Goodgame Empire a récemment lancé un programme officiel \"GGE EF Creator\" permettant aux créateurs (Outils, Contenu, Wiki ou autre) d'avoir certains avantages, notamment l'accès aux prochaines mises à jour, ce qui aurait permis à GGE Assistant de préparer les nouvelles fonctionnalités avant les sorties officielles. La décision est tombée : **notre candidature a été refusée**.\n\n"
                    "La modération officielle considère que le bot enfreint les conditions d'utilisation du jeu en intégrant des liens et des données de sites communautaires tiers (comme GGE-Tracker). Pour obtenir le statut EF Creator, il aurait fallu supprimer toutes les commandes qui utilise cette API.\n\n"
                    "Les classements en direct, les radars et les historiques sont le cœur de GGE Assistant. Amputer le bot de ses capacités les plus utiles simplement pour la validation du studio de jeu va à l'encontre de la vision que j'ai pour ce projet. **J'ai donc choisi de maintenir GGE Assistant en tant que projet 100% indépendant et communautaire.**\n\n"
                    "**Qu'est-ce que cela change pour la suite ?**\n"
                    "Vos fonctionnalités actuelles restent intactes, gratuites et sans publicité. Cependant, je tiens à être transparent : entre mes obligations personnelles et l'absence de soutien officiel de l'éditeur, le développement de nouvelles fonctionnalités va considérablement ralentir par manque de temps. Je continuerai d'assurer la maintenance technique et la stabilité du bot au quotidien, mais les grandes nouveautés se feront plus rares.\n\n"
                    "💬 **Une question ou un mot de soutien ?**\n"
                    f"Rejoignez notre serveur officiel : **[Serveur Support]({lien_support})**\n\n"
                    "*Merci infiniment pour votre confiance, vos retours et votre soutien. Bon jeu à tous ! ⚔️*"
                )
            elif langue == "de":
                titre = "📢 Unabhängigkeit und Zukunft des GGE-Assistenten"
                message = (
                    "Hallo zusammen! 👋\n\n"
                    "Wie einige von euch wissen, hat Goodgame Empire kürzlich ein offizielles Programm „GGE EF Creator“ gestartet, das es Erstellern (Tools, Inhalte, Wiki usw.) ermöglicht, bestimmte Vorteile zu erhalten, insbesondere den Zugang zu kommenden Updates. Dies hätte es dem GGE-Assistenten ermöglicht, neue Funktionen vor den offiziellen Veröffentlichungen vorzubereiten. Die Entscheidung ist gefallen: **Unsere Bewerbung wurde abgelehnt**.\n\n"
                    "Die offizielle Moderation ist der Ansicht, dass der Bot gegen die Nutzungsbedingungen des Spiels verstößt, indem er Links und Daten von externen Community-Seiten (wie GGE-Tracker) integriert. Um den EF Creator-Status zu erhalten, hätten wir alle Befehle löschen müssen, die diese API verwenden.\n\n"
                    "Live-Rankings, Radare und Historien sind das Herzstück des GGE-Assistenten. Den Bot seiner nützlichsten Fähigkeiten zu berauben, nur um die Bestätigung des Spielestudios zu erhalten, widerspricht meiner Vision für dieses Projekt. **Ich habe mich daher entschieden, den GGE-Assistenten als 100% unabhängiges und Community-basiertes Projekt beizubehalten.**\n\n"
                    "**Was ändert sich für die Zukunft?**\n"
                    "Eure aktuellen Funktionen bleiben intakt, kostenlos und werbefrei. Ich möchte jedoch transparent sein: Zwischen meinen persönlichen Verpflichtungen und der fehlenden offiziellen Unterstützung durch den Herausgeber wird sich die Entwicklung neuer Funktionen aus Zeitmangel deutlich verlangsamen. Ich werde weiterhin die tägliche technische Wartung und Stabilität des Bots sicherstellen, aber große Neuerungen werden seltener.\n\n"
                    "💬 **Fragen oder Feedback?**\n"
                    f"Treten Sie unserem offiziellen Server bei: **[Support-Server]({lien_support})**\n\n"
                    "*Vielen Dank für euer Vertrauen, euer Feedback und eure Unterstützung. Viel Spaß beim Spielen! ⚔️*"
                )
            else:
                titre = "📢 Independence and Future of GGE Assistant"
                message = (
                    "Hello everyone! 👋\n\n"
                    'As some of you may know, Goodgame Empire recently launched an official "GGE EF Creator" program allowing creators (Tools, Content, Wiki, etc.) to have certain benefits, notably early access to upcoming updates, which would have allowed GGE Assistant to prepare new features before official releases. The decision has been made: **our application was rejected**.\n\n'
                    "Official moderation considers that the bot violates the game's terms of service by integrating links and data from third-party community sites (like GGE-Tracker). To obtain the EF Creator status, we would have had to delete all commands that use this API.\n\n"
                    "Live rankings, radars, and histories are the very heart of GGE Assistant. Crippling the bot of its most useful capabilities simply for the game studio's validation goes against my vision for this project. **I have therefore chosen to maintain GGE Assistant as a 100% independent and community-driven project.**\n\n"
                    "**What does this change moving forward?**\n"
                    "Your current features remain intact, free, and ad-free. However, I want to be transparent: between my personal obligations and the lack of official support from the publisher, the development of new features will significantly slow down due to a lack of time. I will continue to ensure the technical maintenance and daily stability of the bot, but major new features will become rarer.\n\n"
                    "💬 **Questions or a word of support?**\n"
                    f"Join our official server: **[Support Server]({lien_support})**\n\n"
                    "*Thank you so much for your trust, your feedback, and your support. Happy gaming to all! ⚔️*"
                )

            embed = discord.Embed(title=titre, description=message, color=0xC1121F)

            try:
                user_id = int(user_id_str)
                user = self.bot.get_user(user_id) or await self.bot.fetch_user(user_id)
                await user.send(embed=embed)
                succes += 1
                print(f"✅ MP envoyé à {user.name} ({langue})")
            except discord.Forbidden:
                echecs += 1
                print(f"❌ MP bloqué par l'utilisateur {user_id_str}")
            except Exception as e:
                echecs += 1
                print(f"⚠️ Erreur inattendue pour {user_id_str} : {e}")

            # Pause pour éviter le Rate Limit
            await asyncio.sleep(3.0)

        await ctx.send(f"✅ **Broadcast terminé !**\nSuccès : `{succes}` | Échecs (MP fermés) : `{echecs}`")

import io
import logging
import os
import re
from datetime import datetime, timedelta

import discord
from discord.ext import commands

from utils import (
    BASE_DIR,
    LOCALES_DIR,
    MON_ID_DISCORD,
    charger_langues,
    load_blocks_async,
    save_blocks_async,
    save_maintenance_async,
    setup_embed_footer,
    t,
)

logger = logging.getLogger("GGE_Bot")

EMOJI_REGEX = re.compile(r"<(a?):([a-zA-Z0-9_]+):([0-9]+)>")


class AdminCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.admin_lang = "fr"

    # ==========================================
    # 🆘 MENU D'AIDE ADMINISTRATEUR
    # ==========================================
    @commands.command(name="ahelp", aliases=["admin_help", "adminhelp"], hidden=True)
    async def admin_help(self, ctx):
        """[CACHÉE] !ahelp : Affiche le récapitulatif des commandes admin."""
        if ctx.author.id != MON_ID_DISCORD:
            return

        embed = discord.Embed(
            title="🛠️ Panneau de Contrôle Administrateur",
            description="Voici la liste de tes commandes secrètes (utilisables avec le préfixe `!`).",
            color=discord.Color.dark_red(),
        )

        # Catégorie Système & Scripts
        embed.add_field(
            name="⚙️ Système & Scripts",
            value="`!sync` ➔ Synchronise les commandes Slash.\n"
            "`!m` ➔ Active/Désactive le mode maintenance.\n"
            "`!restart` ➔ Redémarre le bot (via Docker).\n"
            "`!reload [cogs.nom]` ➔ Recharge un module à chaud.\n"
            "`!setstatus [msg]` ➔ Ajoute/retire un statut personnalisé.\n"
            "`!scan_manuel` ➔ Lance `auto_pa_daily.sh` en fond.\n"
            "`!log [date]` ➔ Télécharge les logs.",
            inline=False,
        )

        # Catégorie Sécurité (Videur)
        embed.add_field(
            name="⛔ Videur & Modération",
            value="`!ban_cmd [cmd] [raison]` ➔ Désactive une commande.\n"
            "`!unban_cmd [cmd]` ➔ Réactive une commande.\n"
            "`!ban_user [@user] [cmd ou ALL]` ➔ Restreint un utilisateur.\n"
            "`!unban_user [@user] [cmd ou ALL]` ➔ Lève la restriction.",
            inline=False,
        )

        # Catégorie Gestion des Serveurs
        embed.add_field(
            name="🤖 Serveurs Discord",
            value="`!bot_servers` ➔ Liste les serveurs utilisant le bot.\n"
            "`!bot_leave [ID]` ➔ Force le bot à quitter un serveur.",
            inline=False,
        )

        # Catégorie Traductions
        embed.add_field(
            name="🌐 Traductions (i18n)",
            value="`!i18n_sync` ➔ Scanne le code et met à jour les `.json`.\n"
            "`!i18n_export [lang]` ➔ Génère un `.csv` pour les traducteurs.",
            inline=False,
        )

        # Catégorie Code & Emojis
        embed.add_field(
            name="🛠️ Code & Emojis",
            value="`!emojis_list` ➔ Liste tous les émojis utilisés dans le code.\n"
            "`!replace_raw [old] [new]` ➔ Remplace un émoji dans tout le code.\n"
            "`!check_emojis` ➔ Détécteur d'émojis fantôme dans le code.\n"
            "`!check_bad_emojis` ➔ Détécteur d'émojis éronnés dans le code.",
            inline=False,
        )

        embed.set_footer(text="Ces commandes sont invisibles pour les utilisateurs normaux.")
        await ctx.send(embed=embed)

    # ==========================================
    # 🔄 SYNCHRONISATION DES COMMANDES
    # ==========================================
    @commands.command(name="sync", hidden=True)
    async def sync_tree(self, ctx):
        """[CACHÉE] !sync : Synchronise l'arbre des commandes Slash."""
        if ctx.author.id != MON_ID_DISCORD:
            return

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

    # ==========================================
    # 🛑 GESTION DU MODE MAINTENANCE
    # ==========================================
    @commands.command(name="m", hidden=True)
    async def toggle_maintenance(self, ctx):
        """[CACHÉE] !m : Bascule le bot en mode maintenance."""
        if ctx.author.id != MON_ID_DISCORD:
            return

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

            statut_maint = t("fr", "bot_activity_maintenance", defaut="🚧 EN MAINTENANCE 🚧")
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

    # ==========================================
    # 🔄 REDÉMARRAGE DU BOT (DOCKER)
    # ==========================================
    @commands.command(name="restart", aliases=["reboot"], hidden=True)
    async def restart_bot(self, ctx):
        """[CACHÉE] !restart : Redémarre le bot (Nécessite Docker 'restart: always')."""
        if ctx.author.id != MON_ID_DISCORD:
            return

        msg = t(
            self.admin_lang,
            "admin_restart",
            defaut="🔄 **Redémarrage en cours...** Le bot se déconnecte et devrait revenir dans quelques secondes.",
        )
        await ctx.send(msg)

        logger.warning(f"🔄 Redémarrage forcé déclenché par l'administrateur ({ctx.author.name})")

        await self.bot.close()

    # ==========================================
    # 🔄 RELOAD D'UN COG
    # ==========================================
    @commands.command(name="reload", hidden=True)
    async def reload_cog(self, ctx, extension: str):
        """[CACHÉE] !reload [cogs.nom] : Recharge un module à chaud."""
        if ctx.author.id != MON_ID_DISCORD:
            return

        try:
            await self.bot.reload_extension(extension)
            await ctx.send(f"✅ Module `{extension}` rechargé avec succès !")
        except Exception as e:
            await ctx.send(f"❌ Erreur lors du rechargement :\n```py\n{e}\n```")

    # ==========================================
    # ⛔ VIDEUR : ZONE DE BLOCAGE DES COMMANDES
    # ==========================================
    @commands.command(name="ban_cmd", hidden=True)
    async def ban_cmd(self, ctx, command: str, *, reason: str = "Maintenance."):
        """[CACHÉE] !ban_cmd [command] [reason] : Bloque une commande globalement."""
        if ctx.author.id != MON_ID_DISCORD:
            return
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
        if ctx.author.id != MON_ID_DISCORD:
            return
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

    # ==========================================
    # 👤 VIDEUR : ZONE DE SANCTION DES JOUEURS
    # ==========================================
    @commands.command(name="ban_user", hidden=True)
    async def ban_user(self, ctx, user: discord.User, command: str, *, reason: str = "Abus."):
        """[CACHÉE] !ban_user [user] [command] [reason] : Restreint un utilisateur."""
        if ctx.author.id != MON_ID_DISCORD:
            return
        data = await load_blocks_async()
        uid = str(user.id)
        cmd_clean = command.replace("/", "").strip()

        if "blocked_users" not in data:
            data["blocked_users"] = {}

        if uid not in data["blocked_users"]:
            data["blocked_users"][uid] = {}

        data["blocked_users"][uid][cmd_clean] = reason
        await save_blocks_async(data)

        scope = "Toutes les commandes" if cmd_clean == "ALL" else f"La commande `/{cmd_clean}`"
        msg = t(
            self.admin_lang,
            "admin_ban_user",
            user=user.name,
            uid=uid,
            scope=scope,
            reason=reason,
            defaut=f"🛑 **Videur** : {user.name} (`{uid}`) a été restreint.\n*Cible : {scope}*\n*Raison : {reason}*",
        )
        await ctx.send(msg)

    @commands.command(name="unban_user", hidden=True)
    async def unban_user(self, ctx, user: discord.User, command: str):
        """[CACHÉE] !unban_user [user] [command] : Lève la restriction d'un utilisateur."""
        if ctx.author.id != MON_ID_DISCORD:
            return
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
    # 📊 SUIVI ET CONTRÔLE DES SERVEURS
    # ==========================================
    @commands.command(name="bot_servers", hidden=True)
    async def admin_serveurs(self, ctx):
        """[CACHÉE] !bot_servers : Liste les serveurs Discord sur lesquels le bot est présent."""
        if ctx.author.id != MON_ID_DISCORD:
            return

        serveurs = self.bot.guilds
        nb_serveurs = len(serveurs)

        serveurs_tries = sorted(
            serveurs, key=lambda g: g.me.joined_at if g.me and g.me.joined_at else datetime.min, reverse=True
        )

        liste_serveurs = []
        for g in serveurs_tries:
            if g.me and g.me.joined_at:
                ts = int(g.me.joined_at.timestamp())
                date_str = f"<t:{ts}:d>"
            else:
                date_str = "Inconnue"

            proprio = f"{g.owner.name}" if g.owner else "Inconnu"

            ligne = f"• **{g.name}** (`{g.id}`) | 👑 {proprio} | 👥 {g.member_count} | 📥 {date_str}"
            liste_serveurs.append(ligne)

        texte_serveurs = "\n".join(liste_serveurs)

        trunc_msg = t(self.admin_lang, "admin_servers_trunc", defaut="\n\n... *(Liste tronquée)*")
        if len(texte_serveurs) > 1900:
            texte_serveurs = texte_serveurs[:1850] + trunc_msg

        embed_title = t(self.admin_lang, "admin_servers_title", defaut="🤖 Serveurs connectés")
        embed_desc = t(
            self.admin_lang,
            "admin_servers_desc",
            count=nb_serveurs,
            liste=texte_serveurs,
            defaut=f"Le bot est actuellement présent sur **{nb_serveurs} serveurs** :\n\n{texte_serveurs}",
        )

        embed = discord.Embed(title=embed_title, description=embed_desc, color=discord.Color.blue())
        await setup_embed_footer(embed, ctx, self.admin_lang)
        await ctx.send(embed=embed)

    @commands.command(name="bot_leave", hidden=True)
    async def admin_quitter(self, ctx, server_id: str):
        """[CACHÉE] !bot_leave [server_id] : Force le bot à quitter un serveur Discord."""
        if ctx.author.id != MON_ID_DISCORD:
            return
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

    # ==========================================
    # 🚀 DÉCLENCHEMENT MANUEL DES SCANNERS
    # ==========================================
    @commands.command(name="scan_manuel", hidden=True)
    async def scan_manuel(self, ctx):
        """[CACHÉE] !scan_manuel : Lance manuellement les scanners."""
        if ctx.author.id != MON_ID_DISCORD:
            return

        await ctx.send("⏳ **Lancement manuel des scanners...** (Scan Serveur asynchrone)")

        try:
            logger.info(f"🚀 Lancement manuel du ServerScanner par {ctx.author.name}")

            scan_server = self.bot.get_cog("ScanCog")

            if scan_server is None:
                return await ctx.send("❌ **Erreur :** Le module `ScanCog` n'est pas chargé dans le bot.")

            await scan_server.daily_scan.coro(scan_server)

            await ctx.send("✅ **Tous les scans manuels sont terminés avec succès !**")

        except Exception as e:
            logger.error(f"❌ Erreur critique scan manuel : {e}")
            await ctx.send(f"⚠️ **Erreur lors de l'exécution :**\n```py\n{e}\n```")

    # ==========================================
    # 📜 EXTRACTION DU JOURNAL SYSTÈME (LOGS)
    # ==========================================
    @commands.command(name="log", hidden=True)
    async def get_log(self, ctx, requested_date: str = "today"):
        """[CACHÉE] !log [date] : Récupère les logs système pour une date spécifique."""
        if ctx.author.id != MON_ID_DISCORD:
            return

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

    # ==========================================
    # Statut depuis discord
    # ==========================================
    @commands.command(name="setstatus")
    async def setstatus(self, ctx, *, message: str = None):
        """[Admin] Ajoute ou retire un message de statut personnalisé."""
        if ctx.author.id != MON_ID_DISCORD:
            return

        self.bot.custom_status = message

        if message:
            await ctx.send(f"✅ Message ajouté à la rotation des statuts :\n> `{message}`")
        else:
            await ctx.send("🗑️ Le message personnalisé a été retiré de la rotation.")

    # ==========================================
    # 🧹 VUE DE CONFIRMATION (SÉCURITÉ I18N)
    # ==========================================
    class I18nConfirmView(discord.ui.View):
        def __init__(self, ctx):
            super().__init__(timeout=120)
            self.ctx = ctx
            self.delete = False
            self.responded = False

        @discord.ui.button(label="🗑️ Supprimer les clés", style=discord.ButtonStyle.danger)
        async def btn_delete(self, interaction: discord.Interaction, button: discord.ui.Button):
            if interaction.user.id != self.ctx.author.id:
                return
            self.delete = True
            self.responded = True
            for item in self.children:
                item.disabled = True
            await interaction.response.edit_message(view=self)
            self.stop()

        @discord.ui.button(label="💾 Conserver (Trier & Ajouter)", style=discord.ButtonStyle.success)
        async def btn_keep(self, interaction: discord.Interaction, button: discord.ui.Button):
            if interaction.user.id != self.ctx.author.id:
                return
            self.delete = False
            self.responded = True
            for item in self.children:
                item.disabled = True
            await interaction.response.edit_message(view=self)
            self.stop()

    # ==========================================
    # 🧹 SYNCHRONISATION, TRI ET SÉCURITÉ I18N
    # ==========================================
    @commands.command(name="i18n_sync", aliases=["sortlang", "sort_locales"], hidden=True)
    @commands.is_owner()
    async def i18n_sync(self, ctx):
        """[CACHÉE] !i18n_sync : Scanne, Trie et Synchronise les fichiers JSON avec sécurité."""
        import json
        import re

        from utils import BASE_DIR, LOCALES_DIR

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
            f"🟢 *Les langues ont été rechargées et triées alphabétiquement.* \n"
            f"*(Cherche '[TODO]' dans tes fichiers JSON pour voir ce qu'il faut traduire !)*"
        )

        embed_success = discord.Embed(
            title="✨ Synchronisation Terminée", description=desc, color=discord.Color.green()
        )

        if isinstance(msg, discord.Message):
            await msg.edit(content=None, embed=embed_success, view=None)
        else:
            await ctx.send(embed=embed_success)

    # ==========================================
    # 📤 EXPORTATION POUR LES TRADUCTEURS (CSV)
    # ==========================================
    @commands.command(name="i18n_export", hidden=True)
    @commands.is_owner()
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
    # 🔎 LISTE DES EMOJIS DU CODE
    # ==========================================
    @commands.command(name="emojis_list", hidden=True)
    async def emojis_list(self, ctx):
        """[CACHÉE] !emojis_list : Scan le code pour trouver tous les émojis utilisés."""
        if ctx.author.id != MON_ID_DISCORD:
            return

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
                            content = f.read()
                            matches = EMOJI_REGEX.findall(content)
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

        file_content = "\n".join(report_lines)
        file_buffer = io.BytesIO(file_content.encode("utf-8"))
        discord_file = discord.File(fp=file_buffer, filename="emojis_report.txt")

        await msg_wait.delete()
        await ctx.send(
            f"✅ **Scan terminé !** J'ai trouvé **{len(emojis_found)}** émojis uniques dans le code source.",
            file=discord_file,
        )

    # ==========================================
    # ♻️ CHERCHER / REMPLACER STRICT (PARADE ULTIME ANTI-DISCORD)
    # ==========================================
    @commands.command(name="replace_raw", hidden=True)
    async def replace_raw(self, ctx, old_text: str, new_text: str):
        """[CACHÉE] !replace_raw [ancien] [nouveau] : Remplacement strict de texte."""
        if ctx.author.id != MON_ID_DISCORD:
            return

        old_text = old_text.strip('`"')
        new_text = new_text.strip('`"')

        old_text = old_text.replace("[", "<").replace("]", ">")
        new_text = new_text.replace("[", "<").replace("]", ">")

        msg_wait = await ctx.send(f"⏳ **Remplacement strict en cours...**\nRecherche de `{old_text}` ➔ `{new_text}`")

        exclude_dirs = [".git", "__pycache__", "venv", "logs", "data"]
        occurrences = 0
        fichiers_modifies = []

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
            description=f"Le code a été modifié avec succès.\n\n"
            f"**Recherché :** `{old_text}`\n"
            f"**Nouveau :** `{new_text}`\n"
            f"**Occurrences remplacées :** {occurrences}\n"
            f"**Fichiers affectés :** {len(fichiers_modifies)}",
            color=discord.Color.blue(),
        )

        liste_fichiers = "\n".join([f"`{f}`" for f in fichiers_modifies])
        if len(liste_fichiers) > 1024:
            liste_fichiers = liste_fichiers[:1000] + "\n... (liste tronquée)"

        embed.add_field(name="Fichiers modifiés", value=liste_fichiers, inline=False)
        embed.set_footer(text="⚠️ N'oublie pas de commit sur ton NAS et de relancer le bot !")

        await msg_wait.delete()
        await ctx.send(embed=embed)

    # ==========================================
    # 🔍 DÉTECTEUR D'ÉMOJIS FANTÔMES (AVEC EXPORT .TXT)
    # ==========================================
    @commands.command(name="check_emojis", hidden=True)
    async def check_emojis(self, ctx):
        """[CACHÉE] Vérifie si le bot a accès à tous les émojis du code."""
        if ctx.author.id != MON_ID_DISCORD:
            return

        msg_wait = await ctx.send("⏳ **Scanner d'émojis en cours d'analyse...**")

        bot_emoji_ids = {str(e.id) for e in self.bot.emojis}

        try:
            app_emojis = await self.bot.fetch_application_emojis()
            for e in app_emojis:
                bot_emoji_ids.add(str(e.id))
        except Exception as e:
            print(f"Impossible de récupérer les émojis d'application : {e}")

        emoji_regex = re.compile(r"<(a?):([a-zA-Z0-9_]+):(\d+)>")

        missing_emojis = {}
        exclude_dirs = [".git", "__pycache__", "venv", "logs", "data"]

        for root, dirs, files in os.walk(BASE_DIR):
            dirs[:] = [d for d in dirs if not any(excl in os.path.join(root, d) for excl in exclude_dirs)]

            for file in files:
                if file.endswith((".py", ".json")):
                    filepath = os.path.join(root, file)
                    try:
                        with open(filepath, encoding="utf-8") as f:
                            content = f.read()

                        matches = emoji_regex.findall(content)
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
                files_list = ", ".join([f"`{f}`" for f in files_set])
                embed.add_field(name=f"`{emoji_tag}`", value=f"📁 {files_list}", inline=False)
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

            contenu_complet = "\n".join(lignes_rapport)

            buffer = io.BytesIO(contenu_complet.encode("utf-8"))
            fichier_joint = discord.File(fp=buffer, filename="emojis_fantomes_rapport.txt")
        else:
            embed.set_footer(text="Fin du rapport.")

        await msg_wait.delete()
        if fichier_joint:
            await ctx.send(embed=embed, file=fichier_joint)
        else:
            await ctx.send(embed=embed)

    # ==========================================
    # 🔍 DÉTECTEUR D'ERREURS DE SYNTAXE (ÉMOJIS)
    # ==========================================
    @commands.command(name="check_bad_emojis", hidden=True)
    async def check_bad_emojis(self, ctx):
        """[CACHÉE] Traque les erreurs de syntaxe comme <<: ou >ID>."""
        if ctx.author.id != MON_ID_DISCORD:
            return

        msg_wait = await ctx.send("🔍 **Recherche des émojis malformés en cours...**")

        bad_patterns = [r"<<a?:[a-zA-Z0-9_]+:\d+>", r">\d+>", r"<a?:[a-zA-Z0-9_]+:\d+>>"]
        regexes = [re.compile(p) for p in bad_patterns]

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


async def setup(bot):
    await bot.add_cog(AdminCog(bot))

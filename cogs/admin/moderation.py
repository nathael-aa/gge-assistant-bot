import logging
import os
from datetime import datetime

import discord
from discord.ext import commands

import observability as obs
from utils import (
    ADMINS_DIR,
    CONFIG_DIR,
    JOUEURS_DIR,
    async_json_dump,
    async_json_load,
    load_blocks_async,
    save_blocks_async,
    t,
)

logger = logging.getLogger("GGE_Bot")


class AdminModeration(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.admin_lang = getattr(bot, "admin_lang", "fr")

    @commands.command(name="ban_cmd", hidden=True)
    async def ban_cmd(self, ctx, command: str, *, reason: str = "🚧 Maintenance."):
        """[CACHÉE] !ban_cmd [command] [reason] : Bloque une commande globalement."""

        # 1. Nettoyer le nom tapé par l'admin (enlève le / et les espaces)
        cmd_clean = command.replace("/", "").strip()

        # 2. Récupérer TOUTES les commandes et groupes enregistrés dans le bot (ex: "rank", "rank contests")
        valid_commands = set()
        for cmd in self.bot.tree.walk_commands():
            valid_commands.add(cmd.qualified_name)
            if getattr(cmd, "parent", None):
                valid_commands.add(cmd.parent.qualified_name)

        # 3. Vérifier si la commande existe
        if cmd_clean not in valid_commands:
            msg = t(
                self.admin_lang,
                "admin_ban_cmd_not_found",
                cmd=cmd_clean,
                defaut=f"❌ **Erreur** : La commande ou le groupe `/{cmd_clean}` n'existe pas. Vérifiez l'orthographe.",
            )
            return await ctx.send(msg)

        data = await load_blocks_async()
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
                data = await async_json_load(chemin)

                a_ete_modifie = purger_donnees(data)

                if a_ete_modifie:
                    await async_json_dump(chemin, data, indent=2, ensure_ascii=False)
                    fichiers_purges.append(chemin.name)
            except Exception as e:
                logger.error(f"❌ Erreur lors de la purge de {chemin.name} : {e}")

        if fichiers_purges:
            logger.info(f"🧹 [Videur] Purge complète pour {uid}. Fichiers modifiés : {', '.join(fichiers_purges)}")

        return fichiers_purges

    @commands.command(name="ban_user", hidden=True)
    async def ban_user(self, ctx, user: discord.User, command: str, *, reason: str = "⚠️ Abuse."):
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

    @commands.command(name="vigi_add", hidden=True)
    async def vigi_add(self, ctx, *, cible: str):
        """[CACHÉE] Ajoute un pseudo ou ID à surveiller."""

        vigi_file = ADMINS_DIR / "vigilance.json"
        cibles = []
        if vigi_file.exists():
            try:
                cibles = await async_json_load(vigi_file)
                if isinstance(cibles, dict):
                    cibles = cibles.get("cibles", [])
            except Exception:
                cibles = []

        cible_clean = cible.lower().strip()
        if cible_clean not in cibles:
            cibles.append(cible_clean)
            await async_json_dump(vigi_file, cibles, indent=2, ensure_ascii=True)

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

        cibles = await async_json_load(vigi_file)

        cible_clean = cible.lower().strip()
        if cible_clean in cibles:
            cibles.remove(cible_clean)
            await async_json_dump(vigi_file, cibles, indent=2, ensure_ascii=True)

            await ctx.send(f"🗑️ `{cible}` a été retiré de la liste de vigilance.")
        else:
            await ctx.send(f"❌ `{cible}` n'était pas surveillé.")

    @commands.command(name="vigi_list", hidden=True)
    async def vigi_list(self, ctx):
        """[CACHÉE] Liste les cibles surveillées."""

        vigi_file = ADMINS_DIR / "vigilance.json"
        if not vigi_file.exists():
            return await ctx.send("ℹ️ Liste vide.")

        cibles = await async_json_load(vigi_file)

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

        from utils import ADMINS_DIR

        webhook_url = os.getenv("WEBHOOK_VIGILANCE")
        if not webhook_url or not webhook_url.startswith("http"):
            return

        # 2. On charge la liste des cibles
        vigi_file = ADMINS_DIR / "vigilance.json"
        if not vigi_file.exists():
            return

        try:
            cibles = await async_json_load(vigi_file)
        except Exception as e:
            obs.record_error(source="background", scope="vigilance_load", exception=e, cog="admin")
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
            obs.record_error(source="event", scope="vigilance_webhook", exception=e, cog="admin")

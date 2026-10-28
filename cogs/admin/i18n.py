import asyncio
import io
import logging
import os
import re

import discord
from discord.ext import commands

from utils import BASE_DIR, DICT_EMOJIS, LOCALES_DIR, async_json_dump, async_json_load, charger_langues

logger = logging.getLogger("GGE_Bot")
EMOJI_REGEX = re.compile(r"<(a?):([a-zA-Z0-9_]+):([0-9]+)>")


class I18nConfirmView(discord.ui.View):
    def __init__(self, ctx):
        super().__init__(timeout=120)
        self.ctx = ctx
        self.delete = False
        self.responded = False
        self.message = None

    @discord.ui.button(
        label="Supprimer les clés", style=discord.ButtonStyle.danger, emoji=DICT_EMOJIS.get("e_trash", "🗑️")
    )
    async def btn_delete(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.delete = True
        self.responded = True
        for item in self.children:
            item.disabled = True
        try:
            await interaction.response.edit_message(view=self)
        except Exception:
            pass
        self.stop()

    @discord.ui.button(
        label="Conserver (Trier & Ajouter)",
        style=discord.ButtonStyle.success,
        emoji=DICT_EMOJIS.get("e_save", "💾"),
    )
    async def btn_keep(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.delete = False
        self.responded = True
        for item in self.children:
            item.disabled = True
        try:
            await interaction.response.edit_message(view=self)
        except Exception:
            pass
        self.stop()

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


class AdminI18n(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="i18n_sync", aliases=["sortlang", "sort_locales"], hidden=True)
    async def i18n_sync(self, ctx):
        """[CACHÉE] !i18n_sync : Scanne, Trie et Synchronise les fichiers JSON avec sécurité."""
        pattern = re.compile(r'\bt\s*\(\s*[^,]+,\s*["\']([a-zA-Z0-9_]+)["\']')

        def _scan_used_keys():
            trouvees = set()
            for root, dirs, files in os.walk(BASE_DIR):
                if ".git" in root or "__pycache__" in root:
                    continue
                for file in files:
                    if file.endswith(".py"):
                        with open(os.path.join(root, file), encoding="utf-8") as f:
                            trouvees.update(pattern.findall(f.read()))
            return trouvees

        try:
            cles_utilisees = await asyncio.to_thread(_scan_used_keys)
        except Exception as e:
            logger.error(f"❌ [AdminI18n] Erreur lors du scan des clés i18n : {e}")
            return await ctx.send("❌ Erreur lors du scan des fichiers.")

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
        try:
            for fichier in LOCALES_DIR.glob("*.json"):
                locales_data[fichier.stem] = await async_json_load(fichier)
        except Exception as e:
            logger.error(f"❌ [AdminI18n] Erreur de lecture des fichiers JSON : {e}")
            return await ctx.send("❌ Erreur de chargement des JSON.")

        fr_data = locales_data.get("fr", {})
        cles_existantes_fr = set(fr_data.keys())

        manquantes_fr = cles_utilisees - cles_existantes_fr
        inutiles_fr = cles_existantes_fr - cles_utilisees

        supprimer_cles = False
        msg = None

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
            view = I18nConfirmView(ctx)
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

        try:
            for lang, data in locales_data.items():
                fichier = LOCALES_DIR / f"{lang}.json"
                await async_json_dump(fichier, data, indent=4, ensure_ascii=False)
        except Exception as e:
            logger.error(f"❌ [AdminI18n] Erreur écriture JSON i18n_sync : {e}")
            return await ctx.send("❌ Échec de la sauvegarde des modifications.")

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
            try:
                await msg.edit(content=None, embed=embed_success, view=None)
            except Exception:
                pass
        else:
            await ctx.send(embed=embed_success)

    @commands.command(name="i18n_export", hidden=True)
    async def i18n_export(self, ctx, langue_cible: str = "en"):
        """[CACHÉE] !i18n_export [langue] : Génère un CSV pour les traducteurs communautaires."""
        import csv
        import re

        fr_file = LOCALES_DIR / "fr.json"
        cible_file = LOCALES_DIR / f"{langue_cible}.json"

        if not fr_file.exists() or not cible_file.exists():
            return await ctx.send(f"❌ Impossible de trouver `fr.json` ou `{langue_cible}.json`.")

        try:
            fr_data = await async_json_load(fr_file)
            cible_data = await async_json_load(cible_file)
        except Exception as e:
            logger.error(f"❌ [AdminI18n] Erreur lecture pour i18n_export : {e}")
            return await ctx.send("❌ Erreur de chargement des fichiers de langue.")

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
            description=(
                "Envoie ce fichier `.csv` à tes traducteurs.\n"
                "Ils peuvent l'ouvrir avec **Excel** ou **Google Sheets**.\n\n"
                "⚠️ Dis-leur bien de :\n"
                "1. Ne pas modifier la colonne 1.\n"
                "2. Ne pas effacer les variables (colonne 4)."
            ),
            color=discord.Color.blue(),
        )
        await ctx.send(embed=embed, file=discord_file)

    @commands.command(name="emojis_list", hidden=True)
    async def emojis_list(self, ctx):
        """[CACHÉE] !emojis_list : Scan le code pour trouver tous les émojis utilisés."""

        msg_wait = await ctx.send("⏳ **Scan du code en cours...**")
        exclude_dirs = [".git", "__pycache__", "venv", "logs", "data"]

        def _scan_emojis():
            found = {}
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
                                    if full_emoji not in found:
                                        found[full_emoji] = set()
                                    found[full_emoji].add(rel_path)
                        except Exception:
                            pass
            return found

        try:
            emojis_found = await asyncio.to_thread(_scan_emojis)
        except Exception as e:
            logger.error(f"❌ [AdminI18n] Erreur scan des émojis : {e}")
            return await msg_wait.edit(content="❌ Erreur pendant le scan des emojis.")

        if not emojis_found:
            return await msg_wait.edit(
                content="ℹ️ Aucun émoji `<:name:id>` détecté dans le code (Ce qui est normal si tout a été centralisé !)."
            )

        report_lines = ["=== RAPPORT DES EMOJIS UTILISÉS DANS LE CODE ===\n"]
        for emoji, paths in sorted(emojis_found.items()):
            report_lines.append(f"{emoji} :")
            for path in sorted(paths):
                report_lines.append(f"  └─ {path}")
            report_lines.append("")

        file_buffer = io.BytesIO("\n".join(report_lines).encode("utf-8"))
        discord_file = discord.File(fp=file_buffer, filename="emojis_report.txt")

        try:
            await msg_wait.delete()
        except Exception:
            pass
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

        def _replace_in_files():
            total, modifies = 0, []
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
                                total += count
                                modifies.append(os.path.relpath(filepath, BASE_DIR))
                        except Exception:
                            pass
            return total, modifies

        try:
            occurrences, fichiers_modifies = await asyncio.to_thread(_replace_in_files)
        except Exception as e:
            logger.error(f"❌ [AdminI18n] Erreur lors du remplacement brut : {e}")
            return await msg_wait.edit(content="❌ Erreur lors du remplacement de texte.")

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

        try:
            await msg_wait.delete()
        except Exception:
            pass
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
            logger.warning(f"⚠️ [AdminI18n] Impossible de récupérer les émojis d'application : {e}")

        exclude_dirs = [".git", "__pycache__", "venv", "logs", "data"]

        def _scan_missing_emojis():
            missing = {}
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
                                    if full_tag not in missing:
                                        missing[full_tag] = set()
                                    missing[full_tag].add(file)
                        except Exception:
                            pass
            return missing

        try:
            missing_emojis = await asyncio.to_thread(_scan_missing_emojis)
        except Exception as e:
            logger.error(f"❌ [AdminI18n] Erreur scan_missing_emojis : {e}")
            return await msg_wait.edit(content="❌ Erreur lors de l'analyse des émojis manquants.")

        if not missing_emojis:
            return await msg_wait.edit(
                content="✅ **Parfait !** Le bot a les droits sur **TOUS** les émojis restants dans le code."
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

        try:
            await msg_wait.delete()
        except Exception:
            pass
        if fichier_joint:
            await ctx.send(embed=embed, file=fichier_joint)
        else:
            await ctx.send(embed=embed)

    @commands.command(name="i18n_emojis", hidden=True)
    async def i18n_emojis(self, ctx):
        """[CACHÉE] !i18n_emojis : Vérifie et corrige les émojis dans les traductions."""
        import re

        msg_wait = await ctx.send("⏳ **Analyse de la cohérence des émojis entre les langues...**")

        tag_pattern = re.compile(r"(\{e_[a-zA-Z0-9_]+\})")

        locales_data = {}
        try:
            for fichier in LOCALES_DIR.glob("*.json"):
                locales_data[fichier.stem] = await async_json_load(fichier)
        except Exception as e:
            logger.error(f"❌ [AdminI18n] Erreur de lecture des locales pour emojis i18n : {e}")
            return await msg_wait.edit(content="❌ Impossible de lire les fichiers JSON.")

        fr_data = locales_data.get("fr", {})
        if not fr_data:
            return await msg_wait.edit(content="❌ Fichier `fr.json` introuvable ou vide.")

        rapport = []
        corrections_auto = 0
        erreurs_manuelles = 0

        for lang, data in locales_data.items():
            if lang == "fr":
                continue

            modifie = False
            for cle, texte_fr in fr_data.items():
                if cle not in data:
                    continue

                texte_cible = data[cle]
                fr_emojis = tag_pattern.findall(texte_fr)
                cible_emojis = tag_pattern.findall(texte_cible)

                if fr_emojis != cible_emojis:
                    if len(fr_emojis) == len(cible_emojis):
                        iter_emojis = iter(fr_emojis)
                        nouveau_texte = tag_pattern.sub(lambda m: next(iter_emojis), texte_cible)
                        data[cle] = nouveau_texte
                        corrections_auto += 1
                        modifie = True

                    else:
                        if not texte_cible.startswith("[⚠️ EMOJIS]"):
                            data[cle] = f"[⚠️ EMOJIS] {texte_cible}"
                            modifie = True
                        erreurs_manuelles += 1
                        rapport.append(
                            f"• `{lang}.json` | Clé `{cle}` : FR a {len(fr_emojis)} émoji(s), {lang.upper()} en a {len(cible_emojis)}"
                        )

            if modifie:
                fichier = LOCALES_DIR / f"{lang}.json"
                try:
                    await async_json_dump(fichier, data, indent=4, ensure_ascii=False)
                except Exception as e:
                    logger.error(f"❌ [AdminI18n] Échec sauvegarde json {lang} : {e}")

        charger_langues()

        if corrections_auto == 0 and erreurs_manuelles == 0:
            return await msg_wait.edit(
                content="✅ **Parfait !** Tous les émojis sont parfaitement synchronisés dans toutes les langues."
            )

        embed = discord.Embed(
            title="🔄 Synchronisation des Émojis (i18n)",
            description=(
                f"✅ **Corrections automatiques :** `{corrections_auto}`\n"
                f"*(Les émojis ont été remplacés car le nombre correspondait à la FR)*\n\n"
                f"⚠️ **Interventions manuelles requises :** `{erreurs_manuelles}`\n"
                f"*(Le nombre d'émojis est différent de la version FR)*"
            ),
            color=discord.Color.gold(),
        )

        if rapport:
            lignes = "\n".join(rapport[:15])
            if len(rapport) > 15:
                lignes += f"\n*...et {len(rapport) - 15} autres alertes.*"
            embed.add_field(name="Détails des erreurs (Quantité différente)", value=lignes, inline=False)
            embed.set_footer(text="Les textes avec un problème ont été préfixés par [⚠️ EMOJIS] dans les JSON.")

        try:
            await msg_wait.delete()
        except Exception:
            pass
        await ctx.send(embed=embed)

    @commands.command(name="check_bad_emojis", hidden=True)
    async def check_bad_emojis(self, ctx):
        """[CACHÉE] Traque les erreurs de syntaxe comme <<: ou >ID>."""

        msg_wait = await ctx.send("🔍 **Recherche des émojis malformés en cours...**")
        regexes = [re.compile(p) for p in [r"<<a?:[a-zA-Z0-9_]+:\d+>", r">\d+>", r"<a?:[a-zA-Z0-9_]+:\d+>>"]]
        exclude_dirs = [".git", "__pycache__", "venv", "logs", "data"]

        def _scan_bad_emojis():
            trouvees = {}
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
                                        if file not in trouvees:
                                            trouvees[file] = []
                                        trouvees[file].append(f"Ligne {i + 1} : `{matches[0]}`")
                        except Exception:
                            pass
            return trouvees

        try:
            erreurs_trouvees = await asyncio.to_thread(_scan_bad_emojis)
        except Exception as e:
            logger.error(f"❌ [AdminI18n] Erreur scan_bad_emojis : {e}")
            return await msg_wait.edit(content="❌ Erreur lors de l'analyse.")

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

        try:
            await msg_wait.delete()
        except Exception:
            pass
        await ctx.send(embed=embed)

    @commands.command(name="export_app_emojis", hidden=True)
    async def export_app_emojis(self, ctx):
        await ctx.send("📥 Récupération des émojis d'application depuis le portail développeur...")

        dossier_destination = "assets/emojis"
        try:
            await asyncio.to_thread(os.makedirs, dossier_destination, exist_ok=True)
        except Exception as e:
            logger.error(f"❌ [AdminI18n] Impossible de créer le dossier d'export : {e}")
            return await ctx.send("❌ Impossible de créer le dossier d'export.")

        try:
            app_emojis = await self.bot.fetch_application_emojis()
        except AttributeError:
            return await ctx.send(
                "❌ Erreur : Ta version de discord.py est trop ancienne pour cette fonctionnalité (nécessite la version 2.4+)."
            )
        except Exception as e:
            logger.error(f"❌ [AdminI18n] Impossible de fetch l'API Discord emojis : {e}")
            return await ctx.send("❌ Erreur lors de la récupération des émojis Discord.")

        if not app_emojis:
            return await ctx.send("⚠️ Aucun émoji d'application n'a été trouvé.")

        compteur = 0
        for emoji in app_emojis:
            extension = "gif" if emoji.animated else "png"
            chemin_fichier = f"{dossier_destination}/{emoji.name}.{extension}"

            try:
                await emoji.save(chemin_fichier)
                compteur += 1
            except Exception as e:
                logger.warning(f"⚠️ [AdminI18n] Erreur de sauvegarde avec l'émoji {emoji.name} : {e}")

        await ctx.send(
            f"✅ Exportation terminée ! `{compteur}` émojis d'application ont été sauvegardés dans `{dossier_destination}/`."
        )


async def setup(bot):
    await bot.add_cog(AdminI18n(bot))

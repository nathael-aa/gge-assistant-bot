import asyncio
import io
import os
import re

import discord
from discord.ext import commands

from utils import BASE_DIR, DICT_EMOJIS, LOCALES_DIR, async_json_dump, async_json_load, charger_langues

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
        await interaction.response.edit_message(view=self)
        self.stop()

    @discord.ui.button(
        label="Conserver (Trier & Ajouter)", style=discord.ButtonStyle.success, emoji=DICT_EMOJIS.get("e_save", "💾")
    )
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


class AdminI18n(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="i18n_sync", aliases=["sortlang"], hidden=True)
    async def i18n_sync(self, ctx):
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

        cles_utilisees = await asyncio.to_thread(_scan_used_keys)
        # Ajout manuel des clés de mois
        for i in range(1, 13):
            cles_utilisees.add(f"month_{i:02d}")

        locales_data = {}
        for fichier in LOCALES_DIR.glob("*.json"):
            locales_data[fichier.stem] = await async_json_load(fichier)

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
                description=f"J'ai trouvé **{len(inutiles_fr)}** clés orphelines. Que veux-tu faire ?",
                color=discord.Color.orange(),
            )
            view = I18nConfirmView(ctx)
            msg = await ctx.send(embed=embed_warn, view=view, file=discord_file)
            view.message = msg
            await view.wait()

            if not view.responded:
                return await msg.edit(content="⏱️ **Délai expiré.**", embed=None, view=None)

            supprimer_cles = view.delete

        if supprimer_cles:
            for c in inutiles_fr:
                fr_data.pop(c, None)

        for c in manquantes_fr:
            fr_data[c] = "[TODO] Texte manquant"
        fr_data_sorted = dict(sorted(fr_data.items()))
        locales_data["fr"] = fr_data_sorted

        # Alignement des autres langues sur le FR
        for lang, data in locales_data.items():
            if lang == "fr":
                continue
            for c in list(data.keys()):
                if c not in fr_data_sorted:
                    data.pop(c, None)
            for c in fr_data_sorted.keys():
                if c not in data:
                    data[c] = f"[TODO] {fr_data_sorted[c]}"
            locales_data[lang] = dict(sorted(data.items()))

        for lang, data in locales_data.items():
            fichier = LOCALES_DIR / f"{lang}.json"
            await async_json_dump(fichier, data, indent=4, ensure_ascii=False)

        charger_langues()
        await ctx.send(f"✅ **Synchronisation Terminée !** (Total: `{len(fr_data_sorted)}` clés)")

    @commands.command(name="emojis_list", hidden=True)
    async def emojis_list(self, ctx):
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
                                for match in EMOJI_REGEX.findall(f.read()):
                                    full_emoji = f"<{match[0]}:{match[1]}:{match[2]}>"
                                    found.setdefault(full_emoji, set()).add(os.path.relpath(filepath, BASE_DIR))
                        except Exception:
                            pass
            return found

        emojis_found = await asyncio.to_thread(_scan_emojis)
        if not emojis_found:
            return await msg_wait.edit(content="ℹ️ Aucun émoji détecté dans le code.")

        report_lines = ["=== RAPPORT DES EMOJIS ===\n"]
        for emoji, paths in sorted(emojis_found.items()):
            report_lines.append(f"{emoji} :\n" + "\n".join([f"  └─ {p}" for p in sorted(paths)]) + "\n")

        file_buffer = io.BytesIO("\n".join(report_lines).encode("utf-8"))
        await msg_wait.delete()
        await ctx.send(
            f"✅ Trouvé **{len(emojis_found)}** émojis uniques.",
            file=discord.File(fp=file_buffer, filename="emojis_report.txt"),
        )

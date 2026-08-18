import asyncio
import json
import logging
import math
import random
import urllib.parse
from datetime import datetime

import discord
from discord import app_commands
from discord.ext import commands

import utils
from utils import (
    BASE_DATA_PATH,
    CONFIG_DIR,
    PaginationView,
    _get_api_timestamp,
    alliance_autocomplete,
    format_num,
    get_api_headers,
    get_cached_data,
    get_server_config,
    joueur_autocomplete,
    prompt_vote_if_lucky,
    setup_embed_footer,
    t,
)

logger = logging.getLogger("GGE_Bot")

# ========================================================
# 🎛️ COMPOSANTS UI : ASSISTANT DE CONFIGURATION (WIZARD)
# ========================================================
class CustomNumberModal(discord.ui.Modal):
    def __init__(self, title, placeholder, wizard, key_to_update, next_step):
        super().__init__(title=title)
        self.wizard = wizard
        self.key_to_update = key_to_update
        self.next_step = next_step
        
        self.val_input = discord.ui.TextInput(
            label="Valeur personnalisée",
            placeholder=placeholder,
            required=True,
            max_length=15
        )
        self.add_item(self.val_input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            clean_val = self.val_input.value.replace(" ", "")
            val = int(clean_val)
            self.wizard.config[self.key_to_update] = val
            self.wizard.step = self.next_step
            await self.wizard.update_view(interaction)
        except ValueError:
            await interaction.response.send_message("❌ Valeur invalide, veuillez entrer uniquement un nombre (ex: -1000000 ou 50).", ephemeral=True)

class WizardButton(discord.ui.Button):
    def __init__(self, label, value, step_target, wizard, key_to_update=None, style=discord.ButtonStyle.primary, row=0):
        super().__init__(label=label, style=style, row=row)
        self.value = value
        self.step_target = step_target
        self.wizard = wizard
        self.key_to_update = key_to_update

    async def callback(self, interaction: discord.Interaction):
        if self.key_to_update:
            self.wizard.config[self.key_to_update] = self.value
            
            if self.key_to_update == "ignore_tiers" and self.value is True:
                self.wizard.config["tier_diff"] = 0
            elif self.key_to_update == "tier_diff":
                self.wizard.config["ignore_tiers"] = False

        self.wizard.step = self.step_target
        await self.wizard.update_view(interaction)

class CustomMinMaxModal(discord.ui.Modal):
    def __init__(self, title, ph_min, ph_max, wizard, key_min, key_max, next_step):
        super().__init__(title=title)
        self.wizard = wizard
        self.key_min = key_min
        self.key_max = key_max
        self.next_step = next_step
        
        self.val_min = discord.ui.TextInput(
            label="Minimum (0 = Pas de min)",
            placeholder=ph_min,
            required=True,
            default=str(wizard.config.get(key_min, 0))
        )
        self.val_max = discord.ui.TextInput(
            label="Maximum (99999999 = Pas de max)",
            placeholder=ph_max,
            required=True,
            default=str(wizard.config.get(key_max, 99999999))
        )
        self.add_item(self.val_min)
        self.add_item(self.val_max)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            self.wizard.config[self.key_min] = int(self.val_min.value.replace(" ", ""))
            self.wizard.config[self.key_max] = int(self.val_max.value.replace(" ", ""))
            self.wizard.step = self.next_step
            await self.wizard.update_view(interaction)
        except ValueError:
            await interaction.response.send_message("❌ Valeurs invalides, veuillez entrer uniquement des nombres.", ephemeral=True)

class ToggleButton(discord.ui.Button):
    def __init__(self, key, wizard, label_on, label_off, style_on=discord.ButtonStyle.success, style_off=discord.ButtonStyle.secondary, row=0):
        self.wizard = wizard
        self.key = key
        is_on = self.wizard.config.get(key, False)
        super().__init__(
            label=label_on if is_on else label_off,
            style=style_on if is_on else style_off,
            row=row
        )

    async def callback(self, interaction: discord.Interaction):
        self.wizard.config[self.key] = not self.wizard.config[self.key]
        await self.wizard.update_view(interaction)

class TargetWizard(discord.ui.View):
    def __init__(self, user_id, langue="fr", current_config=None):
        super().__init__(timeout=600)
        self.user_id = user_id
        self.langue = langue
        self.config = current_config or {
            "min_dist": 0, "tier_diff": 0, "max_lvl_diff": 10, "pp_min": -3000000, "pp_max": 10000000,
            "honor_min": 0, "honor_max": 99999999, "loot_min": 0, "loot_max": 999999999,
            "show_doves": False, "ignore_tiers": False, "only_with_alliance": True,
            "level_system": "tier"
        }
        self.step = 1

    async def start(self, interaction: discord.Interaction, content=None):
        await self.update_view(interaction, initial=True, content=content)

    async def update_view(self, interaction: discord.Interaction, initial=False, content=None):
        self.clear_items()
        embed = discord.Embed(color=discord.Color.blue())

        if self.step == 1:
            embed.title = "📍 Étape 1/8 : La Distance"
            embed.description = "À quelle **distance minimum** de ton château veux-tu chercher tes cibles ?"
            self.add_item(WizardButton("Pas de minimum (0)", 0, 2, self, "min_dist"))
            self.add_item(WizardButton("30 lieues", 30, 2, self, "min_dist"))
            self.add_item(WizardButton("50 lieues", 50, 2, self, "min_dist"))
            self.add_item(WizardButton("100 lieues", 100, 2, self, "min_dist"))
            
            b_custom = discord.ui.Button(label="⌨️ Saisir...", style=discord.ButtonStyle.secondary)
            async def custom_cb_1(i):
                await i.response.send_modal(CustomNumberModal("Distance Minimum", "Ex: 15", self, "min_dist", 2))
            b_custom.callback = custom_cb_1
            self.add_item(b_custom)

        elif self.step == 2:
            embed.title = "⚖️ Étape 2/8 : Le Système de Niveau"
            embed.description = "Certains serveurs (comme FR1) utilisent des **Paliers** (tranches communautaires), d'autres se basent sur le **Niveau global exact** (classique + légendaire).\nQuel système veux-tu utiliser ?"

            btn_tier = discord.ui.Button(label="Système de Paliers (Classique)", style=discord.ButtonStyle.primary)
            async def cb_tier(i):
                self.config["level_system"] = "tier"
                self.config["ignore_tiers"] = False
                self.step = 3
                await self.update_view(i)
            btn_tier.callback = cb_tier
            self.add_item(btn_tier)

            btn_lvl = discord.ui.Button(label="Niveau Global (Universel)", style=discord.ButtonStyle.primary)
            async def cb_lvl(i):
                self.config["level_system"] = "level"
                self.config["ignore_tiers"] = False
                self.step = 3
                await self.update_view(i)
            btn_lvl.callback = cb_lvl
            self.add_item(btn_lvl)

            btn_ignore = discord.ui.Button(label="♾️ Ignorer l'écart", style=discord.ButtonStyle.danger)
            async def cb_ignore(i):
                self.config["ignore_tiers"] = True
                self.step = 4
                await self.update_view(i)
            btn_ignore.callback = cb_ignore
            self.add_item(btn_ignore)

            self.add_item(WizardButton("◀ Retour", None, 1, self, style=discord.ButtonStyle.secondary, row=1))

        elif self.step == 3:
            if self.config.get("level_system") == "tier":
                embed.title = "⚖️ Étape 3/8 : L'Écart de Palier"
                embed.description = "Combien de **paliers au-dessus** du tien acceptes-tu d'affronter ?"
                self.add_item(WizardButton("Même palier (0)", 0, 4, self, "tier_diff"))
                self.add_item(WizardButton("+1 Palier", 1, 4, self, "tier_diff"))
                self.add_item(WizardButton("+2 Paliers", 2, 4, self, "tier_diff"))
            else:
                embed.title = "⚖️ Étape 3/8 : L'Écart de Niveau Global"
                embed.description = "Jusqu'à combien de **niveaux (classique + légendaire)** au-dessus de toi acceptes-tu d'affronter ?"
                self.add_item(WizardButton("Même niveau (+10 max)", 10, 4, self, "max_lvl_diff"))
                self.add_item(WizardButton("+50 Niveaux", 50, 4, self, "max_lvl_diff"))
                self.add_item(WizardButton("+150 Niveaux", 150, 4, self, "max_lvl_diff"))

                b_custom = discord.ui.Button(label="⌨️ Saisir...", style=discord.ButtonStyle.secondary)
                async def custom_cb_2(i):
                    await i.response.send_modal(CustomNumberModal("Écart de Niveau Max", "Ex: 75", self, "max_lvl_diff", 4))
                b_custom.callback = custom_cb_2
                self.add_item(b_custom)

            self.add_item(WizardButton("◀ Retour", None, 2, self, style=discord.ButtonStyle.secondary, row=1))

        elif self.step == 4:
            embed.title = "📉 Étape 4/8 : Puissance Minimum"
            embed.description = "Jusqu'à combien de **puissance en MOINS** que toi acceptes-tu de frapper ?"
            self.add_item(WizardButton("Même puissance (0)", 0, 5, self, "pp_min"))
            self.add_item(WizardButton("- 2 Million", -2000000, 5, self, "pp_min"))
            self.add_item(WizardButton("- 5 Millions", -5000000, 5, self, "pp_min"))
            self.add_item(WizardButton("- 10 Millions", -10000000, 5, self, "pp_min"))
            
            b_custom = discord.ui.Button(label="⌨️ Saisir...", style=discord.ButtonStyle.secondary)
            async def custom_cb_3(i):
                await i.response.send_modal(CustomNumberModal("Puissance Minimum", "Ex: -2500000", self, "pp_min", 5))
            b_custom.callback = custom_cb_3
            self.add_item(b_custom)
            
            self.add_item(WizardButton("◀ Retour", None, 3, self, style=discord.ButtonStyle.secondary, row=1))

        elif self.step == 5:
            embed.title = "📈 Étape 5/8 : Puissance Maximum"
            embed.description = "Jusqu'à combien de **puissance en PLUS** que toi acceptes-tu d'affronter ?"
            self.add_item(WizardButton("Même puissance (0)", 0, 6, self, "pp_max"))
            self.add_item(WizardButton("+ 2 Millions", 2000000, 6, self, "pp_max"))
            self.add_item(WizardButton("+ 5 Millions", 5000000, 6, self, "pp_max"))
            self.add_item(WizardButton("+ 10 Millions", 10000000, 6, self, "pp_max"))
            
            b_custom = discord.ui.Button(label="⌨️ Saisir...", style=discord.ButtonStyle.secondary)
            async def custom_cb_4(i):
                await i.response.send_modal(CustomNumberModal("Puissance Maximum", "Ex: 4500000", self, "pp_max", 6))
            b_custom.callback = custom_cb_4
            self.add_item(b_custom)
            
            self.add_item(WizardButton("◀ Retour", None, 4, self, style=discord.ButtonStyle.secondary, row=1))

        elif self.step == 6:
            embed.title = "🎖️ Étape 6/8 : L'Honneur"
            embed.description = "Recherches-tu des cibles avec un montant d'Honneur spécifique ?"
            
            btn_ignore_honor = discord.ui.Button(label="♾️ Peu importe (Ignorer)", style=discord.ButtonStyle.primary)
            async def cb_ignore_honor(i):
                self.config["honor_min"] = 0
                self.config["honor_max"] = 99999999
                self.step = 7
                await self.update_view(i)
            btn_ignore_honor.callback = cb_ignore_honor
            self.add_item(btn_ignore_honor)
            
            btn_custom = discord.ui.Button(label="⌨️ Saisir Min / Max", style=discord.ButtonStyle.secondary)
            async def cb_honor(i):
                await i.response.send_modal(CustomMinMaxModal("Filtre Honneur", "Min (Ex: 1000)", "Max (Ex: 99999)", self, "honor_min", "honor_max", 7))
            btn_custom.callback = cb_honor
            self.add_item(btn_custom)
            
            self.add_item(WizardButton("◀ Retour", None, 5, self, style=discord.ButtonStyle.secondary, row=1))

        elif self.step == 7:
            embed.title = "💰 Étape 7/8 : Le Butin (Loot)"
            embed.description = "Veux-tu filtrer par quantité de butin disponible ?"
            
            btn_ignore_loot = discord.ui.Button(label="♾️ Peu importe (Ignorer)", style=discord.ButtonStyle.primary)
            async def cb_ignore_loot(i):
                self.config["loot_min"] = 0
                self.config["loot_max"] = 999999999
                self.step = 8
                await self.update_view(i)
            btn_ignore_loot.callback = cb_ignore_loot
            self.add_item(btn_ignore_loot)
            
            btn_custom = discord.ui.Button(label="⌨️ Saisir Min / Max", style=discord.ButtonStyle.secondary)
            async def cb_loot(i):
                await i.response.send_modal(CustomMinMaxModal("Filtre Butin", "Min (Ex: 50000)", "Max (Ex: 9999999)", self, "loot_min", "loot_max", 8))
            btn_custom.callback = cb_loot
            self.add_item(btn_custom)
            
            self.add_item(WizardButton("◀ Retour", None, 6, self, style=discord.ButtonStyle.secondary, row=1))

        elif self.step == 8:
            embed.title = "🎛️ Étape 8/8 : Filtres avancés"
            embed.description = "Derniers ajustements. Clique sur les boutons pour activer/désactiver les options :"
            
            self.add_item(ToggleButton("show_doves", self, "🕊️ Colombes : Visibles", "🕊️ Colombes : Masquées"))
            self.add_item(ToggleButton("only_with_alliance", self, "🛡️ Alliance : Obligatoire", "🛡️ Alliance : Non requise"))
            
            b_finish = discord.ui.Button(label="✅ Enregistrer et Terminer", style=discord.ButtonStyle.primary, row=1)
            async def finish_cb(i):
                self.step = 9
                self.save_config()
                await self.update_view(i)
            b_finish.callback = finish_cb
            self.add_item(b_finish)
            
            self.add_item(WizardButton("◀ Retour", None, 7, self, style=discord.ButtonStyle.secondary, row=1))

        elif self.step == 9:
            embed = self.generate_summary_embed()
            embed.color = discord.Color.green()
            self.add_item(WizardButton("🔄 Modifier à nouveau", None, 1, self, style=discord.ButtonStyle.secondary))
            
            b_close = discord.ui.Button(label="❌ Fermer", style=discord.ButtonStyle.danger)
            async def close_cb(i):
                msg_fermeture = "✅ **Configuration sauvegardée et menu fermé.**\n*(Tu peux faire disparaître ce message en cliquant sur 'Ignorer le message' en bleu en bas).* "
                await i.response.edit_message(content=msg_fermeture, embed=None, view=None)
            b_close.callback = close_cb
            self.add_item(b_close)

        if initial:
            await interaction.response.send_message(content=content, embed=embed, view=self, ephemeral=True)
        else:
            await interaction.response.edit_message(content=None, embed=embed, view=self)

    def save_config(self):
        path_users = CONFIG_DIR / 'target.json'
        try:
            data = {}
            if path_users.exists():
                with open(path_users, encoding='utf-8') as f:
                    data = json.load(f)
            data.setdefault(self.user_id, {})["custom_rules"] = self.config
            with open(path_users, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            utils.clear_config_cache()
        except Exception as e:
            logger.error(f"Erreur sauvegarde config perso : {e}")

    def generate_summary_embed(self):
        embed = discord.Embed(title="✅ Radar configuré avec succès !", description="Voici ton nouveau profil de recherche de cibles :")
        
        txt_doves = "✅ Incluses" if self.config.get("show_doves") else "❌ Masquées"
        txt_alli = "✅ Oui" if self.config.get("only_with_alliance") else "❌ Non (Inclut les Sans-alliance)"

        if self.config.get("ignore_tiers"):
            txt_lvl_sys = "Aucun (Ignoré)"
            txt_tiers = "✅ Oui (No-limit)"
        elif self.config.get("level_system") == "tier":
            txt_lvl_sys = f"Paliers Classiques (+{self.config.get('tier_diff')} Palier)"
            txt_tiers = f"❌ Non (+{self.config.get('tier_diff')} Palier max)"
        else:
            txt_lvl_sys = f"Niveau Global (+{self.config.get('max_lvl_diff')} Niveaux)"
            txt_tiers = f"❌ Non (+{self.config.get('max_lvl_diff')} Niveaux max)"

        hon_str = f"{format_num(self.config.get('honor_min', 0))} à {format_num(self.config.get('honor_max', 99999999))}"
        loot_str = f"{format_num(self.config.get('loot_min', 0))} à {format_num(self.config.get('loot_max', 999999999))}"

        f1_val = f"**Distance min** : {self.config.get('min_dist')} lieues\n**Système Niv.** : {txt_lvl_sys}\n**Écart PP** : {format_num(self.config.get('pp_min'))} à +{format_num(self.config.get('pp_max'))}"
        f2_val = f"**Honneur** : {hon_str}\n**Butin** : {loot_str}"
        f3_val = f"**Colombes** : {txt_doves}\n**Ignorer Niveaux** : {txt_tiers}\n**Alliance** : {txt_alli}"

        embed.add_field(name="🔢 Critères Principaux", value=f1_val, inline=False)
        embed.add_field(name="🎯 Critères Secondaires", value=f2_val, inline=False)
        embed.add_field(name="🎛️ Filtres Avancés", value=f3_val, inline=False)
        return embed

# ========================================================
# 🎛️ COMPOSANT UI : PAGINATION DES CIBLES + RELANCE EN DIRECT
# ========================================================
class CiblePaginationView(discord.ui.View):
    def __init__(self, cog, attacker, sort_by, target_alliance, embeds, langue="fr", owner_id=None):
        super().__init__(timeout=1800)
        self.cog = cog
        self.attacker = attacker
        self.sort_by = sort_by
        self.target_alliance = target_alliance
        self.embeds = embeds
        self.current_page = 0
        self.langue = langue
        self.owner_id = owner_id

        self.btn_prev.label = t(langue, "guerre_btn_prev", defaut="Page Précédente")
        self.btn_next.label = t(langue, "guerre_btn_next", defaut="Page Suivante")
        self.btn_rerun.label = t(langue, "guerre_btn_rerun", defaut="Relancer une vague")
        self.update_buttons()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if self.owner_id is not None and interaction.user.id != self.owner_id:
            return False
        return True

    def update_buttons(self):
        self.btn_prev.disabled = self.current_page == 0
        self.btn_next.disabled = self.current_page == len(self.embeds) - 1

    @discord.ui.button(emoji="<:lastpage:1533554126984581283>", style=discord.ButtonStyle.secondary, custom_id="cible_prev")
    async def btn_prev(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page -= 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.embeds[self.current_page], view=self)

    @discord.ui.button(emoji="<:nextpage:1533554128230420590>", style=discord.ButtonStyle.secondary, custom_id="cible_next")
    async def btn_next(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page += 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.embeds[self.current_page], view=self)

    @discord.ui.button(emoji="<:refresh:1533433306610274425>", style=discord.ButtonStyle.primary, custom_id="cible_rerun")
    async def btn_rerun(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children:
            item.disabled = True
            
        msg = t(self.langue, "guerre_lbl_calc_rerun", defaut="<:icon_search:1512505406474293438> *Calcul d'une nouvelle vague de cibles aléatoires en cours...*")
        await interaction.response.edit_message(content=msg, embed=None, view=self)
        
        await self.cog._execute_cible(
            interaction, 
            self.attacker, 
            self.sort_by, 
            self.target_alliance, 
            message_to_edit=interaction.message
        )

# ==========================================
# ⚔️ LE COG GUERRE
# ==========================================
class GuerreCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.clr_proximite = discord.Color.from_rgb(204,0,0)
        self.clr_cible     = discord.Color.from_rgb(183,0,0)
        self.clr_hr        = discord.Color.from_rgb(146,0,0)

    # ==========================================
    # 📍 COMMANDE : PROXIMITY
    # ==========================================
    @app_commands.command(name="proximity", description="Find the enemy castles closest to you")
    @app_commands.autocomplete(my_player=joueur_autocomplete)
    @app_commands.autocomplete(enemy_alliance=alliance_autocomplete)
    async def proximity(self, interaction: discord.Interaction, my_player: str, enemy_alliance: str):
        try: await interaction.response.defer(thinking=True)
        except: return
        
        langue, serveur = await get_server_config(interaction)

        cache_data = await get_cached_data(serveur)
        local_data = cache_data.get('players_data', {})

        target_id = None
        for p_info in local_data.values():
            a_obj = p_info.get('alliance')
            a_name = a_obj.get('name') if isinstance(a_obj, dict) else (p_info.get('alliance_name') or a_obj)
            
            if a_name and str(a_name).lower() == enemy_alliance.lower():
                aid = p_info.get('allianceId') or p_info.get('alliance_id')
                if not aid and isinstance(a_obj, dict):
                    aid = a_obj.get('allianceId') or a_obj.get('alliance_id')
                
                if aid:
                    target_id = str(aid)
                    enemy_alliance = str(a_name) 
                    break

        if not target_id:
            return await interaction.followup.send(t(langue, "guerre_err_alli_cache2", a=enemy_alliance, defaut=f"<:error:1512505075220611172> Alliance **{enemy_alliance}** introuvable dans le cache."))

        headers = await get_api_headers(custom_server=serveur)
        
        my_x, my_y = None, None
        url_me = f"https://api.gge-tracker.com/api/v1/castle/search/{urllib.parse.quote(my_player)}"
        try:
            async with self.bot.session.get(url_me, headers=headers, timeout=5) as r:
                if r.status == 200:
                    c_data = await r.json()
                    if isinstance(c_data, dict): c_data = [c_data]
                    if isinstance(c_data, list):
                        for c in c_data:
                            if str(c.get('kingdomId', c.get('kingdom_id'))) == "0" and str(c.get('type', c.get('castle_type'))) == "1":
                                my_x = c.get('positionX') or c.get('position_x') or c.get('x')
                                my_y = c.get('positionY') or c.get('position_y') or c.get('y')
                                break
        except: pass
        
        if my_x is None or my_y is None:
            return await interaction.followup.send(t(langue, "guerre_err_no_coords", p=my_player, defaut=f"<:error:1512505075220611172> Impossible de trouver les coordonnées exactes de **{my_player}**."))

        my_x, my_y = int(my_x), int(my_y)

        url_alli = f"https://api.gge-tracker.com/api/v1/alliances/id/{target_id}"
        try:
            async with self.bot.session.get(url_alli, headers=headers, timeout=10) as r:
                if r.status != 200: return await interaction.followup.send(t(langue, "guerre_err_api", defaut="<:error:1512505075220611172> Erreur de l'API GGE-Tracker (Alliance)."))
                data = await r.json()
                if isinstance(data, list) and data: data = data[0]
        except Exception as e:
            return await interaction.followup.send(t(langue, "guerre_err_api_join", e=str(e), defaut=f"<:error:1512505075220611172> Impossible de joindre l'API : {e}"))

        members = data.get("players", data.get("members", data.get("playerList", [])))
        if not members: return await interaction.followup.send(t(langue, "guerre_err_alli_empty2", defaut="<:error:1512505075220611172> L'alliance ennemie semble vide."))

        actualisation_dt = _get_api_timestamp(data)
        txt_unk = t(langue, "prof_unknown", defaut="Inconnu")

        async def get_enemy_coords(m):
            p_name = m.get('player_name', m.get('playerName', m.get('name', txt_unk)))
            p_pp = int(m.get('might_current', m.get('might', m.get('main_points', 0))))
            p_peace = m.get('peace_disabled_at')
            is_protected = False
            if p_peace and p_peace != "null":
                try:
                    if datetime.fromisoformat(p_peace.replace('Z', '+00:00')) > discord.utils.utcnow():
                        is_protected = True
                except: pass
            
            url_s = f"https://api.gge-tracker.com/api/v1/castle/search/{urllib.parse.quote(p_name)}"
            try:
                async with self.bot.session.get(url_s, headers=headers, timeout=10) as res:
                    if res.status == 200:
                        c_data = await res.json()
                        if isinstance(c_data, dict): c_data = [c_data]
                        if isinstance(c_data, list):
                            for c in c_data:
                                if str(c.get('kingdomId', c.get('kingdom_id'))) == "0" and str(c.get('type', c.get('castle_type'))) == "1":
                                    x = c.get('positionX') or c.get('position_x') or c.get('x')
                                    y = c.get('positionY') or c.get('position_y') or c.get('y')
                                    if x is not None and y is not None:
                                        dist = math.hypot(int(x) - my_x, int(y) - my_y)
                                        return {"name": p_name, "x": int(x), "y": int(y), "dist": dist, "pp": p_pp, "protected": is_protected}
            except: pass
            return None

        tasks = [get_enemy_coords(m) for m in members]
        results = await asyncio.gather(*tasks)

        valid_targets = [res for res in results if res is not None]
        valid_targets.sort(key=lambda t: t["dist"])

        if not valid_targets:
            return await interaction.followup.send(t(langue, "guerre_err_no_castle_loc", defaut="<:error:1512505075220611172> Impossible de localiser les châteaux de cette alliance sur la carte Principale."))

        embeds = []
        chunk_size = 5 
        nb_pages = max(1, (len(valid_targets) - 1) // chunk_size + 1)
        
        for i in range(0, len(valid_targets), chunk_size):
            chunk = valid_targets[i:i+chunk_size]
            page_num = (i // chunk_size) + 1
            
            embed = discord.Embed(
                title=t(langue, "guerre_prox_title", a=enemy_alliance, defaut=rf"\<:attaque:1512570903886692474> Cibles de Proximité : {enemy_alliance}"), 
                color=self.clr_proximite
            )
            
            desc_i18n = t(langue, "guerre_prox_desc", p=my_player, x=my_x, y=my_y, n=len(valid_targets), defaut=f"🛰️ Ton point de départ : **{my_player}** (`{my_x}:{my_y}`)\n<:icon_search:1512505406474293438> **{len(valid_targets)}** cibles localisées au total.")
            
            lbl_date = t(langue, "guerre_lbl_date_data", defaut="⏱️ **Données datées de :**")
            embed.description = f"{lbl_date} <t:{int(actualisation_dt.timestamp())}:F> (<t:{int(actualisation_dt.timestamp())}:R>)\n\n{desc_i18n}"
            
            lbl_dist = t(langue, "guerre_prox_field_dist", defaut="Distance :")
            lbl_coords = t(langue, "guerre_prox_field_coords", defaut="Coords :")
            lbl_pp = t(langue, "guerre_prox_field_pp", defaut="Puissance :")
            lbl_col_yes = t(langue, "guerre_prox_colombe", defaut="<:peace:1512503935892586566> **SOUS COLOMBE**")
            lbl_col_no = t(langue, "guerre_prox_vuln", defaut=r"\<:attaque:1512570903886692474> **VULNÉRABLE**")

            for j, tg in enumerate(chunk):
                index_global = i + j + 1
                colombe_txt = lbl_col_yes if tg['protected'] else lbl_col_no
                embed.add_field(
                    name=f"#{index_global} - {tg['name']}", 
                    value=f"<:icon_world:1512517516012814537> {lbl_dist} **{int(tg['dist'])} lieues**\n<:compass:1512504625364729987> {lbl_coords} `{tg['x']}:{tg['y']}`\n<:pp2:1512571027119538335> {lbl_pp} {format_num(tg['pp'])}\n{colombe_txt}", 
                    inline=False
                )

            embed.add_field(name=t(langue, "guerre_prox_footer_page", cur=page_num, tot=nb_pages, defaut=f"Page {page_num}/{nb_pages}"), value=t(langue, "guerre_prox_footer_tri", defaut="*Tri effectué du plus proche au plus éloigné.*"), inline=False)
            await setup_embed_footer(embed, interaction, langue)
            embeds.append(embed)

        if len(embeds) == 1: await interaction.followup.send(embed=embeds[0])
        else:
            view = PaginationView(embeds)
            await interaction.followup.send(embed=embeds[0], view=view)
        await prompt_vote_if_lucky(interaction, probability_percent=15, langue=langue)

    # ==========================================
    # 🎯 GROUPE DE COMMANDES : TARGET
    # ==========================================
    target_group = app_commands.Group(
        name="target", 
        description="Radar tools and rules arbitration based on your personal profile"
    )

    @target_group.command(name="setup", description="Configure your personal radar rules step-by-step")
    async def target_setup(self, interaction: discord.Interaction):
        langue, _ = await get_server_config(interaction)
        user_id = str(interaction.user.id)
        
        # Charger la config existante
        import utils
        current_rules = utils.USERS_CONFIG_CACHE.get(user_id, {}).get("custom_rules", None)
        
        # Lancer l'assistant (Wizard)
        wizard = TargetWizard(user_id, langue, current_rules)
        await wizard.start(interaction)

    @target_group.command(name="search", description="Find targets based on your personal rules")
    @app_commands.autocomplete(attacker=joueur_autocomplete)
    @app_commands.autocomplete(target_alliance=alliance_autocomplete)
    async def target_search(self, interaction: discord.Interaction, attacker: str, target_alliance: str = None):
        langue, _ = await get_server_config(interaction)
        import utils
        user_id = str(interaction.user.id)
        has_rules = utils.USERS_CONFIG_CACHE and user_id in utils.USERS_CONFIG_CACHE and "custom_rules" in utils.USERS_CONFIG_CACHE[user_id]
        
        if not has_rules:
            wizard = TargetWizard(user_id, langue)
            err_msg = t(langue, "target_err_no_setup", defaut="⚠️ **Stop !** Tu dois d'abord configurer ton radar. Laisse-moi te guider :")
            return await wizard.start(interaction, content=err_msg)

        try: await interaction.response.defer(thinking=True)
        except: return
        await self._execute_cible(interaction, attacker, "aleatoire", target_alliance)

    @target_group.command(name="hr", description="Check if an attack complies with your personal rules")
    @app_commands.autocomplete(attacker=joueur_autocomplete)
    @app_commands.autocomplete(defender=joueur_autocomplete)
    async def target_hr(self, interaction: discord.Interaction, attacker: str, defender: str):
        langue, _ = await get_server_config(interaction)
        import utils
        user_id = str(interaction.user.id)
        has_rules = utils.USERS_CONFIG_CACHE and user_id in utils.USERS_CONFIG_CACHE and "custom_rules" in utils.USERS_CONFIG_CACHE[user_id]
        
        if not has_rules:
            wizard = TargetWizard(user_id, langue)
            err_msg = t(langue, "target_err_no_setup", defaut="⚠️ **Stop !** Tu dois d'abord configurer ton radar. Laisse-moi te guider :")
            return await wizard.start(interaction, content=err_msg)
            
        try: await interaction.response.defer(thinking=True)
        except: return
        await self._execute_hr(interaction, attacker, defender)

    # ==========================================
    # ⚙️ MOTEUR D'EXÉCUTION CENTRALISÉ DU SCAN
    # ==========================================
    async def _execute_cible(self, interaction: discord.Interaction, attacker: str, sort_by: str = "aleatoire", target_alliance: str = None, message_to_edit=None):
        langue, serveur = await get_server_config(interaction)

        def get_tier(lvl, leg):
            if lvl < 70: return 0
            if leg <= 120: return 1
            if leg <= 360: return 2
            if leg <= 650: return 3
            if leg <= 949: return 4
            return 5

        def get_abs_lvl(lvl, leg):
            return lvl + leg

        user_id = str(interaction.user.id)
        user_rules = utils.USERS_CONFIG_CACHE.get(user_id, {}).get("custom_rules", {})
        
        config = {
            "nom": "Règles Personnelles",
            "check_api_limit": False,
            "level_system": user_rules.get("level_system", "tier"),
            "allowed_tiers_relative": list(range(user_rules.get("tier_diff", 0) + 1)),
            "max_lvl_diff": user_rules.get("max_lvl_diff", 10),
            "pp_offset_min": user_rules.get("pp_min", -3000000),
            "pp_offset_max": user_rules.get("pp_max", 10000000),
            "honor_min": user_rules.get("honor_min", 0),
            "honor_max": user_rules.get("honor_max", 99999999),
            "loot_min": user_rules.get("loot_min", 0),
            "loot_max": user_rules.get("loot_max", 999999999),
            "min_distance": user_rules.get("min_dist", 0),
            "ignore_tiers": user_rules.get("ignore_tiers", False),
            "affichage": {"max_attaques": "Variables", "cooldown": "Selon envies"}
        }
        show_doves = user_rules.get("show_doves", False)
        only_with_alliance = user_rules.get("only_with_alliance", True)

        def is_legal_target(a_pp, a_tier, a_abs_lvl, t_pp, t_tier, t_abs_lvl):
            if not config.get("ignore_tiers", False):
                if config["level_system"] == "tier":
                    diff_tier = t_tier - a_tier
                    allowed_tiers = config.get("allowed_tiers_relative", [0])
                    if diff_tier not in allowed_tiers: return False
                    
                    if a_tier == 0 and t_tier == 0:
                        if abs(a_abs_lvl - t_abs_lvl) > 10: return False
                else:
                    diff_lvl = t_abs_lvl - a_abs_lvl
                    if diff_lvl > config.get("max_lvl_diff", 10): return False

            if "pp_offset_min" in config and t_pp < (a_pp + config["pp_offset_min"]): return False
            if "pp_offset_max" in config and t_pp > (a_pp + config["pp_offset_max"]): return False

            return True

        headers = await get_api_headers(custom_server=serveur)
        cache_data = await get_cached_data(serveur)
        local_data = cache_data.get('players_data', {})
        session_active = self.bot.session

        a_info, a_name_real = None, attacker
        a_coords = {"x": None, "y": None}
        
        try:
            search_url = f"https://api.gge-tracker.com/api/v1/players/{urllib.parse.quote(attacker)}"
            async with session_active.get(search_url, headers=headers, timeout=10) as r:
                if r.status == 200:
                    p_data = await r.json()
                    if isinstance(p_data, list) and p_data: p_data = p_data[0]
                    if isinstance(p_data, dict):
                        a_info = p_data
                        a_name_real = a_info.get("playerName", a_info.get("name", a_info.get("player_name", attacker)))
            
            coords_url = f"https://api.gge-tracker.com/api/v1/castle/search/{urllib.parse.quote(a_name_real)}"
            async with session_active.get(coords_url, headers=headers, timeout=10) as r:
                if r.status == 200:
                    c_data = await r.json()
                    if isinstance(c_data, dict): c_data = [c_data]
                    if isinstance(c_data, list):
                        for c in c_data:
                            if str(c.get('kingdomId', c.get('kingdom_id', 'X'))) == "0" and str(c.get('type', c.get('castle_type', 'X'))) == "1":
                                ax = c.get('positionX') or c.get('position_x') or c.get('x')
                                ay = c.get('positionY') or c.get('position_y') or c.get('y')
                                if ax is not None and ay is not None:
                                    a_coords['x'], a_coords['y'] = float(ax), float(ay)
                                break
        except: pass

        if not a_info:
            for name, info in local_data.items():
                if name.lower() == attacker.lower():
                    a_info, a_name_real = info, name
                    break

        if not a_info:
            err_msg = t(langue, "guerre_err_atk_not_found", atk=attacker, defaut=f"<:error:1512505075220611172> Attaquant **{attacker}** introuvable.")
            if message_to_edit: await message_to_edit.edit(content=err_msg, view=None)
            else: await interaction.followup.send(err_msg)
            return

        a_lvl = int(a_info.get('level', 0))
        a_leg = int(a_info.get('legendary_level', a_info.get('legendaryLevel', 0)))
        a_pp = int(a_info.get('might_current', a_info.get('main_points', a_info.get('might', 0))))
        a_tier = get_tier(a_lvl, a_leg)
        a_abs_lvl = get_abs_lvl(a_lvl, a_leg)
        
        mots_interdits_mur, alliances_mur_alerte = ["repos", "deuil", "hospitalisé"], []
        try:
            fichier_murs = BASE_DATA_PATH / 'murs_scans' / serveur / 'murs_alliances.json'
            if fichier_murs.exists():
                with open(fichier_murs, encoding='utf-8') as f:
                    for aname_json, desc in json.load(f).items():
                        if any(mot in str(desc).lower() for mot in mots_interdits_mur):
                            alliances_mur_alerte.append("".join(c for c in str(aname_json).lower() if c.isalnum()))
        except: pass

        pool_candidats = []
        txt_sans_alliance = t(langue, "guerre_sa", defaut="Sans alliance")

        for t_name, t_info in local_data.items():
            if t_name.lower() == a_name_real.lower(): continue
            
            raw_t_alliance = t_info.get('alliance') or t_info.get('alliance_name') or t_info.get('allianceName') or ''
            if isinstance(raw_t_alliance, dict): 
                t_alliance = raw_t_alliance.get('name') or raw_t_alliance.get('alliance_name') or ''
            else: 
                t_alliance = str(raw_t_alliance)
                
            if only_with_alliance and (not t_alliance or t_alliance == txt_sans_alliance): 
                continue

            if not show_doves:
                p_peace = t_info.get('peace_disabled_at')
                if p_peace and p_peace != "null":
                    try:
                        if datetime.fromisoformat(p_peace.replace('Z', '+00:00')) > discord.utils.utcnow():
                            continue 
                    except: pass
            
            alli_clean = "".join(c for c in t_alliance.lower() if c.isalnum())
            if target_alliance and t_alliance.lower() != target_alliance.lower(): continue

            t_lvl = int(t_info.get('level', 0))
            t_leg = int(t_info.get('legendary_level', 0))
            t_pp = int(t_info.get('main_points', 0))
            t_tier = get_tier(t_lvl, t_leg)
            t_abs_lvl = get_abs_lvl(t_lvl, t_leg)

            if not is_legal_target(a_pp, a_tier, a_abs_lvl, t_pp, t_tier, t_abs_lvl): continue

            has_wall_warning = (alli_clean in alliances_mur_alerte or any(mot in str(t_alliance).lower() for mot in mots_interdits_mur))
            
            pool_candidats.append({
                "name": t_name, "alliance": str(t_alliance), "lvl": t_lvl, "leg": t_leg,
                "tier": t_tier, "abs_lvl": t_abs_lvl, "pp": t_pp, "honor": 0, "loot": 0, "dist": 9999, "x": "???", "y": "???",
                "is_upper_tier": (t_tier > a_tier), "wall_warning": has_wall_warning, "is_ghost": False,
                "peace_disabled_at": t_info.get('peace_disabled_at', "null") 
            })

            if not pool_candidats:
                nom_regle = "Règles Personnelles"
                no_targets_msg = t(langue, "guerre_err_no_target_crit", regle=nom_regle, defaut=f"<:error:1512505075220611172> Aucune cible trouvée respectant les critères (**{nom_regle}**) dans la base de données.")
                if message_to_edit: await message_to_edit.edit(content=no_targets_msg, view=None)
                else: await interaction.followup.send(no_targets_msg)
                return

        random.shuffle(pool_candidats)
        final_targets = []
        chunk_size_api = 5
        
        for k in range(0, len(pool_candidats), chunk_size_api):
            if len(final_targets) >= 10: break
            chunk_candidats = pool_candidats[k:k+chunk_size_api]

            async def fetch_live_target(t_cnd):
                try:
                    async with session_active.get(f"https://api.gge-tracker.com/api/v1/players/{urllib.parse.quote(t_cnd['name'])}", headers=headers, timeout=10) as r:
                        if r.status == 200:
                            d = await r.json()
                            if isinstance(d, list) and d: d = d[0]
                            if isinstance(d, dict):
                                t_cnd['lvl'] = int(d.get('level', t_cnd['lvl']))
                                t_cnd['leg'] = int(d.get('legendary_level', d.get('legendaryLevel', t_cnd['leg'])))
                                t_cnd['pp'] = int(d.get('might_current', d.get('might', t_cnd['pp'])))
                                t_cnd['honor'] = int(d.get('honor', 0)) 
                                t_cnd['loot'] = int(d.get('loot_current', 0))
                                t_cnd['tier'] = get_tier(t_cnd['lvl'], t_cnd['leg'])
                                t_cnd['abs_lvl'] = get_abs_lvl(t_cnd['lvl'], t_cnd['leg'])
                                t_cnd['is_upper_tier'] = (t_cnd['tier'] > a_tier)
                                t_cnd['peace_disabled_at'] = d.get('peace_disabled_at', "null")
                                if "updated_at" in d:
                                    t_cnd["updated_at"] = d["updated_at"]
                            else: t_cnd['is_ghost'] = True
                        elif r.status == 429: await asyncio.sleep(1.5)
                except: pass
                
                if not t_cnd['is_ghost']:
                    try:
                        async with session_active.get(f"https://api.gge-tracker.com/api/v1/castle/search/{urllib.parse.quote(t_cnd['name'])}", headers=headers, timeout=10) as r:
                            if r.status == 200:
                                c_data = await r.json()
                                if isinstance(c_data, dict): c_data = [c_data]
                                if isinstance(c_data, list):
                                    for c in c_data:
                                        if str(c.get('kingdomId', c.get('kingdom_id', 'X'))) == "0" and str(c.get('type', c.get('castle_type', 'X'))) == "1":
                                            tx = c.get('positionX') or c.get('position_x') or c.get('x')
                                            ty = c.get('positionY') or c.get('position_y') or c.get('y')
                                            if tx is not None and ty is not None: 
                                                t_cnd['x'], t_cnd['y'] = str(tx), str(ty)
                                                if a_coords['x'] is not None and a_coords['y'] is not None:
                                                    t_cnd['dist'] = math.sqrt((float(tx) - a_coords['x'])**2 + (float(ty) - a_coords['y'])**2)
                                            break
                            elif r.status == 429: await asyncio.sleep(1.5)
                    except: pass

            await asyncio.gather(*(fetch_live_target(t_cnd) for t_cnd in chunk_candidats))
            
            for t_cnd in chunk_candidats:
                if t_cnd['is_ghost'] or t_cnd['x'] == "???": continue
                
                if is_legal_target(a_pp, a_tier, a_abs_lvl, t_cnd['pp'], t_cnd['tier'], t_cnd['abs_lvl']):
                    if t_cnd['dist'] < config.get("min_distance", 0): 
                        continue
                        
                    # Nouveaux filtres Honneur et Butin
                    if t_cnd['honor'] < config.get("honor_min", 0) or t_cnd['honor'] > config.get("honor_max", 99999999): 
                        continue
                    if t_cnd['loot'] < config.get("loot_min", 0) or t_cnd['loot'] > config.get("loot_max", 999999999): 
                        continue
                        
                    final_targets.append(t_cnd)
            
            await asyncio.sleep(0.15)

        if not final_targets:
            nom_regle = "Règles Personnelles"
            empty_msg = t(langue, "guerre_err_no_target_valid", regle=nom_regle, defaut=f"<:error:1512505075220611172> Les cibles potentielles ne respectent plus les règles (**{nom_regle}**) avec leurs puissances actuelles ou sont hors-ligne.")
            if message_to_edit: await message_to_edit.edit(content=empty_msg, view=None)
            else: await interaction.followup.send(empty_msg)
            return

        actualisation_dt = _get_api_timestamp(a_info, final_targets)

        best_targets = final_targets[:10]
        embeds = []
        chunk_size = 5 
        nb_pages = max(1, (len(best_targets) - 1) // chunk_size + 1)
        
        lbl_alli_target = t(langue, "guerre_cible_alli_target", a=target_alliance, defaut=f" (Alliance : {target_alliance})")
        titre_alliance = lbl_alli_target if target_alliance else ""
        nom_regle_titre = "Règles Personnelles"
        
        lbl_alli = t(langue, "guerre_cible_field_alli", defaut="Alliance :")
        lbl_lvl = t(langue, "guerre_cible_field_lvl", defaut="Niveau :")
        lbl_palier = t(langue, "guerre_cible_field_palier", defaut="Palier")
        lbl_puiss = t(langue, "guerre_cible_field_pp", defaut="Puissance :")
        lbl_honneur = t(langue, "guerre_cible_field_honor", defaut="Honneur :")
        lbl_loot = t(langue, "guerre_cible_field_loot", defaut="Butin :")
        lbl_dist = t(langue, "guerre_cible_field_dist", defaut="Distance :")
        lbl_coords = t(langue, "guerre_cible_field_coords", defaut="Coordonnées :")
        
        for i in range(0, len(best_targets), chunk_size):
            chunk = best_targets[i:i+chunk_size]
            page_num = (i // chunk_size) + 1
            
            titre_emb = t(langue, "guerre_cible_title", r=nom_regle_titre, a=a_name_real, ta=titre_alliance, defaut=f"<:attaque:1512570903886692474> Cibles {nom_regle_titre} pour {a_name_real}{titre_alliance}")
            desc_emb = t(langue, "guerre_cible_desc", pp=format_num(a_pp), t=a_tier, c=len(final_targets), defaut=f"<:pp2:1512571027119538335> Ta Puissance : **{format_num(a_pp)}** | <:lvl:1512571152524906596> Ton Palier : **{a_tier}**\n<:icon_search:1512505406474293438> **{len(final_targets)} cibles valides détectées**.\n\n━━━━━━━━━━━━━━━━━━━━━━━")
            
            embed = discord.Embed(title=titre_emb, color=self.clr_cible)
            
            lbl_date = t(langue, "guerre_lbl_date_data", defaut="⏱️ **Données datées de :**")
            embed.description = f"{lbl_date} <t:{int(actualisation_dt.timestamp())}:F> (<t:{int(actualisation_dt.timestamp())}:R>)\n\n{desc_emb}"

            for j, t_cnd in enumerate(chunk):
                index_global = i + j + 1
                
                dist_str = t(langue, "guerre_dist_lieues", d=int(t_cnd['dist']), defaut=f"{int(t_cnd['dist'])} lieues")
                diff_pp = t_cnd['pp'] - a_pp
                
                txt_plus = t(langue, "guerre_cible_diff_plus", pp=format_num(diff_pp), defaut=f"(+{format_num(diff_pp)} PP)")
                txt_moins = t(langue, "guerre_cible_diff_moins", pp=format_num(diff_pp), defaut=f"({format_num(diff_pp)} PP)")
                diff_txt = txt_plus if diff_pp > 0 else txt_moins
                
                is_under_colombe = False
                if t_cnd.get('peace_disabled_at') and t_cnd['peace_disabled_at'] != "null":
                    try:
                        dt_peace = datetime.fromisoformat(t_cnd['peace_disabled_at'].replace('Z', '+00:00'))
                        if dt_peace > discord.utils.utcnow():
                            is_under_colombe = True
                    except: pass

                target_icon = "<:peace:1512503935892586566>" if is_under_colombe else "<:players:1512504277392953426>"

                warnings = []
                if t_cnd['wall_warning']: warnings.append(t(langue, "guerre_warn_wall", defaut="\n<:error:1512505075220611172> **VÉRIFIEZ LE MUR :** Description d'alliance sensible !"))
                if config.get("level_system") == "tier" and t_cnd['is_upper_tier'] and not config.get("ignore_tiers"): warnings.append(t(langue, "guerre_warn_tier", defaut="\n<:error:1512505075220611172> **RISQUE DE REPRESAILLES :** Joueur du palier supérieur !"))
                if is_under_colombe: warnings.append(t(langue, "guerre_warn_peace", defaut="\n<:peace:1512503935892586566> **JOUEUR SOUS COLOMBE : Protection active (Inattaquable) !**"))
                
                warning_txt = "".join(warnings) if warnings else ""
                
                description_cible = (
                    f"<:icon_alliance:1512573872774451210> {lbl_alli} **{t_cnd['alliance']}**\n"
                    f"<:lvl:1512571152524906596> {lbl_lvl} {t_cnd['lvl']}/{t_cnd['leg']} ({lbl_palier} {t_cnd['tier']})\n"
                    f"<:pp1:1512438903821570160> {lbl_puiss} {format_num(t_cnd['pp'])} {diff_txt}\n"
                    f"<:honor2:1512573861521260544> {lbl_honneur} **{format_num(t_cnd['honor'])}**\n"
                    f"📦 {lbl_loot} **{format_num(t_cnd['loot'])}**\n"
                    f"<:map:1512573907788501242> {lbl_dist} **{dist_str}**\n"
                    f"<:coords:1512574624112578580> {lbl_coords} `{t_cnd['x']}:{t_cnd['y']}`\n"
                    f"{warning_txt}\n━━━━━━━━━━━━━━━━━━━━━━━"
                )
                
                name_cible = t(langue, "guerre_cible_field_title", idx=index_global, n=t_cnd['name'], icon=target_icon, defaut=f"{target_icon} Cible #{index_global} : {t_cnd['name']}")
                embed.add_field(name=name_cible, value=description_cible, inline=False)

            titre_page = t(langue, "guerre_cible_footer_page", cur=page_num, tot=nb_pages, defaut=f"Page {page_num}/{nb_pages}")
            val_spy = t(langue, "guerre_cible_footer_spy", defaut="<:icon_name:1512505444172697611> **SPY OBLIGATOIRE** avant impact.")
            embed.add_field(name=titre_page, value=val_spy, inline=False)
            
            await setup_embed_footer(embed, interaction, langue)
            embeds.append(embed)

        view = CiblePaginationView(self, attacker, sort_by, target_alliance, embeds, langue, owner_id=interaction.user.id)
        
        if message_to_edit:
            await message_to_edit.edit(content=None, embed=embeds[0], view=view)
        else:
            await interaction.followup.send(embed=embeds[0], view=view)
        await prompt_vote_if_lucky(interaction, probability_percent=15, langue=langue)

    # ==========================================
    # ⚖️ MOTEUR D'EXÉCUTION CENTRALISÉ HR
    # ==========================================
    async def _execute_hr(self, interaction: discord.Interaction, attacker: str, defender: str):
        langue, serveur = await get_server_config(interaction)

        if attacker.lower() == defender.lower(): 
            msg = t(langue, "guerre_hr_err_self", defaut="<:error:1512505075220611172> Tu ne peux pas t'attaquer toi-même, voyons ! 😂")
            return await interaction.followup.send(msg)

        user_id = str(interaction.user.id)
        user_rules = utils.USERS_CONFIG_CACHE.get(user_id, {}).get("custom_rules", {})
        
        config = {
            "nom": "Règles Personnelles",
            "level_system": user_rules.get("level_system", "tier"),
            "allowed_tiers_relative": list(range(user_rules.get("tier_diff", 0) + 1)),
            "max_lvl_diff": user_rules.get("max_lvl_diff", 10),
            "pp_offset_min": user_rules.get("pp_min", -3000000),
            "pp_offset_max": user_rules.get("pp_max", 10000000),
            "min_distance": user_rules.get("min_dist", 0),
            "ignore_tiers": user_rules.get("ignore_tiers", False)
        }
        nom_regle_titre = t(langue, "rules_name_perso", defaut="Règles Personnelles")

        cache_data = await get_cached_data(serveur)
        local_data = cache_data.get('players_data', {})

        def get_tier(lvl, leg):
            if lvl < 70: return 0
            if leg <= 120: return 1
            if leg <= 360: return 2
            if leg <= 650: return 3
            if leg <= 949: return 4
            return 5

        a_info, d_info, a_name, d_name = None, None, attacker, defender
        for name, info in local_data.items():
            if name.lower() == attacker.lower(): a_info, a_name = info, name
            if name.lower() == defender.lower(): d_info, d_name = info, name

        if not a_info: return await interaction.followup.send(t(langue, "guerre_hr_err_atk", a=attacker, defaut=f"<:error:1512505075220611172> L'attaquant **{attacker}** est introuvable."))
        if not d_info: return await interaction.followup.send(t(langue, "guerre_hr_err_def", d=defender, defaut=f"<:error:1512505075220611172> Le défenseur **{defender}** est introuvable."))

        a_lvl, a_leg, a_pp = int(a_info.get('level', 0)), int(a_info.get('legendary_level', 0)), int(a_info.get('main_points', 0))
        d_lvl, d_leg, d_pp = int(d_info.get('level', 0)), int(d_info.get('legendary_level', 0)), int(d_info.get('main_points', 0))
        
        a_tier, d_tier = get_tier(a_lvl, a_leg), get_tier(d_lvl, d_leg)
        a_abs_lvl, d_abs_lvl = a_lvl + a_leg, d_lvl + d_leg
        
        a_alli = a_info.get('alliance') or a_info.get('alliance_name')
        if isinstance(a_alli, dict): a_alli = a_alli.get('name')
        d_alli = d_info.get('alliance') or d_info.get('alliance_name')
        if isinstance(d_alli, dict): d_alli = d_alli.get('name')

        a_coords, d_coords = None, None
        headers = await get_api_headers(interaction)
        
        live_castles = []
        for p_name, is_atk in [(a_name, True), (d_name, False)]:
            try:
                async with self.bot.session.get(f"https://api.gge-tracker.com/api/v1/castle/search/{urllib.parse.quote(p_name)}", headers=headers, timeout=5) as r:
                    if r.status == 200:
                        c_data = await r.json()
                        if isinstance(c_data, dict): c_data = [c_data]
                        if isinstance(c_data, list) and c_data:
                            live_castles.append(c_data[0])
                            for c in c_data:
                                if str(c.get('kingdomId', c.get('kingdom_id', 'X'))) == "0" and str(c.get('type', c.get('castle_type', 'X'))) == "1":
                                    x, y = c.get('positionX') or c.get('position_x') or c.get('x'), c.get('positionY') or c.get('position_y') or c.get('y')
                                    if is_atk: a_coords = (int(x), int(y))
                                    else: d_coords = (int(x), int(y))
                                    break
            except: pass

        actualisation_dt = _get_api_timestamp(live_castles)
        distance = math.hypot(d_coords[0] - a_coords[0], d_coords[1] - a_coords[1]) if a_coords and d_coords else None
        
        infractions, avertissements = [], []
        
        if d_alli:
            d_alli_clean = "".join(c for c in str(d_alli).lower() if c.isalnum())
            try:
                fichier_murs = BASE_DATA_PATH / 'murs_scans' / serveur / 'murs_alliances.json'
                if fichier_murs.exists():
                    with open(fichier_murs, encoding='utf-8') as f:
                        for nom_json, desc in json.load(f).items():
                            if "".join(c for c in str(nom_json).lower() if c.isalnum()) == d_alli_clean:
                                desc_mur = str(desc).lower()
                                mot_trouve = next((mot for mot in ["repos", "deuil", "hospitalisé"] if mot in desc_mur), None)
                                if mot_trouve: 
                                    avertissements.append(t(langue, "guerre_hr_diplo_wall", m=mot_trouve.capitalize(), defaut=f"<:alliance_icon:1512574688415580242> **Mur d'alliance** : Mot-clé sensible détecté (**{mot_trouve.capitalize()}**)."))
                                break
            except: pass

        min_dist = config.get("min_distance", 0)
        if distance is not None and distance < min_dist: 
            infractions.append(t(langue, "guerre_hr_dist_short", d=int(distance), m=min_dist, defaut=f"<:icon_search:1512505406474293438> **Distance** : Cible trop proche ! Distance : **{int(distance)} lieues** (Règlement exige Min: {min_dist})."))

        if "pp_offset_min" in config and d_pp < (a_pp + config["pp_offset_min"]):
            infractions.append(t(langue, "guerre_hr_pp_diff_low", d1=format_num(a_pp - d_pp), d2=format_num(abs(config['pp_offset_min'])), defaut=f"<:pp1:1512438903821570160> **Écart de Puissance** : Tu as {format_num(a_pp - d_pp)} PP de plus (L'écart max autorisé vers le bas est de {format_num(abs(config['pp_offset_min']))})."))
        if "pp_offset_max" in config and d_pp > (a_pp + config["pp_offset_max"]):
            avertissements.append(t(langue, "guerre_hr_pp_diff_high", d1=format_num(d_pp - a_pp), defaut=f"<:pp1:1512438903821570160> **Défenseur plus fort** : Le défenseur a {format_num(d_pp - a_pp)} PP de plus que toi. Prudence."))

        if not config.get("ignore_tiers", False):
            if config["level_system"] == "tier":
                diff_tier = d_tier - a_tier
                allowed_tiers = config.get("allowed_tiers_relative", [0])
                
                if diff_tier < min(allowed_tiers):
                    infractions.append(t(langue, "guerre_hr_tier_low", at=a_tier, dt=d_tier, defaut=f"<:lvl:1512571152524906596> **Écart de Palier** : Tu (Palier {a_tier}) n'as pas le droit d'attaquer un joueur de Palier inférieur ({d_tier})."))
                elif diff_tier > max(allowed_tiers):
                    avertissements.append(t(langue, "guerre_hr_tier_high", dt=d_tier, defaut=f"<:lvl:1512571152524906596> **Niveau élevé** : Tu attaques un Palier supérieur ({d_tier}). Risque de représailles."))
            else:
                diff_lvl = d_abs_lvl - a_abs_lvl
                max_diff = config.get("max_lvl_diff", 10)
                
                if diff_lvl > max_diff:
                    avertissements.append(t(langue, "guerre_hr_lvl_high", dl=diff_lvl, md=max_diff, defaut=f"<:lvl:1512571152524906596> **Niveau élevé** : Le défenseur a {diff_lvl} niveaux de plus que toi (Max autorisé: {max_diff}). Risque de représailles."))

        dist_txt = f"{int(distance)} lieues" if distance else t(langue, "guerre_prox_dist_unk", defaut="<:error:1512505075220611172> Inconnue")

        diff_pp = d_pp - a_pp
        if diff_pp > 0:
            diff_txt = t(langue, "guerre_hr_diff_txt_high", pp=format_num(diff_pp), defaut=f"+{format_num(diff_pp)} PP (Défenseur plus fort)")
        elif diff_pp < 0:
            diff_txt = t(langue, "guerre_hr_diff_txt_low", pp=format_num(abs(diff_pp)), defaut=f"-{format_num(abs(diff_pp))} PP (Défenseur plus faible)")
        else:
            diff_txt = t(langue, "guerre_hr_diff_txt_eq", defaut="Égalité stricte")

        embed = discord.Embed(title=t(langue, "guerre_hr_title", r=nom_regle_titre, a=a_name, d=d_name, defaut=f"<:4_:1512574743369224303> Arbitrage {nom_regle_titre} : {a_name} 🆚 {d_name}"), color=self.clr_hr)
        
        a_sa = t(langue, "guerre_sa", defaut="Sans alliance")
        a_alli_txt = a_alli or a_sa
        d_alli_txt = d_alli or a_sa
        
        embed.add_field(name=t(langue, "guerre_hr_field_atk", a=a_name, defaut=f"⚔️ Attaquant : {a_name}"), value=f"<:icon_alliance:1512573872774451210> {a_alli_txt}\n<:lvl:1512571152524906596> Lvl {a_lvl}/{a_leg} (Palier {a_tier})\n<:pp2:1512571027119538335> {format_num(a_pp)} PP", inline=True)
        embed.add_field(name=t(langue, "guerre_hr_field_def", d=d_name, defaut=f"🛡️ Défenseur : {d_name}"), value=f"<:icon_alliance:1512573872774451210> {d_alli_txt}\n<:lvl:1512571152524906596> Lvl {d_lvl}/{d_leg} (Palier {d_tier})\n<:pp2:1512571027119538335> {format_num(d_pp)} PP", inline=True)
        
        lbl_dist_data = t(langue, "guerre_cible_field_dist", defaut="Distance :")
        lbl_diff_data = t(langue, "guerre_hr_field_diff", defaut="Différence :")
        lbl_date = t(langue, "guerre_lbl_date_data", defaut="⏱️ **Données datées de :**")
        
        embed.add_field(
            name=t(langue, "guerre_hr_field_data_title", defaut="📊 Données entre les joueurs"), 
            value=f"{lbl_date} <t:{int(actualisation_dt.timestamp())}:F> (<t:{int(actualisation_dt.timestamp())}:R>)\n<:compass:1512504625364729987> {lbl_dist_data} **{dist_txt}**\n<:pp2:1512571027119538335> {lbl_diff_data} **{diff_txt}**", 
            inline=False
        )
        
        await setup_embed_footer(embed, interaction, langue)

        if infractions:
            embed.color = discord.Color.red()
            embed.add_field(name=t(langue, "guerre_hr_res_red_t", defaut="❌ HORS RÈGLES (HR)"), value=t(langue, "guerre_hr_res_red_d", defaut="__L'attaque est formellement interdite selon ton profil :__\n\n") + "\n".join([f"• {i}" for i in infractions]), inline=False)
            if avertissements:
                embed.add_field(name=t(langue, "guerre_hr_res_warn", defaut="⚠️ Autres observations"), value="\n".join([f"• {a}" for a in avertissements]), inline=False)
            await interaction.followup.send(embed=embed)
        elif avertissements:
            embed.color = discord.Color.orange()
            embed.add_field(name=t(langue, "guerre_hr_res_ora_t", defaut="⚠️ ATTAQUE EN RÈGLES (Mais Risquée)"), value=t(langue, "guerre_hr_res_ora_d", defaut="__L'attaque respecte tes limites, mais attention :__\n\n") + "\n".join([f"• {a}" for a in avertissements]), inline=False)
            await interaction.followup.send(embed=embed)
        else:
            embed.color = discord.Color.green()
            embed.add_field(name=t(langue, "guerre_hr_res_gre_t", defaut="✅ ATTAQUE EN RÈGLES"), value=t(langue, "guerre_hr_res_gre_d", defaut="Aucune infraction ni avertissement détecté selon tes limites."), inline=False)
            await interaction.followup.send(embed=embed)
        await prompt_vote_if_lucky(interaction, probability_percent=8, langue=langue)

async def setup(bot: commands.Bot):
    await bot.add_cog(GuerreCog(bot))
import discord

from utils import DICT_EMOJIS, load_surveillance_async, save_surveillance_async, t


class RadarSettingsView(discord.ui.View):
    def __init__(
        self, p_id: str, entity_id: str, entity_type: str, player_name: str, initial_prefs: dict, langue: str = "fr"
    ):
        super().__init__(timeout=900)
        self.p_id = p_id
        self.entity_id = str(entity_id)
        self.entity_type = entity_type
        self.player_name = player_name
        self.prefs = initial_prefs
        self.langue = langue

        self.btn_pseudo.label = t(langue, "rad_btn_pseudo", defaut="Pseudo")
        self.btn_position.label = t(langue, "rad_btn_pos", defaut="Position")
        self.btn_alliance.label = t(langue, "rad_btn_alli", defaut="Alliance")
        self.btn_puissance.label = t(langue, "rad_btn_pp", defaut="Puissance")
        self.btn_colombe.label = t(langue, "rad_btn_dove", defaut="Colombe")
        self.btn_fermer.label = t(langue, "rad_btn_close", defaut="Terminer et Fermer")
        self.btn_fermer.emoji = DICT_EMOJIS.get("e_check", "✅")

        self.update_buttons_state()

    async def toggle_pref(self, pref_key: str):
        data = await load_surveillance_async()
        dict_key = "abonnes" if self.entity_type == "user" else "guild_abonnes"

        if self.p_id in data.get("players", {}) and self.entity_id in data["players"][self.p_id].get(dict_key, {}):
            current = data["players"][self.p_id][dict_key][self.entity_id].get(pref_key, False)
            new_val = not current
            data["players"][self.p_id][dict_key][self.entity_id][pref_key] = new_val
            self.prefs[pref_key] = new_val
            await save_surveillance_async(data)

    def update_buttons_state(self):
        self.btn_pseudo.style = discord.ButtonStyle.success if self.prefs.get("pseudo") else discord.ButtonStyle.danger
        self.btn_position.style = (
            discord.ButtonStyle.success if self.prefs.get("position") else discord.ButtonStyle.danger
        )
        self.btn_alliance.style = (
            discord.ButtonStyle.success if self.prefs.get("alliance") else discord.ButtonStyle.danger
        )
        self.btn_puissance.style = (
            discord.ButtonStyle.success if self.prefs.get("puissance") else discord.ButtonStyle.danger
        )
        self.btn_colombe.style = (
            discord.ButtonStyle.success if self.prefs.get("colombe") else discord.ButtonStyle.danger
        )

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True

    @discord.ui.button(custom_id="pref_pseudo", row=0)
    async def btn_pseudo(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.toggle_pref("pseudo")
        self.update_buttons_state()
        await interaction.response.edit_message(view=self)

    @discord.ui.button(custom_id="pref_position", row=0)
    async def btn_position(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.toggle_pref("position")
        self.update_buttons_state()
        await interaction.response.edit_message(view=self)

    @discord.ui.button(custom_id="pref_alliance", row=0)
    async def btn_alliance(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.toggle_pref("alliance")
        self.update_buttons_state()
        await interaction.response.edit_message(view=self)

    @discord.ui.button(custom_id="pref_puissance", row=1)
    async def btn_puissance(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.toggle_pref("puissance")
        self.update_buttons_state()
        await interaction.response.edit_message(view=self)

    @discord.ui.button(custom_id="pref_colombe", row=1)
    async def btn_colombe(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.toggle_pref("colombe")
        self.update_buttons_state()
        await interaction.response.edit_message(view=self)

    @discord.ui.button(style=discord.ButtonStyle.secondary, row=2)
    async def btn_fermer(self, interaction: discord.Interaction, button: discord.ui.Button):
        target = (
            t(self.langue, "rad_target_guild", defaut="Le radar de serveur")
            if self.entity_type == "guild"
            else t(self.langue, "rad_target_user", defaut="Le radar personnel")
        )
        e_check = DICT_EMOJIS.get("e_check", "✅")
        msg = t(
            self.langue,
            "rad_prefs_saved",
            target=target,
            defaut=f"{e_check} **Préférences enregistrées !** {target} est actif.",
        )
        await interaction.response.edit_message(content=msg, view=None)


class RadarAllianceSettingsView(discord.ui.View):
    def __init__(
        self, a_id: str, entity_id: str, entity_type: str, alliance_name: str, initial_prefs: dict, langue: str = "fr"
    ):
        super().__init__(timeout=900)
        self.a_id = a_id
        self.entity_id = str(entity_id)
        self.entity_type = entity_type
        self.alliance_name = alliance_name
        self.prefs = initial_prefs
        self.langue = langue

        self.btn_mouvements.label = t(langue, "rad_alli_btn_mouv", defaut="Entrées/Sorties")
        self.btn_rangs.label = t(langue, "rad_alli_btn_rangs", defaut="Promotions/Rétrogradations")
        self.btn_infos.label = t(langue, "rad_alli_btn_infos", defaut="Changement Nom/Chef")
        self.btn_fermer.label = t(langue, "rad_btn_close", defaut="Terminer et Fermer")
        self.btn_fermer.emoji = DICT_EMOJIS.get("e_check", "✅")

        self.update_buttons_state()

    async def toggle_pref(self, pref_key: str):
        data = await load_surveillance_async()
        dict_key = "abonnes" if self.entity_type == "user" else "guild_abonnes"

        if self.a_id in data.get("alliances", {}) and self.entity_id in data["alliances"][self.a_id].get(dict_key, {}):
            current = data["alliances"][self.a_id][dict_key][self.entity_id].get(pref_key, False)
            new_val = not current
            data["alliances"][self.a_id][dict_key][self.entity_id][pref_key] = new_val
            self.prefs[pref_key] = new_val
            await save_surveillance_async(data)

    def update_buttons_state(self):
        self.btn_mouvements.style = (
            discord.ButtonStyle.success if self.prefs.get("mouvements") else discord.ButtonStyle.danger
        )
        self.btn_rangs.style = discord.ButtonStyle.success if self.prefs.get("rangs") else discord.ButtonStyle.danger
        self.btn_infos.style = discord.ButtonStyle.success if self.prefs.get("infos") else discord.ButtonStyle.danger

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True

    @discord.ui.button(custom_id="pref_alli_mouv", row=0)
    async def btn_mouvements(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.toggle_pref("mouvements")
        self.update_buttons_state()
        await interaction.response.edit_message(view=self)

    @discord.ui.button(custom_id="pref_alli_rangs", row=0)
    async def btn_rangs(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.toggle_pref("rangs")
        self.update_buttons_state()
        await interaction.response.edit_message(view=self)

    @discord.ui.button(custom_id="pref_alli_infos", row=0)
    async def btn_infos(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.toggle_pref("infos")
        self.update_buttons_state()
        await interaction.response.edit_message(view=self)

    @discord.ui.button(style=discord.ButtonStyle.secondary, row=2)
    async def btn_fermer(self, interaction: discord.Interaction, button: discord.ui.Button):
        target = (
            t(self.langue, "rad_target_guild", defaut="Le radar de serveur")
            if self.entity_type == "guild"
            else t(self.langue, "rad_target_global", defaut="Le radar global")
        )
        e_check = DICT_EMOJIS.get("e_check", "✅")
        msg = t(
            self.langue,
            "rad_prefs_saved",
            target=target,
            defaut=f"{e_check} **Préférences enregistrées !** {target} est actif.",
        )
        await interaction.response.edit_message(content=msg, view=None)

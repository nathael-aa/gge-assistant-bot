# -*- coding: utf-8 -*-
import os
import json
import logging
import discord
from discord import app_commands
from discord.ext import commands

# 🛠️ On importe nos outils magiques depuis utils.py
from utils import BASE_DATA_PATH, joueur_autocomplete

logger = logging.getLogger("GGE_Bot")

SANCTIONS_FILE = BASE_DATA_PATH / 'sanctions.json'

def load_sanctions():
    if os.path.exists(SANCTIONS_FILE):
        with open(SANCTIONS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

def save_sanctions(data):
    with open(SANCTIONS_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

# 📦 Création du "Cog" (Le module qui contient le groupe de commandes)
@app_commands.guild_only()
class SanctionsCog(commands.GroupCog, group_name="sanction", group_description="⚠️ Gestion des avertissements (Local au serveur)"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        super().__init__()

    # --- COMMANDE : ADD ---
    @app_commands.command(name="add", description="Ajoute un avertissement au dossier d'un joueur")
    @app_commands.default_permissions(manage_messages=True)
    @app_commands.autocomplete(joueur=joueur_autocomplete)
    @app_commands.describe(raison="La raison de la sanction (ex: 0 pts nomades, HR, Inactif...)")
    async def s_add(self, interaction: discord.Interaction, joueur: str, raison: str):
        data = load_sanctions()
        guild_id = str(interaction.guild_id)
        
        if guild_id not in data:
            data[guild_id] = {}
            
        joueur_lower = joueur.lower()
        
        if joueur_lower not in data[guild_id]:
            data[guild_id][joueur_lower] = {"nom_reel": joueur, "dossiers": []}
            
        dossier = {
            "date": discord.utils.utcnow().strftime("%d/%m/%Y"),
            "raison": raison,
            "auteur": interaction.user.name
        }
        
        data[guild_id][joueur_lower]["dossiers"].append(dossier)
        save_sanctions(data)
        
        nb_avertissements = len(data[guild_id][joueur_lower]["dossiers"])
        
        embed = discord.Embed(title="⚠️ Nouvel Avertissement Enregistré", color=discord.Color.red())
        embed.add_field(name="Joueur", value=f"**{joueur}**", inline=True)
        embed.add_field(name="Total Avertissements", value=f"**{nb_avertissements}**", inline=True)
        embed.add_field(name="Motif", value=raison, inline=False)
        embed.set_footer(text=f"Sanction émise sur ce serveur par {interaction.user.name}")
        
        logger.info(f"⚠️ Sanction : {joueur} averti par {interaction.user.name} sur le serveur {guild_id}")
        await interaction.response.send_message(embed=embed)

    # --- COMMANDE : ADD GROUPE ---
    @app_commands.command(name="add_groupe", description="Ajoute un avertissement à plusieurs joueurs (jusqu'à 5)")
    @app_commands.default_permissions(manage_messages=True)
    @app_commands.autocomplete(joueur1=joueur_autocomplete)
    @app_commands.autocomplete(joueur2=joueur_autocomplete)
    @app_commands.autocomplete(joueur3=joueur_autocomplete)
    @app_commands.autocomplete(joueur4=joueur_autocomplete)
    @app_commands.autocomplete(joueur5=joueur_autocomplete)
    @app_commands.describe(
        raison="La raison de la sanction pour tout le groupe",
        joueur1="Premier joueur à sanctionner",
        joueur2="Deuxième joueur (Optionnel)"
    )
    async def s_add_groupe(self, interaction: discord.Interaction, raison: str, joueur1: str, joueur2: str = None, joueur3: str = None, joueur4: str = None, joueur5: str = None):
        data = load_sanctions()
        guild_id = str(interaction.guild_id)
        
        if guild_id not in data:
            data[guild_id] = {}
            
        liste_joueurs = [j for j in [joueur1, joueur2, joueur3, joueur4, joueur5] if j]
        liste_joueurs = list(set(liste_joueurs))
        
        date_jour = discord.utils.utcnow().strftime("%d/%m/%Y")
        auteur = interaction.user.name
        
        joueurs_sanctionnes = []
        
        for joueur in liste_joueurs:
            joueur_lower = joueur.lower()
            if joueur_lower not in data[guild_id]:
                data[guild_id][joueur_lower] = {"nom_reel": joueur, "dossiers": []}
                
            dossier = {"date": date_jour, "raison": raison, "auteur": auteur}
            data[guild_id][joueur_lower]["dossiers"].append(dossier)
            
            nb_avertissements = len(data[guild_id][joueur_lower]["dossiers"])
            joueurs_sanctionnes.append(f"🔹 **{joueur}** *(Total: {nb_avertissements})*")
            logger.info(f"⚠️ Sanction Groupe : {joueur} averti par {auteur} sur le serveur {guild_id}")
            
        save_sanctions(data)
        
        embed = discord.Embed(
            title="⚠️ Sanction de Groupe Appliquée", 
            description=f"**Motif global :** {raison}\n\n**{len(liste_joueurs)} joueur(s) sanctionné(s) :**\n" + "\n".join(joueurs_sanctionnes),
            color=discord.Color.red()
        )
        embed.set_footer(text=f"Action groupée réalisée par {auteur}")
        await interaction.response.send_message(embed=embed)

    # --- COMMANDE : LIST ---
    @app_commands.command(name="list", description="Affiche le casier d'un joueur ou de tous les joueurs sanctionnés")
    @app_commands.autocomplete(joueur=joueur_autocomplete)
    @app_commands.describe(joueur="Laisse vide pour voir tous les joueurs sanctionnés")
    async def s_list(self, interaction: discord.Interaction, joueur: str = None):
        data = load_sanctions()
        guild_id = str(interaction.guild_id)
        
        if guild_id not in data or not data[guild_id]:
            return await interaction.response.send_message("📭 Le registre des sanctions de ce serveur est totalement vierge.", ephemeral=True)
            
        if joueur:
            joueur_lower = joueur.lower()
            if joueur_lower not in data[guild_id] or not data[guild_id][joueur_lower]["dossiers"]:
                return await interaction.response.send_message(f"✅ Le casier de **{joueur}** est vierge sur ce serveur.", ephemeral=True)
                
            dossiers = data[guild_id][joueur_lower]["dossiers"]
            nom_reel = data[guild_id][joueur_lower]["nom_reel"]
            
            embed = discord.Embed(title=f"📜 Casier de {nom_reel}", description=f"Total : **{len(dossiers)} avertissement(s)**", color=discord.Color.orange())
            for i, d in enumerate(dossiers, 1):
                embed.add_field(name=f"Avertissement #{i} ({d['date']})", value=f"**Motif :** {d['raison']}\n*Saisi par : {d['auteur']}*", inline=False)
                
            await interaction.response.send_message(embed=embed)
        else:
            embed = discord.Embed(title="⚠️ Registre des Sanctions", description="Liste détaillée de tous les avertissements :", color=discord.Color.orange())
            
            for j_lower, infos in data[guild_id].items():
                dossiers = infos["dossiers"]
                nb = len(dossiers)
                if nb > 0:
                    details_txt = ""
                    for i, d in enumerate(dossiers, 1):
                        details_txt += f"🔹 **{d['date']}** par *{d['auteur']}* : {d['raison']}\n"
                    if len(details_txt) > 1024:
                        details_txt = details_txt[:1000] + "...\n*(Suite tronquée)*"
                        
                    embed.add_field(name=f"👤 {infos['nom_reel']} ({nb})", value=details_txt, inline=False)
            
            if len(embed.fields) == 0:
                embed.description = "Aucun joueur n'est actuellement sanctionné."
                
            await interaction.response.send_message(embed=embed)

    # --- COMMANDE : REMOVE ---
    @app_commands.command(name="remove", description="Pardonne un joueur (efface un avertissement précis ou tout)")
    @app_commands.default_permissions(manage_messages=True)
    @app_commands.autocomplete(joueur=joueur_autocomplete)
    @app_commands.describe(
        joueur="Le joueur à pardonner",
        id_sanction="Numéro de l'avertissement. Laisse vide pour TOUT effacer !"
    )
    async def s_remove(self, interaction: discord.Interaction, joueur: str, id_sanction: int = None):
        data = load_sanctions()
        guild_id = str(interaction.guild_id)
        joueur_lower = joueur.lower()
        
        if guild_id in data and joueur_lower in data[guild_id] and data[guild_id][joueur_lower]["dossiers"]:
            dossiers = data[guild_id][joueur_lower]["dossiers"]
            
            if id_sanction is not None:
                if 1 <= id_sanction <= len(dossiers):
                    removed = dossiers.pop(id_sanction - 1)
                    if not dossiers:
                        del data[guild_id][joueur_lower]
                        
                    save_sanctions(data)
                    logger.info(f"🕊️ Sanction : Avertissement #{id_sanction} retiré pour {joueur} par {interaction.user.name} (Serveur {guild_id})")
                    await interaction.response.send_message(f"✅ L'avertissement **#{id_sanction}** (*{removed['raison']}*) de **{joueur}** a été effacé.", ephemeral=False)
                else:
                    await interaction.response.send_message(f"⚠️ Numéro invalide. **{joueur}** possède **{len(dossiers)}** avertissement(s).", ephemeral=True)
            else:
                del data[guild_id][joueur_lower]
                save_sanctions(data)
                logger.info(f"🕊️ Sanction : Amnistie totale accordée à {joueur} par {interaction.user.name} (Serveur {guild_id})")
                await interaction.response.send_message(f"🕊️ Amnistie totale ! Le casier de **{joueur}** a été entièrement effacé.", ephemeral=False)
        else:
            await interaction.response.send_message(f"⚠️ **{joueur}** n'a aucun avertissement à effacer sur ce serveur.", ephemeral=True)

# 🔌 Fonction obligatoire pour brancher le fichier au bot principal
async def setup(bot: commands.Bot):
    await bot.add_cog(SanctionsCog(bot))
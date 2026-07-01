# -*- coding: utf-8 -*-
import os
import re

DOSSIER_A_SCANNER = "./cogs"

pattern_defaut = re.compile(r'defaut\s*=\s*f?(["\']).*?\1')
pattern_accents = re.compile(r'[éèêëàâäôöûüùïîçÉÈÊËÀÂÄÔÖÛÜÙÏÎÇ]')

print("🔍 DÉBUT DU SCAN ANTI-TEXTE BRUT...\n" + "="*50)

fichiers_suspects = 0
total_alertes = 0

for root, dirs, files in os.walk(DOSSIER_A_SCANNER):
    if '__pycache__' in root:
        continue
        
    for file in files:
        if file.endswith(".py"):
            filepath = os.path.join(root, file)
            with open(filepath, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            
            alertes_fichier = []
            in_docstring = False # Permet de suivre si on est à l'intérieur d'un bloc """
            
            for i, line in enumerate(lines):
                ligne_strip = line.strip()
                
                # 1. GESTION DES DOCSTRINGS (""")
                if '"""' in ligne_strip or "'''" in ligne_strip:
                    # Si les guillemets s'ouvrent et se ferment sur la même ligne
                    if ligne_strip.count('"""') >= 2 or ligne_strip.count("'''") >= 2:
                        continue
                    # Sinon, on bascule le mode "Dans un docstring"
                    in_docstring = not in_docstring
                    continue
                    
                if in_docstring:
                    continue
                    
                # 2. ENLEVER LES COMMENTAIRES (#)
                # On coupe la ligne au premier '#' et on ne garde que ce qui est avant
                ligne_sans_commentaires = line.split('#')[0].strip()
                
                if not ligne_sans_commentaires:
                    continue
                    
                # 3. IGNORER LES LIGNES SYSTÈMES ET MENUS DISCORD
                if 'logger.' in ligne_sans_commentaires or 'print(' in ligne_sans_commentaires:
                    continue
                if 'app_commands.Choice' in ligne_sans_commentaires:
                    continue
                    
                # 4. ENLEVER LES TEXTES DE SECOURS (defaut=...)
                ligne_nettoyee = re.sub(pattern_defaut, '', ligne_sans_commentaires)
                
                # 5. VÉRIFICATION FINALE DES ACCENTS
                if pattern_accents.search(ligne_nettoyee):
                    alertes_fichier.append((i + 1, ligne_strip))
            
            if alertes_fichier:
                fichiers_suspects += 1
                total_alertes += len(alertes_fichier)
                print(f"\n📂 Fichier : {filepath}")
                for ligne_num, contenu in alertes_fichier:
                    print(f"   Ligne {ligne_num:03d} | 👉 {contenu}")

print("\n" + "="*50)
print(f"✅ SCAN TERMINÉ : {total_alertes} VRAIES alertes trouvées dans {fichiers_suspects} fichiers.")
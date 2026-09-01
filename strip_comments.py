import os
import tokenize
import io
from pathlib import Path

# Dossier source (ton code commenté) et dossier cible (code à pusher)
SOURCE_DIR = Path("cogs")
TARGET_DIR = Path("production/cogs")

def remove_comments(file_path):
    with open(file_path, 'r', encoding='utf-8') as f:
        source_code = f.read()

    result = []
    # Tokenize lit le code de manière intelligente (comprend la syntaxe Python)
    tokens = tokenize.generate_tokens(io.StringIO(source_code).readline)
    
    for toknum, tokval, _, _, _ in tokens:
        if toknum != tokenize.COMMENT:  # On ignore les tokens de type COMMENTAIRE
            result.append((toknum, tokval))
            
    return tokenize.untokenize(result)

def build():
    TARGET_DIR.mkdir(parents=True, exist_ok=True)
    
    for py_file in SOURCE_DIR.glob("*.py"):
        clean_code = remove_comments(py_file)
        
        target_file = TARGET_DIR / py_file.name
        with open(target_file, 'w', encoding='utf-8') as f:
            f.write(clean_code)
        print(f"✅ Nettoyé : {py_file.name}")

if __name__ == "__main__":
    build()
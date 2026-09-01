import os
import tokenize
import io
from pathlib import Path

TARGET_DIR = Path("production")

def remove_comments(file_path):
    with open(file_path, 'r', encoding='utf-8') as f:
        source_code = f.read()

    result = []
    tokens = tokenize.generate_tokens(io.StringIO(source_code).readline)
    
    for toknum, tokval, _, _, _ in tokens:
        if toknum != tokenize.COMMENT:
            result.append((toknum, tokval))
            
    return tokenize.untokenize(result)

def build():
    # .rglob("*.py") cherche dans TOUS les sous-dossiers
    for py_file in Path(".").rglob("*.py"):
        
        # On ignore le dossier de destination et le script lui-même
        if "production" in py_file.parts or py_file.name == "strip_comments.py" or ".github" in py_file.parts:
            continue
            
        clean_code = remove_comments(py_file)
        
        # Recrée les sous-dossiers exacts (ex: production/cogs/, production/data/, etc.)
        target_file = TARGET_DIR / py_file
        target_file.parent.mkdir(parents=True, exist_ok=True)
        
        with open(target_file, 'w', encoding='utf-8') as f:
            f.write(clean_code)
        print(f"✅ Nettoyé : {py_file}")

if __name__ == "__main__":
    build()
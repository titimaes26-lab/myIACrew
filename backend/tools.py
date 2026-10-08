import ast
import json
import yaml
from crewai.tools import tool

from delimiter_scan import scan_delimiters

def check_syntax_content(content: str, file_path: str) -> str:
    """Implémentation réelle de check_syntax (voir sa docstring pour le détail par extension).
    Factorée en fonction Python pure pour être appelable directement — par l'agent (via l'outil
    check_syntax ci-dessous, un objet Tool crewai qui n'est lui-même PAS directement appelable
    comme une fonction) OU en interne par github_write_file/github_write_files (voir
    github_guards.py, _reject_invalid_syntax) comme garde-fou avant un commit, sans dépendre de
    l'objet Tool de crewai ni de son compteur d'usage partagé (qui serait sinon incrémenté par
    les deux appelants indifféremment).
    """
    ext = file_path.rsplit(".", 1)[-1].lower() if "." in file_path else ""

    if ext == "py":
        try:
            ast.parse(content)
            return f"OK : '{file_path}' est syntaxiquement valide (vérification Python exacte via ast.parse)."
        except SyntaxError as e:
            return f"ERREUR_SYNTAXE : '{file_path}' ligne {e.lineno} : {e.msg}"

    if ext == "json":
        try:
            json.loads(content)
            return f"OK : '{file_path}' est un JSON valide."
        except json.JSONDecodeError as e:
            return f"ERREUR_SYNTAXE : '{file_path}' ligne {e.lineno} : {e.msg}"

    if ext in ("yaml", "yml"):
        try:
            yaml.safe_load(content)
            return f"OK : '{file_path}' est un YAML valide."
        except Exception as e:
            return f"ERREUR_SYNTAXE : '{file_path}' : {e}"

    if ext in ("ts", "tsx", "js", "jsx"):
        issues, truncated = scan_delimiters(content)
        if truncated:
            return (
                f"ERREUR_SYNTAXE : '{file_path}' semble tronqué (coupé en fin de fichier) :\n" + "\n".join(issues)
            )
        if issues:
            return (
                f"PROBLÈME(S) DÉTECTÉ(S) dans '{file_path}' (vérification heuristique, pas une vraie "
                f"compilation {ext.upper()}) :\n" + "\n".join(issues)
            )
        return (
            f"OK (heuristique) : '{file_path}' a des accolades/parenthèses/crochets/guillemets "
            "équilibrés. Ceci ne remplace PAS une vraie compilation TypeScript ni un lint : signale "
            "toute incertitude restante dans ton rapport plutôt que de garantir l'absence de bug."
        )

    return f"INFO : type de fichier '.{ext}' non pris en charge par cette vérification statique pour '{file_path}'."

@tool("check_syntax")
def check_syntax(content: str, file_path: str) -> str:
    """
    Vérifie STATIQUEMENT (sans jamais exécuter le code) la validité syntaxique d'un
    contenu de fichier déjà récupéré (via l'outil de lecture local ou github_read_file).
    Pour Python/JSON/YAML, la vérification est exacte (vrai parseur). Pour
    JS/TS/JSX/TSX, c'est une heuristique d'équilibrage de délimiteurs, pas une vraie
    compilation : à utiliser en complément de ta relecture, jamais comme preuve
    absolue d'absence de bug.
    Arguments:
        content (str): le contenu du fichier à vérifier (pas un chemin).
        file_path (str): chemin/nom du fichier, utilisé uniquement pour déduire son
            type (ex: 'src/App.tsx', 'backend/main.py', 'config.json').
    """
    return check_syntax_content(content, file_path)
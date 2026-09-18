import ast
import json
import os
import yaml
from crewai.tools import tool

@tool("read_a_files_content")
def read_a_files_content(file_path: str) -> str:
    """
    Lit le contenu d'un fichier sur le disque.
    Arguments:
        file_path (str): Le chemin du fichier (ex: 'src/App.tsx' ou 'index.html').
    """
    try:
        normalized_path = os.path.normpath(file_path)
        
        if not os.path.exists(normalized_path):
            return (
                f"ERREUR_FICHIER_INEXISTANT : Le fichier '{file_path}' n'existe pas sur le disque. "
                "Inutile de réessayer la lecture de ce fichier exact. "
                "Consigne : Note cette absence dans ton rapport et poursuis ton analyse."
            )
            
        with open(normalized_path, "r", encoding="utf-8") as f:
            content = f.read()
            return content if content.strip() else f"INFO : Le fichier '{file_path}' est vide."
            
    except Exception as e:
        return (
            f"ERREUR_LECTURE : Impossible de lire le fichier '{file_path}'. "
            f"Détail : {str(e)}. Ne réessaie pas d'ouvrir ce fichier."
        )

_REGEX_LITERAL_PRECEDERS = set('([{,;:=!&|?+-*%^~<>') | {''}
# Mots-clés après lesquels un littéral regex peut suivre sans parenthèse/opérateur
# entre les deux (ex: `return /regex/.test(x);`) : après le dernier caractère de ces
# mots, last_significant serait sinon une simple lettre (absente de _REGEX_LITERAL_PRECEDERS),
# et le "/" suivant serait à tort traité comme une division plutôt qu'un littéral regex.
_REGEX_KEYWORD_PRECEDERS = {'return', 'typeof', 'instanceof', 'in', 'of', 'new', 'yield', 'throw', 'delete', 'void', 'case'}

def _check_balanced_delimiters(content: str) -> list[str]:
    """Vérifie l'équilibre des accolades/parenthèses/crochets/guillemets d'un contenu.

    Heuristique volontairement simple (pas un vrai parseur JS/TS/JSX) : suffisante pour
    repérer une bonne partie des erreurs de génération les plus courantes (accolade ou
    parenthèse manquante en fin de fichier, chaîne non terminée), mais ne remplace pas
    une vraie compilation TypeScript ou un lint. Ignore le contenu des commentaires
    // et /* */ : sans ça, une simple apostrophe dans un commentaire en français
    ("n'existe pas", comme dans ce dépôt) serait à tort lue comme un guillemet ouvrant
    une chaîne, et ferait remonter une fausse erreur sur du code par ailleurs valide.
    Reconnaît aussi les littéraux regex (ex: `/['"]/g`, y compris après un mot-clé comme
    `return`/`typeof` sans parenthèse) pour ne pas confondre les guillemets qu'ils
    contiennent avec de vraies chaînes : sans ça, un seul `.replace(/['"]/g, '')` ou
    `return /['"]/g.test(x)` désynchronisait le suivi des chaînes pour tout le reste du
    fichier. Limitation connue (pas une régression) : un littéral regex qui ne suit ni un
    symbole/mot-clé de _REGEX_LITERAL_PRECEDERS/_REGEX_KEYWORD_PRECEDERS ni un début de
    fichier peut ne pas être reconnu.
    Les interpolations `${...}` dans les template literals ne sont pas analysées comme
    du code (limitation connue de cette heuristique, pas une régression : leur contenu
    est traité comme faisant partie de la chaîne, comme le reste du template literal).
    """
    pairs = {')': '(', ']': '[', '}': '{'}
    opening = set(pairs.values())
    stack: list[str] = []
    in_string = None
    escaped = False
    in_line_comment = False
    in_block_comment = False
    issues: list[str] = []
    last_significant = ''
    word_buf: list[str] = []

    i = 0
    n = len(content)
    while i < n:
        ch = content[i]

        if in_line_comment:
            if ch == '\n':
                in_line_comment = False
            i += 1
            continue

        if in_block_comment:
            if ch == '*' and i + 1 < n and content[i + 1] == '/':
                in_block_comment = False
                i += 2
                continue
            i += 1
            continue

        if in_string:
            if escaped:
                escaped = False
            elif ch == '\\':
                escaped = True
            elif ch == in_string:
                in_string = None
                last_significant = ch
            i += 1
            continue

        if ch == '/' and i + 1 < n and content[i + 1] == '/':
            in_line_comment = True
            word_buf = []
            i += 2
            continue
        if ch == '/' and i + 1 < n and content[i + 1] == '*':
            in_block_comment = True
            word_buf = []
            i += 2
            continue

        if ch == '/' and last_significant in _REGEX_LITERAL_PRECEDERS:
            j = i + 1
            in_char_class = False
            while j < n and content[j] != '\n':
                cj = content[j]
                if cj == '\\':
                    j += 2
                    continue
                if cj == '[':
                    in_char_class = True
                elif cj == ']':
                    in_char_class = False
                elif cj == '/' and not in_char_class:
                    break
                j += 1
            if j < n and content[j] == '/':
                i = j + 1
                while i < n and content[i].isalpha():
                    i += 1
                last_significant = '/'
                word_buf = []
                continue
            # Pas de "/" fermant sur la même ligne : probablement pas un littéral regex
            # (une regex ne s'étend jamais sur plusieurs lignes), on retombe sur le
            # traitement normal du caractère ci-dessous.

        if ch.isalnum() or ch == '_':
            word_buf.append(ch)
        else:
            if word_buf:
                if ''.join(word_buf) in _REGEX_KEYWORD_PRECEDERS:
                    # Comme un début d'expression : le "/" qui suivra (après d'éventuels
                    # espaces, ignorés ci-dessous) doit être traité comme un littéral regex.
                    last_significant = ''
                word_buf = []

            if ch in ('"', "'", '`'):
                in_string = ch
            elif ch in opening:
                stack.append(ch)
            elif ch in pairs:
                if not stack or stack[-1] != pairs[ch]:
                    line = content[:i].count('\n') + 1
                    issues.append(f"'{ch}' inattendu ligne {line} (aucune ouverture correspondante).")
                    if len(issues) >= 5:
                        break
                else:
                    stack.pop()

        if not ch.isspace():
            last_significant = ch

        i += 1

    if in_string:
        issues.append(f"Chaîne de caractères non terminée (ouverte avec {in_string}).")
    if in_block_comment:
        issues.append("Commentaire /* ... */ non terminé.")
    for ch in stack:
        issues.append(f"'{ch}' jamais refermé.")

    return issues

@tool("check_syntax")
def check_syntax(content: str, file_path: str) -> str:
    """
    Vérifie STATIQUEMENT (sans jamais exécuter le code) la validité syntaxique d'un
    contenu de fichier déjà récupéré (via read_a_files_content ou github_read_file).
    Pour Python/JSON/YAML, la vérification est exacte (vrai parseur). Pour
    JS/TS/JSX/TSX, c'est une heuristique d'équilibrage de délimiteurs, pas une vraie
    compilation : à utiliser en complément de ta relecture, jamais comme preuve
    absolue d'absence de bug.
    Arguments:
        content (str): le contenu du fichier à vérifier (pas un chemin).
        file_path (str): chemin/nom du fichier, utilisé uniquement pour déduire son
            type (ex: 'src/App.tsx', 'backend/main.py', 'config.json').
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
        issues = _check_balanced_delimiters(content)
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
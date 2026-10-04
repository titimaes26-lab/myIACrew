"""Contrôle des imports relatifs des fichiers livrés : le module visé existe-t-il, exporte-t-il le nom importé."""
import posixpath
import re
from typing import Callable
import analyst_blocks

# --- Cohérence des imports entre fichiers livrés -------------------------------------------------
CODE_EXTENSIONS = {"ts", "tsx", "js", "jsx"}

_IMPORT_FROM = re.compile(
    r"""import\s+(?:type\s+)?([\w$]+)?\s*,?\s*(?:\{([^}]*)\})?\s*(?:\*\s+as\s+[\w$]+)?\s*from\s*['"](\.{1,2}/[^'"]+)['"]""",
    re.DOTALL,
)

_IMPORT_SIDE_EFFECT = re.compile(r"""^\s*import\s*['"](\.{1,2}/[^'"]+)['"]""", re.MULTILINE)

_EXPORT_DECL = re.compile(
    r"export\s+(?:declare\s+)?(?:async\s+)?(?:const|let|var|function\*?|class|abstract\s+class|type|interface|enum)\s+([\w$]+)"
)

_EXPORT_LIST = re.compile(r"export\s*(?:type\s*)?\{([^}]*)\}")

_EXPORT_DESTRUCTURED = re.compile(r"export\s+(?:const|let|var)\s*[{\[]([^}\]]*)[}\]]")

_RESOLVE_SUFFIXES = (".ts", ".tsx", ".js", ".jsx", ".json", ".css")

_ASSET_EXTENSIONS = {"css", "scss", "json", "svg", "png", "jpg", "jpeg", "gif", "webp", "ico"}

def _exports_of(content: str) -> tuple[set[str], bool, bool]:
    """(noms exportés, export par défaut ?, `export *` présent ?)."""
    names = set(_EXPORT_DECL.findall(content))
    for group in _EXPORT_DESTRUCTURED.findall(content):
        for item in group.split(","):
            # "a", "a: alias", "a = defaut", "...reste" : le nom exporté est celui de la dernière position.
            name = item.split("=")[0].split(":")[-1].strip().lstrip(".").strip()
            if name.isidentifier():
                names.add(name)
    has_default = bool(re.search(r"export\s+default\b", content))
    for group in _EXPORT_LIST.findall(content):
        for item in group.split(","):
            parts = item.replace("type ", "", 1).strip().split(" as ")
            exported = parts[-1].strip()
            if exported == "default":
                has_default = True
            elif exported:
                names.add(exported)
    return names, has_default, bool(re.search(r"export\s*\*", content))

def _import_candidates(importer: str, spec: str) -> list[str]:
    target = posixpath.normpath(posixpath.join(posixpath.dirname(importer), spec))
    if target.startswith(".."):
        return []
    extension = target.rsplit(".", 1)[-1].lower() if "." in posixpath.basename(target) else ""
    if extension in _ASSET_EXTENSIONS:
        return [target]
    if extension in ("js", "jsx"):
        stem = target.rsplit(".", 1)[0]
        return [target, stem + ".ts", stem + ".tsx"]
    return (
        [target + suffix for suffix in _RESOLVE_SUFFIXES]
        + [f"{target}/index{suffix}" for suffix in _RESOLVE_SUFFIXES[:4]]
    )

def find_import_problems(
    files: list[dict],
    list_dir: Callable[[str], set[str] | None] | None = None,
    context_files: list[dict] | None = None,
    import_scope: dict[str, str] | None = None,
) -> list[str]:
    """Incohérences entre fichiers : import relatif qui ne mène ni à un fichier livré ni à un
    fichier existant, et import nommé (ou par défaut) absent des exports d'un fichier LIVRÉ.

    list_dir(dossier) -> noms des entrées, ou None si inconnu (erreur réseau, dossier absent) :
    dans le doute, un import n'est jamais signalé (pas de faux positif sur une panne GitHub).
    context_files : fichiers livrés à une tentative PRÉCÉDENTE : ils servent à résoudre les imports
    de `files` mais ne sont pas contrôlés eux-mêmes.
    import_scope : {chemin: texte} — pour ces fichiers, seuls les imports de CE texte sont contrôlés
    (le texte ajouté par une modification ciblée), pas ceux déjà présents dans le fichier d'origine."""
    delivered = {f["path"]: f["content"] for f in (context_files or [])}
    delivered.update({f["path"]: f["content"] for f in files})
    dir_cache: dict[str, set[str] | None] = {}

    def entries(directory: str) -> set[str] | None:
        if list_dir is None:
            return None
        if directory not in dir_cache:
            dir_cache[directory] = list_dir(directory)
        return dir_cache[directory]

    problems: list[str] = []
    scanned = {f["path"]: f["content"] for f in files}
    scanned.update({p: t for p, t in (import_scope or {}).items() if p in scanned})
    for path, content in scanned.items():
        if analyst_blocks.extension(path) not in CODE_EXTENSIONS:
            continue
        imports = [(m.group(1), m.group(2), m.group(3)) for m in _IMPORT_FROM.finditer(content)]
        imports += [(None, None, m.group(1)) for m in _IMPORT_SIDE_EFFECT.finditer(content)]
        for default_name, named, spec in imports:
            candidates = _import_candidates(path, spec)
            resolved = next((c for c in candidates if c in delivered), None)
            if resolved is None:
                known_absent = bool(candidates)
                for candidate in candidates:
                    names = entries(posixpath.dirname(candidate))
                    if names is None:
                        known_absent = False
                        break
                    if posixpath.basename(candidate) in names:
                        known_absent = False
                        break
                if known_absent and list_dir is not None:
                    problems.append(
                        f"{path} : l'import '{spec}' ne correspond à aucun fichier livré ni existant "
                        "(livre ce fichier, ou corrige le chemin)."
                    )
                continue
            if analyst_blocks.extension(resolved) not in CODE_EXTENSIONS:
                continue
            exported, has_default, star = _exports_of(delivered[resolved])
            if star:
                continue
            if default_name and not has_default:
                problems.append(f"{path} : import par défaut depuis '{spec}', mais {resolved} n'a pas d'export par défaut.")
            for item in (named or "").split(","):
                name = item.replace("type ", "", 1).strip().split(" as ")[0].strip()
                if name and name not in exported:
                    problems.append(f"{path} : '{name}' est importé depuis '{spec}', mais {resolved} ne l'exporte pas.")
    return list(dict.fromkeys(problems))

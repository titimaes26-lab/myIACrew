"""Retrait du commit des fichiers de l'Analyste dont les imports ne mènent à rien (exécution 139 : App.tsx importait deux composants jamais écrits)."""
from typing import Callable

import analyst_imports

MAX_REASON_CHARS = 300


def _new_exports(path: str, content: str, read_base: Callable[[str], tuple[str | None, str | None]]) -> set[str]:
    """Noms que ce fichier exporterait ET que sa version d'origine (dépôt) n'exporte pas : ceux que ses importeurs ne retrouveront plus."""
    original, _ = read_base(path)
    return analyst_imports.exported_names(content) - (analyst_imports.exported_names(original) if original else set())


def _reason(messages: list[str]) -> str:
    shown = list(dict.fromkeys(message.split(" : ", 1)[-1] for message in messages))     # le chemin du fichier est déjà dit par la ligne de rapport
    return ("imports cassés : " + " ; ".join(shown))[:MAX_REASON_CHARS]


def drop_files_with_broken_imports(
    merged: dict[str, dict],
    not_extracted: dict[str, str],
    list_dir: Callable[[str], set[str] | None] | None,
    read_base: Callable[[str], tuple[str | None, str | None]],
    edit_scope: dict[str, str],
) -> None:
    """Retire de `merged` (et inscrit dans `not_extracted`) les fichiers dont un import ne mène à rien. Répété jusqu'à stabilité : retirer un fichier
    peut priver un autre de sa cible, même quand ce fichier existe déjà dans le dépôt (voir `withdrawn_exports`). Dans le doute (dossier illisible)
    rien n'est retiré. Les dossiers ne sont lus qu'une fois pour tous les tours."""
    lister = analyst_imports.memoized(list_dir)
    lost: dict[str, set[str]] = {}
    while True:
        broken = analyst_imports.files_with_import_problems(list(merged.values()), lister, dict(edit_scope), lost)
        if not broken:
            return
        for path, messages in broken.items():
            dropped = merged.pop(path, None)
            if dropped is not None:
                lost[path] = _new_exports(path, dropped["content"], read_base)
            not_extracted[path] = _reason(messages)

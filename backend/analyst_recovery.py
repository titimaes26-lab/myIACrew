"""Récupération prudente des fichiers livrés SANS balises <<<FICHIER>>> (exécution 139 : le code d'App.tsx était dans un bloc ```tsx ordinaire)."""
import re

import analyst_blocks

# Chemin de fichier de code dans une ligne de texte : « `src/App.tsx` », « src/App.tsx », « App.tsx ».
_PATH = re.compile(
    r"(?<![\w/.\-@])((?:[\w@.\-]+/)*[\w@.\-]+\.(?:tsx?|jsx?|css|scss|json|html|vue|py|ya?ml))(?![\w/\-])", re.IGNORECASE,
)
_MIN_LINES = 3


def untagged_files(text: str) -> list[dict]:
    """Fichiers [{path, content}] tirés des blocs ``` dont la ligne de texte NON VIDE qui les précède désigne UN seul chemin. Rien n'est deviné :
    plusieurs chemins dans cette ligne, un bloc sans chemin, un bloc trop court, ou un chemin décrit par plusieurs blocs → ignoré. À n'appeler que
    lorsque la réponse ne contient AUCUN fichier balisé."""
    found: dict[str, list[str]] = {}
    previous = ""                                         # dernière ligne de texte non vide avant le bloc courant
    fence: str | None = None
    body: list[str] = []
    path: str | None = None
    for line in (text or "").splitlines():
        marker = analyst_blocks.FENCE_LINE.match(line.strip())
        if fence is None:
            if marker:
                fence, body = marker.group(1)[0], []
                paths = {m.group(1) for m in _PATH.finditer(previous)}
                path = analyst_blocks.normalize_path(next(iter(paths))) if len(paths) == 1 else None
            elif line.strip():
                previous = line
        elif marker and marker.group(1)[0] == fence and line.strip() == marker.group(1):
            if path and len(body) >= _MIN_LINES:
                found.setdefault(path, []).append("\n".join(body) + "\n")
            fence, previous = None, ""
        else:
            body.append(line)
    return [{"path": p, "content": blocks[0]} for p, blocks in found.items() if len(blocks) == 1]

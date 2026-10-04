"""Journalisation de l'application : le module `logging` (niveaux, horodatage, nom du module) à la place de `print`.

Un seul logger parent « myiacrew », configuré une fois : sortie standard (les logs de la plateforme), une ligne par
événement, vidée à chaque enregistrement — `StreamHandler` appelle flush() après chaque ligne, donc rien ne reste en
mémoire tampon si le process est tué (OOM) juste après, ce que `print(..., flush=True)` garantissait déjà.
Le niveau se règle par LOG_LEVEL (INFO par défaut). Les bibliothèques tierces (crewai, litellm) ne sont pas touchées :
seuls les loggers « myiacrew.* » sont configurés."""
import logging
import os
import sys

ROOT_NAME = "myiacrew"
_FORMAT = "%(asctime)s %(levelname)s %(name)s : %(message)s"


def _level_from_env() -> int:
    level = logging.getLevelName(os.getenv("LOG_LEVEL", "INFO").strip().upper())
    return level if isinstance(level, int) else logging.INFO


def configure_logging() -> logging.Logger:
    """Configure (une seule fois) le logger parent et le renvoie ; les appels suivants ne dupliquent rien."""
    root = logging.getLogger(ROOT_NAME)
    root.setLevel(_level_from_env())
    if not any(getattr(handler, "_myiacrew", False) for handler in root.handlers):
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter(_FORMAT))
        handler._myiacrew = True  # type: ignore[attr-defined]
        root.addHandler(handler)
    return root


def get_logger(name: str) -> logging.Logger:
    """Logger d'un module (« myiacrew.<name> »), le parent étant configuré au premier appel."""
    configure_logging()
    return logging.getLogger(f"{ROOT_NAME}.{name}")

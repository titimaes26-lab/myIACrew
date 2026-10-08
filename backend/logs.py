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


class OneLineFormatter(logging.Formatter):
    """Un événement = une ligne : les retours à la ligne du MESSAGE sont écrits en clair (`\\n`), pour qu'un texte
    d'exception ou de dépôt cible multi-lignes ne puisse pas fabriquer de fausses lignes de log (« 12:00 WARNING … »).
    La trace d'une exception (exc_info) garde ses propres lignes, après le message."""

    def formatMessage(self, record: logging.LogRecord) -> str:
        text = super().formatMessage(record)
        return text.replace("\r", "\\r").replace("\n", "\\n")


def configure_logging() -> logging.Logger:
    """Configure (une seule fois) le logger parent et le renvoie ; les appels suivants ne changent plus rien
    (ni doublon de handler, ni niveau réécrit : un niveau réglé ensuite par programme est conservé)."""
    root = logging.getLogger(ROOT_NAME)
    if not any(getattr(handler, "_myiacrew", False) for handler in root.handlers):
        root.setLevel(_level_from_env())
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(OneLineFormatter(_FORMAT))
        handler._myiacrew = True  # type: ignore[attr-defined]
        root.addHandler(handler)
    return root


def get_logger(name: str) -> logging.Logger:
    """Logger d'un module (« myiacrew.<name> »), le parent étant configuré au premier appel."""
    configure_logging()
    return logging.getLogger(f"{ROOT_NAME}.{name}")

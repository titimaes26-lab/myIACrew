"""Fabriques communes aux tests du crew : sorties factices, crew prêt à l'emploi, lecteurs de dépôt factices, textes types."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("GEMINI_API_KEY", "test")

cq = pytest.importorskip("crewquestion")


def output(raw):
    return type("O", (), {"raw": raw})()


def new_crew(owner="", repo="", branch="feature/x"):
    crew = cq.AppDevelopmentCrew()
    crew._reset_execution_state({"repo_owner": owner, "repo_name": repo, "work_branch": branch})
    return crew


GOOD_SPEC = """## Besoin
x
## Utilisateurs
y
## Fonctionnalités (MoSCoW)
- F1 [Must] Ajouter une tâche
- F2 [Should] Filtrer
## Règles et cas limites
z
## Critères d'acceptation
- AC-F1-1 Étant donné une liste vide / Quand j'ajoute / Alors elle s'affiche en moins de 1 seconde
## Hypothèses retenues
h
"""


GOOD_ARCH = """## Existant
Projet vide.
## Cible
src/ avec composants.
## Décisions
Décision : useState | Alternative écartée : Redux | Pourquoi : simple
## Dépendances à ajouter
aucune
## Contrats d'interface
- src/App.tsx : export default function App(): JSX.Element
- src/hooks/useCart.ts : export function useCart(): { items: Item[] }
## Fichiers à créer ou modifier
- CRÉER src/App.tsx : composant racine
- MODIFIER src/hooks/useCart.ts : logique du panier
## Couverture
| Exigence | Fichier(s) |
| F1 | src/App.tsx |
## Risques
- Taille : découper
"""


def _edit_block(path, search, replace):
    return (f"<<<MODIFICATION: {path}>>>\n<<<<<<< CHERCHER\n{search}\n=======\n{replace}\n"
            ">>>>>>> REMPLACER\n<<<FIN_MODIFICATION>>>\n")


def _fake_fetcher(content=None, error=None, branch_missing=False):
    def fetch(path):
        return content, error
    fetch.branch_missing = branch_missing
    return fetch


def _repo_crew():
    crew = new_crew(owner="o", repo="r", branch="crewai/x")
    crew._base_branch = "main"
    return crew

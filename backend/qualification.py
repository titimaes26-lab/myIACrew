"""Qualification du besoin (étape 1) : types de demande, rapport d'analyse, seuil de confiance, prompt et lecture du JSON."""
from typing import Any, List, Literal, Optional, cast, get_args

from pydantic import BaseModel, Field

# --- PYDANTIC MODEL & LLM ---
RequestType = Literal["ANALYSE_ONLY", "BUGFIX", "FEATURE", "DESIGN_AND_DEV"]

# Taille d'une FEATURE : PETIT = ajustement local (1 ou 2 fichiers, ni nouvel écran ni nouvelle dépendance) ; l'étape
# d'architecture est alors sautée. Dans le doute (et pour tout autre type) : GRAND, le parcours complet.
Scope = Literal["PETIT", "GRAND"]

class AnalysisReport(BaseModel):
    # Ordre des champs volontaire : le modèle remplit le JSON dans cet ordre, donc il rédige sa
    # justification (reasoning) et envisage une alternative AVANT de trancher request_type, puis
    # évalue sa confiance APRÈS — plutôt que de choisir d'abord et de rationaliser ensuite.
    summary: str = Field(description="Résumé en 2-3 phrases de ce que l'agent a compris de la demande.")
    reasoning: str = Field(
        default="",
        description="Indices relevés dans la demande (et le contexte) et règle de la grille de décision appliquée.",
    )
    alternative_type: Optional[RequestType] = Field(
        default=None, description="Deuxième catégorie la plus plausible, ou null si aucune."
    )
    request_type: RequestType = Field(description="Type de workflow à déclencher.")
    scope: Scope = Field(
        default="GRAND",
        description="PETIT seulement pour une FEATURE locale (1 ou 2 fichiers, sans nouvel écran ni nouvelle dépendance) ; sinon GRAND.",
    )
    # Obligatoire (pas de valeur par défaut) : un défaut à 1.0 ferait passer pour certaine une
    # réponse qui omet ce champ, sans jamais déclencher le seuil de clarification.
    confidence: float = Field(
        ge=0.0, le=1.0,
        description="Confiance dans request_type, de 0 à 1 (sous 0.6 : la demande doit être clarifiée).",
    )
    is_clear: bool = Field(description="Vrai si la demande est claire, Faux si des ambiguïtés existent.")
    questions: List[str] = Field(default_factory=list, description="Liste de 2 à 4 questions si la demande est floue.")

class QualificationResult(AnalysisReport):
    """Réponse de /api/qualify : AnalysisReport + un indicateur EXPLICITE de repli. Séparé du
    modèle demandé au LLM (output_pydantic) pour que celui-ci ne puisse jamais le remplir."""
    fallback: bool = Field(
        default=False,
        description="Vrai si la qualification automatique a échoué : request_type n'est alors qu'un défaut.",
    )

# Sous ce seuil, la qualification est jugée trop incertaine pour lancer un workflow coûteux
# (jusqu'à 5 agents) sur une catégorie peut-être fausse : on pose plutôt des questions.
QUALIFICATION_CONFIDENCE_THRESHOLD = 0.6

_WORKFLOW_LABELS = {
    "ANALYSE_ONLY": "une analyse/des spécifications sans code",
    "BUGFIX": "la correction d'un bug",
    "FEATURE": "l'ajout d'une fonctionnalité à un projet existant",
    "DESIGN_AND_DEV": "la conception et le développement complets d'un nouveau produit",
}

def _enforce_confidence_threshold(report: AnalysisReport) -> AnalysisReport:
    """Force une clarification quand le modèle n'est pas assez sûr de sa catégorie, et garantit
    qu'une demande jugée floue s'accompagne toujours d'au moins une question à poser."""
    if report.confidence < QUALIFICATION_CONFIDENCE_THRESHOLD:
        report.is_clear = False
    if not report.is_clear and not report.questions:
        alternative = report.alternative_type if report.alternative_type != report.request_type else None
        if alternative:
            report.questions = [
                f"Attends-tu plutôt {_WORKFLOW_LABELS[report.request_type]} ou "
                f"{_WORKFLOW_LABELS[alternative]} ?"
            ]
        else:
            report.questions = [
                "Peux-tu préciser le résultat attendu : " + ", ".join(_WORKFLOW_LABELS.values()) + " ?"
            ]
    return report

QUALIFICATION_EXAMPLES = """\
Exemples de cas limites (la grille de ta fiche reste la référence) :
- "Refais la page de login" -> FEATURE, scope GRAND, alternative DESIGN_AND_DEV, confiance moyenne à bonne : un module d'un produit existant, sans reconception globale.
- "Ajoute un bouton d'export CSV sur la page des commandes" -> FEATURE, scope PETIT, confiance haute : un ajout local, 1 ou 2 fichiers, ni nouvel écran ni nouvelle dépendance.
- "Ajoute un système de comptes avec connexion et rôles" -> FEATURE, scope GRAND, confiance haute : plusieurs écrans, de l'état partagé et probablement de nouvelles dépendances.
- "Pourquoi la liste des commandes est lente ?" -> ANALYSE_ONLY, alternative BUGFIX, confiance moyenne à bonne : l'utilisateur veut comprendre, pas de livraison de code.
- "La recherche plante et ajoute aussi un filtre par date" -> BUGFIX, alternative FEATURE, confiance basse à moyenne, is_clear false : deux intentions, demande de scinder ou de prioriser.
- "Crée un jeu de gestion de ferme en React" -> DESIGN_AND_DEV, alternative null, confiance haute : nouveau produit à concevoir puis développer.
- Suivi "corrige ça" après un tour FEATURE dans le contexte -> BUGFIX, confiance bonne : le contexte précise l'objet du bug.
- "Améliore l'appli" -> FEATURE, alternative ANALYSE_ONLY, confiance très basse, is_clear false : aucun objet précis, poser une question fermée.
"""

def _build_qualification_prompt(user_prompt: str, conversation_context: str = "", has_repo_target: bool = False) -> str:
    repo_line = (
        "Un repository GitHub cible est fourni."
        if has_repo_target
        else "Aucun repository GitHub cible n'est fourni."
    )
    return f"""
        Tu es le Spécialiste en Qualification / Senior Product Owner.
        Voici la demande actuelle : "{user_prompt}"

        Tours précédents de cette conversation (pour interpréter un message de suivi comme
        "corrige ça" ou "ajoute aussi Y" ; ne décide JAMAIS sur ce seul contexte si la demande
        actuelle dit autre chose) :
        {conversation_context or "Aucun échange précédent."}

        {repo_line}

        {QUALIFICATION_EXAMPLES}
        Applique la grille de décision de ta fiche, dans cet ordre :
        1. summary : ce que tu as compris.
        2. reasoning : les indices précis relevés dans la demande et la règle appliquée.
        3. alternative_type : la 2e catégorie la plus plausible (ou null).
        4. request_type : ta décision.
        5. scope : PETIT seulement si c'est une FEATURE locale (un bouton, un champ, un texte, un style, un
           calcul : 1 ou 2 fichiers, sans nouvel écran, nouvelle dépendance ni nouvelle structure d'état) ;
           dans le doute, et pour tout autre type, GRAND.
        6. confidence : de 0 à 1. Sous {QUALIFICATION_CONFIDENCE_THRESHOLD}, is_clear doit être false.
        7. is_clear, puis questions (2 à 4) si is_clear est false.

        Règles pour les questions : fermées (réponse courte ou choix entre options). Si tu hésites
        entre deux catégories, une question nomme ces deux catégories et demande de trancher.
        Si la demande vise du code existant (BUGFIX ou FEATURE) et qu'aucun repository n'est fourni,
        mets is_clear à false et pose UNE question qui demande de renseigner le repository dans le
        formulaire « Repository cible » de l'interface (une réponse tapée dans le chat ne le
        renseigne pas), ou de répondre « local » pour travailler sans repository.
        """

_REQUEST_TYPES = set(get_args(RequestType))

def _as_bool(value: Any) -> bool:
    """bool() tel quel ferait de la chaîne "false" (fréquente dans un JSON extrait à la main) un True."""
    if isinstance(value, str):
        return value.strip().lower() in ("true", "vrai", "oui", "yes", "1")
    return bool(value)

def _coerce_analysis_report(data: Any) -> Optional[AnalysisReport]:
    """Reconstruit un AnalysisReport champ par champ depuis un JSON extrait à la main, en
    corrigeant les écarts courants du modèle (confiance en pourcentage, alternative hors liste)
    au lieu de tout rejeter pour un seul champ. None si request_type lui-même est inexploitable.
    """
    if not isinstance(data, dict):
        return None
    request_type = str(data.get("request_type", "")).strip().upper()
    if request_type not in _REQUEST_TYPES:
        return None
    alternative = str(data.get("alternative_type") or "").strip().upper()
    try:
        confidence = float(data.get("confidence", 0.5))
    except (TypeError, ValueError):
        # Absente ou illisible = incertaine (0.5), pas 1.0 : un JSON récupéré à la main
        # depuis une sortie mal formée ne mérite pas d'être cru sur parole.
        confidence = 0.5
    # Le prompt demande 0 à 1 ; les écarts courants sont une note sur 10 ou un pourcentage.
    # Au-delà, la valeur n'a pas de sens : traitée comme inconnue (0.5).
    if 1 < confidence <= 10:
        confidence /= 10
    elif 10 < confidence <= 100:
        confidence /= 100
    elif confidence > 100:
        confidence = 0.5
    questions = data.get("questions") or []
    return AnalysisReport(
        summary=str(data.get("summary") or "Analyse effectuée."),
        reasoning=str(data.get("reasoning") or ""),
        alternative_type=cast(Optional[RequestType], alternative if alternative in _REQUEST_TYPES else None),
        request_type=cast(RequestType, request_type),   # validé par _REQUEST_TYPES plus haut
        # Tout sauf « PETIT » explicite (absent, illisible) = GRAND : le parcours complet est le choix sûr.
        scope="PETIT" if str(data.get("scope", "")).strip().upper() == "PETIT" else "GRAND",
        confidence=min(max(confidence, 0.0), 1.0),
        is_clear=_as_bool(data.get("is_clear", False)),
        questions=[str(q) for q in questions] if isinstance(questions, list) else [],
    )

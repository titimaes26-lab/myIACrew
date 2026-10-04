"""Résumé final d'une exécution : texte envoyé au modèle, appel borné dans le temps, assemblage du corps."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context

import crew_llms
import crew_retry
import crew_workflow
from logs import get_logger
from summary import delivery_facts, fallback_summary

log = get_logger("crew")

# Marqueur inséré avant la section de résumé, pour que le frontend puisse la séparer
# du reste sans ambiguïté (voir parseCrewResult.ts). Un simple titre "## Résumé" pourrait
# apparaître naturellement dans le rapport d'un agent (ex: sa propre sous-section de
# conclusion) ; ce commentaire HTML, lui, n'a aucune raison d'être produit par un agent.
SUMMARY_SENTINEL = "<!--crew-summary-->"

# Borne la taille du texte envoyé au modèle pour la synthèse : le résultat combiné peut
# contenir du code source complet (workflows FEATURE/DESIGN_AND_DEV), et ce résumé n'est
# qu'un ajout de confort qui ne justifie pas de peser significativement sur le quota
# Gemini déjà sous tension (cf. crew_retry.QuotaManager et crew_retry.retry_on_rate_limit_async).
MAX_SUMMARY_INPUT_CHARS = 6000

MAX_SUMMARY_REQUEST_CHARS = 1500

# Pool dédié et volontairement petit : asyncio.wait_for peut abandonner l'attente d'un
# appel bloqué (ex: litellm qui enchaîne ses propres tentatives internes bien au-delà de
# SUMMARY_WALL_CLOCK_TIMEOUT) sans pouvoir arrêter le thread sous-jacent. En isolant ces
# threads orphelins potentiels dans un pool à part, une panne prolongée de Gemini ne peut
# jamais épuiser le pool par défaut dont dépend le reste de l'application. Ce pool dédié
# peut lui-même se retrouver saturé le temps que litellm abandonne ses propres tentatives
# (borné par LITELLM_NUM_RETRIES/le backoff, pas indéfini) : dans ce cas les résumés sont
# simplement absents pendant cette fenêtre, sans jamais affecter le résultat des agents.
_summary_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="crew-summary")

def _build_summary_input(result) -> str:
    """Texte borné donné en entrée au résumé, avec un budget de troncature réparti
    à parts égales entre les tâches plutôt qu'une simple troncature globale : sur un
    workflow à plusieurs tâches (ex: FEATURE), une troncature globale ne garderait que
    le début (architecture) et perdrait entièrement le code produit et l'avis QA, qui
    sont pourtant l'essentiel de ce qui a été livré.

    Pour chaque tâche, garde le DÉBUT et la FIN de son rapport plutôt qu'un simple préfixe :
    un agent conclut typiquement son rapport par sa synthèse/justification ("pourquoi tel
    choix"), qu'un pur `raw[:budget]` couperait systématiquement en tout premier sur un
    rapport dépassant le budget, alors que le résumé demandé à crew_llms.summary_llm cherche justement
    ce genre de rationale (voir _build_summary_prompt).
    """
    sections = list(crew_workflow._iter_task_sections(result))
    if not sections:
        raw = getattr(result, "raw", None)
        text = raw if raw is not None else str(result)
        return text[:MAX_SUMMARY_INPUT_CHARS]

    per_task_budget = max(MAX_SUMMARY_INPUT_CHARS // len(sections), 500)
    parts = []
    for agent_name, raw in sections:
        if len(raw) <= per_task_budget:
            truncated = raw
        else:
            head_budget = per_task_budget * 2 // 3
            tail_budget = per_task_budget - head_budget
            truncated = f"{raw[:head_budget]} [...tronqué...] {raw[-tail_budget:]}"
        parts.append(f"## {agent_name}\n{truncated}")
    return "\n\n".join(parts)

def _build_summary_prompt(user_request: str, summary_input: str) -> str:
    # user_request vient du texte libre saisi par l'utilisateur (aucune limite de
    # longueur côté frontend) : le borner évite qu'une saisie très longue fasse, à elle
    # seule, dépasser le budget de taille que ce résumé est censé respecter.
    truncated_request = user_request[:MAX_SUMMARY_REQUEST_CHARS]
    return (
        "Voici le résultat produit par une équipe d'agents IA pour répondre à la "
        f"demande suivante :\n\n{truncated_request}\n\n"
        f"Résultat complet :\n---\n{summary_input}\n---\n\n"
        "Rédige, en français, un résumé clair et concret en 3 blocs, avec exactement ces titres :\n"
        "### Ce qui a été fait\n(2 à 3 phrases : décisions clés, ce qui a été produit)\n"
        "### Pourquoi ces choix\n(1 à 2 phrases, uniquement si le résultat ci-dessus justifie des choix importants ; "
        "sinon écris « Non précisé dans le résultat. »)\n"
        "### À faire ensuite\n(1 à 3 puces : réserves de la QA, éléments non livrés, vérifications à faire ; "
        "sinon écris « Rien de particulier. »)\n\n"
        "Les chiffres (étapes, nombre de fichiers, verdict QA) sont déjà affichés ailleurs : ne les répète pas. "
        "N'invente rien qui ne soit pas déjà présent dans le résultat "
        "ci-dessus : ni un fait (ex: un fichier livré qui ne l'a pas été), ni une raison "
        "absente — si le résultat ne justifie pas un choix, décris-le sans inventer de "
        "justification."
    )

# Borne le temps d'attente total (au-delà du request_timeout de crew_llms.summary_llm lui-même),
# car LITELLM_NUM_RETRIES=7 (défini dans crew_llms.py, process-wide) s'applique aussi à cet
# appel : sans ce filet, une erreur transitoire pourrait déclencher jusqu'à 7 tentatives
# internes avant que crew_llms.summary_llm.call() ne lève enfin, contredisant l'objectif même
# d'un résumé qui ne doit jamais faire attendre longtemps une réponse déjà acquise.
# 30 et non 25 : doit rester strictement supérieur au request_timeout de crew_llms.summary_llm
# (25 désormais, voir crew_llms.summary_llm) pour continuer à lui laisser le temps de lever sa
# propre erreur de timeout plutôt que d'être coupé par celui-ci en premier.
SUMMARY_WALL_CLOCK_TIMEOUT = 30

async def _generate_summary(user_request: str, result) -> str | None:
    """Résumé de synthèse ajouté en fin de résultat combiné.

    Best-effort : un échec ici (quota, timeout...) ne doit jamais faire échouer
    l'exécution, dont le résultat des agents est déjà acquis à ce stade. Volontairement
    sans retry applicatif : ce n'est qu'un ajout de confort, pas la livraison principale.
    """
    def _call() -> str:
        crew_retry.quota_mgr.adaptive_pause()
        return crew_llms.summary_llm.call(_build_summary_prompt(user_request, _build_summary_input(result)))

    try:
        loop = asyncio.get_running_loop()
        # loop.run_in_executor() ne copie PAS automatiquement le contexte courant dans le
        # thread (contrairement à asyncio.to_thread, qui le fait mais impose son propre
        # executor par défaut) : sans ce copy_context().run(...) explicite, l'appel à
        # crew_retry.quota_mgr.adaptive_pause() dans _call() perdrait de vue le _current_metrics de
        # CETTE requête (il verrait la valeur par défaut, None), et le temps d'attente
        # de cet appel ne serait jamais comptabilisé dans les métriques renvoyées.
        ctx = copy_context()
        text = await asyncio.wait_for(
            loop.run_in_executor(_summary_executor, ctx.run, _call), timeout=SUMMARY_WALL_CLOCK_TIMEOUT
        )
        return text.strip() or None
    except Exception as e:
        log.warning(f"Génération du résumé ignorée : {type(e).__name__}: {e}")
        return None

def _compose_summary_body(sections, request_type, scope, summary: str | None) -> str:
    """Corps du « ## Résumé » : faits calculés en Python (jamais par le modèle), puis la synthèse du modèle
    ou, si elle a échoué, un résumé de repli sans modèle. Vide quand il n'y a rien à dire."""
    parts = (delivery_facts(sections, request_type, scope), summary or fallback_summary(sections))
    return "\n\n".join(part for part in parts if part)

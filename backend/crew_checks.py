"""État et contrôles propres à UNE exécution : remise à zéro, guardrails de l'Analyste, du Développeur et de la QA."""
from typing import Optional

import crew_guardrails
import design_spec
import crew_plan
import crew_workspace
from crew_state import CrewExecutionState
import analyst_imports
from analyst_import_drop import drop_files_with_broken_imports
from analyst_output import review_diagnostic_output
from analyst_edits import parse_edit_sections
from delivery import extract_user_request, render_delivery_block, unconfirmed_pr_urls
from logs import get_logger
from qa_report import QA_VERDICT, qa_report_issues, reconcile_verdict, required_verdict

log = get_logger("crew")

MAX_RETRY_CONTEXT_CHARS = 4000


def _ambiguity_clarification(ambiguous: list[str]) -> str:
    # Fichier livré DANS la réponse ET déclaré "NON réalisé" : ambigu ("NON réalisé" peut décrire autre chose que le fichier). On ne
    # tranche pas en silence : l'Analyste est invité à clarifier ; s'il ne le fait pas, le fichier est EXCLU (et signalé) — committer
    # une version peut-être partielle par-dessus le vrai fichier est pire que ne rien committer.
    return (
        "Fichier(s) à la fois livré(s) entre balises ET déclaré(s) 'NON réalisé' : "
        + ", ".join(ambiguous)
        + ". Retire leurs balises s'ils ne sont vraiment pas réalisés, ou reformule la ligne "
        "du plan qui les mentionne."
    )


def _join_issues(issue: str | None, extra: str | None) -> str | None:
    """Ajoute `extra` (s'il y en a un) à `issue` : un défaut par paragraphe."""
    return (f"{issue}\n\n{extra}" if issue else extra) if extra else issue


class CrewChecksMixin(CrewExecutionState):
    """Méthodes de AppDevelopmentCrew qui posent l'état d'une exécution et contrôlent la sortie des agents (guardrails)."""

    def _reset_execution_state(self, inputs: Optional[dict] = None) -> None:
        inputs = inputs or {}
        owner, repo = inputs.get("repo_owner") or "", inputs.get("repo_name") or ""
        # Cible FIXÉE par l'exécution (jamais par les arguments que le LLM passe aux outils) :
        # un owner vide passé par erreur ne doit jamais détourner un run GitHub vers le disque.
        self._work_branch = inputs.get("work_branch") or ""
        self._base_branch = inputs.get("base_branch") or "main"
        # Lecteurs du fichier d'ORIGINE (modifications ciblées, imports), créés à la demande.
        self._base_readers = None
        self._repo_target = (owner, repo) if owner and repo else None
        # Par conversation (et non par branche : en mode local, work_branch est toujours vide).
        self._workspace = crew_workspace._conversation_workspace(str(inputs.get("conversation_id") or ""))
        # Fichiers committables, fusionnés au fil des tentatives de l'Analyste (voir le guardrail).
        self._analyst_files = []
        # {chemin: raison} des fichiers annoncés par l'Analyste mais jamais committables
        # (raccourci "// ... reste du code", balise de fin manquante, chemin invalide).
        self._not_extracted = {}
        # {chemin: raison} des fichiers refusés à l'écriture lors du DERNIER commit qui les
        # concernait : un commit ultérieur réussi efface leur entrée.
        self._write_rejections = {}
        self._diagnostic_guardrail_failures = 0
        self._design_guardrail_failures = 0
        # Faits de livraison constatés par les OUTILS (voir _development_report_guardrail) : chemins
        # réellement committés, URLs de PR renvoyées par l'outil, dernière réponse de l'Analyste.
        self._committed_paths = set()
        # {chemin: texte AJOUTÉ} des fichiers modifiés par blocs : la QA ne contrôle que les imports
        # ajoutés, pas ceux déjà présents dans le fichier d'origine (voir qa_verify_delivered_files).
        self._edit_scope = {}
        # Tâche d'architecture de CETTE exécution (None sans architecte, ex. BUGFIX) : la QA y lit le plan.
        self._architecture_task_ref = None
        self._diagnostic_task_ref = None
        self._plan_withdrawn: set[str] = set()
        self._pull_request_urls = []
        self._analyst_raw = ""
        self._request_type = ""
        self._user_request = extract_user_request(str(inputs.get("user_request") or ""))
        self._commit_tool_used = False

    def _reset_for_replay(self, remaining_keys: set[str]) -> None:
        """Étapes rejouées depuis zéro après une tentative avortée : budget de refus intact (sinon le premier souci constaté serait accepté avec
        un simple avertissement) et, pour l'Analyste, sans les fichiers fusionnés par la tentative avortée (voir _diagnostic_guardrail)."""
        if "design" in remaining_keys:
            self._design_guardrail_failures = 0
        if "diagnostic" in remaining_keys:
            self._diagnostic_guardrail_failures = 0
            self._analyst_files, self._not_extracted, self._edit_scope = [], {}, {}

    def _design_guardrail(self, task_output):
        """Refuse UNE fois des specs dont un défaut BLOQUANT est vérifiable en Python (titre manquant, « Must » sans critère, identifiant en double,
        composant renvoyant à une fonctionnalité inexistante) : le Designer les corrige avec la liste exacte. Ensuite, ou pour les défauts non bloquants
        (critères vagues), la note ajoutée en fin de document est lue par les étapes suivantes (comme avant)."""
        raw = getattr(task_output, "raw", "") or ""
        with_components = getattr(self, "_request_type", "") != "ANALYSE_ONLY"        # une analyse seule n'a pas forcément d'interface
        blocking = design_spec.blocking_issues(raw, with_components)
        if blocking and self._design_guardrail_failures < 1:
            self._design_guardrail_failures += 1
            return False, ("Specs à corriger avant de continuer :\n" + "\n".join(f"- {issue}" for issue in blocking)
                           + "\n\nRenvoie le document COMPLET et corrigé (mêmes titres \"##\", mêmes identifiants F/AC).")
        note = crew_guardrails._make_issue_note_guardrail(lambda text: design_spec.spec_issues(text, with_components), "Contrôle automatique des specs")
        return note(task_output)

    def _brief_analyst(self, architecture_raw: str) -> None:
        """Ajoute (une seule fois) le plan de l'Architecte à la description de la tâche de l'Analyste, AVANT sa planification."""
        task, brief = self._diagnostic_task_ref, crew_plan.plan_briefing(architecture_raw)
        if task is not None and brief and crew_plan.PLAN_BRIEFING_TITLE not in (task.description or ""):
            task.description = f"{task.description}\n\n{brief}"
            # CrewAI reconstruit la description depuis `_original_description` à CHAQUE kickoff : sans ceci, un nouvel essai de l'équipe
            # (Analyste en échec après l'Architecte) perdrait le rappel.
            original = getattr(task, "_original_description", None)
            if original is not None and crew_plan.PLAN_BRIEFING_TITLE not in original:
                task._original_description = f"{original}\n\n{brief}"

    def _plan_mismatch(self, delivered_paths) -> Optional[str]:
        plan = getattr(getattr(self._architecture_task_ref, "output", None), "raw", "") or ""
        planned = crew_guardrails._planned_paths(plan)
        withdrawn = set(getattr(self, "_plan_withdrawn", ()))      # retraits « NON réalisé » de TOUTES les tentatives, pas seulement la dernière
        if planned:
            withdrawn |= crew_guardrails._withdrawn_paths(getattr(self, "_analyst_raw", "") or "", sorted(planned))
        self._plan_withdrawn = withdrawn
        return crew_plan.plan_mismatch_issue(planned, set(delivered_paths), withdrawn)

    def _diagnostic_retry_context(self) -> str:
        """Specs/architecture reçues par diagnostic_task : CrewAI ne les repasse PAS à l'agent
        quand un guardrail le fait recommencer (seuls l'erreur et sa réponse précédente le sont),
        alors qu'une réécriture complète doit rester alignée dessus."""
        parts = []
        context = self.diagnostic_task().context
        # Hors run_dynamic_crew, CrewAI laisse ici une sentinelle "non spécifié", non itérable.
        for context_task in context if isinstance(context, list) else []:
            raw = getattr(getattr(context_task, "output", None), "raw", "") or ""
            if raw:
                parts.append(raw[:MAX_RETRY_CONTEXT_CHARS])
        return ("\n\nRappel du contexte reçu (specs/architecture) :\n" + "\n---\n".join(parts)) if parts else ""

    def _diagnostic_guardrail(self, task_output):
        """Refuse UNE fois une sortie de l'Analyste inexploitable (aucun fichier, balise de fin
        manquante, commentaires de type "// ... reste du code") pour qu'il la corrige ; à la 2e
        tentative, accepte en signalant le problème plutôt que de faire échouer tout le crew
        (CrewAI lève une exception quand un guardrail échoue au-delà de guardrail_max_retries).

        Les fichiers sains sont FUSIONNÉS d'une tentative à l'autre : une réponse corrigée qui ne
        reprend que les fichiers fautifs ne fait pas perdre les fichiers sains de la première —
        sauf ceux qu'elle retire explicitement (chemin suivi de "NON réalisé").
        """
        raw = getattr(task_output, "raw", "") or ""
        self._analyst_raw = raw
        for edited_path, edit_blocks in parse_edit_sections(raw)[0].items():
            self._edit_scope[edited_path] = "\n".join(replace for _, replace in edit_blocks)
        files, issue, faulty_paths, broken = review_diagnostic_output(
            raw, read_base=self._read_base_file, list_dir=self._list_base_dir,
            context_files=list(getattr(self, "_analyst_files", []) or []),
        )
        edited = parse_edit_sections(raw)[0]
        for f in files:
            if f["path"] not in edited:
                self._edit_scope.pop(f["path"], None)             # fichier livré EN ENTIER : plus jugé sur un ancien texte ajouté
        merged, not_extracted, ambiguous = self._merge_analyst_attempt(raw, files, faulty_paths, broken)
        issue = _join_issues(issue, _ambiguity_clarification(ambiguous) if ambiguous else None)
        self._analyst_files = list(merged.values())
        self._not_extracted = not_extracted
        lister = analyst_imports.memoized(self._list_base_dir)
        issue = _join_issues(issue, self._merged_import_issue(merged, issue, lister))
        issue = _join_issues(issue, self._plan_mismatch(merged))
        if issue is None:
            return True, task_output
        self._diagnostic_guardrail_failures = getattr(self, "_diagnostic_guardrail_failures", 0) + 1
        if self._diagnostic_guardrail_failures <= 1:
            return False, (
                f"{issue}\n\nRenvoie ta réponse COMPLÈTE, avec TOUS les fichiers entre balises "
                f"(y compris ceux qui étaient déjà corrects).{self._diagnostic_retry_context()}"
            )
        for path in ambiguous:
            merged.pop(path, None)
            not_extracted[path] = "livré mais déclaré NON réalisé, ambiguïté non levée"
        drop_files_with_broken_imports(merged, not_extracted, lister, self._read_base_file, dict(self._edit_scope))
        late_plan = self._plan_mismatch(merged)                   # recalculé APRÈS les retraits : un fichier prévu puis retiré compte comme manquant
        issue = _join_issues(issue, late_plan if late_plan and late_plan not in issue else None)
        self._analyst_files = list(merged.values())
        self._not_extracted = not_extracted
        excluded = "".join(f"\n- {p} : NON réalisé ({reason}, exclu du commit)" for p, reason in sorted(not_extracted.items()))
        return True, f"{raw}\n\n> ⚠️ Contrôle automatique (non corrigé par l'Analyste) : {issue}{excluded}"

    def _merged_import_issue(self, merged: dict[str, dict], issue: str | None, lister) -> str | None:
        """Imports cassés dans l'ENSEMBLE des fichiers fusionnés, pas seulement ceux de cette tentative : un fichier retiré ("NON réalisé") peut
        priver un fichier d'une tentative précédente de sa cible. Ne renvoie que les problèmes pas déjà dans `issue`."""
        problems = analyst_imports.find_import_problems(list(merged.values()), lister, None, dict(self._edit_scope))
        new = [problem for problem in problems if problem not in (issue or "")]
        return ("Incohérences entre fichiers (imports) :\n" + "\n".join(f"- {p}" for p in new[:10])
                + "\nCorrige-les (les fichiers concernés restent à fournir EN ENTIER ou par modification).") if new else None

    def _merge_analyst_attempt(
        self, raw: str, files: list[dict], faulty_paths: set[str], broken: dict[str, str],
    ) -> tuple[dict[str, dict], dict[str, str], list[str]]:
        """Fusionne les fichiers de cette tentative avec ceux des précédentes : (fichiers committables par chemin, {chemin: raison} des
        fichiers jamais committables, chemins AMBIGUS : livrés dans cette réponse ET déclarés "NON réalisé")."""
        merged = {f["path"]: f for f in getattr(self, "_analyst_files", [])}
        not_extracted = dict(getattr(self, "_not_extracted", {}))
        delivered_now = {f["path"] for f in files}
        withdrawn = crew_guardrails._withdrawn_paths(raw, list(merged) + sorted(delivered_now))
        # Fichier d'une tentative PRÉCÉDENTE déclaré "NON réalisé" : retrait explicite.
        for path in withdrawn - delivered_now:
            merged.pop(path, None)
            not_extracted[path] = "retiré par l'Analyste (NON réalisé)"
        for f in files:
            if f["path"] not in faulty_paths:
                merged[f["path"]] = f
                not_extracted.pop(f["path"], None)
        # Jamais de fichier à raccourci ou tronqué dans ce que le Développeur committera : il
        # écraserait le vrai fichier par une version incomplète. Une version SAINE d'une tentative
        # précédente reste en revanche committable.
        for path, reason in [(p, "contenu incomplet (commentaire de raccourci)") for p in faulty_paths] + list(broken.items()):
            if path not in merged:
                not_extracted[path] = reason
        return merged, not_extracted, sorted(withdrawn & delivered_now)

    def _development_report_guardrail(self, task_output):
        """Ajoute au rapport du Développeur ce que les OUTILS ont réellement constaté (fichiers
        committés, URL de PR renvoyée), et signale toute URL de PR citée sans être issue d'un outil.
        Jamais de relance : un rapport inexact est corrigé par ce bloc, pas par un appel LLM de plus."""
        raw = getattr(task_output, "raw", "") or ""
        block = render_delivery_block(
            sorted(self._committed_paths), self._delivery_gaps(), list(self._pull_request_urls),
            getattr(self, "_repo_target", None) is not None,
            unconfirmed_pr_urls(raw, self._pull_request_urls),
            commit_tool_used=getattr(self, "_commit_tool_used", False),
        )
        return True, f"{raw}\n\n{block}"

    def _qa_report_guardrail(self, task_output):
        """Rend le verdict de la QA COHÉRENT avec les faits constatés par les outils (voir
        qa_report.required_verdict) : un verdict plus indulgent est réécrit en place, et les défauts
        du rapport (KO sans preuve localisée, NON VÉRIFIABLE sans test manuel…) sont listés dans une
        note. Jamais de relance : une relance QA coûterait jusqu'à 10 appels d'outils."""
        raw = getattr(task_output, "raw", "") or ""
        notes = list(qa_report_issues(raw))
        if not QA_VERDICT.search(raw):
            raw = (
                f"{raw}\n\n**Verdict : NON FOURNI** — la QA n'a pas conclu explicitement : à considérer "
                "comme NO_GO tant qu'une vérification humaine n'a pas eu lieu."
            )
        else:
            required, reasons = required_verdict(
                raw,
                delivery_gaps=self._delivery_gaps(),
                analyst_file_count=len(getattr(self, "_analyst_files", []) or []),
                committed_count=len(self._committed_paths),
                commit_tool_used=getattr(self, "_commit_tool_used", False),
            )
            raw, adjusted_from = reconcile_verdict(raw, required)
            if adjusted_from:
                notes.insert(0, f"Verdict ajusté de {adjusted_from} à {required} : " + " ; ".join(reasons) + ".")
        if not notes:
            return True, raw if raw != (getattr(task_output, "raw", "") or "") else task_output
        return True, raw + "\n\n## Contrôle automatique du rapport QA\n" + "\n".join(f"- {note}" for note in notes)

"""Déroulement d'UNE exécution du crew : tâches choisies et reliées entre elles, reprise, retry résumable sur quota,
échec attribué à une étape, résumé final."""
import asyncio
from typing import Any, Callable, NoReturn, Optional

from crewai import Crew, Process
from crewai.tasks.task_output import TaskOutput

import crew_cache
import crew_result
import crew_retry
import crew_workflow
from metrics_collect import current_metrics
from github_edit_failures import track_edit_failures
from logs import get_logger

log = get_logger("crew")

# Contexte EXPLICITE par tâche au lieu du défaut CrewAI (toutes les sorties précédentes) :
# chaque agent reçoit ce dont il a besoin pour raisonner, et pas plus. Le Développeur ne
# reçoit que le code de l'Analyste (specs/architecture ne feraient que diluer ce qu'il doit
# committer) ; la QA reçoit les critères d'acceptation du Designer en plus du code et du
# rapport de commit, pour juger la conformité fonctionnelle.
CONTEXT_PLAN = {
    'architecture': ['design'],
    'diagnostic': ['design', 'architecture'],
    'development': ['diagnostic'],
    'qa': ['design', 'diagnostic', 'development'],
}

MAX_RETRIES = 5
BASE_DELAY = 15.0


class CrewRun:
    def __init__(
        self,
        crew,
        inputs: dict,
        request_type: str,
        on_step_change: Optional[Callable[[Optional[str]], None]] = None,
        on_task_output_complete: Optional[Callable[[str, str, Optional[float]], None]] = None,
        resume_outputs: Optional[dict[str, str]] = None,
        scope: Optional[str] = None,
    ):
        self.crew = crew
        self.inputs = inputs
        self.request_type = request_type
        self.on_step_change = on_step_change
        self.on_task_output_complete = on_task_output_complete
        self.resume_outputs = resume_outputs
        self.scope = scope

        # Clés alignées sur WORKFLOW_STEPS (frontend/src/constants/workflowSteps.ts) : c'est
        # ce que on_step_change transmet à execution_state.persist_current_step pour persister l'étape en cours (voir
        # ExecutionHistory.current_step), et le frontend s'attend exactement à ces 5 valeurs
        # pour faire correspondre la progression réelle à l'étape affichée dans StepIndicator.
        # 'diagnostic' précède toujours 'development' (jamais l'inverse) : diagnostic_task
        # (lecture seule, voir diagnostic_agent) produit le code complet que development_task
        # (écriture seule, voir developer_agent) committe ensuite tel quel — process séquentiel
        # CrewAI, donc development_task reçoit automatiquement la sortie de diagnostic_task en
        # contexte sans câblage explicite supplémentaire ici.
        factories = {
            'design': crew.design_task, 'architecture': crew.architecture_task,
            'diagnostic': crew.diagnostic_task, 'development': crew.development_task, 'qa': crew.qa_task,
        }
        self.selected = [(key, factories[key]()) for key in crew_workflow.workflow_step_keys(request_type, scope)]
        self.tasks_by_key = dict(self.selected)
        for key, task_obj in self.selected:
            wanted = [self.tasks_by_key[k] for k in CONTEXT_PLAN.get(key, []) if k in self.tasks_by_key]
            task_obj.context = wanted if wanted else None
        self._reset_state()

        self.step_keys = [key for key, _ in self.selected]
        self.selected_tasks = [task for _, task in self.selected]
        self.total_steps = len(self.selected)

        # Retry RÉSUMABLE : sur une erreur de quota survenant après que certaines tâches ont terminé, seules les
        # tâches RESTANTES sont rejouées. Les objets Task sont créés UNE SEULE FOIS (voir __init__) : une tâche déjà terminée,
        # exclue du prochain sous-crew, fournit toujours sa sortie (`task.output`) aux tâches suivantes en contexte.
        self.completed_keys: list[str] = []
        self.completed_outputs: dict[str, Any] = {}
        self.attempt_completed = 0
        self.retries = 0

    def _reset_state(self) -> None:
        self.crew._reset_execution_state(self.inputs)
        self.crew._request_type = self.request_type
        self.crew._architecture_task_ref = self.tasks_by_key.get('architecture')

    async def execute(self) -> str:
        try:
            # Hors boucle asyncio : le rejeu du contrôle de l'Analyste lit le dépôt (appels GitHub bloquants) et
            # chaque étape reprise écrit en base — comme pour on_step_change, jamais directement sur la boucle.
            # DANS le try : le finally purge le cache mémoïsé de CrewAI même si ce rejeu échoue.
            # Strictement séquentiel (await) : rien d'autre ne touche cette instance de crew pendant ce temps.
            if self.resume_outputs:
                await asyncio.to_thread(self._apply_resume)
            await self._run_attempts()
        finally:
            self._release()
        return await self._final_result()

    # Reprise d'une exécution précédente : les étapes déjà réussies (préfixe, voir crew_workflow.resumable_prefix)
    # sont posées comme TERMINÉES, exactement comme si on_task_complete les avait vues passer — leur
    # sortie reste donc le contexte des étapes suivantes sans être recalculée (ni repayée en quota).
    def _apply_resume(self) -> None:
        resume_outputs = self.resume_outputs or {}
        for key in crew_workflow.resumable_prefix(self.step_keys, resume_outputs):
            task_obj = self.tasks_by_key[key]
            raw = resume_outputs[key]
            role = (task_obj.agent.role or "Agent").strip()
            reused = TaskOutput(description=task_obj.description, raw=raw, agent=role)
            task_obj.output = reused
            if key == 'diagnostic':
                # L'état de l'Analyste (fichiers committables, lacunes) est dérivé de sa sortie par son
                # guardrail : on le rejoue sur la sortie sauvegardée (aucun appel LLM). Au premier refus
                # le guardrail a déjà fusionné l'état ; le second appel applique la règle « accepter ».
                try:
                    accepted, _ = self.crew._diagnostic_guardrail(reused)
                    if not accepted:
                        self.crew._diagnostic_guardrail(reused)
                except Exception as e:
                    log.warning(f"reprise du diagnostic impossible, étape rejouée : {type(e).__name__}: {e}")
                    task_obj.output = None
                    self._reset_state()
                    break
            self.completed_keys.append(key)
            self.completed_outputs[key] = reused
            if self.on_task_output_complete is not None:
                try:
                    self.on_task_output_complete(role, raw, None)
                except Exception as e:
                    log.warning(f"échec du callback de reprise pour '{role}' : {type(e).__name__}: {e}")

    async def _run_attempts(self) -> None:
        while True:
            remaining = [(k, t) for k, t in self.selected if k not in self.completed_keys]

            # diagnostic_task rejouée depuis zéro : elle repart avec un budget guardrail intact (sinon le premier
            # souci constaté serait accepté avec un simple avertissement) et sans les fichiers fusionnés par une
            # tentative avortée (`_analyst_files`/`_not_extracted`, voir _diagnostic_guardrail).
            if any(k == 'diagnostic' for k, _ in remaining):
                self.crew._diagnostic_guardrail_failures = 0
                self.crew._analyst_files = []
                self.crew._not_extracted = {}

            if not remaining:
                # Toutes les tâches déjà terminées lors d'une tentative précédente : ne peut
                # arriver que si l'échec précédent survenait APRÈS la dernière (agrégation
                # CrewAI) et était retryable — plus rien à exécuter.
                return

            self.attempt_completed = 0

            def on_task_complete(task_output, _remaining=remaining):
                self._on_task_complete(task_output, _remaining)

            if self.on_step_change is not None:
                # to_thread : on_step_change écrit en base (bloquant, voir persist_current_step) et ce code tourne
                # sur la boucle asyncio ; les appels de _on_task_complete, eux, viennent du thread de kickoff_async.
                # step_keys[len(completed_keys)] : une reprise redémarre à la prochaine tâche RESTANTE.
                await asyncio.to_thread(self.on_step_change, self.step_keys[len(self.completed_keys)])

            dynamic_crew = Crew(
                agents=list({t.agent for _, t in remaining}),
                tasks=[t for _, t in remaining],
                process=Process.sequential,
                task_callback=on_task_complete,
                # 13 (pas 3) : partagé par TOUS les agents en séquence ; la QA, dernière et au max_iter le plus
                # élevé, hérite le plus de la latence cumulée d'un plafond trop bas. Le retry résumable absorbe
                # les 429 si cette valeur est trop optimiste face au quota Gemini réel.
                max_rpm=13,
                output_log_file='crew_execution.log',
                verbose=True
            )

            m = current_metrics.get()
            if m is not None:
                m.record_attempt()
            try:
                # track_edit_failures() : isole le suivi des échecs répétés de github_edit_file à CETTE exécution
                # (voir _edit_failure_counts dans github_edit_failures.py).
                with track_edit_failures():
                    await dynamic_crew.kickoff_async(inputs=self.inputs)
                return  # succès : toutes les tâches de `remaining` ont rejoint completed_keys
            except Exception as e:
                if await self._wait_before_retry(e, m):
                    continue  # nouvelle tentative, `remaining` recalculé sans les tâches déjà réussies
                await self._raise_step_error(e, remaining)

    def _on_task_complete(self, task_output, remaining) -> None:
        self.attempt_completed += 1
        crew_retry.quota_mgr.adaptive_pause(task_output)

        # Garde-fou : si CrewAI invoquait jamais task_callback plus de fois qu'il n'y a de tâches
        # dans CETTE tentative, ignorer l'appel en trop plutôt que de lever une
        # IndexError depuis le thread d'arrière-plan de kickoff_async.
        if self.attempt_completed > len(remaining):
            return
        key, task_obj = remaining[self.attempt_completed - 1]
        self.completed_keys.append(key)
        self.completed_outputs[key] = task_output

        # Persister la sortie complétée de l'agent pour affichage progressif
        if self.on_task_output_complete is not None:
            agent_name = (task_obj.agent.role or "Agent").strip()
            # Extraire la sortie brute (même logique que crew_workflow._format_crew_result)
            raw_output = getattr(task_output, "raw", None)
            raw_output = raw_output if raw_output is not None else str(task_output)
            # task_obj.execution_duration : propriété CrewAI (start_time/end_time posés
            # en interne pendant task_obj.execute()), None si l'un des deux est absent.
            duration = task_obj.execution_duration
            current = current_metrics.get()
            if current is not None:
                current.record_agent_done(agent_name, duration)
            try:
                self.on_task_output_complete(agent_name, raw_output, duration)
            except Exception as e:
                # Best-effort: ne pas laisser une erreur de persistance casser le workflow
                log.warning(f"échec du callback on_task_output_complete pour '{agent_name}' : {type(e).__name__}: {e}")

        # Annonce la tâche SUIVANTE qui démarre (pas celle qui vient de finir), en
        # indice GLOBAL sur l'ensemble des étapes (pas relatif à cette seule
        # tentative) : rien à annoncer après la dernière (le résultat est ensuite
        # juste agrégé/résumé, sans étape agent supplémentaire pour l'utilisateur).
        next_index = len(self.completed_keys)
        if self.on_step_change is not None and next_index < self.total_steps:
            self.on_step_change(self.step_keys[next_index])

    async def _wait_before_retry(self, error: Exception, metrics) -> bool:
        """Attend avant une nouvelle tentative et renvoie True si l'erreur est transitoire (quota, indisponibilité)
        et le budget de tentatives pas épuisé ; False sinon."""
        err_msg = str(error).lower()
        if not (crew_retry._is_retryable_error(err_msg) and self.retries < MAX_RETRIES):
            return False
        self.retries += 1
        wait_time = crew_retry._compute_backoff_wait(err_msg, self.retries, BASE_DELAY)
        if metrics is not None:
            metrics.record_rate_limit(wait_time)
        if self.on_step_change is not None:
            # Plus aucune étape n'est réellement en cours pendant l'attente avant
            # la prochaine tentative (jusqu'à plusieurs minutes) : même nettoyage
            # que pour l'échec définitif, voir _raise_step_error.
            await asyncio.to_thread(self.on_step_change, None)
        await asyncio.sleep(wait_time)
        return True

    async def _raise_step_error(self, error: Exception, remaining) -> NoReturn:
        # Erreur définitive (non retryable, ou retries épuisés) : identifie la tâche
        # en cours au moment de l'échec (celle juste après la dernière complétée,
        # GLOBALEMENT sur l'ensemble des tentatives) pour que le frontend affiche
        # "échec à l'étape X/Y" plutôt qu'une erreur générique. Si toutes les tâches
        # de cette tentative ont déjà déclenché leur callback (échec après coup, ex:
        # pendant l'agrégation du résultat par crewai), on ne dépasse pas total_steps.
        if self.attempt_completed < len(remaining):
            step_index = len(self.completed_keys) + 1
            agent_role = remaining[self.attempt_completed][1].agent.role
        else:
            step_index = self.total_steps
            agent_role = crew_workflow.FINALIZATION_ROLE
        if self.on_step_change is not None:
            # Plus aucune étape n'est réellement en cours à cet instant : sans ce
            # nettoyage, ExecutionHistory.current_step resterait affiché comme "suivi
            # en direct" (StepIndicator.tsx) sur la dernière étape connue alors que
            # rien n'est concrètement en train de s'exécuter — le message "Échec à
            # l'étape X/Y" (step_index/agent_role ci-dessus) indique déjà où ça s'est arrêté
            # (voir crew_workflow.CrewStepError, parseFailureDetail côté frontend).
            await asyncio.to_thread(self.on_step_change, None)
        raise crew_workflow.CrewStepError(step_index, self.total_steps, agent_role, error) from error

    def _release(self) -> None:
        # crew_cache.evict_memoized_cache_entries(crew) + t.callback = None : une SEULE fois pour
        # TOUTE l'exécution (succès ou échec définitif), pas à chaque tentative de la boucle.
        # Les méthodes @task de crewai sont MÉMOÏSÉES par (nom de méthode, id(self)) dans un cache
        # module-level SANS éviction native — voir crewai/project/utils.py et la docstring de
        # crew_cache.evict_memoized_cache_entries. Purger ce cache ENTRE deux tentatives casserait la reprise :
        # une tâche pas encore exécutée dont le guardrail référence à nouveau self.diagnostic_task()
        # (voir _diagnostic_retry_context) obtiendrait alors un objet Task tout NEUF, sans le
        # `.context` câblé plus haut, plutôt que l'objet mémoïsé qui le porte déjà.
        crew_cache.evict_memoized_cache_entries(self.crew)

        # t.callback = None sur TOUTES les tâches sélectionnées (pas seulement celles de la
        # dernière tentative) : filet de sécurité résiduel. execution.py instancie un
        # AppDevelopmentCrew() dédié à CHAQUE exécution, donc des objets Task neufs ; ceci ne couvre
        # qu'un cas résiduel très improbable (éviction ayant échoué ET CPython réutilisant l'id()
        # d'une instance déjà collectée) où une future exécution récupérerait via le cache ces mêmes
        # objets Task, `.callback` non nettoyée incluse.
        for t in self.selected_tasks:
            t.callback = None

    async def _final_result(self) -> str:
        return await crew_result.build_final_result(
            self.inputs, self.request_type, self.scope, self.step_keys, self.completed_outputs, self.selected_tasks
        )

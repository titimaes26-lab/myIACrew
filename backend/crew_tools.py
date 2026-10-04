"""Outils des agents propres à UNE exécution : lecture du code d'origine, commit des fichiers de l'Analyste, ouverture de la PR, vérification QA."""
from typing import Any

import crew_guardrails
import crew_workspace
from crew_state import CrewExecutionState
from crewai.tools import tool
from analyst_output import build_delivery_report, find_import_problems, format_manifest
from delivery import (
    build_pull_request_body, conventional_commit_message, extract_section,
)
from github_pull_request import open_or_update_pull_request
from github_batch import write_files_to_branch
from github_read import make_dir_lister, make_file_fetcher
from logs import get_logger

log = get_logger("crew")


def _never_cache(_args: Any = None, _result: Any = None) -> bool:
    return False

def _cache_success_only(_args: Any = None, result: Any = None) -> bool:
    return str(result or "").startswith("OK")

# CrewAI refuse d'exécuter deux fois de suite un appel d'outil IDENTIQUE (mode ReAct), cache ou
# non : pour réessayer après une erreur transitoire, les arguments doivent changer.
_RETRY_HINT = (
    "\nPour réessayer après une erreur transitoire, rappelle cet outil avec un commit_message "
    "légèrement différent (ex: ajoute « (2e essai) ») : un appel identique serait refusé."
)


class CrewToolsMixin(CrewExecutionState):
    """Méthodes de AppDevelopmentCrew qui fabriquent les outils d'agents ; lit l'état posé par _reset_execution_state."""

    def _base_source(self):
        """(lecteur de fichier, lecteur de dossier) du code d'ORIGINE, fixé une fois pour toutes :
        la branche de travail si elle existe déjà (un correctif d'un tour précédent s'y trouve),
        sinon la branche de base — comme diagnostic_task le demande à l'Analyste pour ses lectures.
        Jamais de repli d'une branche à l'autre sur une simple erreur : appliquer une modification à
        la version de la base alors que la branche de travail est plus récente écraserait son contenu."""
        cached = getattr(self, "_base_readers", None)
        if cached is not None:
            return cached
        target = getattr(self, "_repo_target", None)
        if target is None:
            workspace = self._workspace
            source = (lambda path: crew_workspace._read_local_file(workspace, path), lambda d: crew_workspace._list_local_dir(workspace, d))
        else:
            owner, repo = target
            branches = list(dict.fromkeys(b for b in (self._work_branch, getattr(self, "_base_branch", "main")) if b))
            fetch, chosen = None, None
            for branch in branches:
                fetch, chosen = make_file_fetcher(owner, repo, branch), branch
                if not getattr(fetch, "branch_missing", False):
                    break
            source = (fetch, make_dir_lister(owner, repo, chosen))
        self._base_readers = source
        return source

    def _read_base_file(self, path: str) -> tuple[str | None, str | None]:
        return self._base_source()[0](path)

    def _list_base_dir(self, directory: str) -> set[str] | None:
        return self._base_source()[1](directory)

    def _build_commit_analyst_files_tool(self):
        crew_self = self

        @tool("github_commit_analyst_files")
        def github_commit_analyst_files(commit_message: str) -> str:
            """
            Committe EN UN SEUL APPEL tous les fichiers rédigés par l'Analyste Diagnostic Technique,
            extraits automatiquement de sa réponse (balises <<<FICHIER: ...>>>) : tu n'as
            PAS à recopier leur contenu. À utiliser EN PRIORITÉ, après github_create_branch.
            Le repository, la branche de travail (ou, sans repository cible, l'espace de travail
            local) sont ceux de l'exécution en cours : tu n'as pas à les fournir.
            Mêmes garde-fous que github_write_files (branche principale refusée, fichiers
            Python/JSON/YAML invalides rejetés et listés dans une section REJETÉS).
            Arguments:
                commit_message (str): message de commit.
            """
            files = list(getattr(crew_self, "_analyst_files", []) or [])
            not_extracted = dict(getattr(crew_self, "_not_extracted", {}) or {})
            excluded_note = (
                "\nEXCLUS (non committables) : "
                + "; ".join(f"{p} ({reason})" for p, reason in sorted(not_extracted.items()))
                + ". Ne les committe JAMAIS, avec aucun outil : liste-les comme non livrés."
            ) if not_extracted else ""
            if not files and not_extracted:
                return f"INFO : aucun fichier committable.{excluded_note}"
            if not files:
                if getattr(crew_self, "_repo_target", None) is None:
                    # En local, cet outil est le SEUL moyen d'écrire : aucun repli possible.
                    return (
                        "INFO : aucun fichier n'a pu être extrait automatiquement de la réponse de "
                        "l'Analyste, rien n'a été écrit dans l'espace de travail local. Indique-le "
                        "dans ton rapport (aucun fichier livré)."
                    )
                return (
                    "INFO : aucun fichier n'a pu être extrait automatiquement de la réponse de "
                    "l'Analyste. Si elle contient quand même du code, committe-le avec "
                    "github_write_files ; sinon, indique dans ton rapport qu'il n'y avait rien à committer."
                )
            manifest = format_manifest(files)
            crew_self._commit_tool_used = True
            commit_message = conventional_commit_message(commit_message, getattr(crew_self, "_request_type", ""))
            rejections: dict[str, str] = {}
            target = getattr(crew_self, "_repo_target", None)
            if target is None:
                result = crew_workspace._write_files_locally(crew_self._workspace, files, rejections)
            else:
                owner, repo = target
                result = write_files_to_branch(owner, repo, crew_self._work_branch, commit_message, files, rejections)
                if not result.startswith("OK"):
                    # Commit entier refusé (branche protégée, collision de dossier, erreur GitHub).
                    for f in files:
                        rejections.setdefault(f["path"], f"commit refusé : {result[:200]}")
            # Cet appel fait foi pour SES fichiers : un échec précédent réparé par ce commit est
            # oublié, un nouveau refus est retenu (voir qa_verify_delivered_files).
            for f in files:
                crew_self._write_rejections.pop(f["path"], None)
            crew_self._write_rejections.update(rejections)
            for f in files:
                crew_self._committed_paths.discard(f["path"])
            if result.startswith("OK"):
                crew_self._committed_paths.update(f["path"] for f in files if f["path"] not in rejections)
            retry_hint = "" if result.startswith("OK") else _RETRY_HINT
            return f"{result}\nFichiers extraits de la réponse de l'Analyste :\n{manifest}{excluded_note}{retry_hint}"

        # Seul un SUCCÈS est mis en cache : un 2e appel après un "OK" ne réécrit pas tous les
        # fichiers, mais une erreur n'est jamais resservie (voir aussi _RETRY_HINT).
        github_commit_analyst_files.cache_function = _cache_success_only
        return github_commit_analyst_files

    def _delivery_gaps(self) -> dict[str, str]:
        """{chemin: raison} des fichiers de l'Analyste qui ne sont PAS dans le dernier commit réussi."""
        gaps = dict(getattr(self, "_not_extracted", {}) or {})
        for path, reason in (getattr(self, "_write_rejections", {}) or {}).items():
            if path not in self._committed_paths:
                gaps[path] = reason
        return gaps

    def _build_open_pull_request_tool(self):
        crew_self = self

        @tool("github_open_delivery_pull_request")
        def github_open_delivery_pull_request(title: str, summary: str = "") -> str:
            """
            Ouvre (ou met à jour, si elle existe déjà) la Pull Request de la branche de travail vers
            la branche de base. La description complète (fichiers livrés et non livrés, traçabilité,
            vérification manuelle) est GÉNÉRÉE à partir des faits : tu ne fournis que le titre et un
            court résumé. La PR est ouverte en brouillon si la livraison est partielle.
            Le repository et les branches sont ceux de l'exécution en cours.
            Arguments:
                title (str): titre court, format conventionnel (ex: « fix: corrige le calcul de la remise »).
                summary (str): résumé en 1 à 3 phrases des changements.
            """
            target = getattr(crew_self, "_repo_target", None)
            if target is None:
                return "INFO : aucun repository cible, donc aucune Pull Request à ouvrir (travail local)."
            owner, repo = target
            gaps = crew_self._delivery_gaps()
            body = build_pull_request_body(
                summary, getattr(crew_self, "_user_request", ""), getattr(crew_self, "_request_type", ""),
                sorted(crew_self._committed_paths), gaps,
                extract_section(getattr(crew_self, "_analyst_raw", ""), "Traçabilité"),
                commit_tool_used=getattr(crew_self, "_commit_tool_used", False),
            )
            url, message = open_or_update_pull_request(
                owner, repo, crew_self._work_branch, getattr(crew_self, "_base_branch", "main"),
                conventional_commit_message(title, getattr(crew_self, "_request_type", "")), body, draft=bool(gaps),
            )
            if url and url not in crew_self._pull_request_urls:
                crew_self._pull_request_urls.append(url)
            return message

        github_open_delivery_pull_request.cache_function = _cache_success_only
        return github_open_delivery_pull_request

    def _build_qa_verify_tool(self):
        crew_self = self

        @tool("qa_verify_delivered_files")
        def qa_verify_delivered_files() -> str:
            """
            Vérifie EN UN SEUL APPEL chaque fichier rédigé par l'Analyste : présence réelle sur la
            branche de travail (ou dans l'espace de travail local), comparaison EXACTE (diff) avec
            la version de l'Analyste, et check_syntax sur le contenu réellement présent — ainsi que
            les fichiers annoncés mais jamais committables. Chaque résultat est une preuve outillée
            [vérifié outil]. Aucun argument : la cible est celle de l'exécution en cours.
            """
            files = list(getattr(crew_self, "_analyst_files", []) or [])
            target = getattr(crew_self, "_repo_target", None)
            if target is None:
                workspace = crew_self._workspace
                fetch = lambda path: crew_workspace._read_local_file(workspace, path)  # noqa: E731
            else:
                fetch = make_file_fetcher(target[0], target[1], crew_self._work_branch)
            not_extracted = dict(getattr(crew_self, "_not_extracted", {}) or {})
            plan_output = getattr(getattr(crew_self, "_architecture_task_ref", None), "output", None)
            planned = crew_guardrails._planned_paths(getattr(plan_output, "raw", "") or "")
            scope_notes = crew_guardrails._scope_notes(planned, {f["path"] for f in files}, set(not_extracted)) if planned else None
            if target is None:
                list_dir = lambda directory: crew_workspace._list_local_dir(crew_self._workspace, directory)  # noqa: E731
            else:
                list_dir = make_dir_lister(target[0], target[1], crew_self._work_branch)
            import_notes = find_import_problems(
                files, list_dir, import_scope=dict(getattr(crew_self, "_edit_scope", {}) or {}),
            )
            return build_delivery_report(
                files, fetch,
                write_rejections=dict(getattr(crew_self, "_write_rejections", {}) or {}),
                not_extracted=not_extracted,
                scope_notes=scope_notes,
                import_notes=import_notes,
            )

        # Sans argument, un 2e appel (après une correction) resservirait sinon l'ancien rapport.
        qa_verify_delivered_files.cache_function = _never_cache
        return qa_verify_delivered_files

    def _build_local_read_tool(self):
        # Mémoïsé : les 4 agents qui l'utilisent (Product Designer, Architecte, Diagnostic, QA)
        # partagent la même instance d'outil au lieu d'en créer une fermeture dupliquée chacun.
        cached = getattr(self, "_local_read_tool", None)
        if cached is not None:
            return cached

        crew_self = self

        @tool("read_a_files_content")
        def read_a_files_content(file_path: str) -> str:
            """
            Lit le contenu d'un fichier de l'espace de travail LOCAL de cette conversation (mode
            sans repository GitHub cible). Avec un repository cible, utilise github_read_file.
            Arguments:
                file_path (str): chemin relatif du fichier (ex: 'src/App.tsx' ou 'index.html').
            """
            if getattr(crew_self, "_repo_target", None) is not None:
                return (
                    "INFO : un repository GitHub cible est défini pour cette exécution : lis ses "
                    "fichiers avec github_read_file, pas sur le disque local."
                )
            content, error = crew_workspace._read_local_file(crew_self._workspace, file_path)
            if content is None:
                return (
                    f"ERREUR_FICHIER_INEXISTANT : {error}. Inutile de réessayer la lecture de ce "
                    "fichier exact : note cette absence dans ton rapport et poursuis ton analyse."
                )
            return content if content.strip() else f"INFO : Le fichier '{file_path}' est vide."

        self._local_read_tool = read_a_files_content
        return read_a_files_content

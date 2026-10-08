"""Lecture déterministe (en Python, sans LLM) de la sortie de diagnostic_task.

diagnostic_task (voir tasksquestion.yaml) encadre chaque fichier livré par deux balises seules
sur leur ligne : "<<<FICHIER: chemin/du/fichier>>>" puis "<<<FIN_FICHIER>>>", avec le contenu COMPLET
entre les deux. Des balises explicites plutôt que des titres Markdown : un titre "### Fichier :"
se confond avec un commentaire, une citation de code ou un bloc ``` imbriqué, alors que ces
balises n'apparaissent jamais par hasard. Jusqu'ici, developer_agent devait recopier ce contenu
à la main dans ses appels d'outils — la principale source de troncature silencieuse du
pipeline. Ce module extrait ces fichiers une fois pour toutes, pour que :
- le guardrail de diagnostic_task rejette une sortie incomplète AVANT qu'elle n'arrive au
  Développeur (voir review_diagnostic_output) ;
- le Développeur committe le contenu extrait tel quel, sans le recopier (voir
  github_commit_analyst_files, crew_tools.py) ;
- la QA compare le contenu RÉELLEMENT présent sur la branche à celui rédigé par l'Analyste,
  sans devoir le faire "à l'œil" (voir build_delivery_report).

Ce module garde le contrôle d'une sortie (review_diagnostic_output) et le rapport de livraison ; la lecture des
balises, des modifications ciblées, des imports et des raccourcis vit dans analyst_blocks, analyst_edits,
analyst_imports et analyst_placeholders.
"""
import difflib
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

import analyst_blocks
import analyst_edits
import analyst_imports
import analyst_placeholders
from tools import check_syntax_content


MAX_DIFF_LINES_PER_FILE = 40
MAX_PARALLEL_FETCHES = 8


def review_diagnostic_output(
    text: str,
    read_base: Callable[[str], tuple[str | None, str | None]] | None = None,
    list_dir: Callable[[str], set[str] | None] | None = None,
    context_files: list[dict] | None = None,
) -> tuple[list[dict], str | None, set[str], dict[str, str]]:
    """(fichiers extraits, problème à renvoyer à l'Analyste, chemins des fichiers à raccourci,
    {chemin: raison} des fichiers annoncés mais inexploitables).

    Les fichiers à raccourci sont extraits mais ne doivent JAMAIS être committés tels quels,
    même si l'Analyste ne corrige pas sa réponse (voir _diagnostic_guardrail, crew_checks.py).

    read_base(chemin) -> (contenu, erreur) lit le fichier d'ORIGINE : il sert à résoudre les blocs
    <<<MODIFICATION: ...>>> en fichiers complets (sans lui, une modification est inexploitable).
    list_dir sert à vérifier les imports relatifs (voir analyst_imports.find_import_problems).
    """
    files, broken = analyst_blocks.parse_file_sections(text)
    edits, edit_broken = analyst_edits.parse_edit_sections(text)
    broken.update(edit_broken)
    edited_paths: set[str] = set()
    replaced_text: list[dict] = []
    for path, blocks in edits.items():
        if any(f["path"] == path for f in files):
            files = [f for f in files if f["path"] != path]
            broken[path] = "livré à la fois en <<<FICHIER>>> et en <<<MODIFICATION>>> : choisis un seul des deux"
            continue
        if read_base is None:
            broken[path] = "modification impossible : lecture du fichier d'origine indisponible"
            continue
        base, error = read_base(path)
        if base is None:
            broken[path] = (
                f"modification impossible : fichier d'origine illisible ({(error or 'erreur inconnue')[:120]}) "
                "— pour créer un nouveau fichier, utilise <<<FICHIER>>>"
            )
            continue
        content, error = analyst_edits.apply_edits(base, blocks)
        if content is None:
            broken[path] = error or "modification inapplicable"
            continue
        files.append({"path": path, "content": content})
        edited_paths.add(path)
        # Seul le texte AJOUTÉ est inspecté : un commentaire déjà présent dans le fichier d'origine
        # n'est pas un raccourci de l'Analyste.
        replaced_text.append({"path": path, "content": "\n".join(replace for _, replace in blocks)})
    problems: list[str] = []
    if broken:
        problems.append(
            "Fichiers annoncés mais inexploitables (ils ne seront PAS committés) :\n"
            + "\n".join(f"- {p} ({reason})" for p, reason in broken.items())
        )
    if not files and not broken:
        # "non réalisé" ne dispense du signalement que si la réponse ne contient AUCUN code :
        # des blocs de code hors balises sont un problème de format, pas un choix assumé.
        has_code = bool(analyst_blocks.ANY_FENCE.search(text or ""))
        if has_code or not analyst_blocks.NOT_DELIVERED_MARKER.search(text or ""):
            problems.append(
                "Aucun fichier exploitable trouvé : encadre CHAQUE fichier livré par une ligne "
                "'<<<FICHIER: chemin/relatif/du/fichier>>>' et une ligne '<<<FIN_FICHIER>>>', avec son "
                "contenu COMPLET entre les deux (ou, pour modifier un fichier existant, un bloc "
                "'<<<MODIFICATION: chemin>>>'). Si tu ne peux livrer aucun fichier, dis-le "
                "explicitement en le marquant 'NON réalisé' avec la raison."
            )
    placeholders = analyst_placeholders.find_placeholders([f for f in files if f["path"] not in edited_paths])
    placeholders += analyst_placeholders.find_placeholders(replaced_text)
    if placeholders:
        listing = "\n".join(f"- {p} ligne {n} : {line}" for p, n, line in placeholders[:10])
        problems.append(
            "Contenu incomplet détecté (commentaires de remplacement qui seraient committés tels "
            f"quels) :\n{listing}\nRéécris CES fichiers EN ENTIER, sans aucun raccourci, ou "
            "retire leurs balises et liste-les comme 'NON réalisé' dans ton plan."
        )
    import_problems = analyst_imports.find_import_problems(
        files, list_dir, context_files,
        import_scope={path: text["content"] for path, text in ((r["path"], r) for r in replaced_text)},
    )
    if import_problems:
        problems.append(
            "Incohérences entre fichiers (imports) :\n"
            + "\n".join(f"- {issue}" for issue in import_problems[:10])
            + "\nCorrige-les (les fichiers concernés restent à fournir EN ENTIER ou par modification)."
        )
    faulty = {p for p, _, _ in placeholders}
    return files, ("\n\n".join(problems) if problems else None), faulty, broken


def format_manifest(files: list[dict]) -> str:
    return "\n".join(
        f"- {f['path']} ({len(f['content'].splitlines())} lignes)" for f in files
    ) or "- (aucun fichier)"


def build_delivery_report(
    files: list[dict],
    fetch: Callable[[str], tuple[str | None, str | None]],
    write_rejections: dict[str, str] | None = None,
    not_extracted: dict[str, str] | None = None,
    scope_notes: list[str] | None = None,
    import_notes: list[str] | None = None,
) -> str:
    """Rapport par fichier : présence réelle, identité avec la version de l'Analyste, syntaxe.

    fetch(path) -> (contenu, erreur) : contenu None en cas d'échec, avec l'erreur préfixée par
    analyst_blocks.FILE_ABSENT (absence confirmée) ou analyst_blocks.PRESENT_UNREADABLE (présent mais illisible) ; toute autre
    erreur rend le fichier NON VÉRIFIABLE. write_rejections : {chemin: raison} des fichiers
    refusés au dernier commit — relus quand même (un autre outil a pu les écrire depuis), mais
    signalés NON LIVRÉS s'ils ne sont pas identiques à la version de l'Analyste. not_extracted :
    {chemin: raison} des fichiers annoncés par l'Analyste mais jamais committables.
    scope_notes : écarts entre les fichiers livrés et le plan de l'Architecte ; import_notes :
    incohérences d'imports entre fichiers livrés (voir analyst_imports.find_import_problems) — deux sections ajoutées
    à la fin, sans quoi ces contrôles n'apparaîtraient pas dans le rapport de la QA.
    Les résultats sont étiquetés [vérifié outil] : ils proviennent d'une comparaison exacte en
    Python et de check_syntax_content, jamais d'une lecture LLM.
    """
    write_rejections = write_rejections or {}
    rows = [
        f"### {path}\n- Présence : NON LIVRÉ, jamais committable [vérifié outil] — {reason}"
        for path, reason in sorted((not_extracted or {}).items())
    ]
    if not files and not rows:
        return (
            "INFO : l'Analyste n'a fourni aucun fichier exploitable pour cette exécution : rien "
            "à comparer. Vérifie le code avec github_read_file/check_syntax si nécessaire."
        )
    # Lectures en parallèle (une par fichier) : sur un gros lot, des allers-retours GitHub
    # séquentiels ajouteraient des dizaines de secondes à un seul appel d'outil de la QA.
    with ThreadPoolExecutor(max_workers=MAX_PARALLEL_FETCHES) as pool:
        fetched = list(pool.map(lambda f: fetch(f["path"]), files))
    for f, (actual, error) in zip(files, fetched):
        path, expected = f["path"], f["content"]
        identical = actual is not None and actual.rstrip("\n") == expected.rstrip("\n")
        if path in write_rejections and not identical:
            rows.append(
                f"### {path}\n- Présence : NON LIVRÉ, écriture refusée au commit [vérifié outil] — "
                f"{write_rejections[path]}"
            )
            continue
        if actual is None and (error or "").startswith(analyst_blocks.PRESENT_UNREADABLE):
            rows.append(
                f"### {path}\n- Présence : PRÉSENT [vérifié outil]\n"
                f"- Contenu : NON VÉRIFIABLE, fichier illisible par l'outil — {error}"
            )
            continue
        if actual is None and (error or "").startswith(analyst_blocks.FILE_ABSENT):
            rows.append(f"### {path}\n- Présence : ABSENT [vérifié outil] — {error}")
            continue
        if actual is None:
            rows.append(
                f"### {path}\n- Présence : NON VÉRIFIABLE (erreur de l'outil, pas une preuve "
                f"d'absence) — {error or 'erreur inconnue'}"
            )
            continue
        if identical:
            match_line = "- Contenu : IDENTIQUE à la version de l'Analyste [vérifié outil]"
        else:
            diff = list(difflib.unified_diff(
                expected.splitlines(), actual.splitlines(),
                fromfile="analyste", tofile="branche", lineterm="", n=1,
            ))
            shown = diff[:MAX_DIFF_LINES_PER_FILE]
            more = f"\n... ({len(diff) - len(shown)} lignes de diff supplémentaires)" if len(diff) > len(shown) else ""
            match_line = (
                "- Contenu : DIVERGENT de la version de l'Analyste [vérifié outil]\n"
                "```diff\n" + "\n".join(shown) + more + "\n```"
            )
        syntax = check_syntax_content(actual, path).strip()
        rows.append(
            f"### {path}\n- Présence : PRÉSENT [vérifié outil]\n{match_line}\n"
            f"- check_syntax : {syntax} [vérifié outil]"
        )
    if scope_notes is not None:
        rows.append("### Périmètre (plan de l'Architecte) [vérifié outil]\n" + (
            "\n".join(f"- {note}" for note in scope_notes) or "- Conforme : aucun fichier hors plan, aucun fichier prévu manquant."
        ))
    if import_notes is not None:
        rows.append("### Cohérence des imports entre fichiers livrés [vérifié outil]\n" + (
            "\n".join(f"- {note}" for note in import_notes) or "- Aucune incohérence détectée (imports relatifs et exports nommés)."
        ))
    return "\n\n".join(rows)

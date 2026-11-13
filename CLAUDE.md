# myIACrew — instructions pour Claude

Studio d'agents CrewAI : l'utilisateur décrit une demande, une équipe d'agents (Designer → Architecte → Analyste → Développeur → QA) la traite et livre sur GitHub. Le document de référence est `PRD.md` ; en cas de doute, il fait foi (et son journal en tête de fichier dit POURQUOI chaque choix a été fait).

## Structure
- `backend/` : API FastAPI + SQLModel, orchestration CrewAI (Python 3.11 minimum, 3.12 en production), Gemini via google-genai.
- `frontend/` : React 19 / Vite / TypeScript, Supabase pour l'authentification seulement.
- `.claude/skills/` : skills du projet (revue de code, taille des fichiers, PRD…).
- Hébergement : Render (backend, offre gratuite : s'endort, 512 Mo), Vercel (frontend), Supabase (base et authentification), LangSmith (traces).

## Commandes
- Backend (depuis `backend/`) : `python -m pytest -q` · `ruff check .` · `mypy` — les trois sont dans la CI.
- Frontend (depuis `frontend/`) : `npm run lint` · `npm run typecheck` · `npm test` · `npm run build`.
- CI (`.github/workflows/ci.yml`, sur `main` et `test`) : jobs `backend (3.11)`, `backend (3.12)`, `frontend`, `audit`. Lire les JOBS, pas seulement le statut du run : `audit` est informatif (`continue-on-error`) et peut être rouge sous un run « vert ».

## Règles de code
- **Aucun fichier de plus de 300 lignes** (Python et tests) ; un module = une responsabilité. Composants React : 200 lignes au plus (skill `react-file-size-manager`). Quand un fichier approche 300 : extraire un module avant d'ajouter.
- **Complexité ruff C901 ≤ 10** par fonction : extraire une méthode plutôt que d'ajouter une branche.
- **Expressions régulières en temps linéaire** : `[^\S\n]` et non `\s` sous `MULTILINE`, quantificateurs possessifs (`*+`) pour les blancs voisins. `tests/test_regex_linear_time.py` rejoue tous les motifs et échoue sur un motif quadratique.
- mypy vérifie les modules listés dans `backend/mypy.ini` : ajouter tout nouveau module à `files`.
- Commentaires en français, expliquant le POURQUOI (souvent un numéro d'exécution ou un défaut observé).
- Dépendances épinglées (`requirements.txt` + `requirements.lock`) ; ne pas en ajouter sans régénérer le verrou.
- Jamais de secret dans le code, les tests, les commits ou le PRD (clés LangSmith/Gemini, `DATABASE_URL`, jetons GitHub).

## Tests
- La suite doit passer en ordre normal, **inversé et mélangé** (aucune dépendance d'ordre entre fichiers).
- Les tests de bout en bout de l'orchestration utilisent le vrai `Crew` avec des LLM factices (`tests/test_agent_context.py`) : ils enregistrent les prompts réellement reçus par chaque agent. Les lancer avec `monkeypatch.chdir(tmp_path)` : CrewAI écrit `docs/*.md` dans le dossier courant et ne doit jamais salir le dépôt.
- Chaque correctif a un test qui échoue sans lui : faire une **mutation** (retirer ou inverser le correctif dans une copie, vérifier qu'un test échoue). Un mutant qui survit = un test trop faible ou un chemin jamais atteint.
- Ne jamais désactiver, ignorer ou assouplir un test pour obtenir du vert.

## Architecture des agents (à ne pas casser)
- **Contexte explicite par étape** : `crew_run.CONTEXT_PLAN` (Designer ← rien ; Architecte ← designer ; Analyste ← designer + architecte ; Développeur ← analyste ; QA ← designer + architecte + analyste + développeur). La demande en cours (`{user_request}`) est dans les tâches ; le Développeur la reçoit seulement pour nommer commit et PR.
- **La planification interne de CrewAI ne lit que la description de la tâche**, jamais le contexte : ce qu'un agent doit absolument voir avant de planifier va dans la description (`crew_checks._brief_analyst`, avec `_original_description` : CrewAI reconstruit la description à chaque kickoff).
- **État Python déterministe, pas de mémoire partagée entre LLM** : `_analyst_files`, `_committed_paths`, `_architecture_task_ref`… vivent sur l'instance du crew (une par exécution) et sont déclarés dans `crew_state.py`.
- **Garde-fous** (`crew_guardrails.py`, `design_spec.py`, `crew_checks.py`) : un refus avec la liste exacte des défauts, puis acceptation avec une note (jamais d'échec de l'exécution pour un défaut de contenu). Budget de refus remis à zéro quand l'étape est rejouée (`_reset_for_replay`).
- **Analyste** : lecture seule ; ses fichiers sont extraits en Python des balises `<<<FICHIER: …>>>` (`analyst_blocks`, `analyst_edits`, `analyst_recovery` pour les blocs non balisés). Un fichier dont les imports ne mènent à rien n'est PAS committé (`analyst_import_drop`). Dans le doute (dossier illisible, panne GitHub) : ne rien retirer.
- **Outils de lecture GitHub épinglés** sur le dépôt cible de l'exécution (`crew_read_tools`) ; sans dépôt cible, lecture locale seulement.
- **Modèles** : `llm_models.AGENT_MODELS` (un par agent), limiteur de débit par modèle (`llm_limiter`), repli automatique sur le modèle léger (`llm_fallback`) sur 503, 504 ou 404. Réglages sans déploiement : `GEMINI_MODEL`, `GEMINI_DIAGNOSTIC_MODEL`, `DIAGNOSTIC_TIMEOUT_S`, `LLM_RATE_MARGIN`, `LLM_FALLBACK_TTL_S`… (liste : `PRD.md` §6.2).
- Un défaut observé dans une exécution se traite à la cause (lire la base et les journaux d'abord), pas au symptôme : les exécutions 137 à 139 ont montré que la cause était rarement « le modèle est mauvais ».

## Flux de travail
- Branche de travail unique : `claude/agent-reasoning-improvements-ri7kug`. Commits file par file (`git add` explicite, jamais `git add -A` : des fichiers générés ont déjà été commités par erreur), messages en français, terminés par les lignes d'attribution demandées.
- **Pousser sur `test` et `main` seulement sur demande de l'utilisateur**, sans force, en avance rapide (vérifier `git merge-base --is-ancestor` avant). Jamais de commit vide ni de fermeture/réouverture pour relancer la CI. Ne pas créer de pull request sans demande.
- Après un push : attendre la CI (pytest ≈ 4–5 min), relire les jobs de chaque run, puis l'état du déploiement Render. Ne déclarer « vert » que ce qui a été lu.
- Chaque évolution est consignée dans `PRD.md` : nouvelle entrée de version en tête du journal (`> - X.YY (MM-JJ) : …`), numéro et date de l'en-tête, et mise à jour des sections concernées (§2.x, §6.2 pour une variable d'environnement, table des modules).
- Cycle habituel : revue de code → l'utilisateur dit quoi corriger → corriger avec tests et mutations → commit et push de la branche → push sur `test`/`main` sur demande → lecture de la CI.
- Rendre compte avec honnêteté : ce qui est vérifié, ce qui ne l'est pas (comportement réel des modèles, dépôt PlatformGame hors périmètre), et les points ouverts.

## Périmètre et sécurité
- L'accès GitHub est limité à `titimaes26-lab/myiacrew`. Le dépôt cible des exécutions (par exemple `PlatformGame`) n'est PAS lisible par Claude : ne pas deviner son contenu, le demander à l'utilisateur.
- Lecture seule sur Supabase et Render pour les diagnostics (tables `executionhistory`, `agentrun`, `executioncheckpoint`) ; aucune migration ni changement de configuration sans demande explicite.
- Fichiers générés à ne pas commiter : `backend/docs/*.md` (sorties des agents), `workspace/`, journaux.

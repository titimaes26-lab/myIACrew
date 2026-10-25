# PRD — myIACrew (Studio CrewAI)

> Version : 1.96 — Mise à jour le 2026-10-22
> Adapté du gabarit `game-prd-creator` : ce repo n'est pas un jeu mais un orchestrateur multi-agents ; les sections ont été
> ajustées au produit réel.
> Historique (condensé) :
> - 1.0–1.2 (09-17 → 10-02) : version initiale, quota Gemini, 6 agents, contrôles de qualité, conversations.
> - 1.3–1.8 (10-02 → 10-03) : mesure de performance par agent et tableau de bord, historique en sélection multiple, erreurs
>   typées, orphelines, validation des entrées, contrôle préalable GitHub.
> - 1.9–1.16 (10-03 → 10-04) : reprise à l'étape en échec et seconde tentative, système de design, CI/lint/types/tests, historique
>   des performances, `DELIVERY_FAILED`, infobulles p50/p95.
> - 1.17–1.22 (10-04) : performance de l'Architecte (aperçu du dépôt, cache de lectures, plafond de sortie, `scope` PETIT/GRAND,
>   plan précédent).
> - 1.23–1.26 (10-04) : interface (sommaire du résultat, aperçu avant lancement, temps restant estimé, notification de fin).
> - 1.27–1.30 (10-04) : revue complète (écritures limitées à `crewai/…`, endpoints en threads, moteur SQL résilient, balayage
>   atomique, logs `logging` sur une ligne).
> - 1.31 (10-04) : consolidation du PRD (variantes de workflow, tableaux de modules, variables d'environnement, chiffres de tests).
> - 1.32 (10-04) : résumé final structuré en 3 blocs, ligne de faits calculée en Python, résumé de repli sans modèle ; un 404 de
>   proxy ne marque plus une exécution supprimée.
> - 1.33 (10-04) : échec avec bloc « Déjà réalisé avant l'échec » (sans modèle) et prochaines étapes du résumé cliquables
>   (préremplissent la saisie).
> - 1.34 (10-04) : revue complète : fichiers de CI/environnement/déploiement jamais écrits par un agent, une seule exécution
>   `running` par conversation garantie par un index unique partiel, plafonds par utilisateur (exécutions simultanées, par heure,
>   qualifications par minute).
> - 1.35 (10-04) : suite de la revue : plafond horaire compté sur un journal de lancements non supprimable, 1 exécution simultanée
>   par défaut, refus avant toute création de conversation, liste des fichiers sensibles complétée (modèles `.env.example`
>   autorisés).
> - 1.36 (10-04) : un double envoi dans la même conversation garde le message de conflit précis (409) avant les plafonds par
>   compte ; question ouverte RLS Supabase.
> - 1.37 (10-04) : limiteur global des requêtes Gemini (plafond commun par minute, pause commune après un 429) sous tous les
>   appels au modèle.
> - 1.38 (10-04) : limiteur Gemini : pause commune plafonnée à 120 s (quota journalier : échec rapide), détection des 429 par code HTTP.
> - 1.39 (10-04) : diagnostic : planification en effort « low » (un seul appel de plan, observation par heuristique, plan et
>   étapes bornés) au lieu de l'observation LLM par étape.
> - 1.40 (10-04) : diagnostic : itérations par étape portées à 8 et réglables, contrepartie de l'effort « low » documentée.
> - 1.41 (10-04) : une seule exécution de crew à la fois (`MAX_CONCURRENT_EXECUTIONS`, 1 par défaut) : les suivantes attendent
>   leur tour (« queued »).
> - 1.42 (10-04) : durée maximale d'une exécution (`EXECUTION_TIMEOUT_S`, 20 min : le créneau est rendu), position dans la file
>   affichée, « en cours depuis » sans le temps de file.
> - 1.43 (10-04) : le crew arrêté pour durée maximale n'écrit plus (base, GitHub, Pull Request) ; arrêt attribué au quota quand
>   l'attente a dominé.
> - 1.44 (10-04) : cache de validation des jetons (60 s), dépendances directes épinglées, plus de `print` (tout par `logging`).
> - 1.45 (10-04) : verrouillage complet des dépendances (`requirements.lock`), CI en Python 3.11 et 3.12, une seule validation de
>   jeton à la fois par jeton.
> - 1.46 (10-04) : l'issue d'une validation (succès, 401, 503) est partagée entre requêtes simultanées, veille des failles (job
>   `audit`, Dependabot), cohérence requirements/verrou testée.
> - 1.47 (10-04) : `useConversation.ts` (728 lignes) découpé en hooks dédiés (dossier `hooks/conversation/`), comportement inchangé.
> - 1.48 (10-04) : `sendMessage` et l'effet de sondage découpés en fonctions nommées (`progressMerge`), `useSendOutcomes` stable,
>   tests unitaires des pièces extraites.
> - 1.49 (10-04) : `ChatInput.tsx` (303 lignes) découpé (`useRepoTarget`, `RepoTargetPanel`, `WorkflowTypeSelect`, `ChatComposer`) et testé.
> - 1.50 (10-04) : saisie : Entrée n'envoie plus pendant une saisie assistée (clavier virtuel, IME) ; API de `useRepoTarget` regroupée.
> - 1.51–1.58 (10-04) : découpage du backend en modules cohérents, un fichier à la fois, sans changement de comportement :
>   `main.py` (2180 lignes), `crewquestion.py` (1990), `github_tools.py` (1279), `analyst_output.py` (703) et `agent_metrics.py`
>   (534) ; plus de 60 tests ajoutés (cas limites, `CrewRun`, commit par lot), vérifiés par mutation du code ; deux textes d'agent
>   altérés par un renommage automatique restaurés, avec un test qui interdit ce défaut.
> - 1.59–1.61 (10-04) : tests répartis par thème (aucun fichier de plus de 300 lignes, 800 cas inchangés, ordre inverse vérifié) ;
>   fixtures communes dans `tests/conftest.py` ; isolation de l'état d'exécution entre tests.
> - 1.62 (10-04) : PRD remis en phase avec le code : table des modules (§3.1) et organisation du code (§3.3), noms de fonctions
>   déplacés, couverture mypy, organisation des tests et règle des 300 lignes (§6.1).

> - 1.63 (10-04) : revue complète : journal CrewAI désactivé par défaut (`CREW_LOG_FILE`) et retiré du suivi git ; la coupure en fin de fichier JS/TS (accolade, `${`, template ou commentaire non terminé) bloque désormais le commit, lecteur de délimiteurs corrigé (apostrophes d'un texte JSX, templates imbriqués : 6 fichiers sur 122 de `frontend/src` à tort signalés avant, 0 après) ; `/messages` borné (`limit`, `before_id`) ; `/repo-targets` agrégé en SQL ; `tools.py` testé.
> - 1.64 (10-04) : revue de la 1.63 : le lecteur de délimiteurs ne coupe plus une chaîne à la fin de la ligne (un attribut JSX sur plusieurs lignes dans une expression `{…}` faisait déclarer le fichier « tronqué » à tort) ; limites connues documentées (`/messages` à 100 tours sans bouton « charger plus », guillemet simple isolé d'un texte JSX).
> - 1.65 (10-05) : revue complète n°2 : les 23 tests de `parse_completed_agents` visent enfin la fonction de production (ils testaient une copie ; +3 cas, déplacés dans `tests/`) ; une vingtaine de commentaires périmés corrigés et un test qui vérifie les renvois `x.py::nom` ; `execute_workflow` fait son accès base (conversation, plafonds, insertion) hors de la boucle d'événements (`asyncio.to_thread`).
> - 1.96 (10-25) : revue n°16 — même famille de défaut que la 1.95 (ReDoS), cherchée dans TOUT le backend par un scanner qui joue chaque expression régulière (171 motifs : module + appels directs) sur 31 entrées pathologiques plus des entrées bâties sur les mots littéraux du motif. 11 motifs réellement super-linéaires, mesurés sur 30 Ko (taille d'une sortie de modèle ; un modèle qui boucle sur des espaces ou des retours à la ligne peut en émettre davantage) : `ANY_FENCE` 13,9 s (détection de code dans la sortie de l'Analyste), `_OTHER_PLAIN_SECTION` > 25 s, `_ARCH_FILE_LINE` 6 s, `_FILE_LINE_REF` 3-4 s (preuves du rapport QA, un simple long mot), `_TRAILING_PAREN`, `_IMPORT_SIDE_EFFECT`, `_IMPORT_FROM`, `_CLOSING_KEYWORD` (corps de la PR), `_NONE_VALUE`, `FILE_BLOCKS` (3 s à 100 Ko), et la demande de l'utilisateur dans `extract_user_request` (~1 s de gel par requête avec 20 000 espaces). Corrigés en temps linéaire : `[^\S\n]*` à la place de `^\s*` sous MULTILINE (qui traversait les lignes vides depuis chaque début de ligne), quantificateurs possessifs là où deux `\s*` se recouvraient, `strip_file_blocks` (recherche de la balise de fin une seule fois), `_strip_trailing_paren`, `_request_part`, lookbehind d'un caractère pour `fichier:ligne`. ÉQUIVALENCE : comparaison aléatoire avec les anciennes expressions, plus de 500 000 cas, 0 différence — elle a trouvé DEUX erreurs de ma réécriture avant livraison (demande vide confondue avec absente ; `((a)` mal retiré) et le comportement glouton de l'ancien `\s*` après « : ». `tests/test_regex_linear_time.py` (+ `regex_scan_support.py`) rejoue en permanence tous les motifs du backend sur des entrées de 20 000 caractères dans un interpréteur neuf, un enfant tué par motif (limite 0,5 s) : une régression échoue au lieu de bloquer la suite ; 8 s ; 4 motifs appelés uniquement avec `.match` sont dispensés, et un test vérifie qu'ils le restent. 61 tests de cas limites (`test_linear_rewrites.py`). Mutations : l'ancienne forme de chaque motif fait échouer le scanner (6 sur 6, une seulement après avoir ajouté « longue suite AVANT le mot » aux entrées). 1441 tests verts (normal, inversé, mélangé), ruff (dont `C901`), mypy.
> - 1.95 (10-25) : revue n°15 — DÉNI DE SERVICE par expression régulière (ReDoS), défaut de mon étape 1. Les motifs de `input_guards` commençaient par `\b[\w.-]*mot-clé` : temps QUADRATIQUE sur une longue ligne de caractères « mot » ; `re` garde le GIL, donc un tracé SVG de 100 Ko (> 20 s mesuré), une suite d'identifiants pointés de 110 Ko (> 20 s), `token` × 10 000 (7,7 s) ou `TOKEN` × 10 000 (11 s) figeait TOUT le processus (API, sondage, autres exécutions) ; sur la demande, le masquage tourne même dans la boucle d'événements. Mesure après : 0,01 à 0,02 s sur les mêmes entrées, 0,14 s pour 1 Mo de code. Corrections : préfixe non borné supprimé (seule la valeur est remplacée, le nom n'a pas besoin d'être capturé), suite du mot-clé bornée (`{0,40}`, `{0,60}?`), longs quantificateurs POSSESSIFS (`{32,}+`, Python 3.11), balises de clé privée cherchées par `str`/`re` linéaires (`_private_key_spans`) au lieu de `.*?` DOTALL, `exfiltr\w{1,20}` ; `delivery_guards` : `curl|wget|base64 … | sh` par SEGMENTS de ligne (`_piped_into_shell`) et paires « A puis B » (`iex`/téléchargement, `nc`/`-e`, `dd`/`of=/dev/…`) par une seule recherche par ligne (`_then_on_same_line`) — linéaires ET sans borne, donc une commande allongée par du remplissage n'échappe pas au contrôle (une simple borne à 500 caractères l'aurait permis : essayée et écartée). Équivalence : 60 000 cas aléatoires, 0 différence pour tous les motifs sauf Slack et `sk-` ; 60 000 scripts, 0 différence. Seul écart voulu : une clé `sk-…` ou Slack suivie d'un « _ » est maintenant masquée en entier (l'ancien `\b` final avec retour arrière en laissait la fin visible). 23 tests ajoutés (13 entrées pathologiques < 2 s, ordre des paires, clé suivie de « _ », blocs de clé privée) ; mutations : l'ancien fichier, les préfixes non bornés et `exfiltr\w+` tuent les tests (blocage > 90 s ou échec), 8 mutations du contrôle des scripts tuées ; 3 survivantes équivalentes en performance. 1378 tests verts (normal, inversé, mélangé), ruff, mypy.
> - 1.94 (10-24) : revue n°14 — TROU de l'étape 1 : le masquage des secrets (`input_guards`) n'était branché que sur l'exécution (`crew_inputs`) ; `/api/qualify` (`analyze_user_request`) envoyait la demande et l'historique BRUTS à Gemini, et `save_analysis_report` écrivait la demande brute dans `docs/qualification_report.md` (fichier jamais lu, non ignoré par git). Corrigé : `guard_text` sur la demande et l'historique au début de `analyze_user_request` (le prompt, le résumé de repli et le rapport en héritent), sur le texte écrit dans le rapport, et `docs/qualification_report.md` ajouté à `backend/.gitignore`. 6 tests (`test_qualification_secrets.py`, texte réellement confié à l'agent capturé) ; 4 mutations tuées. Les chemins qui envoient du texte utilisateur au modèle sont maintenant : qualification (masqué), exécution (`crew_inputs`, masqué), fichiers lus par les outils (encadrés + masqués). 1355 tests verts (normal, inversé, mélangé), ruff, mypy. Limite : si le service est lancé depuis la racine du dépôt, le rapport est écrit dans `./docs/` (non couvert par le `.gitignore` du dossier `backend`).
> - 1.93 (10-24) : revue n°13 — un TEST INTERMITTENT (`test_one_execution_runs_at_a_time_by_default_and_the_others_queue`) échouait environ 1 fois sur 100 sous charge (« Multiple rows were found when exactly one was required »). Cause : la base SQLite de test était EN MÉMOIRE avec UNE connexion partagée (`StaticPool`) entre tous les threads du code testé (`asyncio.to_thread`) : leurs transactions s'entremêlaient. Aucun bug de production (une connexion par thread). `tests/conftest.py` : base SQLite sur FICHIER temporaire, une par test (supprimée à la fin), `timeout` 30 s. Mesure : avant, 1 échec sur 100 exécutions de `test_execution_deadline.py` lancées 10 en parallèle, et 2 sur 50 sur un groupe de 11 fichiers sensibles au temps ; après, 0 sur 200 et 0 sur 120. Coût : la suite passe d'environ 15 s à 22-25 s. D'autres tests (`test_error_codes`, `test_errors`, `test_execution_heartbeat`) gardent leur propre base en mémoire partagée, sans échec observé. Annotation de retour de `_open_repo_branch`. 1349 tests verts (normal, inversé, mélangé), ruff, mypy.
> - 1.92 (10-24) : les 8 fonctions de complexité cyclomatique > 10 sont découpées (`parse_edit_sections` 15 → automate `_EditParser`, `review_diagnostic_output`, `get_current_user`, `_diagnostic_guardrail`, `_build_commit_analyst_files_tool`, `check_github_access`, `make_file_fetcher`, `summarize`), sans changement de comportement. PREUVE : chaque ancienne version a été conservée dans un dossier jetable et comparée à la nouvelle sur des entrées aléatoires — 60 000 textes (`parse_edit_sections`), 40 000 × 2 (`review_diagnostic_output`), 20 000 séquences de 1 à 3 tentatives (`_diagnostic_guardrail`, retours ET état), 6 000 (commit des fichiers de l'Analyste, 16 combinaisons), 3 528 combinaisons d'erreurs GitHub (`check_github_access`), 1 260 (`make_file_fetcher`), 8 000 (`summarize`) : 0 différence. `get_current_user` (concurrence) : mutations sur le nouveau découpage ; 3 survivantes qui n'avaient AUCUN test (jeton non nettoyé, configuration Supabase manquante → 500, issue d'échec « jamais lue » signalée par asyncio) → `tests/test_auth_entry.py`, toutes tuées. Règle `C901` (max 10) ajoutée à `backend/ruff.toml` : la CI refuse désormais une fonction trop complexe. 1349 tests verts (normal, inversé, mélangé), ruff, mypy.
> - 1.91 (10-24) : revue n°12 (complète). (1) CONTOURNEMENT fermé : `github_edit_file` ne contrôle que le texte AJOUTÉ, presque jamais du JSON valide ; l'analyse JSON des scripts de cycle de vie de `package.json` (`delivery_guards`) était donc sautée et `"postinstall": "curl … | sh"` passait en fragment (vérifié). Les clés `preinstall/install/postinstall/prepare…` sont maintenant cherchées par motif dans le texte brut (fichier entier ou fragment), valeur décodée (`\u007c` = `|`). (2) trois nouveaux avis `filelock` 4.0.10 (CVE-2026-112940/112913/112907, `ReadWriteLock` SQLite et verrous logiciels Windows, non utilisés) publiés dans la journée : verrou → 4.2.4 (`huggingface_hub` exige seulement `>=3.10`) ; `pip-audit` local : plus aucune faille hors les 7 avis examinés. (3) `opentelemetry-sdk` et `opentelemetry-exporter-otlp-proto-http` 1.45.1 déclarés dans `requirements.txt` (importés directement par `telemetry.py`, ils n'arrivaient que par CrewAI). (4) `npm audit fix` : `undici` 8.12.3 et `zod` 4.6.7 (développement : jsdom, eslint-plugin-react-hooks), `package.json` inchangé, 0 faille même hors `--omit=dev`. Vérifié dans un environnement neuf : `pip check` propre pour l'application, ruff, mypy (68 fichiers), 1340 tests verts (normal, inversé, mélangé), frontend eslint/tsc/build, 249 tests (normal + 3 passages mélangés). Mutations : toutes tuées (dont le décodage des échappements, d'abord survivant). Non traité : 8 fonctions de complexité > 10 (`parse_edit_sections` : 15).
> - 1.90 (10-23) : revue n°11 — le masquage des secrets abîmait du code ordinaire que les agents lisent puis réécrivent : le motif « nom = valeur » de `input_guards` transformait `const tokenizer = createTokenizerInstance(options)` en `const tokenizer = [SECRET MASQUÉ]` (idem `token = request.headers.get(...)`, `token_url = "https://…"`, `private_key_path = "/etc/…"`) ; une modification par `github_edit_file` échouait (l'`old_string` recopié n'existait pas) et un fichier réécrit en entier pouvait recevoir le marqueur, que `delivery_guards` laissait passer. Corrigé : le motif ne vaut plus que (a) pour une valeur ENTRE GUILLEMETS de 16 caractères ou plus, sans « . » ni « / », mêlant lettres et chiffres, ou (b) pour une ligne de style `.env` (`NOM_EN_MAJUSCULES=valeur`, `export` admis) ; `Bearer` exige aussi lettres et chiffres ; un mot de passe en prose, sans guillemets ni forme `.env`, n'est donc PAS masqué (les formats connus restent le filet fiable) ; `delivery_guards` refuse tout contenu livré contenant `[SECRET MASQUÉ]` (message qui renvoie vers `github_edit_file` sur un extrait sans marqueur), par les quatre chemins d'écriture ; l'avertissement d'injection ne se déclenche plus sur « You are now ready… » / « Tu es maintenant connecté » (rôle exigé : assistant, DAN, admin, pirate…). 18 lignes de code ordinaire en non-régression ; 1317 tests verts (normal, inversé, mélangé), mutations toutes tuées. Non vérifié : CrewAI propage bien le contexte aux threads (lu dans `crew.py` et `agent/core.py`), mais aucune vraie exécution n'a encore levé `TokenBudgetExceeded`.
> - 1.89 (10-23) : fin de l'étape 3 : `delivery_guards.py` — contrôle du CONTENU livré avant tout commit, en plus des chemins sensibles (`.github/`, `.env*`, Dockerfile…) et de la syntaxe déjà refusés. (1) SECRET : un fichier contenant un secret de format connu (clé privée, jeton GitHub, clé Google, AWS, Slack, `sk-…`) est refusé, quel que soit son type ; ni le motif `mot_de_passe = valeur` ni `Bearer` (trop de faux positifs) ; la valeur n'est jamais écrite dans le message ni le journal. (2) SCRIPT DANGEREUX, seulement dans les fichiers exécutables (`.sh .bash .zsh .ps1 .bat .cmd .mk`, `Makefile`, `justfile`) et les scripts de cycle de vie de `package.json` (`preinstall`, `install`, `postinstall`, `prepare`…), jamais dans un README ni le code applicatif : `curl|wget … | sh`, `base64 … | sh`, PowerShell qui télécharge et exécute, `rm -rf` de `/`, `~` ou `$HOME` (pas `rm -rf /tmp/build`), bombe fourche, `/dev/tcp/`, `nc -e`, `chmod 777 /`, `mkfs`, `dd … of=/dev/…`. Branché sur les quatre chemins d'écriture, avant tout appel réseau : `github_write_file`, `github_edit_file` (sur le seul texte AJOUTÉ, pour ne pas rendre un fichier inéditable à vie), le lot (`github_batch`, refus fichier par fichier, commit partiel comme avant) et l'espace local. DÉCISION : pas d'`output_pydantic` pour l'Analyste, le Développeur et la QA — leurs rapports sont du Markdown à blocs de fichiers, déjà contrôlé par `crew_guardrails` ; du code dans des chaînes JSON est un mode de panne connu et rien ne permet de le valider contre le vrai Gemini d'ici (à faire, si voulu, comme chantier séparé derrière un interrupteur). Limites : un refus est définitif pour le fichier ; les formes déguisées (`c""url | sh`, contenu encodé) ne sont pas détectées. 1288 tests verts (normal, inversé, mélangé), 25 mutations tuées sur 25 (2 survivantes d'abord, dont une redondante : motif `bash -i >&` retiré).
> - 1.88 (10-22) : l'étape `npm audit` du job `audit` (de nouveau exécutée depuis que `pip-audit` passe) a signalé `vite` 8.3.0 à 8.3.2 : 1 faille haute, 6 avis (`launch-editor` : exécution de commande par un éditeur ; lecture de fichiers hors de `server.fs.allow` ; exécution de script d'une autre origine) — tous côté serveur de DÉVELOPPEMENT, pas le site compilé. `npm audit fix` : `vite` 8.3.0 → 8.3.5 et 6 paquets liés (verrou seulement, `package.json` inchangé) ; `npm audit` complet : 0 faille. Vérifié : eslint, tsc, build, 249 tests (normal + 3 passages `--sequence.shuffle`). CI #113 sur `7f1155a` : backend 3.11/3.12 (nouveau verrou Python, pillow 13, aiohttp 3.14.5) et frontend verts, `pip-audit` vert.
> - 1.87 (10-22) : le job `audit` de la CI #111 (cb8aa87) est passé au rouge sur 47 NOUVELLES failles Python publiées entre deux runs (aucune ne vient du code). Corrigées dans `backend/requirements.lock` : `aiohttp` 3.14.3 → 3.14.5, `certifi` → 2026.10.19, `opentelemetry-*` → 1.45.1 (et `semantic-conventions`/`http-transport`/`otlp-common` → 0.66b1), `pillow` 12.3.0 → 13.0.0 (majeur ; seul `pdfplumber` l'exige, `>=12.2.0`). Vérifié dans un environnement neuf : `pip check` propre, ruff, mypy et 1089 tests verts (normal, inversé, mélangé ; 1091 avec les 2 tests de prémisse ajoutés). Deux avis restent, sans correctif accessible car CrewAI 1.15.23 borne ses dépendances : `mcp` (CVE-2026-59951, fuite mémoire d'un SERVEUR MCP en HTTP ; `mcp~=1.28.1`) et `uv` (PYSEC-2026-4321, racine TLS révoquée ; `uv~=0.11.6`). Ils sont ignorés dans `ci.yml` APRÈS lecture des avis, parce que l'application n'importe pas `mcp`, ne sert aucun MCP et ne lance pas `uv` — prémisses vérifiées par `test_audit_premises.py` (4 mutations tuées). Limite : pillow 13 n'est exercé qu'indirectement (lecture de PDF par un outil CrewAI, non testée à fond). À revoir à chaque mise à jour de CrewAI.
> - 1.86 (10-22) : étape 3 (disjoncteur) : durée maximale d'une exécution portée de 20 à 30 min (`EXECUTION_TIMEOUT_S` 1800, le mécanisme existant ; pas de second réglage de durée) et NOUVEAU budget de tokens par exécution (`run_budget.py`, `RUN_MAX_TOKENS`, 1 000 000 par défaut, minimum 10 000). `llm_limiter` refuse l'appel AVANT toute réservation de créneau quand le budget est atteint (`TokenBudgetExceeded`, jamais le modèle appelé) et compte après chaque appel les tokens réels (sinon l'estimation d'entrée ; rien pour un appel en erreur). Classé `TOKEN_BUDGET_EXCEEDED`, non réessayable (donc jamais relancé automatiquement) ; le frontend l'affiche (🪙, conseil). Le message d'erreur ne contient AUCUN nombre : `crew_retry` rejoue par sous-chaîne (« 429 », « quota ») et un compteur de 1 042 900 tokens ferait rejouer l'appel refusé (test dédié). Limite : le budget est vérifié avant chaque appel, donc un seul appel peut le dépasser de sa propre taille ; il vaut par tentative d'exécution (une reprise repart à zéro). Reste à faire de l'étape 3 : sorties structurées (`output_pydantic`) et contrôle du contenu livré (motifs dangereux). 1089 tests backend (normal, inversé, mélangé), 249 frontend, 13 mutations tuées sur 13.
> - 1.85 (10-22) : étape 2 : `telemetry.py` — traces OpenTelemetry exportées en OTLP/HTTP vers LangSmith, INACTIVES par défaut (`LANGSMITH_TRACING=true` ET `LANGSMITH_API_KEY` requis ; sinon aucun module `opentelemetry` n'est importé). Spans : `crew.run` (exécution, dans `execution.run_crew`), `agent.task` (tâche terminée : agent, modèles servis, durée ; `crew_run._on_task_complete`), `llm.call` (modèle, tokens réels, attente et cause du limiteur, classe d'erreur ; les 4 enveloppes de `llm_limiter`), tous marqués `run_id`. Le fournisseur n'est PAS posé comme fournisseur global (CrewAI a sa propre télémétrie). Aucun texte des prompts ni des sorties n'est envoyé, sauf `OTEL_CAPTURE_CONTENT=true` (sortie des agents, secrets masqués, 4000 caractères). Une panne de trace n'atteint jamais l'exécution. Variables : `LANGSMITH_ENDPOINT` (défaut api.smith.langchain.com, chemin `/otel/v1/traces`), `LANGSMITH_PROJECT` (défaut `myiacrew`). NON VÉRIFIÉ ici : l'endpoint et les en-têtes (`x-api-key`, `Langsmith-Project`) viennent de ma connaissance de LangSmith, sans accès à leur documentation ni test réel avec une clé. `backend/.env.example` créé (toutes les variables). 1070 tests verts (normal, inversé, mélangé), 15 mutations tuées sur 16 (la survivante est équivalente : sans clé, l'export échoue de toute façon et les traces restent coupées).
> - 1.84 (10-22) : étape 2 (début) : `run_context.py` — identifiant de corrélation `run-<id d'historique>` (ContextVar, déterministe, copié dans les threads de `asyncio.to_thread`) posé par `@tracked("db_entry_id")` sur `run_crew_and_persist` (échec compris) ; `logs.OneLineFormatter` l'insère dans chaque ligne (`… [run-12] : message`), rien hors exécution. Prépare les spans OpenTelemetry (non faits : le backend OTLP et la politique sur le contenu des prompts restent à décider). Pas d'en-tête HTTP : la réponse d'exécution porte déjà l'id d'historique. 1052 tests verts (normal, inversé, mélangé), 5 mutations tuées. Une fausse manœuvre a écrasé `tests/test_run_context.py` (déjà existant) : restauré, mes tests sont dans `test_run_id_logging.py`.
> - 1.83 (10-22) : étape 1 du plan observabilité/garde-fous : `input_guards.py` (masque les secrets évidents — clés privées, jetons GitHub/Google/AWS/Slack/Bearer, valeurs de `password=`/`api_key=` hors substituts —, repère les injections de prompt évidentes, encadre les fichiers lus par `read_a_files_content` et `github_read_file` de marqueurs « données non fiables »). Appliqué à la demande, à l'historique et à l'aperçu du dépôt (`execution_context.crew_inputs`). Les messages d'erreur des outils restent bruts. Limite connue : un agent qui réécrit un fichier lu peut y recopier `[SECRET MASQUÉ]` (la consigne l'interdit). Tests d'évasion de chemin (`..`, absolu, lien symbolique) ajoutés ; 9 mutations tuées ; 1045 tests verts (normal, inversé, mélangé).
> - 1.82 (10-17, CI verte sur `main` : run #104, backend 3.11/3.12, frontend, audit) : `vitest` 3.2.7 → 5.0.5 (équivalent de la PR Dependabot #59, qui devient sans objet) : `npm audit` complet passe de 3 avis (dont `tinypool`, exécution de code par pollution de prototype, classé critique) à 0 faille. Vérifié : 248 tests verts (normal + 3 passages `--sequence.shuffle`), tsc, eslint, build.
> - 1.81 (10-14) : revue n°10 : `ResultSections.test.tsx` dépendait de l'ordre d'exécution (le `Suspense` de `React.lazy(MarkdownRenderer)` entoure aussi le sommaire : un test synchrone passé en premier ne voyait que le message de chargement) ; un échauffement dans `beforeAll` charge le module une fois. Vérifié en ordre mélangé (`vitest --sequence.shuffle`, 6 passages × 248 tests verts) ; côté backend, ordre inversé et ordres mélangés (1004 tests verts). Correction d'une erreur de compte rendu : `pytest -p no:randomly` ne renversait rien (`pytest-randomly` n'est pas installé).
> - 1.80 (10-12) : le job `audit` ayant retrouvé son étape `npm audit` (elle était sautée tant que `pip-audit` échouait), elle a signalé `source-map-js` 1.2.1 (avis GHSA-68fv-2mgg-jv7q, déni de service, haute gravité, dépendance de `vite`/`postcss`) : `npm audit fix` met le verrou à 1.2.2 (et `brace-expansion` 5.0.9 → 5.0.12, développement seulement) ; `npm audit --omit=dev` (celui de la CI) revient à 0 faille. Restent 3 avis de DÉVELOPPEMENT (chaîne `vitest` → `tinypool`/`@vitest/mocker`, correctif seulement avec `vitest` 5, changement majeur déjà proposé par Dependabot) : hors périmètre du job, qui ne lit que les dépendances de production.
> - 1.79 (10-12) : revue n°9 : (1) le journal d'attente du limiteur nomme la contrainte qui fixe vraiment le départ (la pause après un 429 seulement si elle le fixe, sinon la file des réservations déjà faites ; aucune cause quand rien ne retarde l'appel) ; (2) les 4 avis `chromadb` 1.1.1 du job `audit` ont été examinés : ils visent le SERVEUR Chroma (API REST, RBAC, multi-locataire), que l'application ne lance jamais, et `chromadb` n'est là que comme dépendance de CrewAI dont ni la mémoire ni les connaissances ne sont utilisées ; ils sont donc ignorés explicitement (`--ignore-vuln`, commentaire daté dans `ci.yml`), le job ne devient rouge que pour une NOUVELLE faille, et `backend/tests/test_audit_premises.py` vérifie la prémisse et la liste ignorée. Effet de bord corrigé : un `pip-audit` en échec sautait l'étape `npm audit`, qui ne tournait donc plus.
> - 1.78 (10-12) : revue de la 1.77 : les instructions de migration (`ADD COLUMN IF NOT EXISTS`) n'existent pas en SQLite : sur une base SQLite créée avant l'ajout de `executioncheckpoint.model`, chaque écriture d'un agent terminé échouait en silence (ni section de suivi, ni point de reprise). `database._add_missing_columns` (SQLite seulement, après les migrations) ajoute désormais les colonnes facultatives déclarées par les modèles et absentes d'une table existante, et signale celles qui sont obligatoires ; Postgres garde ses instructions `IF NOT EXISTS`.
> - 1.77 (10-12) : modèle de l'agent : le rapport indique le modèle que l'API déclare avoir SERVI (`model_version` de chaque réponse `google-genai`, noté par `llm_limiter` dans les mesures de l'exécution et attribué à l'agent qui vient de finir), le modèle configuré seulement quand l'API n'en dit pas ; une étape reprise (reprise manuelle ou seconde tentative automatique) garde le modèle d'origine (nouvelle colonne `executioncheckpoint.model`, migration `ADD COLUMN IF NOT EXISTS`, transmise par `ResumeOutputs`) ; seuls les marqueurs d'EN-TÊTE d'une section sont lus (backend `AGENT_HEADER_MARKERS`, frontend `extractAgentMeta`) : un marqueur cité dans le texte d'un agent n'est plus pris pour le sien. `_release` de `crew_run` déplacé dans `crew_cache.release_run`.
> - 1.76 (10-12) : le rapport de chaque agent indique le modèle IA utilisé : la section d'un agent (suivi progressif et résultat final) porte, après sa durée, le marqueur `<!--agent-model:gemini-3.8-flash-->` (format unique `crew_workflow.format_agent_section` ; modèle déduit du rôle par `llm_models.model_for_role`, `GEMINI_MODEL` pris en compte ; pas de marqueur pour un rôle inconnu ni pour le résumé). Le frontend l'affiche dans le bandeau de la section (« 🤖 gemini-3.8-flash »), le retire du texte et l'ajoute en tête du texte copié ; le marqueur est aussi retiré du plan donné à l'Architecte au tour suivant.
> - 1.75 (10-12) : revue du limiteur par modèle : `GEMINI_MODEL_LIMITS` est analysée une seule fois (un texte illisible n'est plus signalé à chaque appel au modèle) et le limiteur déjà créé est retrouvé sans recalculer les plafonds ; le journal d'attente nomme la contrainte en cause (requêtes par minute, tokens par minute ou pause après un 429) ; les flux asynchrones comptent désormais le total réel de tokens ; la fenêtre de débit est extraite dans `llm_rate_window.py`.
> - 1.74 (10-12) : modèles et limites de débit par modèle : `qualification_agent` et `developer_agent` sur `gemini-3.5-flash-lite`, `product_designer_agent`, `architect_agent`, `diagnostic_agent` et `qa_agent` sur `gemini-3.8-flash` (carte `AGENT_MODELS` de `backend/llm_models.py`, qui porte aussi les plafonds RPM/TPM/RPD de chaque modèle : 10/250 000/1 500 pour 3.8-flash, 15/250 000/1 000 pour 3.5-flash-lite). `llm_limiter.py` passe d'un limiteur global à un limiteur PAR MODÈLE (choisi d'après l'argument `model` de chaque appel `google-genai`) : fenêtre glissante de requêtes et de tokens (estimation de l'entrée avant l'appel, remplacée par `usage_metadata` à la réponse), plafond journalier (échec immédiat `DailyQuotaExceeded`, classé QUOTA_EXHAUSTED non réessayable), pause après 429 propre au modèle. Marge de sécurité 0,8 (`LLM_RATE_MARGIN`), plafonds remplaçables (`GEMINI_MODEL_LIMITS`), `GEMINI_MODEL` force un modèle unique.
> - 1.73 (10-08) : Dependabot ignore désormais les versions majeures de `typescript` (le PR n°60, TypeScript 7, échouait : `typescript-eslint` borne son peer à `< 6.1.0`) ; le job `audit` reste rouge à cause de `chromadb 1.1.1` (5 failles sans correctif), non bloquant, décision laissée ouverte.
> - 1.72 (10-05) : revue n°8 : le plafond de sortie de l'architecte (`ARCHITECT_MAX_OUTPUT_TOKENS`, 8192 par défaut) n'était pas appliqué non plus : le fournisseur Gemini natif de CrewAI ne lit que `max_output_tokens` pour sa configuration de génération (`max_output_tokens = None`). Les deux champs sont désormais posés ; `tests/test_crew_llm_limits.py` (ex-`test_crew_llm_timeouts.py`) lit la configuration de génération réelle de chaque LLM. À surveiller : sur les modèles Gemini récents, les jetons de réflexion comptent dans la sortie, un plan très long peut être coupé à 8192 (relever la valeur par la variable d'environnement si besoin).
> - 1.71 (10-05) : revue n°7 : les délais par appel LLM (45 s qualification, 90 s designer/architecte/développeur/QA, 120 s diagnostic, 25 s résumé) n'étaient pas appliqués : le fournisseur Gemini natif de CrewAI range `request_timeout` dans `additional_params` sans le lire (délai HTTP `None`). Ils sont désormais posés sur le client google-genai (`client_params` → `HttpOptions(timeout)`), vérifiés par `test_crew_llm_limits.py` (7 échecs sur l'ancien code) ; l'exception mypy de `crew_llms` est retirée (l'erreur était un vrai signal). Un appel figé s'arrête donc à son délai au lieu d'attendre le délai global d'exécution.
> - 1.70 (10-05) : revue n°6 : la CI backend était rouge depuis l'extension de mypy (34 erreurs de typage CrewAI/SQLModel, pytest sauté) ; `qualification.py` typé par `cast`, exceptions mypy ciblées pour `crew_llms` et `crewquestion` (`mypy.ini`), `# type: ignore[misc]` sur deux `select()` de `routes_metrics.py` ; mypy repasse à zéro erreur en cache vierge. Le résultat de la CI doit désormais être lu après chaque push.
> - 1.69 (10-05) : revue complète n°5 : une réponse Supabase 200 sans objet utilisateur (vide, non JSON, tableau) donne un 401 au lieu d'une 500 et n'est jamais mise en cache ; `raise … from` dans `auth.py` ; les filets best-effort qui échouaient en silence journalisent désormais (avertissement pour une exécution non marquée « failed », debug ailleurs ; les lectures de `/proc` et cgroup de `memory_monitor` restent silencieuses, leur absence étant normale).
> - 1.68 (10-05) : revue du sondage incrémental : le frontend lit `completed_count` et ramène ses agents connus au total du serveur quand celui-ci en compte moins (résultat réécrit), pour que le `known` suivant reste cohérent.
> - 1.67 (10-05) : revue complète n°4 : le sondage de progression (toutes les 3 s) ne retransmet plus le texte des agents déjà affichés (`GET …/progress?known=<n>` : seuls les agents suivants, plus `completed_count` ; sans `known`, comportement inchangé ; la colonne `result` reste lue côté base) ; les 8 mutants survivants (sur 97) tués par de nouveaux tests (bornes du quota dominant, limites d'exécution, purge du limiteur, résumé de projet, fuseaux des orphelines, tableau QA, nettoyage des callbacks après succès).
> - 1.66 (10-05) : revue complète n°3 : lecteur de délimiteurs JS/TS refait en lecteur par états (`delimiter_scan.py`, complexité 35 → sous 12, comparé à l'ancien sur 24 000 variantes de fichiers : 0 différence) ; enveloppe morte supprimée ; `verify_github_delivery` (179 lignes) découpé en phases (`_resolve_branch_head`, `_lookup_pull_request`, `_missing_pull_request_issue`) avec 18 tests de caractérisation (9 mutations sur 12 survivaient avant ; les tests passent aussi sur l'ancienne version).
---

## 1. Synthèse & Vision

- **Concept** : un studio web qui qualifie une demande de développement en langage naturel, puis délègue son exécution à une
  équipe de 6 agents CrewAI (qualification, game/product designer, architecte, analyste diagnostic, développeur, QA) capable de
  lire du code sur un repository GitHub cible, de produire le code complet, de le committer sur une branche de travail et d'ouvrir
  une Pull Request.
- **Genre** : outil interne d'assistance au développement (multi-agent orchestration tool), pas une application grand public.
- **Plateforme cible** : Web — frontend Vite + React 19 + TypeScript (déployé sur Vercel), backend FastAPI + CrewAI (déployé sur
  Render), base de données PostgreSQL (Supabase), authentification Supabase Auth.
- **Public visé** : l'équipe/le·s propriétaire·s du studio, authentifié·s (comptes créés manuellement dans le dashboard Supabase —
  pas d'auto-inscription).
- **Boucle principale** : décrire un besoin dans un fil de conversation → qualification automatique (ou choix manuel) du type de
  demande → exécution par les agents en tâche de fond avec progression en direct → résultat par agent (texte + éventuellement une
  Pull Request sur le repo cible) → historisation en base.
- **Principe de qualité** : la fiabilité ne repose pas sur la confiance dans le texte des LLM. Les faits (fichiers committés, URL
  de PR, verdict minimal) sont constatés par des outils et des contrôles Python, sans appel LLM supplémentaire (voir 2.7).

---

## 2. Spécification fonctionnelle

### 2.1 Boucle principale (Core Loop)

1. **Connexion** : l'utilisateur se connecte avec email + mot de passe (Supabase Auth). Sans session valide, seul l'écran de
   connexion est accessible.
2. **Conversation** : chaque échange appartient à un fil (`Conversation`). Les tours précédents sont résumés et fournis aux agents
   (10 derniers tours au plus) pour comprendre un message de suivi (« corrige ça », « ajoute aussi Y »).
3. **Qualification** (`POST /api/qualify`, mode « Détection automatique ») : `qualification_agent` (sans outils) classe la demande
   en une des 4 catégories avec une confiance, une taille (`scope` : `PETIT` seulement pour une FEATURE locale de 1 ou 2 fichiers
   sans nouvel écran ni nouvelle dépendance, sinon `GRAND`), une alternative éventuelle et jusqu'à 4 questions de clarification.
   Sous 0.6 de confiance, la demande est jugée floue et l'utilisateur est questionné. L'utilisateur peut aussi choisir le workflow
   à la main et court-circuiter cette étape.
4. **Repository cible** (optionnel) : owner / repo / branche de base GitHub, renseignés dans le formulaire « Repository cible ».
   Les combinaisons déjà utilisées sont proposées (`GET /api/repo-targets`).
5. **Exécution** (`POST /api/execute`) : la réponse est immédiate (`status: running`) et le crew s'exécute en tâche de fond,
   séquentiellement (voir 2.2). Si un repo est fourni, une branche de travail `crewai/<workflow>-<hex8>` est créée au premier tour
   puis **réutilisée aux tours suivants** de la conversation (même branche, même PR). Une seule exécution à la fois par
   conversation (`409` sinon).
6. **Progression** : le frontend sonde `GET /api/conversations/{id}/progress` ; chaque agent terminé apparaît au fur et à mesure
   avec sa durée d'exécution et le modèle IA qui l'a produit. Le sondage envoie `known` (nombre d'agents déjà reçus) et ne reçoit que les suivants.
7. **Restitution** : le résultat combiné par agent est stocké dans `executionhistory` ; après le crew, le backend vérifie via
   l'API GitHub qu'une branche et une PR à jour existent réellement.

### 2.2 Workflows disponibles

| Type de demande | Tâches exécutées (séquentiel) |
|---|---|
| `ANALYSE_ONLY` | `design_task` → `architecture_task` |
| `BUGFIX` | `diagnostic_task` → `development_task` → `qa_task` |
| `FEATURE` | `architecture_task` → `diagnostic_task` → `development_task` → `qa_task` |
| `DESIGN_AND_DEV` (défaut) | `design_task` → `architecture_task` → `diagnostic_task` → `development_task` → `qa_task` |

**Variante PETIT** : pour une `FEATURE` qualifiée `scope = PETIT` (changement local), `architecture_task` est sautée (`FEATURE` →
`diagnostic_task` → `development_task` → `qa_task`). Les étapes réellement jouées viennent de `workflow_step_keys(request_type,
scope)` (`crew_workflow.py`), reprise par le front via `workflowSteps(workflow, scope)`. Une reprise traite `scope` absent comme
`GRAND`.

Contexte explicite par tâche (pas toutes les sorties précédentes) : `architecture` reçoit `design` ; `diagnostic` reçoit `design`
+ `architecture` ; `development` reçoit `diagnostic` ; `qa` reçoit `design` + `diagnostic` + `development`.

### 2.3 Agents CrewAI

| Agent | Rôle | Outils | Itérations max | Timeout LLM |
|---|---|---|---|---|
| `qualification_agent` | Qualifie le type de demande | Aucun (interdiction explicite de lire des fichiers) | 2 | 45 s |
| `product_designer_agent` | Specs fonctionnelles (jeu ou application) | lecture disque local + lecture GitHub | 3 | 90 s |
| `architect_agent` | Architecture technique React/TypeScript | lecture disque local + lecture GitHub | 5 | 90 s |
| `diagnostic_agent` | Diagnostic de la cause et rédaction du code source complet | lecture seule (disque local + GitHub) | 7 | 120 s |
| `developer_agent` | Commit fidèle du code de l'Analyste et ouverture de la PR | écriture seule : commit des fichiers de l'Analyste, branche, `check_syntax`, PR | 6 | 90 s |
| `qa_agent` | Vérification outillée et verdict | `qa_verify_delivered_files`, lecture, `check_syntax` | 10 | 90 s |

Le plafond de sortie de l'Architecte est de `ARCHITECT_MAX_OUTPUT_TOKENS` (8192 par défaut) ; ses garde-fous
(`crew_guardrails.py`) sont `_architecture_issues` (structure attendue) et `_truncation_issues` (sortie tronquée). L'Architecte et
le Diagnostic reçoivent un résumé déterministe du projet (`project_summary.py`) et l'Architecte le plan du tour précédent.

**Résumé final** (`## Résumé`, après `<!--crew-summary-->`) : une ligne « Faits » calculée en Python (`backend/summary.py` : type
de demande, étapes terminées, fichiers produits par l'Analyste, verdict QA — jamais écrits par le modèle), puis la synthèse du
modèle en 3 blocs (« Ce qui a été fait », « Pourquoi ces choix », « À faire ensuite »). Si la génération échoue (quota, délai), un
résumé de repli sans modèle (première ligne utile de chaque agent) la remplace, signalé comme automatique.

**Prochaines étapes cliquables** : les puces du bloc « À faire ensuite » deviennent des boutons sous le résumé
(`utils/nextSteps.ts`, 3 au plus) ; un clic préremplit la zone de saisie (même mécanisme que « Relancer »), sans envoi automatique
; masqués pendant une exécution, une clarification ou un aperçu en attente.

**Échec avec travail déjà fait** : `execution_outcomes.persist_failure` ajoute, après le bloc GitHub, un bloc « Déjà réalisé avant
l'échec » construit sans modèle depuis les sections des agents terminés (`summary.partial_work_block`) ; le frontend l'affiche en
carte (`splitPartialWork`, `FailureBlock`).

**Planification du diagnostic** : `diagnostic_agent` planifie avant d'agir (`PlanningConfig`, effort « low » par défaut,
`DIAGNOSTIC_REASONING_EFFORT` = low | medium | high) : un seul appel de plan, l'observation de chaque étape se fait par
heuristique sans appel au modèle, le plan est borné à 5 étapes et chaque étape à 8 itérations (`DIAGNOSTIC_STEP_MAX_ITERATIONS`,
15 par défaut dans CrewAI). L'ancien réglage (`reasoning=True`, effort « medium ») ajoutait un appel d'observation par étape, un
replan complet sur échec (jusqu'à 3) et jusqu'à 15 itérations par étape, et — d'après le code de CrewAI — ces appels ne passaient
pas par `max_rpm`. Contrepartie : en effort « low », une étape en échec n'est plus replanifiée (l'heuristique la marque terminée)
; les contrôles de qualité et la seconde tentative automatique restent le filet, et `DIAGNOSTIC_REASONING_EFFORT=medium` rétablit
l'observation par le modèle.

Séparation voulue : l'Analyste ne peut pas écrire sur GitHub, le Développeur ne peut pas lire ni raisonner — il committe tel quel.
Chaque agent rapporte son temps d'exécution (`execution_duration`), affiché dans l'UI.

### 2.4 Intégration GitHub (repo cible dynamique)

- L'utilisateur choisit le repository (owner/repo) et la branche de base à chaque exécution — rien n'est câblé en dur.
- Les écritures sont limitées aux branches `crewai/…` (et, pendant une exécution, à sa seule branche de travail via
  `track_write_scope`) ; tout autre nom (`main`, `test`…) est refusé sans appel réseau et tracé dans les logs. Branche de travail
  obligatoire, puis Pull Request.
- Le commit passe par l'outil `github_commit_analyst_files` : les fichiers de l'Analyste sont extraits **en Python** de sa réponse
  (balises), sans recopie par un LLM. Les fichiers Python/JSON/YAML invalides sont refusés avant commit.
- La description de la PR est **générée à partir des faits** (voir 2.7) ; elle est ouverte en brouillon si la livraison est
  partielle (repli sur une PR normale si le dépôt n'accepte pas les brouillons) et, si une PR existe déjà pour la branche, seule
  sa zone générée est mise à jour.
- Sans repository cible, l'écriture se fait dans un espace de travail local isolé par conversation (`LOCAL_WORKSPACE_DIR`), jamais
  sur le code du serveur.
- Nécessite la variable d'environnement `GITHUB_TOKEN` (PAT avec accès contenu + pull requests) côté backend.

### 2.5 Authentification

- Supabase Auth (email + mot de passe). Pas d'auto-inscription côté frontend : les comptes se créent depuis le dashboard Supabase.
- Le frontend envoie le token de session Supabase en `Authorization: Bearer` sur chaque appel API.
- Le backend valide ce token à chaque requête en interrogeant `SUPABASE_URL/auth/v1/user` (`backend/auth.py`) ; sans token valide,
  réponse `401`. Les conversations et l'historique sont filtrés par `user_id`.

### 2.6 Résilience LLM

- **LLM utilisé** : Gemini, un modèle par agent (carte `AGENT_MODELS` de `backend/llm_models.py`) : `gemini-3.5-flash-lite` pour la
  qualification et le développeur (et le résumé final), `gemini-3.8-flash` pour le designer, l'architecte, le diagnostic et la QA ;
  `GEMINI_MODEL` force un modèle unique pour tous (repli si un modèle n'est pas disponible sur la clé). Une température et un
  timeout par agent (2.3).
- **Quota** : le free tier impose des plafonds PAR MODÈLE (voir « Limiteur Gemini par modèle » plus bas ; environ 10 à 15 requêtes/minute). Une pause minimale de **5 s** entre appels
  (`QuotaManager.min_interval_seconds`) et `max_rpm=13` sur le crew limitent le débit ; le retry absorbe les 429 transitoires.
- **Retry** : une erreur 429/quota ou 503/surcharge est retentie avec attente `retry after N + 2 s` si Gemini l'indique, sinon
  `base_delay × 2^(essai-1)`. Qualification : 5 essais, base 12 s. Crew : 5 essais, base 15 s.
- **Reprise résumable** : sur erreur de quota après que certaines tâches ont terminé, **seules les tâches restantes** sont
  rejouées ; les sorties déjà obtenues continuent d'alimenter les suivantes. Le budget de relance du guardrail de diagnostic est
  réinitialisé quand `diagnostic_task` est rejouée.
- **Pas d'appel LLM de plus pour contrôler** : les guardrails de 2.7 sont déterministes ; seul le diagnostic peut être relancé une fois.

### 2.7 Contrôles automatiques de qualité

Tous les contrôles sont des fonctions Python pures et testées. Ils **signalent** (note ajoutée à la sortie) sauf le diagnostic,
qui refuse une fois.

| Agent | Consignes imposées | Contrôle automatique |
|---|---|---|
| Qualification | 6 exemples de cas limites, confiance obligatoire, questions fermées ; question sur le repository pour un BUGFIX/FEATURE sans repo (« renseigne le formulaire Repository cible, ou réponds *local* ») | `confidence < 0.6` force `is_clear = false` et une question ; repli explicite (`fallback`) si la sortie est inexploitable |
| Designer | Identifiants stables `F1 [Must]`, critères `AC-F1-1` (Étant donné / Quand / Alors), MVP borné (5 Must, 3 critères chacun), exigences non fonctionnelles, composants avec états, gabarit jeu ou application, section « Contrôle » | Note « Contrôle automatique des specs » : section manquante, Must sans critère, critère vague sans seuil mesurable |
| Architecte | Aperçu du repository (racine, **résumé déterministe** de `package.json`/`tsconfig.json` — dépendances, conventions, mode strict, sans appel LLM — et `src` ; listings plafonnés à 150 lignes ; non lu quand une reprise réutilise l'architecture) lu en Python avant l'agent et donné dans son prompt (sinon 4 lectures par outils), consigne resserrée (~400 mots), sortie plafonnée (8 192 tokens par défaut, `ARCHITECT_MAX_OUTPUT_TOKENS`) avec détection d'une sortie tronquée, **plan du tour précédent** de la conversation donné comme base (il ne décrit que ce qui change), conventions reprises, « Dépendances à ajouter », contrats d'interface par fichier, tableau de couverture, risques | Note « Contrôle automatique de l'architecture » : sections, lignes `CRÉER\|MODIFIER chemin : rôle`, chemins dupliqués ou hors projet, fichier de code sans contrat |
| Diagnostic | Aperçu du repository déjà lu par le système (sans relire ces fichiers), hypothèses, traçabilité avec « ce qui infirmerait la cause » et « vérification manuelle », correctif minimal, 5 lectures au plus ; fichiers entre `<<<FICHIER>>>`, ou **modifications ciblées** `<<<MODIFICATION: chemin>>>` (blocs CHERCHER / REMPLACER) résolues en Python contre le fichier d'origine | Refus unique si : fichier tronqué ou sans balise de fin, commentaire de raccourci (`// ... reste du code`), bloc inapplicable (texte absent ou présent plusieurs fois), **import incohérent** (export manquant, import relatif sans cible) ; à la 2e tentative, les fichiers fautifs sont exclus du commit et signalés |
| Développeur | Message de commit conventionnel (`type: résumé`, 72 car.), titre de PR conventionnel, recopie exacte des chemins et URL | Bloc « Livraison constatée par les outils » ajouté au rapport (fichiers réellement committés, non livrés, URL de PR renvoyée) ; toute URL de PR citée sans venir d'un outil est signalée ; description de PR neutralisant `@mentions` et mots-clés de fermeture (`fixes #n`) |
| QA | Tableau `Critère \| Statut \| Preuve \| Correctif suggéré` ; KO avec `fichier:ligne` et correctif ; NON VÉRIFIABLE avec raison et test manuel | **Verdict minimal imposé** : `NO_GO` si un critère est KO, un problème bloquant est listé ou aucun fichier n'est committé ; `GO_AVEC_RESERVES` si des fichiers sont non livrés ou un critère NON VÉRIFIABLE. Un verdict plus indulgent est réécrit en place avec une note ; un plus sévère est conservé. Rapport de l'outil enrichi du périmètre (fichiers hors plan de l'architecte ou manquants) et de la cohérence des imports |

**Limites assumées** : la validation TypeScript/JavaScript est heuristique (délimiteurs équilibrés, imports, exports) — il n'y a
ni `tsc` ni build. Seule la signature d'une coupure en fin de fichier (accolade ou `${` jamais refermée, template ou commentaire non
terminé) bloque un commit : mesurée à 0 faux positif sur `frontend/src` et 1 500 fichiers de bibliothèques, elle attrape environ 89 %
des fichiers coupés au hasard ; les autres indices (parenthèse ou crochet isolé) restent des conseils. Limite connue : un guillemet
simple précédé d'une espace dans un texte JSX (`Voir 'Historique`, impair sur la ligne) peut avaler des accolades et faire
déclarer un fichier valide « tronqué » ; non observé sur les corpus mesurés ; la vérification de PR côté QA reste celle de l'outil et du backend, pas du LLM.

### 2.8 Mesure de performance par agent

- **Collecte** : `backend/metrics_collect.py` écoute les événements CrewAI (appel LLM terminé ou échoué, outil utilisé) et
  attribue chaque mesure à son agent par son rôle (`agentsquestion.yaml`). Les mesures sont isolées par exécution (contexte copié
  par le bus d'événements), donc deux exécutions concurrentes ne se mélangent pas. Les appels hors agent (synthèse finale) sont
  comptés à part.
- **Ce qui est mesuré, par agent et par exécution** : durée de la tâche, appels LLM réels, erreurs LLM, tokens (entrée, sortie,
  total), appels d'outils et erreurs d'outils. Un usage de tokens absent ou tout à zéro est **inconnu**, jamais « 0 token » : il
  n'entre pas dans les moyennes.
- **Changement de sens** : `api_calls_count` (par exécution) compte désormais les appels LLM réels. Il ne comptait avant que les
  tentatives de `kickoff`, ce qui sous-estimait fortement la consommation du quota.
- **Persistance** : une ligne `agentrun` par agent mesuré, écrite en fin d'exécution (succès ou échec). Un agent qui a fait des
  appels sans terminer sa tâche est marqué `incomplete`. L'écriture est « au mieux » : elle n'échoue jamais l'exécution.
- **Tableau de bord** (bouton « 📊 Performance » du Studio, chargé à la demande) : filtres période (7, 30, 90 jours) et workflow ;
  tuiles (exécutions, taux de succès, durée médiane, appels LLM, tokens, pauses quota) ; durée par agent (médiane en barre, p95 en
  point, un seul axe) ; tokens par agent (entrée/sortie empilées) ; échecs par cause (nombre d'exécutions en échec par
  `error_code`, du plus fréquent au moins fréquent ; les échecs d'avant le suivi des causes sont regroupés sous « Cause non
  enregistrée ») ; tendances quotidiennes (une métrique à la fois : appels LLM, durée médiane, taux de succès, tokens ; un jour
  sans donnée n'a pas de colonne) ; tableau de détail. Chaque tuile affiche son **écart par rapport à la période précédente de
  même durée** (flèche, signe et couleur : vert = mieux, rouge = moins bien selon la métrique ; le taux de succès en points, le
  reste en pourcentage ; aucun écart depuis une valeur nulle ou sans période comparable ; une phrase dit pourquoi quand la limite
  l'empêche). Un jour dont la valeur est réellement zéro garde un liseré, un jour sans donnée n'a aucune colonne. Chaque graphique
  a un jumeau tableau, une infobulle (souris et clavier) et des couleurs validées en clair et en sombre.
- **Détail d'une exécution** : sous chaque message terminé, « Voir la performance par agent » affiche durée, appels LLM, tokens et
  outils de cette exécution.

### 2.9 Gestion des erreurs

- **Format unique** : toute erreur de l'API est `{"detail": message, "code": CODE, "retryable": bool}` (`detail` reste la clé lue
  par le frontend). Les gestionnaires de `error_handlers.py` couvrent `AppError` (erreur volontaire d'un endpoint),
  `HTTPException` (y compris les 404/405 de routes et `auth.py`), les erreurs de validation (422, `detail` = phrase lisible, liste
  détaillée dans `errors`) et toute exception non prévue (500 générique, détail dans les logs seulement).
- **Codes** (`backend/errors.py`) : `QUOTA_EXHAUSTED`, `LLM_UNAVAILABLE`, `LLM_TIMEOUT`, `GITHUB_UNAVAILABLE`, `GUARDRAIL_FAILED`,
  `DELIVERY_FAILED` (livraison GitHub non confirmée après l'exécution : ni branche à jour ni PR ; non relançable tel quel),
  `EXECUTION_TIMEOUT`, `INTERRUPTED`, `INTERNAL_ERROR`, `VALIDATION_ERROR`, `UNAUTHORIZED`, `FORBIDDEN`, `NOT_FOUND`, `CONFLICT`,
  `RATE_LIMITED`, `SERVICE_UNAVAILABLE`. `classify_exception` range un échec du crew dans un code ; les mots-clés de
  quota/indisponibilité sont partagés avec les retries de `crew_retry.py`.
- **Écriture GitHub confinée** : les outils d'écriture et de création de branche (`github_write_file`, `github_write_files`,
  `github_edit_file`, `github_create_branch`, commit de l'Analyste) refusent, sans appel réseau, toute branche qui ne commence pas
  par `crewai/` (donc `main`, `test`, une branche de production…) ; pendant une exécution, seule sa propre branche de travail est
  écrivable. Une consigne glissée dans le dépôt cible ne peut donc pas faire écrire l'agent ailleurs.
- **Journalisation** : tout passe par `logging` (`backend/logs.py`, logger parent `myiacrew`, sortie standard vidée à chaque
  ligne, niveau `LOG_LEVEL`, INFO par défaut) ; les bibliothèques tierces ne sont pas touchées. Les messages écrits par
  l'application ne contiennent ni identifiant d'utilisateur ni texte de demande ni contenu de fichier (le texte d'une exception,
  lui, est repris tel quel : il peut contenir ce que renvoie GitHub ou le modèle). Un événement tient sur une ligne : les retours
  à la ligne d'un message sont écrits en clair (`\n`), pour qu'un texte multi-lignes ne fabrique pas de fausses lignes de log ; la
  trace d'une exception garde ses propres lignes (`exc_info`).
- **Branches de travail validées** : un nom d'écriture doit correspondre à `crewai/` + caractères sûrs (lettres, chiffres, `. _ -
  /`), sans espace, retour à la ligne, `..`, `//` ni `/` final.
- **Lecture ciblée à l'envoi** : `/api/execute` lit les tours en cours, les 10 derniers tours (pour le rappel de conversation) et
  la branche de travail précédente par trois requêtes ciblées, au lieu de toute la conversation avec le résultat de chaque tour.
- **Limiteur Gemini par modèle** (`backend/llm_limiter.py`, plafonds dans `backend/llm_models.py`) : le quota de l'offre gratuite est
  propre à chaque modèle (`gemini-3.8-flash` : 10 requêtes/min, 250 000 tokens/min, 1 500 requêtes/jour ; `gemini-3.5-flash-lite` :
  15, 250 000 et 1 000) et partagé par tout le projet, alors que `max_rpm` ne vaut que pour un crew et que le
  planificateur/observateur de CrewAI (agents avec `reasoning=True`) fait des appels hors de ce compte. Le limiteur enveloppe les
  méthodes de requête du client `google-genai` (sync, flux, async) et choisit le limiteur d'après l'argument `model` de l'appel.
  Les plafonds sont appliqués avec une marge (`LLM_RATE_MARGIN`, 0,8 : 8 requêtes/min sur 10, 12 sur 15, 200 000 tokens/min,
  1 200 et 800 requêtes/jour) :
  - **RPM** : fenêtre glissante, chaque appelant réserve son créneau et attend son tour ;
  - **TPM** : les tokens comptent sur la même fenêtre : estimation de l'entrée avant l'envoi (4 caractères par token, `llm_usage.py`),
    remplacée par le total réel (`usage_metadata`, sortie et réflexion comprises) dès la réponse ; la sortie n'étant pas connue
    d'avance, c'est l'appel SUIVANT qui attend si elle a creusé le budget (flux compris : le total du dernier morceau remplace l'estimation) ;
  - **RPD** : plafond journalier (remise à zéro à minuit, heure du Pacifique) : aucune attente n'a de sens, l'appel échoue aussitôt
    avec `DailyQuotaExceeded`, classée QUOTA_EXHAUSTED non réessayable (les reprises ne la rejouent pas) ; le compteur est par process
    et repart de zéro au redémarrage ;
  - **429** : le délai demandé par Google (« retry in Xs », `retryDelay`) devient une pause commune aux seuls appels de CE modèle
    (plafonnée à 120 s : au-delà, aucune pause n'est posée et l'erreur remonte aussitôt comme « Quota épuisé »). Un 429 est
    reconnu par son code HTTP, sinon par un texte précis (`llm_quota_errors.py`), jamais par un nombre cité dans un autre message.
  Un modèle absent de `llm_models.py` tombe sur le limiteur par défaut (`GEMINI_MAX_REQUESTS_PER_MINUTE`, 12, requêtes seules).
  `GEMINI_MODEL_LIMITS` (JSON) remplace ou ajoute des plafonds (offre payante). Les attentes de plus de 2 s sont tracées et
  comptées dans le temps d'attente de l'exécution. Plafonds par process : plusieurs instances du backend les multiplient.
- **Cache de validation des jetons** (`backend/auth.py`) : chaque appel de l'API validait le jeton auprès de Supabase, sondage de
  progression toutes les 3 s compris (latence, limites de débit de Supabase, une panne d'authentification bloquait tout). Une
  validation RÉUSSIE est gardée `AUTH_CACHE_TTL_S` secondes (60 par défaut, 0 la désactive), bornée par l'expiration lue dans le
  jeton ; jamais un 401 ni une panne ; clé = hachage SHA-256 (jamais le jeton), 1000 entrées au plus, utilisateur rendu en copie ;
  une seule validation à la fois par jeton : les requêtes simultanées du même jeton partagent l'issue de la première — succès,
  mais aussi 401 ou 503, pour qu'une panne de Supabase coûte un seul délai à toute la rafale — sans rien garder ensuite hors
  succès ; si la requête qui valide est annulée (client déconnecté), les autres reprennent la main. Contrepartie : un jeton
  révoqué (déconnexion depuis un autre appareil) reste accepté jusqu'à ce délai.
- **Fichiers sensibles protégés** : les outils d'écriture (`github_write_file`, `github_write_files`, `github_edit_file`, commit
  de l'Analyste) refusent, sans appel réseau, tout fichier de CI (`.github/`, `.circleci/`, `.gitlab-ci.yml`…), d'environnement
  (`.env*` sauf les modèles `.env.example`/`.sample`/`.template`, `.npmrc`) ou de déploiement (`Dockerfile*`, `docker-compose.*`,
  `vercel.json`, `render.yaml`, `.pre-commit-config.yaml`, `.gitmodules`…) et tout chemin contenant `..` : un workflow committé
  sur la branche de travail pourrait s'exécuter avec les secrets du dépôt avant toute relecture. Le refus est tracé (chemin
  seulement) et le fichier est signalé comme non livré.
- **Une exécution par conversation, garantie par la base** : un index unique partiel (`status = 'running'`) sur
  `executionhistory.conversation_id` ferme la course du contrôle applicatif (double clic, deux onglets) ; l'insert perdant répond
  409 comme le contrôle. Créé aussi sur une base existante par la migration (journalisée si des doublons empêchent sa création).
- **Une exécution de crew à la fois** : le sémaphore global est à 1 par défaut (`MAX_CONCURRENT_EXECUTIONS`, par process) — deux
  crews en parallèle saturent le quota Gemini de l'offre gratuite (15 requêtes par minute) même étalés par le limiteur. Les
  exécutions suivantes, de tous les utilisateurs, attendent leur tour avec l'étape « queued » ; leur durée totale s'allonge
  d'autant. À relever avec une offre Gemini payante. Le sondage de progression renvoie `queue_ahead` (exécutions qui tournent ou
  en attente créées avant : un simple décompte, tous comptes confondus) et le Studio affiche « N exécutions passent avant elle ».
  Le temps affiché « en cours depuis » ne compte pas l'attente en file observée par ce navigateur (un tour retrouvé au
  rechargement, sans cette observation, affiche le temps total depuis sa création).
- **Durée maximale d'une exécution** : le crew est interrompu au bout de `EXECUTION_TIMEOUT_S` (1800 s, soit 30 min, par défaut ; hors attente
  en file et hors vérification de livraison). Sans cela, avec un seul créneau, une exécution bloquée (son battement de cœur
  continue, le balayage des orphelines ne la libère pas) retiendrait le service pour tous. L'exécution passe en échec
  `EXECUTION_TIMEOUT` (réessayable, jamais relancée automatiquement) et le créneau est rendu ; « Relancer » reprend les étapes
  déjà réussies. Le crew tourne dans un thread que Python ne peut pas tuer : après l'arrêt, l'exécution est marquée « abandonnée »
  (`execution_state.abandoned_execution_ids`) — ses callbacks de persistance n'écrivent plus rien (ni sections d'agents, ni points
  de reprise, ni étape courante, le message d'échec reste intact) — et un signal d'arrêt partagé avec `track_write_scope` fait
  refuser toute écriture GitHub, création de branche et ouverture de Pull Request (« exécution arrêtée »). Le thread peut encore
  lire GitHub et appeler le modèle (donc consommer du quota) jusqu'à sa fin. Quand l'attente du quota (pauses du limiteur,
  backoff) a pris au moins 25 % du délai, le message dit que le service était saturé (et non que la demande est trop grosse) ; le
  détail technique donne la part d'attente. Le délai vaut par tentative : une exécution relancée automatiquement après un échec
  transitoire peut durer 30 min + 90 s + 30 min. Un timeout levé par le crew lui-même (délai d'un appel) reste classé
  `LLM_TIMEOUT`.
- **Plafonds par utilisateur** (`backend/limits.py`, 429 `RATE_LIMITED`, réessayable) : `MAX_USER_RUNNING_EXECUTIONS` (1 : le
  sémaphore global autorise 2 exécutions, un compte ne peut pas les occuper toutes), `MAX_USER_EXECUTIONS_PER_HOUR` (30), comptés
  en base — le plafond horaire lit le journal `executionlaunch`, que la suppression de l'historique ne vide pas (purgé au-delà de
  2 h) ; le conflit de la conversation courante (409) est testé d'abord, puis les plafonds, avant toute création de conversation ;
  un lancement est journalisé dès que sa ligne d'historique est acceptée, même si le démarrage échoue ensuite ;
  `MAX_USER_QUALIFY_PER_MINUTE` (20), fenêtre glissante en mémoire par process.
- **Boucle d'événements libre** : les points d'accès qui n'attendent ni GitHub ni le LLM (historique, conversations, progression,
  tableau de bord, suppression) sont des fonctions synchrones, exécutées par FastAPI dans son pool de threads ; `/api/execute`, lui,
  reste asynchrone (il attend GitHub) mais exécute tout son accès base (`_load_conversation`, `_register_execution`) par
  `asyncio.to_thread` ; un calcul de
  tableau de bord sur 20 000 exécutions ne gèle plus le sondage de progression ni l'authentification. Le moteur SQL vérifie chaque
  connexion avant usage (`pool_pre_ping`) et les recycle après 30 minutes. Sa réserve de connexions est réglable (`DB_POOL_SIZE`
  10, `DB_MAX_OVERFLOW` 10, `DB_POOL_TIMEOUT` 30 s par défaut, hors SQLite). Le balayage des exécutions orphelines libère chaque
  ligne par un `UPDATE` conditionnel (`status = 'running'`) : deux requêtes simultanées ne l'interrompent qu'une fois. Chaque
  écriture GitHub refusée est tracée (« AVERTISSEMENT SÉCURITÉ », branche demandée et autorisée, jamais de contenu).
- **Aucune fuite** : `/api/qualify` ne renvoie plus `str(e)` mais un message lisible lié au code (429/503/504/500).
- **Échecs d'exécution** : `executionhistory` stocke `error_code` et `error_retryable` ; l'étape en échec reste dans `result` («
  Échec à l'étape N/M »). `result` contient un message lisible pour une cause reconnue, suivi du texte technique d'origine tronqué
  (« détail : … »). La classification remonte la chaîne `__cause__` (un timeout ou une panne GitHub dans une étape est reconnu) et
  compare des mots entiers (« 429 » ne reconnaît pas `page_4290.tsx`).
- **Interface** : `ApiError` porte `status`, `code`, `retryable` ; un serveur injoignable donne `NETWORK_ERROR` (« Impossible de
  joindre le serveur »). `ErrorBanner` affiche l'erreur avec « Réessayer » quand elle est transitoire (rechargement de
  l'historique). Sous un échec d'exécution, un conseil adapté au code est affiché (réessayer plus tard, ou reformuler la demande).

- **Exécutions orphelines** (`backend/orphans.py`) : une exécution restée `running` après un crash ou un redéploiement est marquée
  `failed` / `INTERRUPTED` (retryable) quand elle n'a plus donné signe de vie depuis 10 minutes (un battement écrit `updated_at`
  toutes les 60 s tant que l'exécution tourne, y compris pendant une longue étape ou une pause de quota, et à chaque changement
  d'étape) et n'est pas en cours dans le process courant (`execution_state.active_execution_ids`). Balayage au démarrage, au
  sondage de progression, avant le contrôle de concurrence de `/api/execute` (plus de 409 éternel) et avant toute suppression (une
  ligne orpheline devient supprimable). Le texte déjà produit est conservé. Limite : après un crash, la conversation est libérée
  au bout de 10 minutes, pas immédiatement ; avec plusieurs instances du backend (ou un déploiement en continu), le battement et
  le délai de 10 minutes empêchent de voir comme morte une exécution vivante d'une autre instance. La suppression ne balaie que
  les lignes visées.
- **Écritures GitHub partielles** : quand une exécution échoue sur un repository cible, le message d'échec est complété par un
  bloc « Travail déjà présent sur GitHub » constaté via l'API (`describe_partial_delivery`, lecture seule, 20 s maximum) : branche
  et commits d'avance (dont nouveaux commits de cette tentative), Pull Request ouverte ou fusionnée (ou « non vérifiée » si la
  recherche a échoué), ou « rien n'a été poussé ». Si GitHub est injoignable, le bloc le dit au lieu d'affirmer qu'il n'y a rien.
  Il rappelle que « Relancer » reprend sur la même branche et comment abandonner (fermer la PR, supprimer la branche).

- **Validation des entrées** (`backend/validation.py`) : `/api/execute` et `/api/qualify` refusent tout de suite (422, un message
  par champ dans `errors`) une demande vide ou de plus de 20 000 caractères, un workflow inconnu, un propriétaire (« _ » accepté :
  comptes Enterprise Managed User), un nom de repository ou un nom de branche invalide (règles GitHub et `git check-ref-format`, y
  compris par composant du chemin) ; la liste des workflows est dérivée de la qualification. Un champ repository vide est traité
  comme absent.
- **Contrôle préalable GitHub** (`check_github_access`, lecture seule) : pour un workflow qui écrit sur un repository cible (pas
  `ANALYSE_ONLY`), `/api/execute` vérifie avant toute ligne en base et tout appel LLM que le jeton est présent, que le repository
  est lisible, que le jeton a le droit d'écriture et que la branche de base existe. Contrôle exécuté après la vérification de la
  conversation et borné à 15 s. Refus : 404 `NOT_FOUND`, 403 `FORBIDDEN`, 503 réessayable pour la limite de débit GitHub, 502
  `GITHUB_UNAVAILABLE` (jeton invalide, non réessayable), 503 `GITHUB_UNAVAILABLE` (panne, réessayable), 500 (jeton absent), avec
  un message qui dit quoi corriger. Il ajoute un aller-retour GitHub au lancement.
- **Perte de connexion visible** : après 3 pannes consécutives (réseau coupé, 5xx, délai) du sondage de progression — une erreur
  permanente comme 401 ou 404 ne compte pas —, le Studio affiche « Connexion au serveur perdue — nouvelle tentative automatique »
  (ton ambre : l'exécution continue côté serveur) ; le message disparaît dès qu'une réponse arrive.

- **Points de reprise** : la sortie de chaque étape *design*, *architecture* et *diagnostic* terminée est sauvegardée (table
  `executioncheckpoint`, supprimée quand l'exécution réussit — après le commit du succès, au mieux — ou est supprimée). Ces étapes
  produisent du texte sans effet de bord ; *development* (écrit sur GitHub) et *qa* sont toujours rejouées. Seul un **préfixe
  continu** d'étapes est réutilisable (jamais une étape après un trou).
- **Reprise à l'étape en échec** : « Relancer » sur un tour en échec envoie `resume_from_execution_id`. Le serveur ne reprend que
  si l'exécution précédente est en échec, de la même conversation et du même utilisateur, avec la même demande et les mêmes
  précisions, le même workflow et le même repository cible ; sinon il repart de zéro, sans erreur. Les étapes réutilisées sont
  posées comme terminées (leur sortie reste le contexte des suivantes, l'état de l'Analyste est reconstruit en rejouant son
  contrôle, sans appel LLM, hors boucle asyncio ; la sortie sauvegardée étant celle déjà acceptée, ce rejeu retrouve les mêmes
  fichiers committables, y compris les exclusions annoncées) et la progression repart à la première étape restante. La réponse de
  `/api/execute` porte `resumed_steps` et le message affiche « Reprise : … déjà réussies ». La reprise ne s'applique que si la
  demande renvoyée est identique à celle du tour en échec.
- **Seconde tentative automatique** : après un échec transitoire (`retryable` : quota, modèle indisponible, délai) survenu pendant
  une étape **avant l'écriture du code** (design, architecture, diagnostic), la même exécution attend 90 s puis repart une seule
  fois en réutilisant ses étapes déjà réussies. Rien n'a encore été poussé sur GitHub à ce stade. Un échec au développement, à la
  QA, après le crew (vérification de livraison) ou hors étape n'est pas relancé automatiquement : il demanderait de réécrire sur
  GitHub et de repayer ces étapes, l'utilisateur utilise « Relancer ». La relance n'a lieu que si chaque étape déjà réussie a son
  point de reprise (sinon elle repayerait ces étapes pour rien : échec définitif). L'attente se fait hors du sémaphore
  d'exécutions et hors session de base, la ligne reste « en cours » (étape « queued »), et une annulation pendant l'attente la
  marque en échec. Un deuxième échec est définitif. Les mesures par agent de la tentative avortée ne sont pas enregistrées.
  S'ajoute au réessai interne sur quota de `run_dynamic_crew`, qui lui ne couvre pas les délais.

---

## 3. Architecture Logique vs Vue

### 3.1 Séparation des responsabilités

| Couche | Rôle | Fichiers clés |
|---|---|---|
| Frontend (Vue) | Fil de conversation, saisie, progression, historique, auth UI | `frontend/src/App.tsx`, `Studio.tsx`, `Login.tsx`, `components/*`, `components/metrics/*` (tableau de bord), `hooks/useRepoTarget.ts` (repository cible : champs, suggestions, préremplissage unique), `components/{ChatInput,ChatComposer,RepoTargetPanel,WorkflowTypeSelect}.tsx` (zone de saisie), `hooks/useConversation.ts` (état partagé et navigation, 167 lignes) assemblé à partir de `hooks/conversation/` (`useProgressPolling` + `progressMerge` (fonctions pures testées), `useLaunchPreview`, `useSendMessage` (trois chemins : réponse à une clarification, type choisi, qualification automatique), `sendOutcomes`, `turnMapping`), `hooks/useMetricsSummary.ts` ; résultat et échec : `ResultSections`, `FailureBlock`, `CopyButton` ; lancement : `LaunchPreview` ; progression : `StepIndicator`, `hooks/useStepStats`, `utils/estimateRemaining` ; notification : `hooks/useCompletionNotice`, `hooks/useNotifyPreference` ; aide : `InfoTip` (`components/metrics`) |
| Client Supabase | Session, token d'accès | `frontend/src/supabaseClient.ts` |
| API (Logique HTTP) | Assemblage de l'app, démarrage/arrêt, format d'erreur, points d'accès, schémas d'entrée | `backend/main.py` (assemblage, CORS, démarrage : tables, rattrapage des verdicts QA, balayage des orphelines ; arrêt : attente des crews), `routes_execute.py` (`/api/qualify`, `/api/execute`), `routes_conversations.py` (conversations, messages, progression, dépôts récents), `routes_history.py` (historique, suppression unitaire et par lot), `routes_metrics.py` (tableau de bord), `error_handlers.py` (format d'erreur unique, CORS), `schemas.py`, `auth.py`, `limits.py`, `memory_monitor.py` |
| Exécution en tâche de fond | Créneau d'exécution, contexte, issue, persistance, reprise | `backend/execution.py` (lancement, file d'attente, délai maximal), `execution_state.py` (tâches suivies, battement de cœur, étape courante), `execution_context.py` (consignes, aperçu du dépôt, plan et branche des tours précédents), `execution_outcomes.py` (succès, échec, relance, livraison), `execution_persistence.py` (sections d'agents, points de reprise, mesures), `execution_resume.py` (échec avant le développement, sorties réutilisables) |
| Socle backend | Journalisation, erreurs typées, validation, orphelines, résumé de projet | `backend/logs.py`, `errors.py`, `validation.py`, `orphans.py`, `project_summary.py`, `summary.py` |
| Orchestration agents | Agents et tâches CrewAI, déroulement d'une exécution, guardrails, retry | `backend/crewquestion.py` (agents et tâches), `crew_run.py` (`CrewRun` : reprise, retry résumable, échec attribué à une étape), `crew_tools.py` et `crew_checks.py` (outils et guardrails propres à une exécution, mixins de `AppDevelopmentCrew`, état déclaré dans `crew_state.py`), `crew_guardrails.py` (contrôles des specs, du plan, du verdict QA), `crew_workflow.py` (étapes, reprise, résultat formaté), `crew_retry.py`, `crew_llms.py` (un LLM par agent), `llm_models.py` (carte agent → modèle, plafonds RPM/TPM/RPD par modèle), `llm_limiter.py` (limiteur de débit par modèle : choix du limiteur, enveloppes du client), `llm_rate_window.py` (fenêtre de requêtes et de tokens, compteur journalier, pause 429), `llm_usage.py` (comptage des tokens), `llm_quota_errors.py` (reconnaissance des 429), `input_guards.py` (secrets masqués, contenus non fiables encadrés), `run_context.py` (identifiant `run-<id>` des journaux), `telemetry.py` (traces LangSmith, inactives par défaut), `run_budget.py` (budget de tokens par exécution), `delivery_guards.py` (secrets et scripts dangereux refusés avant commit), `crew_summary.py`, `crew_result.py`, `crew_workspace.py`, `crew_cache.py`, `conversation_context.py`, `qualification.py`, `agentsquestion.yaml`, `tasksquestion.yaml` |
| Contrôles de qualité (purs) | Lecture de la sortie de l'Analyste, livraison (commit/PR), rapport QA | `backend/analyst_output.py` (contrôle d'une sortie, rapport de livraison), `analyst_blocks.py` (balises de fichiers), `analyst_edits.py` (modifications ciblées), `analyst_imports.py`, `analyst_placeholders.py`, `delivery.py`, `qa_report.py` |
| Mesure de performance | Collecte par agent (événements CrewAI), agrégats (percentiles, moyennes, tendance) | `backend/metrics_collect.py`, `metrics_report.py`, `metrics_roles.py` |
| Outils agents et GitHub | Actions concrètes : lecture disque, lecture/écriture GitHub, vérification de syntaxe, livraison | `backend/tools.py`, `delimiter_scan.py` (équilibre des délimiteurs JS/TS), `github_client.py` (client, cache de lecture), `github_read.py`, `github_write.py`, `github_batch.py` (lot en un commit), `github_edit.py`, `github_edit_failures.py`, `github_guards.py` (branche protégée, portée d'écriture, fichiers sensibles), `github_snapshot.py`, `github_delivery.py`, `github_pull_request.py`, `github_access.py` |
| Persistance | Modèles et accès à la base de données | `backend/database.py` |

### 3.2 Flux de données

```
Utilisateur (navigateur)
  → Login.tsx (Supabase Auth) → session + access_token
  → Studio.tsx / useConversation : POST /api/qualify (mode auto) puis POST /api/execute
       → routes_execute.py : Depends(get_current_user) valide le token auprès de Supabase
       → /api/execute valide, vérifie GitHub, lit le résumé du dépôt (`execution_context.prefetch_repo_snapshot`) puis répond tout de suite ; asyncio.create_task lance `execution.execute_crew_and_persist`
            → crew_run.py : CrewRun.execute (via run_dynamic_crew : tâches sélectionnées, contexte par tâche,
              guardrails, retry résumable) ; agents → tools.py / github_*.py
            → chaque agent terminé est persisté (section + durée) dans ExecutionHistory.result
            → verify_github_delivery : branche et PR à jour confirmées via l'API GitHub
  → useConversation sonde /api/conversations/{id}/progress puis lit /messages
    (resynchronisation via GET /api/executions/{id})
```

L'état frontend est local au hook `useConversation` (pas de store global).

### 3.3 Organisation du code backend

- **Un module = une responsabilité**, sous 300 lignes (le plus long en compte 295) ; aucun module de façade : chaque import vise
  le module qui définit le nom, ce qui rend les dépendances lisibles.
- **Appel par module** (`execution_state.persist_current_step(...)`, `github_client._get_repo(...)`) plutôt que par nom importé :
  un test remplace alors un comportement en un seul endroit (`monkeypatch.setattr(<module>, "nom")`), et `database.engine` est
  l'unique point de remplacement de la base (`Session(database.engine)`).
- **Ordre d'import** : `crew_llms` est importé avant `crewai` (chargement du `.env`, réglage LiteLLM) ; l'abonnement de la mesure
  aux événements CrewAI (`metrics_collect.register_event_listeners`) se fait à l'import de `crewquestion`.
- **Classe du crew** : `AppDevelopmentCrew` (`crewquestion.py`) garde les agents et les tâches décorés ; ses outils et ses
  guardrails par exécution viennent de deux mixins, `CrewToolsMixin` (`crew_tools.py`) et `CrewChecksMixin` (`crew_checks.py`),
  qui lisent l'état déclaré dans `CrewExecutionState` (`crew_state.py`) et posé par `_reset_execution_state`. `run_dynamic_crew`
  délègue à `CrewRun` (`crew_run.py`).
- **Compatibilité** : le découpage n'a changé ni route, ni chemin OpenAPI, ni texte envoyé aux agents (toutes les chaînes de
  l'ancien code ont été retrouvées à l'identique).

---

## 4. Modèles de Données & État

### 4.1 État frontend

Défini dans `frontend/src/types.ts` et `hooks/useConversation.ts` : `ChatTurn` (message, statut `clarifying | running | success |
failed | cancelled`, workflow, résumé de l'agent, questions, résultat), `pendingClarification`, `workflowType` (`AUTO` ou une des
4 catégories), `RepoTarget { owner, name, branch }`, `pendingLaunch` (aperçu avant lancement : type, taille, étapes, repository ;
confirmation désactivable via `localStorage` `studio.confirmLaunch`). Le type de réponse d'historique (`HistoryListEntry`) ne
contient pas le résultat, lu à la demande par `GET /api/executions/{id}`. Le brouillon de saisie est conservé localement et effacé
à la déconnexion.

### 4.2 Contrats API

```ts
// POST /api/qualify
Request  { user_request: string; conversation_id?: number;
           has_repo_target?: boolean }  // sans repo cible, BUGFIX/FEATURE déclenchent une question
Response { summary: string; reasoning: string; is_clear: boolean;
           request_type: 'ANALYSE_ONLY' | 'BUGFIX' | 'FEATURE' | 'DESIGN_AND_DEV';
           alternative_type: <request_type> | null;
           scope: 'PETIT' | 'GRAND';     // PETIT : une FEATURE locale saute l'architecture
           confidence: number;            // 0-1, < 0.6 => is_clear = false
           questions: string[]; fallback: boolean }

// POST /api/execute  (répond immédiatement ; l'exécution continue en tâche de fond)
Request  { user_request: string; target_workflow: string; clarifications?: string;
           repo_owner?: string; repo_name?: string; base_branch?: string;
           conversation_id?: number; scope?: 'PETIT' | 'GRAND';
           resume_from_execution_id?: number }
Response { status: 'running'; id: number; conversation_id: number; resumed_steps?: string[] }
Erreurs  404 conversation introuvable · 409 exécution déjà en cours · 401 non authentifié

// GET /api/conversations/{id}/progress   → sondage pendant une exécution
Response { id: number | null; status: string | null; current_step: string | null;
           completed_agents: Record<string, string> }   // sections d'agents terminés

// Autres
POST /api/conversations (title?) · GET /api/conversations
GET  /api/conversations/{id}/messages?limit=100&before_id= → ExecutionHistory[]  (derniers tours, 500 au plus, ordre chronologique)
GET  /api/repo-targets → { repo_owner, repo_name, base_branch }[]   (20 derniers, distincts)

// GET /api/metrics/summary?days=30&workflow=BUGFIX&tz_offset=120   (days borné à 1-365 ; workflow optionnel ;
//   tz_offset = minutes à l'est d'UTC du navigateur, borné à ±840, pour regrouper `daily` par jour LOCAL)
// Exécutions TERMINÉES de l'utilisateur sur la période courante ET la précédente (20 000 au plus, les plus récentes : garde-fou mémoire, pas une limite de produit ; un usage normal sur 365 jours est calculé exactement).
Response { period_days: number; workflow: string | null;
           executions: { total, success, failed, median_duration_seconds, avg_llm_calls, avg_tokens,
                         token_executions, rate_limit_hits, wait_seconds,
                         auto_retried, resumed,                       // raisonnement : relances auto / reprises à l'étape
                         qa_verdicts: { GO, GO_AVEC_RESERVES, NO_GO }, // qualité : verdict QA final des résultats
                         total_cost | null, avg_cost | null };         // coût estimé ; null sans tarif ou sans tokens connus
           agents: { agent, label, runs, incomplete, duration_p50, duration_p95, avg_llm_calls, llm_errors,
                     token_runs, avg_prompt_tokens, avg_completion_tokens, avg_tool_calls, tool_errors }[];
           failures: { code, label, count }[];   // échecs par cause (error_code), du plus fréquent au moins fréquent
           truncated: boolean;                   // vrai si la limite de sécurité (20 000 exécutions) coupe la période courante
           previous: { …mêmes champs que `executions` } | null;   // période précédente de même durée ; null sans donnée ou si la limite la coupe
           comparison_limited: boolean;          // vrai si la limite a fait abandonner la comparaison alors que la période courante est complète
           currency: string | null;              // devise du coût (COST_CURRENCY, « $ » par défaut) ; null sans tarif
           daily: { date, executions, failed, llm_calls, tokens, qa_total, qa_go, median_duration_seconds | null }[] }

// GET /api/metrics/executions?days&workflow&status=all|success|failed&sort=created_at|duration|llm_calls|tokens&order=asc|desc&limit(1-100)&offset
// Exécutions terminées de la période, triées/filtrées côté serveur (valeurs manquantes toujours en dernier ; demande tronquée à 140 caractères).
Response { total: number;
           items: { id, conversation_id, user_request, workflow, status: 'success'|'failed', created_at, duration_seconds | null,
                    llm_calls | null, tokens | null, error_code | null, qa_verdict | null, attempts, reused_steps, repo | null }[] }

// GET /api/executions/{id}/agent-runs   → détail par agent d'UNE exécution (404 si elle n'est pas à l'utilisateur)
Response { agent, label, status: 'completed' | 'incomplete' | 'n/a', duration_seconds, llm_calls, llm_errors,
           tokens_known, prompt_tokens, completion_tokens, total_tokens, tool_calls, tool_errors }[]
GET  /api/history?limit&offset → HistoryListEntry[]  (l'exécution SANS `result` ni `clarifications`, ni champs internes : le résultat s'obtient par GET /api/executions/{id} ou avec la conversation)
GET  /api/executions/{id} → ExecutionHistory  (résultat complet ; 404 si elle n'est pas à l'utilisateur ; sert à resynchroniser un tour en cours)
GET  /api/conversations/{id}/messages → ExecutionHistory[]  (100 derniers tours par défaut, résultats compris ; l'interface n'a pas encore de « charger plus » : au-delà, les tours plus anciens ne sont pas affichés) · DELETE /api/history/{id}
POST /api/execute { …, resume_from_execution_id?: number, scope?: 'PETIT' | 'GRAND' }  → { status, id, conversation_id, resumed_steps: string[] }  (reprise : voir 2.9)
POST /api/history/bulk-delete {ids: int[≤100]} → {deleted: int[], skipped: [{id, reason: "running"|"not_found"}]}  (un seul commit ; supprime aussi les `agentrun` ; id étranger/inconnu → not_found ; en cours → running)
```

### 4.3 Persistance (Supabase Postgres, via SQLModel)

**Table `conversation`** : `id`, `user_id` (indexé), `title`, `created_at`, `updated_at`.

**Table `executionhistory`** (`backend/database.py`) :

| Colonne | Type | Description |
|---|---|---|
| `id` | int (PK, auto) | identifiant |
| `user_request` | text | demande initiale |
| `workflow` | text | workflow exécuté |
| `clarifications` | text (nullable) | précisions apportées par l'utilisateur |
| `result` | text (nullable) | sortie du crew, une section par agent avec sa durée |
| `status` | text | `running` \| `success` \| `failed` |
| `current_step` | text (nullable) | étape en cours (`design`, `architecture`, `diagnostic`, `development`, `qa`), vidée à la fin |
| `error_code` / `error_retryable` | text / bool (nullables) | cause d'un échec (voir 2.9), uniquement si `status = failed` |
| `user_id` | text (indexé) | auteur (id Supabase) |
| `conversation_id` | int (indexé) | fil de conversation |
| `repo_owner`, `repo_name`, `base_branch`, `work_branch` | text (nullable) | cible GitHub et branche de travail |
| `api_calls_count`, `rate_limit_hits`, `total_wait_time_seconds` | int / float (nullable) | coût et attentes de quota de l'exécution |
| `scope` | text (nullable) | `PETIT` : FEATURE dont l'étape d'architecture a été sautée ; `NULL` pour tout autre cas |
| `attempts`, `reused_steps` | int (nullable) | tentatives (2 = relance automatique) et étapes reprises d'une exécution précédente |
| `qa_verdict` | text (nullable) | verdict QA final (`GO`, `GO_AVEC_RESERVES`, `NO_GO`) |
| `created_at`, `updated_at` | datetime | horodatages |

**Table `agentrun`** : une ligne par agent mesuré et par exécution — `execution_id`, `conversation_id`, `user_id` (indexés),
`workflow`, `agent` (`design`, `architecture`, `diagnostic`, `development`, `qa`, `system`), `status` (`completed`, `incomplete`,
`n/a`), `duration_seconds` (nullable), `llm_calls`, `llm_errors`, `usage_calls` (appels dont les tokens sont connus),
`prompt_tokens`, `completion_tokens`, `total_tokens`, `tool_calls`, `tool_errors`, `created_at`. Table nouvelle : créée
automatiquement, sans migration.

**Table `executioncheckpoint`** : sortie d'une étape reprenable terminée — `execution_id` (indexé), `step` (`design`,
`architecture`, `diagnostic`), `raw`, `model` (modèle qui a produit la sortie, repris dans le rapport de l'agent ; colonne ajoutée
par migration `ADD COLUMN IF NOT EXISTS`, nulle pour les anciens points de reprise), `created_at`. Supprimée avec l'exécution.

L'URL de la Pull Request n'a **pas** de colonne dédiée : elle figure dans le texte du résultat (bloc « Livraison constatée par les
outils »). Le schéma `auth` est géré entièrement par Supabase. Au démarrage, `qa_verdict` des exécutions réussies antérieures est
rattrapé depuis le texte de leur résultat ; `attempts` et `reused_steps` ne le sont pas (inconnus avant 1.14, lus comme 1
tentative et 0 étape reprise). `status`, `sort` et `order` de `/api/metrics/executions` sont validés (422 hors valeurs listées).
Variables d'environnement du coût : `TOKEN_PRICE_INPUT_PER_MILLION`, `TOKEN_PRICE_OUTPUT_PER_MILLION` (sans elles, aucun coût
n'est affiché), `COST_CURRENCY`. Chronologie d'une exécution : les heures de début par étape ne sont pas stockées ; les étapes
sont placées bout à bout selon leur durée, suivies d'un segment « Attente et finalisation » (quota, écritures GitHub) quand il
reste au moins 1 s. Les colonnes ajoutées après coup sont rattrapées par des migrations légères au démarrage.

---

## 5. Écrans & Navigation

### 5.1 Écrans principaux

| Écran | Composant | Condition d'affichage | Description |
|---|---|---|---|
| Connexion | `Login.tsx` | pas de session Supabase active | Formulaire email + mot de passe |
| Chargement | inline dans `App.tsx` | vérification de session en cours | Texte « Chargement... » |
| Studio | `Studio.tsx` | session active | Tableau de bord de performance (`MetricsPanel`, ouvert par le bouton « 📊 Performance » de `StudioHeader`), fil de conversation (`ChatThread`, `ChatMessage` qui affiche l'indicateur d'étapes `StepIndicator` et le résumé par agent `AgentSummary`), saisie (`ChatInput`, qui contient le choix du workflow et le formulaire du repo cible `RepoTargetFields`), historique des exécutions (`HistoryPanel` : lignes `HistoryEntryRow`, barre `HistorySelectionBar`, état de sélection `hooks/useHistorySelection.ts`), en-tête (`StudioHeader`). Chaque message terminé peut déplier sa performance par agent (`ExecutionBreakdown`). Résultat (`ResultSections`) : une section repliable par agent, sommaire cliquable, « Tout déplier / Tout replier », copie par section et « Copier tout » (`CopyButton`). Échec (`FailureBlock`) : détail technique long replié ; pour `DELIVERY_FAILED`, phrase de diagnostic déduite de GitHub, rapport de l'agent replié et copiable, guide « Que faire ? » et lien vers la branche. **Temps restant** (`StepIndicator`, `utils/estimateRemaining.ts`, `hooks/useStepStats.ts`) : la frise affiche la durée de l'étape en cours et, quand l'étape a été vue démarrer, un temps restant = reste de l'étape courante (médiane − écoulé, jamais négatif) + médianes des étapes suivantes non reprises + finalisation ; les médianes viennent de `/api/metrics/summary` (30 jours, par workflow, mises en cache 10 min) et exigent au moins 3 exécutions par étape, sinon aucune estimation ; une étape plus longue que sa médiane est signalée ; les pauses de quota ne sont pas comptées. **Notification de fin** (`hooks/useCompletionNotice.ts`, `hooks/useNotifyPreference.ts`, bouton « 🔔 Me prévenir ») : pendant l'exécution le titre de l'onglet passe à « ⏳ », puis à « ✓ Terminé » ou « ✗ Échec » si l'onglet est masqué, jusqu'au retour ; une notification du navigateur est envoyée en plus si l'utilisateur l'a activée (désactivée par défaut, permission demandée à l'activation, jamais pour une exécution annulée). **Aperçu avant lancement** (`LaunchPreview`, détection automatique seulement, jamais pour une reprise) : après la qualification, le tour reste « en attente » avec type, taille (FEATURE), étapes qui vont tourner et repository, à corriger puis « Lancer » ou « Annuler » ; une nouvelle demande tapée remplace l'aperçu ; la case « Toujours demander confirmation » (activée par défaut, mémorisée dans le navigateur) permet de lancer directement |

### 5.2 Navigation

**Système de design** (`frontend/src/styles/`, `components/ui/`) : `tokens.css` définit les jetons sémantiques (surface, ligne,
texte, primaire, danger, succès, avertissement, information, rayons, espacements), en clair par défaut et en sombre selon le thème
de l'OS (`prefers-color-scheme`) ; aucune couleur d'interface n'est écrite en dur dans les composants. `ui.css` porte l'apparence
des composants de base avec leurs états (`:hover`, `:active`, `:disabled`, `:focus-visible`, mouvement réduit) : `Button`
(variantes primaire, secondaire, danger, information, discret ; tailles md, sm, pastille), `Card`, `Badge` (succès, danger,
avertissement), `Alert` (danger ou avertissement ; rôle `alert` pour une erreur, `status` sinon) et les champs de formulaire. Les
anciens jetons d'`index.css` (`--text`, `--bg`, `--border`, `--accent`…) sont des alias des jetons du design : une seule palette,
y compris pour l'anneau de focus. `app.css` contient la mise en page par classes (Studio, fil, historique, connexion). La
coloration syntaxique suit le thème (`useColorScheme`). Le tableau de bord conserve ses jetons `--viz-*` propres, déjà validés en
clair et en sombre. Seuls restent en style en ligne les styles calculés (hauteurs de la zone de saisie, largeurs de graphiques).

**Lecture, progression, échecs** : le Studio tient dans une colonne de lecture d'environ 760 px (messages de l'assistant sur toute
la largeur, ceux de l'utilisateur à droite, interligne confortable ; le texte est aligné à gauche). La progression d'une exécution
est une frise horizontale (`StepIndicator`) : pastille terminée (✓), en cours (icône de l'agent, pulsation coupée si mouvement
réduit), à venir (numéro) ou reprise d'une tentative précédente (↻, contour en pointillé) ; liste verticale sous 560 px ; liste
ordonnée avec `aria-current="step"` et état dit aux lecteurs d'écran. Un échec (`FailureBlock`) affiche la cause (icône et titre
selon `error_code`) et l'étape touchée ; une **panne temporaire** (`error_retryable`) est en ambre avec « Relancer » en bouton
principal, un **vrai échec** en rouge ; le bloc « Travail déjà présent sur GitHub » du message devient une carte avec la branche,
la PR et des liens cliquables (seuls les liens `https://github.com/…` sont reconnus, le reste reste du texte brut).

Pas de routeur : un écran conditionnel (`Login` vs `Studio`) piloté par l'état de session Supabase (`onAuthStateChange`). Dans le
Studio, l'historique est un panneau ; reprendre une conversation recharge ses tours.

**Sélection multiple de l'historique** : chaque ligne a une case à cocher (libellé accessible avec date et demande) ; Maj+clic
coche ou décoche une plage ; « Tout sélectionner » (état indéterminé si partiel) porte sur les lignes affichées (les 20 plus
récentes) ; compteur, « Supprimer la sélection (N) » (confirmation avec le nombre) et « Annuler la sélection ». Une exécution
`running` n'est pas sélectionnable (case désactivée, info-bulle). Si le serveur ignore certaines lignes, un message le dit (« 1
supprimée, 1 ignorée : en cours ») et seules les lignes supprimées ou introuvables disparaissent. Pas de pagination (hors
périmètre).

---

## 6. Exigences Non-Fonctionnelles

- **Performance** : pas d'exigence temps réel. Une exécution complète (`DESIGN_AND_DEV`) peut durer plusieurs minutes (pause de 5
  s entre appels, `max_rpm=13`, 5 agents) ; l'utilisateur suit la progression et peut fermer l'onglet, car l'exécution continue
  côté serveur.
- **Limites Gemini free tier** : environ 15 requêtes/minute. Le système s'adapte (pause, retry, reprise résumable) mais un quota
  épuisé peut allonger fortement une exécution.
- **Fiabilité** : retry sur 429/503, reprise des seules tâches restantes, contrôles automatiques déterministes (2.7), vérification
  de la livraison GitHub après le crew, une seule exécution à la fois par conversation.
- **Sécurité** : toutes les routes `/api/*` exigent un token Supabase valide ; conversations et historique filtrés par
  utilisateur. CORS actuellement ouvert (`allow_origins=["*"]`). Écriture GitHub jamais directe sur la branche principale ;
  chemins d'écriture confinés (espace de travail local, pas de `..`). Le texte généré dans les PR neutralise les `@mentions` et
  les mots-clés de fermeture d'issues.
- **Persistance** : historique en base Postgres (Supabase) ; les fichiers markdown intermédiaires (`docs/*.md`,
  `tests/reports/qa_report.md`) sont écrits sur le disque **éphémère** de Render (perdus au redéploiement) sauf s'ils sont écrits
  via les outils GitHub sur le repo cible. Sans `DATABASE_URL`, le backend retombe sur SQLite local éphémère.
- **Tests** : suite backend `pytest` (1441 cas, 777 fonctions, tout dans `backend/tests/`) rangée
  par thème et sans fichier de plus de 300 lignes : collecte et agrégats des mesures (`test_metrics_*`), suppression d'historique,
  exécution de bout en bout avec un faux crew (`test_run_*`, `test_resume_*`, `test_auto_retry`, `test_execution_deadline`),
  orphelines (`test_orphans_sweep`, `test_execution_heartbeat`, `test_partial_delivery`), crew (`test_crew_*`, dont
  `test_crew_run` pour `CrewRun`), analyse de la sortie (`test_analyst_*`), GitHub (`test_github*`), erreurs, limites, validation.
  Les fixtures communes (`engine`, `session`, `counting`) sont dans `tests/conftest.py`, qui remet aussi à zéro l'état d'exécution
  entre deux tests (la suite passe en ordre inverse) ; les fabriques de données sont dans les `*_support.py`.
  `test_module_hygiene` interdit qu'un nom `module.nom` s'infiltre dans un texte destiné aux agents. Les cas limites ajoutés (404,
  occurrences, commit par lot, retry de `CrewRun`) ont été contrôlés par mutation du code : une modification volontaire du module
  doit faire échouer un test. Suite frontend Vitest 5 (249 tests, 33 fichiers) ; `tsc`, `eslint`, build. Aucun test de bout en bout
  contre le vrai Gemini ni un vrai GitHub.
- **Internationalisation** : interface et prompts entièrement en français, non paramétrable.
- **Responsive** : l'aperçu avant lancement et le fil restent utilisables à 390 px ; pas de layout mobile dédié pour le tableau de bord.
- **Accessibilité** : attributs ARIA sur les contrôles récents (copie annoncée, aperçu en attente de confirmation, focus sur «
  Lancer ») ; pas d'audit WCAG complet.
- **Sécurité (mise à jour)** : les écritures GitHub sont confinées aux branches `crewai/…` ; le `GITHUB_TOKEN` reste partagé par
  tous les utilisateurs (voir §7).

---

### 6.1 Qualité du code et intégration continue

- **Dépendances** : les dépendances directes de `backend/requirements.txt` sont épinglées (`==`) et TOUTES les dépendances
  (indirectes comprises, `pydantic` inclus : CrewAI le borne lui-même) sont figées par `backend/requirements.lock`, appliqué comme
  contraintes par `-c requirements.lock` en tête de `requirements.txt` — la commande de build habituelle (`pip install -r
  requirements.txt`) l'utilise donc sans changement. Le verrou est résolu pour Python 3.12 (celui de la production,
  `backend/.python-version`) ; la suite complète a été vérifiée dans un environnement vierge installé depuis ce fichier (Python
  3.11). Mise à jour : changer une version dans `requirements.txt`, régénérer le verrou (commande en tête du fichier), lancer
  `pytest`, relire le journal des modifications de CrewAI.
- **CI en deux versions de Python** : la CI backend tourne en 3.11 (minimum accepté, `asyncio.timeout`) et en 3.12 (production),
  avec le verrou dans la clé du cache pip. Ses contrôles s'appellent « backend (3.11) » et « backend (3.12) » : une règle de
  protection de branche qui exigerait l'ancien nom « backend » doit être mise à jour. Un test vérifie que chaque dépendance
  directe de `requirements.txt` a la même version dans le verrou et que le verrou est entièrement épinglé, trié, sans doublon.
- **Veille des failles** : le job `audit` de la CI (informatif, n'empêche pas de fusionner) lance `pip-audit` sur le verrou et
  `npm audit --omit=dev` ; Dependabot propose chaque semaine les mises à jour npm et GitHub Actions (pas Python : le verrou se
  régénère à la main). Une exécution de `pip-audit` sur le verrou actuel signale 5 lignes d'avis sur `chromadb` 1.1.1 (4 avis : dépendance
  indirecte bornée par CrewAI, sans version corrigée publiée, visant le serveur Chroma que l'application ne lance pas) : ignorés
  explicitement dans `ci.yml` après examen (prémisse testée par `test_audit_premises.py`), à revoir à chaque mise à jour de CrewAI. Limites connues du verrou : résolu
  pour Linux x86-64 (un hébergement ARM compilerait certains paquets) et sans empreintes de paquets (`--require-hashes`).
- **Lint backend** (`backend/ruff.toml`) : erreurs réelles seulement (imports et variables inutilisés, noms indéfinis, erreurs de
  syntaxe, arguments mutables par défaut) ; pas de règles de style, qui réécriraient l'historique sans corriger de bug.
- **Types backend** (`backend/mypy.ini`) : tous les modules du backend sont vérifiés (API, exécution, orchestration, analyse de la
  sortie, GitHub, mesures), sauf `database.py`, `logs.py` et `project_summary.py`. Pour `main.py`, `routes_*`,
  `execution_*` et les mixins du crew, les codes `arg-type`, `union-attr`, `operator` et `call-overload` sont désactivés (bruit
  des clés primaires `Optional[int]` de SQLModel) ; attribut inexistant, variable non annotée, affectation incompatible et retour
  manquant y restent contrôlés. `crewquestion` a ses propres exceptions (`call-arg`, `arg-type`, `override`) pour les limites des stubs CrewAI (méthodes `@task`). Les outils de développement sont épinglés (`requirements-dev.txt`, dont `types-PyYAML`) et alignés
  sur le `rev` de ruff du pré-commit, pour qu'une nouvelle version ne casse pas la CI sans changement de code.
- **Tests frontend** (Vitest + Testing Library, `frontend/vitest.config.ts`, fichiers `*.test.ts(x)` à côté du code) : fonctions
  pures (échecs, tons, formats, parseurs), hooks (`useHistorySelection`, `useConnectionStatus`) et composants (`HistoryPanel` :
  sélection multiple, lot partiel, erreurs ; `FailureBlock`, `StepIndicator`, `LaunchPreview`, `ResultSections`, `StudioHeader`,
  `ChatMessage`), hooks de notification, de statistiques d'étapes et de resynchronisation de `useConversation`.
- **Exécution d'une demande** (`execution.run_crew_and_persist` et modules `execution_*`) : une suite d'étapes nommées —
  `execution_context.capture_branch_sha`, `crew_inputs` / `repo_instructions`, `execution.run_crew`,
  `execution_outcomes.verify_delivery`, `persist_success` (`persist_success_safely`), `persist_failure` (`failure_detail`),
  `retry_outputs_if_transient`, `mark_startup_failure` — au lieu d'une fonction unique de plus de 400 lignes. L'état d'une
  tentative (SHA de référence, métriques) vit dans `execution_context.RunState`, lu par le chemin d'échec même si le crew a planté
  en route. Le SHA de la branche de travail est capturé pour tout run avec repository cible (`ANALYSE_ONLY` compris : les
  consignes données aux agents en dépendent). La persistance du succès est hors du `try` du crew : une erreur de validation après
  coup est retentée une fois sur une Session neuve, puis journalisée — elle ne déclare jamais en échec une exécution déjà livrée
  (la ligne resterait « running » jusqu'au balayage des orphelines).

### 6.2 Variables d'environnement

| Variable | Défaut | Rôle |
|---|---|---|
| `GEMINI_API_KEY` | — | Clé du modèle |
| `GEMINI_MODEL` | — (carte `AGENT_MODELS`) | Force UN modèle pour tous les agents et le résumé |
| `GEMINI_MODEL_LIMITS` | — | JSON `{"modèle": {"rpm": …, "tpm": …, "rpd": …}}` remplaçant les plafonds par modèle |
| `LLM_RATE_MARGIN` | `0.8` | Part des plafonds RPM/TPM/RPD réellement utilisée (0,1 à 1) |
| `RUN_MAX_TOKENS` | 1000000 | Budget de tokens par exécution (minimum 10000) ; atteint : arrêt `TOKEN_BUDGET_EXCEEDED`, non réessayable |
| `LANGSMITH_TRACING` / `LANGSMITH_API_KEY` | absents | Les DEUX requis pour activer les traces OpenTelemetry vers LangSmith (données envoyées à un tiers) |
| `LANGSMITH_ENDPOINT` / `LANGSMITH_PROJECT` | api.smith.langchain.com / `myiacrew` | Adresse et projet des traces |
| `OTEL_CAPTURE_CONTENT` | absent | `true` : envoie aussi la sortie des agents (secrets masqués, 4000 caractères au plus) |
| `GITHUB_TOKEN` | — | Accès GitHub (contenu + pull requests) |
| `DATABASE_URL` | SQLite local | Base Postgres (Supabase) |
| `SQL_ECHO` | `false` | Trace des requêtes SQL |
| `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` / `DB_POOL_TIMEOUT` | 10 / 10 / 30 | Réserve de connexions (hors SQLite) |
| `SUPABASE_URL` / `SUPABASE_ANON_KEY` | — | Validation du token côté backend |
| `LOCAL_WORKSPACE_DIR` | — | Espace de travail local sans repository cible |
| `LOG_LEVEL` | `INFO` | Niveau de journalisation |
| `CREW_LOG_FILE` | — | Chemin du journal détaillé de CrewAI (prompts et sorties complets) ; absent : aucun fichier |
| `ARCHITECT_MAX_OUTPUT_TOKENS` | 8192 | Plafond de sortie de l'Architecte |
| `MAX_USER_RUNNING_EXECUTIONS` / `MAX_USER_EXECUTIONS_PER_HOUR` / `MAX_USER_QUALIFY_PER_MINUTE` | 1 / 30 / 20 | Plafonds par utilisateur (429 `RATE_LIMITED`) |
| `AUTO_RETRY_DELAY_S` | 90 | Attente avant la seconde tentative automatique |
| `MAX_CONCURRENT_EXECUTIONS` | 1 | Exécutions de crew simultanées (par process) ; les suivantes attendent |
| `EXECUTION_TIMEOUT_S` | 1800 | Durée maximale d'un crew (minimum 60) ; au-delà : échec `EXECUTION_TIMEOUT`, créneau rendu |
| `AUTH_CACHE_TTL_S` | 60 | Durée du cache de validation des jetons (0 : désactivé) |
| `GEMINI_MAX_REQUESTS_PER_MINUTE` | 12 | Plafond commun de requêtes Gemini par minute (par process) |
| `DIAGNOSTIC_REASONING_EFFORT` / `DIAGNOSTIC_STEP_MAX_ITERATIONS` | low / 8 | Effort de planification du diagnostic (low, medium, high) et itérations du modèle par étape |
| `COST_CURRENCY` / `TOKEN_PRICE_INPUT_PER_MILLION` / `TOKEN_PRICE_OUTPUT_PER_MILLION` | `$` / 0 / 0 | Coût estimé du tableau de bord |
| `VITE_API_URL`, `VITE_SUPABASE_URL`, `VITE_SUPABASE_ANON_KEY` | — | Frontend |

---

---

## 7. Questions Ouvertes

- [ ] Faut-il stocker l'URL de la Pull Request générée dans une colonne dédiée de `executionhistory`, plutôt que seulement dans le
      texte du résultat ?
- [ ] Autorisation par repository : le `GITHUB_TOKEN` est partagé, tout utilisateur authentifié peut cibler n'importe quel dépôt
      accessible au jeton. Mécanisme à choisir (liste blanche serveur, table par utilisateur, ou les deux) et comportement par
      défaut (tout refuser / autoriser avec avertissement).
- [ ] Supabase : la protection au niveau des lignes (RLS) est-elle activée sur `conversation`, `executionhistory`,
      `executionlaunch` et les autres tables, ou le schéma `public` est-il retiré de l'API REST exposée ? La clé anonyme est
      publique côté frontend ; le backend, lui, se connecte en direct à Postgres et n'en dépend pas.
- [ ] Le CORS `allow_origins=["*"]` doit-il être restreint au domaine Vercel de production ? Les identifiants CORS (`allow_credentials=True`) sont inutiles : l'API utilise un jeton Bearer, pas de cookies.
- [ ] Risque accepté à confirmer : les agents lisent des fichiers du dépôt cible, dont le contenu peut contenir des consignes hostiles (injection de prompt) ; l'impact est borné par les écritures confinées (branche `crewai/…`, fichiers sensibles refusés).
- [ ] Les fichiers `docs/*.md` écrits sur le disque local de Render ont-ils encore une utilité maintenant que l'écriture se fait
      directement sur GitHub ?
- [ ] Faut-il ajouter une vraie compilation (`tsc`, build) dans un bac à sable pour la QA, au lieu des vérifications statiques ?
- [ ] Faut-il repasser en « prête à relire » une PR ouverte en brouillon quand un tour ultérieur complète la livraison (l'API REST
      ne le permet pas, il faudrait GraphQL) ?
- [ ] Le comptage de tokens et d'appels LLM repose sur les événements CrewAI : il reste à le confirmer sur des exécutions réelles
      (usage fourni par Gemini, rôle de l'agent sur chaque événement). Faut-il alerter quand un agent atteint son plafond
      d'itérations ?
- [ ] Un échantillon d'exécutions réelles doit-il servir à calibrer les seuils des contrôles (0.6 de confiance, bornes du
      designer, budgets de lecture) ?

---

## 8. Checklist du Livrable

- [x] Boucle principale (conversations, exécution asynchrone, progression) et workflows documentés
- [x] 6 agents, outils, budgets et timeouts listés
- [x] Intégration GitHub (branche réutilisée, commit extrait en Python, PR générée, vérification de livraison) décrite
- [x] Contrôles automatiques de qualité par agent documentés
- [x] Authentification Supabase documentée
- [x] Contrats API et modèles de données/état décrits
- [x] Schémas des tables `conversation` et `executionhistory`
- [x] Écrans et navigation listés
- [x] Mesure de performance par agent et tableau de bord documentés
- [x] Variables d'environnement consolidées (§6.2)
- [x] Variantes PETIT/GRAND, écritures confinées et observabilité documentées
- [x] Questions ouvertes identifiées
- [x] Organisation du code backend (modules, conventions, couverture de types) et des tests documentée (§3.1, §3.3, §6.1)

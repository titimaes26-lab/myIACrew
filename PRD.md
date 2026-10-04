# PRD — myIACrew (Studio CrewAI)

> Version : 1.42 — Mise à jour le 2026-10-04
> Adapté du gabarit `game-prd-creator` : ce repo n'est pas un jeu mais un orchestrateur multi-agents ; les sections ont été ajustées au produit réel.
> Historique (condensé) :
> - 1.0–1.2 (09-17 → 10-02) : version initiale, quota Gemini, 6 agents, contrôles de qualité, conversations.
> - 1.3–1.8 (10-02 → 10-03) : mesure de performance par agent et tableau de bord, historique en sélection multiple, erreurs typées, orphelines, validation des entrées, contrôle préalable GitHub.
> - 1.9–1.16 (10-03 → 10-04) : reprise à l'étape en échec et seconde tentative, système de design, CI/lint/types/tests, historique des performances, `DELIVERY_FAILED`, infobulles p50/p95.
> - 1.17–1.22 (10-04) : performance de l'Architecte (aperçu du dépôt, cache de lectures, plafond de sortie, `scope` PETIT/GRAND, plan précédent).
> - 1.23–1.26 (10-04) : interface (sommaire du résultat, aperçu avant lancement, temps restant estimé, notification de fin).
> - 1.27–1.30 (10-04) : revue complète (écritures limitées à `crewai/…`, endpoints en threads, moteur SQL résilient, balayage atomique, logs `logging` sur une ligne).
> - 1.31 (10-04) : consolidation du PRD (variantes de workflow, tableaux de modules, variables d'environnement, chiffres de tests).
> - 1.32 (10-04) : résumé final structuré en 3 blocs, ligne de faits calculée en Python, résumé de repli sans modèle ; un 404 de proxy ne marque plus une exécution supprimée.
> - 1.33 (10-04) : échec avec bloc « Déjà réalisé avant l'échec » (sans modèle) et prochaines étapes du résumé cliquables (préremplissent la saisie).
> - 1.34 (10-04) : revue complète : fichiers de CI/environnement/déploiement jamais écrits par un agent, une seule exécution `running` par conversation garantie par un index unique partiel, plafonds par utilisateur (exécutions simultanées, par heure, qualifications par minute).
> - 1.35 (10-04) : suite de la revue : plafond horaire compté sur un journal de lancements non supprimable, 1 exécution simultanée par défaut, refus avant toute création de conversation, liste des fichiers sensibles complétée (modèles `.env.example` autorisés).
> - 1.36 (10-04) : un double envoi dans la même conversation garde le message de conflit précis (409) avant les plafonds par compte ; question ouverte RLS Supabase.
> - 1.37 (10-04) : limiteur global des requêtes Gemini (plafond commun par minute, pause commune après un 429) sous tous les appels au modèle.
> - 1.38 (10-04) : limiteur Gemini : pause commune plafonnée à 120 s (quota journalier : échec rapide), détection des 429 par code HTTP.
> - 1.39 (10-04) : diagnostic : planification en effort « low » (un seul appel de plan, observation par heuristique, plan et étapes bornés) au lieu de l'observation LLM par étape.
> - 1.40 (10-04) : diagnostic : itérations par étape portées à 8 et réglables, contrepartie de l'effort « low » documentée.
> - 1.41 (10-04) : une seule exécution de crew à la fois (`MAX_CONCURRENT_EXECUTIONS`, 1 par défaut) : les suivantes attendent leur tour (« queued »).
> - 1.42 (10-04) : durée maximale d'une exécution (`EXECUTION_TIMEOUT_S`, 20 min : le créneau est rendu), position dans la file affichée, « en cours depuis » sans le temps de file.

---

## 1. Synthèse & Vision

- **Concept** : un studio web qui qualifie une demande de développement en langage naturel, puis délègue son exécution à une équipe de 6 agents CrewAI (qualification, game/product designer, architecte, analyste diagnostic, développeur, QA) capable de lire du code sur un repository GitHub cible, de produire le code complet, de le committer sur une branche de travail et d'ouvrir une Pull Request.
- **Genre** : outil interne d'assistance au développement (multi-agent orchestration tool), pas une application grand public.
- **Plateforme cible** : Web — frontend Vite + React 19 + TypeScript (déployé sur Vercel), backend FastAPI + CrewAI (déployé sur Render), base de données PostgreSQL (Supabase), authentification Supabase Auth.
- **Public visé** : l'équipe/le·s propriétaire·s du studio, authentifié·s (comptes créés manuellement dans le dashboard Supabase — pas d'auto-inscription).
- **Boucle principale** : décrire un besoin dans un fil de conversation → qualification automatique (ou choix manuel) du type de demande → exécution par les agents en tâche de fond avec progression en direct → résultat par agent (texte + éventuellement une Pull Request sur le repo cible) → historisation en base.
- **Principe de qualité** : la fiabilité ne repose pas sur la confiance dans le texte des LLM. Les faits (fichiers committés, URL de PR, verdict minimal) sont constatés par des outils et des contrôles Python, sans appel LLM supplémentaire (voir 2.7).

---

## 2. Spécification fonctionnelle

### 2.1 Boucle principale (Core Loop)

1. **Connexion** : l'utilisateur se connecte avec email + mot de passe (Supabase Auth). Sans session valide, seul l'écran de connexion est accessible.
2. **Conversation** : chaque échange appartient à un fil (`Conversation`). Les tours précédents sont résumés et fournis aux agents (10 derniers tours au plus) pour comprendre un message de suivi (« corrige ça », « ajoute aussi Y »).
3. **Qualification** (`POST /api/qualify`, mode « Détection automatique ») : `qualification_agent` (sans outils) classe la demande en une des 4 catégories avec une confiance, une taille (`scope` : `PETIT` seulement pour une FEATURE locale de 1 ou 2 fichiers sans nouvel écran ni nouvelle dépendance, sinon `GRAND`), une alternative éventuelle et jusqu'à 4 questions de clarification. Sous 0.6 de confiance, la demande est jugée floue et l'utilisateur est questionné. L'utilisateur peut aussi choisir le workflow à la main et court-circuiter cette étape.
4. **Repository cible** (optionnel) : owner / repo / branche de base GitHub, renseignés dans le formulaire « Repository cible ». Les combinaisons déjà utilisées sont proposées (`GET /api/repo-targets`).
5. **Exécution** (`POST /api/execute`) : la réponse est immédiate (`status: running`) et le crew s'exécute en tâche de fond, séquentiellement (voir 2.2). Si un repo est fourni, une branche de travail `crewai/<workflow>-<hex8>` est créée au premier tour puis **réutilisée aux tours suivants** de la conversation (même branche, même PR). Une seule exécution à la fois par conversation (`409` sinon).
6. **Progression** : le frontend sonde `GET /api/conversations/{id}/progress` ; chaque agent terminé apparaît au fur et à mesure avec sa durée d'exécution.
7. **Restitution** : le résultat combiné par agent est stocké dans `executionhistory` ; après le crew, le backend vérifie via l'API GitHub qu'une branche et une PR à jour existent réellement.

### 2.2 Workflows disponibles

| Type de demande | Tâches exécutées (séquentiel) |
|---|---|
| `ANALYSE_ONLY` | `design_task` → `architecture_task` |
| `BUGFIX` | `diagnostic_task` → `development_task` → `qa_task` |
| `FEATURE` | `architecture_task` → `diagnostic_task` → `development_task` → `qa_task` |
| `DESIGN_AND_DEV` (défaut) | `design_task` → `architecture_task` → `diagnostic_task` → `development_task` → `qa_task` |

**Variante PETIT** : pour une `FEATURE` qualifiée `scope = PETIT` (changement local), `architecture_task` est sautée (`FEATURE` → `diagnostic_task` → `development_task` → `qa_task`). Les étapes réellement jouées viennent de `workflow_step_keys(request_type, scope)` (`crewquestion.py`), reprise par le front via `workflowSteps(workflow, scope)`. Une reprise traite `scope` absent comme `GRAND`.

Contexte explicite par tâche (pas toutes les sorties précédentes) : `architecture` reçoit `design` ; `diagnostic` reçoit `design` + `architecture` ; `development` reçoit `diagnostic` ; `qa` reçoit `design` + `diagnostic` + `development`.

### 2.3 Agents CrewAI

| Agent | Rôle | Outils | Itérations max | Timeout LLM |
|---|---|---|---|---|
| `qualification_agent` | Qualifie le type de demande | Aucun (interdiction explicite de lire des fichiers) | 2 | 45 s |
| `product_designer_agent` | Specs fonctionnelles (jeu ou application) | lecture disque local + lecture GitHub | 3 | 90 s |
| `architect_agent` | Architecture technique React/TypeScript | lecture disque local + lecture GitHub | 5 | 90 s |
| `diagnostic_agent` | Diagnostic de la cause et rédaction du code source complet | lecture seule (disque local + GitHub) | 7 | 120 s |
| `developer_agent` | Commit fidèle du code de l'Analyste et ouverture de la PR | écriture seule : commit des fichiers de l'Analyste, branche, `check_syntax`, PR | 6 | 90 s |
| `qa_agent` | Vérification outillée et verdict | `qa_verify_delivered_files`, lecture, `check_syntax` | 10 | 90 s |

Le plafond de sortie de l'Architecte est de `ARCHITECT_MAX_OUTPUT_TOKENS` (8192 par défaut) ; ses garde-fous sont `_architecture_issues` (structure attendue) et `_truncation_issues` (sortie tronquée). L'Architecte et le Diagnostic reçoivent un résumé déterministe du projet (`project_summary.py`) et l'Architecte le plan du tour précédent.

**Résumé final** (`## Résumé`, après `<!--crew-summary-->`) : une ligne « Faits » calculée en Python (`backend/summary.py` : type de demande, étapes terminées, fichiers produits par l'Analyste, verdict QA — jamais écrits par le modèle), puis la synthèse du modèle en 3 blocs (« Ce qui a été fait », « Pourquoi ces choix », « À faire ensuite »). Si la génération échoue (quota, délai), un résumé de repli sans modèle (première ligne utile de chaque agent) la remplace, signalé comme automatique.

**Prochaines étapes cliquables** : les puces du bloc « À faire ensuite » deviennent des boutons sous le résumé (`utils/nextSteps.ts`, 3 au plus) ; un clic préremplit la zone de saisie (même mécanisme que « Relancer »), sans envoi automatique ; masqués pendant une exécution, une clarification ou un aperçu en attente.

**Échec avec travail déjà fait** : `_persist_failure` ajoute, après le bloc GitHub, un bloc « Déjà réalisé avant l'échec » construit sans modèle depuis les sections des agents terminés (`partial_work_block`) ; le frontend l'affiche en carte (`splitPartialWork`, `FailureBlock`).

**Planification du diagnostic** : `diagnostic_agent` planifie avant d'agir (`PlanningConfig`, effort « low » par défaut, `DIAGNOSTIC_REASONING_EFFORT` = low | medium | high) : un seul appel de plan, l'observation de chaque étape se fait par heuristique sans appel au modèle, le plan est borné à 5 étapes et chaque étape à 8 itérations (`DIAGNOSTIC_STEP_MAX_ITERATIONS`, 15 par défaut dans CrewAI). L'ancien réglage (`reasoning=True`, effort « medium ») ajoutait un appel d'observation par étape, un replan complet sur échec (jusqu'à 3) et jusqu'à 15 itérations par étape, et — d'après le code de CrewAI — ces appels ne passaient pas par `max_rpm`. Contrepartie : en effort « low », une étape en échec n'est plus replanifiée (l'heuristique la marque terminée) ; les contrôles de qualité et la seconde tentative automatique restent le filet, et `DIAGNOSTIC_REASONING_EFFORT=medium` rétablit l'observation par le modèle.

Séparation voulue : l'Analyste ne peut pas écrire sur GitHub, le Développeur ne peut pas lire ni raisonner — il committe tel quel. Chaque agent rapporte son temps d'exécution (`execution_duration`), affiché dans l'UI.

### 2.4 Intégration GitHub (repo cible dynamique)

- L'utilisateur choisit le repository (owner/repo) et la branche de base à chaque exécution — rien n'est câblé en dur.
- Les écritures sont limitées aux branches `crewai/…` (et, pendant une exécution, à sa seule branche de travail via `track_write_scope`) ; tout autre nom (`main`, `test`…) est refusé sans appel réseau et tracé dans les logs. Branche de travail obligatoire, puis Pull Request.
- Le commit passe par l'outil `github_commit_analyst_files` : les fichiers de l'Analyste sont extraits **en Python** de sa réponse (balises), sans recopie par un LLM. Les fichiers Python/JSON/YAML invalides sont refusés avant commit.
- La description de la PR est **générée à partir des faits** (voir 2.7) ; elle est ouverte en brouillon si la livraison est partielle (repli sur une PR normale si le dépôt n'accepte pas les brouillons) et, si une PR existe déjà pour la branche, seule sa zone générée est mise à jour.
- Sans repository cible, l'écriture se fait dans un espace de travail local isolé par conversation (`LOCAL_WORKSPACE_DIR`), jamais sur le code du serveur.
- Nécessite la variable d'environnement `GITHUB_TOKEN` (PAT avec accès contenu + pull requests) côté backend.

### 2.5 Authentification

- Supabase Auth (email + mot de passe). Pas d'auto-inscription côté frontend : les comptes se créent depuis le dashboard Supabase.
- Le frontend envoie le token de session Supabase en `Authorization: Bearer` sur chaque appel API.
- Le backend valide ce token à chaque requête en interrogeant `SUPABASE_URL/auth/v1/user` (`backend/auth.py`) ; sans token valide, réponse `401`. Les conversations et l'historique sont filtrés par `user_id`.

### 2.6 Résilience LLM

- **LLM utilisé** : Gemini (`gemini/gemini-3.5-flash-lite` par défaut, configurable via `GEMINI_MODEL`), avec une température et un timeout par agent (2.3).
- **Quota** : le free tier impose environ **15 requêtes/minute** par projet/modèle. Une pause minimale de **5 s** entre appels (`QuotaManager.min_interval_seconds`) et `max_rpm=13` sur le crew limitent le débit ; le retry absorbe les 429 transitoires.
- **Retry** : une erreur 429/quota ou 503/surcharge est retentie avec attente `retry after N + 2 s` si Gemini l'indique, sinon `base_delay × 2^(essai-1)`. Qualification : 5 essais, base 12 s. Crew : 5 essais, base 15 s.
- **Reprise résumable** : sur erreur de quota après que certaines tâches ont terminé, **seules les tâches restantes** sont rejouées ; les sorties déjà obtenues continuent d'alimenter les suivantes. Le budget de relance du guardrail de diagnostic est réinitialisé quand `diagnostic_task` est rejouée.
- **Pas d'appel LLM de plus pour contrôler** : les guardrails de 2.7 sont déterministes ; seul le diagnostic peut être relancé une fois.

### 2.7 Contrôles automatiques de qualité

Tous les contrôles sont des fonctions Python pures et testées. Ils **signalent** (note ajoutée à la sortie) sauf le diagnostic, qui refuse une fois.

| Agent | Consignes imposées | Contrôle automatique |
|---|---|---|
| Qualification | 6 exemples de cas limites, confiance obligatoire, questions fermées ; question sur le repository pour un BUGFIX/FEATURE sans repo (« renseigne le formulaire Repository cible, ou réponds *local* ») | `confidence < 0.6` force `is_clear = false` et une question ; repli explicite (`fallback`) si la sortie est inexploitable |
| Designer | Identifiants stables `F1 [Must]`, critères `AC-F1-1` (Étant donné / Quand / Alors), MVP borné (5 Must, 3 critères chacun), exigences non fonctionnelles, composants avec états, gabarit jeu ou application, section « Contrôle » | Note « Contrôle automatique des specs » : section manquante, Must sans critère, critère vague sans seuil mesurable |
| Architecte | Aperçu du repository (racine, **résumé déterministe** de `package.json`/`tsconfig.json` — dépendances, conventions, mode strict, sans appel LLM — et `src` ; listings plafonnés à 150 lignes ; non lu quand une reprise réutilise l'architecture) lu en Python avant l'agent et donné dans son prompt (sinon 4 lectures par outils), consigne resserrée (~400 mots), sortie plafonnée (8 192 tokens par défaut, `ARCHITECT_MAX_OUTPUT_TOKENS`) avec détection d'une sortie tronquée, **plan du tour précédent** de la conversation donné comme base (il ne décrit que ce qui change), conventions reprises, « Dépendances à ajouter », contrats d'interface par fichier, tableau de couverture, risques | Note « Contrôle automatique de l'architecture » : sections, lignes `CRÉER\|MODIFIER chemin : rôle`, chemins dupliqués ou hors projet, fichier de code sans contrat |
| Diagnostic | Aperçu du repository déjà lu par le système (sans relire ces fichiers), hypothèses, traçabilité avec « ce qui infirmerait la cause » et « vérification manuelle », correctif minimal, 5 lectures au plus ; fichiers entre `<<<FICHIER>>>`, ou **modifications ciblées** `<<<MODIFICATION: chemin>>>` (blocs CHERCHER / REMPLACER) résolues en Python contre le fichier d'origine | Refus unique si : fichier tronqué ou sans balise de fin, commentaire de raccourci (`// ... reste du code`), bloc inapplicable (texte absent ou présent plusieurs fois), **import incohérent** (export manquant, import relatif sans cible) ; à la 2e tentative, les fichiers fautifs sont exclus du commit et signalés |
| Développeur | Message de commit conventionnel (`type: résumé`, 72 car.), titre de PR conventionnel, recopie exacte des chemins et URL | Bloc « Livraison constatée par les outils » ajouté au rapport (fichiers réellement committés, non livrés, URL de PR renvoyée) ; toute URL de PR citée sans venir d'un outil est signalée ; description de PR neutralisant `@mentions` et mots-clés de fermeture (`fixes #n`) |
| QA | Tableau `Critère \| Statut \| Preuve \| Correctif suggéré` ; KO avec `fichier:ligne` et correctif ; NON VÉRIFIABLE avec raison et test manuel | **Verdict minimal imposé** : `NO_GO` si un critère est KO, un problème bloquant est listé ou aucun fichier n'est committé ; `GO_AVEC_RESERVES` si des fichiers sont non livrés ou un critère NON VÉRIFIABLE. Un verdict plus indulgent est réécrit en place avec une note ; un plus sévère est conservé. Rapport de l'outil enrichi du périmètre (fichiers hors plan de l'architecte ou manquants) et de la cohérence des imports |

**Limites assumées** : la validation TypeScript/JavaScript est heuristique (délimiteurs équilibrés, imports, exports) — il n'y a ni `tsc` ni build ; la vérification de PR côté QA reste celle de l'outil et du backend, pas du LLM.

### 2.8 Mesure de performance par agent

- **Collecte** : `backend/agent_metrics.py` écoute les événements CrewAI (appel LLM terminé ou échoué, outil utilisé) et attribue chaque mesure à son agent par son rôle (`agentsquestion.yaml`). Les mesures sont isolées par exécution (contexte copié par le bus d'événements), donc deux exécutions concurrentes ne se mélangent pas. Les appels hors agent (synthèse finale) sont comptés à part.
- **Ce qui est mesuré, par agent et par exécution** : durée de la tâche, appels LLM réels, erreurs LLM, tokens (entrée, sortie, total), appels d'outils et erreurs d'outils. Un usage de tokens absent ou tout à zéro est **inconnu**, jamais « 0 token » : il n'entre pas dans les moyennes.
- **Changement de sens** : `api_calls_count` (par exécution) compte désormais les appels LLM réels. Il ne comptait avant que les tentatives de `kickoff`, ce qui sous-estimait fortement la consommation du quota.
- **Persistance** : une ligne `agentrun` par agent mesuré, écrite en fin d'exécution (succès ou échec). Un agent qui a fait des appels sans terminer sa tâche est marqué `incomplete`. L'écriture est « au mieux » : elle n'échoue jamais l'exécution.
- **Tableau de bord** (bouton « 📊 Performance » du Studio, chargé à la demande) : filtres période (7, 30, 90 jours) et workflow ; tuiles (exécutions, taux de succès, durée médiane, appels LLM, tokens, pauses quota) ; durée par agent (médiane en barre, p95 en point, un seul axe) ; tokens par agent (entrée/sortie empilées) ; échecs par cause (nombre d'exécutions en échec par `error_code`, du plus fréquent au moins fréquent ; les échecs d'avant le suivi des causes sont regroupés sous « Cause non enregistrée ») ; tendances quotidiennes (une métrique à la fois : appels LLM, durée médiane, taux de succès, tokens ; un jour sans donnée n'a pas de colonne) ; tableau de détail. Chaque tuile affiche son **écart par rapport à la période précédente de même durée** (flèche, signe et couleur : vert = mieux, rouge = moins bien selon la métrique ; le taux de succès en points, le reste en pourcentage ; aucun écart depuis une valeur nulle ou sans période comparable ; une phrase dit pourquoi quand la limite l'empêche). Un jour dont la valeur est réellement zéro garde un liseré, un jour sans donnée n'a aucune colonne. Chaque graphique a un jumeau tableau, une infobulle (souris et clavier) et des couleurs validées en clair et en sombre.
- **Détail d'une exécution** : sous chaque message terminé, « Voir la performance par agent » affiche durée, appels LLM, tokens et outils de cette exécution.

### 2.9 Gestion des erreurs

- **Format unique** : toute erreur de l'API est `{"detail": message, "code": CODE, "retryable": bool}` (`detail` reste la clé lue par le frontend). Les gestionnaires de `main.py` couvrent `AppError` (erreur volontaire d'un endpoint), `HTTPException` (y compris les 404/405 de routes et `auth.py`), les erreurs de validation (422, `detail` = phrase lisible, liste détaillée dans `errors`) et toute exception non prévue (500 générique, détail dans les logs seulement).
- **Codes** (`backend/errors.py`) : `QUOTA_EXHAUSTED`, `LLM_UNAVAILABLE`, `LLM_TIMEOUT`, `GITHUB_UNAVAILABLE`, `GUARDRAIL_FAILED`, `DELIVERY_FAILED` (livraison GitHub non confirmée après l'exécution : ni branche à jour ni PR ; non relançable tel quel), `EXECUTION_TIMEOUT`, `INTERRUPTED`, `INTERNAL_ERROR`, `VALIDATION_ERROR`, `UNAUTHORIZED`, `FORBIDDEN`, `NOT_FOUND`, `CONFLICT`, `RATE_LIMITED`, `SERVICE_UNAVAILABLE`. `classify_exception` range un échec du crew dans un code ; les mots-clés de quota/indisponibilité sont partagés avec les retries de `crewquestion.py`.
- **Écriture GitHub confinée** : les outils d'écriture et de création de branche (`github_write_file`, `github_write_files`, `github_edit_file`, `github_create_branch`, commit de l'Analyste) refusent, sans appel réseau, toute branche qui ne commence pas par `crewai/` (donc `main`, `test`, une branche de production…) ; pendant une exécution, seule sa propre branche de travail est écrivable. Une consigne glissée dans le dépôt cible ne peut donc pas faire écrire l'agent ailleurs.
- **Journalisation** : tout passe par `logging` (`backend/logs.py`, logger parent `myiacrew`, sortie standard vidée à chaque ligne, niveau `LOG_LEVEL`, INFO par défaut) ; les bibliothèques tierces ne sont pas touchées. Les messages écrits par l'application ne contiennent ni identifiant d'utilisateur ni texte de demande ni contenu de fichier (le texte d'une exception, lui, est repris tel quel : il peut contenir ce que renvoie GitHub ou le modèle). Un événement tient sur une ligne : les retours à la ligne d'un message sont écrits en clair (`\n`), pour qu'un texte multi-lignes ne fabrique pas de fausses lignes de log ; la trace d'une exception garde ses propres lignes (`exc_info`).
- **Branches de travail validées** : un nom d'écriture doit correspondre à `crewai/` + caractères sûrs (lettres, chiffres, `. _ - /`), sans espace, retour à la ligne, `..`, `//` ni `/` final.
- **Lecture ciblée à l'envoi** : `/api/execute` lit les tours en cours, les 10 derniers tours (pour le rappel de conversation) et la branche de travail précédente par trois requêtes ciblées, au lieu de toute la conversation avec le résultat de chaque tour.
- **Limiteur global Gemini** (`backend/llm_limiter.py`) : le quota de l'offre gratuite (15 requêtes par minute et par modèle) est partagé par tout le projet, alors que `max_rpm` ne vaut que pour un crew et que le planificateur/observateur de CrewAI (agents avec `reasoning=True`) fait des appels hors de ce compte. Le limiteur enveloppe les méthodes de requête du client `google-genai` (sync, flux, async) : au plus `GEMINI_MAX_REQUESTS_PER_MINUTE` requêtes (12) sur une minute glissante, chaque appelant réservant son créneau, et, sur un 429, le délai demandé par Google (« retry in Xs », `retryDelay`) devient une pause commune à tous les appels suivants (plafonnée à 120 s : au-delà, par exemple un quota journalier épuisé, aucune pause n'est posée et l'erreur remonte aussitôt comme « Quota épuisé » au lieu de laisser l'exécution dormir). Un 429 est reconnu par son code HTTP, sinon par un texte précis (`RESOURCE_EXHAUSTED`, « exceeded your current quota », « 429 » accompagné de quota/rate limit), jamais par un nombre cité dans un autre message. Les attentes de plus de 2 s sont tracées et comptées dans le temps d'attente de l'exécution. Plafond par process : plusieurs instances du backend le multiplient.
- **Fichiers sensibles protégés** : les outils d'écriture (`github_write_file`, `github_write_files`, `github_edit_file`, commit de l'Analyste) refusent, sans appel réseau, tout fichier de CI (`.github/`, `.circleci/`, `.gitlab-ci.yml`…), d'environnement (`.env*` sauf les modèles `.env.example`/`.sample`/`.template`, `.npmrc`) ou de déploiement (`Dockerfile*`, `docker-compose.*`, `vercel.json`, `render.yaml`, `.pre-commit-config.yaml`, `.gitmodules`…) et tout chemin contenant `..` : un workflow committé sur la branche de travail pourrait s'exécuter avec les secrets du dépôt avant toute relecture. Le refus est tracé (chemin seulement) et le fichier est signalé comme non livré.
- **Une exécution par conversation, garantie par la base** : un index unique partiel (`status = 'running'`) sur `executionhistory.conversation_id` ferme la course du contrôle applicatif (double clic, deux onglets) ; l'insert perdant répond 409 comme le contrôle. Créé aussi sur une base existante par la migration (journalisée si des doublons empêchent sa création).
- **Une exécution de crew à la fois** : le sémaphore global est à 1 par défaut (`MAX_CONCURRENT_EXECUTIONS`, par process) — deux crews en parallèle saturent le quota Gemini de l'offre gratuite (15 requêtes par minute) même étalés par le limiteur. Les exécutions suivantes, de tous les utilisateurs, attendent leur tour avec l'étape « queued » ; leur durée totale s'allonge d'autant. À relever avec une offre Gemini payante. Le sondage de progression renvoie `queue_ahead` (exécutions qui tournent ou en attente créées avant : un simple décompte, tous comptes confondus) et le Studio affiche « N exécutions passent avant elle ». Le temps affiché « en cours depuis » ne compte pas l'attente en file observée par ce navigateur (un tour retrouvé au rechargement, sans cette observation, affiche le temps total depuis sa création).
- **Durée maximale d'une exécution** : le crew est interrompu au bout de `EXECUTION_TIMEOUT_S` (1200 s par défaut ; hors attente en file et hors vérification de livraison). Sans cela, avec un seul créneau, une exécution bloquée (son battement de cœur continue, le balayage des orphelines ne la libère pas) retiendrait le service pour tous. L'exécution passe en échec `EXECUTION_TIMEOUT` (réessayable, jamais relancée automatiquement) et le créneau est rendu ; « Relancer » reprend les étapes déjà réussies. Limite : le crew tourne dans un thread que Python ne peut pas tuer, il peut finir de s'exécuter (et consommer du quota) après l'arrêt, sans effet sur l'exécution déjà en échec. Un timeout levé par le crew lui-même (délai d'un appel) reste classé `LLM_TIMEOUT`.
- **Plafonds par utilisateur** (`backend/limits.py`, 429 `RATE_LIMITED`, réessayable) : `MAX_USER_RUNNING_EXECUTIONS` (1 : le sémaphore global autorise 2 exécutions, un compte ne peut pas les occuper toutes), `MAX_USER_EXECUTIONS_PER_HOUR` (30), comptés en base — le plafond horaire lit le journal `executionlaunch`, que la suppression de l'historique ne vide pas (purgé au-delà de 2 h) ; le conflit de la conversation courante (409) est testé d'abord, puis les plafonds, avant toute création de conversation ; un lancement est journalisé dès que sa ligne d'historique est acceptée, même si le démarrage échoue ensuite ; `MAX_USER_QUALIFY_PER_MINUTE` (20), fenêtre glissante en mémoire par process.
- **Boucle d'événements libre** : les points d'accès qui n'attendent ni GitHub ni le LLM (historique, conversations, progression, tableau de bord, suppression) sont des fonctions synchrones, exécutées par FastAPI dans son pool de threads ; un calcul de tableau de bord sur 20 000 exécutions ne gèle plus le sondage de progression ni l'authentification. Le moteur SQL vérifie chaque connexion avant usage (`pool_pre_ping`) et les recycle après 30 minutes. Sa réserve de connexions est réglable (`DB_POOL_SIZE` 10, `DB_MAX_OVERFLOW` 10, `DB_POOL_TIMEOUT` 30 s par défaut, hors SQLite). Le balayage des exécutions orphelines libère chaque ligne par un `UPDATE` conditionnel (`status = 'running'`) : deux requêtes simultanées ne l'interrompent qu'une fois. Chaque écriture GitHub refusée est tracée (« AVERTISSEMENT SÉCURITÉ », branche demandée et autorisée, jamais de contenu).
- **Aucune fuite** : `/api/qualify` ne renvoie plus `str(e)` mais un message lisible lié au code (429/503/504/500).
- **Échecs d'exécution** : `executionhistory` stocke `error_code` et `error_retryable` ; l'étape en échec reste dans `result` (« Échec à l'étape N/M »). `result` contient un message lisible pour une cause reconnue, suivi du texte technique d'origine tronqué (« détail : … »). La classification remonte la chaîne `__cause__` (un timeout ou une panne GitHub dans une étape est reconnu) et compare des mots entiers (« 429 » ne reconnaît pas `page_4290.tsx`).
- **Interface** : `ApiError` porte `status`, `code`, `retryable` ; un serveur injoignable donne `NETWORK_ERROR` (« Impossible de joindre le serveur »). `ErrorBanner` affiche l'erreur avec « Réessayer » quand elle est transitoire (rechargement de l'historique). Sous un échec d'exécution, un conseil adapté au code est affiché (réessayer plus tard, ou reformuler la demande).

- **Exécutions orphelines** (`backend/orphans.py`) : une exécution restée `running` après un crash ou un redéploiement est marquée `failed` / `INTERRUPTED` (retryable) quand elle n'a plus donné signe de vie depuis 10 minutes (un battement écrit `updated_at` toutes les 60 s tant que l'exécution tourne, y compris pendant une longue étape ou une pause de quota, et à chaque changement d'étape) et n'est pas en cours dans le process courant (`_active_execution_ids`). Balayage au démarrage, au sondage de progression, avant le contrôle de concurrence de `/api/execute` (plus de 409 éternel) et avant toute suppression (une ligne orpheline devient supprimable). Le texte déjà produit est conservé. Limite : après un crash, la conversation est libérée au bout de 10 minutes, pas immédiatement ; avec plusieurs instances du backend (ou un déploiement en continu), le battement et le délai de 10 minutes empêchent de voir comme morte une exécution vivante d'une autre instance. La suppression ne balaie que les lignes visées.
- **Écritures GitHub partielles** : quand une exécution échoue sur un repository cible, le message d'échec est complété par un bloc « Travail déjà présent sur GitHub » constaté via l'API (`describe_partial_delivery`, lecture seule, 20 s maximum) : branche et commits d'avance (dont nouveaux commits de cette tentative), Pull Request ouverte ou fusionnée (ou « non vérifiée » si la recherche a échoué), ou « rien n'a été poussé ». Si GitHub est injoignable, le bloc le dit au lieu d'affirmer qu'il n'y a rien. Il rappelle que « Relancer » reprend sur la même branche et comment abandonner (fermer la PR, supprimer la branche).

- **Validation des entrées** (`backend/validation.py`) : `/api/execute` et `/api/qualify` refusent tout de suite (422, un message par champ dans `errors`) une demande vide ou de plus de 20 000 caractères, un workflow inconnu, un propriétaire (« _ » accepté : comptes Enterprise Managed User), un nom de repository ou un nom de branche invalide (règles GitHub et `git check-ref-format`, y compris par composant du chemin) ; la liste des workflows est dérivée de la qualification. Un champ repository vide est traité comme absent.
- **Contrôle préalable GitHub** (`check_github_access`, lecture seule) : pour un workflow qui écrit sur un repository cible (pas `ANALYSE_ONLY`), `/api/execute` vérifie avant toute ligne en base et tout appel LLM que le jeton est présent, que le repository est lisible, que le jeton a le droit d'écriture et que la branche de base existe. Contrôle exécuté après la vérification de la conversation et borné à 15 s. Refus : 404 `NOT_FOUND`, 403 `FORBIDDEN`, 503 réessayable pour la limite de débit GitHub, 502 `GITHUB_UNAVAILABLE` (jeton invalide, non réessayable), 503 `GITHUB_UNAVAILABLE` (panne, réessayable), 500 (jeton absent), avec un message qui dit quoi corriger. Il ajoute un aller-retour GitHub au lancement.
- **Perte de connexion visible** : après 3 pannes consécutives (réseau coupé, 5xx, délai) du sondage de progression — une erreur permanente comme 401 ou 404 ne compte pas —, le Studio affiche « Connexion au serveur perdue — nouvelle tentative automatique » (ton ambre : l'exécution continue côté serveur) ; le message disparaît dès qu'une réponse arrive.

- **Points de reprise** : la sortie de chaque étape *design*, *architecture* et *diagnostic* terminée est sauvegardée (table `executioncheckpoint`, supprimée quand l'exécution réussit — après le commit du succès, au mieux — ou est supprimée). Ces étapes produisent du texte sans effet de bord ; *development* (écrit sur GitHub) et *qa* sont toujours rejouées. Seul un **préfixe continu** d'étapes est réutilisable (jamais une étape après un trou).
- **Reprise à l'étape en échec** : « Relancer » sur un tour en échec envoie `resume_from_execution_id`. Le serveur ne reprend que si l'exécution précédente est en échec, de la même conversation et du même utilisateur, avec la même demande et les mêmes précisions, le même workflow et le même repository cible ; sinon il repart de zéro, sans erreur. Les étapes réutilisées sont posées comme terminées (leur sortie reste le contexte des suivantes, l'état de l'Analyste est reconstruit en rejouant son contrôle, sans appel LLM, hors boucle asyncio ; la sortie sauvegardée étant celle déjà acceptée, ce rejeu retrouve les mêmes fichiers committables, y compris les exclusions annoncées) et la progression repart à la première étape restante. La réponse de `/api/execute` porte `resumed_steps` et le message affiche « Reprise : … déjà réussies ». La reprise ne s'applique que si la demande renvoyée est identique à celle du tour en échec.
- **Seconde tentative automatique** : après un échec transitoire (`retryable` : quota, modèle indisponible, délai) survenu pendant une étape **avant l'écriture du code** (design, architecture, diagnostic), la même exécution attend 90 s puis repart une seule fois en réutilisant ses étapes déjà réussies. Rien n'a encore été poussé sur GitHub à ce stade. Un échec au développement, à la QA, après le crew (vérification de livraison) ou hors étape n'est pas relancé automatiquement : il demanderait de réécrire sur GitHub et de repayer ces étapes, l'utilisateur utilise « Relancer ». La relance n'a lieu que si chaque étape déjà réussie a son point de reprise (sinon elle repayerait ces étapes pour rien : échec définitif). L'attente se fait hors du sémaphore d'exécutions et hors session de base, la ligne reste « en cours » (étape « queued »), et une annulation pendant l'attente la marque en échec. Un deuxième échec est définitif. Les mesures par agent de la tentative avortée ne sont pas enregistrées. S'ajoute au réessai interne sur quota de `run_dynamic_crew`, qui lui ne couvre pas les délais.

---

## 3. Architecture Logique vs Vue

### 3.1 Séparation des responsabilités

| Couche | Rôle | Fichiers clés |
|---|---|---|
| Frontend (Vue) | Fil de conversation, saisie, progression, historique, auth UI | `frontend/src/App.tsx`, `Studio.tsx`, `Login.tsx`, `components/*`, `components/metrics/*` (tableau de bord), `hooks/useConversation.ts`, `hooks/useMetricsSummary.ts` ; résultat et échec : `ResultSections`, `FailureBlock`, `CopyButton` ; lancement : `LaunchPreview` ; progression : `StepIndicator`, `hooks/useStepStats`, `utils/estimateRemaining` ; notification : `hooks/useCompletionNotice`, `hooks/useNotifyPreference` ; aide : `InfoTip` (`components/metrics`) |
| Client Supabase | Session, token d'accès | `frontend/src/supabaseClient.ts` |
| API (Logique HTTP) | Endpoints, validation des entrées, auth, exécution en tâche de fond, persistance, vérification de livraison | `backend/main.py`, `backend/auth.py` |
| Socle backend | Journalisation, erreurs typées, validation, orphelines, résumé de projet | `backend/logs.py`, `backend/errors.py`, `backend/validation.py`, `backend/orphans.py`, `backend/project_summary.py` |
| Orchestration agents | Définition et exécution des agents/tâches CrewAI, guardrails, retry | `backend/crewquestion.py`, `backend/agentsquestion.yaml`, `backend/tasksquestion.yaml` |
| Contrôles de qualité (purs) | Lecture de la sortie de l'Analyste, livraison (commit/PR), rapport QA | `backend/analyst_output.py`, `backend/delivery.py`, `backend/qa_report.py` |
| Mesure de performance | Collecte par agent (événements CrewAI), agrégats (percentiles, moyennes, tendance) | `backend/agent_metrics.py` |
| Outils agents | Actions concrètes (lecture disque, lecture/écriture GitHub, vérification de syntaxe) | `backend/tools.py`, `backend/github_tools.py` |
| Persistance | Modèles et accès à la base de données | `backend/database.py` |

### 3.2 Flux de données

```
Utilisateur (navigateur)
  → Login.tsx (Supabase Auth) → session + access_token
  → Studio.tsx / useConversation : POST /api/qualify (mode auto) puis POST /api/execute
       → main.py : Depends(get_current_user) valide le token auprès de Supabase
       → /api/execute valide, vérifie GitHub, lit le résumé du dépôt (`_prefetch_repo_snapshot`) puis répond tout de suite ; asyncio.create_task lance _execute_crew_and_persist
            → crewquestion.py : run_dynamic_crew (tâches sélectionnées, contexte par tâche,
              guardrails, retry résumable) ; agents → tools.py / github_tools.py
            → chaque agent terminé est persisté (section + durée) dans ExecutionHistory.result
            → verify_github_delivery : branche et PR à jour confirmées via l'API GitHub
  → useConversation sonde /api/conversations/{id}/progress puis lit /messages
    (resynchronisation via GET /api/executions/{id})
```

L'état frontend est local au hook `useConversation` (pas de store global).

---

## 4. Modèles de Données & État

### 4.1 État frontend

Défini dans `frontend/src/types.ts` et `hooks/useConversation.ts` : `ChatTurn` (message, statut `clarifying | running | success | failed | cancelled`, workflow, résumé de l'agent, questions, résultat), `pendingClarification`, `workflowType` (`AUTO` ou une des 4 catégories), `RepoTarget { owner, name, branch }`, `pendingLaunch` (aperçu avant lancement : type, taille, étapes, repository ; confirmation désactivable via `localStorage` `studio.confirmLaunch`). Le type de réponse d'historique (`HistoryListEntry`) ne contient pas le résultat, lu à la demande par `GET /api/executions/{id}`. Le brouillon de saisie est conservé localement et effacé à la déconnexion.

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
GET  /api/conversations/{id}/messages → ExecutionHistory[]
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
GET  /api/conversations/{id}/messages → ExecutionHistory[]  (conversation complète, résultats compris) · DELETE /api/history/{id}
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

**Table `agentrun`** : une ligne par agent mesuré et par exécution — `execution_id`, `conversation_id`, `user_id` (indexés), `workflow`, `agent` (`design`, `architecture`, `diagnostic`, `development`, `qa`, `system`), `status` (`completed`, `incomplete`, `n/a`), `duration_seconds` (nullable), `llm_calls`, `llm_errors`, `usage_calls` (appels dont les tokens sont connus), `prompt_tokens`, `completion_tokens`, `total_tokens`, `tool_calls`, `tool_errors`, `created_at`. Table nouvelle : créée automatiquement, sans migration.

**Table `executioncheckpoint`** : sortie d'une étape reprenable terminée — `execution_id` (indexé), `step` (`design`, `architecture`, `diagnostic`), `raw`, `created_at`. Supprimée avec l'exécution. Table nouvelle : créée automatiquement, sans migration.

L'URL de la Pull Request n'a **pas** de colonne dédiée : elle figure dans le texte du résultat (bloc « Livraison constatée par les outils »). Le schéma `auth` est géré entièrement par Supabase. Au démarrage, `qa_verdict` des exécutions réussies antérieures est rattrapé depuis le texte de leur résultat ; `attempts` et `reused_steps` ne le sont pas (inconnus avant 1.14, lus comme 1 tentative et 0 étape reprise). `status`, `sort` et `order` de `/api/metrics/executions` sont validés (422 hors valeurs listées). Variables d'environnement du coût : `TOKEN_PRICE_INPUT_PER_MILLION`, `TOKEN_PRICE_OUTPUT_PER_MILLION` (sans elles, aucun coût n'est affiché), `COST_CURRENCY`. Chronologie d'une exécution : les heures de début par étape ne sont pas stockées ; les étapes sont placées bout à bout selon leur durée, suivies d'un segment « Attente et finalisation » (quota, écritures GitHub) quand il reste au moins 1 s. Les colonnes ajoutées après coup sont rattrapées par des migrations légères au démarrage.

---

## 5. Écrans & Navigation

### 5.1 Écrans principaux

| Écran | Composant | Condition d'affichage | Description |
|---|---|---|---|
| Connexion | `Login.tsx` | pas de session Supabase active | Formulaire email + mot de passe |
| Chargement | inline dans `App.tsx` | vérification de session en cours | Texte « Chargement... » |
| Studio | `Studio.tsx` | session active | Tableau de bord de performance (`MetricsPanel`, ouvert par le bouton « 📊 Performance » de `StudioHeader`), fil de conversation (`ChatThread`, `ChatMessage` qui affiche l'indicateur d'étapes `StepIndicator` et le résumé par agent `AgentSummary`), saisie (`ChatInput`, qui contient le choix du workflow et le formulaire du repo cible `RepoTargetFields`), historique des exécutions (`HistoryPanel` : lignes `HistoryEntryRow`, barre `HistorySelectionBar`, état de sélection `hooks/useHistorySelection.ts`), en-tête (`StudioHeader`). Chaque message terminé peut déplier sa performance par agent (`ExecutionBreakdown`). Résultat (`ResultSections`) : une section repliable par agent, sommaire cliquable, « Tout déplier / Tout replier », copie par section et « Copier tout » (`CopyButton`). Échec (`FailureBlock`) : détail technique long replié ; pour `DELIVERY_FAILED`, phrase de diagnostic déduite de GitHub, rapport de l'agent replié et copiable, guide « Que faire ? » et lien vers la branche. **Temps restant** (`StepIndicator`, `utils/estimateRemaining.ts`, `hooks/useStepStats.ts`) : la frise affiche la durée de l'étape en cours et, quand l'étape a été vue démarrer, un temps restant = reste de l'étape courante (médiane − écoulé, jamais négatif) + médianes des étapes suivantes non reprises + finalisation ; les médianes viennent de `/api/metrics/summary` (30 jours, par workflow, mises en cache 10 min) et exigent au moins 3 exécutions par étape, sinon aucune estimation ; une étape plus longue que sa médiane est signalée ; les pauses de quota ne sont pas comptées. **Notification de fin** (`hooks/useCompletionNotice.ts`, `hooks/useNotifyPreference.ts`, bouton « 🔔 Me prévenir ») : pendant l'exécution le titre de l'onglet passe à « ⏳ », puis à « ✓ Terminé » ou « ✗ Échec » si l'onglet est masqué, jusqu'au retour ; une notification du navigateur est envoyée en plus si l'utilisateur l'a activée (désactivée par défaut, permission demandée à l'activation, jamais pour une exécution annulée). **Aperçu avant lancement** (`LaunchPreview`, détection automatique seulement, jamais pour une reprise) : après la qualification, le tour reste « en attente » avec type, taille (FEATURE), étapes qui vont tourner et repository, à corriger puis « Lancer » ou « Annuler » ; une nouvelle demande tapée remplace l'aperçu ; la case « Toujours demander confirmation » (activée par défaut, mémorisée dans le navigateur) permet de lancer directement |

### 5.2 Navigation

**Système de design** (`frontend/src/styles/`, `components/ui/`) : `tokens.css` définit les jetons sémantiques (surface, ligne, texte, primaire, danger, succès, avertissement, information, rayons, espacements), en clair par défaut et en sombre selon le thème de l'OS (`prefers-color-scheme`) ; aucune couleur d'interface n'est écrite en dur dans les composants. `ui.css` porte l'apparence des composants de base avec leurs états (`:hover`, `:active`, `:disabled`, `:focus-visible`, mouvement réduit) : `Button` (variantes primaire, secondaire, danger, information, discret ; tailles md, sm, pastille), `Card`, `Badge` (succès, danger, avertissement), `Alert` (danger ou avertissement ; rôle `alert` pour une erreur, `status` sinon) et les champs de formulaire. Les anciens jetons d'`index.css` (`--text`, `--bg`, `--border`, `--accent`…) sont des alias des jetons du design : une seule palette, y compris pour l'anneau de focus. `app.css` contient la mise en page par classes (Studio, fil, historique, connexion). La coloration syntaxique suit le thème (`useColorScheme`). Le tableau de bord conserve ses jetons `--viz-*` propres, déjà validés en clair et en sombre. Seuls restent en style en ligne les styles calculés (hauteurs de la zone de saisie, largeurs de graphiques).

**Lecture, progression, échecs** : le Studio tient dans une colonne de lecture d'environ 760 px (messages de l'assistant sur toute la largeur, ceux de l'utilisateur à droite, interligne confortable ; le texte est aligné à gauche). La progression d'une exécution est une frise horizontale (`StepIndicator`) : pastille terminée (✓), en cours (icône de l'agent, pulsation coupée si mouvement réduit), à venir (numéro) ou reprise d'une tentative précédente (↻, contour en pointillé) ; liste verticale sous 560 px ; liste ordonnée avec `aria-current="step"` et état dit aux lecteurs d'écran. Un échec (`FailureBlock`) affiche la cause (icône et titre selon `error_code`) et l'étape touchée ; une **panne temporaire** (`error_retryable`) est en ambre avec « Relancer » en bouton principal, un **vrai échec** en rouge ; le bloc « Travail déjà présent sur GitHub » du message devient une carte avec la branche, la PR et des liens cliquables (seuls les liens `https://github.com/…` sont reconnus, le reste reste du texte brut).

Pas de routeur : un écran conditionnel (`Login` vs `Studio`) piloté par l'état de session Supabase (`onAuthStateChange`). Dans le Studio, l'historique est un panneau ; reprendre une conversation recharge ses tours.

**Sélection multiple de l'historique** : chaque ligne a une case à cocher (libellé accessible avec date et demande) ; Maj+clic coche ou décoche une plage ; « Tout sélectionner » (état indéterminé si partiel) porte sur les lignes affichées (les 20 plus récentes) ; compteur, « Supprimer la sélection (N) » (confirmation avec le nombre) et « Annuler la sélection ». Une exécution `running` n'est pas sélectionnable (case désactivée, info-bulle). Si le serveur ignore certaines lignes, un message le dit (« 1 supprimée, 1 ignorée : en cours ») et seules les lignes supprimées ou introuvables disparaissent. Pas de pagination (hors périmètre).

---

## 6. Exigences Non-Fonctionnelles

- **Performance** : pas d'exigence temps réel. Une exécution complète (`DESIGN_AND_DEV`) peut durer plusieurs minutes (pause de 5 s entre appels, `max_rpm=13`, 5 agents) ; l'utilisateur suit la progression et peut fermer l'onglet, car l'exécution continue côté serveur.
- **Limites Gemini free tier** : environ 15 requêtes/minute. Le système s'adapte (pause, retry, reprise résumable) mais un quota épuisé peut allonger fortement une exécution.
- **Fiabilité** : retry sur 429/503, reprise des seules tâches restantes, contrôles automatiques déterministes (2.7), vérification de la livraison GitHub après le crew, une seule exécution à la fois par conversation.
- **Sécurité** : toutes les routes `/api/*` exigent un token Supabase valide ; conversations et historique filtrés par utilisateur. CORS actuellement ouvert (`allow_origins=["*"]`). Écriture GitHub jamais directe sur la branche principale ; chemins d'écriture confinés (espace de travail local, pas de `..`). Le texte généré dans les PR neutralise les `@mentions` et les mots-clés de fermeture d'issues.
- **Persistance** : historique en base Postgres (Supabase) ; les fichiers markdown intermédiaires (`docs/*.md`, `tests/reports/qa_report.md`) sont écrits sur le disque **éphémère** de Render (perdus au redéploiement) sauf s'ils sont écrits via les outils GitHub sur le repo cible. Sans `DATABASE_URL`, le backend retombe sur SQLite local éphémère.
- **Tests** : suite backend `pytest` (722 tests) couvrant les contrôles purs, les guardrails, les outils, la base et les logs ; suite frontend Vitest (203 tests, 27 fichiers) ; `tsc`, `eslint`, build. Aucun test de bout en bout contre le vrai Gemini ni un vrai GitHub.
- **Internationalisation** : interface et prompts entièrement en français, non paramétrable.
- **Responsive** : l'aperçu avant lancement et le fil restent utilisables à 390 px ; pas de layout mobile dédié pour le tableau de bord.
- **Accessibilité** : attributs ARIA sur les contrôles récents (copie annoncée, aperçu en attente de confirmation, focus sur « Lancer ») ; pas d'audit WCAG complet.
- **Sécurité (mise à jour)** : les écritures GitHub sont confinées aux branches `crewai/…` ; le `GITHUB_TOKEN` reste partagé par tous les utilisateurs (voir §7).

---

### 6.1 Qualité du code et intégration continue

- **CI** (`.github/workflows/ci.yml`, sur `main`, `test` et chaque pull request) : backend (`ruff check`, `mypy`, `pytest`) et frontend (`eslint`, `tsc -b`, `vitest`, build). Dépendances de développement : `backend/requirements-dev.txt`. En local, `pre-commit` (`.pre-commit-config.yaml`) lance ruff et eslint + tsc avant chaque commit.
- **Lint backend** (`backend/ruff.toml`) : erreurs réelles seulement (imports et variables inutilisés, noms indéfinis, erreurs de syntaxe, arguments mutables par défaut) ; pas de règles de style, qui réécriraient l'historique sans corriger de bug.
- **Types backend** (`backend/mypy.ini`) : vérifiés sur les modules récents et purs (`errors`, `validation`, `orphans`, `delivery`, `qa_report`, `agent_metrics`) et sur `main.py`, où seuls les codes `arg-type`, `union-attr`, `operator` et `call-overload` sont désactivés (bruit des clés primaires `Optional[int]` de SQLModel) ; attribut inexistant, variable non annotée, affectation incompatible et retour manquant y restent contrôlés. Les autres gros modules y entreront par étapes. Les outils de développement sont épinglés (`requirements-dev.txt`, dont `types-PyYAML`) et alignés sur le `rev` de ruff du pré-commit, pour qu'une nouvelle version ne casse pas la CI sans changement de code.
- **Tests frontend** (Vitest + Testing Library, `frontend/vitest.config.ts`, fichiers `*.test.ts(x)` à côté du code) : fonctions pures (échecs, tons, formats, parseurs), hooks (`useHistorySelection`, `useConnectionStatus`) et composants (`HistoryPanel` : sélection multiple, lot partiel, erreurs ; `FailureBlock`, `StepIndicator`, `LaunchPreview`, `ResultSections`, `StudioHeader`, `ChatMessage`), hooks de notification, de statistiques d'étapes et de resynchronisation de `useConversation`.
- **Exécution d'une demande** (`_run_crew_and_persist`, `backend/main.py`) : une suite d'étapes nommées — `_capture_branch_sha`, `_crew_inputs` / `_repo_instructions`, `_run_crew`, `_verify_delivery`, `_persist_success`, `_persist_failure` (`_failure_detail`), `_retry_outputs_if_transient`, `_mark_startup_failure` — au lieu d'une fonction unique de plus de 400 lignes. L'état d'une tentative (SHA de référence, métriques) vit dans `_RunState`, lu par le chemin d'échec même si le crew a planté en route. Le SHA de la branche de travail est capturé pour tout run avec repository cible (`ANALYSE_ONLY` compris : les consignes données aux agents en dépendent). La persistance du succès est hors du `try` du crew : une erreur de validation après coup est retentée une fois sur une Session neuve, puis journalisée — elle ne déclare jamais en échec une exécution déjà livrée (la ligne resterait « running » jusqu'au balayage des orphelines).

### 6.2 Variables d'environnement

| Variable | Défaut | Rôle |
|---|---|---|
| `GEMINI_API_KEY` | — | Clé du modèle |
| `GEMINI_MODEL` | `gemini/gemini-3.5-flash-lite` | Modèle utilisé |
| `GITHUB_TOKEN` | — | Accès GitHub (contenu + pull requests) |
| `DATABASE_URL` | SQLite local | Base Postgres (Supabase) |
| `SQL_ECHO` | `false` | Trace des requêtes SQL |
| `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` / `DB_POOL_TIMEOUT` | 10 / 10 / 30 | Réserve de connexions (hors SQLite) |
| `SUPABASE_URL` / `SUPABASE_ANON_KEY` | — | Validation du token côté backend |
| `LOCAL_WORKSPACE_DIR` | — | Espace de travail local sans repository cible |
| `LOG_LEVEL` | `INFO` | Niveau de journalisation |
| `ARCHITECT_MAX_OUTPUT_TOKENS` | 8192 | Plafond de sortie de l'Architecte |
| `MAX_USER_RUNNING_EXECUTIONS` / `MAX_USER_EXECUTIONS_PER_HOUR` / `MAX_USER_QUALIFY_PER_MINUTE` | 1 / 30 / 20 | Plafonds par utilisateur (429 `RATE_LIMITED`) |
| `AUTO_RETRY_DELAY_S` | 90 | Attente avant la seconde tentative automatique |
| `MAX_CONCURRENT_EXECUTIONS` | 1 | Exécutions de crew simultanées (par process) ; les suivantes attendent |
| `EXECUTION_TIMEOUT_S` | 1200 | Durée maximale d'un crew (minimum 60) ; au-delà : échec `EXECUTION_TIMEOUT`, créneau rendu |
| `GEMINI_MAX_REQUESTS_PER_MINUTE` | 12 | Plafond commun de requêtes Gemini par minute (par process) |
| `DIAGNOSTIC_REASONING_EFFORT` / `DIAGNOSTIC_STEP_MAX_ITERATIONS` | low / 8 | Effort de planification du diagnostic (low, medium, high) et itérations du modèle par étape |
| `COST_CURRENCY` / `TOKEN_PRICE_INPUT_PER_MILLION` / `TOKEN_PRICE_OUTPUT_PER_MILLION` | `$` / 0 / 0 | Coût estimé du tableau de bord |
| `VITE_API_URL`, `VITE_SUPABASE_URL`, `VITE_SUPABASE_ANON_KEY` | — | Frontend |

---

---

## 7. Questions Ouvertes

- [ ] Faut-il stocker l'URL de la Pull Request générée dans une colonne dédiée de `executionhistory`, plutôt que seulement dans le texte du résultat ?
- [ ] Autorisation par repository : le `GITHUB_TOKEN` est partagé, tout utilisateur authentifié peut cibler n'importe quel dépôt accessible au jeton. Mécanisme à choisir (liste blanche serveur, table par utilisateur, ou les deux) et comportement par défaut (tout refuser / autoriser avec avertissement).
- [ ] Supabase : la protection au niveau des lignes (RLS) est-elle activée sur `conversation`, `executionhistory`, `executionlaunch` et les autres tables, ou le schéma `public` est-il retiré de l'API REST exposée ? La clé anonyme est publique côté frontend ; le backend, lui, se connecte en direct à Postgres et n'en dépend pas.
- [ ] Le CORS `allow_origins=["*"]` doit-il être restreint au domaine Vercel de production ?
- [ ] Les fichiers `docs/*.md` écrits sur le disque local de Render ont-ils encore une utilité maintenant que l'écriture se fait directement sur GitHub ?
- [ ] Faut-il ajouter une vraie compilation (`tsc`, build) dans un bac à sable pour la QA, au lieu des vérifications statiques ?
- [ ] Faut-il repasser en « prête à relire » une PR ouverte en brouillon quand un tour ultérieur complète la livraison (l'API REST ne le permet pas, il faudrait GraphQL) ?
- [ ] Le comptage de tokens et d'appels LLM repose sur les événements CrewAI : il reste à le confirmer sur des exécutions réelles (usage fourni par Gemini, rôle de l'agent sur chaque événement). Faut-il alerter quand un agent atteint son plafond d'itérations ?
- [ ] Un échantillon d'exécutions réelles doit-il servir à calibrer les seuils des contrôles (0.6 de confiance, bornes du designer, budgets de lecture) ?

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

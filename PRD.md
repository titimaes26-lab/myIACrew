# PRD — myIACrew (Studio CrewAI)

> Version : 1.12 — Mise à jour le 2026-10-04
> Adapté du gabarit `game-prd-creator` : ce repo n'est pas un jeu mais un orchestrateur multi-agents ; les sections ont été ajustées au produit réel.
> Historique : 1.0 (2026-09-17) version initiale · 1.1 (2026-10-01) temps d'exécution, quota Gemini · 1.2 (2026-10-02) 6 agents, contrôles automatiques de qualité, conversations, contrats d'API à jour · 1.3 (2026-10-02) mesure de performance par agent et tableau de bord · 1.4 (2026-10-03) sélection multiple et suppression groupée dans l'historique · 1.5 (2026-10-03) gestion des erreurs typées · 1.6 (2026-10-03) exécutions orphelines et écritures GitHub partielles · 1.7 (2026-10-03) validation des entrées, contrôle préalable GitHub, perte de connexion visible · 1.8 (2026-10-03) échecs par cause dans le tableau de bord · 1.9 (2026-10-03) reprise à l'étape en échec et seconde tentative automatique · 1.10 (2026-10-03) système de design (jetons, composants de base) et thème sombre sur toute l'interface · 1.11 (2026-10-03) colonne de lecture, frise de progression, échecs plus parlants · 1.12 (2026-10-04) qualité du code : CI, lint, types, tests frontend, exécution découpée en étapes.

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
3. **Qualification** (`POST /api/qualify`, mode « Détection automatique ») : `qualification_agent` (sans outils) classe la demande en une des 4 catégories avec une confiance, une alternative éventuelle et jusqu'à 4 questions de clarification. Sous 0.6 de confiance, la demande est jugée floue et l'utilisateur est questionné. L'utilisateur peut aussi choisir le workflow à la main et court-circuiter cette étape.
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

Séparation voulue : l'Analyste ne peut pas écrire sur GitHub, le Développeur ne peut pas lire ni raisonner — il committe tel quel. Chaque agent rapporte son temps d'exécution (`execution_duration`), affiché dans l'UI.

### 2.4 Intégration GitHub (repo cible dynamique)

- L'utilisateur choisit le repository (owner/repo) et la branche de base à chaque exécution — rien n'est câblé en dur.
- Aucune écriture directe sur `main`/`master` (refus côté outils) : branche de travail obligatoire, puis Pull Request.
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
| Architecte | Lecture de `package.json`/`tsconfig.json` (4 lectures), conventions reprises, « Dépendances à ajouter », contrats d'interface par fichier, tableau de couverture, risques | Note « Contrôle automatique de l'architecture » : sections, lignes `CRÉER\|MODIFIER chemin : rôle`, chemins dupliqués ou hors projet, fichier de code sans contrat |
| Diagnostic | Hypothèses, traçabilité avec « ce qui infirmerait la cause » et « vérification manuelle », correctif minimal, 5 lectures au plus ; fichiers entre `<<<FICHIER>>>`, ou **modifications ciblées** `<<<MODIFICATION: chemin>>>` (blocs CHERCHER / REMPLACER) résolues en Python contre le fichier d'origine | Refus unique si : fichier tronqué ou sans balise de fin, commentaire de raccourci (`// ... reste du code`), bloc inapplicable (texte absent ou présent plusieurs fois), **import incohérent** (export manquant, import relatif sans cible) ; à la 2e tentative, les fichiers fautifs sont exclus du commit et signalés |
| Développeur | Message de commit conventionnel (`type: résumé`, 72 car.), titre de PR conventionnel, recopie exacte des chemins et URL | Bloc « Livraison constatée par les outils » ajouté au rapport (fichiers réellement committés, non livrés, URL de PR renvoyée) ; toute URL de PR citée sans venir d'un outil est signalée ; description de PR neutralisant `@mentions` et mots-clés de fermeture (`fixes #n`) |
| QA | Tableau `Critère \| Statut \| Preuve \| Correctif suggéré` ; KO avec `fichier:ligne` et correctif ; NON VÉRIFIABLE avec raison et test manuel | **Verdict minimal imposé** : `NO_GO` si un critère est KO, un problème bloquant est listé ou aucun fichier n'est committé ; `GO_AVEC_RESERVES` si des fichiers sont non livrés ou un critère NON VÉRIFIABLE. Un verdict plus indulgent est réécrit en place avec une note ; un plus sévère est conservé. Rapport de l'outil enrichi du périmètre (fichiers hors plan de l'architecte ou manquants) et de la cohérence des imports |

**Limites assumées** : la validation TypeScript/JavaScript est heuristique (délimiteurs équilibrés, imports, exports) — il n'y a ni `tsc` ni build ; la vérification de PR côté QA reste celle de l'outil et du backend, pas du LLM.

### 2.8 Mesure de performance par agent

- **Collecte** : `backend/agent_metrics.py` écoute les événements CrewAI (appel LLM terminé ou échoué, outil utilisé) et attribue chaque mesure à son agent par son rôle (`agentsquestion.yaml`). Les mesures sont isolées par exécution (contexte copié par le bus d'événements), donc deux exécutions concurrentes ne se mélangent pas. Les appels hors agent (synthèse finale) sont comptés à part.
- **Ce qui est mesuré, par agent et par exécution** : durée de la tâche, appels LLM réels, erreurs LLM, tokens (entrée, sortie, total), appels d'outils et erreurs d'outils. Un usage de tokens absent ou tout à zéro est **inconnu**, jamais « 0 token » : il n'entre pas dans les moyennes.
- **Changement de sens** : `api_calls_count` (par exécution) compte désormais les appels LLM réels. Il ne comptait avant que les tentatives de `kickoff`, ce qui sous-estimait fortement la consommation du quota.
- **Persistance** : une ligne `agentrun` par agent mesuré, écrite en fin d'exécution (succès ou échec). Un agent qui a fait des appels sans terminer sa tâche est marqué `incomplete`. L'écriture est « au mieux » : elle n'échoue jamais l'exécution.
- **Tableau de bord** (bouton « 📊 Performance » du Studio, chargé à la demande) : filtres période (7, 30, 90 jours) et workflow ; tuiles (exécutions, taux de succès, durée médiane, appels LLM, tokens, pauses quota) ; durée par agent (médiane en barre, p95 en point, un seul axe) ; tokens par agent (entrée/sortie empilées) ; échecs par cause (nombre d'exécutions en échec par `error_code`, du plus fréquent au moins fréquent ; les échecs d'avant le suivi des causes sont regroupés sous « Cause non enregistrée ») ; appels LLM par jour ; tableau de détail. Chaque graphique a un jumeau tableau, une infobulle (souris et clavier) et des couleurs validées en clair et en sombre.
- **Détail d'une exécution** : sous chaque message terminé, « Voir la performance par agent » affiche durée, appels LLM, tokens et outils de cette exécution.

### 2.9 Gestion des erreurs

- **Format unique** : toute erreur de l'API est `{"detail": message, "code": CODE, "retryable": bool}` (`detail` reste la clé lue par le frontend). Les gestionnaires de `main.py` couvrent `AppError` (erreur volontaire d'un endpoint), `HTTPException` (y compris les 404/405 de routes et `auth.py`), les erreurs de validation (422, `detail` = phrase lisible, liste détaillée dans `errors`) et toute exception non prévue (500 générique, détail dans les logs seulement).
- **Codes** (`backend/errors.py`) : `QUOTA_EXHAUSTED`, `LLM_UNAVAILABLE`, `LLM_TIMEOUT`, `GITHUB_UNAVAILABLE`, `GUARDRAIL_FAILED`, `INTERRUPTED`, `INTERNAL_ERROR`, `VALIDATION_ERROR`, `UNAUTHORIZED`, `FORBIDDEN`, `NOT_FOUND`, `CONFLICT`, `SERVICE_UNAVAILABLE`. `classify_exception` range un échec du crew dans un code ; les mots-clés de quota/indisponibilité sont partagés avec les retries de `crewquestion.py`.
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
| Frontend (Vue) | Fil de conversation, saisie, progression, historique, auth UI | `frontend/src/App.tsx`, `Studio.tsx`, `Login.tsx`, `components/*`, `components/metrics/*` (tableau de bord), `hooks/useConversation.ts`, `hooks/useMetricsSummary.ts` |
| Client Supabase | Session, token d'accès | `frontend/src/supabaseClient.ts` |
| API (Logique HTTP) | Endpoints, validation des entrées, auth, exécution en tâche de fond, persistance, vérification de livraison | `backend/main.py`, `backend/auth.py` |
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
       → /api/execute répond tout de suite ; asyncio.create_task lance _execute_crew_and_persist
            → crewquestion.py : run_dynamic_crew (tâches sélectionnées, contexte par tâche,
              guardrails, retry résumable) ; agents → tools.py / github_tools.py
            → chaque agent terminé est persisté (section + durée) dans ExecutionHistory.result
            → verify_github_delivery : branche et PR à jour confirmées via l'API GitHub
  → useConversation sonde /api/conversations/{id}/progress puis lit /messages
```

L'état frontend est local au hook `useConversation` (pas de store global).

---

## 4. Modèles de Données & État

### 4.1 État frontend

Défini dans `frontend/src/types.ts` et `hooks/useConversation.ts` : `ChatTurn` (message, statut `clarifying | running | success | failed | cancelled`, workflow, résumé de l'agent, questions, résultat), `pendingClarification`, `workflowType` (`AUTO` ou une des 4 catégories), `RepoTarget { owner, name, branch }`. Le brouillon de saisie est conservé localement et effacé à la déconnexion.

### 4.2 Contrats API

```ts
// POST /api/qualify
Request  { user_request: string; conversation_id?: number;
           has_repo_target?: boolean }  // sans repo cible, BUGFIX/FEATURE déclenchent une question
Response { summary: string; reasoning: string; is_clear: boolean;
           request_type: 'ANALYSE_ONLY' | 'BUGFIX' | 'FEATURE' | 'DESIGN_AND_DEV';
           alternative_type: <request_type> | null;
           confidence: number;            // 0-1, < 0.6 => is_clear = false
           questions: string[]; fallback: boolean }

// POST /api/execute  (répond immédiatement ; l'exécution continue en tâche de fond)
Request  { user_request: string; target_workflow: string; clarifications?: string;
           repo_owner?: string; repo_name?: string; base_branch?: string;
           conversation_id?: number }
Response { status: 'running'; id: number; conversation_id: number }
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
// Exécutions TERMINÉES de l'utilisateur sur la période (1000 au plus, les plus récentes).
Response { period_days: number; workflow: string | null;
           executions: { total, success, failed, median_duration_seconds, avg_llm_calls, avg_tokens,
                         token_executions, rate_limit_hits, wait_seconds };
           agents: { agent, label, runs, incomplete, duration_p50, duration_p95, avg_llm_calls, llm_errors,
                     token_runs, avg_prompt_tokens, avg_completion_tokens, avg_tool_calls, tool_errors }[];
           failures: { code, label, count }[];   // échecs par cause (error_code), du plus fréquent au moins fréquent
           truncated: boolean;                   // vrai si la limite de 1000 exécutions est atteinte (la période n'est pas couverte en entier)
           daily: { date, executions, failed, llm_calls, tokens }[] }

// GET /api/executions/{id}/agent-runs   → détail par agent d'UNE exécution (404 si elle n'est pas à l'utilisateur)
Response { agent, label, status: 'completed' | 'incomplete' | 'n/a', duration_seconds, llm_calls, llm_errors,
           tokens_known, prompt_tokens, completion_tokens, total_tokens, tool_calls, tool_errors }[]
GET  /api/history?limit&offset → ExecutionHistory[] · DELETE /api/history/{id}
POST /api/execute { …, resume_from_execution_id?: number }  → { status, id, conversation_id, resumed_steps: string[] }  (reprise : voir 2.9)
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
| `created_at`, `updated_at` | datetime | horodatages |

**Table `agentrun`** : une ligne par agent mesuré et par exécution — `execution_id`, `conversation_id`, `user_id` (indexés), `workflow`, `agent` (`design`, `architecture`, `diagnostic`, `development`, `qa`, `system`), `status` (`completed`, `incomplete`, `n/a`), `duration_seconds` (nullable), `llm_calls`, `llm_errors`, `usage_calls` (appels dont les tokens sont connus), `prompt_tokens`, `completion_tokens`, `total_tokens`, `tool_calls`, `tool_errors`, `created_at`. Table nouvelle : créée automatiquement, sans migration.

**Table `executioncheckpoint`** : sortie d'une étape reprenable terminée — `execution_id` (indexé), `step` (`design`, `architecture`, `diagnostic`), `raw`, `created_at`. Supprimée avec l'exécution. Table nouvelle : créée automatiquement, sans migration.

L'URL de la Pull Request n'a **pas** de colonne dédiée : elle figure dans le texte du résultat (bloc « Livraison constatée par les outils »). Le schéma `auth` est géré entièrement par Supabase. Les colonnes ajoutées après coup sont rattrapées par des migrations légères au démarrage.

---

## 5. Écrans & Navigation

### 5.1 Écrans principaux

| Écran | Composant | Condition d'affichage | Description |
|---|---|---|---|
| Connexion | `Login.tsx` | pas de session Supabase active | Formulaire email + mot de passe |
| Chargement | inline dans `App.tsx` | vérification de session en cours | Texte « Chargement... » |
| Studio | `Studio.tsx` | session active | Tableau de bord de performance (`MetricsPanel`, ouvert par le bouton « 📊 Performance » de `StudioHeader`), fil de conversation (`ChatThread`, `ChatMessage` qui affiche l'indicateur d'étapes `StepIndicator` et le résumé par agent `AgentSummary`), saisie (`ChatInput`, qui contient le choix du workflow et le formulaire du repo cible `RepoTargetFields`), historique des exécutions (`HistoryPanel` : lignes `HistoryEntryRow`, barre `HistorySelectionBar`, état de sélection `hooks/useHistorySelection.ts`), en-tête (`StudioHeader`). Chaque message terminé peut déplier sa performance par agent (`ExecutionBreakdown`) |

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
- **Tests** : suite backend `pytest` (environ 290 tests) couvrant les contrôles purs, les guardrails et les outils ; typecheck frontend `tsc --noEmit`. Aucun test de bout en bout contre le vrai Gemini ni un vrai GitHub.
- **Internationalisation** : interface et prompts entièrement en français, non paramétrable.
- **Responsive** : mise en page simple, pas de layout mobile dédié.
- **Accessibilité** : non ciblée spécifiquement (pas d'audit WCAG).

---

### 6.1 Qualité du code et intégration continue

- **CI** (`.github/workflows/ci.yml`, sur `main`, `test` et chaque pull request) : backend (`ruff check`, `mypy`, `pytest`) et frontend (`eslint`, `tsc -b`, `vitest`, build). Dépendances de développement : `backend/requirements-dev.txt`. En local, `pre-commit` (`.pre-commit-config.yaml`) lance ruff et eslint + tsc avant chaque commit.
- **Lint backend** (`backend/ruff.toml`) : erreurs réelles seulement (imports et variables inutilisés, noms indéfinis, erreurs de syntaxe, arguments mutables par défaut) ; pas de règles de style, qui réécriraient l'historique sans corriger de bug.
- **Types backend** (`backend/mypy.ini`) : vérifiés sur les modules récents et purs (`errors`, `validation`, `orphans`, `delivery`, `qa_report`, `agent_metrics`) et sur `main.py`, où seuls les codes `arg-type`, `union-attr`, `operator` et `call-overload` sont désactivés (bruit des clés primaires `Optional[int]` de SQLModel) ; attribut inexistant, variable non annotée, affectation incompatible et retour manquant y restent contrôlés. Les autres gros modules y entreront par étapes. Les outils de développement sont épinglés (`requirements-dev.txt`, dont `types-PyYAML`) et alignés sur le `rev` de ruff du pré-commit, pour qu'une nouvelle version ne casse pas la CI sans changement de code.
- **Tests frontend** (Vitest + Testing Library, `frontend/vitest.config.ts`, fichiers `*.test.ts(x)` à côté du code) : fonctions pures (échecs, tons, formats, parseurs), hooks (`useHistorySelection`, `useConnectionStatus`) et composants (`HistoryPanel` : sélection multiple, lot partiel, erreurs ; `FailureBlock`, `StepIndicator`).
- **Exécution d'une demande** (`_run_crew_and_persist`, `backend/main.py`) : une suite d'étapes nommées — `_capture_branch_sha`, `_crew_inputs` / `_repo_instructions`, `_run_crew`, `_verify_delivery`, `_persist_success`, `_persist_failure` (`_failure_detail`), `_retry_outputs_if_transient`, `_mark_startup_failure` — au lieu d'une fonction unique de plus de 400 lignes. L'état d'une tentative (SHA de référence, métriques) vit dans `_RunState`, lu par le chemin d'échec même si le crew a planté en route. Le SHA de la branche de travail est capturé pour tout run avec repository cible (`ANALYSE_ONLY` compris : les consignes données aux agents en dépendent). La persistance du succès est hors du `try` du crew : une erreur de validation après coup est retentée une fois sur une Session neuve, puis journalisée — elle ne déclare jamais en échec une exécution déjà livrée (la ligne resterait « running » jusqu'au balayage des orphelines).

---

## 7. Questions Ouvertes

- [ ] Faut-il stocker l'URL de la Pull Request générée dans une colonne dédiée de `executionhistory`, plutôt que seulement dans le texte du résultat ?
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
- [x] Questions ouvertes identifiées

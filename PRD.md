# PRD — myIACrew (Studio CrewAI)

> Version : 1.0 — Généré le 2026-09-17
> Adapté du gabarit `game-prd-creator` : ce repo n'est pas un jeu mais un orchestrateur multi-agents ; les sections ont été ajustées au produit réel.

---

## 1. Synthèse & Vision

- **Concept** : un studio web qui qualifie une demande de développement en langage naturel, puis délègue son exécution à une équipe d'agents CrewAI (game designer, architecte, développeur, QA) capable de lire/écrire du code directement sur un repository GitHub cible et d'en proposer les changements via Pull Request.
- **Genre** : outil interne d'assistance au développement (multi-agent orchestration tool), pas une application grand public.
- **Plateforme cible** : Web — frontend Vite + React 19 + TypeScript (déployé sur Vercel), backend FastAPI + CrewAI (déployé sur Render), base de données PostgreSQL (Supabase), authentification Supabase Auth.
- **Public visé** : l'équipe/le·s propriétaire·s du studio, authentifié·s (comptes créés manuellement dans le dashboard Supabase — pas d'auto-inscription).
- **Boucle principale** : décrire un besoin → qualification automatique du type de demande → choix/ajustement du workflow → exécution par les agents → récupération du résultat (texte + éventuellement une Pull Request sur le repo cible) → historisation en base.

---

## 2. Spécification fonctionnelle

### 2.1 Boucle principale (Core Loop)

1. **Connexion** : l'utilisateur se connecte avec email + mot de passe (Supabase Auth). Sans session valide, seul l'écran de connexion est accessible.
2. **Qualification** (`POST /api/qualify`) : l'utilisateur décrit son besoin dans un textarea libre. Un agent unique (`qualification_agent`, sans outils) classe la demande en une des 4 catégories et renvoie un résumé, un indicateur de clarté, et jusqu'à 4 questions de clarification. Le rapport est aussi sauvegardé dans `docs/qualification_report.md` sur le disque du backend.
3. **Renseignement du repository cible** (optionnel) : owner / repo / branche de base GitHub.
4. **Exécution** (`POST /api/execute`) : selon le workflow choisi, un sous-ensemble d'agents s'exécute séquentiellement (voir 2.2). Si un repo GitHub est fourni, le backend génère un nom de branche de travail unique (`crewai/<workflow>-<hex8>`) et l'agent développeur y committe ses changements puis ouvre une Pull Request.
5. **Restitution** : le résultat brut du crew s'affiche dans l'UI et est historisé dans la table `executionhistory` (Supabase Postgres).

### 2.2 Workflows disponibles

| Type de demande | Tâches exécutées (séquentiel) |
|---|---|
| `ANALYSE_ONLY` | `design_task` → `architecture_task` |
| `BUGFIX` | `development_task` → `qa_task` |
| `FEATURE` | `architecture_task` → `development_task` → `qa_task` |
| `DESIGN_AND_DEV` (défaut) | `design_task` → `architecture_task` → `development_task` → `qa_task` |

### 2.3 Agents CrewAI

| Agent | Rôle | Outils |
|---|---|---|
| `qualification_agent` | Qualifie le type de demande | Aucun (interdiction explicite de lire des fichiers) |
| `product_designer_agent` | Spécifications fonctionnelles (jeu ou application) | lecture disque local + lecture GitHub |
| `architect_agent` | Architecture technique React/TypeScript | lecture disque local + lecture GitHub |
| `developer_agent` | Implémentation du code | lecture/écriture disque local + lecture/écriture GitHub + branche + Pull Request |
| `qa_agent` | Revue qualité du code produit | lecture disque local + lecture GitHub |

### 2.4 Intégration GitHub (repo cible dynamique)

- L'utilisateur choisit le repository (owner/repo) et la branche de base à chaque exécution — rien n'est câblé en dur.
- Le `developer_agent` ne peut jamais écrire directement sur `main`/`master` (refus explicite côté outil `github_write_file`) : il doit créer une branche de travail avant toute écriture, puis ouvrir une Pull Request.
- Nécessite la variable d'environnement `GITHUB_TOKEN` (PAT avec accès contenu + pull requests) côté backend.

### 2.5 Authentification

- Supabase Auth (email + mot de passe). Pas d'auto-inscription côté frontend : les comptes se créent depuis le dashboard Supabase.
- Le frontend envoie le token de session Supabase en `Authorization: Bearer` sur chaque appel API.
- Le backend valide ce token à chaque requête en interrogeant `SUPABASE_URL/auth/v1/user` (`backend/auth.py`) ; sans token valide, réponse `401`.

### 2.6 Résilience LLM

- LLM utilisé : Gemini (`gemini/gemini-3.5-flash-lite` par défaut, configurable via `GEMINI_MODEL`).
- Un wrapper de retry maison (`retry_on_rate_limit_async`) retente automatiquement les appels en cas d'erreur 429/quota **et** 503/surcharge (« high demand »/« overloaded »), avec backoff exponentiel.
- `max_rpm=3` et une pause adaptative entre tâches limitent le débit d'appels au LLM.

---

## 3. Architecture Logique vs Vue

### 3.1 Séparation des responsabilités

| Couche | Rôle | Fichiers clés |
|---|---|---|
| Frontend (Vue) | Formulaire, affichage des rapports/résultats, auth UI | `frontend/src/App.tsx`, `frontend/src/Login.tsx` |
| Client Supabase | Session, token d'accès | `frontend/src/supabaseClient.ts` |
| API (Logique HTTP) | Endpoints, validation des entrées, auth, persistance | `backend/main.py`, `backend/auth.py` |
| Orchestration agents | Définition et exécution des agents/tâches CrewAI | `backend/crewquestion.py`, `backend/agentsquestion.yaml`, `backend/tasksquestion.yaml` |
| Outils agents | Actions concrètes (lecture disque, lecture/écriture GitHub) | `backend/tools.py`, `backend/github_tools.py` |
| Persistance | Modèle et accès à la base de données | `backend/database.py` |

### 3.2 Flux de données

```
Utilisateur (navigateur)
  → Login.tsx (Supabase Auth) → session + access_token
  → App.tsx (Studio) : fetch /api/qualify puis /api/execute (Authorization: Bearer <token>)
       → main.py : Depends(get_current_user) valide le token auprès de Supabase
       → crewquestion.py : construit les inputs (user_request, repo_owner, repo_name,
         base_branch, work_branch, repo_instructions) et lance le Crew CrewAI
            → agents utilisent tools.py (disque local) et/ou github_tools.py (API GitHub)
       → résultat brut renvoyé au frontend + écrit dans ExecutionHistory (Postgres/Supabase)
```

Il n'y a pas de state manager global côté frontend : `App.tsx` gère l'état via `useState` local (session, formulaire, résultats).

---

## 4. Modèles de Données & État

### 4.1 État frontend (`Studio`, dans `App.tsx`)

```ts
prompt: string                    // demande utilisateur en texte libre
loadingQualif / loadingExec: bool // états de chargement des deux étapes
report: QualificationReport | null
selectedWorkflow: string          // ANALYSE_ONLY | BUGFIX | FEATURE | DESIGN_AND_DEV
clarifications: string
executionResult: string | null
errorMessage: string | null
repoOwner / repoName / baseBranch: string  // repo GitHub cible (optionnel)
```

### 4.2 Contrats API

```ts
// POST /api/qualify
Request  { user_request: string }
Response { summary: string; is_clear: boolean;
           request_type: 'ANALYSE_ONLY' | 'BUGFIX' | 'FEATURE' | 'DESIGN_AND_DEV';
           questions: string[] }

// POST /api/execute
Request  { user_request: string; target_workflow: string; clarifications?: string;
           repo_owner?: string; repo_name?: string; base_branch?: string }
Response { status: string; id: number; workflow: string; result: string }
```

### 4.3 Persistance (Supabase Postgres, via SQLModel)

**Table `executionhistory`** (`backend/database.py`) :

| Colonne | Type | Description |
|---|---|---|
| `id` | int (PK, auto) | identifiant |
| `user_request` | text | demande initiale |
| `workflow` | text | workflow exécuté |
| `clarifications` | text (nullable) | précisions apportées par l'utilisateur |
| `result` | text | sortie brute du Crew |

Le repository/branche GitHub ciblés et l'URL de PR **ne sont pas** stockés dans des colonnes dédiées — seulement dans le texte libre de `result` si l'agent les y mentionne.

Le schéma `auth` (utilisateurs, mots de passe hashés, sessions) est géré entièrement par Supabase, hors du contrôle du code applicatif.

---

## 5. Écrans & Navigation

### 5.1 Écrans principaux

| Écran | Composant | Condition d'affichage | Description |
|---|---|---|---|
| Connexion | `Login.tsx` | pas de session Supabase active | Formulaire email + mot de passe |
| Chargement | inline dans `App.tsx` | vérification de session en cours | Texte "Chargement..." |
| Studio | `Studio` (dans `App.tsx`) | session active | Formulaire de demande → rapport de qualification → repo cible → sélection workflow → exécution → résultat |

### 5.2 Navigation

Pas de routeur : un seul écran conditionnel (`Login` vs `Studio`) piloté par l'état de session Supabase (`onAuthStateChange`).

---

## 6. Exigences Non-Fonctionnelles

- **Performance** : pas d'exigence temps réel — les exécutions d'agents peuvent prendre plusieurs dizaines de secondes à plusieurs minutes (limité par `max_rpm=3` côté LLM).
- **Sécurité** : toutes les routes API (`/api/qualify`, `/api/execute`) exigent un token Supabase valide. CORS actuellement ouvert (`allow_origins=["*"]`). Écriture GitHub jamais directe sur la branche principale.
- **Persistance** : historique des exécutions en base Postgres (Supabase) ; les fichiers markdown intermédiaires (`docs/*.md`, `tests/reports/qa_report.md`) sont écrits sur le disque **éphémère** de Render (perdus au redéploiement) sauf s'ils sont écrits via les outils GitHub sur le repo cible.
- **Internationalisation** : interface et prompts entièrement en français, non paramétrable.
- **Responsive** : mise en page simple (`maxWidth: 850px`, centrée), pas de layout mobile dédié.
- **Accessibilité** : non ciblée spécifiquement (pas d'audit WCAG).
- **Résilience** : retry automatique sur erreurs Gemini 429/quota et 503/surcharge.

---

## 7. Questions Ouvertes

- [ ] Faut-il stocker le repo/branche GitHub cible et l'URL de la PR générée comme colonnes dédiées dans `executionhistory`, plutôt que seulement dans le texte du résultat ?
- [ ] Faut-il lier chaque `ExecutionHistory` à l'utilisateur Supabase qui l'a déclenchée (actuellement anonyme une fois le token validé) ?
- [ ] Le CORS `allow_origins=["*"]` doit-il être restreint au domaine Vercel de production ?
- [ ] Les fichiers `docs/*.md` écrits sur le disque local de Render ont-ils encore une utilité maintenant que l'écriture peut se faire directement sur GitHub ?

---

## 8. Checklist du Livrable

- [x] Boucle principale et workflows documentés
- [x] Agents, outils et permissions par agent listés
- [x] Intégration GitHub (branche de travail, PR, refus d'écriture sur main) décrite
- [x] Authentification Supabase documentée
- [x] Contrats API et modèles de données/état décrits
- [x] Schéma de la table `executionhistory`
- [x] Écrans et navigation listés
- [x] Questions ouvertes identifiées

---
description: Génère un Product Requirement Document (PRD.md) complet en analysant l'architecture, la logique, les vues et les structures de données d'un code de jeu existant.
---

# Game PRD Creator

Tu es un Product Manager technique spécialisé dans les jeux de gestion. Ton rôle est de produire un PRD.md précis et exploitable à partir du code source existant.

## Étape 1 — Exploration du code

Analyse systématiquement les fichiers suivants (en parallèle si possible) :

- **Architecture générale** : `src/`, structure des dossiers, point d'entrée (`App.jsx`, `main.jsx`)
- **Modèles de données** : constantes (`src/constants/`), état initial, structures des objets (stock, tables, serveurs, cuisine, etc.)
- **Logique de jeu** : hooks (`src/hooks/`), utilitaires (`src/utils/`), boucle principale
- **Vues & UI** : composants (`src/views/`, `src/components/`), navigation, écrans principaux
- **Internationalisation** : fichiers i18n si présents
- **Sauvegarde** : localStorage, persistance d'état

## Étape 2 — Clarification (optionnelle)

Si des ambiguïtés critiques subsistent après l'exploration, pose **2 à 3 questions maximum** à l'utilisateur avant de générer le PRD. Sinon, passe directement à l'étape 3.

## Étape 3 — Génère `PRD.md` à la racine du projet

Le fichier doit suivre exactement ce gabarit :

```markdown
# PRD — [Nom du Jeu]

> Version : 1.0 — Généré le [date]

---

## 1. Synthèse & Vision

- **Concept** : [1-2 phrases décrivant le jeu]
- **Genre** : [ex. Jeu de gestion de restaurant en temps réel]
- **Plateforme cible** : [ex. Web (Vite + React)]
- **Public visé** : [ex. Joueurs casual, 15-35 ans]
- **Boucle de jeu principale** : [ex. Gérer le service, satisfaire les clients, développer le restaurant]

---

## 2. Game Design Specification

### 2.1 Boucle principale (Core Loop)
[Décrire le cycle journée/service : démarrage, phase de service, fermeture, progression]

### 2.2 Systèmes de jeu
| Système | Description | Fichiers clés |
|---------|-------------|---------------|
| [ex. Stock] | [description] | [fichiers] |
| ... | ... | ... |

### 2.3 Progression & Niveaux
[Niveaux du restaurant, déblocages, conditions de montée de niveau]

### 2.4 Événements aléatoires
[Liste des événements, fréquence, effets]

### 2.5 Économie du jeu
[Revenus, dépenses, flux de cash, prix des ingrédients]

---

## 3. Architecture Logique vs Vue

### 3.1 Séparation des responsabilités
| Couche | Rôle | Exemples de fichiers |
|--------|------|----------------------|
| Logique pure | Calculs, transformations, pas de React | `src/utils/`, `src/constants/` |
| Hooks | État + effets, orchestration | `src/hooks/` |
| Vues | Rendu UI, interactions utilisateur | `src/views/`, `src/components/` |

### 3.2 Flux de données
[Décrire comment l'état circule : état global → hooks → composants]

---

## 4. Modèles de Données & Game State

### 4.1 État global
[Pour chaque slice d'état : nom, type, description, valeur initiale]

```js
// Exemple de structure
{
  stock: [...],   // Array<StockItem>
  cash: 0,        // number
  ...
}
```

### 4.2 Structures de données clés
[Décrire chaque type d'objet important : StockItem, Table, Server, Dish, etc.]

---

## 5. Écrans & Navigation UI

### 5.1 Écrans principaux
| Écran | Route/Tab | Composant | Description |
|-------|-----------|-----------|-------------|
| [ex. Stock] | `/stock` | `StockView` | Gestion des matières premières |
| ... | ... | ... | ... |

### 5.2 Composants partagés
[Liste des composants réutilisables avec leur rôle]

---

## 6. Exigences Non-Fonctionnelles

- **Performance** : [ex. 60 FPS, pas de re-renders inutiles]
- **Persistance** : [ex. localStorage, clés utilisées]
- **Internationalisation** : [ex. fr/en, fichiers i18n]
- **Responsive** : [ex. desktop uniquement, mobile prévu]
- **Accessibilité** : [ex. non ciblée / WCAG AA]

---

## 7. Questions Ouvertes

- [ ] [Question 1 sur un point ambigu du design]
- [ ] [Question 2 sur une fonctionnalité future envisagée]

---

## 8. Checklist du Livrable

- [ ] Tous les systèmes de jeu documentés
- [ ] Modèles de données complets avec types
- [ ] Flux de données décrit (state → hooks → UI)
- [ ] Écrans et navigation listés
- [ ] Événements aléatoires inventoriés
- [ ] Économie du jeu chiffrée
- [ ] Questions ouvertes identifiées
```

Écris le contenu réel du PRD en remplaçant tous les placeholders par les informations extraites du code. Le PRD doit être directement utilisable par un développeur ou un designer sans avoir à lire le code source.

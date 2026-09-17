---
description: Review the current diff for bugs before pushing to remote. Claude must invoke this skill BEFORE any git push. Runs /code-review, presents findings, then pushes only after user confirms.
---

## Tâche

Revue automatique du code avant poussée. Suis ces étapes dans l'ordre.

### Étape 1 — Récupère le diff

!`git diff @{upstream}...HEAD 2>/dev/null || git diff main...HEAD 2>/dev/null || git diff HEAD~1 2>/dev/null`

Branche courante :

!`git branch --show-current`

### Étape 2 — Lance /code-review

Exécute le skill `/code-review` sur le diff ci-dessus (high effort, tous les angles).

### Étape 3 — Décision

**Si le tableau de findings est non vide** :
- Affiche chaque finding avec fichier, ligne, résumé et scénario d'échec
- Demande à l'utilisateur : « Des problèmes ont été détectés (voir ci-dessus). Voulez-vous quand même pousser ? (oui / non) »
- Si **non** → arrête ici, ne pousse pas
- Si **oui** → passe à l'étape 4

**Si le tableau est vide `[]`** :
- Affiche « ✓ Aucun problème détecté. »
- Passe directement à l'étape 4

### Étape 4 — Pousse le code

Exécute cette commande unique (la variable `CLAUDE_PUSH_REVIEWED=1` sert de jeton d'autorisation reconnu par le hook) :

`CLAUDE_PUSH_REVIEWED=1 git push -u origin $(git branch --show-current)`

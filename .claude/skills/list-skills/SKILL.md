---
description: Liste l'ensemble des skills Claude Code disponibles (globaux et locaux) avec leur description.
---

# Skill: /list-skills

Analyse les répertoires de skills Claude Code et affiche la liste exhaustive des skills disponibles avec leurs descriptions.

## Instructions

1. Parcours les répertoires de skills suivants :
   - `~/.claude/skills/` (Skills globaux)
   - `.claude/skills/` (Skills du projet courant)

2. Pour chaque fichier `SKILL.md` trouvé :
   - Extrais `name` et `description` depuis le frontmatter YAML.
   - Si le frontmatter est absent, utilise le nom du dossier et la première ligne descriptive du fichier.

3. Génère un tableau récapitulatif :

| Commande | Portée | Description |
|----------|--------|-------------|
| `/nom-du-skill` | Global `~` ou Projet | Description extraite |

4. Trie le tableau : skills globaux en premier, puis skills projet, chaque groupe par ordre alphabétique.

5. Si un répertoire n'existe pas, indique simplement « Aucun skill trouvé » pour cette portée.

---
name: react-game-animator
description: À utiliser lors de la conception d'animations d'interface, de transitions de menus, d'effets visuels au survol, ou de l'affichage de textes flottants (ex: +100 Or).
---

# Expert Animations & Game Feel React

Tu es un développeur Front-End spécialisé dans les animations web interactives et fluides à 60 images par seconde.

## 1. Optimisation des Animations (Hardware Acceleration)
Pour éviter de faire ramer le thread principal de React, applique ces règles strictes :
- **Transitions CSS :** Privilégie les transitions CSS pures gérées par le GPU. Utilise uniquement les propriétés hautement optimisées : `transform` (pour les déplacements, zooms, rotations) et `opacity`.
- **Interdiction des animations sur les propriétés de layout :** N'anime JAMAIS des propriétés comme `width`, `height`, `top` ou `left`, car elles forcent le navigateur à recalculer toute la mise en page (Reflow), ce qui fait saccader le jeu.
- **Exemple pour une Jauge :** Pour animer une barre de progression, fixe sa largeur à 100% et anime sa propriété `transform: scaleX(progression)` (avec `transform-origin: left`).

## 2. Effets Spécifiques aux Jeux (Popups de texte)
- Pour les effets de texte flottant (ex: un "+10 Gold" qui monte et disparaît au clic), conçois des micro-composants éphémères qui s'auto-détruisent (`setTimeout` avec nettoyage dans `useEffect`) après la fin de leur animation CSS.
- **`setTimeout` est ici légitime** : il s'agit d'un effet UI ponctuel, pas de logique économique. Cette règle ne contredit pas `game-engine-js` qui restreint `setInterval` haute fréquence pour les calculs de jeu.

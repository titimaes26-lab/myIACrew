---
name: react-game-layout
description: À utiliser pour concevoir la structure globale des écrans (menus, HUD, panneaux latéraux), la navigation du jeu, et s'assurer que l'interface est responsive (mobile/tablette/PC).
---

# Expert Layout & Navigation Responsive

Tu es un architecte d'interface spécialisé dans l'adaptabilité multi-écran et les architectures de navigation d'applications de jeu web et mobiles.

## 1. Principes d'Architecture Globale (HUD & Écrans)
- **HUD Fixe :** La barre supérieure (ressources, options, profil) doit rester figée au sommet de l'écran (`position: fixed` ou `sticky`, `top-0`, `z-50`). Elle ne doit jamais scroller avec le reste du jeu.
- **Navigation par State :** N'utilise pas de routeur lourd (comme React Router). Utilise un gestionnaire d'état textuel simple (`currentScreen`).
- **Zone de Contenu Dynamique :** Le reste de l'espace vertical disponible en dessous du HUD doit être calculé proprement via Flexbox (`flex-1`) ou des unités de viewport pour éviter les débordements (overflow) indésirables.

## 2. Responsivité & Ergonomie Tactile (Mobile/Tablette)
- **Taille des Cibles Tactiles :** Tous les boutons interactifs, onglets et éléments cliquables doivent avoir une zone d'interaction minimale de **44x44 pixels** pour garantir le confort sur écran tactile (Android).
- **Flexbox & Grid :** Privilégie l'utilisation de CSS Grid pour les grilles de boutons ou d'inventaires, et Flexbox pour l'alignement des éléments du HUD. Use des classes de media-queries (ex: `md:`, `lg:`) pour ajuster le nombre de colonnes selon la taille de l'écran.

---

## 3. Structure de Référence (Template à appliquer)

Lorsque tu crées ou modifies la structure principale du jeu, base-toi systématiquement sur ce modèle d'architecture responsive en JavaScript :

```javascript
import React, { useState } from 'react';
// Adapte le chemin relatif selon la position du fichier dans src/
// ex: '../constants/gameData.js' depuis src/components/
//     '../../constants/gameData.js' depuis src/views/monEcran/
import { Z } from '../constants/gameData.js'; // token z-index centralisés

// 1. Structure Générique du HUD Supérieur
// Utilise position:sticky dans un flex-column — pas de position:fixed
// pour éviter les offsets manuels fragiles.
const HUD = ({ currentScreen, setScreen, gold, gems }) => {
  return (
    <div style={{
      position: 'sticky',
      top: 0,
      width: '100%',
      flexShrink: 0,
      height: '60px',
      backgroundColor: '#1a1a1a',
      color: '#fff',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'space-between',
      padding: '0 16px',
      zIndex: Z.header,
      borderBottom: '2px solid #333'
    }}>
      {/* Section Ressources — valeurs passées en props, pas de DOM impératif */}
      <div style={{ display: 'flex', gap: '16px' }}>
        <div>💰 {gold}</div>
        <div>💎 {gems}</div>
      </div>
      
      {/* Section Navigation épurée */}
      <div style={{ display: 'flex', gap: '8px' }}>
        <button 
          onClick={() => setScreen('GAME')}
          style={{ minWidth: '44px', minHeight: '44px', padding: '0 12px', cursor: 'pointer' }}
        >
          Jeu
        </button>
        <button 
          onClick={() => setScreen('UPGRADES')}
          style={{ minWidth: '44px', minHeight: '44px', padding: '0 12px', cursor: 'pointer' }}
        >
          Améliorations
        </button>
      </div>
    </div>
  );
};

// 2. Composant Principal (Layout Engine)
// gold/gems ne vivent pas en local — reçois-les depuis le state global
// (props, context, ou hook dédié) pour éviter un HUD figé à 0.
export default function GameLayout({ gold = 0, gems = 0 }) {
  const [currentScreen, setCurrentScreen] = useState('MAIN_MENU');

  // Rendu conditionnel des écrans
  const renderScreen = () => {
    switch(currentScreen) {
      case 'MAIN_MENU':
        return (
          <div className="screen-menu" style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '16px', paddingTop: '40px' }}>
            <h1>Menu Principal</h1>
            <button
              onClick={() => setCurrentScreen('GAME')}
              style={{ minWidth: '44px', minHeight: '44px', padding: '12px 32px', fontSize: '16px', cursor: 'pointer' }}
            >
              Jouer
            </button>
          </div>
        );
      case 'GAME':
        return <div className="screen-game">Zone de Gestion (Tables, Clients)</div>;
      case 'UPGRADES':
        return <div className="screen-upgrades">Magasin / Améliorations</div>;
      default:
        return <div>Écran inconnu</div>;
    }
  };

  return (
    // height:100dvh (pas 100vh) — 100dvh exclut la barre d'adresse mobile
    <div style={{ 
      display: 'flex', 
      flexDirection: 'column', 
      height: '100dvh',
      overflow: 'hidden',
      backgroundColor: '#121212',
      color: '#ffffff',
      fontFamily: 'sans-serif'
    }}>
      {/* Le HUD n'apparaît pas sur le menu principal */}
      {currentScreen !== 'MAIN_MENU' && (
        <HUD
          currentScreen={currentScreen}
          setScreen={setCurrentScreen}
          gold={gold}
          gems={gems}
        />
      )}
      
      {/* Zone de contenu scrollable — flex:1 absorbe l'espace restant */}
      <main style={{ 
        flex: 1,
        overflowY: 'auto',
        padding: '16px',
        display: 'flex',
        flexDirection: 'column'
      }}>
        {renderScreen()}
      </main>
    </div>
  );
}
```

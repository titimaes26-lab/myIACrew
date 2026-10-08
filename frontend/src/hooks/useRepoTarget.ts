import { useCallback, useEffect, useRef, useState } from 'react';
import { apiClient } from '../api';
import type { RepoTarget, RepoTargetSuggestion } from '../types';

// Repository GitHub cible de la zone de saisie : champs, panneau replié ou ouvert, suggestions récentes et
// préremplissage automatique (une seule fois par montage).
export function useRepoTarget(apiUrl: string, accessToken: string) {
  const [showRepoFields, setShowRepoFields] = useState(false);
  const [repoOwner, setRepoOwner] = useState('');
  const [repoName, setRepoName] = useState('');
  const [baseBranch, setBaseBranch] = useState('main');
  const [repoSuggestions, setRepoSuggestions] = useState<RepoTargetSuggestion[]>([]);
  // Garde à usage unique pour le préremplissage automatique du repo cible ci-dessous : sans
  // elle, un rafraîchissement du token Supabase (accessToken change, autoRefreshToken par
  // défaut — voir supabaseClient.ts) redéclencherait l'effet de fetch (dépendance
  // [apiUrl, accessToken]) et donc le préremplissage, alors qu'aucune nouvelle conversation n'a
  // commencé : la suggestion la plus récente serait réappliquée par-dessus des champs que
  // l'utilisateur a depuis modifiés à la main (ex: repoBranch laissé à sa valeur par défaut
  // 'main', indiscernable de "jamais touché" — voir plus bas), et le panneau se rouvrirait même
  // si l'utilisateur l'avait explicitement refermé entretemps.
  const repoPrefillAppliedRef = useRef(false);
  // Vrai dès que l'utilisateur interagit lui-même avec la zone repo (ouvre/ferme le panneau, ou
  // tape dans un des 3 champs) AVANT que le préremplissage ci-dessous ait eu l'occasion de
  // s'appliquer : dans ce cas, aucune suggestion n'est appliquée du tout (voir plus bas), plutôt
  // que de fusionner par champ ("current || suggestion") — une fusion pourrait sinon marier
  // owner tapé par l'utilisateur avec name/branch d'une suggestion sans rapport si le fetch
  // résout pendant qu'il tape, ou rouvrir un panneau qu'il vient de refermer explicitement.
  const repoUiTouchedRef = useRef(false);

  useEffect(() => {
    let cancelled = false;
    apiClient(apiUrl, accessToken)
      .listRepoTargets()
      .then((data) => {
        if (cancelled) return;
        setRepoSuggestions(data);
        // Une seule fois par montage (voir repoPrefillAppliedRef ci-dessus), qu'il y ait ou non
        // une suggestion disponible cette fois-ci : marquer la tentative comme faite même sur
        // une liste vide évite qu'un rafraîchissement de token une heure plus tard, une fois
        // qu'une cible existe enfin, ne déclenche alors un préremplissage tardif et surprenant
        // en pleine conversation.
        if (repoPrefillAppliedRef.current) return;
        repoPrefillAppliedRef.current = true;
        // Pré-remplit avec la cible la plus récente (data[0], déjà triée ainsi côté backend —
        // voir /api/repo-targets) plutôt que de repartir sur des champs vides à chaque nouvelle
        // conversation : la même cible GitHub est en pratique réutilisée d'une conversation à
        // l'autre bien plus souvent qu'elle ne change. Tout ou rien (repoUiTouchedRef) plutôt
        // qu'un remplissage champ par champ : voir sa définition ci-dessus. Ouvre aussi le
        // panneau, sinon ce préremplissage resterait invisible derrière le bouton replié.
        if (data.length > 0 && !repoUiTouchedRef.current) {
          const [mostRecent] = data;
          setRepoOwner(mostRecent.repo_owner);
          setRepoName(mostRecent.repo_name);
          setBaseBranch(mostRecent.base_branch || 'main');
          setShowRepoFields(true);
        }
      })
      .catch(() => {
        // Suggestions optionnelles : un échec de chargement ne doit pas bloquer la saisie. Marque
        // quand même la tentative de préremplissage comme faite (voir repoPrefillAppliedRef) :
        // sans ça, un échec réseau transitoire à cette toute première tentative laisserait la
        // porte ouverte à un réessai plus tard déclenché par un rafraîchissement de token (mêmes
        // dépendances d'effet), qui prérempli(rait) alors les champs en pleine conversation —
        // exactement ce que cette garde à usage unique est censée empêcher.
        if (!cancelled) repoPrefillAppliedRef.current = true;
      });
    return () => {
      cancelled = true;
    };
  }, [apiUrl, accessToken]);

  const applySuggestion = (suggestion: RepoTargetSuggestion) => {
    repoUiTouchedRef.current = true;
    setRepoOwner(suggestion.repo_owner);
    setRepoName(suggestion.repo_name);
    setBaseBranch(suggestion.base_branch || 'main');
  };

  // useCallback (pas de simples fonctions inline) : ces 3 handlers sont transmis à
  // RepoTargetFields, un composant enfant qui pourrait sinon être mémoïsé — sans ça, chaque
  // frappe dans la zone de message (text, un state local à CE composant) recréerait de
  // nouvelles références à chaque rendu même si rien ne change côté repo. Toutes deux marquent
  // repoUiTouchedRef au passage, sans quoi taper dans un champ pendant que le fetch de
  // préremplissage est encore en vol ne serait pas distingué d'un champ resté à sa valeur par
  // défaut (voir la définition de ce ref plus haut).
  const handleRepoOwnerChange = useCallback((value: string) => {
    repoUiTouchedRef.current = true;
    setRepoOwner(value);
  }, []);
  const handleRepoNameChange = useCallback((value: string) => {
    repoUiTouchedRef.current = true;
    setRepoName(value);
  }, []);
  const handleBaseBranchChange = useCallback((value: string) => {
    repoUiTouchedRef.current = true;
    setBaseBranch(value);
  }, []);

  // Ouvre ou ferme le panneau : compte comme une interaction de l'utilisateur (voir repoUiTouchedRef).
  const togglePanel = () => {
    repoUiTouchedRef.current = true;
    setShowRepoFields((v) => !v);
  };

  // Replie le panneau (après chaque envoi) sans toucher aux valeurs saisies.
  const collapsePanel = () => setShowRepoFields(false);

  const currentTarget = (): RepoTarget => ({ owner: repoOwner.trim(), name: repoName.trim(), branch: baseBranch.trim() });

  return {
    showRepoFields, repoOwner, repoName, baseBranch, repoSuggestions, currentTarget, applySuggestion,
    panel: { toggle: togglePanel, collapse: collapsePanel },
    onChange: { owner: handleRepoOwnerChange, name: handleRepoNameChange, branch: handleBaseBranchChange },
  };
}

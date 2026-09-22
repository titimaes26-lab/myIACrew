import { useCallback, useEffect, useRef, useState, type KeyboardEvent } from 'react';
import { apiClient } from '../api';
import type { RepoTarget, RepoTargetSuggestion } from '../types';
import RepoTargetFields from './RepoTargetFields';
import { readChatDraft, writeChatDraft } from '../utils/chatDraft';
import { WORKFLOW_TYPE_OPTIONS, type WorkflowType } from '../constants/workflowTypes';

const MIN_TEXTAREA_HEIGHT = 52;
const MAX_TEXTAREA_HEIGHT = 200;

// Partagé entre les deux repères de séquence ("① Contexte" / "② Votre message") pour qu'ils ne
// puissent pas diverger visuellement si l'un est retouché sans l'autre. Appliqué à des <h3> (pas
// de simples <p>) : un lecteur d'écran qui navigue ce formulaire par titres (touche H) doit
// pouvoir s'arrêter sur ces deux repères comme il le ferait pour n'importe quel autre titre de
// section, index.css ne stylant que h1/h2 donc sans effet de cascade indésirable à neutraliser
// ici pour h3.
const SECTION_LABEL_STYLE = { margin: 0, fontSize: '11px', fontWeight: 600, color: '#999', textTransform: 'uppercase' as const, letterSpacing: '0.04em' };

interface ChatInputProps {
  disabled: boolean;
  // Distinct de `disabled` dans l'API de ce composant (même si Studio.tsx leur passe
  // actuellement la même valeur, `busy`) : onCancel (cancelSending, useConversation.ts) marque
  // localement tout tour "running" comme "cancelled", y compris un tour repris depuis
  // l'Historique dont l'exécution tourne encore côté serveur sans que CETTE session l'ait
  // elle-même envoyée — cela n'arrête PAS réellement l'exécution serveur (son résultat, une fois
  // prêt, reste consultable en rechargeant la conversation), seulement le suivi local affiché.
  // Cancellable reste donc un knob distinct pour un appelant qui voudrait un jour afficher un
  // simple indicateur d'attente sans bouton "Annuler" fonctionnel.
  cancellable: boolean;
  onCancel: () => void;
  apiUrl: string;
  accessToken: string;
  onSend: (text: string, repoTarget: RepoTarget) => void;
  // Contrôlé par useConversation (pas un état local à ce composant) : ce hook a besoin de
  // pouvoir remettre ce choix à 'AUTO' à des limites que ce composant ne connaît pas
  // (nouvelle conversation, reprise d'une conversation différente) sans quoi un choix
  // manuel resterait actif en silence pour une demande sans rapport avec celle où il avait
  // été fait.
  workflowType: WorkflowType;
  onWorkflowTypeChange: (workflowType: WorkflowType) => void;
  // "Relancer cette demande" (ChatMessage, via Studio) : préremplit la zone de saisie avec le
  // texte d'un tour en échec. `nonce` change à chaque clic (même si le texte relancé est
  // identique au tour précédent) pour que l'effet ci-dessous se redéclenche à coup sûr — un
  // objet figé sur le seul `text` ne le ferait pas si l'utilisateur relance deux fois de
  // suite exactement la même demande sans rien taper entretemps.
  retryDraft: { text: string; nonce: number } | null;
}

// Studio.tsx monte ce composant avec `key={conversationResetSignal}` : un changement RÉEL de
// conversation (nouvelle, ou reprise d'une différente — voir la définition de ce signal dans
// useConversation.ts) démonte et remonte entièrement ce composant plutôt que de laisser son
// state local (repo cible préremplis compris) attaché en silence à une conversation sans
// rapport. Ce remontage réinitialise tout naturellement ET refait tourner l'effet de
// préremplissage ci-dessous depuis un état neuf, sans nécessiter de second mécanisme de
// synchronisation manuel en plus de useState/useEffect standards.
export default function ChatInput({ disabled, cancellable, onCancel, apiUrl, accessToken, onSend, workflowType, onWorkflowTypeChange, retryDraft }: ChatInputProps) {
  const [text, setText] = useState(readChatDraft);
  const [showRepoFields, setShowRepoFields] = useState(false);
  const [repoOwner, setRepoOwner] = useState('');
  const [repoName, setRepoName] = useState('');
  const [baseBranch, setBaseBranch] = useState('main');
  const [repoSuggestions, setRepoSuggestions] = useState<RepoTargetSuggestion[]>([]);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  // Dernier `nonce` de retryDraft déjà appliqué à `text` : comparé pendant le rendu (pas dans
  // un effet, voir juste plus bas) pour détecter un NOUVEAU clic sur "Relancer" — y compris
  // un second clic sur le même tour, qui renvoie un texte identique mais un nonce différent.
  const [syncedRetryNonce, setSyncedRetryNonce] = useState(retryDraft?.nonce);
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

  // Synchronisation pendant le rendu plutôt que dans un effet ("Adjusting some state when a
  // prop changes" — react.dev) : évite un rendu supplémentaire (effet -> setState -> nouveau
  // rendu) pour un simple recopiage de prop vers l'état local.
  if (retryDraft && retryDraft.nonce !== syncedRetryNonce) {
    setSyncedRetryNonce(retryDraft.nonce);
    setText(retryDraft.text);
  }

  useEffect(() => {
    writeChatDraft(text);
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, MAX_TEXTAREA_HEIGHT)}px`;
  }, [text]);

  // Focus : un vrai effet de bord sur le DOM (pas une synchronisation d'état), qui reste donc
  // ici plutôt que dans le bloc de rendu ci-dessus.
  useEffect(() => {
    if (retryDraft?.nonce !== undefined) textareaRef.current?.focus();
  }, [retryDraft?.nonce]);

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

  const submit = () => {
    if (!text.trim() || disabled) return;
    onSend(text, { owner: repoOwner.trim(), name: repoName.trim(), branch: baseBranch.trim() });
    setText('');
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  };

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

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
      {/* Regroupe le type de demande et le repo cible sous un même repère visuel "① Contexte",
          distinct du "② Votre message" plus bas (séparés par une ligne) : rend explicite la
          séquence déjà suivie par l'ordre du DOM (configurer le contexte avant d'écrire le
          message) plutôt que de laisser ces deux zones se lire comme des blocs sans rapport. */}
      <h3 style={SECTION_LABEL_STYLE}>
        ① Contexte de la demande
      </h3>
      <div style={{ display: 'flex', gap: '8px', alignItems: 'center', flexWrap: 'wrap' }}>
        <label style={{ fontSize: '13px', color: '#666', display: 'flex', alignItems: 'center', gap: '6px' }}>
          Type de demande :
          <select
            value={workflowType}
            onChange={(e) => onWorkflowTypeChange(e.target.value as WorkflowType)}
            disabled={disabled}
            // Ce choix persiste d'un message à l'autre (jamais réinitialisé à 'AUTO' après
            // l'envoi) pour permettre plusieurs messages de suite dans un même mode manuel
            // sans avoir à le re-sélectionner à chaque fois. Un style distinct quand il
            // n'est pas 'AUTO' est donc nécessaire : sans lui, un choix manuel oublié depuis
            // un message précédent resterait actif en silence pour une demande sans rapport,
            // qui contournerait alors /api/qualify sans que rien ne le signale à l'écran.
            style={{
              padding: '4px 8px',
              fontSize: '13px',
              borderRadius: '6px',
              border: workflowType === 'AUTO' ? '1px solid #ccc' : '1px solid #0070f3',
              backgroundColor: workflowType === 'AUTO' ? '#fff' : '#eff6ff',
              color: workflowType === 'AUTO' ? '#333' : '#0070f3',
              fontWeight: workflowType === 'AUTO' ? 'normal' : 600,
              cursor: disabled ? 'not-allowed' : 'pointer',
            }}
          >
            {WORKFLOW_TYPE_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.icon} {opt.label}
              </option>
            ))}
          </select>
        </label>

        <button
          type="button"
          onClick={() => {
            repoUiTouchedRef.current = true;
            setShowRepoFields((v) => !v);
          }}
          style={{ padding: '4px 10px', fontSize: '13px', backgroundColor: 'transparent', border: '1px solid #ccc', borderRadius: '6px', cursor: 'pointer', color: '#666' }}
        >
          🔗 {repoOwner && repoName ? `${repoOwner}/${repoName}` : 'Repository GitHub cible (optionnel)'}
        </button>
      </div>

      {showRepoFields && (
        <>
          {repoSuggestions.length > 0 && (
            <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap' }}>
              {repoSuggestions.map((s) => (
                <button
                  key={`${s.repo_owner}/${s.repo_name}@${s.base_branch}`}
                  type="button"
                  onClick={() => applySuggestion(s)}
                  disabled={disabled}
                  style={{ fontSize: '12px', padding: '4px 10px', borderRadius: '999px', border: '1px solid #ccc', backgroundColor: '#f3f4f6', color: '#333', cursor: disabled ? 'not-allowed' : 'pointer' }}
                >
                  {s.repo_owner}/{s.repo_name}{s.base_branch ? `@${s.base_branch}` : ''}
                </button>
              ))}
            </div>
          )}
          <RepoTargetFields
            repoOwner={repoOwner}
            repoName={repoName}
            baseBranch={baseBranch}
            onRepoOwnerChange={handleRepoOwnerChange}
            onRepoNameChange={handleRepoNameChange}
            onBaseBranchChange={handleBaseBranchChange}
            disabled={disabled}
          />
        </>
      )}

      <div style={{ borderTop: '1px solid #eee', margin: '2px 0' }} />
      <h3 style={SECTION_LABEL_STYLE}>
        ② Votre message
      </h3>
      <div style={{ display: 'flex', gap: '10px' }}>
        <textarea
          ref={textareaRef}
          disabled={disabled}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Décrivez votre besoin ou répondez à l'agent..."
          aria-label="Votre message"
          style={{
            flex: 1,
            padding: '12px',
            borderRadius: '6px',
            border: '1px solid #ccc',
            fontSize: '15px',
            boxSizing: 'border-box',
            resize: 'none',
            overflowY: 'auto',
            minHeight: `${MIN_TEXTAREA_HEIGHT}px`,
            maxHeight: `${MAX_TEXTAREA_HEIGHT}px`,
            fontFamily: 'inherit',
          }}
        />
        {disabled && cancellable ? (
          <button
            type="button"
            onClick={onCancel}
            style={{ padding: '0 20px', backgroundColor: '#fff', color: '#991b1b', border: '1px solid #fca5a5', borderRadius: '6px', cursor: 'pointer', fontWeight: 'bold' }}
          >
            🚫 Annuler
          </button>
        ) : disabled ? (
          // Rien à annuler dans ce cas (voir cancellable ci-dessus) : un bouton visuellement
          // désactivé plutôt que le "Annuler" fonctionnel ci-dessus, pour ne pas laisser croire
          // qu'un clic aurait un effet.
          <button
            type="button"
            disabled
            style={{ padding: '0 20px', backgroundColor: '#f3f4f6', color: '#666', border: '1px solid #ccc', borderRadius: '6px', cursor: 'not-allowed', fontWeight: 'bold' }}
          >
            🔄 En attente...
          </button>
        ) : (
          <button
            type="button"
            onClick={submit}
            disabled={!text.trim()}
            style={{ padding: '0 20px', backgroundColor: '#0070f3', color: '#fff', border: 'none', borderRadius: '6px', cursor: 'pointer', fontWeight: 'bold' }}
          >
            Envoyer
          </button>
        )}
      </div>
    </div>
  );
}

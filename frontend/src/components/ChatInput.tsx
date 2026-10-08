import { useEffect, useRef, useState, type KeyboardEvent } from 'react';
import Button from './ui/Button';
import type { RepoTarget } from '../types';
import ChatComposer from './ChatComposer';
import { MAX_TEXTAREA_HEIGHT } from '../constants/composer';
import RepoTargetPanel from './RepoTargetPanel';
import WorkflowTypeSelect from './WorkflowTypeSelect';
import { useRepoTarget } from '../hooks/useRepoTarget';
import { readChatDraft, writeChatDraft } from '../utils/chatDraft';
import type { WorkflowType } from '../constants/workflowTypes';


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
  const repo = useRepoTarget(apiUrl, accessToken);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  // Dernier `nonce` de retryDraft déjà appliqué à `text` : comparé pendant le rendu (pas dans
  // un effet, voir juste plus bas) pour détecter un NOUVEAU clic sur "Relancer" — y compris
  // un second clic sur le même tour, qui renvoie un texte identique mais un nonce différent.
  const [syncedRetryNonce, setSyncedRetryNonce] = useState(retryDraft?.nonce);

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

  const submit = () => {
    if (!text.trim() || disabled) return;
    onSend(text, repo.currentTarget());
    setText('');
    // Replie le panneau après CHAQUE envoi, y compris dans la même conversation (sur demande
    // explicite) : ne réinitialise PAS repoOwner/repoName/baseBranch (le bouton replié continue
    // d'afficher "owner/repo" sélectionné, voir plus bas), donc rouvrir le panneau pour un
    // message suivant retrouve les mêmes valeurs sans avoir à les ressaisir.
    repo.panel.collapse();
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    // Entrée qui VALIDE un mot en cours de saisie assistée (clavier virtuel, autocorrection, saisie japonaise ou chinoise)
    // n'envoie rien : `isComposing`, et le code 229 que Chrome Android renvoie pendant une composition.
    if (e.nativeEvent.isComposing || e.keyCode === 229) return;
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  };

  return (
    <div className="stack">
      {/* Regroupe le type de demande et le repo cible sous un même repère visuel "① Contexte",
          distinct du "② Votre message" plus bas (séparés par une ligne) : rend explicite la
          séquence déjà suivie par l'ordre du DOM (configurer le contexte avant d'écrire le
          message) plutôt que de laisser ces deux zones se lire comme des blocs sans rapport. */}
      <h3 className="section-label">
        ① Contexte de la demande
      </h3>
      <div className="row">
        <WorkflowTypeSelect workflowType={workflowType} onWorkflowTypeChange={onWorkflowTypeChange} disabled={disabled} />

        <Button
          variant="ghost"
          size="sm"
          aria-expanded={repo.showRepoFields}
          onClick={repo.panel.toggle}
        >
          🔗 {repo.repoOwner && repo.repoName ? `${repo.repoOwner}/${repo.repoName}` : 'Repository GitHub cible (optionnel)'}
        </Button>
      </div>

      {repo.showRepoFields && (
        <RepoTargetPanel
          repoOwner={repo.repoOwner}
          repoName={repo.repoName}
          baseBranch={repo.baseBranch}
          suggestions={repo.repoSuggestions}
          onApplySuggestion={repo.applySuggestion}
          onRepoOwnerChange={repo.onChange.owner}
          onRepoNameChange={repo.onChange.name}
          onBaseBranchChange={repo.onChange.branch}
          disabled={disabled}
        />
      )}

      <hr className="divider" />
      <h3 className="section-label">
        ② Votre message
      </h3>
      <ChatComposer
        text={text}
        onTextChange={setText}
        onKeyDown={handleKeyDown}
        textareaRef={textareaRef}
        disabled={disabled}
        cancellable={cancellable}
        onCancel={onCancel}
        onSubmit={submit}
      />
    </div>
  );
}

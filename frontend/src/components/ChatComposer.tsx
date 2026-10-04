import type { KeyboardEvent, RefObject } from 'react';
import Button from './ui/Button';
import { MAX_TEXTAREA_HEIGHT, MIN_TEXTAREA_HEIGHT } from '../constants/composer';

interface ChatComposerProps {
  text: string;
  onTextChange: (text: string) => void;
  onKeyDown: (e: KeyboardEvent<HTMLTextAreaElement>) => void;
  textareaRef: RefObject<HTMLTextAreaElement | null>;
  disabled: boolean;
  cancellable: boolean;
  onCancel: () => void;
  onSubmit: () => void;
}

// Zone de message : champ de texte, et selon l'état le bouton Envoyer, Annuler ou « En attente ».
export default function ChatComposer({ text, onTextChange, onKeyDown, textareaRef, disabled, cancellable, onCancel, onSubmit }: ChatComposerProps) {
  return (
    <div className="composer">
      {/* Masquée (pas seulement désactivée) pendant une exécution en cours : sur demande
          explicite, pour laisser toute la place au bouton "Annuler" plutôt que d'afficher une
          zone de texte grisée et inutilisable à côté. `text` reste en state local le temps du
          masquage (pas de perte du brouillon en cours de saisie au moment de l'envoi, déjà vidé
          par submit() de toute façon), donc la zone retrouve son contenu éventuel dès que
          disabled redevient faux. */}
      {!disabled && (
        <textarea
          ref={textareaRef}
          disabled={disabled}
          value={text}
          onChange={(e) => onTextChange(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder="Décrivez votre besoin ou répondez à l'agent..."
          aria-label="Votre message"
          className="field"
          style={{ minHeight: `${MIN_TEXTAREA_HEIGHT}px`, maxHeight: `${MAX_TEXTAREA_HEIGHT}px` }}
        />
      )}
      {disabled && cancellable ? (
        <Button variant="danger" block onClick={onCancel}>
          🚫 Annuler
        </Button>
      ) : disabled ? (
        // Rien à annuler dans ce cas (voir cancellable ci-dessus) : un bouton visuellement
        // désactivé plutôt que le "Annuler" fonctionnel ci-dessus, pour ne pas laisser croire
        // qu'un clic aurait un effet.
        <Button block disabled>
          🔄 En attente...
        </Button>
      ) : (
        <Button variant="primary" block onClick={onSubmit} disabled={!text.trim()}>
          Envoyer
        </Button>
      )}
    </div>
  );
}

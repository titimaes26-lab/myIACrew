import { useEffect, useMemo, useRef } from 'react';
import type { ChatTurn } from '../types';
import ChatMessage from './ChatMessage';

interface ChatThreadProps {
  turns: ChatTurn[];
  onRetry?: (turn: ChatTurn) => void;
  // Nom volontairement pas "sending" : recouvre aussi le cas où une clarification est en
  // attente de réponse (voir Studio.tsx), pas seulement un envoi réseau en cours.
  retryDisabled: boolean;
}

// Au-delà de cette marge (en pixels) sous le viewport, on considère que l'utilisateur a
// délibérément remonté lire l'historique plutôt que de simplement suivre la conversation.
const NEAR_BOTTOM_THRESHOLD_PX = 150;

export default function ChatThread({ turns, onRetry, retryDisabled }: ChatThreadProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  // Vrai tant que l'utilisateur est resté "collé" au bas du fil (ou vient d'y être ramené par
  // l'ajout d'un nouveau tour, voir plus bas) : piloté en continu par un vrai listener de scroll
  // (pas mesuré au moment d'un changement de contenu, voir cet effet) et lu par le
  // ResizeObserver plus bas pour décider de suivre ou non la croissance du contenu affiché.
  const stickToBottomRef = useRef(true);
  // basée sur createdAt (jamais réassigné après création d'un tour — voir pushRunningTurn dans
  // useConversation.ts), pas sur `id` : un tour envoyé dans cette session change d'id
  // (temporaire -> réel) exactement au moment où il passe à "success" (voir
  // applyExecuteSuccess), ce qui ferait alors classer à tort CETTE transition — la plus
  // courante de toutes — comme "nouveau tour" plutôt que comme la simple croissance de contenu
  // qu'elle est réellement.
  //
  // useMemo : évite de refaire ce .map().join() sur un rendu de ce composant déclenché par
  // autre chose qu'un changement de `turns` (ex: Studio.tsx re-rendu par son propre state local,
  // showHistory). Ne change en revanche rien pendant qu'un tour est "running" : le sondage de
  // progression de useConversation.ts (toutes les PROGRESS_POLL_MS) recrée alors un nouveau
  // tableau `turns` à CHAQUE tick via .map(), même quand aucun createdAt n'a changé — useMemo se
  // fie à la référence de `turns`, pas à son contenu, et recalcule donc quand même dans ce cas.
  const identityKey = useMemo(() => turns.map((t) => t.createdAt).join(','), [turns]);
  const prevIdentityKeyRef = useRef(identityKey);

  // Suit la position de scroll en continu : c'est ce qui permet de savoir si l'utilisateur est
  // proche du bas AU MOMENT où un changement de contenu survient, sans avoir à le mesurer après
  // coup une fois ce contenu déjà inséré (ce qui répondrait à la mauvaise question — voir le
  // ResizeObserver plus bas).
  useEffect(() => {
    const updateSticky = () => {
      const el = bottomRef.current;
      if (!el) return;
      stickToBottomRef.current = el.getBoundingClientRect().top - window.innerHeight <= NEAR_BOTTOM_THRESHOLD_PX;
    };
    updateSticky();
    window.addEventListener('scroll', updateSticky, { passive: true });
    window.addEventListener('resize', updateSticky);
    return () => {
      window.removeEventListener('scroll', updateSticky);
      window.removeEventListener('resize', updateSticky);
    };
  }, []);

  // Mécanisme générique : recolle en bas à CHAQUE croissance du contenu affiché (nouveau tour,
  // résultat qui arrive, contenu markdown/code chargé paresseusement après coup par
  // MarkdownRenderer, bloc d'erreur qui apparaît...) tant que l'utilisateur y était déjà collé —
  // plutôt que d'essayer d'anticiper individuellement chaque source possible de changement de
  // hauteur via une dépendance d'effet dédiée (turns.length, un statut qui change...), qui rate
  // systématiquement toute croissance survenant APRÈS le rendu déclencheur (ex: le chargement
  // différé de MarkdownRenderer, qui ne termine qu'une fois le Suspense fallback déjà affiché et
  // le tour déjà passé à "success" — aucune nouvelle valeur de dépendance ne redéclencherait
  // alors un effet basé sur turns pour suivre cette croissance-là).
  //
  // hasTurns en dépendance (pas un tableau [] figé) : turns est vide au tout premier rendu (page
  // qui vient de charger, avant tout envoi ou reprise de conversation), donc <div ref=
  // {containerRef}> n'existe pas encore (turns.length === 0 fait retourner le <p> de repli plus
  // bas) — un effet [] figé capturerait alors containerRef.current === null pour toujours, sans
  // jamais retenter une fois ce conteneur réellement monté, et le ResizeObserver ne serait donc
  // JAMAIS créé de toute la session. hasTurns change précisément quand ce conteneur apparaît
  // (ou disparaît, ex: ✨ Nouvelle conversation), ce qui redéclenche cet effet au bon moment.
  const hasTurns = turns.length > 0;
  useEffect(() => {
    if (!hasTurns) return;
    const container = containerRef.current;
    if (!container || typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(() => {
      if (stickToBottomRef.current) {
        bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
      }
    });
    observer.observe(container);
    return () => observer.disconnect();
  }, [hasTurns]);

  // Nouveau tour ajouté (envoi par l'utilisateur), ou fil remplacé par une autre conversation
  // (même de longueur identique) : "recolle" toujours au bas, sans condition — dans les deux
  // cas, l'utilisateur vient de déclencher lui-même ce changement et s'attend à voir la fin de
  // ce qui s'affiche désormais, même s'il avait remonté lire l'historique juste avant. Ce
  // "recollage" (stickToBottomRef mis à true, pas seulement un scroll ponctuel) est ce qui
  // permet ensuite au ResizeObserver ci-dessus de continuer à suivre la croissance de CE
  // nouveau tour (résultat qui arrive progressivement) sans dépendance supplémentaire.
  useEffect(() => {
    if (identityKey === prevIdentityKeyRef.current) return;
    prevIdentityKeyRef.current = identityKey;
    stickToBottomRef.current = true;
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [identityKey]);

  if (turns.length === 0) {
    return (
      <p style={{ color: '#666', textAlign: 'center', marginTop: '40px' }}>
        Décrivez votre besoin ci-dessous pour démarrer la conversation.
      </p>
    );
  }

  return (
    <div ref={containerRef} style={{ display: 'flex', flexDirection: 'column' }}>
      {turns.map((turn) => (
        <ChatMessage key={turn.id} turn={turn} onRetry={onRetry} retryDisabled={retryDisabled} />
      ))}
      <div ref={bottomRef} />
    </div>
  );
}

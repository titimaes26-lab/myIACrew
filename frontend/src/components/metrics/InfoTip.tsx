import { useEffect, useId, useRef, useState } from 'react';

const POPOVER_WIDTH = 260;
const MARGIN = 8;

function focusIsKeyboard(element: HTMLElement): boolean {
  try {
    return element.matches(':focus-visible');
  } catch {
    return false;
  }
}

// Bouton « ? » qui ouvre une bulle d'explication : au survol, au focus clavier et au clic ou toucher (mobile).
// La bulle est en position fixe (calculée à l'ouverture) pour ne pas être coupée par le défilement horizontal d'un tableau.
export default function InfoTip({ term, text }: { term: string; text: string }) {
  const id = useId();
  const buttonRef = useRef<HTMLButtonElement>(null);
  const [hover, setHover] = useState(false);
  const [pinned, setPinned] = useState(false);
  const [position, setPosition] = useState<{ top: number; left: number } | null>(null);
  const visible = hover || pinned;

  useEffect(() => {
    if (!visible || !buttonRef.current) return;
    const rect = buttonRef.current.getBoundingClientRect();
    const left = Math.min(Math.max(MARGIN, rect.left + rect.width / 2 - POPOVER_WIDTH / 2), window.innerWidth - POPOVER_WIDTH - MARGIN);
    setPosition({ top: rect.bottom + 6, left: Math.max(MARGIN, left) });
  }, [visible]);

  useEffect(() => {
    if (!visible) return;
    const close = () => { setHover(false); setPinned(false); };
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') close(); };
    const onPointer = (event: PointerEvent) => {
      if (!buttonRef.current?.contains(event.target as Node)) close();
    };
    document.addEventListener('keydown', onKey);
    document.addEventListener('pointerdown', onPointer);
    window.addEventListener('scroll', close, true);
    window.addEventListener('resize', close);
    return () => {
      document.removeEventListener('keydown', onKey);
      document.removeEventListener('pointerdown', onPointer);
      window.removeEventListener('scroll', close, true);
      window.removeEventListener('resize', close);
    };
  }, [visible]);

  return (
    <span className="viz-info">
      <button
        ref={buttonRef}
        type="button"
        className="viz-info-btn"
        aria-label={`Explication : ${term}`}
        aria-expanded={visible}
        aria-describedby={visible ? id : undefined}
        onClick={() => setPinned((current) => !current)}
        onMouseEnter={() => setHover(true)}
        onMouseLeave={() => setHover(false)}
        onFocus={(event) => { if (focusIsKeyboard(event.currentTarget)) setPinned(true); }}
        onBlur={() => { setPinned(false); setHover(false); }}
      >
        ?
      </button>
      {visible && (
        <span id={id} role="tooltip" className="viz-info-pop" style={position ?? { visibility: 'hidden' }}>
          {text}
        </span>
      )}
    </span>
  );
}

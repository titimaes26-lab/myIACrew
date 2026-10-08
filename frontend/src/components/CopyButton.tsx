import { useEffect, useRef, useState } from 'react';

type CopyState = 'idle' | 'copied' | 'failed';

// Copie `text` dans le presse-papiers : API moderne, repli sur une zone de texte temporaire (contexte non sécurisé,
// navigateur ancien). Le retour d'état (« Copié » / « Copie impossible ») disparaît seul.
async function copyToClipboard(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // Refusé (permission, iframe) : repli ci-dessous.
  }
  try {
    const area = document.createElement('textarea');
    area.value = text;
    area.setAttribute('readonly', '');
    area.style.position = 'fixed';
    area.style.opacity = '0';
    document.body.appendChild(area);
    area.select();
    const done = document.execCommand('copy');
    document.body.removeChild(area);
    return done;
  } catch {
    return false;
  }
}

const RESET_MS = 2000;

export default function CopyButton({ text, label = 'Copier', subject }: { text: string; label?: string; subject?: string }) {
  const [state, setState] = useState<CopyState>('idle');
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);

  const onClick = async () => {
    const ok = await copyToClipboard(text);
    setState(ok ? 'copied' : 'failed');
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setState('idle'), RESET_MS);
  };

  const message = state === 'copied' ? 'Copié' : state === 'failed' ? 'Copie impossible' : '';
  return (
    <>
      {/* aria-label fixe : le texte qui change dans le bouton ne serait jamais lu, d'où cette zone d'annonce à part. */}
      <button type="button" className="copy-btn" onClick={onClick} aria-label={subject ? `${label} : ${subject}` : label}>
        <span aria-hidden="true">{state === 'copied' ? '✓' : state === 'failed' ? '⚠️' : '📋'}</span>{' '}
        <span aria-hidden="true">{message || label}</span>
      </button>
      <span className="sr-only" role="status" aria-live="polite">{message}</span>
    </>
  );
}

// Présentation d'un échec d'exécution : cause lisible (icône + titre), panne temporaire ou vrai échec,
// et carte « Travail déjà présent sur GitHub » extraite du message (voir backend/delivery.py).

export interface FailureCause {
  icon: string;
  title: string;
}

const CAUSES: Record<string, FailureCause> = {
  QUOTA_EXHAUSTED: { icon: '⏳', title: 'Quota du modèle épuisé' },
  LLM_UNAVAILABLE: { icon: '🌩️', title: 'Modèle indisponible ou surchargé' },
  LLM_TIMEOUT: { icon: '⏱️', title: 'Délai du modèle dépassé' },
  GITHUB_UNAVAILABLE: { icon: '🔌', title: 'GitHub injoignable' },
  GUARDRAIL_FAILED: { icon: '🛡️', title: 'Contrôle de qualité non respecté' },
  DELIVERY_FAILED: { icon: '📦', title: 'Livraison GitHub non confirmée' },
  INTERRUPTED: { icon: '🔌', title: 'Exécution interrompue' },
  INTERNAL_ERROR: { icon: '🐞', title: 'Erreur interne' },
};

const UNKNOWN_CAUSE: FailureCause = { icon: '❌', title: 'Échec de l\'exécution' };

export function failureCause(code: string | null | undefined): FailureCause {
  return (code && CAUSES[code]) || UNKNOWN_CAUSE;
}

const TRANSIENT_CODES = new Set(['QUOTA_EXHAUSTED', 'LLM_UNAVAILABLE', 'LLM_TIMEOUT', 'GITHUB_UNAVAILABLE', 'INTERRUPTED']);

// « Panne temporaire » = un nouvel essai tel quel a une chance de réussir ; tout le reste est un vrai échec
// (rouge). `retryable` (dit par le serveur) fait foi ; à défaut (ligne ancienne, valeur absente), le code de
// la cause décide — une seule source, que le conseil affiché partage : couleur et texte ne divergent jamais.
export function isTransientFailure(retryable: boolean | null | undefined, code?: string | null): boolean {
  if (retryable != null) return retryable;
  return code != null && TRANSIENT_CODES.has(code);
}

const GITHUB_MARKER = '--- Travail déjà présent sur GitHub ---';

export interface SplitFailureMessage {
  main: string;
  // Lignes de la carte GitHub (sans le marqueur) ; null si le message n'en contient pas.
  githubLines: string[] | null;
}

export function splitGithubWork(message: string): SplitFailureMessage {
  const index = message.indexOf(GITHUB_MARKER);
  if (index === -1) return { main: message, githubLines: null };
  // Une ligne commence par « - » ; une ligne qui n'en a pas prolonge la précédente (message d'erreur sur
  // plusieurs lignes) au lieu de devenir une puce à part.
  const lines: string[] = [];
  for (const raw of message.slice(index + GITHUB_MARKER.length).split('\n')) {
    const text = raw.trim();
    if (!text) continue;
    if (/^-\s+/.test(text) || lines.length === 0) lines.push(text.replace(/^-\s+/, ''));
    else lines[lines.length - 1] += ` ${text}`;
  }
  return { main: message.slice(0, index).trim(), githubLines: lines };
}

export type InlinePart = { kind: 'text'; value: string } | { kind: 'code'; value: string } | { kind: 'link'; value: string };

// Découpe une ligne en texte, `code` (entre accents graves) et liens github.com — seuls ces deux
// types de balisage existent dans le bloc ; tout le reste reste du texte brut (jamais du HTML).
export function parseInline(line: string): InlinePart[] {
  const parts: InlinePart[] = [];
  const pattern = /`([^`]+)`|(https:\/\/github\.com\/[^\s)>\]]+)/g;
  let last = 0;
  for (const match of line.matchAll(pattern)) {
    const start = match.index ?? 0;
    if (start > last) parts.push({ kind: 'text', value: line.slice(last, start) });
    if (match[1] !== undefined) {
      parts.push({ kind: 'code', value: match[1] });
    } else {
      // La ponctuation de fin de phrase appartient au texte, pas à l'adresse (« …/pull/12. »).
      const url = match[2].replace(/[.,;:!?]+$/, '');
      parts.push({ kind: 'link', value: url });
      if (url.length < match[2].length) parts.push({ kind: 'text', value: match[2].slice(url.length) });
    }
    last = start + match[0].length;
  }
  if (last < line.length) parts.push({ kind: 'text', value: line.slice(last) });
  return parts;
}

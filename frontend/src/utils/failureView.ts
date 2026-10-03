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
  INTERRUPTED: { icon: '🔌', title: 'Exécution interrompue' },
  INTERNAL_ERROR: { icon: '🐞', title: 'Erreur interne' },
};

const UNKNOWN_CAUSE: FailureCause = { icon: '❌', title: 'Échec de l\'exécution' };

export function failureCause(code: string | null | undefined): FailureCause {
  return (code && CAUSES[code]) || UNKNOWN_CAUSE;
}

// « Panne temporaire » = le serveur dit qu'un nouvel essai tel quel a une chance de réussir (retryable) ;
// tout le reste est un vrai échec, présenté en rouge.
export function isTransientFailure(retryable: boolean | null | undefined): boolean {
  return retryable === true;
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
  const lines = message
    .slice(index + GITHUB_MARKER.length)
    .split('\n')
    .map((line) => line.replace(/^\s*-\s*/, '').trim())
    .filter(Boolean);
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
    if (match[1] !== undefined) parts.push({ kind: 'code', value: match[1] });
    else parts.push({ kind: 'link', value: match[2] });
    last = start + match[0].length;
  }
  if (last < line.length) parts.push({ kind: 'text', value: line.slice(last) });
  return parts;
}

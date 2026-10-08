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
  RATE_LIMITED: { icon: '🚦', title: 'Trop de demandes' },
  EXECUTION_TIMEOUT: { icon: '⏱️', title: 'Exécution arrêtée : durée maximale dépassée' },
  GUARDRAIL_FAILED: { icon: '🛡️', title: 'Contrôle de qualité non respecté' },
  DELIVERY_FAILED: { icon: '📦', title: 'Livraison GitHub non confirmée' },
  INTERRUPTED: { icon: '🔌', title: 'Exécution interrompue' },
  INTERNAL_ERROR: { icon: '🐞', title: 'Erreur interne' },
};

const UNKNOWN_CAUSE: FailureCause = { icon: '❌', title: 'Échec de l\'exécution' };

export function failureCause(code: string | null | undefined): FailureCause {
  return (code && CAUSES[code]) || UNKNOWN_CAUSE;
}

const TRANSIENT_CODES = new Set(['QUOTA_EXHAUSTED', 'LLM_UNAVAILABLE', 'LLM_TIMEOUT', 'GITHUB_UNAVAILABLE', 'RATE_LIMITED', 'EXECUTION_TIMEOUT', 'INTERRUPTED']);

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

const PARTIAL_MARKER = "--- Déjà réalisé avant l'échec ---";

export interface PartialWork {
  // « 2 étapes terminées avant l'échec »
  summary: string;
  // Une ligne par agent déjà terminé.
  lines: string[];
}

// Retire du message le bloc « Déjà réalisé avant l'échec » (toujours en dernier, voir backend/summary.py) ; à appeler
// AVANT splitGithubWork, dont le bloc le précède.
export function splitPartialWork(message: string): { main: string; partial: PartialWork | null } {
  const index = message.indexOf(PARTIAL_MARKER);
  if (index === -1) return { main: message, partial: null };
  const rows = message.slice(index + PARTIAL_MARKER.length).split('\n').map((row) => row.trim()).filter(Boolean);
  const main = message.slice(0, index).trim();
  if (rows.length === 0) return { main, partial: null };
  return { main, partial: { summary: rows[0], lines: rows.slice(1).map((row) => row.replace(/^-\s+/, '')) } };
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

// --- Échec de livraison GitHub (code DELIVERY_FAILED, voir backend/execution_outcomes.py::delivery_failure_message) -----------

const DELIVERY_REPORT_MARKER = "--- Rapport de l'agent (non vérifié sur GitHub) ---";
const DELIVERY_REASON_PREFIX = /^Un repository GitHub cible était configuré mais la vérification après coup a échoué\s*:\s*/;

export interface DeliveryFailureView {
  // Constat du serveur (sans l'amorce fixe) et rapport de l'agent, tel que le serveur l'a joint (non vérifié).
  reason: string;
  report: string | null;
  // Explication en une phrase, déduite de ce que GitHub contient réellement.
  summary: string;
  branch: string | null;
  branchUrl: string | null;
}

// Découpe un message d'échec de livraison en constat, rapport de l'agent et diagnostic lisible. `githubLines` vient
// de splitGithubWork (la carte « Travail déjà présent sur GitHub »).
export function describeDeliveryFailure(main: string, githubLines: string[] | null): DeliveryFailureView {
  const markerIndex = main.indexOf(DELIVERY_REPORT_MARKER);
  const head = (markerIndex === -1 ? main : main.slice(0, markerIndex)).trim();
  const report = markerIndex === -1 ? null : main.slice(markerIndex + DELIVERY_REPORT_MARKER.length).trim() || null;
  const reason = head.replace(DELIVERY_REASON_PREFIX, '');

  const branchLine = githubLines?.find((line) => /^Branche `[^`]+`/.test(line)) ?? null;
  const branch = branchLine?.match(/^Branche `([^`]+)`/)?.[1] ?? null;
  const branchUrl = branchLine
    ? parseInline(branchLine).find((part) => part.kind === 'link')?.value ?? null
    : null;
  const nothingAhead = branchLine !== null && /\b0 commit\(s\) d'avance/.test(branchLine);

  let summary = "La livraison n'a pas pu être confirmée sur GitHub.";
  if (/aucune branche/i.test(reason) || githubLines?.some((line) => /^Rien n'a été poussé/.test(line))) {
    summary = "Aucune branche n'a été créée sur GitHub : le Développeur n'a rien écrit.";
  } else if (nothingAhead) {
    summary = `La branche ${branch ? `\`${branch}\` ` : ''}existe mais n'a aucun commit d'avance sur la branche de base : le Développeur n'a rien écrit pendant cette exécution.`;
  } else if (/impossible de vérifier/i.test(reason)) {
    summary = "GitHub n'a pas répondu : la livraison n'a pas pu être vérifiée.";
  }
  return { reason, report, summary, branch, branchUrl };
}

// Un détail technique long est replié (voir FailureBlock) : au-delà de ce seuil, il n'est plus affiché d'office.
export const LONG_FAILURE_TEXT_CHARS = 600;

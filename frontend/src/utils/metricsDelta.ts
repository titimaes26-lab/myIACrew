// Écart d'une valeur par rapport à la période précédente, avec sa lecture « mieux / moins bien ».
export type Better = 'up' | 'down' | 'none'; // sens qui est une amélioration ; « none » = neutre (ex: nombre d'exécutions)
export type DeltaMode = 'relative' | 'points'; // % de variation, ou écart en points (pour un taux déjà en %)

export interface Delta {
  direction: 'up' | 'down' | 'flat';
  tone: 'good' | 'bad' | 'neutral';
  // Texte court affiché (« ▲ +12 % »), jamais la couleur seule : la flèche et le signe portent le sens.
  text: string;
  // Phrase complète pour les lecteurs d'écran.
  label: string;
}

const MINUS = '−'; // vrai signe moins (U+2212), pas un tiret

function format(value: number, mode: DeltaMode): string {
  const rounded = Math.round(Math.abs(value));
  return mode === 'points' ? `${rounded} pt${rounded > 1 ? 's' : ''}` : `${rounded} %`;
}

export function computeDelta(
  current: number | null | undefined,
  previous: number | null | undefined,
  better: Better,
  mode: DeltaMode = 'relative',
): Delta | null {
  if (current == null || previous == null) return null;
  // Une variation relative depuis zéro n'a pas de sens : pas d'écart plutôt qu'un « +∞ % » trompeur.
  if (mode === 'relative' && previous === 0) return null;
  const raw = mode === 'points' ? current - previous : ((current - previous) / previous) * 100;
  if (Math.round(Math.abs(raw)) === 0) {
    return { direction: 'flat', tone: 'neutral', text: '= stable', label: 'stable par rapport à la période précédente' };
  }
  const direction = raw > 0 ? 'up' : 'down';
  const tone = better === 'none' ? 'neutral' : direction === better ? 'good' : 'bad';
  const amount = format(raw, mode);
  return {
    direction,
    tone,
    text: `${direction === 'up' ? '▲ +' : `▼ ${MINUS}`}${amount}`,
    label: `en ${direction === 'up' ? 'hausse' : 'baisse'} de ${amount} par rapport à la période précédente`,
  };
}

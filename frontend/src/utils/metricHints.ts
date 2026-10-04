// Explications des termes statistiques du tableau de bord, partagées par la légende et les tableaux.
export const METRIC_HINTS = {
  p50: 'Médiane : la durée typique. La moitié des exécutions sont plus rapides, l’autre moitié plus lentes.',
  p95: '95 % des exécutions sont plus rapides ; seule 1 sur 20 est plus lente. Un grand écart avec le p50 signale un agent irrégulier (pauses de quota, relances). Peu fiable sous une vingtaine d’exécutions.',
} as const;

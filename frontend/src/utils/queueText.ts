// Position dans la file : le service exécute une demande à la fois, les suivantes (tous comptes) attendent leur tour.
export function queueAheadText(ahead: number | null | undefined): string {
  if (ahead == null) return 'd\'autres exécutions occupent déjà ce service.';
  if (ahead <= 0) return 'elle est la prochaine.';
  return `${ahead} exécution${ahead > 1 ? 's' : ''} ${ahead > 1 ? 'passent' : 'passe'} avant elle.`;
}

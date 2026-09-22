export function agentIcon(agentName: string): string {
  const name = agentName.toLowerCase();
  if (name.includes('résumé')) return '📝';
  if (name.includes('qa') || name.includes('test')) return '🔍';
  if (name.includes('architect')) return '🏗️';
  // Avant 'dév'/'dev' ci-dessous : "Analyste Diagnostic Technique" ne contient ni l'un ni l'autre,
  // mais l'ordre est gardé volontairement sûr si ce rôle est un jour renommé pour les inclure.
  if (name.includes('diagnostic') || name.includes('analyste')) return '🔎';
  if (name.includes('dév') || name.includes('dev')) return '💻';
  if (name.includes('design')) return '🎨';
  if (name.includes('qualif') || name.includes('product owner')) return '🧭';
  return '🤖';
}

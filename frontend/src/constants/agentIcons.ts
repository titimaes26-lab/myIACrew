export function agentIcon(agentName: string): string {
  const name = agentName.toLowerCase();
  if (name.includes('qa') || name.includes('test')) return '🔍';
  if (name.includes('architect')) return '🏗️';
  if (name.includes('dév') || name.includes('dev')) return '💻';
  if (name.includes('design')) return '🎨';
  if (name.includes('qualif') || name.includes('product owner')) return '🧭';
  return '🤖';
}

export interface CrewResultSection {
  agentName: string | null;
  content: string;
}

// Ne coupe que sur un séparateur suivi d'un nouveau titre d'agent : c'est l'unique
// forme que _format_crew_result (backend) produit entre deux tâches. Un simple "---"
// utilisé par un agent dans son propre texte (règle horizontale Markdown) ne matche pas,
// faute du "## " qui doit obligatoirement le suivre.
const SECTION_SEPARATOR = /\n\n---\n\n(?=##\s)/;
const AGENT_HEADING = /^##\s+(.+?)\s*\n([\s\S]*)$/;

export function parseCrewResult(raw: string): CrewResultSection[] {
  const chunks = raw
    .split(SECTION_SEPARATOR)
    .map((chunk) => chunk.trim())
    .filter(Boolean);

  if (chunks.length === 0) return [];

  return chunks.map((chunk) => {
    const match = chunk.match(AGENT_HEADING);
    if (match) {
      return { agentName: match[1].trim(), content: match[2].trim() };
    }
    return { agentName: null, content: chunk };
  });
}

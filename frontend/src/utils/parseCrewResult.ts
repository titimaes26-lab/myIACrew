export interface CrewResultSection {
  agentName: string | null;
  content: string;
}

const SECTION_SEPARATOR = /\n\n---\n\n/;
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

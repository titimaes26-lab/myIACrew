export interface CrewResultSection {
  agentName: string | null;
  content: string;
}

// Rôles déclarés dans backend/agentsquestion.yaml : seul un titre "## <rôle exact>"
// suivant un "---" est traité comme une vraie frontière de section. Un simple "---"
// suivi d'un titre quelconque écrit par un agent dans son propre texte (ex: "##
// Recommandations" en conclusion d'un rapport) ne matche aucun de ces rôles et reste
// donc rattaché au contenu de la section en cours, sans le fragmenter à tort.
const KNOWN_AGENT_ROLES = [
  'Senior Product Owner / Specialist en Qualification',
  'Lead Product / Game Designer',
  'Architecte Logiciel React / TypeScript',
  'Développeur Fullstack React / TypeScript',
  'QA Engineer / Automated Tester',
];

const BOUNDARY = /\n\n---\n\n(?=##\s+(.+))/g;
const AGENT_HEADING = /^##\s+(.+?)\s*\n([\s\S]*)$/;

function isKnownAgentHeading(headingText: string): boolean {
  const normalized = headingText.trim().toLowerCase();
  return KNOWN_AGENT_ROLES.some((role) => role.toLowerCase() === normalized);
}

export function parseCrewResult(raw: string): CrewResultSection[] {
  const splitPoints: number[] = [];
  for (const match of raw.matchAll(BOUNDARY)) {
    if (isKnownAgentHeading(match[1]) && match.index !== undefined) {
      splitPoints.push(match.index);
    }
  }

  const chunks: string[] = [];
  let cursor = 0;
  for (const point of splitPoints) {
    chunks.push(raw.slice(cursor, point));
    cursor = point + '\n\n---\n\n'.length;
  }
  chunks.push(raw.slice(cursor));

  const trimmedChunks = chunks.map((chunk) => chunk.trim()).filter(Boolean);
  if (trimmedChunks.length === 0) return [];

  return trimmedChunks.map((chunk) => {
    const match = chunk.match(AGENT_HEADING);
    if (match) {
      return { agentName: match[1].trim(), content: match[2].trim() };
    }
    return { agentName: null, content: chunk };
  });
}

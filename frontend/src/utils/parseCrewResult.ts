export interface CrewResultSection {
  agentName: string | null;
  content: string;
}

// Rôles déclarés dans backend/agentsquestion.yaml : seul un titre "## <rôle exact>"
// suivant un "---" est traité comme une vraie frontière de section. Un simple "---"
// suivi d'un titre quelconque écrit par un agent dans son propre texte (ex: "##
// Recommandations" en conclusion d'un rapport) ne matche aucun de ces rôles et reste
// donc rattaché au contenu de la section en cours, sans le fragmenter à tort.
// Garder cette liste synchronisée avec les champs `role:` de backend/agentsquestion.yaml.
// "Agent" couvre le fallback `agent_name = getattr(task_output, "agent", None) or "Agent"`
// de _format_crew_result (backend/crewquestion.py) si task_output.agent est un jour absent.
const KNOWN_AGENT_ROLES = [
  'Senior Product Owner / Specialist en Qualification',
  'Lead Product / Game Designer',
  'Architecte Logiciel React / TypeScript',
  'Analyste Diagnostic Technique',
  'Développeur Fullstack React / TypeScript',
  'QA Engineer / Automated Tester',
  'Agent',
];

// Doit rester identique à SUMMARY_SENTINEL (backend/crewquestion.py). Contrairement aux
// frontières entre agents, le résumé de synthèse n'utilise pas un titre "## <rôle>" (un
// simple "## Résumé" pourrait apparaître naturellement dans le rapport d'un agent, ex:
// sa propre sous-section de conclusion) mais ce marqueur, qu'aucun agent n'a normalement
// de raison de produire lui-même. On exige en plus qu'il soit immédiatement suivi d'un
// titre "## " (comme le backend le construit toujours) : des agents ayant accès en
// lecture au code source (read_a_files_content/github_read_file, y compris sur ce
// dépôt) pourraient un jour citer littéralement la ligne Python `SUMMARY_SENTINEL =
// "<!--crew-summary-->"`, qui ne serait elle jamais suivie de "## Résumé".
const SUMMARY_BOUNDARY = /<!--crew-summary-->\n\n(?=##\s+(.+))/g;

const BOUNDARY = /\n\n---\n\n(?=##\s+(.+))/g;
const AGENT_HEADING = /^##\s+(.+?)\s*\n([\s\S]*)$/;

function isKnownAgentHeading(headingText: string): boolean {
  const normalized = headingText.trim().toLowerCase();
  return KNOWN_AGENT_ROLES.some((role) => role.toLowerCase() === normalized);
}

function toSection(chunk: string): CrewResultSection {
  const match = chunk.match(AGENT_HEADING);
  if (match) {
    return { agentName: match[1].trim(), content: match[2].trim() };
  }
  return { agentName: null, content: chunk };
}

function parseAgentSections(raw: string): CrewResultSection[] {
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

  return chunks.map((chunk) => chunk.trim()).filter(Boolean).map(toSection);
}

export function parseCrewResult(raw: string): CrewResultSection[] {
  // Le dernier match, pas le premier : le backend n'ajoute ce marqueur qu'une seule
  // fois, à la toute fin. S'il apparaissait par coïncidence plus tôt (voir plus haut),
  // s'arrêter au premier couperait au mauvais endroit et perdrait le contenu réel qui suit.
  let lastMatch: RegExpExecArray | null = null;
  for (const match of raw.matchAll(SUMMARY_BOUNDARY)) {
    lastMatch = match;
  }

  if (!lastMatch || lastMatch.index === undefined) {
    return parseAgentSections(raw);
  }

  const summaryStart = lastMatch.index + lastMatch[0].length;
  const sections = parseAgentSections(raw.slice(0, lastMatch.index));
  const summaryChunk = raw.slice(summaryStart).trim();
  if (summaryChunk) {
    sections.push(toSection(summaryChunk));
  }
  return sections;
}

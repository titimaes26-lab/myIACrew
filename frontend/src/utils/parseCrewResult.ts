export interface CrewResultSection {
  agentName: string | null;
  content: string;
  // Temps d'exécution de l'agent en secondes (Task.execution_duration côté CrewAI),
  // extrait du marqueur <!--agent-duration:...--> d'en-tête écrit par le backend (voir
  // extractAgentMeta) ; absent si l'agent n'a pas encore terminé ou si la durée
  // n'a pas pu être mesurée.
  durationSeconds?: number | null;
  // Modèle IA utilisé par l'agent (par exemple « gemini-3.8-flash »), extrait du marqueur <!--agent-model:...-->
  // écrit par le backend ; absent pour une section qui n'est pas celle d'un agent connu (ex : le résumé).
  model?: string | null;
}

// Marqueurs écrits par le backend (voir backend/crew_workflow.py, format_agent_section) dans l'EN-TÊTE d'une section : après le
// heading « ## <rôle> » (présent ou non selon l'origine du texte : résultat final déjà découpé par toSection, ou valeur brute de
// turn.completedAgents, voir ChatMessage.tsx), chacun sur sa ligne, puis une ligne vide. Seul ce bloc d'en-tête est lu : un
// marqueur cité plus loin par l'agent dans son propre texte (ou en tête de SON texte, après la ligne vide) n'en est pas un.
const HEADER_MARKERS = /^(\s*##[^\n]*\n)?\s*((?:<!--agent-(?:duration|model):[^\s>]*-->[ \t]*\n)+)\n*/;

// Durée, modèle et texte d'une section brute ; les marqueurs d'en-tête sont retirés du texte.
export function extractAgentMeta(raw: string): { durationSeconds: number | null; model: string | null; content: string } {
  const header = raw.match(HEADER_MARKERS);
  if (!header) return { durationSeconds: null, model: null, content: raw };
  const duration = parseFloat(header[2].match(/<!--agent-duration:([\d.]+)-->/)?.[1] ?? '');
  return {
    durationSeconds: Number.isFinite(duration) ? duration : null,
    model: header[2].match(/<!--agent-model:([^\s>]+)-->/)?.[1] ?? null,
    content: (header[1] ?? '') + raw.slice(header[0].length),
  };
}

export function extractAgentDuration(raw: string): { durationSeconds: number | null; content: string } {
  const { durationSeconds, content } = extractAgentMeta(raw);
  return { durationSeconds, content };
}

// Rôles déclarés dans backend/agentsquestion.yaml : seul un titre "## <rôle exact>"
// suivant un "---" est traité comme une vraie frontière de section. Un simple "---"
// suivi d'un titre quelconque écrit par un agent dans son propre texte (ex: "##
// Recommandations" en conclusion d'un rapport) ne matche aucun de ces rôles et reste
// donc rattaché au contenu de la section en cours, sans le fragmenter à tort.
// Garder cette liste synchronisée avec les champs `role:` de backend/agentsquestion.yaml.
// "Agent" couvre le fallback `agent_name = getattr(task_output, "agent", None) or "Agent"`
// de _format_crew_result (backend/crew_workflow.py) si task_output.agent est un jour absent.
const KNOWN_AGENT_ROLES = [
  'Senior Product Owner / Specialist en Qualification',
  'Lead Product / Game Designer',
  'Architecte Logiciel React / TypeScript',
  'Analyste Diagnostic Technique',
  'Développeur Fullstack React / TypeScript',
  'QA Engineer / Automated Tester',
  'Agent',
];

// Doit rester identique à SUMMARY_SENTINEL (backend/crew_summary.py). Contrairement aux
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
    const { durationSeconds, model, content } = extractAgentMeta(match[2].trim());
    return { agentName: match[1].trim(), content, durationSeconds, model };
  }
  const { durationSeconds, model, content } = extractAgentMeta(chunk);
  return { agentName: null, content, durationSeconds, model };
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

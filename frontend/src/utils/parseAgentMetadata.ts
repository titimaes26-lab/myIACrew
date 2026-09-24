/**
 * Extrait les métadonnées structurées (verdict, confiance, fichiers, décisions clés)
 * du contenu Markdown d'un agent, pour afficher un résumé condensé dans l'accordéon.
 */

export interface AgentMetadata {
  agentName: string;
  // Verdict/statut court (ex: "GO", "NON_GO", "confiance: 85%")
  status?: string;
  // Fichiers extraits (pour Diagnostic Agent)
  files?: Array<{ path: string; status?: string }>;
  // Décisions/recommendations clés (pour Architect, Designer)
  decisions?: string[];
  // Questions ou alternatives (pour Qualification)
  questions?: string[];
}

export function parseAgentMetadata(content: string, agentName: string): AgentMetadata {
  const metadata: AgentMetadata = { agentName };

  // Extraire le verdict QA (pattern: "Verdict : GO", "Verdict: NON_GO", etc.)
  const verdictMatch = content.match(/Verdict\s*[:—–-]\s*(GO|NON_GO|GO_AVEC_RÉS|GO[^A-Z]*réserves|[^:\n]{1,20})/i);
  if (verdictMatch) {
    metadata.status = verdictMatch[1].trim().toUpperCase().substring(0, 20);
  }

  // Extraire confiance (pattern: "confidence: 85%", "confiance: 0.85", etc.)
  const confidenceMatch = content.match(/confidence\s*[:—–-]\s*([\d.]+)(?:\s*%)?/i);
  if (confidenceMatch) {
    let confValue = parseFloat(confidenceMatch[1]);
    // Normaliser : si entre 0 et 1, x100 pour %
    if (confValue <= 1) confValue *= 100;
    metadata.status = `Confiance: ${Math.round(confValue)}%`;
  }

  // Extraire fichiers (pattern: "<<<FICHIER: path>>>")
  const fileMatches = content.matchAll(/<<<FICHIER:\s*([^\n>]+)\s*>>>/g);
  const files: AgentMetadata['files'] = [];
  for (const match of fileMatches) {
    files.push({ path: match[1].trim() });
  }
  if (files.length > 0) {
    metadata.files = files;
  }

  // Extraire headings (### Sections) comme décisions clés
  const headingMatches = content.matchAll(/^###\s+(.+?)$/gm);
  const decisions: string[] = [];
  for (const match of headingMatches) {
    const heading = match[1].trim();
    // Exclure les headings génériques
    if (!['Fichiers', 'Fichier', 'Architecture', 'Résultat', 'Contenu'].includes(heading)) {
      decisions.push(heading);
    }
  }
  if (decisions.length > 0 && decisions.length <= 5) {
    // Limiter à 5 pour éviter un bloc trop gros
    metadata.decisions = decisions.slice(0, 5);
  }

  // Extraire questions (pour Qualification Agent)
  const questionsMatch = content.match(/questions?\s*[:—–-]\s*\n([\s\S]*?)(?:\n##|\n$)/i);
  if (questionsMatch) {
    const questionsText = questionsMatch[1];
    const questionLines = questionsText.split('\n').filter((l) => l.match(/^[-*•]\s+/));
    if (questionLines.length > 0) {
      metadata.questions = questionLines.map((l) => l.replace(/^[-*•]\s+/, '').trim());
    }
  }

  return metadata;
}

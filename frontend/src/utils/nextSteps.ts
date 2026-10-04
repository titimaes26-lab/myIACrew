// Prochaines étapes proposées par le résumé : les puces du bloc « ### À faire ensuite » (voir
// _build_summary_prompt, backend/crewquestion.py). Chacune devient un bouton qui préremplit la zone de saisie.
const NEXT_STEPS_TITLE = /^#{2,4}\s*À faire ensuite\s*$/i;
const ANY_TITLE = /^#{1,6}\s+\S/;
const BULLET = /^\s*(?:[-*•]|\d{1,2}[.)])\s+(.*\S)\s*$/;
const NOTHING = /^rien\b/i;

export const MAX_NEXT_STEPS = 3;
export const MAX_NEXT_STEP_CHARS = 300;

export function extractNextSteps(summary: string): string[] {
  const steps: string[] = [];
  let inBlock = false;
  for (const line of summary.split('\n')) {
    if (NEXT_STEPS_TITLE.test(line.trim())) { inBlock = true; continue; }
    if (!inBlock) continue;
    if (ANY_TITLE.test(line.trim())) break;
    const match = line.match(BULLET);
    if (!match) continue;
    const text = match[1].replace(/\*\*|__|`/g, '').trim();
    if (!text || NOTHING.test(text)) continue;
    steps.push(text.length > MAX_NEXT_STEP_CHARS ? `${text.slice(0, MAX_NEXT_STEP_CHARS - 1)}…` : text);
    if (steps.length === MAX_NEXT_STEPS) break;
  }
  return steps;
}

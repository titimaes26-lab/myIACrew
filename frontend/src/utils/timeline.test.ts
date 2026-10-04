import { describe, expect, it } from 'vitest';
import type { AgentRunView } from '../types';
import { buildTimeline } from './timeline';

const run = (agent: string, seconds: number | null, status: AgentRunView['status'] = 'completed'): AgentRunView => ({
  agent, label: agent, status, duration_seconds: seconds, llm_calls: 1, llm_errors: 0, tokens_known: true,
  prompt_tokens: 1, completion_tokens: 1, total_tokens: 2, tool_calls: 0, tool_errors: 0,
});

describe('buildTimeline', () => {
  it('enchaîne les étapes l’une après l’autre dans l’ordre reçu', () => {
    const { segments, total } = buildTimeline([run('design', 30), run('qa', 20)], 50);
    expect(segments.map((s) => [s.key, s.start, s.duration])).toEqual([['design', 0, 30], ['qa', 30, 20]]);
    expect(total).toBe(50);
  });

  it('regroupe le temps hors étapes dans une dernière barre d’attente', () => {
    const { segments, total } = buildTimeline([run('design', 30), run('qa', 20)], 200);
    const wait = segments.at(-1);
    expect(wait).toMatchObject({ key: 'wait', kind: 'wait', start: 50, duration: 150 });
    expect(total).toBe(200);
  });

  it('ignore un écart négligeable (bruit de mesure) et un total inconnu', () => {
    expect(buildTimeline([run('design', 30)], 30.4).segments).toHaveLength(1);
    expect(buildTimeline([run('design', 30)], null).segments).toHaveLength(1);
  });

  it('ne fabrique pas d’attente négative quand les étapes dépassent le total', () => {
    const { segments, total } = buildTimeline([run('design', 30), run('qa', 30)], 40);
    expect(segments.some((s) => s.kind === 'wait')).toBe(false);
    expect(total).toBe(60);
  });

  it('marque une étape sans durée ou incomplète, avec une barre de largeur nulle', () => {
    const { segments } = buildTimeline([run('design', null, 'incomplete'), run('qa', 10)], 10);
    expect(segments[0]).toMatchObject({ duration: 0, incomplete: true });
    expect(segments[1]).toMatchObject({ start: 0, duration: 10, incomplete: false });
  });

  it('renvoie une chronologie vide sans mesure ni total', () => {
    expect(buildTimeline([], null)).toEqual({ segments: [], total: 0 });
  });
});

import { renderHook } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { useSendOutcomes } from './sendOutcomes';
import { turn } from './fixtures';

function setup(generation = 0) {
  const state = { turns: [turn({ id: 'temp-1-1' })], error: null as string | null, conversationId: null as number | null, clarification: 'en attente' as string | null };
  const deps = {
    setTurns: vi.fn((update) => { state.turns = update(state.turns); }),
    setError: vi.fn((value) => { state.error = value; }),
    setConversationId: vi.fn((value) => { state.conversationId = value; }),
    setPendingClarification: vi.fn((value) => { state.clarification = value; }),
    conversationGenerationRef: { current: generation },
  };
  const hook = renderHook(() => useSendOutcomes(deps as never));
  return { state, deps, hook };
}

describe('useSendOutcomes', () => {
  it('applyExecuteAccepted rattache le tour à son identifiant réel et à la conversation', () => {
    const { state, hook } = setup();
    hook.result.current.applyExecuteAccepted('temp-1-1', { status: 'running', id: 42, conversation_id: 5, resumed_steps: ['design'] });
    expect(state.conversationId).toBe(5);
    expect(state.turns[0]).toMatchObject({ id: 42, resumedSteps: ['design'], status: 'running' });
    hook.result.current.applyExecuteAccepted('42', { status: 'running', id: 43, conversation_id: 5, resumed_steps: [] });
    expect(state.turns[0].id).toBe(42);           // autre tour : rien ne change
  });

  it('reportSendError marque un envoi annulé « annulé » et oublie la clarification en attente', () => {
    const { state, hook } = setup();
    hook.result.current.reportSendError(new DOMException('annulé', 'AbortError'), 'temp-1-1', 0);
    expect(state.turns[0]).toMatchObject({ status: 'cancelled' });
    expect(state.clarification).toBeNull();
    expect(state.error).toBeNull();
  });

  it('reportSendError marque un autre échec « en échec », avec sa cause, et l’affiche', () => {
    const { state, hook } = setup();
    hook.result.current.reportSendError(new Error('serveur en panne'), 'temp-1-1', 0);
    expect(state.turns[0]).toMatchObject({ status: 'failed', result: 'serveur en panne' });
    expect(state.error).toBe('serveur en panne');
  });

  it('reportSendError ignore l’issue d’une conversation abandonnée', () => {
    const { state, deps, hook } = setup(3);
    hook.result.current.reportSendError(new Error('tardif'), 'temp-1-1', 2);
    expect(state.turns[0].status).toBe('running');
    expect(deps.setError).not.toHaveBeenCalled();
    expect(deps.setPendingClarification).not.toHaveBeenCalled();
  });

  it('garde des fonctions stables d’un rendu à l’autre', () => {
    const { hook } = setup();
    const first = hook.result.current;
    hook.rerender();
    expect(hook.result.current.applyExecuteAccepted).toBe(first.applyExecuteAccepted);
    expect(hook.result.current.reportSendError).toBe(first.reportSendError);
  });
});

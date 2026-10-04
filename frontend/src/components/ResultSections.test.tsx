import { act, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import ResultSections from './ResultSections';

vi.mock('./MarkdownRenderer', () => ({ default: ({ content }: { content: string }) => <pre data-testid="md">{content}</pre> }));

const sections = [
  { agentName: 'Architecte Logiciel React / TypeScript', content: 'Plan : un panier', durationSeconds: 12 },
  { agentName: 'Analyste Diagnostic Technique', content: 'Code du panier', durationSeconds: 40 },
  { agentName: 'QA', content: 'Verdict : GO', durationSeconds: 5 },
];

function details() {
  return Array.from(document.querySelectorAll<HTMLDetailsElement>('details.section-details'));
}

let writeText: ReturnType<typeof vi.fn>;

beforeEach(() => {
  writeText = vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
});

afterEach(() => {
  vi.useRealTimers();
});

describe('ResultSections', () => {
  it('ouvre seulement la dernière section par défaut', async () => {
    render(<ResultSections sections={sections} />);
    await screen.findAllByTestId('md');
    expect(details().map((d) => d.open)).toEqual([false, false, true]);
  });

  it('offre un sommaire dont un clic ouvre la section visée', async () => {
    render(<ResultSections sections={sections} />);
    const toc = within(screen.getByRole('navigation', { name: 'Sommaire du résultat' }));
    await userEvent.click(toc.getByRole('button', { name: /Architecte/ }));
    expect(details()[0].open).toBe(true);
  });

  it('déplie et replie tout', async () => {
    render(<ResultSections sections={sections} />);
    await userEvent.click(screen.getByRole('button', { name: 'Tout déplier' }));
    expect(details().every((d) => d.open)).toBe(true);
    await userEvent.click(screen.getByRole('button', { name: 'Tout replier' }));
    expect(details().every((d) => !d.open)).toBe(true);
  });

  it('copie une section seule, ou tout le résultat avec les titres', async () => {
    render(<ResultSections sections={sections} />);
    await userEvent.click(screen.getByRole('button', { name: 'Copier : QA' }));
    expect(writeText).toHaveBeenLastCalledWith('Verdict : GO');
    await userEvent.click(screen.getByRole('button', { name: 'Copier tout : tout le résultat' }));
    const all = writeText.mock.calls[writeText.mock.calls.length - 1][0] as string;
    expect(all).toContain('## Architecte Logiciel React / TypeScript\n\nPlan : un panier');
    expect(all).toContain('## QA\n\nVerdict : GO');
  });

  it('une seule section : ni sommaire ni actions globales', async () => {
    render(<ResultSections sections={[sections[2]]} />);
    await screen.findByTestId('md');
    expect(screen.queryByRole('navigation')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Tout déplier' })).toBeNull();
  });

  it('confirme la copie puis l’efface, et signale un échec', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    render(<ResultSections sections={[sections[2]]} />);
    const button = await screen.findByRole('button', { name: 'Copier : QA' });
    await act(async () => { button.click(); });
    expect(button).toHaveTextContent('Copié');
    await act(async () => { vi.advanceTimersByTime(2500); });
    expect(button).toHaveTextContent('Copier');

    writeText.mockRejectedValue(new Error('refusé'));
    document.execCommand = vi.fn().mockReturnValue(false);
    await act(async () => { button.click(); });
    expect(button).toHaveTextContent('Copie impossible');
  });
});

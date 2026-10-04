import type { ConversationProgress } from '../../api';
import type { ChatTurn, ExecutionHistoryEntry } from '../../types';
import { historyEntryToTurn } from './turnMapping';

// Applique la progression sondée (étape courante, position dans la file, agents terminés) aux tours « running ».
export function applyProgress(turns: ChatTurn[], progress: ConversationProgress): ChatTurn[] {
  return turns.map((turn) => {
    const hasUpdate = turn.currentStep !== progress.current_step ||
                      (turn.queueAhead ?? null) !== (progress.queue_ahead ?? null) ||
                      Object.keys(progress.completed_agents || {}).length > 0;
    if (turn.status === 'running' && hasUpdate) {
      const newCompletedAgents = {
        ...(turn.completedAgents || {}),
        ...(progress.completed_agents || {}),
      };
      return {
        ...turn,
        currentStep: progress.current_step,
        queueAhead: progress.queue_ahead ?? null,
        completedAgents: newCompletedAgents,
      };
    }
    return turn;
  });
}

// Resynchronise les tours « running » relus par id avec leur état serveur. Renvoie aussi le message d'un échec découvert
// à cette occasion (pour la bannière d'erreur globale).
export function mergeResyncedTurns(
  turns: ChatTurn[], idsAtFetch: Set<number>, messages: ExecutionHistoryEntry[],
): { turns: ChatTurn[]; failedMessage: string | null } {
  let failedMessage: string | null = null;
  const merged = turns.map((turn) => {
    // Seuls les tours relus ici : un tour devenu « running » entre-temps n'a pas été interrogé.
    if (turn.status !== 'running' || typeof turn.id !== 'number' || !idsAtFetch.has(turn.id)) return turn;
    const matching = messages.find((m) => m.id === turn.id);
    if (matching) {
      if (matching.status === 'failed') failedMessage = matching.result ?? 'Une erreur est survenue.';
      // createdAt du turn LOCAL conservé (pas celui, forcément différent, de
      // ExecutionHistory.created_at côté serveur — voir database.py, fixé au moment
      // du INSERT, donc toujours postérieur de quelques centaines de ms à l'appel
      // client à pushRunningTurn) : ChatThread.tsx s'appuie sur le fait que createdAt
      // ne change JAMAIS après la création d'un tour, aussi bien pour sa clé React
      // (key={turn.createdAt}, voir son commentaire) que pour identityKey (qui décide
      // s'il faut recoller au bas du fil). Écraser createdAt ici démonterait/
      // remonterait ce <ChatMessage> ET forcerait un recollage en bas au moment même
      // où cette exécution se termine — y compris si l'utilisateur était remonté lire
      // l'historique entretemps.
      return { ...historyEntryToTurn(matching), createdAt: turn.createdAt };
    }
    // Toujours introuvable dans l'historique de cette conversation : la ligne a
    // disparu de la base (ex: nettoyage manuel direct en base d'une exécution restée
    // bloquée à "running" pour toujours après un crash serveur — /api/history ne
    // permet plus, lui, de supprimer une ligne "running" justement pour éviter ce
    // cas). Sans ce repli, ce sondage tournerait indéfiniment : plus aucune ligne
    // "running" à trouver, mais rien non plus à faire correspondre à ce tour pour le
    // faire sortir de cet état.
    return {
      ...turn,
      status: 'cancelled' as const,
      result: "Cette exécution n'existe plus en base (nettoyage manuel probable).",
      updatedAt: new Date().toISOString(),
    };
  });
  return { turns: merged, failedMessage };
}

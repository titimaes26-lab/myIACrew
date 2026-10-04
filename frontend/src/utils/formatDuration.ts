import { toServerDate } from './serverDate';

export function formatSeconds(totalSeconds: number): string {
  const safeSeconds = Math.max(0, Math.round(totalSeconds));
  const minutes = Math.floor(safeSeconds / 60);
  const seconds = safeSeconds % 60;
  return minutes > 0 ? `${minutes}m ${seconds}s` : `${seconds}s`;
}

export function formatDuration(startIso: string, endIso: string): string | null {
  const ms = toServerDate(endIso).getTime() - toServerDate(startIso).getTime();
  if (!Number.isFinite(ms) || ms < 0) return null;
  return formatSeconds(ms / 1000);
}

export function formatTime(iso: string): string {
  return toServerDate(iso).toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' });
}

// Durée écoulée en secondes entre deux instants serveur, ou null si elle n'a pas de sens.
export function elapsedSeconds(startIso: string, endIso: string): number | null {
  const ms = toServerDate(endIso).getTime() - toServerDate(startIso).getTime();
  return Number.isFinite(ms) && ms >= 0 ? ms / 1000 : null;
}

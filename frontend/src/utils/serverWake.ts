import { useSyncExternalStore } from 'react';

// Offre gratuite de Render : le service s'endort après ~15 min sans trafic et met plus d'une minute à se réveiller. Pendant ce
// temps le proxy coupe ou répond sans en-têtes CORS : le navigateur ne voit qu'un échec réseau. Ce module sonde la route publique
// `GET /` du backend pour savoir si le serveur répond, et expose l'état « en réveil » (bandeau).
export const WAKE_TIMEOUT_MS = 90_000;
export const WAKE_INTERVAL_MS = 3_000;
export const PROBE_TIMEOUT_MS = 8_000;

let wakingCount = 0;
const listeners = new Set<() => void>();

function changeWaking(delta: number): void {
  wakingCount += delta;
  listeners.forEach((listener) => listener());
}

export function isServerWaking(): boolean {
  return wakingCount > 0;
}

export function subscribeWaking(listener: () => void): () => void {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}

export function useServerWaking(): boolean {
  return useSyncExternalStore(subscribeWaking, isServerWaking, () => false);
}

function abortError(): DOMException {
  return new DOMException('Attente annulée', 'AbortError');
}

// Vrai si le serveur répond VRAIMENT : HTTP 2xx sur `/` ET contenu JSON (la route du backend renvoie {"status": …}). Une page d'attente
// HTML, le 200 d'un proxy ou une réponse vide ne comptent pas : rejouer la requête trop tôt échouerait. Ne lève jamais (échec, délai
// ou annulation : faux).
export async function probeServer(apiUrl: string, signal?: AbortSignal): Promise<boolean> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), PROBE_TIMEOUT_MS);
  const relay = () => controller.abort();
  signal?.addEventListener('abort', relay, { once: true });
  try {
    const res = await fetch(`${apiUrl}/`, { signal: controller.signal, cache: 'no-store' });
    return res.ok && (res.headers.get('content-type') ?? '').toLowerCase().includes('application/json');
  } catch {
    return false;
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener('abort', relay);
  }
}

// Un sondage sans attente : le serveur se réveille pendant que l'utilisateur lit ou écrit. Un échec est ignoré.
export function wakeServer(apiUrl: string): void {
  void probeServer(apiUrl);
}

function pause(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) { reject(abortError()); return; }
    const onAbort = () => { clearTimeout(timer); reject(abortError()); };
    const timer = setTimeout(() => { signal?.removeEventListener('abort', onAbort); resolve(); }, ms);
    signal?.addEventListener('abort', onAbort, { once: true });
  });
}

// Sonde jusqu'à ce que le serveur réponde (vrai) ou que le délai soit écoulé (faux). L'annulation lève AbortError.
export async function waitForServer(
  apiUrl: string,
  { timeoutMs = WAKE_TIMEOUT_MS, intervalMs = WAKE_INTERVAL_MS, signal }: { timeoutMs?: number; intervalMs?: number; signal?: AbortSignal } = {},
): Promise<boolean> {
  changeWaking(1);
  try {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      if (signal?.aborted) throw abortError();
      if (await probeServer(apiUrl, signal)) return true;
      await pause(intervalMs, signal);
    }
    return false;
  } finally {
    changeWaking(-1);
  }
}

export type WakeOutcome = 'awake' | 'woke' | 'still-down';

// Après un échec réseau : 'awake' = le serveur répondait déjà (l'échec vient d'ailleurs, ne rien rejouer), 'woke' = il était
// endormi et répond maintenant (la requête peut être rejouée UNE fois), 'still-down' = il ne répond toujours pas.
export async function recoverFromSleep(apiUrl: string, signal?: AbortSignal): Promise<WakeOutcome> {
  if (await probeServer(apiUrl, signal)) return 'awake';
  return (await waitForServer(apiUrl, { signal })) ? 'woke' : 'still-down';
}

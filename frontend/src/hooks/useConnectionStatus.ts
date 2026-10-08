import { useCallback, useRef, useState } from 'react';

// Nombre d'échecs de sondage consécutifs avant d'afficher « connexion perdue » : un échec isolé
// (réseau mobile qui hoquette) ne doit pas alarmer, le sondage suivant réussit presque toujours.
export const CONNECTION_LOST_AFTER = 3;

// Suit la santé du sondage de progression : `lost` devient vrai après plusieurs échecs d'affilée et
// retombe dès qu'une réponse arrive. Les trois actions sont stables (utilisables dans un effet).
export function useConnectionStatus() {
  const failuresRef = useRef(0);
  const [lost, setLost] = useState(false);

  const reportFailure = useCallback(() => {
    failuresRef.current += 1;
    if (failuresRef.current >= CONNECTION_LOST_AFTER) setLost(true);
  }, []);

  const reset = useCallback(() => {
    failuresRef.current = 0;
    setLost(false);
  }, []);

  return { lost, reportFailure, reportSuccess: reset, reset };
}

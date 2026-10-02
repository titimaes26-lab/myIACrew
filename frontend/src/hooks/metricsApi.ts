import { createContext } from 'react';

// Accès à l'API pour les composants profonds du fil de conversation (détail par agent d'un message),
// sans faire descendre apiUrl/accessToken à travers ChatThread et ChatMessage.
export interface MetricsApiConfig {
  apiUrl: string;
  accessToken: string;
}

export const MetricsApiContext = createContext<MetricsApiConfig | null>(null);

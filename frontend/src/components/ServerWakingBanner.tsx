import Alert from './ui/Alert';
import { useServerWaking } from '../utils/serverWake';

// Information (ambre, role="status"), pas une erreur : l'offre gratuite d'hébergement met le serveur en veille, il se réveille seul.
export default function ServerWakingBanner() {
  if (!useServerWaking()) return null;
  return <Alert tone="warning">⏳ Le serveur se réveille (hébergement gratuit), cela peut prendre jusqu'à une minute…</Alert>;
}

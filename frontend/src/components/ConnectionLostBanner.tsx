import Alert from './ui/Alert';

// Ton ambre (pas rouge) : ce n'est pas un échec de l'exécution, seulement l'affichage qui n'est
// plus à jour. Le sondage continue de réessayer seul.
export default function ConnectionLostBanner() {
  return (
    <Alert tone="warning">
      📡 Connexion au serveur perdue — nouvelle tentative automatique. L'exécution continue côté serveur.
    </Alert>
  );
}

// Ton ambre (pas rouge) : ce n'est pas un échec de l'exécution, seulement l'affichage qui n'est
// plus à jour. Le sondage continue de réessayer seul.
export default function ConnectionLostBanner() {
  return (
    <div
      role="status"
      style={{ padding: '10px 14px', backgroundColor: '#fffbeb', border: '1px solid #fcd34d', borderRadius: '6px', color: '#92400e', fontSize: '14px', marginBottom: '10px' }}
    >
      📡 Connexion au serveur perdue — nouvelle tentative automatique. L'exécution continue côté serveur.
    </div>
  );
}

const DRAFT_KEY = 'myiacrew:chat-draft';

export function readChatDraft(): string {
  try {
    return localStorage.getItem(DRAFT_KEY) ?? '';
  } catch {
    return '';
  }
}

export function writeChatDraft(value: string) {
  try {
    if (value) localStorage.setItem(DRAFT_KEY, value);
    else localStorage.removeItem(DRAFT_KEY);
  } catch {
    // Stockage indisponible (navigation privée, quota, etc.) : le brouillon ne sera
    // simplement pas conservé entre rechargements.
  }
}

// Appelé à la déconnexion : sur un poste partagé, le prochain utilisateur à se
// connecter ne doit pas hériter du brouillon non envoyé du précédent.
export function clearChatDraft() {
  writeChatDraft('');
}

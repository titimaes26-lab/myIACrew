import os
from crewai.tools import tool

@tool("read_a_files_content")
def read_a_files_content(file_path: str) -> str:
    """
    Lit le contenu d'un fichier sur le disque.
    Arguments:
        file_path (str): Le chemin du fichier (ex: 'src/App.tsx' ou 'index.html').
    """
    try:
        normalized_path = os.path.normpath(file_path)
        
        if not os.path.exists(normalized_path):
            return (
                f"ERREUR_FICHIER_INEXISTANT : Le fichier '{file_path}' n'existe pas sur le disque. "
                "Inutile de réessayer la lecture de ce fichier exact. "
                "Consigne : Note cette absence dans ton rapport et poursuis ton analyse."
            )
            
        with open(normalized_path, "r", encoding="utf-8") as f:
            content = f.read()
            return content if content.strip() else f"INFO : Le fichier '{file_path}' est vide."
            
    except Exception as e:
        return (
            f"ERREUR_LECTURE : Impossible de lire le fichier '{file_path}'. "
            f"Détail : {str(e)}. Ne réessaie pas d'ouvrir ce fichier."
        )
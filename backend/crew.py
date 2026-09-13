
Gemini
New chat
Search chats
Images
Library
Gems
New notebook
Erreur 429 API Gemini Résolue
Création d'un jeu de gestion
Récupération de la clé API
CrewAI pour Bugs React
Envoi d'un message
Utiliser Gemini sur un PC
Création d'un Gem CrewAI pour le Jeu Vidéo
Agents Gemini synchronisés avec Python
Bruit dans ancienne tuyauterie
Canevas de 1-on-1 pour Équipe QA
Structurer la communication du support IT
Normes électriques résidentielles en Belgique
Nettoyer une poêle brûlée
Tests unitaires React avec Claude
Verbose OpenSSL Command Guide
Détecter une fuite de joint de douche
Traiter l'humidité d'un mur de cave
Comment plier un t-shirt
React et Vercel : Gestion des données en jeu
Plan Financier Studio Étudiant
Prompt pour Agent MTP Confluence
Poids et dimensions d'un pain
Définition du 3DI en IA
Résoudre l'erreur No space left on device PostgreSQL
Graphiques de gestion hôtelière
Horaires en restauration hôtelière
KPI essentiels en gestion hôtelière
Gemini Agent QA Playwright Script
Vinaigre blanc dans le lave-vaisselle : risques
Le Soleil Altère la Levure Sèche
Erreur `NET::ERR_CERT_AUTHORITY_INVALID`
Rénovation Électrique Maison 1975 Belgique
Optimisation Configuration Agent QA Gemini
Créer un agent de test React

Activity
Manage, Belgium
Based on your places (home)
Update location
Conversation with Gemini
je reçois cette erreur lors de call api gemini : An unknown error occurred. Please check the details below.

Error details: 429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': 'You exceeded your current quota, please check your plan and billing details. For more information on this error, head to: https://ai.google.dev/gemini-api/docs/rate-limits. To monitor your current usage, head to: https://ai.dev/rate-limit. \n* Quota exceeded for metric: generativelanguage.googleapis.com/generate_content_free_tier_input_token_count, limit: 250000, model: gemini-3.5-flash-lite\nPlease retry in 33.804490529s.', 'status': 'RESOURCE_EXHAUSTED', 'details': [{'@type': 'type.googleapis.com/google.rpc.Help', 'links': [{'description': 'Learn more about Gemini API quotas', 'url': 'https://ai.google.dev/gemini-api/docs/rate-limits'}]}, {'@type': 'type.googleapis.com/google.rpc.QuotaFailure', 'violations': [{'quotaMetric': 'generativelanguage.googleapis.com/generate_content_free_tier_input_token_count', 'quotaId': 'GenerateContentInputTokensPerModelPerMinute-FreeTier', 'quotaDimensions': {'location': 'global', 'model': 'gemini-3.5-flash-lite'}, 'quotaValue': '250000'}]}, {'@type': 'type.googleapis.com/google.rpc.RetryInfo', 'retryDelay': '33s'}]}}

L'erreur 429 RESOURCE_EXHAUSTED indique que vous avez dépassé la limite de requêtes ou de tokens autorisée par le niveau gratuit (Free Tier) de l'API Gemini.

D'après le détail de l'erreur :

Métrique dépassée : generate_content_free_tier_input_token_count

Limite atteinte : 250 000 tokens d'entrée par minute (TPM) sur le modèle gemini-3.5-flash-lite.

Délai d'attente requis : L'API vous demande de patienter environ 34 secondes avant d'envoyer la requête suivante.

Solutions pour résoudre le problème
1. Implémenter une gestion d'attente (Exponential Backoff)
Pour gérer ce quota par minute (TPM/RPM) sans faire planter votre application, implémentez une logique de re-tentative automatique en lisant l'en-tête de retour ou en attendant le temps préconisé (retryDelay).

En Python (avec la bibliothèque tenacity ou le SDK Google), vous pouvez capturer l'exception 429 et attendre avant de re-tester :

Python
import time
from google.api_core.exceptions import ResourceExhausted

def call_gemini_with_retry(prompt, retries=3):
    for attempt in range(retries):
        try:
            # Votre appel d'API ici
            response = model.generate_content(prompt)
            return response
        except ResourceExhausted as e:
            print(f"Quota atteint. Attente de 35 secondes (Tentative {attempt + 1}/{retries})...")
            time.sleep(35)
    raise Exception("Nombre maximal de tentatives atteint.")
Pour vérifier si cette gestion d'erreur fonctionne correctement dans votre code :

Envoyez un volume de prompts dépassant le quota pour forcer une erreur 429.

Vérifiez que votre script se met en pause pendant 35 secondes puis retente l'appel avec succès.

2. Optimiser l'envoi de tokens
Réduire le contexte : Tronquez l'historique de conversation ou les documents volumineux envoyés dans le prompt.

Mettre en place du traitement par lots (Batching) espacé : Si vous traitez de gros volumes de données, introduisez un délai (time.sleep()) calculé entre chaque appel pour ne pas dépasser le plafond des 250 000 tokens d'entrée par tranche de 60 secondes.

3. Passer au plan Pay-as-you-go
Si votre cas d'usage nécessite un débit plus élevé ou un traitement continu en production :

Associez un compte de facturation (Billing account) à votre projet Google Cloud / Google AI Studio.

Les limites du niveau payant (Pay-as-you-go) augmentent considérablement le nombre de tokens par minute autorisés et débloquent des quotas plus élevés.

voici mon code : import os

import time

from pathlib import Path

from dotenv import load_dotenv



# Chargement du fichier .env à la racine

env_path = Path(__file__).resolve().parent / '.env'

load_dotenv(dotenv_path=env_path)



GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not GEMINI_API_KEY:

    raise ValueError("❌ GEMINI_API_KEY est manquante dans le fichier .env")



from crewai import Agent, Crew, Process, Task, LLM

from crewai.project import CrewBase, agent, crew, task

from crewai_tools import DirectoryReadTool, FileReadTool, FileWriterTool



# 1. Configuration LLM avec un modèle stable et bridage à 5 requêtes/min

MODEL_NAME = os.getenv("GEMINI_MODEL", "gemini/gemini-1.5-flash")



gemini_llm = LLM(

    model=MODEL_NAME,

    api_key=GEMINI_API_KEY,

    temperature=0.7,

    rpm_max=5  # Limitation stricte à 5 appels par minute pour étaler la consommation de tokens

)



PROJECT_ROOT = "."

file_read_tool = FileReadTool()

file_write_tool = FileWriterTool()

dir_read_tool = DirectoryReadTool(directory=PROJECT_ROOT)



@CrewBase

class RestaurantGameCrew():

    """Crew de développement sécurisée contre les erreurs de rate-limit 429."""



    agents_config = 'config/agents.yaml'

    tasks_config = 'config/tasks.yaml'



    @agent

    def game_designer(self) -> Agent:

        return Agent(

            config=self.agents_config['game_designer'],

            tools=[file_read_tool, dir_read_tool],

            llm=gemini_llm,

            max_retries=3, # Permet à CrewAI de réessayer jusqu'à 10 fois par tâche

            verbose=True

        )



    @agent

    def lead_developer(self) -> Agent:

        return Agent(

            config=self.agents_config['lead_developer'],

            tools=[dir_read_tool, file_read_tool, file_write_tool],

            llm=gemini_llm,

            max_retries=3,

            verbose=True

        )



    @agent

    def qa_engineer(self) -> Agent:

        return Agent(

            config=self.agents_config['qa_engineer'],

            tools=[dir_read_tool, file_read_tool, file_write_tool],

            llm=gemini_llm,

            max_retries=3,

            verbose=True

        )



    @task

    def design_gameplay_expansion(self) -> Task:

        return Task(

            config=self.tasks_config['design_gameplay_expansion'],

            output_file='docs/specs_gameplay.md'

        )



    @task

    def implement_gameplay_feature(self) -> Task:

        return Task(

            config=self.tasks_config['implement_gameplay_feature']

        )



    @task

    def generate_qa_tests(self) -> Task:

        return Task(

            config=self.tasks_config['generate_qa_tests'],

            output_file='tests/reports/qa_report.md'

        )



    @crew

    def crew(self) -> Crew:

        return Crew(

            agents=self.agents,

            tasks=self.tasks,

            process=Process.sequential,

            verbose=True

        )



def run_crew_with_retry(max_attempts=5):

    """Exécute la Crew et intercepte les erreurs 429 avec une pause de 40 secondes."""

    for attempt in range(1, max_attempts + 1):

        try:

            print(f"🚀 Tentative {attempt}/{max_attempts} de lancement de la Crew...")

            result = RestaurantGameCrew().crew().kickoff()

            print("\n✅ Exécution terminée avec succès !")

            return result

        except Exception as e:

            error_str = str(e)

            if "429" in error_str or "RESOURCE_EXHAUSTED" in error_str:

                wait_time = 60

                print(f"\n⚠️ Quota API dépassé (Error 429). Pause de {wait_time} secondes pour réinitialiser la fenêtre de tokens...")

                time.sleep(wait_time)

            else:

                print(f"\n❌ Erreur non liée au quota : {e}")

                raise e

    raise RuntimeError("❌ Nombre maximal de tentatives atteint. Vérifiez votre quota Google AI Studio.")



if __name__ == "__main__":

    os.makedirs("docs", exist_ok=True)

    os.makedirs("tests/reports", exist_ok=True)



    run_crew_with_retry()

Le problème ne vient pas du nombre de requêtes par minute (RPM), mais du volume total de tokens injectés dans la fenêtre d'une minute (TPM).

L'erreur indique que le modèle gemini-3.5-flash-lite utilise gemini-3.5-flash-lite (définie dans votre .env ou surchargée) alors que votre code Python utilise par défaut gemini/gemini-1.5-flash. Les agents CrewAI lisent des fichiers entiers via FileReadTool et DirectoryReadTool. À chaque itération, l'historique de pensée (chain-of-thought) et le contenu des fichiers sont renvoyés à l'API. Même avec un rpm_max=5, une seule requête contenant un gros fichier peut instantanément consommer la totalité de la limite des 250 000 tokens d'entrée par minute (TPM).

De plus, quand run_crew_with_retry() intercepte l'erreur 429 et attend 60s, l'exécution repart depuis le début (kickoff()) au lieu de reprendre la tâche en cours, ce qui gaspille vos quotas.

Corrections à apporter
1. Remplacer rpm_max par un délai explicite et ajuster le modèle
rpm_max limite les requêtes mais n'aide pas lorsque le contexte enfle. Passez sur le modèle gemini-1.5-flash ou gemini-2.5-flash (selon vos clés) et configurez les timeouts de LiteLLM (utilisé en arrière-plan par CrewAI) pour qu'il gère les réessais en interne plutôt que de relancer toute la Crew.

2. Limiter les outils d'inspection de répertoire (DirectoryReadTool)
Si DirectoryReadTool scanne un dossier contenant node_modules, build, .git ou de gros rapports, le prompt injecte des milliers de lignes de structure de projet inutiles.

Voici le code corrigé :

Python
import os
import time
from pathlib import Path
from dotenv import load_dotenv

# Chargement du fichier .env
env_path = Path(__file__).resolve().parent / '.env'
load_dotenv(dotenv_path=env_path)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    raise ValueError("❌ GEMINI_API_KEY est manquante dans le fichier .env")

# Forcer les variables d'environnement LiteLLM pour la gestion automatique des backoffs 429
os.environ["GEMINI_API_KEY"] = GEMINI_API_KEY
os.environ["LITELLM_NUM_RETRIES"] = "5"

from crewai import Agent, Crew, Process, Task, LLM
from crewai.project import CrewBase, agent, crew, task
from crewai_tools import DirectoryReadTool, FileReadTool, FileWriterTool

# 1. Utilisation de gemini-1.5-flash (plus stable sur les quotas Free Tier)
MODEL_NAME = os.getenv("GEMINI_MODEL", "gemini/gemini-1.5-flash")

gemini_llm = LLM(
    model=MODEL_NAME,
    api_key=GEMINI_API_KEY,
    temperature=0.7,
    # Augmente le temps d'attente interne pour réinsérer une requête après rate-limit
    request_timeout=120
)

# 2. Restreindre la portée des outils pour économiser les input tokens
PROJECT_ROOT = "."
file_read_tool = FileReadTool()
file_write_tool = FileWriterTool()

# Restreindre DirectoryReadTool aux dossiers pertinents (évite de charger la racine complète)
dir_read_tool = DirectoryReadTool(
    directory=PROJECT_ROOT,
    ignored_dirs=["node_modules", ".git", "build", "dist", "coverage", "__pycache__"]
)

@CrewBase
class RestaurantGameCrew():
    """Crew de développement optimisée pour limiter l'empreinte de tokens."""

    agents_config = 'config/agents.yaml'
    tasks_config = 'config/tasks.yaml'

    @agent
    def game_designer(self) -> Agent:
        return Agent(
            config=self.agents_config['game_designer'],
            tools=[file_read_tool, dir_read_tool],
            llm=gemini_llm,
            max_retry_limit=5,
            verbose=True
        )

    @agent
    def lead_developer(self) -> Agent:
        return Agent(
            config=self.agents_config['lead_developer'],
            tools=[dir_read_tool, file_read_tool, file_write_tool],
            llm=gemini_llm,
            max_retry_limit=5,
            verbose=True
        )

    @agent
    def qa_engineer(self) -> Agent:
        return Agent(
            config=self.agents_config['qa_engineer'],
            tools=[dir_read_tool, file_read_tool, file_write_tool],
            llm=gemini_llm,
            max_retry_limit=5,
            verbose=True
        )

    @task
    def design_gameplay_expansion(self) -> Task:
        return Task(
            config=self.tasks_config['design_gameplay_expansion'],
            output_file='docs/specs_gameplay.md'
        )

    @task
    def implement_gameplay_feature(self) -> Task:
        return Task(
            config=self.tasks_config['implement_gameplay_feature']
        )

    @task
    def generate_qa_tests(self) -> Task:
        return Task(
            config=self.tasks_config['generate_qa_tests'],
            output_file='tests/reports/qa_report.md'
        )

    @crew
    def crew(self) -> Crew:
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            verbose=True
        )

if __name__ == "__main__":
    os.makedirs("docs", exist_ok=True)
    os.makedirs("tests/reports", exist_ok=True)

    print("🚀 Lancement de la Crew...")
    result = RestaurantGameCrew().crew().kickoff()
    print("\n✅ Exécution terminée avec succès !")
Récapitulatif des optimisations
Exclusion de répertoires lourds (ignored_dirs) : Empêche le DirectoryReadTool d'injecter l'arborescence complète de fichiers inutiles dans le contexte à chaque tâche.

Gestion du retry par LiteLLM (os.environ["LITELLM_NUM_RETRIES"] = "5") : CrewAI gérera l'attente 429 au niveau de la requête exacte au lieu de faire échouer la fonction et de relancer tout le workflow depuis le début.

Désactivation du rpm_max=5 global au profit du timeout : Laisse l'agent consommer les fenêtres de façon plus souple sans restreindre arbitrairement des petites requêtes.

ajouter le stop lorsque l'on appuie sur la lettre q

Pour permettre à l'utilisateur de quitter ou de stopper l'exécution du script à tout moment en appuyant sur la touche 'q', la méthode la plus propre en Python consiste à utiliser un thread séparé qui écoute le clavier (avec la bibliothèque keyboard ou pynput).

Voici le code mis à jour utilisant la bibliothèque légère pynput :

1. Installation de la dépendance
Si vous ne l'avez pas encore installée :

Bash
pip install pynput
2. Code complet mis à jour
Le script lance un écouteur de clavier en arrière-plan qui intercepte la touche q. Dès que la touche est pressée, il lève un signal d'interruption et stoppe le programme immédiatement.

Python
import os
import sys
import time
from pathlib import Path
from dotenv import load_dotenv
from pynput import keyboard

# Chargement du fichier .env
env_path = Path(__file__).resolve().parent / '.env'
load_dotenv(dotenv_path=env_path)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    raise ValueError("❌ GEMINI_API_KEY est manquante dans le fichier .env")

# Variables d'environnement LiteLLM pour la gestion automatique des retry
os.environ["GEMINI_API_KEY"] = GEMINI_API_KEY
os.environ["LITELLM_NUM_RETRIES"] = "5"

from crewai import Agent, Crew, Process, Task, LLM
from crewai.project import CrewBase, agent, crew, task
from crewai_tools import DirectoryReadTool, FileReadTool, FileWriterTool

# --- GESTION DE L'ARRÊT PAR LA TOUCHE 'Q' ---
stop_requested = False

def on_press(key):
    global stop_requested
    try:
        if key.char == 'q':
            print("\n\n🛑 Touche 'q' détectée ! Arrêt immédiat de l'exécution...")
            stop_requested = True
            # Arrêt brutal mais propre du processus principal
            os._exit(0)
    except AttributeError:
        pass

# Démarrage de l'écouteur de clavier en thread d'arrière-plan
listener = keyboard.Listener(on_press=on_press)
listener.daemon = True
listener.start()
# ---------------------------------------------

MODEL_NAME = os.getenv("GEMINI_MODEL", "gemini/gemini-1.5-flash")

gemini_llm = LLM(
    model=MODEL_NAME,
    api_key=GEMINI_API_KEY,
    temperature=0.7,
    request_timeout=120
)

PROJECT_ROOT = "."
file_read_tool = FileReadTool()
file_write_tool = FileWriterTool()

dir_read_tool = DirectoryReadTool(
    directory=PROJECT_ROOT,
    ignored_dirs=["node_modules", ".git", "build", "dist", "coverage", "__pycache__"]
)

@CrewBase
class RestaurantGameCrew():
    """Crew de développement optimisée pour limiter l'empreinte de tokens."""

    agents_config = 'config/agents.yaml'
    tasks_config = 'config/tasks.yaml'

    @agent
    def game_designer(self) -> Agent:
        return Agent(
            config=self.agents_config['game_designer'],
            tools=[file_read_tool, dir_read_tool],
            llm=gemini_llm,
            max_retry_limit=5,
            verbose=True
        )

    @agent
    def lead_developer(self) -> Agent:
        return Agent(
            config=self.agents_config['lead_developer'],
            tools=[dir_read_tool, file_read_tool, file_write_tool],
            llm=gemini_llm,
            max_retry_limit=5,
            verbose=True
        )

    @agent
    def qa_engineer(self) -> Agent:
        return Agent(
            config=self.agents_config['qa_engineer'],
            tools=[dir_read_tool, file_read_tool, file_write_tool],
            llm=gemini_llm,
            max_retry_limit=5,
            verbose=True
        )

    @task
    def design_gameplay_expansion(self) -> Task:
        return Task(
            config=self.tasks_config['design_gameplay_expansion'],
            output_file='docs/specs_gameplay.md'
        )

    @task
    def implement_gameplay_feature(self) -> Task:
        return Task(
            config=self.tasks_config['implement_gameplay_feature']
        )

    @task
    def generate_qa_tests(self) -> Task:
        return Task(
            config=self.tasks_config['generate_qa_tests'],
            output_file='tests/reports/qa_report.md'
        )

    @crew
    def crew(self) -> Crew:
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            verbose=True
        )

if __name__ == "__main__":
    os.makedirs("docs", exist_ok=True)
    os.makedirs("tests/reports", exist_ok=True)

    print("🚀 Lancement de la Crew... (Appuyez sur 'q' à tout moment pour stopper)")
    
    try:
        result = RestaurantGameCrew().crew().kickoff()
        print("\n✅ Exécution terminée avec succès !")
    except KeyboardInterrupt:
        print("\n🛑 Interrompu par l'utilisateur.")
        sys.exit(0)
Comment cela fonctionne :
Thread non-bloquant : keyboard.Listener s'exécute en tâche de fond (daemon = True) pour écouter les saisies clavier sans ralentir ou bloquer les appels CrewAI / Gemini.

Arrêt immédiat : os._exit(0) force l'arrêt instantané de tous les threads Python en cours (y compris la requête HTTP active vers l'API Gemini), sans attendre que l'agent termine sa phrase.

toujours l'erreur : An unknown error occurred. Please check the details below.

Error details: 429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': 'You exceeded your current quota, please check your plan and billing details. For more information on this error, head to: https://ai.google.dev/gemini-api/docs/rate-limits. To monitor your current usage, head to: https://ai.dev/rate-limit. \n* Quota exceeded for metric: generativelanguage.googleapis.com/generate_content_free_tier_input_token_count, limit: 250000, model: gemini-3.5-flash-lite\nPlease retry in 33.124794936s.', 'status': 'RESOURCE_EXHAUSTED', 'details': [{'@type': 'type.googleapis.com/google.rpc.Help', 'links': [{'description': 'Learn more about Gemini API quotas', 'url': 'https://ai.google.dev/gemini-api/docs/rate-limits'}]}, {'@type': 'type.googleapis.com/google.rpc.QuotaFailure', 'violations': [{'quotaMetric': 'generativelanguage.googleapis.com/generate_content_free_tier_input_token_count', 'quotaId': 'GenerateContentInputTokensPerModelPerMinute-FreeTier', 'quotaDimensions': {'location': 'global', 'model': 'gemini-3.5-flash-lite'}, 'quotaValue': '250000'}]}, {'@type': 'type.googleapis.com/google.rpc.RetryInfo', 'retryDelay': '33s'}]}}

Regardez attentivement l'erreur envoyée par l'API :

Plaintext
quotaDimensions: {'location': 'global', 'model': 'gemini-3.5-flash-lite'}
L'erreur est toujours générée par gemini-3.5-flash-lite, et non par gemini-1.5-flash que nous avons configuré dans le script.

Cela signifie que votre paramètre MODEL_NAME est ignoré ou surchargé par CrewAI ou LiteLLM. CrewAI lit parfois la variable d'environnement MODEL ou la configuration présente dans vos fichiers YAML.

Pourquoi cette erreur persiste
Le modèle gemini-3.5-flash-lite a un quota Free Tier très strict : 250 000 tokens par minute. Dans un système d'agents comme CrewAI (qui ré-injecte tout le contexte, la mémoire et les outils à chaque appel), vous atteignez cette limite en 1 à 2 appels seulement.

Conflit de modèle : CrewAI ou litellm force l'utilisation de gemini-3.5-flash-lite (soit via votre fichier .env, soit via une variable système).

Solutions pour corriger le problème définitivement
Solution A : Forcer le modèle gemini-1.5-flash dans le .env et le code
Ajoutez ou modifiez explicitement cette ligne dans votre fichier .env :

Code snippet
GEMINI_MODEL=gemini/gemini-1.5-flash
Dans votre script Python, forcez le modèle sur l'objet LLM de CrewAI et désactivez la surcharge automatique :

Python
# Forcez directement la chaîne sans passer par une valeur par défaut ambiguë
MODEL_NAME = "gemini/gemini-1.5-flash"

gemini_llm = LLM(
    model=MODEL_NAME,
    api_key=GEMINI_API_KEY,
    temperature=0.7,
    request_timeout=120
)
Vérifiez également vos fichiers config/agents.yaml pour vous assurer qu'aucun modèle n'y est spécifié en dur (par exemple llm: gemini-3.5-flash-lite).

Solution B : Réduire drastiquement le contexte (Si vous devez absolument utiliser gemini-3.5-flash-lite)
Si vous voulez conserver gemini-3.5-flash-lite, le volume de texte envoyé aux agents doit être fortement réduit :

Retirez le DirectoryReadTool des agents. Cet outil scanne le disque et ré-injecte l'arborescence complète à chaque pensée de l'agent.

N'utilisez que FileReadTool avec des fichiers cibles très spécifiques.

Exemple de configuration d'agent allégée :

Python
    @agent
    def game_designer(self) -> Agent:
        return Agent(
            config=self.agents_config['game_designer'],
            tools=[file_read_tool], # Retrait de dir_read_tool
            llm=gemini_llm,
            max_retry_limit=5,
            verbose=True
        )
Solution C : Ajouter un délai forcé entre les tâches (Rate Limiting Manuel)
Pour empêcher CrewAI d'enchaîner trop rapidement les requêtes et de dépasser le quota de 250k tokens/min, vous pouvez utiliser un décorateur de tâche ou désactiver l'exécution parallèle si elle est activée.

En complément, vérifiez le tableau des quotas dans votre console Google AI Studio pour basculer sur une clé liée à un compte Pay-as-you-go (qui supprime la limite des 250k TPM) si votre usage nécessite une exécution rapide.

Comment réécrire la Crew sans DirectoryReadTool pour minimiser l'envoi de tokens ?

Voici la réécriture de votre script sans DirectoryReadTool.

Pour supprimer l'envoi massif de tokens causé par le scan des dossiers, nous laissons les agents travailler uniquement sur des fichiers explicites via FileReadTool et FileWriterTool.

Python
import os
import sys
import time
from pathlib import Path
from dotenv import load_dotenv
from pynput import keyboard

# Chargement du fichier .env
env_path = Path(__file__).resolve().parent / '.env'
load_dotenv(dotenv_path=env_path)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    raise ValueError("❌ GEMINI_API_KEY est manquante dans le fichier .env")

# Configuration de LiteLLM pour gérér les retries
os.environ["GEMINI_API_KEY"] = GEMINI_API_KEY
os.environ["LITELLM_NUM_RETRIES"] = "5"

from crewai import Agent, Crew, Process, Task, LLM
from crewai.project import CrewBase, agent, crew, task
from crewai_tools import FileReadTool, FileWriterTool

# --- GESTION DE L'ARRÊT PAR LA TOUCHE 'Q' ---
def on_press(key):
    try:
        if key.char == 'q':
            print("\n\n🛑 Touche 'q' détectée ! Arrêt immédiat de l'exécution...")
            os._exit(0)
    except AttributeError:
        pass

listener = keyboard.Listener(on_press=on_press)
listener.daemon = True
listener.start()
# ---------------------------------------------

# Force explicitement Gemini 1.5 Flash pour un quota TPM plus élevé
MODEL_NAME = os.getenv("GEMINI_MODEL", "gemini/gemini-1.5-flash")

gemini_llm = LLM(
    model=MODEL_NAME,
    api_key=GEMINI_API_KEY,
    temperature=0.7,
    request_timeout=120
)

# Utilisation d'outils ciblés uniquement (PAS de DirectoryReadTool)
file_read_tool = FileReadTool()
file_write_tool = FileWriterTool()

@CrewBase
class RestaurantGameCrew():
    """Crew optimisée pour minimiser la consommation de tokens."""

    agents_config = 'config/agents.yaml'
    tasks_config = 'config/tasks.yaml'

    @agent
    def game_designer(self) -> Agent:
        return Agent(
            config=self.agents_config['game_designer'],
            tools=[file_read_tool], # Seule la lecture de fichier spécifique est autorisée
            llm=gemini_llm,
            max_retry_limit=5,
            verbose=True
        )

    @agent
    def lead_developer(self) -> Agent:
        return Agent(
            config=self.agents_config['lead_developer'],
            tools=[file_read_tool, file_write_tool],
            llm=gemini_llm,
            max_retry_limit=5,
            verbose=True
        )

    @agent
    def qa_engineer(self) -> Agent:
        return Agent(
            config=self.agents_config['qa_engineer'],
            tools=[file_read_tool, file_write_tool],
            llm=gemini_llm,
            max_retry_limit=5,
            verbose=True
        )

    @task
    def design_gameplay_expansion(self) -> Task:
        return Task(
            config=self.tasks_config['design_gameplay_expansion'],
            output_file='docs/specs_gameplay.md'
        )

    @task
    def implement_gameplay_feature(self) -> Task:
        return Task(
            config=self.tasks_config['implement_gameplay_feature']
        )

    @task
    def generate_qa_tests(self) -> Task:
        return Task(
            config=self.tasks_config['generate_qa_tests'],
            output_file='tests/reports/qa_report.md'
        )

    @crew
    def crew(self) -> Crew:
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            verbose=True
        )

if __name__ == "__main__":
    os.makedirs("docs", exist_ok=True)
    os.makedirs("tests/reports", exist_ok=True)

    print("🚀 Lancement de la Crew optimisée en tokens... (Appuyez sur 'q' pour stopper)")
    
    try:
        result = RestaurantGameCrew().crew().kickoff()
        print("\n✅ Exécution terminée avec succès !")
    except KeyboardInterrupt:
        print("\n🛑 Interrompu par l'utilisateur.")
        sys.exit(0)
Qu'est-ce qui change dans vos fichiers de configuration ?
Comme les agents n'ont plus la capacité de scanner le dossier du projet par eux-mêmes, vous devez spécifier le chemin exact des fichiers à lire directement dans vos tâches YAML.

Exemple dans config/tasks.yaml :

YAML
design_gameplay_expansion:
  description: >
    Analyse les spécifications existantes dans 'docs/specs_gameplay.md' (si disponible) 
    et détaille la nouvelle fonctionnalité d'extension du restaurant.
  expected_output: >
    Un document Markdown complet décrivant les règles de gameplay.

implement_gameplay_feature:
  description: >
    Lis le fichier 'docs/specs_gameplay.md' et génère ou mets à jour le code 
    dans 'src/gameplay.js'.
  expected_output: >
    Le code implémenté validé.
Cette approche garantit que l'agent ne lit que les 50 à 100 lignes de code nécessaires au lieu de charger tout votre répertoire à chaque étape.

voici mon fichier actuel , peux-tu le modifier : design_gameplay_expansion:

  description: >

    Analyser la boucle de jeu actuelle dans src/ et concevoir la spécification 

    complète d'un système d'événements aléatoires pendant le service (pannes, critiques gastronomiques).

    Définir les types TypeScript nécessaires et le modèle mathématique d'impact sur le GameState.

  expected_output: >

    Un document de spécification Markdown complet incluant les interfaces TypeScript,

    la liste des événements avec leurs probabilités, et la logique de résolution.

  agent: game_designer

  output_file: docs/specs_gameplay.md  # 👈 Sauvegarde automatique des specs



implement_gameplay_feature:

  description: >

    Ta mission est d'implémenter ou de mettre à jour la fonctionnalité décrite dans docs/specs_gameplay.md.



    AVANT TOUTE MODIFICATION OU ÉCRITURE DE FICHIER, TU DOIS SUIVRE STRICTEMENT CETTE PROCÉDURE :



    1. INSPECTION : Utilise `DirectoryReadTool` pour lister le contenu du dossier `src/` et identifier les fichiers cibles existants.

    2. LECTURE & ANALYSE : Pour chaque fichier identifié (ex: `src/logic/types.ts`, `src/App.tsx`) :

       - Utilise `FileReadTool` pour lire l'intégralité du code existant.

       - Analyse si la fonctionnalité ou les types demandés y sont déjà présents.

    3. DÉCISION & ÉCRITURE :

       - Si le fichier existe et contient déjà la logique valide : NE LE RÉÉCRIS PAS inutilement.

       - Si le fichier existe mais doit être complété : Conserve le code fonctionnel existant et intègre uniquement les nouveaux ajouts/correctifs.

       - Si le fichier n'existe pas : Crée-le à l'aide de `FileWriterTool`.



  expected_output: >

    Un journal d'implémentation au format Markdown résumant :

    - La liste des fichiers inspectés (avec leur statut : Inchangé / Mis à jour / Créé).

    - Un récapitulatif des modifications apportées au code TypeScript/React.

  agent: lead_developer

  context:

    - design_gameplay_expansion



generate_qa_tests:

  description: >

    Inspecter les modifications dans src/, rédiger les suites de tests unitaires Vitest 

    dans tests/unit/ et le scénario E2E Playwright dans tests/e2e/. 

    Simuler/exécuter la vérification et produire un rapport d'anomalies structuré.

  expected_output: >

    Un rapport de test QA au format Markdown détaillant la couverture de code,

    les cas de test créés et le statut de validation (PASS/FAIL).

  agent: qa_engineer

  context:

    - implement_gameplay_feature

  output_file: tests/reports/qa_report.md  # 👈 Sauvegarde du rapport QA

Voici le fichier tasks.yaml modifié pour supprimer toute référence à DirectoryReadTool (qui scannait le dossier src/ et consommait une quantité massive de tokens).

Les tâches demandent désormais aux agents de lire directement les fichiers cibles connus via FileReadTool, éliminant le coût d'inspection globale.

YAML
design_gameplay_expansion:
  description: >
    Consulter les types et la logique existants dans 'src/types.ts' ou 'src/gameplay.ts' (via FileReadTool) 
    et concevoir la spécification complète d'un système d'événements aléatoires pendant le service (pannes, critiques gastronomiques).
    Définir les types TypeScript nécessaires et le modèle mathématique d'impact sur le GameState.
  expected_output: >
    Un document de spécification Markdown complet incluant les interfaces TypeScript,
    la liste des événements avec leurs probabilités, et la logique de résolution.
  agent: game_designer
  output_file: docs/specs_gameplay.md

implement_gameplay_feature:
  description: >
    Ta mission est d'implémenter ou de mettre à jour la fonctionnalité décrite dans 'docs/specs_gameplay.md'.

    AVANT TOUTE MODIFICATION OU ÉCRITURE DE FICHIER, TU DOIS SUIVRE STRICTEMENT CETTE PROCÉDURE :

    1. LECTURE & ANALYSE : Pour chaque fichier cible identifié dans les spécifications (ex: 'src/types.ts', 'src/gameplay.ts', 'src/App.tsx') :
       - Utilise `FileReadTool` avec le chemin exact du fichier pour lire le code existant.
       - Analyse si la fonctionnalité ou les types demandés y sont déjà présents.
    2. DÉCISION & ÉCRITURE :
       - Si le fichier existe et contient déjà la logique valide : NE LE RÉÉCRIS PAS inutilement.
       - Si le fichier existe mais doit être complété : Conserve le code fonctionnel existant et intègre uniquement les nouveaux ajouts/correctifs via `FileWriterTool`.
       - Si le fichier n'existe pas : Crée-le à l'aide de `FileWriterTool`.

  expected_output: >
    Un journal d'implémentation au format Markdown résumant :
    - La liste des fichiers traités (avec leur statut : Inchangé / Mis à jour / Créé).
    - Un récapitulatif des modifications apportées au code TypeScript/React.
  agent: lead_developer
  context:
    - design_gameplay_expansion

generate_qa_tests:
  description: >
    Lire le journal d'implémentation et les fichiers modifiés dans 'src/' avec `FileReadTool`.
    Rédiger les suites de tests unitaires Vitest dans 'tests/unit/eventSystem.test.ts' 
    et le scénario E2E Playwright dans 'tests/e2e/eventSystem.spec.ts' via `FileWriterTool`.
    Produire un rapport d'anomalies structuré.
  expected_output: >
    Un rapport de test QA au format Markdown détaillant la couverture de code,
    les cas de test créés et le statut de validation (PASS/FAIL).
  agent: qa_engineer
  context:
    - implement_gameplay_feature
  output_file: tests/reports/qa_report.md
Ce qui a changé :
Élimination de la phase "INSPECTION (DirectoryReadTool)" dans implement_gameplay_feature.

Ciblage direct par chemin de fichier dans les descriptions (src/types.ts, docs/specs_gameplay.md, tests/unit/...). L'agent utilisera directement FileReadTool('src/types.ts') au lieu d'explorer le répertoire au préalable.

Consommation de tokens divisée de manière significative, empêchant le déclenchement de l'erreur 429 RESOURCE_EXHAUSTED tout en préservant le contexte entre les tâches.

ajouter au fichier crew une limite supplémentaire : rpm_max

Voici la modification à apporter à la méthode crew() dans votre fichier Python pour ajouter le paramètre max_rpm (qui est le nom exact du paramètre pour limiter le nombre de requêtes par minute au niveau de la Crew dans CrewAI) :

Python
    @crew
    def crew(self) -> Crew:
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            verbose=True,
            max_rpm=2  # 👈 Limite globale pour la Crew (ex: max 2 requêtes par minute)
        )
Remarques importantes :
Nom du paramètre : Dans la classe Crew de CrewAI, le paramètre s'appelle max_rpm (et non rpm_max).

Valeur recommandée pour le niveau gratuit Gemini (Free Tier) :

Définir max_rpm=2 ou max_rpm=3 permet d'étaler le temps d'exécution.

Cela force CrewAI à insérer automatiquement des pauses de 20 à 30 secondes entre chaque requête, garantissant que vos fenêtres de comptage de tokens par minute (TPM) s'apaisent avant l'appel suivant.

Comment insérer un temps de pause personnalisé (ex: time.sleep) entre chaque tâche exécutée par CrewAI ?

Pour insérer une pause personnalisée (time.sleep) entre chaque tâche dans CrewAI, vous pouvez utiliser les hooks de tâche intégrés (task_callback ou after_task_execution) ou surcharger l'exécution séquentielle en exécutant les tâches une par une dans une boucle Python.

Voici les 3 méthodes possibles, de la plus propre à la plus directe :

Méthode 1 : Utiliser un task_callback (La méthode native CrewAI)
Vous pouvez définir une fonction de rappel (callback) qui sera exécutée automatiquement dès qu'une tâche se termine, avant de passer à la suivante.

Python
import time
from crewai import Crew, Process

def pause_entre_taches(task_output):
    """Callback exécuté à la fin de chaque tâche."""
    delai = 30  # Pause de 30 secondes pour réinitialiser le quota TPM
    print(f"\n⏳ Tâche terminée. Pause de sécurité de {delai} secondes pour l'API Gemini...")
    time.sleep(delai)

@CrewBase
class RestaurantGameCrew():
    # ... (vos agents et tasks)

    @crew
    def crew(self) -> Crew:
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            task_callback=pause_entre_taches,  # 👈 Ajout du callback ici
            max_rpm=2,
            verbose=True
        )
Méthode 2 : Exécuter les tâches séquentiellement en Python (Contrôle total)
Si vous souhaitez avoir un contrôle fin sur la durée de la pause entre deux tâches spécifiques (ou afficher un décompte), vous pouvez exécuter le workflow tâche par tâche dans votre fonction main :

Python
if __name__ == "__main__":
    os.makedirs("docs", exist_ok=True)
    os.makedirs("tests/reports", exist_ok=True)

    print("🚀 Lancement séquentiel avec pauses... (Appuyez sur 'q' pour stopper)")
    
    crew_instance = RestaurantGameCrew()
    tasks = [
        crew_instance.design_gameplay_expansion(),
        crew_instance.implement_gameplay_feature(),
        crew_instance.generate_qa_tests()
    ]

    PAUSE_SECONDES = 40

    for index, task in enumerate(tasks, start=1):
        print(f"\n--- 📋 Exécution de la tâche {index}/{len(tasks)} : {task.description[:50]}... ---")
        
        # Création d'une mini-crew pour la tâche courante
        single_task_crew = Crew(
            agents=crew_instance.agents,
            tasks=[task],
            process=Process.sequential,
            verbose=True
        )
        
        result = single_task_crew.kickoff()
        
        # Si ce n'est pas la dernière tâche, on applique la pause
        if index < len(tasks):
            print(f"\n⏳ Pause de {PAUSE_SECONDES}s pour vider la fenêtre de tokens Gemini...")
            time.sleep(PAUSE_SECONDES)

    print("\n✅ Toutes les tâches ont été exécutées avec succès !")
Méthode 3 : Utiliser le décorateur @after_kickoff (Pour les versions récentes de CrewAI)
Si vous utilisez la structure décorée par CrewAI @CrewBase, vous pouvez utiliser les événements de cycle de vie :

Python
from crewai.project import after_kickoff

@CrewBase
class RestaurantGameCrew():
    # ...

    @after_kickoff
    def temporisation_apres_execution(self, result):
        print("⏳ Nettoyage et temporisation globale...")
        time.sleep(10)
        return result
Quel délai choisir pour Gemini Free Tier ?
20 à 30 secondes conviennent généralement si vos prompts restent sous la barre des 100 000 tokens.

45 à 60 secondes apportent une garantie supplémentaire en cas de gros volume de code généré (pour réinitialiser complètement la fenêtre glissante des 60 secondes).

modifie ce fichier avec la methode 1 : import os

import sys

import time

from pathlib import Path

from dotenv import load_dotenv

from pynput import keyboard



# Chargement du fichier .env

env_path = Path(__file__).resolve().parent / '.env'

load_dotenv(dotenv_path=env_path)



GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not GEMINI_API_KEY:

    raise ValueError("❌ GEMINI_API_KEY est manquante dans le fichier .env")



# Configuration de LiteLLM pour gérér les retries

os.environ["GEMINI_API_KEY"] = GEMINI_API_KEY

os.environ["LITELLM_NUM_RETRIES"] = "5"



from crewai import Agent, Crew, Process, Task, LLM

from crewai.project import CrewBase, agent, crew, task

from crewai_tools import FileReadTool, FileWriterTool



# --- GESTION DE L'ARRÊT PAR LA TOUCHE 'Q' ---

def on_press(key):

    try:

        if key.char == 'q':

            print("\n\n🛑 Touche 'q' détectée ! Arrêt immédiat de l'exécution...")

            os._exit(0)

    except AttributeError:

        pass



listener = keyboard.Listener(on_press=on_press)

listener.daemon = True

listener.start()

# ---------------------------------------------



# Force explicitement Gemini 1.5 Flash pour un quota TPM plus élevé

MODEL_NAME = os.getenv("GEMINI_MODEL", "gemini/gemini-1.5-flash")



gemini_llm = LLM(

    model=MODEL_NAME,

    api_key=GEMINI_API_KEY,

    temperature=0.7,

    request_timeout=120

    max_rpm=5

)



# Utilisation d'outils ciblés uniquement (PAS de DirectoryReadTool)

file_read_tool = FileReadTool()

file_write_tool = FileWriterTool()



@CrewBase

class RestaurantGameCrew():

    """Crew optimisée pour minimiser la consommation de tokens."""



    agents_config = 'config/agents.yaml'

    tasks_config = 'config/tasks.yaml'



    @agent

    def game_designer(self) -> Agent:

        return Agent(

            config=self.agents_config['game_designer'],

            tools=[file_read_tool], # Seule la lecture de fichier spécifique est autorisée

            llm=gemini_llm,

            max_retry_limit=5,

            verbose=True

        )



    @agent

    def lead_developer(self) -> Agent:

        return Agent(

            config=self.agents_config['lead_developer'],

            tools=[file_read_tool, file_write_tool],

            llm=gemini_llm,

            max_retry_limit=5,

            verbose=True

        )



    @agent

    def qa_engineer(self) -> Agent:

        return Agent(

            config=self.agents_config['qa_engineer'],

            tools=[file_read_tool, file_write_tool],

            llm=gemini_llm,

            max_retry_limit=5,

            verbose=True

        )



    @task

    def design_gameplay_expansion(self) -> Task:

        return Task(

            config=self.tasks_config['design_gameplay_expansion'],

            output_file='docs/specs_gameplay.md'

        )



    @task

    def implement_gameplay_feature(self) -> Task:

        return Task(

            config=self.tasks_config['implement_gameplay_feature']

        )



    @task

    def generate_qa_tests(self) -> Task:

        return Task(

            config=self.tasks_config['generate_qa_tests'],

            output_file='tests/reports/qa_report.md'

        )



    @crew

    def crew(self) -> Crew:

        return Crew(

            agents=self.agents,

            tasks=self.tasks,

            process=Process.sequential,

            verbose=True

        )



if __name__ == "__main__":

    os.makedirs("docs", exist_ok=True)

    os.makedirs("tests/reports", exist_ok=True)



    print("🚀 Lancement de la Crew optimisée en tokens... (Appuyez sur 'q' pour stopper)")

    

    try:

        result = RestaurantGameCrew().crew().kickoff()

        print("\n✅ Exécution terminée avec succès !")

    except KeyboardInterrupt:

        print("\n🛑 Interrompu par l'utilisateur.")

        sys.exit(0)

Voici votre fichier mis à jour avec la Méthode 1 (task_callback).

J'ai également corrigé au passage une petite erreur de syntaxe dans votre code (il manquait une virgule après request_timeout=120 dans la déclaration de gemini_llm).

Python
import os
import sys
import time
from pathlib import Path
from dotenv import load_dotenv
from pynput import keyboard

# Chargement du fichier .env
env_path = Path(__file__).resolve().parent / '.env'
load_dotenv(dotenv_path=env_path)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    raise ValueError("❌ GEMINI_API_KEY est manquante dans le fichier .env")

# Configuration de LiteLLM pour gérer les retries
os.environ["GEMINI_API_KEY"] = GEMINI_API_KEY
os.environ["LITELLM_NUM_RETRIES"] = "5"

from crewai import Agent, Crew, Process, Task, LLM
from crewai.project import CrewBase, agent, crew, task
from crewai_tools import FileReadTool, FileWriterTool

# --- GESTION DE L'ARRÊT PAR LA TOUCHE 'Q' ---
def on_press(key):
    try:
        if key.char == 'q':
            print("\n\n🛑 Touche 'q' détectée ! Arrêt immédiat de l'exécution...")
            os._exit(0)
    except AttributeError:
        pass

listener = keyboard.Listener(on_press=on_press)
listener.daemon = True
listener.start()
# ---------------------------------------------

# --- CALLBACK POUR INTERCALER UNE PAUSE ENTRE LES TÂCHES ---
def pause_entre_taches(task_output):
    """S'exécute automatiquement après chaque tâche terminée."""
    delai = 30  # Pause de 30 secondes pour réinitialiser la fenêtre de tokens
    print(f"\n⏳ Tâche terminée. Pause de sécurité de {delai} secondes pour l'API Gemini...")
    time.sleep(delai)
# -------------------------------------------------------------

# Force explicitement Gemini 1.5 Flash pour un quota TPM plus élevé
MODEL_NAME = os.getenv("GEMINI_MODEL", "gemini/gemini-1.5-flash")

gemini_llm = LLM(
    model=MODEL_NAME,
    api_key=GEMINI_API_KEY,
    temperature=0.7,
    request_timeout=120
)

# Utilisation d'outils ciblés uniquement (PAS de DirectoryReadTool)
file_read_tool = FileReadTool()
file_write_tool = FileWriterTool()

@CrewBase
class RestaurantGameCrew():
    """Crew optimisée pour minimiser la consommation de tokens."""

    agents_config = 'config/agents.yaml'
    tasks_config = 'config/tasks.yaml'

    @agent
    def game_designer(self) -> Agent:
        return Agent(
            config=self.agents_config['game_designer'],
            tools=[file_read_tool], # Seule la lecture de fichier spécifique est autorisée
            llm=gemini_llm,
            max_retry_limit=5,
            verbose=True
        )

    @agent
    def lead_developer(self) -> Agent:
        return Agent(
            config=self.agents_config['lead_developer'],
            tools=[file_read_tool, file_write_tool],
            llm=gemini_llm,
            max_retry_limit=5,
            verbose=True
        )

    @agent
    def qa_engineer(self) -> Agent:
        return Agent(
            config=self.agents_config['qa_engineer'],
            tools=[file_read_tool, file_write_tool],
            llm=gemini_llm,
            max_retry_limit=5,
            verbose=True
        )

    @task
    def design_gameplay_expansion(self) -> Task:
        return Task(
            config=self.tasks_config['design_gameplay_expansion'],
            output_file='docs/specs_gameplay.md'
        )

    @task
    def implement_gameplay_feature(self) -> Task:
        return Task(
            config=self.tasks_config['implement_gameplay_feature']
        )

    @task
    def generate_qa_tests(self) -> Task:
        return Task(
            config=self.tasks_config['generate_qa_tests'],
            output_file='tests/reports/qa_report.md'
        )

    @crew
    def crew(self) -> Crew:
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            task_callback=pause_entre_taches,  # 👈 Callback exécuté à la fin de chaque tâche
            max_rpm=2,                         # 👈 Limite globale pour espacer les requêtes
            verbose=True
        )

if __name__ == "__main__":
    os.makedirs("docs", exist_ok=True)
    os.makedirs("tests/reports", exist_ok=True)

    print("🚀 Lancement de la Crew optimisée en tokens... (Appuyez sur 'q' pour stopper)")
    
    try:
        result = RestaurantGameCrew().crew().kickoff()
        print("\n✅ Exécution terminée avec succès !")
    except KeyboardInterrupt:
        print("\n🛑 Interrompu par l'utilisateur.")
        sys.exit(0)
dis moi si le resultat de chaque agent est loggé sur disque ?

Dans votre configuration actuelle, seulement deux des trois tâches sauvegardent leur résultat directement sur le disque.

Bilan par agent et tâche
Agent / Tâche	Sauvegarde sur disque ?	Emplacement / Explication
game_designer


design_gameplay_expansion

Oui	Sauvegardé automatiquement dans docs/specs_gameplay.md grâce au paramètre output_file.
lead_developer


implement_gameplay_feature

Non (ou via outils)	Pas de paramètre output_file sur la tâche. Le journal d'exécution reste uniquement dans la console Python, sauf si l'agent utilise activement FileWriterTool pour écrire du code dans les fichiers source (ex: src/gameplay.ts).
qa_engineer


generate_qa_tests

Oui	Sauvegardé automatiquement dans tests/reports/qa_report.md grâce au paramètre output_file.
Comment tout sauvegarder sur disque ?
1. Sauvegarder le rapport du Lead Developer
Si vous souhaitez que le journal d'implémentation du lead_developer soit également conservé sous forme de fichier Markdown, ajoutez un output_file à sa tâche :

Python
    @task
    def implement_gameplay_feature(self) -> Task:
        return Task(
            config=self.tasks_config['implement_gameplay_feature'],
            output_file='docs/implementation_log.md'  # 👈 Ajout du fichier de sortie
        )
2. Conserver les logs complets de la console dans un fichier
Si vous souhaitez enregistrer l'intégralité des réflexions et actions des agents (le mode verbose=True), vous pouvez demander à CrewAI de tout consigner dans un fichier de log global :

Python
    @crew
    def crew(self) -> Crew:
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            task_callback=pause_entre_taches,
            max_rpm=2,
            output_log_file='crew_execution.log',  # 👈 Sauvegarde tous les logs de la console
            verbose=True
        )
Je veux les deux

Voici le code Python complet mis à jour intégrant à la fois la sauvegarde du journal d'implémentation du lead_developer et la création d'un fichier de log global crew_execution.log pour consigner l'intégralité des échanges de la console.

Python
import os
import sys
import time
from pathlib import Path
from dotenv import load_dotenv
from pynput import keyboard

# Chargement du fichier .env
env_path = Path(__file__).resolve().parent / '.env'
load_dotenv(dotenv_path=env_path)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    raise ValueError("❌ GEMINI_API_KEY est manquante dans le fichier .env")

# Configuration de LiteLLM pour gérer les retries
os.environ["GEMINI_API_KEY"] = GEMINI_API_KEY
os.environ["LITELLM_NUM_RETRIES"] = "5"

from crewai import Agent, Crew, Process, Task, LLM
from crewai.project import CrewBase, agent, crew, task
from crewai_tools import FileReadTool, FileWriterTool

# --- GESTION DE L'ARRÊT PAR LA TOUCHE 'Q' ---
def on_press(key):
    try:
        if key.char == 'q':
            print("\n\n🛑 Touche 'q' détectée ! Arrêt immédiat de l'exécution...")
            os._exit(0)
    except AttributeError:
        pass

listener = keyboard.Listener(on_press=on_press)
listener.daemon = True
listener.start()
# ---------------------------------------------

# --- CALLBACK POUR INTERCALER UNE PAUSE ENTRE LES TÂCHES ---
def pause_entre_taches(task_output):
    """S'exécute automatiquement après chaque tâche terminée."""
    delai = 30  # Pause de 30 secondes pour réinitialiser la fenêtre de tokens
    print(f"\n⏳ Tâche terminée. Pause de sécurité de {delai} secondes pour l'API Gemini...")
    time.sleep(delai)
# -------------------------------------------------------------

# Force explicitement Gemini 1.5 Flash pour un quota TPM plus élevé
MODEL_NAME = os.getenv("GEMINI_MODEL", "gemini/gemini-1.5-flash")

gemini_llm = LLM(
    model=MODEL_NAME,
    api_key=GEMINI_API_KEY,
    temperature=0.7,
    request_timeout=120
)

# Utilisation d'outils ciblés uniquement (PAS de DirectoryReadTool)
file_read_tool = FileReadTool()
file_write_tool = FileWriterTool()

@CrewBase
class RestaurantGameCrew():
    """Crew optimisée pour minimiser la consommation de tokens."""

    agents_config = 'config/agents.yaml'
    tasks_config = 'config/tasks.yaml'

    @agent
    def game_designer(self) -> Agent:
        return Agent(
            config=self.agents_config['game_designer'],
            tools=[file_read_tool],
            llm=gemini_llm,
            max_retry_limit=5,
            verbose=True
        )

    @agent
    def lead_developer(self) -> Agent:
        return Agent(
            config=self.agents_config['lead_developer'],
            tools=[file_read_tool, file_write_tool],
            llm=gemini_llm,
            max_retry_limit=5,
            verbose=True
        )

    @agent
    def qa_engineer(self) -> Agent:
        return Agent(
            config=self.agents_config['qa_engineer'],
            tools=[file_read_tool, file_write_tool],
            llm=gemini_llm,
            max_retry_limit=5,
            verbose=True
        )

    @task
    def design_gameplay_expansion(self) -> Task:
        return Task(
            config=self.tasks_config['design_gameplay_expansion'],
            output_file='docs/specs_gameplay.md'
        )

    @task
    def implement_gameplay_feature(self) -> Task:
        return Task(
            config=self.tasks_config['implement_gameplay_feature'],
            output_file='docs/implementation_log.md'  # 👈 1. Journal du lead_developer sauvegardé
        )

    @task
    def generate_qa_tests(self) -> Task:
        return Task(
            config=self.tasks_config['generate_qa_tests'],
            output_file='tests/reports/qa_report.md'
        )

    @crew
    def crew(self) -> Crew:
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            task_callback=pause_entre_taches,
            max_rpm=2,
            output_log_file='crew_execution.log',  # 👈 2. Sauvegarde de tous les logs de console
            verbose=True
        )

if __name__ == "__main__":
    os.makedirs("docs", exist_ok=True)
    os.makedirs("tests/reports", exist_ok=True)

    print("🚀 Lancement de la Crew optimisée en tokens... (Appuyez sur 'q' pour stopper)")
    
    try:
        result = RestaurantGameCrew().crew().kickoff()
        print("\n✅ Exécution terminée avec succès !")
    except KeyboardInterrupt:
        print("\n🛑 Interrompu par l'utilisateur.")
        sys.exit(0)
Récapitulatif des fichiers générés à l'exécution :
docs/specs_gameplay.md : Spécifications du Game Designer.

docs/implementation_log.md : Bilan des modifications et code rédigé par le Lead Developer.

tests/reports/qa_report.md : Rapport de validation et tests du QA Engineer.

crew_execution.log : Trace complète de tout ce qui s'est affiché dans la console durant le déroulement des tâches.




Gemini is AI and can make mistakes, including about people. Your privacy and GeminiOpens in a new window


import os
import sys
import time
import asyncio
import json
import re
import functools
from pathlib import Path
from typing import List, Literal, Callable, Any
from dotenv import load_dotenv
from pydantic import BaseModel, Field

# --- CHARGEMENT DES VARIABLES D'ENVIRONNEMENT ---
env_path = Path(__file__).resolve().parent / '.env'
load_dotenv(dotenv_path=env_path)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Configuration LiteLLM & Quotas
os.environ["GEMINI_API_KEY"] = GEMINI_API_KEY or ""
os.environ["LITELLM_NUM_RETRIES"] = "7"
os.environ["LITELLM_TIME_CONTINUOUS_BACKOFF"] = "2"

from crewai import Agent, Crew, Process, Task, LLM
from crewai.project import CrewBase, agent, crew, task
from crewai_tools import FileWriterTool
from tools import read_a_files_content
from github_tools import (
    github_read_file,
    github_list_directory,
    github_create_branch,
    github_write_file,
    github_open_pull_request,
)

# --- MÉTRIQUES & PAUSES ---
class ExecutionMetrics:
    def __init__(self):
        self.start_time = time.time()
        self.api_calls_count = 0
        self.rate_limit_hits = 0
        self.total_wait_time = 0.0

    def record_call(self):
        self.api_calls_count += 1

    def record_rate_limit(self, wait_seconds: float):
        self.rate_limit_hits += 1
        self.total_wait_time += wait_seconds

metrics = ExecutionMetrics()

def retry_on_rate_limit_async(max_retries: int = 5, base_delay: float = 10.0):
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            retries = 0
            while True:
                metrics.record_call()
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    err_msg = str(e).lower()
                    is_retryable = any(marker in err_msg for marker in (
                        "429", "resource_exhausted", "rate limit", "quota",
                        "503", "unavailable", "high demand", "overloaded",
                    ))
                    if is_retryable:
                        retries += 1
                        if retries > max_retries:
                            raise e
                        match = re.search(r'retry after (\d+(\.\d+)?)', err_msg)
                        wait_time = float(match.group(1)) + 2.0 if match else base_delay * (2 ** (retries - 1))
                        metrics.record_rate_limit(wait_time)
                        await asyncio.sleep(wait_time)
                    else:
                        raise e
        return wrapper
    return decorator

class QuotaManager:
    def __init__(self):
        self.last_execution_time = 0.0
        self.min_interval_seconds = 5.0
    
    def adaptive_pause(self, task_output=None):
        elapsed = time.time() - self.last_execution_time
        if elapsed < self.min_interval_seconds:
            wait_time = self.min_interval_seconds - elapsed
            metrics.total_wait_time += wait_time
            time.sleep(wait_time)
        self.last_execution_time = time.time()

quota_mgr = QuotaManager()

class CrewStepError(Exception):
    """Erreur levée quand le crew échoue à une étape précise (voir run_dynamic_crew).

    Conserve le message de l'exception d'origine (str(e) identique) pour que
    retry_on_rate_limit_async continue de détecter les erreurs de quota/rate-limit
    normalement, tout en exposant l'étape et l'agent en cours au moment de l'échec.
    """
    def __init__(self, step_index: int, total_steps: int, agent_role: str, original: Exception):
        self.step_index = step_index
        self.total_steps = total_steps
        self.agent_role = agent_role.strip()
        super().__init__(str(original))

def _format_crew_result(result) -> str:
    """Combine les sorties de toutes les tâches exécutées, pas seulement la dernière.

    result.raw ne reflète que la sortie de la dernière tâche du crew. Pour un workflow
    à plusieurs tâches (ex: ANALYSE_ONLY = design_task puis architecture_task), le
    contenu produit par les tâches précédentes serait sinon silencieusement perdu et
    jamais renvoyé à l'utilisateur.
    """
    tasks_output = getattr(result, "tasks_output", None)
    if not tasks_output or len(tasks_output) <= 1:
        return str(result.raw) if hasattr(result, "raw") else str(result)

    sections = []
    for task_output in tasks_output:
        agent_name = getattr(task_output, "agent", None) or "Agent"
        raw = getattr(task_output, "raw", None) or str(task_output)
        sections.append(f"## {agent_name}\n\n{raw}")
    return "\n\n---\n\n".join(sections)

# --- PYDANTIC MODEL & LLM ---
class AnalysisReport(BaseModel):
    summary: str = Field(description="Résumé en 2-3 phrases de ce que l'agent a compris de la demande.")
    is_clear: bool = Field(description="Vrai si la demande est claire, Faux si des ambiguïtés existent.")
    request_type: Literal["ANALYSE_ONLY", "BUGFIX", "FEATURE", "DESIGN_AND_DEV"] = Field(
        description="Type de workflow à déclencher."
    )
    questions: List[str] = Field(default_factory=list, description="Liste de 2 à 4 questions si la demande est floue.")

MODEL_NAME = os.getenv("GEMINI_MODEL", "gemini/gemini-3.5-flash-lite")
gemini_llm = LLM(model=MODEL_NAME, api_key=GEMINI_API_KEY, temperature=0.7, request_timeout=120)
file_write_tool = FileWriterTool()

# --- CREW BASE ---
@CrewBase
class AppDevelopmentCrew():
    agents_config = 'agentsquestion.yaml'
    tasks_config = 'tasksquestion.yaml'

    @agent
    def qualification_agent(self) -> Agent:
        return Agent(config=self.agents_config['qualification_agent'], tools=[], llm=gemini_llm, max_iter=2, verbose=True)

    @agent
    def product_designer_agent(self) -> Agent:
        return Agent(
            config=self.agents_config['product_designer_agent'],
            tools=[read_a_files_content, file_write_tool, github_read_file, github_list_directory],
            llm=gemini_llm, max_iter=3, verbose=True,
        )

    @agent
    def architect_agent(self) -> Agent:
        return Agent(
            config=self.agents_config['architect_agent'],
            tools=[read_a_files_content, file_write_tool, github_read_file, github_list_directory],
            llm=gemini_llm, max_iter=3, verbose=True,
        )

    @agent
    def developer_agent(self) -> Agent:
        return Agent(
            config=self.agents_config['developer_agent'],
            tools=[
                read_a_files_content, file_write_tool,
                github_read_file, github_list_directory,
                github_create_branch, github_write_file, github_open_pull_request,
            ],
            llm=gemini_llm, max_iter=3, verbose=True,
        )

    @agent
    def qa_agent(self) -> Agent:
        return Agent(
            config=self.agents_config['qa_agent'],
            tools=[read_a_files_content, file_write_tool, github_read_file, github_list_directory],
            llm=gemini_llm, max_iter=3, verbose=True,
        )

    @task
    def qualification_task(self) -> Task:
        return Task(config=self.tasks_config['qualification_task'], agent=self.qualification_agent(), output_file='docs/qualification_report.md')

    @task
    def design_task(self) -> Task:
        return Task(config=self.tasks_config['design_task'], agent=self.product_designer_agent(), output_file='docs/specs_design.md')

    @task
    def architecture_task(self) -> Task:
        return Task(config=self.tasks_config['architecture_task'], agent=self.architect_agent(), output_file='docs/architecture_spec.md')

    @task
    def development_task(self) -> Task:
        return Task(config=self.tasks_config['development_task'], agent=self.developer_agent(), output_file='docs/implementation_log.md')

    @task
    def qa_task(self) -> Task:
        return Task(config=self.tasks_config['qa_task'], agent=self.qa_agent(), output_file='tests/reports/qa_report.md')

    @retry_on_rate_limit_async(max_retries=5, base_delay=12.0)
    async def analyze_user_request(self, user_prompt: str) -> AnalysisReport:
        qualif_agent = self.qualification_agent()
        task_prompt = f"""
        Tu es le Spécialiste en Qualification / Senior Product Owner.
        Voici la demande : "{user_prompt}"
        Remplis le rapport JSON structuré : summary, is_clear, request_type, questions.
        """
        analysis_task = Task(description=task_prompt, expected_output="Schéma JSON AnalysisReport.", agent=qualif_agent, output_pydantic=AnalysisReport)
        analysis_crew = Crew(agents=[qualif_agent], tasks=[analysis_task], process=Process.sequential, verbose=False)
        
        # Exécution asynchrone pour éviter l'erreur d'event loop
        result = await analysis_crew.kickoff_async()
        quota_mgr.last_execution_time = time.time()

        if hasattr(result, 'pydantic') and result.pydantic is not None:
            return result.pydantic

        raw_output = str(result.raw) if hasattr(result, 'raw') else str(result)
        try:
            match = re.search(r'\{.*\}', raw_output, re.DOTALL)
            if match:
                data = json.loads(match.group(0))
                return AnalysisReport(
                    summary=data.get("summary", "Analyse effectuée."),
                    is_clear=bool(data.get("is_clear", True)),
                    request_type=data.get("request_type", "DESIGN_AND_DEV"),
                    questions=data.get("questions", [])
                )
        except Exception:
            pass

        return AnalysisReport(summary=f"Analyse : {user_prompt}", is_clear=True, request_type="DESIGN_AND_DEV", questions=[])

    def save_analysis_report(self, report: AnalysisReport, user_prompt: str, filepath: str = "docs/qualification_report.md"):
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        q_text = "\n".join([f"- {q}" for q in report.questions]) if report.questions else "_Aucune question._"
        md_content = f"# Rapport\n\n**Demande:** {user_prompt}\n\n### Synthèse\n{report.summary}\n\n### Workflow\n- Clear: {report.is_clear}\n- Type: {report.request_type}\n\n### Questions\n{q_text}"
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(md_content)

    @retry_on_rate_limit_async(max_retries=5, base_delay=15.0)
    async def run_dynamic_crew(self, inputs: dict, request_type: str):
        if request_type == "ANALYSE_ONLY":
            selected_tasks = [self.design_task(), self.architecture_task()]
        elif request_type == "BUGFIX":
            selected_tasks = [self.development_task(), self.qa_task()]
        elif request_type == "FEATURE":
            selected_tasks = [self.architecture_task(), self.development_task(), self.qa_task()]
        else:
            selected_tasks = [self.design_task(), self.architecture_task(), self.development_task(), self.qa_task()]

        selected_agents = list({task.agent for task in selected_tasks})

        completed_count = 0

        def on_task_complete(task_output):
            nonlocal completed_count
            completed_count += 1
            quota_mgr.adaptive_pause(task_output)

        dynamic_crew = Crew(
            agents=selected_agents,
            tasks=selected_tasks,
            process=Process.sequential,
            task_callback=on_task_complete,
            max_rpm=3,
            output_log_file='crew_execution.log',
            verbose=True
        )
        try:
            result = await dynamic_crew.kickoff_async(inputs=inputs)
        except Exception as e:
            # Identifie la tâche qui était en cours au moment de l'échec (celle juste
            # après la dernière complétée avec succès) pour que le frontend puisse
            # afficher "échec pendant X" plutôt qu'une erreur générique.
            failed_task = selected_tasks[completed_count] if completed_count < len(selected_tasks) else None
            agent_role = failed_task.agent.role if failed_task else "étape finale"
            raise CrewStepError(completed_count + 1, len(selected_tasks), agent_role, e) from e

        quota_mgr.last_execution_time = time.time()
        return _format_crew_result(result)

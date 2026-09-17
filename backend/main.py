import traceback
import os
import uuid
from datetime import datetime

from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
from sqlmodel import Session, select

from crewquestion import AppDevelopmentCrew, AnalysisReport
from database import create_db_and_tables, get_session, ExecutionHistory
from auth import get_current_user

# 1. INSTANCIATION DE FASTAPI (Obligatoire au tout début !)
app = FastAPI(title="CrewAI App Development API")

# 2. ÉVÉNEMENT DE DÉMARRAGE (Création des tables BDD)
@app.on_event("startup")
def on_startup():
    create_db_and_tables()

# 3. CONFIGURATION CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

crew_instance = AppDevelopmentCrew()

class UserRequestInput(BaseModel):
    user_request: str

class WorkflowExecutionInput(BaseModel):
    user_request: str
    target_workflow: str
    clarifications: Optional[str] = ""
    repo_owner: Optional[str] = None
    repo_name: Optional[str] = None
    base_branch: Optional[str] = "main"

# 4. ENDPOINTS API
@app.get("/")
def read_root():
    return {"status": "API CrewAI opérationnelle"}

@app.post("/api/qualify", response_model=AnalysisReport)
async def qualify_request(data: UserRequestInput, user: dict = Depends(get_current_user)):
    """Étape 1 : Qualification du besoin"""
    try:
        report = await crew_instance.analyze_user_request(data.user_request)
        crew_instance.save_analysis_report(report, data.user_request)
        return report
    except Exception as e:
        print("--- ERREUR CREWAI DETECTEE ---")
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/execute")
async def execute_workflow(
    data: WorkflowExecutionInput,
    session: Session = Depends(get_session),
    user: dict = Depends(get_current_user),
):
    """Étape 2 : Lancement dynamique des agents & enregistrement BDD"""
    final_prompt = (
        f"Demande initiale : {data.user_request}\n"
        f"Type d'exécution : {data.target_workflow}\n"
        f"Précisions apportées : {data.clarifications if data.clarifications else 'Aucune.'}"
    )

    has_repo_target = bool(data.repo_owner and data.repo_name)
    work_branch = f"crewai/{data.target_workflow.lower()}-{uuid.uuid4().hex[:8]}" if has_repo_target else ""

    # Enregistrement immédiat (statut "running") pour garder une trace même en cas d'échec
    db_entry = ExecutionHistory(
        user_request=data.user_request,
        workflow=data.target_workflow,
        clarifications=data.clarifications,
        status="running",
        user_id=user.get("id"),
        repo_owner=data.repo_owner if has_repo_target else None,
        repo_name=data.repo_name if has_repo_target else None,
        base_branch=data.base_branch if has_repo_target else None,
        work_branch=work_branch or None,
    )
    session.add(db_entry)
    session.commit()
    session.refresh(db_entry)

    try:
        result = await crew_instance.run_dynamic_crew(
            inputs={
                'user_request': final_prompt,
                'repo_owner': data.repo_owner or '',
                'repo_name': data.repo_name or '',
                'base_branch': data.base_branch or 'main',
                'work_branch': work_branch,
                'repo_instructions': (
                    f"Repository GitHub cible : {data.repo_owner}/{data.repo_name}\n"
                    f"Branche de base : {data.base_branch or 'main'}\n"
                    f"Branche de travail à créer et utiliser pour toute écriture : {work_branch}"
                    if has_repo_target
                    else "Aucun repository GitHub cible fourni : n'utilise aucun outil github_*, travaille uniquement sur le disque local."
                ),
            },
            request_type=data.target_workflow
        )
        raw_result = str(result.raw) if hasattr(result, 'raw') else str(result)

        db_entry.result = raw_result
        db_entry.status = "success"
        db_entry.updated_at = datetime.utcnow()
        session.add(db_entry)
        session.commit()
        session.refresh(db_entry)

        return {
            "status": "success",
            "id": db_entry.id,
            "workflow": data.target_workflow,
            "result": raw_result
        }
    except Exception as e:
        print("--- ERREUR CREWAI EXECUTION DETECTEE ---")
        print(traceback.format_exc())

        db_entry.status = "failed"
        db_entry.result = str(e)
        db_entry.updated_at = datetime.utcnow()
        session.add(db_entry)
        session.commit()

        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/history", response_model=List[ExecutionHistory])
async def get_history(
    limit: int = 20,
    offset: int = 0,
    session: Session = Depends(get_session),
    user: dict = Depends(get_current_user),
):
    """Historique des exécutions de l'utilisateur courant, les plus récentes en premier."""
    limit = min(max(limit, 1), 100)
    offset = max(offset, 0)
    statement = (
        select(ExecutionHistory)
        .where(ExecutionHistory.user_id == user.get("id"))
        .order_by(ExecutionHistory.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    return session.exec(statement).all()

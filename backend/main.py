import traceback
import os

from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
from sqlmodel import Session

from crewquestion import AppDevelopmentCrew, AnalysisReport
from database import create_db_and_tables, get_session, ExecutionHistory

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

# 4. ENDPOINTS API
@app.get("/")
def read_root():
    return {"status": "API CrewAI opérationnelle"}

@app.post("/api/qualify", response_model=AnalysisReport)
async def qualify_request(data: UserRequestInput):
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
    session: Session = Depends(get_session)
):
    """Étape 2 : Lancement dynamique des agents & enregistrement BDD"""
    final_prompt = (
        f"Demande initiale : {data.user_request}\n"
        f"Type d'exécution : {data.target_workflow}\n"
        f"Précisions apportées : {data.clarifications if data.clarifications else 'Aucune.'}"
    )
    
    try:
        result = await crew_instance.run_dynamic_crew(
            inputs={'user_request': final_prompt},
            request_type=data.target_workflow
        )
        raw_result = str(result.raw) if hasattr(result, 'raw') else str(result)

        # Enregistrement en base de données SQLModel
        db_entry = ExecutionHistory(
            user_request=data.user_request,
            workflow=data.target_workflow,
            clarifications=data.clarifications,
            result=raw_result
        )
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
        raise HTTPException(status_code=500, detail=str(e))

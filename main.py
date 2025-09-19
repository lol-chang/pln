import os

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from typing import Any, Dict, List, Optional

from planner_singleton import FestPlanner
from request_models import PlanRequest
from google_places_singleton import get_google_places_client
from openai_singleton import get_openai_client

from scheduler_module import start_weather_scheduler, stop_weather_scheduler
from scheduler_module import fetch_weather_summary

load_dotenv()

app = FastAPI(title="Travel AI Chatbot")

@app.on_event("startup")
def _startup():
    start_weather_scheduler(app)

@app.on_event("shutdown")
def _shutdown():
    stop_weather_scheduler()


@app.get("/")  # GET /
def read_root():
    return {
        "status": "ok",
        "google_api_key": bool(os.getenv("GOOGLE_API_KEY")),
        "openai_api_key": bool(os.getenv("OPENAI_API_KEY")),
        "KMA_service_key": bool(os.getenv("KMA_SERVICE_KEY")),
    }

@app.post("/plan")
def create_plan(req: PlanRequest):
    try:
        planner = FestPlanner(
            fest_title=req.fest_title,
            fest_location_text=req.fest_location_text,
            travel_needs=req.travel_needs,
            places_client=get_google_places_client(api_key=os.getenv("GOOGLE_API_KEY")),
            openai_client=get_openai_client(api_key=os.getenv("OPENAI_API_KEY")),
        )
        plan = planner.suggest_plan()
        if isinstance(plan, dict) and "error" in plan:
            raise HTTPException(status_code=500, detail=plan["error"])
        return plan
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/weather/summary")
def weather_summary():
    return fetch_weather_summary(92, 131)
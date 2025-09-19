import os

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from typing import Any, Dict, List, Optional

from planner_singleton import FestPlanner
from request_models import PlanRequest
from google_places_singleton import get_google_places_client
from openai_singleton import get_openai_client
from rain_change_proposal import build_rain_change_proposal, apply_user_choices

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
def weather_summary(body: Dict[str, Any] = {}):
    nx = int((body or {}).get("nx", 92))
    ny = int((body or {}).get("ny", 131))
    return fetch_weather_summary(nx, ny)


@app.post("/rain/check")
def rain_check(body: Dict[str, Any] = {}):
    try:
        plan: Dict[str, Any] = body.get("plan") or {}
        if not plan:
            raise HTTPException(status_code=400, detail="plan is required")

        # 입력 파라미터
        center_coords: Optional[str] = body.get("center_coords")
        rainy_dates_input: Optional[List[str]] = body.get("rainy_dates")
        radius_km_for_alt: float = float(body.get("radius_km_for_alt", 5.0))
        indoor_keywords: Optional[List[str]] = body.get("indoor_keywords")
        protect_titles: Optional[List[str]] = body.get("protect_titles")
        top_k: int = int(body.get("top_k", 3))
        max_distance_km: Optional[float] = body.get("max_distance_km")
        if max_distance_km is not None:
            max_distance_km = float(max_distance_km)

        # 비 오는 날짜 자동 계산 (없으면 Cloud Function 호출)
        def _to_iso(d: str) -> str:
            d = str(d)
            return f"{d[:4]}-{d[4:6]}-{d[6:8]}" if (len(d) == 8 and d.isdigit()) else d[:10]

        rainy_dates: List[str]
        if rainy_dates_input:
            rainy_dates = sorted({_to_iso(d) for d in rainy_dates_input})
        else:
            nx = int(body.get("nx", 92))
            ny = int(body.get("ny", 131))
            data = fetch_weather_summary(nx, ny)
            summary = (data or {}).get("summary", {})
            rainy_raw = [d for d, v in summary.items() if (v or {}).get("rain_condition") == 1]
            rainy_dates = sorted({_to_iso(d) for d in rainy_raw})

        places_client = get_google_places_client(api_key=os.getenv("GOOGLE_API_KEY"))
        proposal = build_rain_change_proposal(
            plan,
            places_client,
            is_rainy=bool(rainy_dates),
            center_coords=center_coords,
            rainy_dates=set(rainy_dates),
            protect_titles=set(protect_titles or []),
            radius_km_for_alt=radius_km_for_alt,
            indoor_keywords=indoor_keywords,
            top_k=top_k,
            max_distance_km=max_distance_km,
        )

        return {
            "proposal": proposal,
            "auto_rainy_dates": rainy_dates,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/rain/apply")
def rain_apply(body: Dict[str, Any] = {}):
    try:
        plan: Dict[str, Any] = body.get("plan") or {}
        proposal: Dict[str, Any] = body.get("proposal") or {}
        choices: List[Dict[str, int]] = body.get("choices") or []
        if not plan or not proposal:
            raise HTTPException(status_code=400, detail="plan and proposal are required")
        new_plan = apply_user_choices(plan, proposal, choices)
        return new_plan
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
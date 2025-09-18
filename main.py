import os

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from typing import Any, Dict, List, Optional
import requests
from datetime import datetime
from typing import Optional as _Optional
from apscheduler.schedulers.background import BackgroundScheduler

from planner_core import (
    FestPlanner,
    PlacesClient,
    build_rain_change_proposal,
    apply_user_choices,
    DEFAULT_INDOOR_KWS,
)

load_dotenv()

app = FastAPI(title="Travel AI Chatbot")

@app.get("/")  # GET /
def read_root():
    return {
        "status": "ok",
        "google_api_key": bool(os.getenv("GOOGLE_API_KEY")),
        "openai_api_key": bool(os.getenv("OPENAI_API_KEY")),
        "KMA_service_key": bool(os.getenv("KMA_SERVICE_KEY")),
    }


class PlanRequest(BaseModel):
    fest_title: str = Field(..., description="시작 지점/행사 명칭")
    fest_location_text: str = Field(..., description="시작 지점 주소 또는 명칭")
    travel_needs: Dict[str, Any] = Field(
        ..., description="{start_at,end_at,categories[,budget]}"
    )


@app.post("/plan")
def create_plan(req: PlanRequest):
    try:
        planner = FestPlanner(
            fest_title=req.fest_title,
            fest_location_text=req.fest_location_text,
            travel_needs=req.travel_needs,
        )
        plan = planner.suggest_plan()
        if isinstance(plan, dict) and plan.get("error"):
            raise HTTPException(status_code=500, detail=plan["error"])
        return plan
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ----------------------------
# Cloud Function 연동 및 스케줄러
# ----------------------------
FUNCTION_URL = os.getenv("FUNCTION_URL")
FUNCTION_AUDIENCE = os.getenv("FUNCTION_AUDIENCE", FUNCTION_URL)
DEFAULT_NX = int(os.getenv("DEFAULT_NX", "92"))
DEFAULT_NY = int(os.getenv("DEFAULT_NY", "131"))
FUNCTION_PRIVATE = os.getenv("FUNCTION_PRIVATE", "true").lower() in ("1", "true", "yes")
POLL_MINUTES = int(os.getenv("WEATHER_POLL_MINUTES", "60"))


def fetch_weather_summary(nx: int, ny: int) -> Dict[str, Any]:
    if not FUNCTION_URL:
        raise RuntimeError("FUNCTION_URL가 설정되지 않았습니다.")
    headers = {"Content-Type": "application/json"}
    if FUNCTION_PRIVATE:
        try:
            from google.oauth2 import id_token
            from google.auth.transport.requests import Request as GoogleRequest
            token = id_token.fetch_id_token(GoogleRequest(), FUNCTION_AUDIENCE)
            headers["Authorization"] = f"Bearer {token}"
        except Exception:
            pass
    resp = requests.post(
        FUNCTION_URL,
        json={"nx": nx, "ny": ny},
        headers=headers,
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json()


def extract_rainy_dates(summary: Dict[str, Dict[str, int]]) -> List[str]:
    rainy = [d for d, v in summary.items() if v.get("rain_condition") == 1]
    return sorted(rainy)


def hourly_weather_check_and_propose():
    try:
        data = fetch_weather_summary(DEFAULT_NX, DEFAULT_NY)
        summary = data.get("summary", {})
        rainy_dates = extract_rainy_dates(summary)
        # 콘솔 출력만 수행
        print(f"[weather] {datetime.now()} nx={DEFAULT_NX} ny={DEFAULT_NY}")
        print(f"[weather] rainy_dates_next_3d={rainy_dates}")
        try:
            # summary는 날짜별 rain_condition 맵
            first_keys = sorted(summary.keys())[:4]
            preview = {k: summary[k] for k in first_keys}
            print(f"[weather] summary_preview={preview}")
        except Exception:
            print("[weather] summary keys=", list(summary.keys())[:4])
    except Exception as e:
        print("[hourly_weather_check_and_propose][ERROR]", e)


def get_active_plans() -> List[Dict[str, Any]]:
    return [{
        "fest_title": "춘천 마임축제",
        "fest_location_text": "강원특별자치도 춘천시",
        "travel_needs": {"start_at": "2025-09-20", "end_at": "2025-09-22", "categories": ["관광지", "음식점"]}
    }]


class RainProposalRequest(BaseModel):
    plan: Dict[str, Any]
    is_rainy: bool = True
    center_coords: Optional[str] = None
    rainy_dates: Optional[List[str]] = None
    protect_titles: Optional[List[str]] = None
    radius_km_for_alt: float = 5.0
    indoor_keywords: Optional[List[str]] = None
    top_k: int = 3
    max_distance_km: Optional[float] = None


@app.post("/rain/proposal")
def rain_proposal(req: RainProposalRequest):
    try:
        places = PlacesClient()
        proposal = build_rain_change_proposal(
            plan=req.plan,
            places_client=places,
            is_rainy=req.is_rainy,
            center_coords=req.center_coords,
            rainy_dates=set(req.rainy_dates or []),
            protect_titles=set(req.protect_titles or []),
            radius_km_for_alt=req.radius_km_for_alt,
            indoor_keywords=req.indoor_keywords or DEFAULT_INDOOR_KWS,
            top_k=req.top_k,
            max_distance_km=req.max_distance_km,
        )
        return proposal
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class RainApplyRequest(BaseModel):
    plan: Dict[str, Any]
    proposal: Dict[str, Any]
    choices: List[Dict[str, int]]


@app.post("/rain/apply")
def rain_apply(req: RainApplyRequest):
    try:
        new_plan = apply_user_choices(
            plan=req.plan,
            proposal=req.proposal,
            choices=req.choices,
        )
        return new_plan
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ----------------------------
# 스케줄러 시작/종료 훅
# ----------------------------
scheduler: Optional[BackgroundScheduler] = None

@app.on_event("startup")
def start_scheduler():
    global scheduler
    if FUNCTION_URL:
        scheduler = BackgroundScheduler()
        scheduler.add_job(hourly_weather_check_and_propose, "interval", minutes=POLL_MINUTES, id="weather-poll")
        scheduler.start()
        print(f"[scheduler] started: every {POLL_MINUTES} minutes")
    else:
        print("[scheduler] skipped: FUNCTION_URL not set")


@app.on_event("shutdown")
def stop_scheduler():
    if scheduler:
        scheduler.shutdown()
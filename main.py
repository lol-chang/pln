import os

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from typing import Any, Dict, List, Optional

from planner_singleton import FestPlanner
from request_models import PlanRequest
from google_places_singleton import get_google_places_client
from openai_singleton import get_openai_client
from rain_change_proposal import build_rain_change_proposal, apply_user_choices
from threading import RLock
from llm import decide_replace_indices_gpt

from scheduler_module import start_weather_scheduler, stop_weather_scheduler
from scheduler_module import fetch_weather_summary

load_dotenv()

app = FastAPI(title="Travel AI Chatbot")

# ─────────────────────────────────────────────────────────
# 간단 세션 저장소 (메모리)
# key: session_id → {"plan": plan_dict, "proposal": proposal_dict}
# ─────────────────────────────────────────────────────────
_SESSION_STORE: Dict[str, Dict[str, Any]] = {}
_SESSION_LOCK = RLock()

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
        session_id: Optional[str] = body.get("session_id")
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

        # 비가 안 오면 아무 것도 하지 않음
        if not rainy_dates:
            out = {
                "proposal": None,
                "auto_rainy_dates": [],
                "message": "no rain - no changes"
            }
            # 세션이 있으면 plan만 저장
            if session_id:
                with _SESSION_LOCK:
                    _SESSION_STORE[session_id] = {"plan": plan, "proposal": None}
            return out

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

        # 세션 저장
        if session_id:
            with _SESSION_LOCK:
                _SESSION_STORE[session_id] = {"plan": plan, "proposal": proposal}

        return {"proposal": proposal, "auto_rainy_dates": rainy_dates}


@app.post("/rain/llm-apply")
def rain_llm_apply(body: Dict[str, Any] = {}):
    """
    입력:
      - session_id: string (필수)
      - user_message: string (필수)  예) "두 번째 후보로 바꿔줘"
    동작:
      - 세션에 저장된 plan/proposal을 불러오고
      - proposal.candidates의 각 항목에서 2번째 대체(인덱스 1)를 뽑아 LLM에 판단 요청
      - replace_indices를 choices로 환산하여 apply 후 전체 일정을 반환
    """
    try:
        session_id = (body or {}).get("session_id")
        user_message = (body or {}).get("user_message")
        if not session_id or not user_message:
            raise HTTPException(status_code=400, detail="session_id and user_message are required")

        with _SESSION_LOCK:
            sess = _SESSION_STORE.get(session_id)
        if not sess or not sess.get("plan"):
            raise HTTPException(status_code=404, detail="session not found or plan missing")

        plan = sess["plan"]
        proposal = sess.get("proposal") or {}
        candidates = proposal.get("candidates") or []
        if not candidates:
            # 후보가 없으면 원본 반환
            return plan

        # (original, second_alt) 쌍 만들기
        pairs = []
        for c in candidates:
            alts = c.get("alternatives") or []
            if len(alts) >= 2:
                pairs.append((c.get("original", {}), alts[1]))  # 두 번째 대안 기준
        if not pairs:
            return plan

        replace_indices = decide_replace_indices_gpt(pairs, user_message)
        # replace_indices는 pairs 기준 인덱스이므로, 실제 candidate index로 매핑
        choices: List[Dict[str, int]] = []
        pair_i = 0
        for c in candidates:
            alts = c.get("alternatives") or []
            if len(alts) >= 2:
                if pair_i in replace_indices:
                    choices.append({"index": c.get("index"), "choice": 1})  # 두 번째 대안 적용
                pair_i += 1

        new_plan = apply_user_choices(plan, proposal, choices)

        # 세션에 업데이트 저장
        with _SESSION_LOCK:
            _SESSION_STORE[session_id] = {"plan": new_plan, "proposal": proposal}
        return new_plan
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# @app.post("/rain/apply")
# def rain_apply(body: Dict[str, Any] = {}):
#     try:
#         plan: Dict[str, Any] = body.get("plan") or {}
#         proposal: Dict[str, Any] = body.get("proposal") or {}
#         choices: List[Dict[str, int]] = body.get("choices") or []
#         if not plan or not proposal:
#             raise HTTPException(status_code=400, detail="plan and proposal are required")
#         # 제안에 후보가 없으면 원본 유지
#         if not (proposal.get("candidates") or []):
#             return plan
#         new_plan = apply_user_choices(plan, proposal, choices)
#         return new_plan
#     except HTTPException:
#         raise
#     except Exception as e:
#         raise HTTPException(status_code=500, detail=str(e))
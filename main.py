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
from llm import decide_replace_indices_gpt, decide_alternatives_with_llm

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

# prune_and_attach.py
import copy
from typing import Dict, Any, Optional

# 주차장 관련 함수들
import csv
import math

def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = phi2 - phi1
    dlambda = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))

def _try_float(value) -> Optional[float]:
    try:
        if value is None:
            return None
        s = str(value).strip()
        if not s:
            return None
        return float(s)
    except Exception:
        return None

def parking_info_from_csv_kr(csv_path: str):
    if not os.path.exists(csv_path):
        return []
    rows = []
    try:
        # 여러 인코딩 시도 (한국어 우선)
        encodings = ["euc-kr", "cp949", "utf-8-sig", "utf-8"]
        f = None
        for enc in encodings:
            try:
                f = open(csv_path, "r", encoding=enc, newline="")
                break
            except UnicodeDecodeError:
                continue
        if f is None:
            print(f"[parking] 인코딩 실패: {csv_path}")
            return []
        
        with f:
            reader = csv.DictReader(f)
            for row in reader:
                lat = None
                lng = None
                name = "주차장"
                address = "주소 미상"
                
                for key, value in row.items():
                    key_lower = key.lower().strip()
                    if "위도" in key or "lat" in key_lower:
                        lat = _try_float(value)
                    elif "경도" in key or "lng" in key_lower or "lon" in key_lower:
                        lng = _try_float(value)
                    elif "주차장" in key or "명" in key:
                        name = str(value).strip() or "주차장"
                    elif "주소" in key or "소재" in key:
                        address = str(value).strip() or "주소 미상"
                
                if lat is not None and lng is not None:
                    rows.append({
                        "name": name,
                        "address": address,
                        "lat": lat,
                        "lng": lng,
                    })
        print(f"[parking] 로드된 주차장 수: {len(rows)}")
    except Exception as e:
        print(f"[parking] CSV 로드 실패: {e}")
        return []
    return rows

def attach_parking_to_list(places, parking_rows, top_n=3):
    out = []
    for p in places:
        plat, plng = _try_float(p.get("lat")), _try_float(p.get("lng"))
        candidates = []
        if plat is not None and plng is not None:
            for pr in parking_rows:
                rlat, rlng = pr.get("lat"), pr.get("lng")
                if rlat is None or rlng is None:
                    continue
                dist = _haversine_km(plat, plng, float(rlat), float(rlng))
                candidates.append({
                    "name": pr.get("name"),
                    "address": pr.get("address"),
                    "lat": rlat,
                    "lng": rlng,
                    "distance_km": round(dist, 2),
                })
            candidates.sort(key=lambda x: x["distance_km"])
        out.append({
            **p,
            "parking_candidates": candidates[:top_n] if candidates else []
        })
    return out

def prune_alternatives_and_attach_parking(
    proposal: Dict[str, Any],
    *,
    parking_df: Optional[list],
    choice_index: int = 1,   # 0-based로 두 번째 대안
    top_n_parking: int = 3
) -> Dict[str, Any]:
    """
    - 각 candidate.alternatives에서 choice_index만 남긴다.
    - 선택된 대안(alt)에 parking_candidates를 붙인다.
    - proposal(dict)을 수정한 사본을 반환한다.
    """
    new_prop = copy.deepcopy(proposal)

    candidates = new_prop.get("candidates", [])
    for cand in candidates:
        alts = cand.get("alternatives") or []
        if not alts:
            cand["alternatives"] = []
            continue

        # 선택(인덱스가 범위를 벗어나면 안전하게 첫 번째로 대체)
        sel = alts[choice_index] if choice_index < len(alts) else alts[0]

        # 주차장 붙이기 (lat/lng 없으면 빈 리스트)
        if parking_df is not None and len(parking_df) > 0 and sel.get("lat") is not None and sel.get("lng") is not None:
            enriched = attach_parking_to_list([sel], parking_df, top_n=top_n_parking)
            sel = enriched[0] if enriched else {**sel, "parking_candidates": []}
        else:
            sel = {**sel, "parking_candidates": []}

        # 대안을 선택된 것 하나만 남기기
        cand["alternatives"] = [sel]

    return new_prop

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
        plan = planner.suggest_plan(model="gpt-4o-mini")
        if isinstance(plan, dict) and "error" in plan:
            raise HTTPException(status_code=500, detail=plan["error"])
        
        # 주차장 정보 추가
        parking_csv_path = "강원특별자치도_강릉시_주차장정보_20230828.csv"
        parking_df = None
        if os.path.exists(parking_csv_path):
            parking_df = parking_info_from_csv_kr(parking_csv_path)
        
        # 여행 계획의 각 장소에 주차장 정보 추가
        if parking_df and len(parking_df) > 0 and isinstance(plan, dict):
            itinerary = plan.get("itinerary", [])
            for item in itinerary:
                if item.get("type") == "place" and item.get("lat") is not None and item.get("lng") is not None:
                    enriched = attach_parking_to_list([item], parking_df, top_n=3)
                    if enriched:
                        item.update(enriched[0])
        
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
        prune_choice_index: Optional[int] = body.get("prune_choice_index")
        parking_csv_path: Optional[str] = body.get("parking_csv_path") or "강원특별자치도_강릉시_주차장정보_20230828.csv"
        top_n_parking: int = int(body.get("top_n_parking", 3))
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
                    if session_id not in _SESSION_STORE:
                        _SESSION_STORE[session_id] = {
                            "plan": copy.deepcopy(plan),
                            "proposal": None,
                            "original_plan": copy.deepcopy(plan),
                            "history": []
                        }
                    else:
                        _SESSION_STORE[session_id]["plan"] = copy.deepcopy(plan)
                        _SESSION_STORE[session_id]["proposal"] = None
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

        # 주차장 정보 로드
        parking_df = None
        if parking_csv_path:
            parking_df = parking_info_from_csv_kr(parking_csv_path)

        # 모든 대안에 주차장 정보 추가
        if parking_df and len(parking_df) > 0:
            for candidate in proposal.get("candidates", []):
                alternatives = candidate.get("alternatives", [])
                enriched_alternatives = attach_parking_to_list(alternatives, parking_df, top_n=top_n_parking)
                candidate["alternatives"] = enriched_alternatives

        # 선택 인덱스 기반 대안 정리(옵션)
        if prune_choice_index is not None:
            try:
                ci = int(prune_choice_index)
            except Exception:
                ci = 1
            proposal = prune_alternatives_and_attach_parking(
                proposal,
                parking_df=parking_df,
                choice_index=ci,
                top_n_parking=top_n_parking,
            )

        # 세션 저장 (히스토리 포함)
        if session_id:
            with _SESSION_LOCK:
                if session_id not in _SESSION_STORE:
                    _SESSION_STORE[session_id] = {
                        "plan": copy.deepcopy(plan),
                        "proposal": proposal,
                        "original_plan": copy.deepcopy(plan),
                        "history": []
                    }
                else:
                    _SESSION_STORE[session_id]["plan"] = copy.deepcopy(plan)
                    _SESSION_STORE[session_id]["proposal"] = proposal

        return {"proposal": proposal, "auto_rainy_dates": rainy_dates}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/rain/apply-choice")
def rain_apply_choice(body: Dict[str, Any] = {}):
    """
    특정 대안을 선택해서 적용하는 엔드포인트
    입력:
      - session_id: string (필수)
      - candidate_index: int (필수) - 몇 번째 후보를 변경할지 (0-based)
      - alternative_index: int (필수) - 그 후보의 몇 번째 대안을 선택할지 (0-based)
    """
    try:
        session_id = (body or {}).get("session_id")
        candidate_index = (body or {}).get("candidate_index")
        alternative_index = (body or {}).get("alternative_index")
        
        if not session_id or candidate_index is None or alternative_index is None:
            raise HTTPException(status_code=400, detail="session_id, candidate_index, alternative_index are required")

        with _SESSION_LOCK:
            sess = _SESSION_STORE.get(session_id)
        if not sess or not sess.get("plan"):
            raise HTTPException(status_code=404, detail="session not found or plan missing")

        plan = sess["plan"]
        proposal = sess.get("proposal") or {}
        candidates = proposal.get("candidates") or []
        
        if candidate_index >= len(candidates):
            raise HTTPException(status_code=400, detail="candidate_index out of range")
        
        candidate = candidates[candidate_index]
        alternatives = candidate.get("alternatives") or []
        
        if alternative_index >= len(alternatives):
            raise HTTPException(status_code=400, detail="alternative_index out of range")

        # 선택된 대안 하나만 적용
        choices = [{"index": candidate.get("index"), "choice": alternative_index}]
        new_plan = apply_user_choices(plan, proposal, choices)

        # 세션에 업데이트 저장 (히스토리 포함)
        with _SESSION_LOCK:
            sess = _SESSION_STORE[session_id]
            # 현재 플랜을 히스토리에 추가
            sess["history"].append(copy.deepcopy(sess["plan"]))
            # 새 플랜으로 업데이트
            sess["plan"] = new_plan
        
        return {
            "updated_plan": new_plan,
            "applied_choice": {
                "candidate_index": candidate_index,
                "alternative_index": alternative_index,
                "selected_alternative": alternatives[alternative_index]
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/rain/llm-apply")
def rain_llm_apply(body: Dict[str, Any] = {}):
    """
    LLM이 자연어를 해석해서 대안을 적용하는 엔드포인트
    입력:
      - session_id: string (필수)
      - user_message: string (필수)  예) "두 번째 박물관으로 바꿔줘", "에디슨과학박물관 좋아요"
    동작:
      - 세션에 저장된 plan/proposal을 불러오고
      - 모든 대안을 LLM에 제공하고 사용자 메시지를 해석
      - 선택된 대안들을 적용해서 업데이트된 계획 반환
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
            return {"updated_plan": plan, "applied_choices": []}

        # 개선된 LLM 판단 사용
        selections = decide_alternatives_with_llm(candidates, user_message)
        
        if not selections:
            # 선택된 것이 없으면 원본 유지
            return {"updated_plan": plan, "applied_choices": []}

        # selections를 apply_user_choices 형식으로 변환
        choices: List[Dict[str, int]] = []
        applied_details = []
        
        for sel in selections:
            candidate_idx = sel["candidate_index"]
            alternative_idx = sel["alternative_index"]
            
            if candidate_idx < len(candidates):
                candidate = candidates[candidate_idx]
                alternatives = candidate.get("alternatives", [])
                
                if alternative_idx < len(alternatives):
                    original_index = candidate.get("index")  # 원본 일정에서의 인덱스
                    choices.append({"index": original_index, "choice": alternative_idx})
                    
                    applied_details.append({
                        "candidate_index": candidate_idx,
                        "alternative_index": alternative_idx,
                        "original_title": candidate.get("original", {}).get("title"),
                        "selected_alternative": alternatives[alternative_idx]
                    })

        new_plan = apply_user_choices(plan, proposal, choices)

        # 세션에 업데이트 저장 (히스토리 포함)
        with _SESSION_LOCK:
            sess = _SESSION_STORE[session_id]
            # 현재 플랜을 히스토리에 추가 (변경사항이 있을 때만)
            if applied_details:  # 실제 변경이 있을 때만
                sess["history"].append(copy.deepcopy(sess["plan"]))
            # 새 플랜으로 업데이트
            sess["plan"] = new_plan
        
        return {
            "updated_plan": new_plan, 
            "applied_choices": applied_details,
            "llm_interpretation": user_message
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/rain/rollback")
def rain_rollback(body: Dict[str, Any] = {}):
    """
    이전 플랜 상태로 되돌리기
    입력:
      - session_id: string (필수)
    동작:
      - 세션의 history에서 가장 최근 플랜을 가져와서 현재 플랜으로 복원
      - history가 비어있으면 에러 반환
    """
    try:
        session_id = (body or {}).get("session_id")
        if not session_id:
            raise HTTPException(status_code=400, detail="session_id is required")

        with _SESSION_LOCK:
            sess = _SESSION_STORE.get(session_id)
            if not sess:
                raise HTTPException(status_code=404, detail="session not found")
            
            history = sess.get("history", [])
            if not history:
                raise HTTPException(status_code=400, detail="no history to rollback")
            
            # 가장 최근 히스토리를 현재 플랜으로 복원
            previous_plan = history.pop()  # 히스토리에서 제거하면서 가져오기
            current_plan = sess["plan"]
            
            # 현재 플랜은 히스토리에 추가하지 않음 (롤백이므로)
            sess["plan"] = previous_plan
        
        return {
            "message": "rolled back to previous plan",
            "updated_plan": previous_plan,
            "remaining_history_count": len(history)
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/rain/reset")
def rain_reset(body: Dict[str, Any] = {}):
    """
    원본 플랜으로 완전 리셋
    입력:
      - session_id: string (필수)
    동작:
      - 세션의 original_plan으로 완전히 되돌리기
      - history 모두 삭제
    """
    try:
        session_id = (body or {}).get("session_id")
        if not session_id:
            raise HTTPException(status_code=400, detail="session_id is required")

        with _SESSION_LOCK:
            sess = _SESSION_STORE.get(session_id)
            if not sess:
                raise HTTPException(status_code=404, detail="session not found")
            
            original_plan = sess.get("original_plan")
            if not original_plan:
                raise HTTPException(status_code=400, detail="no original plan to reset to")
            
            # 원본 플랜으로 리셋하고 히스토리 삭제
            sess["plan"] = copy.deepcopy(original_plan)
            sess["history"] = []
            sess["proposal"] = None
        
        return {
            "message": "reset to original plan",
            "updated_plan": sess["plan"]
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/rain/history/{session_id}")
def get_history(session_id: str):
    """
    세션의 플랜 히스토리 조회
    """
    try:
        with _SESSION_LOCK:
            sess = _SESSION_STORE.get(session_id)
            if not sess:
                raise HTTPException(status_code=404, detail="session not found")
            
            return {
                "session_id": session_id,
                "current_plan": sess.get("plan"),
                "original_plan": sess.get("original_plan"),
                "history_count": len(sess.get("history", [])),
                "history": sess.get("history", [])
            }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/rain/chat")
def rain_chat(body: Dict[str, Any] = {}):
    """
    🤖 LLM 통합 채팅 엔드포인트 - 자연어로 모든 기능 제어
    
    입력:
      - session_id: string (필수)
      - user_message: string (필수) - 자연어 명령
      - plan: object (선택) - 새로운 플랜 (처음 시작할 때만)
    
    지원하는 자연어 명령들:
      - "비 오는 날 대안 확인해줘" → /rain/check
      - "두 번째 박물관으로 바꿔줘" → /rain/apply 
      - "이전으로 되돌려줘" → /rain/rollback
      - "처음으로 초기화해줘" → /rain/reset
      - "히스토리 보여줘" → /rain/history
      - "현재 계획 보여줘" → 현재 플랜 출력
    """
    try:
        session_id = (body or {}).get("session_id")
        user_message = (body or {}).get("user_message", "").strip()
        plan = (body or {}).get("plan")
        
        if not session_id or not user_message:
            return {
                "response": "세션 ID와 메시지를 입력해주세요! 😊",
                "action": "error"
            }
        
        # LLM으로 사용자 의도 파악
        intent = _parse_user_intent_with_llm(user_message, session_id, plan)
        
        # 의도에 따른 액션 실행
        result = _execute_intent_action(intent, session_id, user_message, plan)
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        return {
            "response": f"죄송합니다, 오류가 발생했습니다: {str(e)} 😅",
            "action": "error",
            "error": str(e)
        }


def _parse_user_intent_with_llm(user_message: str, session_id: str, plan: Dict = None) -> Dict[str, Any]:
    """
    LLM을 사용해서 사용자의 의도를 파악하는 함수
    """
    from openai_singleton import get_openai_client, llm_json
    
    # 세션 정보 확인
    with _SESSION_LOCK:
        sess = _SESSION_STORE.get(session_id, {})
    
    has_plan = bool(sess.get("plan") or plan)
    has_proposal = bool(sess.get("proposal"))
    history_count = len(sess.get("history", []))
    
    system_prompt = f"""
당신은 여행 계획 관리 AI입니다. 사용자의 자연어 메시지를 분석해서 어떤 액션을 수행할지 판단하세요.

현재 세션 상태:
- 플랜 있음: {has_plan}
- 대안 제안 있음: {has_proposal}
- 히스토리 개수: {history_count}

가능한 액션들:
1. "check" - 비 오는 날 대안 확인 (키워드: 비, 대안, 확인, 날씨, 우천)
2. "apply" - 특정 대안 적용 (키워드: 바꿔, 변경, 선택, 적용, 박물관, 카페 등 구체적 장소명)
3. "rollback" - 이전으로 되돌리기 (키워드: 되돌려, 이전, 취소, 원래대로)
4. "reset" - 처음으로 초기화 (키워드: 초기화, 처음, 원본, 리셋)
5. "history" - 히스토리 보기 (키워드: 히스토리, 기록, 변경사항)
6. "show" - 현재 계획 보기 (키워드: 보여줘, 현재, 계획, 일정)
7. "help" - 도움말 (키워드: 도움, 사용법, 뭘 할 수 있어)

JSON으로 응답하세요:
{{
  "action": "액션명",
  "confidence": 0.0-1.0,
  "reasoning": "판단 근거",
  "extracted_info": {{}} // 추가 정보 (장소명, 인덱스 등)
}}
"""
    
    try:
        openai_client = get_openai_client(api_key=os.getenv("OPENAI_API_KEY"))
        response = llm_json(
            openai_client, 
            system_prompt, 
            f"사용자 메시지: '{user_message}'",
            model="gpt-4o-mini"
        )
        return response
    except Exception as e:
        # LLM 실패시 키워드 기반 fallback
        return _parse_intent_with_keywords(user_message)


def _parse_intent_with_keywords(user_message: str) -> Dict[str, Any]:
    """
    키워드 기반 의도 파악 (LLM 실패시 fallback)
    """
    msg = user_message.lower()
    
    if any(word in msg for word in ["비", "대안", "확인", "날씨", "우천", "체크"]):
        return {"action": "check", "confidence": 0.8, "reasoning": "키워드 매칭: 대안 확인"}
    elif any(word in msg for word in ["바꿔", "변경", "선택", "적용"]):
        return {"action": "apply", "confidence": 0.8, "reasoning": "키워드 매칭: 대안 적용"}
    elif any(word in msg for word in ["되돌려", "이전", "취소", "롤백"]):
        return {"action": "rollback", "confidence": 0.9, "reasoning": "키워드 매칭: 되돌리기"}
    elif any(word in msg for word in ["초기화", "처음", "원본", "리셋"]):
        return {"action": "reset", "confidence": 0.9, "reasoning": "키워드 매칭: 초기화"}
    elif any(word in msg for word in ["히스토리", "기록", "변경사항"]):
        return {"action": "history", "confidence": 0.9, "reasoning": "키워드 매칭: 히스토리"}
    elif any(word in msg for word in ["보여줘", "현재", "계획", "일정"]):
        return {"action": "show", "confidence": 0.8, "reasoning": "키워드 매칭: 현재 계획"}
    else:
        return {"action": "help", "confidence": 0.5, "reasoning": "의도 불분명"}


def _execute_intent_action(intent: Dict[str, Any], session_id: str, user_message: str, plan: Dict = None) -> Dict[str, Any]:
    """
    파악된 의도에 따라 실제 액션을 실행하는 함수
    """
    action = intent.get("action", "help")
    
    try:
        if action == "check":
            return _handle_check_action(session_id, plan)
        elif action == "apply":
            return _handle_apply_action(session_id, user_message)
        elif action == "rollback":
            return _handle_rollback_action(session_id)
        elif action == "reset":
            return _handle_reset_action(session_id)
        elif action == "history":
            return _handle_history_action(session_id)
        elif action == "show":
            return _handle_show_action(session_id)
        else:
            return _handle_help_action()
            
    except Exception as e:
        return {
            "response": f"액션 실행 중 오류가 발생했습니다: {str(e)} 😅",
            "action": "error",
            "error": str(e)
        }


def _handle_check_action(session_id: str, plan: Dict = None) -> Dict[str, Any]:
    """비 오는 날 대안 확인 처리"""
    with _SESSION_LOCK:
        sess = _SESSION_STORE.get(session_id, {})
        current_plan = sess.get("plan") or plan
    
    if not current_plan:
        return {
            "response": "먼저 여행 계획을 제공해주세요! 😊",
            "action": "check",
            "success": False
        }
    
    # rain/check 로직 실행
    body = {
        "session_id": session_id,
        "plan": current_plan,
        "nx": 92, "ny": 131,
        "protect_titles": ["강릉역"],
        "top_n_parking": 3
    }
    
    try:
        # rain_check 함수 직접 호출
        result = rain_check(body)
        proposal = result.get("proposal")
        
        if not proposal:
            return {
                "response": "좋은 소식이에요! 비가 오지 않아서 계획을 변경할 필요가 없습니다! ☀️",
                "action": "check",
                "success": True,
                "data": result
            }
        
        candidates_count = len(proposal.get("candidates", []))
        alternatives_count = sum(len(c.get("alternatives", [])) for c in proposal.get("candidates", []))
        
        # 구체적인 대안 제안 생성
        suggestions_text = _format_alternatives_for_chat(proposal.get("candidates", []))
        
        return {
            "response": f"비 오는 날을 대비한 대안을 찾았습니다! 🌧️\n\n📍 {candidates_count}개 장소에 대해 총 {alternatives_count}개의 실내 대안을 준비했어요.\n\n{suggestions_text}\n\n'박물관으로 바꿔줘', '두 번째 대안으로 해줘' 등으로 말씀해주시면 적용해드릴게요! 😊",
            "action": "check",
            "success": True,
            "data": result
        }
        
    except Exception as e:
        return {
            "response": f"대안 확인 중 오류가 발생했습니다: {str(e)} 😅",
            "action": "check",
            "success": False,
            "error": str(e)
        }


def _handle_apply_action(session_id: str, user_message: str) -> Dict[str, Any]:
    """대안 적용 처리"""
    try:
        # rain/llm-apply 로직 실행
        body = {"session_id": session_id, "user_message": user_message}
        result = rain_llm_apply(body)
        
        applied_choices = result.get("applied_choices", [])
        if not applied_choices:
            return {
                "response": "요청하신 변경사항을 찾을 수 없었어요. 다른 방식으로 말씀해주시겠어요? 🤔\n\n예: '박물관으로 바꿔줘', '첫 번째 대안으로 해줘'",
                "action": "apply",
                "success": False
            }
        
        changes_text = []
        for choice in applied_choices:
            original = choice.get("original_title", "")
            selected = choice.get("selected_alternative", {}).get("title", "")
            changes_text.append(f"• {original} → {selected}")
        
        return {
            "response": f"계획을 변경했습니다! ✅\n\n{chr(10).join(changes_text)}\n\n변경사항이 마음에 들지 않으시면 '되돌려줘'라고 말씀해주세요! 🔄",
            "action": "apply",
            "success": True,
            "data": result
        }
        
    except Exception as e:
        return {
            "response": f"계획 변경 중 오류가 발생했습니다: {str(e)} 😅",
            "action": "apply", 
            "success": False,
            "error": str(e)
        }


def _handle_rollback_action(session_id: str) -> Dict[str, Any]:
    """되돌리기 처리"""
    try:
        body = {"session_id": session_id}
        result = rain_rollback(body)
        
        return {
            "response": f"이전 상태로 되돌렸습니다! 🔄\n\n남은 히스토리: {result.get('remaining_history_count', 0)}개",
            "action": "rollback",
            "success": True,
            "data": result
        }
        
    except HTTPException as e:
        if e.status_code == 400 and "no history" in str(e.detail):
            return {
                "response": "되돌릴 이전 상태가 없습니다. 아직 변경사항이 없거나 이미 최초 상태예요! 😊",
                "action": "rollback",
                "success": False
            }
        else:
            raise
    except Exception as e:
        return {
            "response": f"되돌리기 중 오류가 발생했습니다: {str(e)} 😅",
            "action": "rollback",
            "success": False,
            "error": str(e)
        }


def _handle_reset_action(session_id: str) -> Dict[str, Any]:
    """초기화 처리"""
    try:
        body = {"session_id": session_id}
        result = rain_reset(body)
        
        return {
            "response": "원본 계획으로 완전히 초기화했습니다! 🔄✨\n\n모든 변경사항이 취소되고 처음 상태로 돌아갔어요!",
            "action": "reset",
            "success": True,
            "data": result
        }
        
    except HTTPException as e:
        if e.status_code == 400 and "no original plan" in str(e.detail):
            return {
                "response": "초기화할 원본 계획이 없습니다. 먼저 여행 계획을 만들어주세요! 😊",
                "action": "reset",
                "success": False
            }
        else:
            raise
    except Exception as e:
        return {
            "response": f"초기화 중 오류가 발생했습니다: {str(e)} 😅",
            "action": "reset",
            "success": False,
            "error": str(e)
        }


def _handle_history_action(session_id: str) -> Dict[str, Any]:
    """히스토리 보기 처리"""
    try:
        result = get_history(session_id)
        history_count = result.get("history_count", 0)
        
        if history_count == 0:
            return {
                "response": "아직 변경 기록이 없습니다. 계획을 변경하시면 히스토리가 쌓여요! 📝",
                "action": "history",
                "success": True,
                "data": result
            }
        
        return {
            "response": f"변경 기록을 조회했습니다! 📋\n\n총 {history_count}개의 이전 상태가 저장되어 있어요.\n\n'되돌려줘'로 이전 상태로 돌아갈 수 있습니다!",
            "action": "history",
            "success": True,
            "data": result
        }
        
    except Exception as e:
        return {
            "response": f"히스토리 조회 중 오류가 발생했습니다: {str(e)} 😅",
            "action": "history",
            "success": False,
            "error": str(e)
        }


def _handle_show_action(session_id: str) -> Dict[str, Any]:
    """현재 계획 보기 처리"""
    with _SESSION_LOCK:
        sess = _SESSION_STORE.get(session_id, {})
        current_plan = sess.get("plan")
    
    if not current_plan:
        return {
            "response": "현재 저장된 여행 계획이 없습니다. 먼저 계획을 만들어주세요! 😊",
            "action": "show",
            "success": False
        }
    
    itinerary = current_plan.get("itinerary", [])
    places_count = len(itinerary)
    
    return {
        "response": f"현재 여행 계획을 보여드릴게요! 📋\n\n총 {places_count}개의 장소가 계획되어 있어요.\n\n'비 오는 날 대안 확인해줘'라고 말씀하시면 우천 대비 계획도 준비해드릴게요! 🌧️",
        "action": "show",
        "success": True,
        "data": {"current_plan": current_plan}
    }


def _handle_help_action() -> Dict[str, Any]:
    """도움말 처리"""
    help_text = """
🤖 **여행 계획 AI 도우미 사용법**

다음과 같이 자연어로 말씀해주세요:

🌧️ **비 오는 날 대안 확인**
- "비 오는 날 대안 확인해줘"
- "날씨 나쁠 때 갈 곳 추천해줘"

✅ **계획 변경**
- "박물관으로 바꿔줘"
- "두 번째 대안으로 해줘"
- "경포호 대신 다른 곳으로"

🔄 **되돌리기/초기화**
- "이전으로 되돌려줘"
- "처음으로 초기화해줘"

📋 **정보 확인**
- "현재 계획 보여줘"
- "히스토리 보여줘"

자연스럽게 대화하듯 말씀해주시면 됩니다! 😊
"""
    
    return {
        "response": help_text,
        "action": "help",
        "success": True
    }


def _format_alternatives_for_chat(candidates: List[Dict[str, Any]]) -> str:
    """
    대안들을 채팅용으로 보기 좋게 포맷팅하는 함수
    """
    if not candidates:
        return ""
    
    formatted_parts = []
    
    for i, candidate in enumerate(candidates):
        original_title = candidate.get("original", {}).get("title", "")
        alternatives = candidate.get("alternatives", [])
        
        if not alternatives:
            continue
            
        # 원본 장소명
        formatted_parts.append(f"🎯 **{original_title}** 대신:")
        
        # 각 대안들
        for j, alt in enumerate(alternatives[:3]):  # 최대 3개만 표시
            title = alt.get("title", "")
            rating = alt.get("rating", 0)
            distance = alt.get("distance_km", 0)
            
            rating_stars = "⭐" * int(rating) if rating >= 4 else f"⭐{rating}"
            distance_text = f"({distance:.1f}km)" if distance else ""
            
            formatted_parts.append(f"  {j+1}. **{title}** {rating_stars} {distance_text}")
        
        formatted_parts.append("")  # 빈 줄 추가
    
    return "\n".join(formatted_parts).strip()


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
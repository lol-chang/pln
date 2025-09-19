import json
import math
from typing import Dict, Any, List, Tuple, Optional, Set
from datetime import datetime, timezone, timedelta

# ─────────────────────────────────────────────────────────
# 공통 상수/유틸
# ─────────────────────────────────────────────────────────
KST = timezone(timedelta(hours=9))

# ---------- 파일 I/O ----------
def save_json(path: str, obj: Any) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)

def load_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

# ---------- 키워드/보호 규칙 ----------
DEFAULT_INDOOR_KWS = [
    "박물관","전시","미술관","과학관","도서관","쇼핑몰","아쿠아리움","실내 체험",
    "키즈카페","갤러리","볼링장","VR","만화카페","보드게임","카페","영화관","공연장"
]
DEFAULT_OUTDOOR_KWS = [
    "공원","해변","호수","강","산","정상","야외","산책로","전망대",
    "캠핑","스카이워크","정원","폭포","해수욕장","야외전시","전망"
]
HERITAGE_OUTDOOR_KWS = [
    "고택","한옥","전통가옥","유적","사적","향교","서원","누정","서당",
    "민속촌","고건축","문화재","옛집","고가","고택군","객사","별당","행궁","전통마을","정원","한옥마을"
]
PROTECT_TYPES = {"festival", "parking", "cafe", "restaurant"}
PROTECT_KWS = [
    # 교통/거점 등
    "역","터미널","정거장","환승","공항","항만","항구",
    # 실내 확실
    "박물관","미술관","과학관","도서관","쇼핑몰","아쿠아리움",
    "전시장","컨벤션","센터","체육관","공연장","도청","시청","도서문화센터"
]

def _lc(s: Optional[str]) -> str:
    return (s or "").strip().lower()

def _parse_kst_date(iso_str: Optional[str]) -> Optional[str]:
    if not iso_str:
        return None
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=KST)
        return dt.astimezone(KST).date().isoformat()
    except Exception:
        return iso_str[:10] if len(iso_str or "") >= 10 else None

# ─────────────────────────────────────────────────────────
# 거리/좌표 헬퍼
# ─────────────────────────────────────────────────────────
def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    R = 6371.0
    φ1, φ2 = math.radians(lat1), math.radians(lat2)
    dφ = φ2 - φ1
    dλ = math.radians(lng2 - lng1)
    a = math.sin(dφ/2)**2 + math.cos(φ1)*math.cos(φ2)*math.sin(dλ/2)**2
    return 2 * R * math.asin(math.sqrt(a))

def _to_latlng_str(lat: Optional[float], lng: Optional[float]) -> Optional[str]:
    if lat is None or lng is None:
        return None
    return f"{lat},{lng}"

def _resolve_item_center_coords(
    original: Dict[str, Any],
    places_client: "PlacesClient",
    fallback_center: Optional[str]
) -> Optional[str]:
    """
    피대상(원래 장소)의 좌표를 구해 대안 탐색 중심으로 사용.
    우선순위: (1) item.lat/lng → (2) place_id → (3) title → (4) fallback_center
    """
    # 1) item.lat/lng
    lat = original.get("lat")
    lng = original.get("lng")
    s = _to_latlng_str(lat, lng)
    if s:
        return s

    # 2) place_id → 좌표 (PlacesClient._geocode_place_id가 있을 때만)
    pid = original.get("place_id")
    if pid and hasattr(places_client, "_geocode_place_id"):
        try:
            coords = places_client._geocode_place_id(pid)  # 내부 메서드지만 있으면 사용
            if coords:
                return coords
        except Exception:
            pass

    # 3) 제목으로 좌표 추정
    title = original.get("title")
    if title:
        try:
            coords = places_client.get_coords_from_place_name(title)
            if coords:
                return coords
        except Exception:
            pass

    # 4) 최후 fallback
    return fallback_center

# ─────────────────────────────────────────────────────────
# 보호/실외 판정
# ─────────────────────────────────────────────────────────
def _is_protected(item: Dict[str, Any], is_first: bool, protect_titles: Set[str]) -> Tuple[bool, Optional[str]]:
    if is_first:
        return True, "protected:first_item"
    ty = _lc(item.get("type"))
    if ty in PROTECT_TYPES:
        return True, f"protected:type:{ty}"
    title = item.get("title") or ""
    desc  = item.get("description") or ""
    joined = f"{title} {desc}".lower()
    hit = [kw for kw in PROTECT_KWS if kw.lower() in joined]
    if hit:
        return True, f"protected:keyword:{'|'.join(hit)}"
    if title in protect_titles:
        return True, f"protected:title_exact:{title}"
    return False, None

def _looks_outdoor(item: Dict[str, Any], places_client: "PlacesClient") -> bool:
    """
    Google Place types + 키워드로 실외 추정
    """
    ty = _lc(item.get("type"))
    if ty in {"parking", "cafe", "restaurant"}:
        return False
    title = item.get("title") or ""
    desc  = item.get("description") or ""
    joined = f"{title} {desc} {ty}".lower()

    # Google Place types 힌트
    pid = item.get("place_id")
    if pid:
        try:
            details = places_client.get_place_details(pid) or {}
            types = [t.lower() for t in (details.get("types") or [])]
            if any(t in types for t in [
                "park","campground","zoo","rv_park","natural_feature",
                "tourist_attraction","hindu_temple","mosque","church"
            ]):
                return True
        except Exception:
            pass

    # 키워드 기반
    if any(kw.lower() in joined for kw in DEFAULT_OUTDOOR_KWS):
        return True
    if any(kw.lower() in joined for kw in HERITAGE_OUTDOOR_KWS):
        return True
    return False

# ─────────────────────────────────────────────────────────
# 1) 우천시 교체 후보 수집 (적용 X)
# ─────────────────────────────────────────────────────────
def collect_rain_change_candidates(
    plan: Dict[str, Any],
    places_client: "PlacesClient",
    *,
    is_rainy: bool,
    rainy_dates: Optional[Set[str]] = None,
    protect_titles: Optional[Set[str]] = None
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    반환:
      candidates: [{index, title, date, reason}],
      kept:       [{index, title, reason}]
    """
    itinerary = plan.get("itinerary", [])
    if not is_rainy:
        kept = [{"index": it.get("index"), "title": it.get("title"), "reason": "not_rainy"} for it in itinerary]
        return [], kept

    protect_titles = set(protect_titles or [])
    rainy_dates = set(rainy_dates or [])

    candidates: List[Dict[str, Any]] = []
    kept: List[Dict[str, Any]] = []

    for i, item in enumerate(itinerary):
        date = _parse_kst_date(item.get("start_time"))
        apply_today = (not rainy_dates) or (date in rainy_dates)

        protected, reason = _is_protected(item, is_first=(i == 0), protect_titles=protect_titles)
        if protected:
            kept.append({"index": item.get("index"), "title": item.get("title"), "reason": reason})
            continue

        if apply_today and _looks_outdoor(item, places_client):
            candidates.append({
                "index": item.get("index"),
                "title": item.get("title"),
                "date": date,
                "reason": "rain_outdoor_candidate"
            })
        else:
            kept.append({
                "index": item.get("index"),
                "title": item.get("title"),
                "reason": "kept:not_applicable_or_indoor"
            })
    return candidates, kept

# ─────────────────────────────────────────────────────────
# 2) 후보별 실내 대안 수집 (피대상 좌표 기준, 거리순 정렬)
# ─────────────────────────────────────────────────────────
def fetch_indoor_alternatives(
    places_client: "PlacesClient",
    *,
    center_coords: str,                      # ← 피대상 좌표(필수)
    indoor_keywords: Optional[List[str]] = None,
    radius_km_for_alt: float = 5.0,
    avoid_titles: Optional[Set[str]] = None,
    top_k: int = 3,
    max_distance_km: Optional[float] = None  # 거리 상한(없으면 미적용)
) -> List[Dict[str, Any]]:
    """
    피대상(center_coords)와의 거리 기준으로 가까운 실내 후보 상위 top_k 반환
    반환: [{title, address, place_id, lat, lng, rating, type, distance_km}]
    """
    indoor_keywords = indoor_keywords or DEFAULT_INDOOR_KWS
    avoid_titles = set(avoid_titles or [])
    all_results: List[Dict[str, Any]] = []
    seen_names: Set[str] = set()

    # 중심 좌표 파싱
    try:
        c_lat, c_lng = map(float, center_coords.split(","))
    except Exception:
        return []

    for kw in indoor_keywords:
        raw = places_client.search_places_nearby(
            location=center_coords, keyword=kw, radius_m=int(radius_km_for_alt * 1000)
        )
        for r in raw:
            name = r.get("name") or "정보 없음"
            if name in avoid_titles or name in seen_names:
                continue
            loc = r.get("geometry", {}).get("location", {})
            lat, lng = loc.get("lat"), loc.get("lng")
            if lat is None or lng is None:
                continue
            pid = r.get("place_id")
            details = {}
            if pid:
                try:
                    details = places_client.get_place_details(pid) or {}
                except Exception:
                    details = {}
            dist = _haversine_km(c_lat, c_lng, float(lat), float(lng))
            if (max_distance_km is not None) and (dist > max_distance_km):
                continue
            all_results.append({
                "title": details.get("name", name),
                "address": details.get("formatted_address", r.get("vicinity", "정보 없음")),
                "place_id": pid,
                "lat": lat,
                "lng": lng,
                "rating": details.get("rating", r.get("rating")),
                "type": "place",
                "distance_km": round(dist, 2),
            })
            seen_names.add(name)

    # 거리순 정렬 후 상위 top_k
    all_results.sort(key=lambda x: x["distance_km"])
    return all_results[:top_k]

# ─────────────────────────────────────────────────────────
# 3) 제안 JSON 생성 및 저장 (적용 전 단계)
# ─────────────────────────────────────────────────────────
def build_and_save_rain_change_proposal(
    plan: Dict[str, Any],
    places_client: "PlacesClient",
    *,
    is_rainy: bool,
    center_coords: Optional[str],
    rainy_dates: Optional[Set[str]] = None,
    protect_titles: Optional[Set[str]] = None,
    radius_km_for_alt: float = 5.0,
    indoor_keywords: Optional[List[str]] = None,
    proposal_path: str = "rain_change_proposal.json",
    top_k: int = 3,
    max_distance_km: Optional[float] = None
) -> Dict[str, Any]:
    """
    (적용 전) 교체 후보 + 각 후보별 대안 후보들을 묶어 제안 JSON 파일 저장
    구조:
    {
      "meta": {...},
      "candidates": [
        {
          "index": 3,
          "original": {title, start_time, end_time, description, type, place_id, lat, lng},
          "alternatives": [{title, address, place_id, lat, lng, rating, type, distance_km}, ...]
        }, ...
      ],
      "kept": [{index, title, reason}, ...]
    }
    """
    candidates, kept = collect_rain_change_candidates(
        plan, places_client,
        is_rainy=is_rainy,
        rainy_dates=rainy_dates,
        protect_titles=protect_titles
    )

    existing_titles = {str(it.get("title", "")) for it in plan.get("itinerary", [])}
    proposal_items: List[Dict[str, Any]] = []

    if is_rainy:
        for c in candidates:
            idx = c["index"]
            original = next((it for it in plan.get("itinerary", []) if it.get("index") == idx), None)
            if not original:
                continue

            # ✅ 피대상(원래 장소)의 좌표를 우선 해석
            victim_center = _resolve_item_center_coords(original, places_client, center_coords)
            if not victim_center:
                # 좌표를 못 구하면 이 후보는 스킵하거나, fallback center 사용하려면 아래 주석 해제
                # victim_center = center_coords
                continue

            alternatives = fetch_indoor_alternatives(
                places_client,
                center_coords=victim_center,                 # 후보별 중심 좌표!
                indoor_keywords=indoor_keywords,
                radius_km_for_alt=radius_km_for_alt,
                avoid_titles=existing_titles,
                top_k=top_k,
                max_distance_km=max_distance_km
            )

            proposal_items.append({
                "index": idx,
                "original": {
                    "title": original.get("title"),
                    "start_time": original.get("start_time"),
                    "end_time": original.get("end_time"),
                    "description": original.get("description"),
                    "type": original.get("type"),
                    "place_id": original.get("place_id"),
                    "lat": original.get("lat"),
                    "lng": original.get("lng"),
                },
                "alternatives": alternatives
            })

    proposal = {
        "meta": {
            "generated_at": datetime.now(KST).isoformat(),
            "is_rainy": is_rainy,
            "rainy_dates": sorted(list(rainy_dates or [])),
            "fallback_center_coords": center_coords,
            "radius_km_for_alt": radius_km_for_alt,
            "top_k": top_k,
            "max_distance_km": max_distance_km
        },
        "candidates": proposal_items,
        "kept": kept
    }
    save_json(proposal_path, proposal)
    return proposal

# ─────────────────────────────────────────────────────────
# (선택 사항) 적용 단계: 사용자가 고른 선택을 반영
# ─────────────────────────────────────────────────────────
def apply_user_choices(
    plan: Dict[str, Any],
    proposal: Dict[str, Any],
    choices: List[Dict[str, int]],  # 예: [{"index": 3, "choice": 1}, {"index": 5, "choice": 0}]
    *,
    output_path: Optional[str] = None
) -> Dict[str, Any]:
    """
    제안(proposal)에서 사용자가 고른 대안을 plan에 반영.
    choice = -1 이면 교체하지 않음(원본 유지).
    """
    index_to_choice = {c["index"]: c["choice"] for c in choices}
    idx_to_alts = {c["index"]: c["alternatives"] for c in proposal.get("candidates", [])}
    new_plan = {"itinerary": [], "totals": dict(plan.get("totals", {}))}

    for item in plan.get("itinerary", []):
        idx = item.get("index")
        if idx in index_to_choice and idx in idx_to_alts:
            choice = index_to_choice[idx]
            if choice is not None and choice >= 0:
                alts = idx_to_alts[idx]
                if 0 <= choice < len(alts):
                    alt = alts[choice]
                    replaced = {
                        **item,
                        "type": alt.get("type", "place") if item.get("type") not in {"cafe", "restaurant"} else item.get("type"),
                        "title": alt.get("title"),
                        "description": f"우천 대안 적용 · 주소: {alt.get('address')}",
                        "place_id": alt.get("place_id"),
                        "lat": alt.get("lat"),
                        "lng": alt.get("lng"),
                        "rating": alt.get("rating"),
                    }
                    new_plan["itinerary"].append(replaced)
                    continue
            # choice == -1 또는 유효하지 않으면 원본 유지
        new_plan["itinerary"].append(item)

    # index 재정렬(원본 index 유지하고 싶으면 주석 처리)
    for i, it in enumerate(new_plan["itinerary"], start=1):
        it["index"] = i

    if output_path:
        save_json(output_path, new_plan)
    return new_plan

# ─────────────────────────────────────────────────────────
# 사용 예 (주석)
# ─────────────────────────────────────────────────────────
# 1) 현재 결과 저장
# save_json("cur_result.json", result1)
#
# 2) 교체 제안 JSON 생성/저장 (적용 전, LLM 없음)
# proposal = build_and_save_rain_change_proposal(
#     result1,
#     planner.places,
#     is_rainy=True,
#     center_coords=planner.fest_location,     # 축제/거점 좌표(후보별 좌표가 우선 사용됨)
#     rainy_dates={"2025-08-20"},              # 특정 날짜만 비오면 제한
#     protect_titles={"강릉역"},                # 필요시 보호 타이틀
#     radius_km_for_alt=5.0,
#     indoor_keywords=DEFAULT_INDOOR_KWS,
#     proposal_path="rain_change_proposal.json",
#     top_k=3,
#     max_distance_km=3.0


import json
from pathlib import Path
from typing import List, Tuple, Dict, Any

def extract_second_alternatives(json_path: str) -> List[Tuple[Dict[str, Any], Dict[str, Any]]]:
    """
    주어진 rain_change_proposal.json에서 각 candidate의 2번째 대체 후보만 뽑아서
    (original, second_alternative) 쌍의 리스트를 반환.

    Args:
        json_path (str): 입력 JSON 파일 경로

    Returns:
        List[Tuple[Dict[str, Any], Dict[str, Any]]]:
            [
              ( {original place dict}, {2번째 대체 dict} ),
              ...
            ]
    """
    path = Path(json_path)
    if not path.exists():
        raise FileNotFoundError(f"파일을 찾을 수 없음: {json_path}")

    data = json.loads(path.read_text(encoding="utf-8"))
    results: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []

    for candidate in data.get("candidates", []):
        alts = candidate.get("alternatives", []) or []
        if len(alts) >= 2:
            original = candidate.get("original", {})
            second_alt = alts[1]  # 0-based index → 두 번째
            results.append((original, second_alt))

    return results



import os
import json
from typing import List, Tuple, Dict, Any, Optional
from openai import OpenAI

def decide_replace_indices_gpt(
    pairs: List[Tuple[Dict[str, Any], Dict[str, Any]]],
    user_message: str,
    *,
    model: str = "gpt-4o-mini",
    api_key: Optional[str] = None,
) -> List[int]:
    """
    GPT에게 판단을 맡겨 교체할 인덱스 리스트를 받는다.
    반환 형식: [0, 1, ...]
    """
    key = api_key or os.getenv("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("OPENAI_API_KEY가 설정되어 있지 않습니다.")
    client = OpenAI(api_key=key)

    # 최소 정보만 전달
    compact_pairs = []
    for i, (orig, alt) in enumerate(pairs):
        compact_pairs.append({
            "index": i,
            "original_title": (orig or {}).get("title"),
            "alternative_title": (alt or {}).get("title"),
        })

    system_prompt = (
        "역할: 일정 대체 판단기.\n"
        "입력은 (index, original_title, alternative_title) 목록과 사용자 메시지다.\n"
        "네 출력은 오직 JSON 하나이며 {'replace_indices':[...]} 형식만 허용된다.\n"
        "0은 첫번째, 1은 두번째. '둘 다' → [0,1], '처음만' → [0], '변경 없음' → []."
    )

    user_payload = {
        "pairs": compact_pairs,
        "user_message": user_message,
    }

    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)}
        ],
        response_format={"type": "json_object"},  # ✅ Chat Completions에서는 이게 지원됨
    )

    out_text = resp.choices[0].message.content
    try:
        data = json.loads(out_text)
        indices = data.get("replace_indices", [])
        indices = [int(i) for i in indices if isinstance(i, int) and 0 <= i < len(compact_pairs)]
        return sorted(set(indices))
    except Exception:
        return []


def decide_alternatives_with_llm(
    candidates: List[Dict[str, Any]],
    user_message: str,
    *,
    model: str = "gpt-4o-mini",
    api_key: Optional[str] = None,
) -> List[Dict[str, int]]:
    """
    개선된 LLM 판단 함수: 모든 후보와 대안을 LLM에 제공하고 사용자 메시지 해석
    
    Args:
        candidates: proposal의 candidates 배열
        user_message: 사용자의 자연어 메시지
        
    Returns:
        List[Dict[str, int]]: [{"candidate_index": 0, "alternative_index": 1}, ...]
    """
    key = api_key or os.getenv("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("OPENAI_API_KEY가 설정되어 있지 않습니다.")
    client = OpenAI(api_key=key)

    # 후보와 대안 정보를 구조화
    structured_candidates = []
    for i, candidate in enumerate(candidates):
        original = candidate.get("original", {})
        alternatives = candidate.get("alternatives", [])
        
        structured_alternatives = []
        for j, alt in enumerate(alternatives):
            structured_alternatives.append({
                "index": j,
                "title": alt.get("title", ""),
                "address": alt.get("address", ""),
                "rating": alt.get("rating", 0),
                "distance_km": alt.get("distance_km", 0)
            })
        
        structured_candidates.append({
            "candidate_index": i,
            "original_title": original.get("title", ""),
            "alternatives": structured_alternatives
        })

    system_prompt = """역할: 여행 일정 대안 선택 전문가

당신은 사용자의 자연어 메시지를 해석해서 어떤 대안을 선택할지 결정합니다.

입력 형식:
- candidates: 각 후보별 원본 장소와 대안들
- user_message: 사용자의 요청 메시지

출력 형식: JSON만 허용
{"selections": [{"candidate_index": 숫자, "alternative_index": 숫자}, ...]}

예시:
- "두 번째 박물관으로 바꿔줘" → 박물관 관련 대안 중 두 번째 선택
- "에디슨과학박물관 좋아요" → 해당 이름의 대안 선택
- "첫 번째 것으로 해주세요" → 첫 번째 대안 선택
- "다 바꿔주세요" → 모든 후보의 첫 번째 대안 선택
- "안 바꿀래요" → 빈 배열 반환

주의사항:
- candidate_index는 0부터 시작 (0, 1, 2...)
- alternative_index는 0부터 시작 (0, 1, 2...)
- 명확하지 않으면 가장 적절한 선택을 추론
- 존재하지 않는 인덱스는 선택하지 마세요"""

    user_payload = {
        "candidates": structured_candidates,
        "user_message": user_message,
    }

    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)}
            ],
            response_format={"type": "json_object"},
        )

        out_text = resp.choices[0].message.content
        data = json.loads(out_text)
        selections = data.get("selections", [])
        
        # 유효성 검증
        valid_selections = []
        for sel in selections:
            if isinstance(sel, dict):
                candidate_idx = sel.get("candidate_index")
                alternative_idx = sel.get("alternative_index")
                
                if (isinstance(candidate_idx, int) and isinstance(alternative_idx, int) and
                    0 <= candidate_idx < len(candidates)):
                    candidate = candidates[candidate_idx]
                    alternatives = candidate.get("alternatives", [])
                    if 0 <= alternative_idx < len(alternatives):
                        valid_selections.append({
                            "candidate_index": candidate_idx,
                            "alternative_index": alternative_idx
                        })
        
        return valid_selections
        
    except Exception as e:
        print(f"[LLM] 대안 선택 실패: {e}")
        return []
# rain_change_proposal.py
import json
import math
from typing import Dict, Any, List, Tuple, Optional, Set
from datetime import datetime, timezone, timedelta

# ─────────────────────────────────────────────────────────
# 공통 상수/유틸
# ─────────────────────────────────────────────────────────
KST = timezone(timedelta(hours=9))

def save_json(path: str, obj: Any) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)

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
# 분류 키워드/보호 규칙
# ─────────────────────────────────────────────────────────
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
    "역","터미널","정거장","환승","공항","항만","항구",
    "박물관","미술관","과학관","도서관","쇼핑몰","아쿠아리움",
    "전시장","컨벤션","센터","체육관","공연장","도청","시청","도서문화센터"
]

# ← Google Places details.types 에서 실외로 간주할 타입 세트(확장 가능)
OUTDOOR_PLACE_TYPES = {
    "park", "campground", "zoo", "rv_park",
    "natural_feature", "tourist_attraction",
    "amusement_park",
}

# ← 유적/누각류 고유명 패턴(경포대/○○루/○○각/○○문/정자/전망대 등)
HERITAGE_SUFFIX_CHARS = ["대", "루", "각", "문"]  # 한 글자 접미사
HERITAGE_TOKENS = [
    "정자", "누각", "문루", "전망대",
    "서원", "향교", "사적", "유적", "고분",
    "성곽", "산성", "읍성", "궁", "고궁",
    "정원", "후원", "별서", "종묘", "사직",
    "탑", "비"
]

def _title_looks_heritage(title: str) -> bool:
    t = (title or "").strip()
    if len(t) <= 1:
        return False
    if any(t.endswith(suf) for suf in HERITAGE_SUFFIX_CHARS):
        return True
    if any(tok in t for tok in HERITAGE_TOKENS):
        return True
    return False

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

# ─────────────────────────────────────────────────────────
# 싱글톤 클라이언트용 좌표 해석 (우선순위: lat/lng → place_id → title → fallback)
# type: "GooglePlacesClient" 는 문자열로만 사용(런타임 의존성 X)
# ─────────────────────────────────────────────────────────
def _resolve_item_center_coords(
    original: Dict[str, Any],
    places_client: "GooglePlacesClient",
    fallback_center: Optional[str]
) -> Optional[str]:
    s = _to_latlng_str(original.get("lat"), original.get("lng"))
    if s:
        return s

    pid = original.get("place_id")
    if pid and hasattr(places_client, "geocode_place_id"):
        try:
            coords = places_client.geocode_place_id(pid)
            if coords:
                return coords
        except Exception:
            pass

    title = original.get("title")
    if title:
        try:
            coords = places_client.get_coords_from_place_name(title)
            if coords:
                return coords
        except Exception:
            pass

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

def _looks_outdoor(item: Dict[str, Any], places_client: "GooglePlacesClient") -> bool:
    """
    Google Place details.types + 제목 패턴 + 키워드 기반 실외 추정(강화판).
    """
    ty = _lc(item.get("type"))
    if ty in {"parking", "cafe", "restaurant"}:
        return False

    title = item.get("title") or ""
    desc  = item.get("description") or ""
    joined = f"{title} {desc} {ty}".lower()

    # 0) 제목이 전형적인 유적/정원/누각 패턴이면 실외로 본다
    if _title_looks_heritage(title):
        return True

    # 1) place_id 또는 제목→place_id 해상 후 details.types 확인
    pid = item.get("place_id")
    if not pid:
        try:
            pid = places_client.find_place_id(title)
        except Exception:
            pid = None

    if pid:
        try:
            details = places_client.get_place_details(pid) or {}
            types = [t.lower() for t in (details.get("types") or [])]
            if any(t in types for t in OUTDOOR_PLACE_TYPES):
                return True
        except Exception:
            pass

    # 2) 키워드 보조 신호
    if any(kw.lower() in joined for kw in DEFAULT_OUTDOOR_KWS):
        return True
    if any(kw.lower() in joined for kw in HERITAGE_OUTDOOR_KWS):
        return True

    return False

# ─────────────────────────────────────────────────────────
# 1) 우천시 교체 후보 수집
# ─────────────────────────────────────────────────────────
def collect_rain_change_candidates(
    plan: Dict[str, Any],
    places_client: "GooglePlacesClient",
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
# 2) 후보별 실내 대안 수집 (거리순 정렬)
# ─────────────────────────────────────────────────────────
def fetch_indoor_alternatives(
    places_client: "GooglePlacesClient",
    *,
    center_coords: str,
    indoor_keywords: Optional[List[str]] = None,
    radius_km_for_alt: float = 5.0,
    avoid_titles: Optional[Set[str]] = None,
    top_k: int = 3,
    max_distance_km: Optional[float] = None
) -> List[Dict[str, Any]]:
    """
    center_coords 기준 가까운 실내 후보 상위 top_k 반환
    반환: [{title, address, place_id, lat, lng, rating, type, distance_km}]
    """
    indoor_keywords = indoor_keywords or DEFAULT_INDOOR_KWS
    avoid_titles = set(avoid_titles or [])
    all_results: List[Dict[str, Any]] = []
    seen_names: Set[str] = set()

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

    all_results.sort(key=lambda x: x["distance_km"])
    return all_results[:top_k]

# ─────────────────────────────────────────────────────────
# 3) 제안 생성 (파일 저장 X, 옵션으로만 저장)
# ─────────────────────────────────────────────────────────
def build_rain_change_proposal(
    plan: Dict[str, Any],
    places_client: "GooglePlacesClient",
    *,
    is_rainy: bool,
    center_coords: Optional[str],
    rainy_dates: Optional[Set[str]] = None,
    protect_titles: Optional[Set[str]] = None,
    radius_km_for_alt: float = 5.0,
    indoor_keywords: Optional[List[str]] = None,
    top_k: int = 3,
    max_distance_km: Optional[float] = None,
    save_to: Optional[str] = None,
) -> Dict[str, Any]:

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

            victim_center = _resolve_item_center_coords(original, places_client, center_coords)
            if not victim_center:
                continue

            alternatives = fetch_indoor_alternatives(
                places_client,
                center_coords=victim_center,
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

    if save_to:
        save_json(save_to, proposal)

    return proposal

# ─────────────────────────────────────────────────────────
# 4) (선택) 사용자 선택 적용
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
        new_plan["itinerary"].append(item)

    for i, it in enumerate(new_plan["itinerary"], start=1):
        it["index"] = i

    if output_path:
        save_json(output_path, new_plan)
    return new_plan

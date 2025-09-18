import os
import json
import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple, Set

import requests
from openai import OpenAI


# ==========================
# 데이터 모델
# ==========================
@dataclass
class Place:
    name: str
    address: str
    category: List[str]
    rating: Optional[float]
    lat: float
    lng: float
    operating_hours: List[str]
    place_id: Optional[str] = None


class GoogleAPIError(Exception):
    pass


class PlacesClient:
    def __init__(self, api_key: Optional[str] = None, language: str = "ko"):
        self.api_key = api_key or os.getenv("GOOGLE_API_KEY")
        if not self.api_key:
            raise ValueError("GOOGLE_API_KEY가 설정되지 않았습니다.")
        self.language = language

    def _looks_too_generic(self, addr: str) -> bool:
        if not addr:
            return True
        generic_tokens = ["대한민국", "강원", "강릉시", "Gangneung-si", "Korea"]
        if addr.count(",") <= 0 and any(t in addr for t in generic_tokens):
            return True
        return False

    def _reverse_geocode(self, lat: float, lng: float) -> Optional[str]:
        url = "https://maps.googleapis.com/maps/api/geocode/json"
        params = {
            "latlng": f"{lat},{lng}",
            "key": self.api_key,
            "language": self.language,
            "region": "kr",
            "result_type": "street_address|premise|point_of_interest",
        }
        try:
            r = requests.get(url, params=params, timeout=10)
            r.raise_for_status()
            results = r.json().get("results", [])
            if not results:
                return None
            return results[0].get("formatted_address")
        except requests.exceptions.RequestException:
            return None

    def _find_place_id(self, place_name: str) -> str:
        url = "https://maps.googleapis.com/maps/api/place/findplacefromtext/json"
        params = {
            "input": place_name,
            "inputtype": "textquery",
            "key": self.api_key,
            "language": self.language,
            "region": "kr",
            "fields": "place_id",
        }
        try:
            r = requests.get(url, params=params, timeout=10)
            r.raise_for_status()
            candidates = r.json().get("candidates", [])
            return candidates[0]["place_id"] if candidates else ""
        except requests.exceptions.RequestException as e:
            raise GoogleAPIError(f"findplacefromtext 실패: {e}") from e

    def _geocode_place_id(self, place_id: str) -> Optional[str]:
        url = "https://maps.googleapis.com/maps/api/geocode/json"
        params = {
            "place_id": place_id,
            "key": self.api_key,
            "language": self.language,
            "region": "kr",
        }
        try:
            r = requests.get(url, params=params, timeout=10)
            r.raise_for_status()
            results = r.json().get("results", [])
            if not results:
                return None
            loc = results[0]["geometry"]["location"]
            return f"{loc['lat']},{loc['lng']}"
        except requests.exceptions.RequestException as e:
            raise GoogleAPIError(f"geocode 실패: {e}") from e

    def get_coords_from_place_name(self, place_name: str) -> str:
        place_id = self._find_place_id(place_name)
        if not place_id:
            return ""
        coords = self._geocode_place_id(place_id)
        return coords or ""

    def get_place_details(self, place_id: str) -> Dict[str, Any]:
        url = "https://maps.googleapis.com/maps/api/place/details/json"
        params = {
            "place_id": place_id,
            "fields": (
                "name,formatted_address,address_components,adr_address,plus_code,"
                "rating,opening_hours,vicinity,geometry,types"
            ),
            "key": self.api_key,
            "language": self.language,
            "region": "kr",
        }
        try:
            r = requests.get(url, params=params, timeout=10)
            r.raise_for_status()
            return r.json().get("result", {}) or {}
        except requests.exceptions.RequestException as e:
            raise GoogleAPIError(f"place details 실패: {e}") from e

    def search_places_nearby(self, location: str, keyword: str, radius_m: int = 10000) -> List[Dict[str, Any]]:
        url = "https://maps.googleapis.com/maps/api/place/nearbysearch/json"
        params = {
            "location": location,
            "keyword": keyword,
            "radius": radius_m,
            "key": self.api_key,
            "language": self.language,
        }
        try:
            r = requests.get(url, params=params, timeout=10)
            r.raise_for_status()
            return r.json().get("results", []) or []
        except requests.exceptions.RequestException as e:
            raise GoogleAPIError(f"nearbysearch 실패: {e}") from e

    def _expand_categories(self, categories: List[str]) -> List[str]:
        mapping = {
            "카페": ["카페", "디저트", "베이커리"],
            "맛집": ["맛집", "식당", "로컬 맛집", "현지 맛집"],
            "관광": ["관광", "명소", "랜드마크", "볼거리", "투어"],
            "전시": ["전시", "미술관", "갤러리", "아트"],
            "박물관": ["박물관", "뮤지엄"],
            "정원": ["정원", "가든", "수목원", "식물원"],
            "한옥": ["한옥", "고택", "전통가옥", "사적", "유적", "향교", "서원"],
            "자연경관": ["자연경관", "전망대", "해변", "호수", "폭포", "산책로"],
            "체험": ["체험", "공방", "클래스", "체험관"],
            "쇼핑": ["쇼핑", "시장", "아울렛", "상점가"],
        }
        out: List[str] = []
        for c in categories or []:
            c = str(c).strip()
            if not c:
                continue
            out.extend(mapping.get(c, [c]))
        seen = set()
        uniq = [x for x in out if not (x in seen or seen.add(x))]
        return uniq

    def find_near_places(self, fest_location: str, keywords: Optional[List[str]] = None, radius_m: int = 10000) -> List[Place]:
        expanded_keywords = self._expand_categories(keywords or [])
        if not expanded_keywords:
            expanded_keywords = ["관광"]

        results: List[Place] = []
        for kw in expanded_keywords:
            try:
                raw = self.search_places_nearby(location=fest_location, keyword=kw, radius_m=radius_m)
            except GoogleAPIError:
                continue

            for r in raw:
                try:
                    loc = r.get("geometry", {}).get("location", {})
                    lat, lng = loc.get("lat"), loc.get("lng")
                    if lat is None or lng is None:
                        continue

                    details = {}
                    pid = r.get("place_id")
                    if pid:
                        try:
                            details = self.get_place_details(pid) or {}
                        except GoogleAPIError:
                            details = {}

                    address = (
                        details.get("formatted_address")
                        or details.get("vicinity")
                        or r.get("vicinity")
                        or ""
                    )

                    if self._looks_too_generic(address):
                        rg = self._reverse_geocode(lat, lng)
                        if rg:
                            address = rg

                    results.append(
                        Place(
                            name=details.get("name", r.get("name", "정보 없음")),
                            address=address or "정보 없음",
                            category=r.get("types") or ["정보 없음"],
                            rating=details.get("rating", r.get("rating")),
                            lat=lat,
                            lng=lng,
                            operating_hours=details.get("opening_hours", {}).get("weekday_text", ["정보 없음"]),
                            place_id=pid,
                        )
                    )
                except Exception:
                    continue

        return results

    def resolve_place_by_title(self, title: str) -> Optional[Dict[str, Any]]:
        try:
            pid = self._find_place_id(title)
            if not pid:
                return None
            details = self.get_place_details(pid) or {}

            lat = None
            lng = None
            try:
                geom = details.get("geometry", {}) or {}
                loc = geom.get("location", {}) or {}
                lat = loc.get("lat")
                lng = loc.get("lng")
            except Exception:
                pass
            if lat is None or lng is None:
                coords = self._geocode_place_id(pid)
                if coords:
                    try:
                        lat_str, lng_str = coords.split(",")
                        lat = float(lat_str)
                        lng = float(lng_str)
                    except Exception:
                        pass

            addr = details.get("formatted_address") or details.get("vicinity") or ""
            if (lat is not None and lng is not None) and self._looks_too_generic(addr):
                rg = self._reverse_geocode(lat, lng)
                if rg:
                    addr = rg

            return {
                "place_id": pid,
                "address": addr,
                "lat": lat,
                "lng": lng,
                "rating": details.get("rating"),
            }
        except Exception:
            return None


# ==========================
# 플래너
# ==========================

class FestPlanner:
    """
    - 체류시간 제약 X
    - 숙소/주차장 미고려
    - 이동수단/경로 최적화 미고려
    - OpenAI 응답 호출 포함
    """

    def __init__(self, fest_title: str, fest_location_text: str, travel_needs: Dict[str, Any], places_client: Optional[PlacesClient] = None):
        self.fest_title = fest_title
        self.travel_needs = self._normalize_needs(travel_needs)
        self.places = places_client or PlacesClient()
        self.fest_location = self.places.get_coords_from_place_name(fest_location_text)
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OPENAI_API_KEY가 설정되지 않았습니다.")
        self.client = OpenAI(api_key=api_key)

    def _normalize_needs(self, needs: Dict[str, Any]) -> Dict[str, Any]:
        if "budget" not in needs and "burget" in needs:
            needs["budget"] = needs.pop("burget")
        required = ["start_at", "end_at", "categories"]
        for k in required:
            if k not in needs:
                raise ValueError(f"travel_needs에 '{k}'가 필요합니다.")
        if not isinstance(needs["categories"], list):
            needs["categories"] = [str(needs["categories"])]
        return needs

    def find_places_in_categories(self, categories: List[str], radius_km: int = 10) -> List[Place]:
        if not self.fest_location:
            return []
        radius_m = max(1000, int(radius_km * 1000))
        return self.places.find_near_places(self.fest_location, keywords=categories, radius_m=radius_m)

    def build_prompt(self, nearby_places: Optional[List[Place]] = None) -> str:
        snippets = []
        for p in (nearby_places or [])[:20]:
            cat = ", ".join(p.category[:3])
            snippets.append(f"- {p.name} | {cat} | 평점:{p.rating} | {p.address}")
        places_block = "\n".join(snippets) if snippets else "(근처 후보 없음)"

        start_at = self.travel_needs["start_at"]
        end_at = self.travel_needs["end_at"]
        categories = ", ".join(self.travel_needs["categories"])
        budget = self.travel_needs.get("budget", "미지정")

        user_prompt = f"""
역할: 여행 플래너

입력 정보
- 시작지: {self.fest_title}
- 시작정보(위도,경도): {self.fest_location}  # 예: "37.1234,127.5678"
- 여행 기간(시작~종료, KST ISO8601): {start_at} ~ {end_at}
- 추가 고려 옵션:
  - 최대 예산: {budget}
  - 희망 여행 컨셉(참고용): {categories}

참고용 주변 장소(최대 20개)
{places_block}

요구사항
1) 내부 자료와 웹 서칭을 통해 주변 장소 후보를 탐색하고, 검증 가능한 대표 정보(명칭·카테고리·대략적 평판/특징)를 근거로 선정할 것.
2) 반드시 처음은 시작 위치일 것
3) 이동 시간을 현실적으로 반영하되, 특정 이동수단이나 경로 최적화는 고려하지 말 것.
4) 예산 제약은 반드시 준수하고, 희망 여행 컨셉은 참고만 할 것.
5) 숙소와 주차장은 고려하지 말 것(추천·배치·유형 사용 금지).
6) 모든 시간은 KST ISO8601 형식으로 기입할 것(예: 2025-08-19T10:00:00+09:00).
7) 장소 유형(type)은 다음 중 하나로만 사용: festival, place, cafe, restaurant.
8) 일정이 의미적으로 중복되는 것은 피하기
9) 결과는 JSON만 출력하고, 그 외 설명/텍스트는 포함하지 말 것.
10) 장소만 적을 것 (강릉 여행 종료 이런거 금지)

출력 스키마 예시
{{
  "itinerary": [
    {{
      "index": 1,
      "type": "festival",
      "title": "{self.fest_title}",
      "start_time": "2025-08-19T10:00:00+09:00",
      "end_time": "2025-08-19T11:30:00+09:00",
      "description": "행사장 중심 활동"
    }},
    {{
      "index": 2,
      "type": "place",
      "title": "소양강 스카이워크",
      "start_time": "2025-08-19T11:30:00+09:00",
      "end_time": "2025-08-19T12:00:00+09:00",
      "description": "주변 추천지"
    }}
  ],
  "totals": {{
    "estimated_cost_krw": 0,
    "estimated_travel_time_minutes": 0
  }}
}}
"""
        return user_prompt.strip()

    # ✅ LLM 결과 후처리: address/lat/lng/place_id/rating 보강 (+역지오코딩 보정)
    def _enrich_itinerary_with_place_info(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        enriched: List[Dict[str, Any]] = []
        for item in items:
            has_address = bool(item.get("address"))
            has_latlng = item.get("lat") is not None and item.get("lng") is not None

            if has_address and has_latlng and item.get("place_id"):
                # 주소가 러프하면 한 번 더 보정
                if self.places._looks_too_generic(str(item.get("address", ""))) and has_latlng:
                    rg = self.places._reverse_geocode(float(item["lat"]), float(item["lng"]))
                    if rg:
                        item["address"] = rg
                enriched.append(item)
                continue

            title = item.get("title") or ""
            info = None

            # place_id 우선 보강
            if item.get("place_id"):
                try:
                    details = self.places.get_place_details(item["place_id"]) or {}
                    lat = None
                    lng = None
                    try:
                        geom = details.get("geometry", {}) or {}
                        loc = geom.get("location", {}) or {}
                        lat = loc.get("lat")
                        lng = loc.get("lng")
                    except Exception:
                        pass
                    addr = details.get("formatted_address") or details.get("vicinity") or item.get("address") or ""
                    # 주소 러프하면 좌표로 보정
                    if (lat is not None and lng is not None) and self.places._looks_too_generic(addr):
                        rg = self.places._reverse_geocode(lat, lng)
                        if rg:
                            addr = rg
                    info = {
                        "place_id": item["place_id"],
                        "address": addr,
                        "lat": lat if lat is not None else item.get("lat"),
                        "lng": lng if lng is not None else item.get("lng"),
                        "rating": details.get("rating", item.get("rating")),
                    }
                except Exception:
                    info = None

            # place_id 없거나 실패 → 제목 기반 해소(+보정 포함)
            if info is None and title:
                info = self.places.resolve_place_by_title(title)

            # 적용
            if info:
                item.setdefault("place_id", info.get("place_id"))
                if info.get("address"):
                    item["address"] = info["address"]
                if info.get("lat") is not None:
                    item["lat"] = info["lat"]
                if info.get("lng") is not None:
                    item["lng"] = info["lng"]
                if info.get("rating") is not None:
                    item["rating"] = info["rating"]

            # address가 여전히 없다면 최소 표기 + 역지오코딩 최후 시도
            if not item.get("address"):
                if item.get("lat") is not None and item.get("lng") is not None:
                    rg = self.places._reverse_geocode(float(item["lat"]), float(item["lng"]))
                    if rg:
                        item["address"] = rg
                if not item.get("address"):
                    desc = (item.get("description") or "").strip()
                    if "주소:" not in desc:
                        item["description"] = (desc + (" · " if desc else "") + "주소: 정보 없음").strip()

            enriched.append(item)
        return enriched

    def suggest_plan(self) -> Any:
        try:
            nearby_places = []
            if self.fest_location:
                # ✅ categories 기반 확장 검색
                nearby_places = self.find_places_in_categories(self.travel_needs["categories"], radius_km=10)

            user_prompt = self.build_prompt(nearby_places=nearby_places)

            # NOTE: responses.create는 JSON 강제 보장이 약함. 필요시 chat.completions+response_format 권장
            response = self.client.responses.create(
                model="gpt-4o",
                tools=[{"type": "web_search_preview"}],
                input=user_prompt
            )

            main_plan_text = getattr(response, "output_text", None) or str(response)

            try:
                main_plan = json.loads(main_plan_text)
            except json.JSONDecodeError as e:
                return {"error": f"OpenAI 응답 JSON 파싱 실패: {e}"}

            main_itinerary = main_plan.get("itinerary", [])
            # ✅ 주소/좌표/평점/ID 보강(+역지오코딩)
            main_itinerary = self._enrich_itinerary_with_place_info(main_itinerary)

            main_plan["itinerary"] = main_itinerary
            main_plan.setdefault("totals", {"estimated_cost_krw": 0, "estimated_travel_time_minutes": 0})
            return main_plan

        except Exception as e:
            return {"error": f"계획 생성 중 오류 발생: {str(e)}"}

    def suggest_parking(self) -> Any:
        nearby_places = []
        if self.fest_location:
            nearby_places = self.find_places_in_categories(["공영주차장"], radius_km=1.5)
            first_3_places = nearby_places[:3]
            return self.create_itinerary(first_3_places)

    def create_itinerary(self, places: List[Any]):
        itinerary = []
        for index, place in enumerate(places, start=1):
            item = {
                "index": index,
                "type": place.category[0] if place.category else "place",
                "title": place.name,
                "start_time": "2025-08-20T00:00:00+09:00",
                "end_time": "2025-08-20T00:00:00+09:00",
                "address": place.address,  # ✅ 주소 필드 포함
                "lat": place.lat,
                "lng": place.lng,
                "rating": place.rating,
                "description": f"주소: {place.address}",
                "place_id": place.place_id,
            }
            itinerary.append(item)

        final_json = {
            "itinerary": itinerary,
            "totals": {
                "estimated_cost_krw": 0,
                "estimated_travel_time_minutes": 0
            }
        }
        return final_json

# ==========================
# 우천 대안 관련 유틸
# ==========================
def _lc(s: Optional[str]) -> str:
    return (s or "").strip().lower()


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


def _parse_kst_date(iso_str: Optional[str]) -> Optional[str]:
    if not iso_str:
        return None
    try:
        from datetime import datetime, timezone, timedelta
        KST = timezone(timedelta(hours=9))
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=KST)
        return dt.astimezone(KST).date().isoformat()
    except Exception:
        return iso_str[:10] if len(iso_str or "") >= 10 else None


def _looks_outdoor(item: Dict[str, Any], places_client: PlacesClient) -> bool:
    ty = _lc(item.get("type"))
    if ty in {"parking", "cafe", "restaurant"}:
        return False
    title = item.get("title") or ""
    desc = item.get("description") or ""
    joined = f"{title} {desc} {ty}".lower()

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

    if any(kw.lower() in joined for kw in DEFAULT_OUTDOOR_KWS):
        return True
    if any(kw.lower() in joined for kw in HERITAGE_OUTDOOR_KWS):
        return True
    return False


def _is_protected(item: Dict[str, Any], is_first: bool, protect_titles: Set[str]) -> Tuple[bool, Optional[str]]:
    if is_first:
        return True, "protected:first_item"
    ty = _lc(item.get("type"))
    if ty in PROTECT_TYPES:
        return True, f"protected:type:{ty}"
    title = item.get("title") or ""
    desc = item.get("description") or ""
    joined = f"{title} {desc}".lower()
    hit = [kw for kw in PROTECT_KWS if kw.lower() in joined]
    if hit:
        return True, f"protected:keyword:{'|'.join(hit)}"
    if title in protect_titles:
        return True, f"protected:title_exact:{title}"
    return False, None


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


def _resolve_item_center_coords(original: Dict[str, Any], places_client: PlacesClient, fallback_center: Optional[str]) -> Optional[str]:
    lat = original.get("lat")
    lng = original.get("lng")
    s = _to_latlng_str(lat, lng)
    if s:
        return s
    pid = original.get("place_id")
    if pid and hasattr(places_client, "_geocode_place_id"):
        try:
            coords = places_client._geocode_place_id(pid)
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


def collect_rain_change_candidates(
    plan: Dict[str, Any],
    places_client: PlacesClient,
    *,
    is_rainy: bool,
    rainy_dates: Optional[Set[str]] = None,
    protect_titles: Optional[Set[str]] = None
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
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


def fetch_indoor_alternatives(
    places_client: PlacesClient,
    *,
    center_coords: str,
    indoor_keywords: Optional[List[str]] = None,
    radius_km_for_alt: float = 5.0,
    avoid_titles: Optional[Set[str]] = None,
    top_k: int = 3,
    max_distance_km: Optional[float] = None
) -> List[Dict[str, Any]]:
    indoor_keywords = indoor_keywords or DEFAULT_INDOOR_KWS
    avoid_titles = set(avoid_titles or [])
    all_results: List[Dict[str, Any]] = []
    seen_names: Set[str] = set()

    try:
        c_lat, c_lng = map(float, center_coords.split(","))
    except Exception:
        return []

    for kw in indoor_keywords:
        raw = places_client.search_places_nearby(location=center_coords, keyword=kw, radius_m=int(radius_km_for_alt * 1000))
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


def build_rain_change_proposal(
    plan: Dict[str, Any],
    places_client: PlacesClient,
    *,
    is_rainy: bool,
    center_coords: Optional[str],
    rainy_dates: Optional[Set[str]] = None,
    protect_titles: Optional[Set[str]] = None,
    radius_km_for_alt: float = 5.0,
    indoor_keywords: Optional[List[str]] = None,
    top_k: int = 3,
    max_distance_km: Optional[float] = None
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

    from datetime import datetime, timezone, timedelta
    KST = timezone(timedelta(hours=9))
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
    return proposal


def apply_user_choices(
    plan: Dict[str, Any],
    proposal: Dict[str, Any],
    choices: List[Dict[str, int]],
) -> Dict[str, Any]:
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
    return new_plan



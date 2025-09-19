# planner_singleton.py
import os
import json
from dataclasses import dataclass
from typing import List, Dict, Any, Optional

# ✅ 싱글톤 모듈들에서 클라이언트 가져오기
from google_places_singleton import (
    get_google_places_client,
    GoogleAPIError,
    Place as GPlace,
)
from openai_singleton import (
    get_openai_client,
    llm_json,         # 관대 파싱 (코드블록/앞뒤 텍스트 제거)
    llm_json_strict,  # 엄격 JSON (버전 호환 폴백 내장)
)

# ─────────────────────────────────────────────────────────────────────────────
# (선택) 내부에서 쓰는 Place 데이터 모델
# ─────────────────────────────────────────────────────────────────────────────
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


# ─────────────────────────────────────────────────────────────────────────────
# FestPlanner (싱글톤 사용)
# ─────────────────────────────────────────────────────────────────────────────
class FestPlanner:
    """
    - 체류시간 제약 X
    - 숙소/주차장 미고려
    - 이동수단/경로 최적화 미고려
    - OpenAI 응답 호출 포함
    """

    def __init__(
        self,
        fest_title: str,
        fest_location_text: str,
        travel_needs: Dict[str, Any],
        *,
        places_client=None,   # 주입 가능 (미지정 시 싱글톤)
        openai_client=None,   # 주입 가능 (미지정 시 싱글톤)
        language: str = "ko",
    ):
        self.fest_title = fest_title
        self.travel_needs = self._normalize_needs(travel_needs)

        # ✅ Google Places 싱글톤
        # api_key를 get_google_places_client에 명시적으로 전달
        self.places = places_client or get_google_places_client(api_key=os.getenv("GOOGLE_API_KEY"), language=language)
        
        # ✅ 시작 좌표
        self.fest_location = self.places.get_coords_from_place_name(fest_location_text)

        # ✅ OpenAI 싱글톤
        # api_key를 get_openai_client에 명시적으로 전달
        self.client = openai_client or get_openai_client(api_key=os.getenv("OPENAI_API_KEY"))

    # ── 내부: 필요 필드 정규화
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

    # ── GooglePlaces 싱글톤 결과를 내부 Place로 매핑 (search_places_nearby 사용)
    def find_places_in_categories(self, categories: List[str], radius_km: int = 10) -> List[Place]:
        if not self.fest_location:
            return []
        radius_m = max(1000, int(radius_km * 1000))
        out: List[Place] = []
        expanded_keywords = self._expand_categories(categories)
        
        for kw in expanded_keywords:
            try:
                raw_alts = self.places.search_places_nearby(
                    location=self.fest_location,
                    keyword=kw,
                    radius_m=radius_m
                )
                for p in raw_alts:
                    loc = p.get("geometry", {}).get("location", {})
                    lat, lng = loc.get("lat"), loc.get("lng")
                    if lat is None or lng is None:
                        continue
                    
                    details = {}
                    pid = p.get("place_id")
                    if pid:
                        try:
                            details = self.places.get_place_details(pid) or {}
                        except GoogleAPIError:
                            details = {}

                    address = (
                        details.get("formatted_address")
                        or details.get("vicinity")
                        or p.get("vicinity")
                        or ""
                    )

                    if self._addr_is_generic(address):
                        rg = self._rg(lat, lng)
                        if rg:
                            address = rg

                    out.append(
                        Place(
                            name=details.get("name", p.get("name", "정보 없음")),
                            address=address or "정보 없음",
                            category=p.get("types") or ["정보 없음"],
                            rating=details.get("rating", p.get("rating")),
                            lat=lat,
                            lng=lng,
                            operating_hours=details.get("opening_hours", {}).get("weekday_text", ["정보 없음"]),
                            place_id=pid,
                        )
                    )
            except GoogleAPIError:
                continue
        return out

    # ── 카테고리 확장 헬퍼 함수 추가 (plan.py에서 옮겨옴)
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

    # ── 프롬프트 구성
    def build_prompt(self, nearby_places: Optional[List[Place]] = None) -> str:
        snippets = []
        for p in (nearby_places or [])[:20]:
            cat = ", ".join((p.category or [])[:3])
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

    # ── 호환 래퍼 (이전 코드와 이름 맞추기)
    def _rg(self, lat: float, lng: float) -> Optional[str]:
        # google_places_singleton 은 reverse_geocode(공개) 제공
        return self.places.reverse_geocode(lat, lng)

    def _addr_is_generic(self, addr: str) -> bool:
        # 내부 메서드 존재 (google_places_singleton과 동일한 이름)
        fn = getattr(self.places, "_looks_too_generic", None)
        return bool(fn(addr)) if fn else False

    # ── LLM 결과 후처리: address/lat/lng/place_id/rating 보강
    def _enrich_itinerary_with_place_info(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        enriched: List[Dict[str, Any]] = []
        for item in items:
            has_address = bool(item.get("address"))
            has_latlng = item.get("lat") is not None and item.get("lng") is not None

            if has_address and has_latlng and item.get("place_id"):
                if self._addr_is_generic(str(item.get("address", ""))):
                    rg = self._rg(float(item["lat"]), float(item["lng"]))
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
                    lat = (details.get("geometry") or {}).get("location", {}).get("lat")
                    lng = (details.get("geometry") or {}).get("location", {}).get("lng")
                    addr = details.get("formatted_address") or details.get("vicinity") or item.get("address") or ""
                    if (lat is not None and lng is not None) and self._addr_is_generic(addr):
                        rg = self._rg(lat, lng)
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

            # place_id 없거나 실패 → 제목 기반 해소
            if info is None and title:
                try:
                    info = self.places.resolve_place_by_title(title)
                except Exception:
                    info = None

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

            # address 최후 보정
            if not item.get("address") and item.get("lat") is not None and item.get("lng") is not None:
                rg = self._rg(float(item["lat"]), float(item["lng"]))
                if rg:
                    item["address"] = rg
            if not item.get("address"):
                desc = (item.get("description") or "").strip()
                if "주소:" not in desc:
                    item["description"] = (desc + (" · " if desc else "") + "주소: 정보 없음").strip()

            enriched.append(item)
        return enriched

    # ── 메인 플로우 (JSON 파싱 견고)
    def suggest_plan(self, *, model: str = "gpt-4o") -> Any:
        try:
            nearby_places: List[Place] = []
            if self.fest_location:
                nearby_places = self.find_places_in_categories(self.travel_needs["categories"], radius_km=10)

            user_prompt = self.build_prompt(nearby_places=nearby_places)

            # 1차: 엄격 JSON (SDK 버전 호환 폴백 내장)
            try:
                main_plan = llm_json_strict(user_prompt, model=model, client=self.client, debug=True)
            except Exception:
                # 2차: 관대 JSON 파싱
                main_plan = llm_json(user_prompt, model=model, client=self.client, debug=True)

            if isinstance(main_plan, dict) and "error" in main_plan:
                return main_plan  # 파싱 에러 정보 포함

            # itinerary 보강
            main_itinerary = (main_plan or {}).get("itinerary", [])
            main_itinerary = self._enrich_itinerary_with_place_info(main_itinerary)
            main_plan["itinerary"] = main_itinerary
            main_plan.setdefault("totals", {"estimated_cost_krw": 0, "estimated_travel_time_minutes": 0})
            return main_plan

        except Exception as e:
            return {"error": f"계획 생성 중 오류 발생: {str(e)}"}

    # ── 간단 주차장 제안 (예시 유지)
    def suggest_parking(self) -> Any:
        nearby_places: List[Place] = []
        if self.fest_location:
            ps = self.find_places_in_categories(["공영주차장"], radius_km=1.5)
            nearby_places = ps[:3]
            return self.create_itinerary(nearby_places)

    def create_itinerary(self, places: List[Place]):
        itinerary = []
        for index, place in enumerate(places, start=1):
            itinerary.append({
                "index": index,
                "type": place.category[0] if place.category else "place",
                "title": place.name,
                "start_time": "2025-08-20T00:00:00+09:00",
                "end_time": "2025-08-20T00:00:00+09:00",
                "address": place.address,
                "lat": place.lat,
                "lng": place.lng,
                "rating": place.rating,
                "description": f"주소: {place.address}",
                "place_id": place.place_id,
            })
        return {
            "itinerary": itinerary,
            "totals": {"estimated_cost_krw": 0, "estimated_travel_time_minutes": 0}
        }



if __name__ == "__main__":
    needs = {
        "start_at": "2025-09-20T10:00:00+09:00",
        "end_at": "2025-09-20T18:00:00+09:00",
        "categories": ["관광", "카페"],
        "budget": 100000
    }
    planner = FestPlanner("강릉역", "강릉역", needs)
    print(json.dumps(planner.suggest_plan(model="gpt-4o-mini"), ensure_ascii=False, indent=2))

# google_places_singleton.py
import requests
import os
import requests
from dataclasses import dataclass
from typing import List, Dict, Any, Optional
from functools import lru_cache
from threading import RLock

# 환경변수 우선, 없으면 기본값(가능하면 환경변수만 쓰는 걸 권장)
GOOGLE_API_KEY = "AIzaSyDtmP9H6utavbigd5NZxrTqoe2sATsAj3A"

_LOCK = RLock()
_SHARED_SESSION = requests.Session()  # 커넥션 재사용

class GoogleAPIError(Exception):
    pass

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

class GooglePlacesClient:
    """Google Maps/Places API 전용 클라이언트 (싱글톤 대상)"""
    def __init__(self, api_key: Optional[str] = None, language: str = "ko", session: Optional[requests.Session] = None):
        self.api_key = api_key or GOOGLE_API_KEY
        if not self.api_key:
            raise ValueError("GOOGLE_API_KEY가 설정되지 않았습니다.")
        self.language = language
        self.session = session or requests.Session()

    # ── 공통 유틸
    def _looks_too_generic(self, addr: str) -> bool:
        if not addr:
            return True
        generic_tokens = ["대한민국", "강원", "강릉시", "Gangneung-si", "Korea"]
        if addr.count(",") <= 0 and any(t in addr for t in generic_tokens):
            return True
        return False

    def reverse_geocode(self, lat: float, lng: float) -> Optional[str]:
        url = "https://maps.googleapis.com/maps/api/geocode/json"
        params = {
            "latlng": f"{lat},{lng}",
            "key": self.api_key,
            "language": self.language,
            "region": "kr",
            "result_type": "street_address|premise|point_of_interest",
        }
        try:
            r = self.session.get(url, params=params, timeout=10)
            r.raise_for_status()
            results = r.json().get("results", []) or []
            return results[0].get("formatted_address") if results else None
        except requests.exceptions.RequestException:
            return None

    # ── Places 기반 해상도
    def find_place_id(self, place_name: str) -> str:
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
            r = self.session.get(url, params=params, timeout=10)
            r.raise_for_status()
            candidates = r.json().get("candidates", []) or []
            return candidates[0]["place_id"] if candidates else ""
        except requests.exceptions.RequestException as e:
            raise GoogleAPIError(f"findplacefromtext 실패: {e}") from e

    def geocode_place_id(self, place_id: str) -> Optional[str]:
        url = "https://maps.googleapis.com/maps/api/geocode/json"
        params = {"place_id": place_id, "key": self.api_key, "language": self.language, "region": "kr"}
        try:
            r = self.session.get(url, params=params, timeout=10)
            r.raise_for_status()
            results = r.json().get("results", []) or []
            if not results:
                return None
            loc = results[0]["geometry"]["location"]
            return f"{loc['lat']},{loc['lng']}"
        except requests.exceptions.RequestException as e:
            raise GoogleAPIError(f"geocode 실패: {e}") from e

    def get_coords_from_place_name(self, place_name: str) -> str:
        pid = self.find_place_id(place_name)
        if not pid:
            return ""
        return self.geocode_place_id(pid) or ""

    def get_place_details(self, place_id: str) -> Dict[str, Any]:
        url = "https://maps.googleapis.com/maps/api/place/details/json"
        params = {
            "place_id": place_id,
            "fields": ("name,formatted_address,address_components,adr_address,plus_code,"
                       "rating,opening_hours,vicinity,geometry,types"),
            "key": self.api_key,
            "language": self.language,
            "region": "kr",
        }
        try:
            r = self.session.get(url, params=params, timeout=10)
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
            r = self.session.get(url, params=params, timeout=10)
            r.raise_for_status()
            return r.json().get("results", []) or []
        except requests.exceptions.RequestException as e:
            raise GoogleAPIError(f"nearbysearch 실패: {e}") from e

    # ── 제목만으로 address/lat/lng/rating 보강하는 헬퍼 (옵션)
    def resolve_place_by_title(self, title: str) -> Optional[Dict[str, Any]]:
        try:
            pid = self.find_place_id(title)
            if not pid:
                return None
            details = self.get_place_details(pid) or {}

            lat = None
            lng = None
            try:
                loc = (details.get("geometry") or {}).get("location") or {}
                lat, lng = loc.get("lat"), loc.get("lng")
            except Exception:
                pass
            if lat is None or lng is None:
                coords = self.geocode_place_id(pid)
                if coords:
                    try:
                        lat_str, lng_str = coords.split(",")
                        lat, lng = float(lat_str), float(lng_str)
                    except Exception:
                        pass

            addr = details.get("formatted_address") or details.get("vicinity") or ""
            if (lat is not None and lng is not None) and self._looks_too_generic(addr):
                rg = self.reverse_geocode(lat, lng)
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

    # ─────────────────────────────────────────────────────────────
    # ✅ 추가: 카테고리 확장 + 주변 후보 수집 (Place 리스트 반환)
    # ─────────────────────────────────────────────────────────────
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
            "공영주차장": ["공영주차장", "주차장"],
        }
        out: List[str] = []
        for c in categories or []:
            c = str(c).strip()
            if not c:
                continue
            out.extend(mapping.get(c, [c]))
        # 중복 제거(순서 보존)
        seen = set()
        return [x for x in out if not (x in seen or seen.add(x))]

    def find_near_places(self, fest_location: str, keywords: Optional[List[str]] = None, radius_m: int = 10000) -> List[Place]:
        """
        fest_location: "lat,lng" 문자열
        keywords: ["관광", "카페"] 등 카테고리(확장 후 검색)
        radius_m: 검색 반경(m)
        """
        expanded_keywords = self._expand_categories(keywords or [])
        if not expanded_keywords:
            expanded_keywords = ["관광"]

        results: List[Place] = []
        seen_names = set()

        for kw in expanded_keywords:
            try:
                raw = self.search_places_nearby(location=fest_location, keyword=kw, radius_m=radius_m)
            except GoogleAPIError as e:
                print(f"[에러] keyword={kw} API 호출 실패: {e}")
                continue

            for r in raw:
                loc = (r.get("geometry") or {}).get("location") or {}
                lat, lng = loc.get("lat"), loc.get("lng")
                if lat is None or lng is None:
                    continue

                pid = r.get("place_id")
                details = {}
                if pid:
                    try:
                        details = self.get_place_details(pid) or {}
                    except GoogleAPIError as e:
                        print(f"[경고] details 실패: {e}")

                # 주소 선택: formatted_address → vicinity → raw vicinity → "" → 필요 시 역지오코딩 보정
                address = (
                    details.get("formatted_address")
                    or details.get("vicinity")
                    or r.get("vicinity")
                    or ""
                )
                if self._looks_too_generic(address):
                    rg = self.reverse_geocode(float(lat), float(lng))
                    if rg:
                        address = rg

                name = details.get("name", r.get("name", "정보 없음"))
                if name in seen_names:
                    continue
                seen_names.add(name)

                results.append(
                    Place(
                        name=name,
                        address=address or "정보 없음",
                        category=r.get("types") or ["정보 없음"],
                        rating=details.get("rating", r.get("rating")),
                        lat=float(lat),
                        lng=float(lng),
                        operating_hours=(details.get("opening_hours") or {}).get("weekday_text", ["정보 없음"]),
                        place_id=pid,
                    )
                )

        return results


# ── 싱글톤 팩토리 ──────────────────────────────────────────────────────
@lru_cache(maxsize=None)
def get_google_places_client(api_key: Optional[str] = None, language: str = "ko") -> GooglePlacesClient:
    """
    같은 (api_key, language) 조합에 대해 프로세스 내 단일 인스턴스 반환.
    """
    with _LOCK:
        return GooglePlacesClient(api_key=api_key or GOOGLE_API_KEY,
                                  language=language,
                                  session=_SHARED_SESSION)

def reset_google_places_singleton():
    """테스트/리셋용"""
    with _LOCK:
        get_google_places_client.cache_clear()

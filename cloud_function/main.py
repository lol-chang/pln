import os
import json
import re
import datetime
from collections import defaultdict, Counter
from typing import Any, Dict, List

import requests


def safe_float(val):
    try:
        return float(val)
    except Exception:
        return 0.0


def parse_pcp(v: str) -> float:
    if not v:
        return 0.0
    s = str(v).strip()
    if s in ("강수없음", "없음"):
        return 0.0
    if "미만" in s:
        return 0.0
    m = re.search(r"([\d\.]+)", s)
    if m:
        try:
            return float(m.group(1))
        except Exception:
            return 0.0
    return 0.0



def get_vilage_forecast_list(service_key: str, nx: int = 92, ny: int = 131) -> List[Dict[str, Any]]:
    KST = datetime.timezone(datetime.timedelta(hours=9))
    now = datetime.datetime.now(KST)

    base_hours = [2, 5, 8, 11, 14, 17, 20, 23]
    if now.hour < 2:
        base_date = (now - datetime.timedelta(days=1)).strftime("%Y%m%d")
        base_time = "2300"
    else:
        base_date = now.strftime("%Y%m%d")
        base_time = f"{max(h for h in base_hours if h <= now.hour):02d}00"

    base = "http://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst"
    url = f"{base}?serviceKey={service_key}"  # 🔹 인코딩된 키를 URL에 직접 붙임

    params = {
        # "serviceKey": service_key,   # ❌ (삭제)
        "pageNo": "1",
        "numOfRows": "1000",
        "dataType": "JSON",
        "base_date": base_date,
        "base_time": base_time,
        "nx": nx,
        "ny": ny,
    }

    res = requests.get(url, params=params, timeout=20)
    res.raise_for_status()
    data = res.json()
    items = data["response"]["body"]["items"]["item"]
    
    mapping = {
        "TMP": ("temperature", safe_float),
        "PCP": ("rainfall", parse_pcp),
        "SKY": ("sky", lambda v: {1: "맑음", 3: "구름많음", 4: "흐림"}.get(int(v), v)),
        "PTY": ("precipitation_type", lambda v: {0: "없음", 1: "비", 2: "비/눈", 3: "눈", 4: "소나기"}.get(int(v), v)),
        "WSD": ("wind_speed", safe_float),
    }

    # 3시간 간격만
    valid_times = {"0900", "1200", "1500", "1800"}

    # 오늘 ~ +3일(= 오늘, 내일, 모레, 글피)
    today = now.strftime("%Y%m%d")
    max_date = (now + datetime.timedelta(days=3)).strftime("%Y%m%d")

    forecasts: List[Dict[str, Any]] = []
    time_keys = sorted(set((it["fcstDate"], it["fcstTime"]) for it in items))
    for d, t in time_keys:
        if not (today <= d <= max_date):
            continue
        if t not in valid_times:
            continue

        forecast_dict = {k: None for k in ["temperature", "rainfall", "sky", "precipitation_type", "wind_speed"]}
        for it in [x for x in items if x["fcstDate"] == d and x["fcstTime"] == t]:
            key, caster = mapping.get(it["category"], (None, None))
            if key:
                forecast_dict[key] = caster(it["fcstValue"])

        forecasts.append({"date": d, "time": t, "forecast": forecast_dict})
    return forecasts


def summarize_weather_condition(forecasts: List[Dict[str, Any]]) -> Dict[str, Dict[str, int]]:
    """날짜별로 비/눈/소나기 있으면 1, 없으면 0만 리턴"""
    priority = {"비": 3, "눈": 2, "소나기": 1, "없음": 0}
    buckets = defaultdict(list)

    for f in forecasts:
        date = f["date"]
        ptype = f["forecast"]["precipitation_type"]
        if ptype is not None:
            buckets[date].append(ptype)

    summary: Dict[str, Dict[str, int]] = {}
    for d, types in buckets.items():
        if not types:
            summary[d] = {"rain_condition": 0}
            continue
        # 다수결 + 우선순위
        counts = Counter(types)
        max_count = max(counts.values())
        candidates = [ptype for ptype, cnt in counts.items() if cnt == max_count]
        top = max(candidates, key=lambda x: priority.get(x, -1))
        rain_condition = int(top in ("비", "눈", "소나기"))
        summary[d] = {"rain_condition": rain_condition}
    return summary


# ===== Cloud Functions(Gen2) HTTP 엔트리포인트 =====
# 환경변수:
#   KMA_SERVICE_KEY (필수), DEFAULT_NX/DEFAULT_NY (옵션)
def crawl_weather(request):
    try:
        try:
            body = request.get_json(silent=True) or {}
        except Exception:
            body = {}

        service_key = os.environ["KMA_SERVICE_KEY"]  # 필수
        nx = int(body.get("nx", os.getenv("DEFAULT_NX", 92)))
        ny = int(body.get("ny", os.getenv("DEFAULT_NY", 131)))

        forecasts = get_vilage_forecast_list(service_key, nx=nx, ny=ny)
        summary = summarize_weather_condition(forecasts)

        # ✅ 리턴만 함 (서버로 푸시 없음)
        return (
            json.dumps(
                {
                    "ok": True,
                    "nx": nx,
                    "ny": ny,
                    "summary": summary,  # {"YYYYMMDD": {"rain_condition": 0/1}, ...}
                },
                ensure_ascii=False,
            ),
            200,
            {"Content-Type": "application/json"},
        )

    except Exception as e:
        return (
            json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False),
            500,
            {"Content-Type": "application/json"},
        )